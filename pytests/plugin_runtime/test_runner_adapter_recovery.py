"""Adapter recovery requires both Runner initialization and Host registration.

Only process creation and the RPC transport are isolated. Restart decisions,
health decisions, ready/register handlers and Host cleanup use production code.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.plugin_runtime.host import supervisor as supervisor_module
from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor
from src.plugin_runtime.protocol.envelope import (
    Envelope,
    HealthPayload,
    MessageType,
    RegisterPluginPayload,
    ReloadPluginResultPayload,
    ReloadPluginsResultPayload,
    RunnerReadyPayload,
    UnloadPluginsResultPayload,
    UnregisterPluginPayload,
)

_QQ = "gateway.qq"
_IRC = "gateway.irc"
_EXTENSION = "feature.demo"


def _request(method: str, payload: dict[str, Any]) -> Envelope:
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
    registered: dict[str, str] = field(default_factory=dict)


class _RunnerBoundary:
    """Script Runner outputs without implementing any recovery decisions."""

    def __init__(self, supervisor: PluginRunnerSupervisor) -> None:
        self.supervisor = supervisor
        self.is_connected = True
        self.last_handshake_rejection_reason = ""
        self.startups: deque[_Startup] = deque()
        self.spawned: list[_Process] = []
        self.health = HealthPayload(healthy=True, loaded_plugins=[_QQ])
        self.result: ReloadPluginResultPayload | ReloadPluginsResultPayload | UnloadPluginsResultPayload | None = None

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
        if method in {"plugin.reload", "plugin.reload_batch", "plugin.unload_batch"}:
            assert self.result is not None
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

    def get_pending_request_snapshot(self) -> list[Any]:
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

    async def no_debug_file(event: str, payload: dict[str, Any]) -> None:
        pass

    monkeypatch.setattr(supervisor, "_write_debug_event", no_debug_file)
    boundary = _RunnerBoundary(supervisor)
    monkeypatch.setattr(supervisor, "_rpc_server", boundary)
    monkeypatch.setattr(supervisor, "_spawn_runner", boundary.spawn)
    supervisor._running = True
    supervisor._runner_process = _Process()
    return supervisor, boundary


async def _seed_loaded(supervisor: PluginRunnerSupervisor, plugin_ids: list[str]) -> None:
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
@pytest.mark.parametrize("report", ["failed", "omitted", "loaded_without_registration"])
async def test_restart_rejects_adapter_that_did_not_recover(runtime, report: str) -> None:
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_QQ])
    supervisor._restart_count = 1
    payload = {
        "failed": RunnerReadyPayload(failed_plugins=[_QQ], failed_plugin_reasons={_QQ: "initialization failed"}),
        "omitted": RunnerReadyPayload(),
        "loaded_without_registration": RunnerReadyPayload(loaded_plugins=[_QQ]),
    }[report]
    boundary.startups.append(_Startup(payload))

    assert await supervisor._restart_runner("health_check_failed") is False
    assert supervisor._restart_count == 2
    assert supervisor._adapter_recovery_targets == {_QQ}
    assert supervisor._runner_process is None
    assert supervisor.get_loaded_plugin_ids() == []
    assert boundary.spawned[0].returncode is not None
    assert supervisor._running is True


@pytest.mark.asyncio
async def test_targets_survive_two_failed_restarts_and_clear_only_on_recovery(runtime) -> None:
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_QQ, _IRC])
    boundary.startups.extend(
        [
            _Startup(RunnerReadyPayload(loaded_plugins=[_IRC], failed_plugins=[_QQ]), {_IRC: "adapter"}),
            _Startup(RunnerReadyPayload()),
            _Startup(RunnerReadyPayload(loaded_plugins=[_QQ, _IRC]), {_QQ: "adapter", _IRC: "adapter"}),
        ]
    )

    for expected_count in (1, 2):
        assert await supervisor._restart_runner("health_check_failed") is False
        assert supervisor._restart_count == expected_count
        assert supervisor._adapter_recovery_targets == {_QQ, _IRC}
        assert supervisor._runner_process is None
        assert supervisor.get_loaded_plugin_ids() == []

    assert await supervisor._restart_runner("runner_process_missing") is True
    assert len(boundary.spawned) == 3
    assert supervisor._restart_count == 0
    assert supervisor._adapter_recovery_targets == set()
    assert supervisor.get_loaded_plugin_ids_by_type("adapter") == [_IRC, _QQ]
    await supervisor.stop()


@pytest.mark.asyncio
async def test_extension_failure_and_never_ready_adapter_do_not_block_recovery(runtime) -> None:
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_QQ])
    await _register(supervisor, _EXTENSION, "extension")
    await _register(supervisor, "gateway.never_ready")
    await _ready(supervisor, RunnerReadyPayload(loaded_plugins=[_QQ, _EXTENSION]))
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
    assert supervisor._adapter_recovery_targets == set()
    await supervisor.stop()


@pytest.mark.asyncio
async def test_ready_inactive_adapter_is_not_forced_back_online(runtime) -> None:
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_QQ, _IRC])
    boundary.startups.append(
        _Startup(RunnerReadyPayload(loaded_plugins=[_IRC], inactive_plugins=[_QQ]), {_IRC: "adapter"})
    )

    assert await supervisor._restart_runner("health_check_failed") is True
    assert supervisor.get_loaded_plugin_ids_by_type("adapter") == [_IRC]
    assert supervisor.get_plugin_load_statuses()[_QQ] == "inactive"
    assert supervisor._adapter_recovery_targets == set()
    await supervisor.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["reload_single", "reload_batch", "unload_success", "unload_partial", "unload_failure"]
)
async def test_operator_inactive_or_successful_unload_removes_pending_target(runtime, operation: str) -> None:
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_QQ])
    boundary.startups.append(_Startup(RunnerReadyPayload(failed_plugins=[_QQ])))
    assert await supervisor._restart_runner("health_check_failed") is False
    assert supervisor._adapter_recovery_targets == {_QQ}

    # An operator acts on a live Runner after loading the adapter again.
    # The watchdog has not yet confirmed completion of the recovery cycle.
    supervisor._runner_process = _Process()
    boundary.is_connected = True
    await _seed_loaded(supervisor, [_QQ])

    if operation == "reload_single":
        boundary.result = ReloadPluginResultPayload(
            success=True,
            requested_plugin_id=_QQ,
            unloaded_plugins=[_QQ],
            inactive_plugins=[_QQ],
        )
        assert await supervisor.reload_plugin(_QQ) is True
    elif operation == "reload_batch":
        boundary.result = ReloadPluginsResultPayload(
            success=True,
            requested_plugin_ids=[_QQ, _EXTENSION],
            unloaded_plugins=[_QQ],
            inactive_plugins=[_QQ],
        )
        assert await supervisor.reload_plugins([_QQ, _EXTENSION]) is True
    else:
        success = operation == "unload_success"
        qq_unloaded = operation != "unload_failure"
        requested = [_QQ, _EXTENSION] if operation == "unload_partial" else [_QQ]
        boundary.result = UnloadPluginsResultPayload(
            success=success,
            requested_plugin_ids=requested,
            unloaded_plugins=[_QQ] if qq_unloaded else [],
            failed_plugins={} if success else {_EXTENSION if qq_unloaded else _QQ: "unload refused"},
        )
        result = await supervisor.unload_plugins(requested, reason="local_operator_offline")
        assert result.success is success

    pending = operation == "unload_failure"
    assert supervisor._adapter_recovery_targets == ({_QQ} if pending else set())
    boundary.startups.append(_Startup(RunnerReadyPayload()))
    assert await supervisor._restart_runner("runner_process_missing") is (not pending)
    if pending:
        assert supervisor._restart_count == 2
        assert supervisor._runner_process is None
    await supervisor.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["health_report", "registration", "both", "neither"])
async def test_healthy_rpc_does_not_hide_pending_adapter_recovery(runtime, monkeypatch, missing: str) -> None:
    supervisor, boundary = runtime
    if missing not in {"registration", "both"}:
        await _register(supervisor, _QQ)
    await _ready(
        supervisor,
        RunnerReadyPayload(loaded_plugins=[_QQ]),
    )
    boundary.health = HealthPayload(healthy=True, loaded_plugins=[] if missing in {"health_report", "both"} else [_QQ])
    supervisor._adapter_recovery_targets = {_QQ}
    supervisor._restart_count = 2
    boundary.startups.append(_Startup(RunnerReadyPayload(loaded_plugins=[_QQ]), {_QQ: "adapter"}))

    # healthy=True alone does not establish that the pending adapter is restored.
    await _run_health_tick(supervisor, monkeypatch)

    assert len(boundary.spawned) == (0 if missing == "neither" else 1)
    assert supervisor.get_loaded_plugin_ids_by_type("adapter") == [_QQ]
    assert supervisor._restart_count == 0
    assert supervisor._adapter_recovery_targets == set()
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
            assert supervisor._adapter_recovery_targets == set()
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
    assert supervisor._adapter_recovery_targets == {_QQ}

    if lifecycle == "start":
        supervisor._running = False
        boundary.startups.append(_Startup(RunnerReadyPayload()))

        async def idle_health_loop() -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(supervisor, "_health_check_loop", idle_health_loop)
        await supervisor.start()
    else:
        await supervisor.stop()

    assert supervisor._adapter_recovery_targets == set()
    await supervisor.stop()


@pytest.mark.asyncio
async def test_failed_adapter_recovery_respects_restart_budget(runtime) -> None:
    supervisor, boundary = runtime
    await _seed_loaded(supervisor, [_QQ])
    boundary.startups.extend(_Startup(RunnerReadyPayload(failed_plugins=[_QQ])) for _ in range(3))

    for attempt in range(1, 4):
        assert await supervisor._restart_runner("runner_process_missing") is False
        assert supervisor._restart_count == attempt
        assert supervisor._adapter_recovery_targets == {_QQ}
        assert supervisor._should_keep_health_loop_after_restart_failure() is (attempt < 3)

    boundary.startups.append(_Startup(RunnerReadyPayload(loaded_plugins=[_QQ]), {_QQ: "adapter"}))
    assert await supervisor._restart_runner("runner_process_missing") is False
    assert len(boundary.spawned) == 3
    assert supervisor._restart_count == 3
    assert supervisor._adapter_recovery_targets == {_QQ}
    await supervisor.stop()
