from contextlib import suppress
from types import ModuleType, SimpleNamespace
from typing import Any

import asyncio
import builtins
import importlib
import pytest
import sys

from src.core.event_bus import EventBus
from src.core.process_shutdown import run_process_shutdown
from src.core.types import EventType


def _event_value(event: Any) -> str:
    if isinstance(event, EventType):
        return event.value
    return "" if event is None else str(event)


def _patch_module_attr(monkeypatch: pytest.MonkeyPatch, module_name: str, attr: str, value: Any) -> None:
    module = sys.modules.get(module_name)
    if module is None:
        module = ModuleType(module_name)
        monkeypatch.setitem(sys.modules, module_name, module)
    monkeypatch.setattr(module, attr, value, raising=False)


def _sync_step(calls: list[str], name: str):
    def _call(*_args: Any, **_kwargs: Any) -> None:
        calls.append(name)

    return _call


def _async_step(calls: list[str], name: str, leftover_guard: SimpleNamespace):
    async def _call(*_args: Any, **_kwargs: Any) -> None:
        leftover = leftover_guard.task
        if name == "tasks" and leftover is not None:
            assert not leftover.done()
        calls.append(name)

    return _call


async def _hang_forever() -> None:
    await asyncio.Event().wait()


async def _cleanup_leftover(task: asyncio.Task[None]) -> None:
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@pytest.fixture
def shutdown_fakes(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """用假协作对象替换停机步骤的延迟导入目标，并按调用顺序记录名称。"""

    calls: list[str] = []
    leftover_guard = SimpleNamespace(task=None)

    async def fake_bridge_event(*args: Any, **kwargs: Any) -> tuple[bool, None]:
        event = args[0] if args else kwargs.get("event_type_value", kwargs.get("event_type"))
        value = _event_value(event)
        calls.append("bridge_event" if value == EventType.ON_STOP.value else f"bridge_event:{value}")
        return True, None

    async def fake_emit(event_type: Any = None, message: Any = None, **kwargs: Any) -> tuple[bool, None]:
        event = event_type if event_type is not None else kwargs.get("event_type")
        value = _event_value(event)
        calls.append("emit" if value == EventType.ON_STOP.value else f"emit:{value}")
        return True, None

    fake_runtime = SimpleNamespace(
        bridge_event=fake_bridge_event,
        stop=_async_step(calls, "runtime.stop", leftover_guard),
    )
    fake_webui = SimpleNamespace(shutdown=_async_step(calls, "webui", leftover_guard))

    def get_plugin_runtime_manager() -> SimpleNamespace:
        return fake_runtime

    def get_mcp_service() -> SimpleNamespace:
        return SimpleNamespace(close=_async_step(calls, "mcp", leftover_guard))

    _patch_module_attr(monkeypatch, "src.common.shutdown", "request_shutdown", _sync_step(calls, "request_shutdown"))
    _patch_module_attr(monkeypatch, "src.common.runtime_loop", "set_main_loop", _sync_step(calls, "set_main_loop"))
    _patch_module_attr(monkeypatch, "src.core.event_bus", "event_bus", SimpleNamespace(emit=fake_emit))
    _patch_module_attr(
        monkeypatch,
        "src.emoji_system.emoji_manager",
        "emoji_manager",
        SimpleNamespace(shutdown=_sync_step(calls, "emoji")),
    )
    _patch_module_attr(
        monkeypatch,
        "src.services.memory_flow_service",
        "memory_automation_service",
        SimpleNamespace(shutdown=_async_step(calls, "memory_automation", leftover_guard)),
    )
    _patch_module_attr(
        monkeypatch,
        "src.A_memorix.host_service",
        "a_memorix_host_service",
        SimpleNamespace(stop=_async_step(calls, "memorix", leftover_guard)),
    )
    _patch_module_attr(
        monkeypatch,
        "src.plugin_runtime.integration",
        "get_plugin_runtime_manager",
        get_plugin_runtime_manager,
    )
    _patch_module_attr(
        monkeypatch,
        "src.manager.async_task_manager",
        "async_task_manager",
        SimpleNamespace(stop_and_wait_all_tasks=_async_step(calls, "tasks", leftover_guard)),
    )
    _patch_module_attr(monkeypatch, "src.mcp_module.service", "get_mcp_service", get_mcp_service)
    _patch_module_attr(
        monkeypatch,
        "src.config.config",
        "config_manager",
        SimpleNamespace(stop_file_watcher=_async_step(calls, "watcher", leftover_guard)),
    )

    return SimpleNamespace(calls=calls, leftover_guard=leftover_guard, webui_server=fake_webui)


@pytest.mark.asyncio
async def test_full模式按指定顺序关闭全部协作组件(shutdown_fakes: SimpleNamespace) -> None:
    """full：webui → emoji → memory_automation → memorix → bridge_event(on_stop) → runtime.stop → tasks → mcp → watcher → set_main_loop。"""

    leftover = asyncio.create_task(_hang_forever(), name="process-shutdown-leftover")
    shutdown_fakes.leftover_guard.task = leftover
    try:
        await run_process_shutdown("full", webui_server=shutdown_fakes.webui_server)
        assert shutdown_fakes.calls == [
            "webui",
            "emoji",
            "memory_automation",
            "memorix",
            "bridge_event",
            "runtime.stop",
            "tasks",
            "mcp",
            "watcher",
            "set_main_loop",
        ]
        assert not leftover.done()
    finally:
        await _cleanup_leftover(leftover)


@pytest.mark.asyncio
async def test_stepwise_timeout模式按指定顺序关停且不调用memorix_mcp_emoji_watcher(
    shutdown_fakes: SimpleNamespace,
) -> None:
    """stepwise_timeout：request_shutdown → webui → emit(ON_STOP) → runtime.stop → tasks → 取消剩余任务。"""

    leftover = asyncio.create_task(_hang_forever(), name="process-shutdown-leftover")
    shutdown_fakes.leftover_guard.task = leftover
    try:
        await run_process_shutdown("stepwise_timeout", webui_server=shutdown_fakes.webui_server)
        assert shutdown_fakes.calls == [
            "request_shutdown",
            "webui",
            "emit",
            "runtime.stop",
            "tasks",
        ]
        assert leftover.cancelled()
        for forbidden in ("memorix", "mcp", "emoji", "watcher"):
            assert forbidden not in shutdown_fakes.calls
    finally:
        await _cleanup_leftover(leftover)


@pytest.mark.asyncio
async def test_restart_partial模式只触发emit与runtime_stop与tasks(shutdown_fakes: SimpleNamespace) -> None:
    """restart_partial：emit(ON_STOP) → runtime.stop → tasks，且只做这三步。"""

    leftover = asyncio.create_task(_hang_forever(), name="process-shutdown-leftover")
    shutdown_fakes.leftover_guard.task = leftover
    try:
        await run_process_shutdown("restart_partial", webui_server=shutdown_fakes.webui_server)
        assert shutdown_fakes.calls == [
            "emit",
            "runtime.stop",
            "tasks",
        ]
        assert not leftover.done()
        for forbidden in (
            "request_shutdown",
            "webui",
            "emoji",
            "memory_automation",
            "memorix",
            "bridge_event",
            "mcp",
            "watcher",
            "set_main_loop",
        ):
            assert forbidden not in shutdown_fakes.calls
    finally:
        await _cleanup_leftover(leftover)


@pytest.mark.asyncio
async def test_未注入ipc桥接时emit不导入plugin_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """未调用 EventBus.set_ipc_bridge 时，emit 不得 import plugin_runtime。"""

    bus = EventBus()

    async def handler(message: Any) -> tuple[bool, Any]:
        return True, message

    bus.subscribe(EventType.ON_STOP, handler, name="keep-path", intercept=True)

    imported: list[str] = []
    real_import = builtins.__import__
    real_import_module = importlib.import_module

    def tracking_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0):
        if "plugin_runtime" in name:
            imported.append(name)
        return real_import(name, globals, locals, fromlist, level)

    def tracking_import_module(name: str, package: str | None = None):
        if "plugin_runtime" in name:
            imported.append(name)
        return real_import_module(name, package)

    monkeypatch.setattr(builtins, "__import__", tracking_import)
    monkeypatch.setattr(importlib, "import_module", tracking_import_module)

    continue_flag, _ = await bus.emit(event_type=EventType.ON_STOP)
    assert continue_flag is True
    assert imported == []


@pytest.mark.asyncio
async def test_空handler时emit提前返回且不调用已注入的ipc桥接() -> None:
    """即使已 set_ipc_bridge，空 handler 仍应提前返回，不调用 bridge。"""

    bus = EventBus()
    bridge_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def fake_bridge(*args: Any, **kwargs: Any) -> tuple[bool, None]:
        bridge_calls.append((args, kwargs))
        return True, None

    bus.set_ipc_bridge(fake_bridge)
    continue_flag, message = await bus.emit(event_type=EventType.ON_STOP)

    assert continue_flag is True
    assert message is None
    assert bridge_calls == []
