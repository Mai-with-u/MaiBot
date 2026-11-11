"""CLI shutdown deadlines must not be extended by cancellation-resistant steps."""

from collections import deque
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import ast
import asyncio
import os
import subprocess
import sys
import time
import typing

import pytest

from src.common.python_process import python_environment, python_executable


ROOT = Path(__file__).resolve().parents[2]


def load_runtime(monkeypatch):
    """Load actual shutdown code without worker startup, networking or process locks."""
    events = []

    async def record(name):
        events.append(name)

    def operation(name):
        return lambda: record(name)

    modules = {
        "src.common.logger": {"stop_ws_broadcasts": operation("broadcast")},
        "src.chat.heart_flow.heartflow_manager": {
            "heartflow_manager": SimpleNamespace(shutdown=operation("heart")),
        },
        "src.desktop_pet.service": {"desktop_pet_service": SimpleNamespace(stop=operation("pet"))},
        "src.agent.service": {"agent_service": SimpleNamespace(stop=operation("agent"))},
        "src.config.config": {"config_manager": SimpleNamespace(stop_file_watcher=operation("watcher"))},
        "src.core.event_bus": {"event_bus": SimpleNamespace(emit=lambda **_: record("event"))},
        "src.core.types": {"EventType": SimpleNamespace(ON_STOP="stop")},
        "src.plugin_runtime.integration": {
            "get_plugin_runtime_manager": lambda: SimpleNamespace(stop=operation("plugin")),
        },
        "src.chat.image_system.image_manager": {"image_manager": SimpleNamespace(shutdown=operation("image"))},
        "src.A_memorix.host_service": {"a_memorix_host_service": SimpleNamespace(stop=operation("memory"))},
        "src.services.memory_flow_service": {
            "memory_automation_service": SimpleNamespace(shutdown=operation("writer")),
        },
        "src.emoji_system.emoji_manager": {"emoji_manager": SimpleNamespace(shutdown=lambda: events.append("emoji"))},
        "src.mcp_module.service": {"get_mcp_service": lambda: SimpleNamespace(close=operation("mcp"))},
        "src.common.shutdown_diagnostics": {"shutdown_step": lambda *_: nullcontext()},
        "src.learners.jargon_inference_service": {
            "get_jargon_inference_service": lambda: SimpleNamespace(stop=operation("inference")),
        },
    }
    for name, members in modules.items():
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(**members))
    names = {
        "graceful_shutdown",
        "_shutdown_time_left",
        "_await_shutdown_step",
        "_ShutdownStepIncomplete",
        "_remember_shutdown_step",
        "_finish_shutdown_step",
    }
    source = ast.parse((ROOT / "src/runtime.py").read_text(encoding="utf-8"))
    selected = [
        node
        for node in source.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names
    ]
    namespace = {
        "asyncio": asyncio,
        "time": time,
        "MainSystem": SimpleNamespace,
        "SHUTDOWN_TIMEOUT": 0.08,
        "MEMORY_WRITER_TIMEOUT": 0.01,
        "MEMORY_KERNEL_TIMEOUT": 0.01,
        "FINAL_CLEANUP_TIMEOUT": 0.01,
        "IMAGE_SYNC_TIMEOUT": 0.01,
        "_shutdown_deadline": None,
        "_pending_shutdown_steps": set(),
        "request_shutdown": Mock(),
        "report_phase": Mock(),
        "logger": Mock(),
        "t": lambda key, **_: key,
        "tn": lambda key, *_: key,
        "async_task_manager": SimpleNamespace(stop_and_wait_all_tasks=operation("tasks")),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROOT / "src/runtime.py"), "exec"), namespace)
    return namespace, events, operation


def load_inference_service():
    """Use the real cancellation-resistant stop and submit lock, with no database jobs."""
    source = ast.parse((ROOT / "src/learners/jargon_inference_service.py").read_text(encoding="utf-8"))
    body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    body.extend(
        node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "JargonInferenceService"
    )
    namespace = {
        **vars(typing),
        "asyncio": asyncio,
        "deque": deque,
        "global_config": SimpleNamespace(
            jargon=SimpleNamespace(
                inference=SimpleNamespace(
                    job_retention_days=7,
                    thresholds=[4, 8, 25, 100],
                    stop_after_last_threshold=True,
                )
            )
        ),
        "config_manager": Mock(),
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), "jargon_inference_service.py", "exec"
        ),
        namespace,
    )
    return namespace["JargonInferenceService"]()


@pytest.mark.asyncio
async def test_real_inference_stop_timeout_does_not_close_its_dependencies(monkeypatch):
    """验证真实 stop 吞取消时按预算失败返回，且依赖未被提前销毁。"""
    namespace, events, operation = load_runtime(monkeypatch)
    service = load_inference_service()
    service._loop = asyncio.get_running_loop()
    await service._submit_lock.acquire()
    monkeypatch.setitem(
        sys.modules,
        "src.learners.jargon_inference_service",
        SimpleNamespace(get_jargon_inference_service=lambda: service),
    )
    released = asyncio.Event()

    def release():
        """允许真实 stop 收束，避免测试退出时遗留锁和任务。"""
        released.set()
        if service._submit_lock.locked():
            service._submit_lock.release()

    handle = asyncio.get_running_loop().call_later(0.25, release)
    started = time.monotonic()
    try:
        result = await namespace["graceful_shutdown"](
            SimpleNamespace(app=None, http_server=SimpleNamespace(shutdown=operation("http")))
        )
        elapsed = time.monotonic() - started
        assert result is False
        assert not released.is_set(), f"stop cancellation extended shutdown to {elapsed:.3f}s"
        assert service._stop_task is not None and not service._stop_task.done()
        assert not {"http", "plugin", "writer", "memory"}.intersection(events)
        assert namespace["_pending_shutdown_steps"]
    finally:
        handle.cancel()
        release()
        await service.stop()
        await asyncio.sleep(0)
    assert service._loop is None
    assert not namespace["_pending_shutdown_steps"]


@pytest.mark.asyncio
async def test_finished_inference_stop_precedes_resource_shutdown(monkeypatch):
    """正常完成推断关停后，仍按原顺序关闭 HTTP、插件和存储。"""
    namespace, events, operation = load_runtime(monkeypatch)
    result = await namespace["graceful_shutdown"](
        SimpleNamespace(app=None, http_server=SimpleNamespace(shutdown=operation("http")))
    )
    assert result is True
    for name in ("http", "plugin", "writer", "memory"):
        assert events.index("inference") < events.index(name)


@pytest.mark.asyncio
async def test_exhausted_stage_does_not_start_subsequent_steps(monkeypatch):
    """预算为零时不启动新的关停步骤，也不遗留未等待的协程。"""
    namespace, events, operation = load_runtime(monkeypatch)
    namespace["SHUTDOWN_TIMEOUT"] = 0
    assert (
        await namespace["graceful_shutdown"](
            SimpleNamespace(app=None, http_server=SimpleNamespace(shutdown=operation("http")))
        )
        is False
    )
    assert events == []
    assert not namespace["_pending_shutdown_steps"]


@pytest.mark.asyncio
async def test_remaining_task_cancellation_respects_final_deadline(monkeypatch):
    """最终任务排空同样有界，不能被吞取消的剩余任务延长。"""
    namespace, _events, _operation = load_runtime(monkeypatch)
    released = asyncio.Event()
    started = asyncio.Event()

    async def stubborn():
        """模拟收到取消后仍必须等待排空的后台步骤。"""
        started.set()
        while not released.is_set():
            try:
                await released.wait()
            except asyncio.CancelledError:
                pass

    task = asyncio.create_task(stubborn())
    await started.wait()
    handle = asyncio.get_running_loop().call_later(0.25, released.set)
    try:
        assert await namespace["graceful_shutdown"]() is False
        assert not released.is_set(), "final gather extended shutdown after cancellation"
        assert task in namespace["_pending_shutdown_steps"]
    finally:
        handle.cancel()
        released.set()
        await task
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_outer_cancellation_propagates_without_waiting_for_child_cleanup(monkeypatch):
    """调用方取消直接传播，同时保留仍在清理的子任务所有权。"""
    namespace, _events, _operation = load_runtime(monkeypatch)
    started, released = asyncio.Event(), asyncio.Event()

    async def stubborn():
        """模拟收到取消后仍必须等待排空的后台步骤。"""
        started.set()
        while not released.is_set():
            try:
                await released.wait()
            except asyncio.CancelledError:
                pass

    child = asyncio.create_task(stubborn())
    owner = asyncio.create_task(namespace["_await_shutdown_step"](child, timeout=5, step_name="test"))
    await started.wait()
    owner.cancel()
    handle = asyncio.get_running_loop().call_later(0.25, released.set)
    try:
        done, _ = await asyncio.wait({owner}, timeout=0.1)
        assert owner in done, "caller cancellation waited for resistant child"
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert child in namespace["_pending_shutdown_steps"]
    finally:
        handle.cancel()
        released.set()
        await asyncio.gather(owner, child, return_exceptions=True)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_finished_step_failure_still_allows_later_cleanup(monkeypatch):
    """已经终止的异常步骤继续采用原有后续清理策略。"""
    namespace, events, operation = load_runtime(monkeypatch)

    async def failed():
        events.append("inference")
        raise RuntimeError("state write failed")

    monkeypatch.setitem(
        sys.modules,
        "src.learners.jargon_inference_service",
        SimpleNamespace(get_jargon_inference_service=lambda: SimpleNamespace(stop=failed)),
    )
    assert (
        await namespace["graceful_shutdown"](
            SimpleNamespace(app=None, http_server=SimpleNamespace(shutdown=operation("http")))
        )
        is False
    )
    assert {"http", "plugin", "writer", "memory"}.issubset(events)


def test_worker_finally_exits_after_incomplete_shutdown_without_runner_drain():
    """Execute the real worker finally in a child; it must not re-wait via asyncio.run."""
    code = """
import ast
import asyncio
import os
import runpy
import sys
import traceback
from contextlib import nullcontext
from types import SimpleNamespace
import pytest

helpers = runpy.run_path(TEST_PATH)
patch = pytest.MonkeyPatch()
namespace, events, operation = helpers['load_runtime'](patch)
service = helpers['load_inference_service']()

async def initialize():
    service._loop = asyncio.get_running_loop()
    await service._submit_lock.acquire()

async def schedule():
    raise asyncio.CancelledError

def logging_closed():
    assert not {'http', 'plugin', 'writer', 'memory'}.intersection(events), events
    print('SAFE_EXIT', flush=True)

sys.modules['src.learners.jargon_inference_service'] = SimpleNamespace(get_jargon_inference_service=lambda: service)
sys.modules['src.common.logger'].initialize_ws_handler = lambda loop: None
sys.modules['src.common.memory_diagnostics'] = SimpleNamespace(stop_memory_diagnostics=lambda: None)
system = SimpleNamespace(initialize=initialize, schedule_tasks=schedule, app=None,
                         http_server=SimpleNamespace(shutdown=operation('http')))
namespace['t'] = lambda key, **kwargs: f'{key}: {kwargs}'
namespace['logger'].error.side_effect = lambda *args, **kwargs: print('ERROR', *args, flush=True)
namespace['logger'].warning.side_effect = lambda *args, **kwargs: print('WARNING', *args, flush=True)
namespace.update(os=os, sys=sys, traceback=traceback, loop=None,
    _shutdown_task=None, _shutdown_signal_count=0, _active_main_task=None, _active_main_loop=None,
    raw_main=lambda: system, set_main_loop=lambda value: None,
    application_signal_handlers=lambda handler: nullcontext(),
    _mark_shutdown_and_interrupt=lambda *args: None,
    shutdown_logging=logging_closed, RESTART_EXIT_CODE=42)
source = ast.parse((helpers['ROOT'] / 'src/runtime.py').read_text(encoding='utf-8'))
names = {'_cancel_main_task', '_run_graceful_shutdown', '_run_until_complete',
         '_is_windows_proactor_cancel_race', '_set_active_main_task', '_print_interrupt_exit_notice'}
body = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
body += [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
body += source.body[-1].body
exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), 'src/runtime.py', 'exec'), namespace)
""".replace("TEST_PATH", repr(str(Path(__file__).resolve())))
    result = subprocess.run(
        [python_executable(), "-u", "-c", code],
        cwd=ROOT,
        env=python_environment({**os.environ, "PYTHONUTF8": "1"}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=3,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "SAFE_EXIT" in result.stdout, result.stdout + result.stderr
    assert "startup.event_loop_closed" in result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["enter", "exit"])
async def test_diagnostic_failure_cannot_leave_unowned_stop_or_close_dependencies(monkeypatch, failure_phase):
    """诊断失败不能跳过组件关停，也不能让未完成的步骤失去所有者。"""
    namespace, events, operation = load_runtime(monkeypatch)
    service = load_inference_service()
    service._loop = asyncio.get_running_loop()
    await service._submit_lock.acquire()
    monkeypatch.setitem(
        sys.modules,
        "src.learners.jargon_inference_service",
        SimpleNamespace(get_jargon_inference_service=lambda: service),
    )

    class BrokenDiagnostic:
        """分别模拟诊断线程启动或退出日志失败。"""

        def __enter__(self):
            """在组件协程尚未启动时注入准备失败。"""
            if failure_phase == "enter":
                raise RuntimeError("diagnostic thread start failed")

        def __exit__(self, *_):
            raise RuntimeError("diagnostic logging failed")

    monkeypatch.setitem(
        sys.modules,
        "src.common.shutdown_diagnostics",
        SimpleNamespace(
            shutdown_step=lambda name: BrokenDiagnostic() if name == "停止黑话推断" else nullcontext(),
        ),
    )
    try:
        result = await namespace["graceful_shutdown"](
            SimpleNamespace(
                app=None,
                http_server=SimpleNamespace(shutdown=operation("http")),
            )
        )
        await asyncio.sleep(0)
        assert result is False
        assert not {"http", "plugin", "writer", "memory"}.intersection(events)
        if failure_phase == "enter":
            assert service._stop_task is None
            assert not namespace["_pending_shutdown_steps"]
        else:
            assert service._stop_task is not None
            assert namespace["_pending_shutdown_steps"]
    finally:
        service._submit_lock.release()
        await service.stop()
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("exhaust_after", ["inference", "image"])
async def test_reserved_memory_budget_requires_finished_producers_not_only_no_pending(monkeypatch, exhaust_after):
    """已完成的步骤耗尽前置预算时，只有生产者确已停止才能进入预留存储阶段。"""
    namespace, events, _operation = load_runtime(monkeypatch)
    now = [100.0]
    namespace["time"] = SimpleNamespace(monotonic=lambda: now[0])
    active = {"pet", "heart", "agent"}
    unsafe_closures = []

    async def complete(name):
        """记录已完成步骤，在选定边界模拟耗尽前置阶段预算。"""
        events.append(name)
        active.discard(name)
        if name == exhaust_after:
            # 前置阶段是 50ms；总预算还有 writer/kernel/final 合计 30ms。
            now[0] += 0.055

    async def close_storage(name):
        """记录打开预留预算时是否仍有上游生产者活跃。"""
        events.append(name)
        if active:
            unsafe_closures.append((name, sorted(active)))

    modules = {
        "src.learners.jargon_inference_service": {
            "get_jargon_inference_service": lambda: SimpleNamespace(stop=lambda: complete("inference")),
        },
        "src.desktop_pet.service": {"desktop_pet_service": SimpleNamespace(stop=lambda: complete("pet"))},
        "src.chat.heart_flow.heartflow_manager": {
            "heartflow_manager": SimpleNamespace(shutdown=lambda: complete("heart")),
        },
        "src.agent.service": {"agent_service": SimpleNamespace(stop=lambda: complete("agent"))},
        "src.chat.image_system.image_manager": {
            "image_manager": SimpleNamespace(shutdown=lambda: complete("image")),
        },
        "src.services.memory_flow_service": {
            "memory_automation_service": SimpleNamespace(shutdown=lambda: close_storage("writer")),
        },
        "src.A_memorix.host_service": {
            "a_memorix_host_service": SimpleNamespace(stop=lambda: close_storage("memory")),
        },
    }
    for name, members in modules.items():
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(**members))

    result = await namespace["graceful_shutdown"]()
    assert not namespace["_pending_shutdown_steps"]
    assert not unsafe_closures, (events, unsafe_closures)
    if exhaust_after == "inference":
        assert result is False
        assert active == {"pet", "heart", "agent"}
        assert "writer" not in events and "memory" not in events
    else:
        assert result is True
        assert not active
        assert events.index("image") < events.index("writer") < events.index("memory")
