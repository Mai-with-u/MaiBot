"""Plugin recovery requires both Runner initialization and Host registration.

Only process creation and the RPC transport are isolated. Restart decisions,
health decisions, ready/register handlers and Host cleanup use production code.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Deque, Dict, List, Optional
from unittest.mock import AsyncMock, Mock

import asyncio
import os

import pytest

from src.plugin_runtime import integration as integration_module
from src.plugin_runtime.host import supervisor as supervisor_module
from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor
from src.plugin_runtime.integration import DependencySyncState, PluginRuntimeManager
from src.plugin_runtime.plugin_catalog import PluginCatalogEntry
from src.plugin_runtime.protocol.envelope import (
    Envelope,
    HealthPayload,
    MessageType,
    RegisterPluginPayload,
    ReloadPluginResultPayload,
    ReloadPluginsResultPayload,
    RunnerReadyPayload,
    RunnerStartupProgressPayload,
    UnloadPluginsResultPayload,
    UnregisterPluginPayload,
)
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError

_QQ = "gateway.qq"
_IRC = "gateway.irc"
_EXTENSION = "feature.demo"


@pytest.mark.asyncio
async def test_runner_respawn_keeps_host_identity(monkeypatch):
    monkeypatch.setenv("MAIBOT_HOST_PID", "1")
    spawn = AsyncMock(return_value=SimpleNamespace(pid=4242, returncode=None, stderr=None))
    monkeypatch.setattr(supervisor_module.asyncio, "create_subprocess_exec", spawn)
    supervisor = PluginRunnerSupervisor(plugin_dirs=[])
    await supervisor._spawn_runner()
    supervisor._runner_process = None
    await supervisor._spawn_runner()
    assert spawn.await_count == 2
    assert all(call.kwargs["env"]["MAIBOT_HOST_PID"] == str(os.getpid()) for call in spawn.await_args_list)


def _request(method: str, payload: Dict[str, Any]) -> Envelope:
    return Envelope(request_id=1, message_type=MessageType.REQUEST, method=method, payload=payload)


async def _register(supervisor: PluginRunnerSupervisor, plugin_id: str, plugin_type: str = "adapter") -> None:
    payload = RegisterPluginPayload(plugin_id=plugin_id, plugin_type=plugin_type)
    response = await supervisor._handle_register_plugin(_request("plugin.register_components", payload.model_dump()))
    assert response.error is None


async def _ready(supervisor: PluginRunnerSupervisor, payload: RunnerReadyPayload) -> None:
    response = await supervisor._handle_runner_ready(_request("runner.ready", payload.model_dump()))
    assert response.error is None


class _Process:
    """A process whose drain can exit or block at a controlled gate."""

    pid = 4242

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.drain_entered: asyncio.Event | None = None
        self.drain_release: asyncio.Event | None = None

    async def wait(self) -> int:
        if self.drain_entered is not None:
            entered, release = self.drain_entered, self.drain_release
            self.drain_entered = None
            entered.set()
            assert release is not None
            await release.wait()
        self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


@dataclass
class _Startup:
    ready: RunnerReadyPayload
    registered: Dict[str, str] = field(default_factory=dict)


class _RunnerBoundary:
    """Script Runner outputs without implementing any recovery decisions."""

    def __init__(self, supervisor: PluginRunnerSupervisor) -> None:
        self.supervisor = supervisor
        self.is_connected = True
        self.last_handshake_rejection_reason = ""
        self.startups: Deque[_Startup] = deque()
        self.spawned: List[_Process] = []
        self.health = HealthPayload(healthy=True, loaded_plugins=[_QQ])
        self.result: ReloadPluginResultPayload | ReloadPluginsResultPayload | UnloadPluginsResultPayload | None = None
        self.recovery_calls: List[List[str]] = []

    async def spawn(self) -> None:
        startup = self.startups.popleft()
        process = _Process()
        self.spawned.append(process)
        self.supervisor._runner_process = process
        self.is_connected = True
        for plugin_id, plugin_type in startup.registered.items():
            await _register(self.supervisor, plugin_id, plugin_type)
        await _ready(self.supervisor, startup.ready)

    async def send_request(self, method: str, **kwargs: Any) -> Envelope:
        request = _request(method, kwargs.get("payload", {}))
        if method in {"plugin.prepare_shutdown", "plugin.shutdown"}:
            return request.make_response(payload={"accepted": True})
        if method == "plugin.health":
            return request.make_response(payload=self.health.model_dump())
        if method in {"plugin.reload", "plugin.reload_batch", "plugin.unload_batch", "plugin.recover"}:
            assert self.result is not None
            if method == "plugin.recover":
                self.recovery_calls.append(kwargs["payload"]["plugin_ids"])
                for plugin_id in self.result.reloaded_plugins:
                    await _register(
                        self.supervisor, plugin_id, "adapter" if plugin_id.startswith("gateway.") else "extension"
                    )
            for plugin_id in self.result.unloaded_plugins:
                payload = UnregisterPluginPayload(plugin_id=plugin_id, reason="test_operator")
                response = await self.supervisor._handle_unregister_plugin(
                    _request("plugin.unregister_plugin", payload.model_dump())
                )
                assert response.error is None
            return request.make_response(payload=self.result.model_dump())
        raise AssertionError(f"Unexpected RPC method: {method}")

    def clear_handshake_state(self) -> None:
        self.is_connected = False
        self.last_handshake_rejection_reason = ""

    def get_pending_request_snapshot(self) -> List[Any]:
        return []

    def abort_pending_requests(self, reason: str) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self.is_connected = False


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(supervisor_module, "is_shutdown_requested", lambda: False)
    supervisor = PluginRunnerSupervisor(
        plugin_dirs=[],
        group_name="builtin",
        health_check_interval_sec=0.01,
        max_restart_attempts=3,
        runner_spawn_timeout_sec=1.0,
    )

    async def no_debug_file(event: str, payload: Dict[str, Any]) -> None:
        pass

    monkeypatch.setattr(supervisor, "_write_debug_event", no_debug_file)
    boundary = _RunnerBoundary(supervisor)
    monkeypatch.setattr(supervisor, "_rpc_server", boundary)
    monkeypatch.setattr(supervisor, "_spawn_runner", boundary.spawn)
    supervisor._running = True
    supervisor._runner_process = _Process()
    return supervisor, boundary


@pytest.mark.asyncio
@pytest.mark.parametrize("configured, override", [(0, None), (3, 0)])
async def test_zero_restart_budget_disables_process_recovery(monkeypatch, configured: int, override: Optional[int]) -> None:
    monkeypatch.setattr(supervisor_module.global_config.plugin_runtime, "max_restart_attempts", configured)
    monkeypatch.setattr(supervisor_module, "is_shutdown_requested", lambda: False)
    supervisor = PluginRunnerSupervisor(max_restart_attempts=override)
    supervisor._running = True
    spawn = AsyncMock(side_effect=AssertionError("zero restart budget must not spawn a runner"))
    monkeypatch.setattr(supervisor, "_spawn_runner", spawn)
    monkeypatch.setattr(supervisor, "_shutdown_runner", AsyncMock())

    assert await supervisor._restart_runner("runner_process_exited") is False
    spawn.assert_not_awaited()
    assert supervisor._restart_count == 0


async def _seed_loaded(supervisor: PluginRunnerSupervisor, plugin_ids: List[str]) -> None:
    for plugin_id in plugin_ids:
        await _register(supervisor, plugin_id)
    await _ready(supervisor, RunnerReadyPayload(loaded_plugins=plugin_ids))


async def _run_health_tick(supervisor: PluginRunnerSupervisor, monkeypatch) -> None:
    ticks = 0

    async def next_tick(delay: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(supervisor_module.asyncio, "sleep", next_tick)
    await supervisor._health_check_loop()


@pytest.mark.asyncio
@pytest.mark.parametrize("plugin_id, plugin_type", [(_QQ, "adapter"), (_EXTENSION, "extension")])
@pytest.mark.parametrize("report", ["failed", "omitted", "unregistered"])
async def test_partial_restart_keeps_process_and_records_missing_plugin(runtime, plugin_id, plugin_type, report):
    supervisor, boundary = runtime
    await _register(supervisor, plugin_id, plugin_type)
    await _register(supervisor, _IRC)
    await _ready(supervisor, RunnerReadyPayload(loaded_plugins=[plugin_id, _IRC]))
    payload = RunnerReadyPayload(
        loaded_plugins=[_IRC, plugin_id] if report == "unregistered" else [_IRC],
        failed_plugins=[plugin_id] if report == "failed" else [],
    )
    boundary.startups.append(_Startup(payload, {_IRC: "adapter"}))

    assert await supervisor._restart_runner("runner_process_exited") is False
    assert supervisor._runner_process is boundary.spawned[0]
    assert supervisor._runner_process.returncode is None
    assert supervisor.get_loaded_plugin_ids() == [_IRC]
    assert supervisor._plugin_recovery_targets == {plugin_id, _IRC}
    assert supervisor._restart_count == 0
    assert supervisor._should_keep_health_loop_after_restart_failure() is True
    await supervisor.stop()


@pytest.mark.asyncio
async def test_bad_plugin_exhausts_own_budget_without_stopping_healthy_plugins(runtime, monkeypatch):
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_IRC])
    process = supervisor._runner_process
    supervisor._plugin_recovery_targets = {_QQ, _EXTENSION}
    boundary.health = HealthPayload(healthy=True, loaded_plugins=[_IRC])
    boundary.result = ReloadPluginsResultPayload(
        success=False, reloaded_plugins=[_EXTENSION], failed_plugins={_QQ: "broken"}
    )
    await _run_health_tick(supervisor, monkeypatch)
    assert supervisor._plugin_recovery_targets == {_QQ}
    assert supervisor._plugin_recovery_attempts == {_QQ: 1}
    boundary.health.loaded_plugins = [_IRC, _EXTENSION]
    boundary.result = ReloadPluginsResultPayload(success=False, failed_plugins={_QQ: "broken"})
    for _ in range(5):
        await _run_health_tick(supervisor, monkeypatch)

    assert boundary.recovery_calls == [[_EXTENSION, _QQ], [_QQ], [_QQ]]
    assert supervisor._plugin_recovery_attempts == {_QQ: 3}
    assert supervisor.get_loaded_plugin_ids() == [_EXTENSION, _IRC]
    assert supervisor._runner_process is process
    assert process.returncode is None
    assert boundary.spawned == []
    assert supervisor._running is True
    await supervisor.stop()


@pytest.mark.asyncio
async def test_recovered_plugin_clears_its_budget(runtime, monkeypatch):
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_IRC])
    supervisor._plugin_recovery_targets = {_QQ}
    supervisor._plugin_recovery_attempts = {_QQ: 2}
    boundary.health = HealthPayload(healthy=True, loaded_plugins=[_IRC])
    boundary.result = ReloadPluginsResultPayload(success=True, reloaded_plugins=[_QQ])
    await _run_health_tick(supervisor, monkeypatch)

    assert supervisor._plugin_recovery_targets == set()
    assert supervisor._plugin_recovery_attempts == {}
    assert supervisor.get_loaded_plugin_ids_by_type("adapter") == [_IRC, _QQ]
    assert boundary.spawned == []
    await supervisor.stop()


@pytest.mark.asyncio
async def test_busy_reload_defers_recovery_without_consuming_budget(runtime, monkeypatch):
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_IRC])
    supervisor._plugin_recovery_targets = {_QQ}
    original_send = boundary.send_request

    async def busy_send(method: str, **kwargs: Any) -> Envelope:
        if method == "plugin.recover":
            raise RPCError(ErrorCode.E_RELOAD_IN_PROGRESS, "manual update in progress")
        return await original_send(method, **kwargs)

    monkeypatch.setattr(boundary, "send_request", busy_send)
    for _ in range(5):
        await supervisor._recover_missing_plugins([_IRC])
    assert supervisor._plugin_recovery_attempts == {_QQ: 0}
    assert supervisor._plugin_recovery_targets == {_QQ}
    assert supervisor.get_loaded_plugin_ids() == [_IRC]
    await supervisor.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["disabled", "unloaded", "dependency_blocked"])
async def test_operator_intent_controls_recovery_target(runtime, operation):
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_IRC])
    supervisor._plugin_recovery_targets = {_QQ}
    if operation == "unloaded":
        supervisor._apply_plugin_unload_result([_QQ])
    else:
        supervisor._apply_plugin_reload_result(
            reloaded_plugins=[],
            inactive_plugins=[_QQ],
            failed_plugins={},
            explicitly_disabled_plugins=[_QQ] if operation == "disabled" else [],
        )
    boundary.result = ReloadPluginsResultPayload(success=False, inactive_plugins=[_QQ])
    await supervisor._recover_missing_plugins([_IRC])
    assert supervisor._plugin_recovery_targets == ({_QQ} if operation == "dependency_blocked" else set())
    assert len(boundary.recovery_calls) == (1 if operation == "dependency_blocked" else 0)
    await supervisor.stop()


@pytest.mark.asyncio
async def test_process_failure_still_respects_restart_budget(runtime, monkeypatch):
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_QQ])
    calls = 0

    async def failed_spawn():
        nonlocal calls
        calls += 1
        raise OSError("process creation failed")

    monkeypatch.setattr(supervisor, "_spawn_runner", failed_spawn)
    for attempt in range(1, 4):
        assert await supervisor._restart_runner("runner_process_missing") is False
        assert supervisor._restart_count == attempt
        assert supervisor._runner_process is None
        assert supervisor._should_keep_health_loop_after_restart_failure() is (attempt < 3)
    assert await supervisor._restart_runner("runner_process_missing") is False
    assert calls == 3
    assert supervisor._plugin_recovery_targets == {_QQ}
    await supervisor.stop()


@pytest.mark.asyncio
async def test_never_ready_plugins_do_not_block_recovery(runtime) -> None:
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_QQ])
    await _register(supervisor, _EXTENSION, "extension")
    await _register(supervisor, "gateway.never_ready")
    await _ready(supervisor, RunnerReadyPayload(loaded_plugins=[_QQ]))
    supervisor._restart_count = 2
    boundary.startups.append(
        _Startup(
            RunnerReadyPayload(loaded_plugins=[_QQ], failed_plugins=[_EXTENSION]),
            {_QQ: "adapter"},
        )
    )

    assert await supervisor._restart_runner("health_check_failed") is True
    assert supervisor.get_loaded_plugin_ids_by_type("adapter") == [_QQ]
    assert supervisor.get_plugin_load_statuses()[_EXTENSION] == "failed"
    assert supervisor._restart_count == 0
    assert supervisor._plugin_recovery_targets == set()
    await supervisor.stop()


@pytest.mark.asyncio
async def test_ready_explicitly_disabled_adapter_is_not_forced_back_online(runtime) -> None:
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_QQ, _IRC])
    boundary.startups.append(
        _Startup(
            RunnerReadyPayload(loaded_plugins=[_IRC], inactive_plugins=[_QQ], explicitly_disabled_plugins=[_QQ]),
            {_IRC: "adapter"},
        )
    )

    assert await supervisor._restart_runner("health_check_failed") is True
    assert supervisor.get_loaded_plugin_ids_by_type("adapter") == [_IRC]
    assert supervisor.get_plugin_load_statuses()[_QQ] == "inactive"
    assert supervisor._plugin_recovery_targets == set()
    await supervisor.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["stop", "global_shutdown"])
@pytest.mark.parametrize("stage", ["drain", "connection", "ready"])
async def test_shutdown_during_restart_wait_never_reports_recovery(
    runtime, monkeypatch, interruption: str, stage: str
) -> None:
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_QQ])
    entered = asyncio.Event()
    release = asyncio.Event()
    if stage == "drain":
        supervisor._runner_process.drain_entered = entered
        supervisor._runner_process.drain_release = release
    else:
        method_name = "_wait_for_runner_connection" if stage == "connection" else "_wait_for_runner_ready"
        original_wait = getattr(supervisor, method_name)

        async def gated_wait(*args: Any, **kwargs: Any):
            entered.set()
            await release.wait()
            return await original_wait(*args, **kwargs)

        monkeypatch.setattr(supervisor, method_name, gated_wait)
    boundary.startups.append(_Startup(RunnerReadyPayload(loaded_plugins=[_QQ]), {_QQ: "adapter"}))
    restart = asyncio.create_task(supervisor._restart_runner("health_check_failed"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        spawned_before_shutdown = len(boundary.spawned)
        if interruption == "stop":
            await supervisor.stop()
        else:
            monkeypatch.setattr(supervisor_module, "is_shutdown_requested", lambda: True)
        # A late connection/ready response must not override the shutdown decision.
        if stage != "drain":
            boundary.is_connected = True
            await _ready(supervisor, RunnerReadyPayload(loaded_plugins=[_QQ]))
        release.set()

        assert await asyncio.wait_for(restart, timeout=1.0) is False
        assert len(boundary.spawned) == spawned_before_shutdown
        if stage == "drain":
            assert boundary.spawned == []
        assert supervisor._runner_process is None
        if interruption == "stop":
            assert supervisor._plugin_recovery_targets == set()
    finally:
        release.set()
        if not restart.done():
            restart.cancel()
        await asyncio.gather(restart, return_exceptions=True)
        await supervisor.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle", ["start", "stop"])
async def test_explicit_lifecycle_discards_old_recovery_targets(runtime, monkeypatch, lifecycle: str) -> None:
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_QQ])
    boundary.startups.append(_Startup(RunnerReadyPayload(failed_plugins=[_QQ])))
    assert await supervisor._restart_runner("health_check_failed") is False
    assert supervisor._plugin_recovery_targets == {_QQ}

    if lifecycle == "start":
        supervisor._running = False
        boundary.startups.append(_Startup(RunnerReadyPayload()))

        async def idle_health_loop() -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(supervisor, "_health_check_loop", idle_health_loop)
        await supervisor.start()
    else:
        await supervisor.stop()

    assert supervisor._plugin_recovery_targets == set()
    await supervisor.stop()


@pytest.mark.asyncio
async def test_start_waits_for_load_round_including_failed_plugins(runtime, monkeypatch) -> None:
    supervisor, boundary = runtime
    supervisor._running = False
    connected = asyncio.Event()

    async def spawn_loading_runner() -> None:
        supervisor._runner_process = _Process()
        boundary.is_connected = True
        await _register(supervisor, _QQ)
        connected.set()

    monkeypatch.setattr(supervisor, "_spawn_runner", spawn_loading_runner)
    monkeypatch.setattr(supervisor, "_health_check_loop", AsyncMock())
    startup = asyncio.create_task(supervisor.start())
    try:
        await asyncio.wait_for(connected.wait(), timeout=1.0)
        assert not startup.done()
        assert supervisor.is_loading
        assert supervisor._health_task is None

        await _ready(supervisor, RunnerReadyPayload(
            loaded_plugins=[_QQ], failed_plugins=[_EXTENSION], inactive_plugins=[_IRC],
        ))
        await asyncio.wait_for(startup, timeout=1.0)
        assert not supervisor.is_loading
        assert supervisor.get_plugin_load_statuses()[_EXTENSION] == "failed"
    finally:
        if not startup.done():
            startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)
        await supervisor.stop()


@pytest.mark.asyncio
async def test_registration_cannot_override_reported_failure_or_supply_dependency_version(runtime):
    supervisor, _ = runtime
    await _register(supervisor, _QQ)
    assert supervisor.get_plugin_load_statuses()[_QQ] == "loading"
    assert supervisor.get_loaded_plugin_versions() == {}

    await _ready(supervisor, RunnerReadyPayload(
        failed_plugins=[_QQ], failed_plugin_reasons={_QQ: "on_load failed"},
    ))
    assert supervisor.get_plugin_load_statuses()[_QQ] == "failed"
    assert supervisor.get_plugin_load_failure_reasons()[_QQ] == "on_load failed"
    assert supervisor.get_loaded_plugin_versions() == {}

    supervisor._apply_plugin_reload_result(
        reloaded_plugins=[_QQ], inactive_plugins=[], explicitly_disabled_plugins=[], failed_plugins={},
    )
    assert supervisor.get_plugin_load_statuses()[_QQ] == "success"
    assert _QQ in supervisor.get_loaded_plugin_versions()


@pytest.mark.asyncio
async def test_connection_timeout_stops_unconnected_runner(runtime, monkeypatch) -> None:
    supervisor, boundary = runtime
    supervisor._running = False
    supervisor._runner_spawn_timeout = 0.01
    process = _Process()

    async def spawn_loading_runner() -> None:
        supervisor._runner_process = process
        boundary.is_connected = False

    monkeypatch.setattr(supervisor, "_spawn_runner", spawn_loading_runner)
    with pytest.raises(TimeoutError):
        await supervisor.start()

    assert process.returncode is not None
    assert supervisor._runner_process is None
    assert not supervisor._running
    assert not supervisor.is_loading
    assert supervisor.get_loaded_plugin_ids() == []
    assert supervisor._health_task is None
    assert not boundary.is_connected


@pytest.mark.asyncio
async def test_repeated_loading_timeouts_keep_runner_and_publish_partial_results(runtime, monkeypatch) -> None:
    supervisor, boundary = runtime
    supervisor._running = False
    supervisor._runner_spawn_timeout = 0.01
    timed_out_twice = asyncio.Event()
    timeout_count = 0
    process = _Process()

    def status_changed() -> None:
        nonlocal timeout_count
        if supervisor.startup_load_status == "timed_out":
            timeout_count += 1
            if timeout_count >= 2:
                timed_out_twice.set()

    async def spawn_loading_runner() -> None:
        supervisor._runner_process = process
        boundary.is_connected = True
        await _register(supervisor, _QQ)
        await _register(supervisor, _EXTENSION)
        progress = RunnerStartupProgressPayload(
            loaded_plugins=[_QQ], failed_plugins=[_IRC], loading_plugins=[_EXTENSION],
            failed_plugin_reasons={_IRC: "on_load raised an error"},
        )
        await supervisor._handle_startup_progress(_request("runner.startup_progress", progress.model_dump()))

    monkeypatch.setattr(supervisor, "_spawn_runner", spawn_loading_runner)
    monkeypatch.setattr(supervisor, "_status_changed_callback", status_changed)
    monkeypatch.setattr(supervisor, "_health_check_loop", AsyncMock())
    startup = asyncio.create_task(supervisor.start())
    try:
        await asyncio.wait_for(timed_out_twice.wait(), 1.0)
        assert not startup.done()
        assert process.returncode is None
        assert supervisor._runner_process is process
        assert supervisor.get_plugin_load_statuses() == {_QQ: "success", _IRC: "failed", _EXTENSION: "timed_out"}
        reasons = supervisor.get_plugin_load_failure_reasons()
        assert reasons[_IRC] == "on_load raised an error"
        assert "仍在后台加载" in reasons[_EXTENSION]
        assert supervisor.startup_load_status == "timed_out"

        await _ready(supervisor, RunnerReadyPayload(loaded_plugins=[_QQ, _EXTENSION], failed_plugins=[_IRC]))
        await asyncio.wait_for(startup, 1.0)
        assert process.returncode is None
        assert supervisor.get_plugin_load_statuses()[_EXTENSION] == "success"
        assert supervisor.startup_load_reason == ""
        # 最终 ready 优先于迟到的启动进度。
        await supervisor._handle_startup_progress(_request(
            "runner.startup_progress", RunnerStartupProgressPayload(loading_plugins=[_EXTENSION]).model_dump(),
        ))
        assert supervisor.get_plugin_load_statuses()[_EXTENSION] == "success"
    finally:
        startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)
        await supervisor.stop()


@pytest.mark.asyncio
async def test_cancelled_start_cleans_loading_runner(runtime, monkeypatch) -> None:
    supervisor, boundary = runtime
    supervisor._running = False
    connected = asyncio.Event()
    process = _Process()

    async def spawn_loading_runner() -> None:
        supervisor._runner_process = process
        boundary.is_connected = True
        connected.set()

    monkeypatch.setattr(supervisor, "_spawn_runner", spawn_loading_runner)
    startup = asyncio.create_task(supervisor.start())
    await asyncio.wait_for(connected.wait(), timeout=1.0)
    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup
    assert process.returncode is not None
    assert supervisor._runner_process is None
    assert not supervisor._running
    assert supervisor._health_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("load_result", ["ready", "late_ready", "failed"])
async def test_manager_starts_watcher_only_after_runner_load_round(runtime, monkeypatch, tmp_path, load_result):
    supervisor, boundary = runtime
    supervisor._running = False
    supervisor._runner_spawn_timeout = 1.0 if load_result == "ready" else 0.01
    connected = asyncio.Event()
    manager = PluginRuntimeManager()
    platform_io = SimpleNamespace(
        set_inbound_dispatcher=Mock(), ensure_send_pipeline_ready=AsyncMock(),
        clear_inbound_dispatcher=Mock(), stop=AsyncMock(),
    )

    async def spawn_loading_runner() -> None:
        supervisor._runner_process = _Process()
        boundary.is_connected = True
        await _register(supervisor, _QQ)
        connected.set()

    def build_supervisors(*args) -> None:
        manager._builtin_supervisor = supervisor

    watcher_start = AsyncMock()
    async def initialize_catalog(*args):
        manager._plugin_catalog._entries = {
            tmp_path / "qq": PluginCatalogEntry(_QQ, tmp_path / "qq", {"plugin_type": "adapter"}, False, True, "", None),
        }
        manager._plugin_catalog._initialized = True

    monkeypatch.setattr(manager._plugin_catalog, "start", initialize_catalog)
    monkeypatch.setattr(supervisor, "_spawn_runner", spawn_loading_runner)
    monkeypatch.setattr(supervisor, "_health_check_loop", AsyncMock())
    monkeypatch.setattr(manager, "_resolve_runtime_plugin_dirs", lambda: ([tmp_path], []))
    monkeypatch.setattr(manager, "_build_supervisors", build_supervisors)
    monkeypatch.setattr(manager, "_sync_plugin_dependencies", AsyncMock(return_value=DependencySyncState(set(), False)))
    monkeypatch.setattr(manager, "ensure_builtin_hook_specs_registered", Mock())
    monkeypatch.setattr(manager, "_start_plugin_usage_tracking", AsyncMock())
    monkeypatch.setattr(manager, "_build_group_start_order", lambda *args, **kwargs: ["builtin"])
    monkeypatch.setattr(manager, "_get_group_dependency_flags", lambda *args, **kwargs: (False, False))
    monkeypatch.setattr(manager, "_start_plugin_file_watcher", watcher_start)
    monkeypatch.setattr(integration_module, "get_platform_io_manager", lambda: platform_io)
    monkeypatch.setattr(integration_module.config_manager.get_global_config().plugin_runtime, "enabled", True)
    monkeypatch.setattr(integration_module.config_manager, "register_reload_callback", Mock())
    monkeypatch.setattr(integration_module.config_manager, "unregister_reload_callback", Mock())

    startup = asyncio.create_task(manager.start())
    try:
        await asyncio.wait_for(connected.wait(), timeout=1.0)
        watcher_start.assert_not_awaited()
        assert not manager._started
        if load_result == "ready":
            await _ready(supervisor, RunnerReadyPayload(loaded_plugins=[_QQ], failed_plugins=[_EXTENSION]))
        else:
            async def wait_for_timeout():
                while supervisor.startup_load_status != "timed_out":
                    await asyncio.sleep(0)

            await asyncio.wait_for(wait_for_timeout(), 1.0)
            watcher_start.assert_not_awaited()
            assert not manager._started
            assert not startup.done()
            assert supervisor._runner_process.returncode is None
            if load_result == "failed":
                supervisor._runner_process.returncode = 1
            else:
                await _ready(supervisor, RunnerReadyPayload(loaded_plugins=[_QQ]))
        await asyncio.wait_for(startup, timeout=1.0)

        if load_result == "failed":
            watcher_start.assert_not_awaited()
            assert not manager._started
            assert manager._builtin_supervisor is supervisor
            snapshot = await manager.get_plugin_state_snapshot()
            assert snapshot["statuses"][_QQ] == "failed"
            assert "退出码 1" in snapshot["failure_reasons"][_QQ]
        else:
            watcher_start.assert_awaited_once()
            assert manager._started
    finally:
        if not startup.done():
            startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)
        await manager.stop()
