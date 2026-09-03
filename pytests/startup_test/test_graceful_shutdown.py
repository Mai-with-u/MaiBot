"""不启动真实 Bot；覆盖信号、关闭次序、失败状态以及 Runner 的重启协议。"""

from unittest.mock import Mock
from types import SimpleNamespace

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
        assert events.index("memory_producer_stop") < events.index("memory_stop") < events.index("remaining_cancelled")
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
            await asyncio.Event().wait()

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
    monkeypatch.setattr(process_runner.signal, "signal", lambda sig, handler: handlers.setdefault(sig, handler))
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

    def poll():
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
    assert result.stdout.splitlines().count("memory_stop") == 1
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
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
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


@pytest.mark.skipif(os.name != "posix", reason="需要真实 POSIX 信号；Windows terminate() 不是 SIGTERM")
@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("early", [False, True])
def test_posix_real_runner_worker_shutdown(fail, early):
    args = [sys.executable, "-u", "-m", "pytests.startup_test.shutdown_fixture"] + (["--fail"] if fail else [])
    args += ["--early-stop"] if early else []
    process = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
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
        assert text.splitlines().count("memory_stop") == 1
        assert ("startup.shutdown_completed" in text) is (not fail)
        assert "runner_exit=" + str(1 if fail else 0) in text
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


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
    assert lines.count("startup.shutdown_started") == lines.count("memory_stop") == 1
    assert lines.count("startup.shutdown_completed") == 1
    assert lines.index("memory_stop") < lines.index("metadata_close") < lines.index("startup.shutdown_completed")


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
