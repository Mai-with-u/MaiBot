from types import ModuleType
import asyncio
import logging
import sys
import threading

import pytest

from pytests.startup_test.shutdown_fixture import load_bot_functions
from src.common import shutdown_diagnostics
from src.common.logger import WebSocketLogHandler
from src.manager.async_task_manager import AsyncTaskManager


def test_shutdown_stops_chat_before_dependencies(monkeypatch):
    events = []
    namespace, system, _ = load_bot_functions(events, install_module=lambda k, v: monkeypatch.setitem(sys.modules, k, v))
    assert asyncio.run(namespace["graceful_shutdown"](system))
    assert events.index("app_stop") < events.index("chat_stop")
    assert events.index("chat_stop") < events.index("agent_stop") < events.index("plugin_stop")
    assert events.index("chat_stop") < events.index("memory_producer_stop") < events.index("memory_stop")


def test_shutdown_step_reports_duration_on_error(monkeypatch):
    messages = []
    monkeypatch.setattr(shutdown_diagnostics.logger, "info", messages.append)

    async def run():
        with pytest.raises(ValueError):
            with shutdown_diagnostics.shutdown_step("probe"):
                raise ValueError("failure")

    asyncio.run(run())
    assert "step=probe" in messages[-1]
    assert "耗时=" in messages[-1] and "outcome=failed" in messages[-1]


def test_shutdown_sampler_captures_blocked_loop(monkeypatch):
    sampled = threading.Event()
    messages = []

    def record(message):
        messages.append(message)
        sampled.set()

    monkeypatch.setattr(shutdown_diagnostics.logger, "warning", record)

    async def blocked_shutdown():
        secret = "sensitive-shutdown-local"
        with shutdown_diagnostics.shutdown_step("cancel_tasks", warn_after=0.02, interval=0.005):
            assert sampled.wait(2.0)
        await asyncio.sleep(0.03)
        assert len(messages) == 1
        assert "blocked_shutdown" in messages[0]
        assert "step=cancel_tasks" in messages[0]
        assert "主循环无心跳=" in messages[0]
        assert secret not in messages[0]
        assert not any(t.name == "shutdown-stack-sampler" for t in threading.enumerate())

    asyncio.run(blocked_shutdown())


@pytest.mark.parametrize("already_started", [False, True])
def test_ws_stop_drains_tasks_and_rejects_queued_callbacks(monkeypatch, already_started):
    calls = []
    module = ModuleType("src.webui.logs_ws")

    async def broadcast_log(data):
        calls.append(data)
        await asyncio.Event().wait()

    module.broadcast_log = broadcast_log
    monkeypatch.setitem(sys.modules, module.__name__, module)

    async def run():
        loop = asyncio.get_running_loop()
        handler = WebSocketLogHandler()
        handler.set_loop(loop)
        handler._schedule_broadcast({"before": True}, loop)
        if already_started:
            await asyncio.sleep(0)
        loop.call_soon(handler._schedule_broadcast, {"queued": True}, loop)
        await handler.stop()
        handler._schedule_broadcast({"after": True}, loop)
        handler.emit(logging.LogRecord("test", logging.INFO, "", 0, "after", (), None))
        await asyncio.sleep(0)
        assert calls == ([{"before": True}] if already_started else [])
        assert not handler._broadcast_tasks
        handler.close()

    asyncio.run(run())


def test_task_manager_propagates_caller_cancellation():
    async def run():
        manager = AsyncTaskManager()
        cleanup_started = asyncio.Event()
        release = asyncio.Event()

        async def worker():
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await release.wait()

        task = asyncio.create_task(worker(), name="probe")
        manager.tasks["probe"] = task
        await asyncio.sleep(0)
        stop = asyncio.create_task(manager.stop_and_wait_all_tasks())
        await cleanup_started.wait()
        stop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop
        assert manager.abort_flag.is_set()
        assert "probe" in manager.tasks
        assert task.done()

    asyncio.run(run())
