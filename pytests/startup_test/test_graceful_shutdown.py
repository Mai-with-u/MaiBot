"""不启动真实 Bot；覆盖信号、关闭次序、失败状态以及 Runner 的重启协议。"""

from types import SimpleNamespace
from unittest.mock import Mock

import ast
import asyncio
import os
import signal
import subprocess
import sys

import pytest
from uvicorn import Config, Server

from pytests.startup_test.shutdown_fixture import ROOT, load_bot_functions
from src.common import process_runner
from src.common.shutdown import application_signal_handlers


@pytest.fixture
def bot(monkeypatch):
    events = []

    def make(**kwargs):
        ns, system, _ = load_bot_functions(
            events, install_module=lambda k, v: monkeypatch.setitem(sys.modules, k, v), **kwargs
        )
        return ns, system, events

    return make


@pytest.mark.parametrize("fail", [False, True])
def test_shutdown_once_and_memory_before_remaining_cancellation(bot, fail):
    ns, system, events = bot(memory_failure=fail)
    loop = asyncio.new_event_loop()

    async def remaining():
        try:
            await asyncio.Event().wait()
        finally:
            events.append("remaining_cancelled")

    try:
        loop.create_task(remaining())
        loop.run_until_complete(asyncio.sleep(0))
        assert ns["_run_graceful_shutdown"](loop, system) is (not fail)
        assert ns["_run_graceful_shutdown"](loop, system) is (not fail)
        assert events.count("memory_stop") == 1
        assert events.count("image_sync") == 1
        assert (
            events.index("image_sync")
            < events.index("memory_producer_stop")
            < events.index("memory_stop")
            < events.index("remaining_cancelled")
        )
        assert ("startup.shutdown_completed" in events) is (not fail)
        if not fail:
            assert events.index("persist") < events.index("metadata_close") < events.index("writer_lock_release")
    finally:
        loop.close()


@pytest.mark.parametrize(
    "signals",
    [
        (signal.SIGTERM,),
        (signal.SIGTERM, signal.SIGTERM),
        (signal.SIGINT, signal.SIGTERM),
        (signal.SIGTERM, signal.SIGINT),
    ],
)
def test_worker_signals_survive_uvicorn_and_cancel_only_once(bot, signals):
    ns, system, events = bot()
    loop = asyncio.new_event_loop()
    ns["_active_main_loop"] = loop
    original_capture = Server.capture_signals
    original_term = signal.getsignal(signal.SIGTERM)

    class TestServer(Server):
        async def _serve(self, sockets=None):
            for sig in signals:
                signal.raise_signal(sig)
            # uvicorn 若绕过 capture_signals 接管信号，应限时明确失败。
            await asyncio.sleep(5)
            raise AssertionError("停止信号被 uvicorn 截获，未交给 Worker 的关闭处理器")

    try:
        with application_signal_handlers(ns["_mark_shutdown_and_interrupt"]):
            server = TestServer(Config(app=None, log_config=None))
            task = loop.create_task(server.serve())
            ns["_active_main_task"] = task
            with pytest.raises(asyncio.CancelledError):
                loop.run_until_complete(task)
            assert task.cancelling() == 1
            assert ns["_run_graceful_shutdown"](loop, system)
            signal.raise_signal(signal.SIGTERM)
            assert ns["_run_graceful_shutdown"](loop, system)
            assert events.count("memory_stop") == 1
        assert Server.capture_signals is original_capture
        assert signal.getsignal(signal.SIGTERM) is original_term
    finally:
        loop.close()


def test_repeated_signal_during_memory_shutdown_does_not_cancel_it(bot):
    ns, system, events = bot(memory_delay=0.02)
    loop = asyncio.new_event_loop()
    ns["_active_main_loop"] = loop
    try:
        with application_signal_handlers(ns["_mark_shutdown_and_interrupt"]):
            loop.call_later(0.01, signal.raise_signal, signal.SIGTERM)
            assert ns["_run_graceful_shutdown"](loop, system)
        assert events.count("memory_stop") == 1
        assert "metadata_close" in events
    finally:
        loop.close()


def test_missing_uvicorn_capture_signals_fails_before_changing_handlers(monkeypatch):
    monkeypatch.delattr(Server, "capture_signals")
    install_handler = Mock()
    monkeypatch.setattr(signal, "signal", install_handler)
    with pytest.raises(AttributeError, match="capture_signals"):
        with application_signal_handlers(Mock()):
            pytest.fail("缺少 capture_signals 时不应进入信号守护作用域")
    install_handler.assert_not_called()


def test_signal_guard_fails_boundedly_when_uvicorn_bypasses_capture(bot, monkeypatch):
    original_capture = Server.capture_signals

    async def bypass_capture(server, sockets=None):
        # 模拟未来 serve() 不再读取被替换的类属性，而直接捕获应用信号。
        with original_capture(server):
            await server._serve(sockets)

    monkeypatch.setattr(Server, "serve", bypass_capture)
    with pytest.raises(AssertionError, match="停止信号被 uvicorn 截获"):
        test_worker_signals_survive_uvicorn_and_cancel_only_once(bot, (signal.SIGTERM,))


@pytest.mark.parametrize("image_exhausts_stage", [False, True])
def test_image_sync_timeout_still_runs_memory_cleanup(bot, image_exhausts_stage):
    ns, system, events = bot()
    # 缩小所有阶段但保持比例；使用真实 wait_for 验证超时取消后的实际协程执行。
    for name in (
        "SHUTDOWN_TIMEOUT",
        "IMAGE_SYNC_TIMEOUT",
        "MEMORY_WRITER_TIMEOUT",
        "MEMORY_KERNEL_TIMEOUT",
        "FINAL_CLEANUP_TIMEOUT",
    ):
        ns[name] /= 100
    if image_exhausts_stage:
        ns["SHUTDOWN_TIMEOUT"] = (
            ns["IMAGE_SYNC_TIMEOUT"]
            + ns["MEMORY_WRITER_TIMEOUT"]
            + ns["MEMORY_KERNEL_TIMEOUT"]
            + ns["FINAL_CLEANUP_TIMEOUT"]
        )

    async def image_sync():
        events.append("image_sync")
        try:
            await asyncio.Event().wait()
        finally:
            events.append("image_sync_cancelled")

    sys.modules["src.chat.image_system.image_manager"].image_manager.shutdown = image_sync
    loop = asyncio.new_event_loop()
    try:
        assert not ns["_run_graceful_shutdown"](loop, system)
        assert not ns["_run_graceful_shutdown"](loop, system)
        assert events.count("image_sync") == events.count("memory_producer_stop") == events.count("memory_stop") == 1
        assert events.index("image_sync_cancelled") < events.index("memory_producer_stop") < events.index("memory_stop")
        assert "metadata_close" in events
        assert "manager_stop" in events
        assert "startup.shutdown_completed" not in events
    finally:
        loop.close()


@pytest.mark.parametrize("image_failure", ["runtime_error", "cancelled_error", "cancelled_description_task"])
def test_image_sync_failure_or_cancellation_is_not_success(bot, image_failure):
    ns, system, events = bot()

    if image_failure == "cancelled_description_task":
        # 只加载真实 shutdown 方法，隔离图片管理器的数据库及模型依赖。
        path = ROOT / "src/chat/image_system/image_manager.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        manager = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ImageManager")
        shutdown = next(
            node for node in manager.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "shutdown"
        )
        image_ns = {"asyncio": asyncio}
        exec(compile(ast.Module(body=[shutdown], type_ignores=[]), str(path), "exec"), image_ns)

    async def image_sync():
        events.append("image_sync")
        if image_failure != "cancelled_description_task":
            error = RuntimeError if image_failure == "runtime_error" else asyncio.CancelledError
            raise error("fixture image sync")

        async def sync_description():
            # 让真实 shutdown 的 gather 先接管子任务，再取消子任务自身。
            await asyncio.sleep(0)
            asyncio.current_task().cancel()
            await asyncio.sleep(0)

        sync_task = asyncio.create_task(sync_description())
        image_manager = SimpleNamespace(_pending_description_tasks={}, _description_sync_tasks={sync_task})
        sync_task.add_done_callback(image_manager._description_sync_tasks.discard)
        await image_ns["shutdown"](image_manager)

    async def remaining():
        try:
            await asyncio.Event().wait()
        finally:
            events.append("remaining_cancelled")

    sys.modules["src.chat.image_system.image_manager"].image_manager.shutdown = image_sync
    loop = asyncio.new_event_loop()
    try:
        remaining_task = loop.create_task(remaining())
        loop.run_until_complete(asyncio.sleep(0))
        shutdown_task = loop.create_task(ns["graceful_shutdown"](system))
        assert loop.run_until_complete(shutdown_task) is False
        assert shutdown_task.cancelling() == 0
        cleanup = [
            "image_sync",
            "memory_producer_stop",
            "memory_stop",
            "emoji_stop",
            "mcp_close",
            "manager_stop",
            "remaining_cancelled",
        ]
        assert all(events.count(step) == 1 for step in cleanup)
        assert [events.index(step) for step in cleanup] == sorted(events.index(step) for step in cleanup)
        assert "metadata_close" in events
        assert "writer_lock_release" in events
        assert "startup.shutdown_completed" not in events
        if image_failure != "runtime_error":
            assert "等待图片描述同步 内部任务被取消，继续执行后续关停步骤" in events
    finally:
        remaining_task.cancel()
        loop.run_until_complete(asyncio.gather(remaining_task, return_exceptions=True))
        loop.close()


@pytest.mark.parametrize("cancel_target", ["helper", "graceful_shutdown"])
def test_external_shutdown_task_cancellation_propagates(bot, cancel_target):
    ns, system, events = bot()

    async def run():
        started = asyncio.Event()

        async def image_sync():
            started.set()
            await asyncio.Event().wait()

        sys.modules["src.chat.image_system.image_manager"].image_manager.shutdown = image_sync
        awaitable = (
            ns["_await_shutdown_step"](image_sync(), timeout=5.0, step_name="等待图片描述同步")
            if cancel_target == "helper"
            else ns["graceful_shutdown"](system)
        )
        task = asyncio.create_task(awaitable)
        try:
            await asyncio.wait_for(started.wait(), timeout=1.0)
            assert task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1.0)
            assert task.cancelled()
            assert task.cancelling() == 1
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert "等待图片描述同步 内部任务被取消，继续执行后续关停步骤" not in events
    assert "startup.shutdown_completed" not in events


@pytest.mark.parametrize("pre_cleanup_exhausted", [False, True])
@pytest.mark.parametrize("writer_exhausted", [False, True])
def test_cleanup_stages_reserve_budget_after_earlier_timeouts(
    bot, monkeypatch, pre_cleanup_exhausted, writer_exhausted
):
    ns, system, events = bot()
    clock = [0.0]
    ns["time"] = SimpleNamespace(monotonic=lambda: clock[0])
    timeouts = {}

    async def wait_for(awaitable, timeout):
        # 模拟 wait_for(timeout=0) 的真实语义：协程体根本不会获得执行机会。
        if timeout <= 0:
            awaitable.close()
            raise asyncio.TimeoutError
        await awaitable
        step = events[-1]
        timeouts[step] = timeout
        if step == "plugin_stop":
            clock[0] = ns["_shutdown_deadline"] - (0 if pre_cleanup_exhausted else ns["IMAGE_SYNC_TIMEOUT"])
        if (
            step == "image_sync"
            or (step == "memory_producer_stop" and writer_exhausted)
            or step == "writer_lock_release"
        ):
            clock[0] += timeout
            raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    assert not asyncio.run(ns["graceful_shutdown"](system))
    assert ("image_sync" in events) is (not pre_cleanup_exhausted)
    assert events.count("memory_producer_stop") == events.count("memory_stop") == 1
    assert timeouts["memory_producer_stop"] == ns["MEMORY_WRITER_TIMEOUT"]
    assert timeouts["writer_lock_release"] == ns["MEMORY_KERNEL_TIMEOUT"]
    assert timeouts["manager_stop"] == ns["FINAL_CLEANUP_TIMEOUT"]
    assert clock[0] <= ns["SHUTDOWN_TIMEOUT"]
    assert "startup.shutdown_completed" not in events


def test_shutdown_step_timeout_and_cancelled_shutdown_are_failures(bot):
    ns, system, events = bot()
    loop = asyncio.new_event_loop()
    try:
        assert not loop.run_until_complete(
            ns["_await_shutdown_step"](asyncio.sleep(1), timeout=0.001, step_name="test")
        )
        cancelled = loop.create_task(asyncio.sleep(1))
        cancelled.cancel()
        ns["_shutdown_task"] = cancelled
        assert not ns["_run_graceful_shutdown"](loop, system)
        assert "startup.shutdown_completed" not in events
    finally:
        loop.close()


def test_shutdown_steps_share_one_deadline(bot, monkeypatch):
    ns, system, events = bot()
    clock = [0.0]
    ns["time"] = SimpleNamespace(monotonic=lambda: clock[0])
    ns["_shutdown_deadline"] = 50.0
    timeouts = []

    async def timeout_step(awaitable, timeout):
        await awaitable
        timeouts.append(timeout)
        clock[0] += timeout
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", timeout_step)

    async def run():
        for _ in range(3):
            assert not await ns["_await_shutdown_step"](asyncio.sleep(0), timeout=30, step_name="fixture")

    asyncio.run(run())
    assert timeouts == [30.0, 20.0, 0.0]
    assert clock[0] == 50


def test_emoji_failure_does_not_skip_later_cleanup(bot):
    ns, system, events = bot()

    def fail():
        raise RuntimeError("fixture emoji cleanup")

    sys.modules["src.emoji_system.emoji_manager"].emoji_manager.shutdown = fail
    loop = asyncio.new_event_loop()
    try:
        assert not ns["_run_graceful_shutdown"](loop, system)
        assert "mcp_close" in events
        assert "manager_stop" in events
        assert "startup.shutdown_completed" not in events
    finally:
        loop.close()


@pytest.fixture
def runner(monkeypatch):
    handlers = {}
    clock = [0.0]

    def install_handler(sig, handler):
        previous = handlers.get(sig, signal.getsignal(sig))
        handlers[sig] = handler
        return previous

    monkeypatch.setattr(process_runner.signal, "signal", install_handler)
    monkeypatch.setattr(process_runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(process_runner.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr(process_runner, "WORKER_SHUTDOWN_TIMEOUT", 0.3)
    logger = Mock()
    return handlers, clock, logger


@pytest.mark.parametrize(
    "stop_signals",
    [
        (signal.SIGTERM,),
        (signal.SIGTERM, signal.SIGTERM),
        (signal.SIGINT, signal.SIGTERM),
        (signal.SIGTERM, signal.SIGINT),
    ],
)
@pytest.mark.parametrize("platform_name", ["nt", "posix"])
def test_runner_forwards_once_and_waits(runner, monkeypatch, stop_signals, platform_name):
    handlers, clock, logger = runner
    sent = []
    monkeypatch.setattr(process_runner, "os", SimpleNamespace(name=platform_name))
    # 在 POSIX 上也能静态验证 Windows 分支，而不依赖该平台具有控制台常量。
    monkeypatch.setattr(signal, "SIGBREAK", 21, raising=False)
    monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", 1, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)

    class Worker:
        returncode = None
        waits = 0

        def poll(self):
            return self.returncode

        def send_signal(self, sig):
            sent.append(sig)

        def wait(self, timeout):
            self.waits += 1
            clock[0] += 0.05
            if self.waits == 1:
                for sig in stop_signals:
                    handlers[sig](sig, None)
            if self.waits == 4:
                self.returncode = 0
                return 0
            raise subprocess.TimeoutExpired("fixture", timeout)

    worker = Worker()
    monkeypatch.setattr(process_runner.subprocess, "Popen", lambda *a, **kw: worker)
    assert process_runner.supervise_worker(["fixture"], {}, logger) == 0
    assert worker.waits == 4
    assert sent == [signal.CTRL_BREAK_EVENT if platform_name == "nt" else signal.SIGTERM]


def test_stop_racing_restart_suppresses_new_worker(runner, monkeypatch):
    handlers, clock, logger = runner
    finished = Mock(returncode=42)

    def poll():
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        return 42

    finished.poll.side_effect = poll
    popen = Mock(return_value=finished)
    monkeypatch.setattr(process_runner.subprocess, "Popen", popen)
    assert process_runner.supervise_worker(["fixture"], {}, logger) == 42
    popen.assert_called_once()
    finished.send_signal.assert_not_called()


def test_runner_timeout_kills_and_returns_failure(runner, monkeypatch):
    handlers, clock, logger = runner
    worker = Mock(returncode=None)
    worker.poll.side_effect = lambda: worker.returncode

    def wait(timeout):
        if worker.returncode is not None:
            return worker.returncode
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        clock[0] += 0.1
        raise subprocess.TimeoutExpired("fixture", timeout)

    worker.wait.side_effect = wait
    worker.kill.side_effect = lambda: setattr(worker, "returncode", 1)
    monkeypatch.setattr(process_runner.subprocess, "Popen", lambda *a, **kw: worker)
    assert process_runner.supervise_worker(["fixture"], {}, logger) == 1
    worker.send_signal.assert_called_once()
    worker.kill.assert_called_once()
    assert clock[0] >= 0.3
    logger.error.assert_called_once()


def test_runner_kill_reap_timeout_returns_failure(runner, monkeypatch):
    handlers, clock, logger = runner
    worker = Mock(returncode=None)
    worker.poll.return_value = None

    def wait(timeout):
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        clock[0] += 0.1
        raise subprocess.TimeoutExpired("fixture", timeout)

    worker.wait.side_effect = wait
    monkeypatch.setattr(process_runner.subprocess, "Popen", lambda *a, **kw: worker)

    assert process_runner.supervise_worker(["fixture"], {}, logger) == 1
    worker.kill.assert_called_once()
    assert worker.wait.call_args_list[-1].kwargs == {"timeout": 5}
    logger.error.assert_any_call("Worker 强制终止后仍无法回收；应用关闭未完成")


def test_runner_restart_42_and_exited_worker_signal(runner, monkeypatch):
    handlers, clock, logger = runner
    workers = [Mock(returncode=42), Mock(returncode=0)]
    for worker in workers:
        worker.poll.side_effect = lambda w=worker: w.returncode
    popen = Mock(side_effect=workers)
    monkeypatch.setattr(process_runner.subprocess, "Popen", popen)
    assert process_runner.supervise_worker(["fixture"], {}, logger) == 0
    assert popen.call_count == 2
    assert clock[0] >= 1

    finished = Mock(returncode=0)
    previous_handler = handlers[signal.SIGTERM]

    def poll():
        assert handlers[signal.SIGTERM] is not previous_handler
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        return 0

    finished.poll.side_effect = poll
    popen.side_effect = [finished]
    assert process_runner.supervise_worker(["fixture"], {}, logger) == 0
    finished.send_signal.assert_not_called()


@pytest.mark.parametrize("fail,restart,code", [(False, False, 0), (True, False, 1), (False, True, 42), (True, True, 1)])
def test_real_worker_entrypoint_exit_semantics(fail, restart, code):
    args = [sys.executable, "-u", "-m", "pytests.startup_test.shutdown_fixture", "--worker", "--self-stop"]
    args += ["--fail"] if fail else []
    args += ["--restart"] if restart else []
    result = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ | {"PYTHONUTF8": "1"},
        timeout=10,
    )
    assert result.returncode == code, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines.count("image_sync") == lines.count("memory_producer_stop") == lines.count("memory_stop") == 1
    assert lines.index("image_sync") < lines.index("memory_producer_stop") < lines.index("memory_stop")
    assert ("startup.shutdown_completed" in result.stdout) is (not fail)
    assert ("metadata_close" in result.stdout) is (not fail)


def test_real_worker_consumes_stop_received_during_startup():
    result = subprocess.run(
        [
            sys.executable,
            "-u",
            "-m",
            "pytests.startup_test.shutdown_fixture",
            "--worker",
            "--self-stop",
            "--early-stop",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ | {"PYTHONUTF8": "1"},
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "worker_booting" in result.stdout
    assert "initialize" not in result.stdout
    assert result.stdout.splitlines().count("memory_stop") == 1
    assert "startup.shutdown_completed" in result.stdout


def test_early_worker_guard_runs_before_business_imports(bot, monkeypatch):
    ns, system, events = bot()
    tree = ast.parse((ROOT / "src/runtime.py").read_text(encoding="utf-8"))
    business_import = next(
        i for i, node in enumerate(tree.body) if isinstance(node, ast.ImportFrom) and node.module.startswith("src.")
    )
    guard = next(
        node
        for node in tree.body[:business_import]
        if isinstance(node, ast.If) and "_install_early_worker_signal_handlers" in ast.unparse(node)
    )
    installed = {}
    monkeypatch.setattr(signal, "signal", lambda sig, handler: installed.setdefault(sig, handler))
    ns.update(__name__="__main__", os=SimpleNamespace(environ={"MAIBOT_WORKER_PROCESS": "1"}))
    exec(compile(ast.Module(body=[guard], type_ignores=[]), "bot.py", "exec"), ns)
    assert installed[signal.SIGTERM] is ns["_mark_shutdown_and_interrupt"]
    installed[signal.SIGTERM](signal.SIGTERM, None)
    assert ns["_shutdown_signal_count"] == 1
    assert not events  # Business services are not accessed before the loop is ready.


def _cleanup_process_group(process):
    """即使 Runner 已退出，也清理独立会话中可能仍存活的 Worker。"""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


@pytest.mark.parametrize("outcome", ["confirmed", "eof", "sigbreak", "pending_stop"])
def test_confirmation_restores_deferred_handlers(bot, monkeypatch, outcome):
    ns, _, _ = bot()
    monkeypatch.setattr(signal, "SIGBREAK", 21, raising=False)
    original = ns["_mark_shutdown_and_interrupt"]
    handlers = {sig: original for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK)}

    def install_handler(sig, handler):
        previous = handlers[sig]
        handlers[sig] = handler
        return previous

    def confirmation_input():
        assert handlers[signal.SIGINT] is signal.default_int_handler
        if outcome == "eof":
            raise EOFError
        if outcome == "sigbreak":
            handlers[signal.SIGBREAK](signal.SIGBREAK, None)
        return "confirmed"

    monkeypatch.setattr(signal, "signal", install_handler)
    user_input = Mock(side_effect=confirmation_input)
    ns.update(confirm_logger=Mock(), input=user_input)
    if outcome == "pending_stop":
        ns["_shutdown_signal_count"] = 1
    try:
        if outcome == "confirmed":
            ns["_prompt_user_confirmation"]("eula", "privacy")
        else:
            with pytest.raises(EOFError if outcome == "eof" else KeyboardInterrupt):
                ns["_prompt_user_confirmation"]("eula", "privacy")
        if outcome == "pending_stop":
            user_input.assert_not_called()
        else:
            user_input.assert_called_once()
    finally:
        assert all(handler is original for handler in handlers.values())


@pytest.mark.skipif(os.name != "posix", reason="需要真实 POSIX 早期停止信号")
def test_posix_worker_pending_stop_skips_confirmation(tmp_path):
    (tmp_path / "EULA.md").write_text("fixture EULA", encoding="utf-8")
    (tmp_path / "PRIVACY.md").write_text("fixture privacy", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-u",
            "-m",
            "pytests.startup_test.shutdown_fixture",
            "--worker",
            "--confirm",
            "--early-stop",
            "--self-stop",
        ],
        cwd=tmp_path,
        env=os.environ | {"PYTHONPATH": str(ROOT), "PYTHONUTF8": "1", "EULA_AGREE": "", "PRIVACY_AGREE": ""},
        input="",
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "worker_booting" in result.stdout
    assert "startup.agreement_confirm_prompt" not in result.stdout
    assert "initialize" not in result.stdout


@pytest.mark.skipif(os.name != "posix", reason="需要真实 POSIX 信号和独立进程组")
@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize(
    "runner_process,stop_signal", [(False, signal.SIGINT), (False, signal.SIGTERM), (True, signal.SIGTERM)]
)
def test_posix_worker_confirmation_signal_exit(tmp_path, confirmed, runner_process, stop_signal):
    import queue
    import threading

    (tmp_path / "EULA.md").write_text("fixture EULA", encoding="utf-8")
    (tmp_path / "PRIVACY.md").write_text("fixture privacy", encoding="utf-8")
    args = [sys.executable, "-u", "-m", "pytests.startup_test.shutdown_fixture", "--confirm"]
    if not runner_process:
        args.append("--worker")
    process = subprocess.Popen(
        args,
        cwd=tmp_path,
        env=os.environ | {"PYTHONPATH": str(ROOT), "PYTHONUTF8": "1", "EULA_AGREE": "", "PRIVACY_AGREE": ""},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    lines = queue.Queue()
    reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
    reader.start()
    output = []

    def wait_for_line(marker):
        while True:
            line = lines.get(timeout=5)
            output.append(line)
            if marker in line:
                return

    try:
        wait_for_line("startup.agreement_confirm_prompt")
        # 先让真实 input 消费一次输入；stdin 保持打开，下一轮继续阻塞等待。
        process.stdin.write("retry\n")
        process.stdin.flush()
        wait_for_line("startup.agreement_confirm_retry")
        worker_pid = int(next(line.split("=", 1)[1] for line in output if line.startswith("worker_pid=")))
        if confirmed:
            process.stdin.write("confirmed\n")
            process.stdin.flush()
            wait_for_line("worker_ready")
        process.send_signal(stop_signal)
        assert process.wait(timeout=3) == 0
        reader.join(timeout=1)
        while not lines.empty():
            output.append(lines.get_nowait())
        text = "".join(output)
        if confirmed:
            assert text.splitlines().count("memory_stop") == 1
            assert "startup.shutdown_completed" in text
            assert (tmp_path / "eula.confirmed").is_file()
            assert (tmp_path / "privacy.confirmed").is_file()
        else:
            assert "initialize" not in text
            assert "startup.shutdown_completed" not in text
            assert not (tmp_path / "eula.confirmed").exists()
            assert not (tmp_path / "privacy.confirmed").exists()
        if runner_process:
            assert "runner_exit=0" in text
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
    finally:
        _cleanup_process_group(process)
        reader.join(timeout=1)
        process.stdin.close()
        process.stdout.close()


@pytest.mark.skipif(os.name != "posix", reason="需要真实 POSIX 信号；Windows terminate() 不是 SIGTERM")
@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("early", [False, True])
def test_posix_real_runner_worker_shutdown(fail, early):
    args = [sys.executable, "-u", "-m", "pytests.startup_test.shutdown_fixture"] + (["--fail"] if fail else [])
    args += ["--early-stop"] if early else []
    process = subprocess.Popen(
        args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True
    )
    import queue
    import threading

    lines = queue.Queue()
    reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
    reader.start()
    output = []
    try:
        while True:
            line = lines.get(timeout=10)
            output.append(line)
            if ("worker_booting" if early else "worker_ready") in line:
                break
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == (1 if fail else 0)
        reader.join(timeout=1)
        while not lines.empty():
            output.append(lines.get_nowait())
        text = "".join(output)
        event_lines = text.splitlines()
        assert (
            event_lines.count("image_sync")
            == event_lines.count("memory_producer_stop")
            == event_lines.count("memory_stop")
            == 1
        )
        assert (
            event_lines.index("image_sync")
            < event_lines.index("memory_producer_stop")
            < event_lines.index("memory_stop")
        )
        assert ("startup.shutdown_completed" in text) is (not fail)
        assert "runner_exit=" + str(1 if fail else 0) in text
        # 服务标记显式 flush；Worker 最后的普通 print 可能被 os._exit 丢弃，不能用作顺序证据。
        assert event_lines.index("memory_stop") < event_lines.index("runner_exit=" + str(1 if fail else 0))
    finally:
        _cleanup_process_group(process)
        reader.join(timeout=1)
        process.stdout.close()


@pytest.mark.parametrize(
    "shutdown_at", ["init_pending", "init_return", "after_init", "before_publish", "after_publish", "scheduled"]
)
@pytest.mark.parametrize("repeated", [False, True])
def test_worker_consumes_shutdown_across_task_handoff(shutdown_at, repeated):
    args = [
        sys.executable,
        "-u",
        "-m",
        "pytests.startup_test.shutdown_fixture",
        "--worker",
        f"--shutdown-at={shutdown_at}",
    ]
    if repeated:
        args.append("--repeated")
    result = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        env=os.environ | {"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    scheduler_started = shutdown_at in {"after_publish", "scheduled"}
    assert ("schedule_entered" in lines) is scheduler_started
    assert ("schedule_cancelled" in lines) is scheduler_started
    if scheduler_started:
        assert lines.index("schedule_cancelled") < lines.index("startup.shutdown_started")
    assert lines.count("startup.shutdown_started") == lines.count("image_sync") == lines.count("memory_stop") == 1
    assert lines.count("startup.shutdown_completed") == 1
    assert (
        lines.index("image_sync")
        < lines.index("memory_producer_stop")
        < lines.index("memory_stop")
        < lines.index("metadata_close")
        < lines.index("startup.shutdown_completed")
    )


@pytest.mark.parametrize("point", ["before_publish", "after_publish", "after_check"])
def test_signal_on_publication_boundaries_cancels_once(bot, point):
    ns, system, events = bot()
    loop = asyncio.new_event_loop()
    ns["_active_main_loop"] = loop
    publish = ns["_set_active_main_task"]
    triggered = False

    def stop():
        nonlocal triggered
        triggered = True
        ns["_mark_shutdown_and_interrupt"](signal.SIGTERM, None)

    def trace(frame, event, arg):
        # 精确在赋值后/条件求值后注入；执行原函数，不复制其发布逻辑。
        if frame.f_code is publish.__code__ and not triggered:
            if point == "after_publish" and event == "line" and ns["_active_main_task"] is task:
                stop()
            elif point == "after_check" and event == "return":
                stop()
        return trace

    async def scheduler():
        await asyncio.Event().wait()

    previous_trace = sys.gettrace()
    task = loop.create_task(scheduler())
    try:
        if point == "before_publish":
            stop()
        sys.settrace(trace)
        publish(task)
        sys.settrace(previous_trace)
        with pytest.raises(asyncio.CancelledError):
            ns["_run_until_complete"](loop, task)
        assert triggered and task.cancelling() == 1
        assert ns["_run_graceful_shutdown"](loop, system)
        assert events.count("memory_stop") == 1
    finally:
        sys.settrace(previous_trace)
        loop.close()
