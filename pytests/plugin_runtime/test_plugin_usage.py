from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncio
import json

import pytest

from src.common import atomic_file
from src.plugin_runtime import integration as integration_module
from src.plugin_runtime import usage_tracker as usage_tracker_module
from src.plugin_runtime.host.rpc_server import RPCServer
from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor
from src.plugin_runtime.integration import PluginRuntimeManager
from src.plugin_runtime.protocol.envelope import (
    Envelope,
    MessageType,
    RegisterPluginPayload,
    RunnerReadyPayload,
    UnregisterPluginPayload,
)
from src.plugin_runtime.usage_tracker import PluginUsageTracker


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def request(method: str, payload: dict) -> Envelope:
    return Envelope(request_id=1, message_type=MessageType.REQUEST, method=method, payload=payload)


@pytest.mark.asyncio
async def test_usage_counts_only_running_intervals_and_survives_restart(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "usage.json"
    tracker = PluginUsageTracker(path, clock)
    await tracker.load()
    tracker.reconcile(["demo", "other"])
    clock.now += 10
    tracker.reconcile(["demo", "other"])
    clock.now += 5
    tracker.reconcile(["other"])
    clock.now += 20
    assert tracker.snapshot() == {"demo": 15, "other": 35}
    tracker.reconcile(["demo", "other"])
    clock.now += 7
    await tracker.save()
    assert tracker.snapshot() == {"demo": 22, "other": 42}

    clock.now += 100_000
    restored = PluginUsageTracker(path, clock)
    await restored.load()
    assert restored.snapshot() == {"demo": 22, "other": 42}
    restored.reconcile(["demo"])
    clock.now += 3
    restored.reconcile([])
    assert restored.snapshot() == {"demo": 25, "other": 42}


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", [
    "{broken", '{"version":1,"usage_seconds":{"demo":-1}}',
    '{"version":1,"usage_seconds":{"demo":true}}',
    '{"version":1,"usage_seconds":{"demo":NaN}}', '{"version":2,"usage_seconds":{}}',
    json.dumps({"version": 1, "usage_seconds": {"demo": 10**400}}),
])
async def test_corrupt_usage_is_quarantined(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "usage.json"
    path.write_text(contents, encoding="utf-8")
    tracker = PluginUsageTracker(path)
    await tracker.load()
    assert tracker.snapshot() == {}
    assert not path.exists()
    (backup,) = tmp_path.glob("usage.json.corrupt-*")
    assert backup.read_text(encoding="utf-8") == contents


@pytest.mark.asyncio
async def test_cancelled_checkpoint_finishes_before_final_save(tmp_path: Path, monkeypatch) -> None:
    clock = Clock()
    tracker = PluginUsageTracker(tmp_path / "usage.json", clock)
    tracker.reconcile(["demo"])
    clock.now += 5
    entered, release = Event(), Event()
    original_write = tracker._write_totals
    writes = []

    def blocked_write(totals):
        if not writes:
            entered.set()
            assert release.wait(5)
        writes.append(totals)
        original_write(totals)

    monkeypatch.setattr(tracker, "_write_totals", blocked_write)
    checkpoint = asyncio.create_task(tracker.save())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        checkpoint.cancel()
        await asyncio.sleep(0)
        assert not checkpoint.done()
        clock.now += 20
        tracker.reconcile([])
        final_save = asyncio.create_task(tracker.save())
        await asyncio.sleep(0)
        assert not final_save.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await checkpoint
    await final_save
    assert writes == [{"demo": 5}, {"demo": 25}]
    restored = PluginUsageTracker(tmp_path / "usage.json", clock)
    await restored.load()
    assert restored.snapshot() == {"demo": 25}


@pytest.mark.asyncio
async def test_failed_atomic_replace_preserves_previous_record(tmp_path: Path, monkeypatch) -> None:
    clock = Clock()
    path = tmp_path / "usage.json"
    tracker = PluginUsageTracker(path, clock)
    tracker.reconcile(["demo"])
    clock.now += 5
    await tracker.save()
    original = path.read_bytes()

    def fail_replace(source, target):
        raise OSError("disk write failed")

    monkeypatch.setattr(atomic_file, "_replace_once", fail_replace)
    clock.now += 10
    with pytest.raises(OSError, match="disk write failed"):
        await tracker.save()
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_supervisor_waits_for_activation_and_excludes_failed_or_unavailable_plugins() -> None:
    supervisor = PluginRunnerSupervisor(plugin_dirs=[])
    supervisor._running = True
    supervisor._runner_process = SimpleNamespace(returncode=None)
    supervisor._rpc_server._connection = SimpleNamespace(is_closed=False)
    registration = RegisterPluginPayload(plugin_id="demo")
    await supervisor._handle_register_plugin(request("plugin.register_components", registration.model_dump()))
    assert supervisor.get_usage_active_plugin_ids() == set()
    await supervisor._handle_runner_ready(request("runner.ready", RunnerReadyPayload(loaded_plugins=["demo"]).model_dump()))
    assert supervisor.get_usage_active_plugin_ids() == {"demo"}

    await supervisor._handle_unregister_plugin(request(
        "plugin.unregister", UnregisterPluginPayload(plugin_id="demo", reason="reload").model_dump(),
    ))
    assert supervisor.get_usage_active_plugin_ids() == set()
    await supervisor._handle_register_plugin(request("plugin.register_components", registration.model_dump()))
    assert supervisor.get_usage_active_plugin_ids() == set()
    supervisor._apply_plugin_reload_result(["demo"], [], [], {})
    assert supervisor.get_usage_active_plugin_ids() == {"demo"}
    supervisor._registered_plugins["demo"].config_error = "invalid config"
    assert supervisor.get_usage_active_plugin_ids() == set()
    supervisor._registered_plugins["demo"].config_error = ""
    supervisor._runner_process.returncode = 1
    assert supervisor.get_usage_active_plugin_ids() == set()
    supervisor._runner_process.returncode = None
    supervisor._rpc_server._connection.is_closed = True
    assert supervisor.get_usage_active_plugin_ids() == set()
    supervisor._rpc_server._connection.is_closed = False
    supervisor._handle_usage_connection_lost()
    assert supervisor.get_usage_active_plugin_ids() == set()


@pytest.mark.asyncio
async def test_rpc_disconnect_notifies_when_active_connection_is_removed(monkeypatch) -> None:
    callback = Mock()
    server = RPCServer(transport=Mock(), host_version="1.0.0", connection_lost_callback=callback)
    connection = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(server, "_handle_handshake", AsyncMock(return_value=True))
    monkeypatch.setattr(server, "_recv_loop", AsyncMock())
    await server._handle_connection(connection)
    assert server._connection is None
    callback.assert_called_once_with()


@pytest.mark.asyncio
async def test_manager_tracks_without_webui_and_flushes_before_restart(tmp_path: Path, monkeypatch) -> None:
    clock = Clock()
    active = set()
    manager = PluginRuntimeManager()
    manager._third_party_supervisor = SimpleNamespace(get_usage_active_plugin_ids=lambda: active)
    monkeypatch.setattr(integration_module, "get_instance_root", lambda: tmp_path)
    monkeypatch.setattr(integration_module, "PluginUsageTracker", lambda path: PluginUsageTracker(path, clock))
    await manager._start_plugin_usage_tracking()
    task = manager._plugin_usage_task
    try:
        active.add("demo")
        manager._notify_status_changed()
        clock.now += 10
        active.clear()
        manager._notify_status_changed()
        clock.now += 20
        assert manager.get_plugin_usage_seconds() == {"demo": 10}
        active.add("demo")
        manager._notify_status_changed()
        clock.now += 5
    finally:
        await manager._stop_plugin_usage_tracking()
    assert task.done()
    assert manager._plugin_usage_task is None
    clock.now += 30
    assert manager.get_plugin_usage_seconds() == {"demo": 15}
    saved = json.loads((tmp_path / "data/plugin_runtime/plugin_usage.json").read_text(encoding="utf-8"))
    assert saved["usage_seconds"] == {"demo": 15}
    await manager._start_plugin_usage_tracking()
    try:
        clock.now += 2
        assert manager.get_plugin_usage_seconds() == {"demo": 17}
    finally:
        await manager._stop_plugin_usage_tracking()


@pytest.mark.asyncio
async def test_manager_resumes_tracking_after_corrupt_record(tmp_path: Path, monkeypatch) -> None:
    clock = Clock()
    manager = PluginRuntimeManager()
    manager._third_party_supervisor = SimpleNamespace(get_usage_active_plugin_ids=lambda: {"demo"})
    monkeypatch.setattr(integration_module, "get_instance_root", lambda: tmp_path)
    monkeypatch.setattr(integration_module, "PluginUsageTracker", lambda path: PluginUsageTracker(path, clock))
    path = tmp_path / "data/plugin_runtime/plugin_usage.json"
    path.parent.mkdir(parents=True)
    contents = bytes(968)
    path.write_bytes(contents)
    await manager._start_plugin_usage_tracking()
    try:
        clock.now += 12
        assert manager.get_plugin_usage_seconds() == {"demo": 12}
    finally:
        await manager._stop_plugin_usage_tracking()
    (backup,) = path.parent.glob("plugin_usage.json.corrupt-*")
    assert backup.read_bytes() == contents
    restored = PluginUsageTracker(path, clock)
    await restored.load()
    assert restored.snapshot() == {"demo": 12}


@pytest.mark.asyncio
async def test_manager_reports_failed_quarantine_without_overwriting_record(tmp_path: Path, monkeypatch) -> None:
    manager = PluginRuntimeManager()
    monkeypatch.setattr(integration_module, "get_instance_root", lambda: tmp_path)
    path = tmp_path / "data/plugin_runtime/plugin_usage.json"
    path.parent.mkdir(parents=True)
    contents = b"{broken"
    path.write_bytes(contents)
    monkeypatch.setattr(usage_tracker_module, "quarantine_file", Mock(side_effect=PermissionError("access denied")))

    await manager._start_plugin_usage_tracking()
    await manager._stop_plugin_usage_tracking()
    with pytest.raises(RuntimeError, match="access denied"):
        manager.get_plugin_usage_seconds()
    assert manager._plugin_usage_task is None
    assert path.read_bytes() == contents
