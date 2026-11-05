"""用轻量 Python 子进程验证受管启停与进程树回收，不启动真实 Bot。"""

from pathlib import Path
from typing import Dict

import asyncio
import ctypes
import io
import os
import subprocess
import sys
import textwrap

import pytest

from src.host.process import ManagedProcess


ROOT = Path(__file__).resolve().parents[2]


def child_env() -> Dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}


async def wait_for_file(path: Path, log: Path) -> None:
    for _ in range(200):
        if await asyncio.to_thread(path.exists):
            return
        await asyncio.sleep(0.025)
    details = await asyncio.to_thread(log.read_text, encoding="utf-8")
    pytest.fail(f"子进程未就绪: {path}\n{details}")


def cooperative_script(directory: Path) -> str:
    return textwrap.dedent(f"""
        from pathlib import Path
        import os
        import signal
        import sys
        import time
        root = Path({str(directory)!r})
        def stop(signum, frame):
            (root / 'stopped').write_text('graceful', encoding='utf-8')
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, stop)
        (root / 'prefix').write_text(sys.prefix, encoding='utf-8')
        (root / 'ready').write_text(str(os.getpid()), encoding='utf-8')
        while True:
            time.sleep(0.025)
    """)


def windows_process_running(pid: int) -> bool:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return False
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.asyncio
async def test_stopping_one_process_does_not_stop_another(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    a = await ManagedProcess.start(
        [sys.executable, "-c", cooperative_script(first)], cwd=first, env=child_env(), log_path=first / "log",
    )
    b = await ManagedProcess.start(
        [sys.executable, "-c", cooperative_script(second)], cwd=second, env=child_env(), log_path=second / "log",
    )
    try:
        await wait_for_file(first / "ready", first / "log")
        await wait_for_file(second / "ready", second / "log")
        assert int((first / "ready").read_text(encoding="utf-8")) == a.pid
        assert (first / "prefix").read_text(encoding="utf-8") == sys.prefix
        assert await a.stop(timeout=3) == 0
        assert not a.forced
        assert a.stop_error is None
        assert (first / "stopped").read_text(encoding="utf-8") == "graceful"
        assert b.returncode is None
        assert await b.stop(timeout=3) == 0
        assert not b.forced
    finally:
        await a.stop(timeout=0)
        await b.stop(timeout=0)


@pytest.mark.asyncio
async def test_stop_during_startup_before_business_handler_is_normal(tmp_path: Path) -> None:
    managed = await ManagedProcess.start(
        [sys.executable, "-c", "import time\nwhile True:\n    time.sleep(0.01)"],
        cwd=tmp_path, env=child_env(), log_path=tmp_path / "log",
    )
    try:
        assert await managed.stop(timeout=3) == 0
        assert not managed.forced
        assert managed.stop_error is None
    finally:
        await managed.stop(timeout=0)


def orphaning_script(directory: Path, exit_parent: bool) -> str:
    child = textwrap.dedent(f"""
        from pathlib import Path
        import os
        import time
        Path({str(directory / 'child-ready')!r}).write_text(str(os.getpid()), encoding='utf-8')
        while True:
            time.sleep(0.05)
    """)
    return textwrap.dedent(f"""
        from pathlib import Path
        import os
        import signal
        import subprocess
        import sys
        import time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        subprocess.Popen(
            [sys.executable, '-c', {child!r}],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        while not Path({str(directory / 'child-ready')!r}).exists():
            time.sleep(0.01)
        if {exit_parent!r}:
            raise SystemExit(0)
        while True:
            time.sleep(0.05)
    """)


@pytest.mark.skipif(os.name != "nt", reason="验证 Windows Job Object 的整树回收")
@pytest.mark.parametrize("exit_parent", [False, True])
@pytest.mark.asyncio
async def test_windows_job_reaps_descendants_before_wait_returns(tmp_path: Path, exit_parent: bool) -> None:
    managed = await ManagedProcess.start(
        [sys.executable, "-c", orphaning_script(tmp_path, exit_parent)], cwd=tmp_path,
        env=child_env(), log_path=tmp_path / "log",
    )
    try:
        await wait_for_file(tmp_path / "child-ready", tmp_path / "log")
        child_pid = int((tmp_path / "child-ready").read_text(encoding="utf-8"))
        if exit_parent:
            assert await asyncio.wait_for(managed.wait(), 5) == 0
        else:
            assert await managed.stop(timeout=0.1) != 0
        assert managed.forced
        assert managed.stop_error is not None
        assert not windows_process_running(child_pid)
    finally:
        await managed.stop(timeout=0)


@pytest.mark.skipif(os.name != "nt", reason="验证宿主异常退出时内核关闭 Job 句柄")
@pytest.mark.asyncio
async def test_windows_host_death_terminates_owned_process_tree(tmp_path: Path) -> None:
    child_code = orphaning_script(tmp_path, False)
    host_code = textwrap.dedent(f"""
        from pathlib import Path
        from src.host.process import ManagedProcess
        import asyncio
        import os
        import sys
        async def main():
            process = await ManagedProcess.start(
                [sys.executable, '-c', {child_code!r}], cwd=Path({str(tmp_path)!r}),
                env=dict(os.environ), log_path=Path({str(tmp_path / 'bot-log')!r}),
            )
            await process.wait()
        asyncio.run(main())
    """)
    with (tmp_path / "host-log").open("wb") as log:
        host = subprocess.Popen(
            [sys.executable, "-c", host_code], cwd=tmp_path, env=child_env(), stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    try:
        await wait_for_file(tmp_path / "child-ready", tmp_path / "host-log")
        child_pid = int((tmp_path / "child-ready").read_text(encoding="utf-8"))
        host.kill()
        await asyncio.to_thread(host.wait, 5)
        for _ in range(100):
            if not windows_process_running(child_pid):
                break
            await asyncio.sleep(0.02)
        assert not windows_process_running(child_pid)
    finally:
        if host.poll() is None:
            host.kill()
            await asyncio.to_thread(host.wait, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", "[测试 Bot/12345678] "])
async def test_console_streams_live_logs_without_replaying_history(tmp_path: Path, capsys, prefix: str) -> None:
    log = tmp_path / "runner.log"
    log.write_bytes(b"old history\n")
    script = """
import sys
import time
print('实时日志', flush=True)
print('错误日志', file=sys.stderr, flush=True)
while True:
    time.sleep(0.02)
"""
    managed = await ManagedProcess.start(
        [sys.executable, "-c", script], cwd=tmp_path, env=child_env(), log_path=log, console_prefix=prefix,
    )
    output = ""
    try:
        async with asyncio.timeout(10):
            while "错误日志" not in output:
                await asyncio.sleep(0.05)
                output += capsys.readouterr().out
        assert managed.returncode is None
        assert f"{prefix}实时日志" in output.splitlines()
        assert f"{prefix}错误日志" in output.splitlines()
        assert "old history" not in output
    finally:
        await managed.stop(timeout=5)
    persisted = log.read_text(encoding="utf-8")
    assert persisted.startswith("old history\n实时日志\n错误日志\n")
    assert prefix == "" or prefix not in persisted


@pytest.mark.asyncio
async def test_console_gbk_output_does_not_break_process_cleanup(tmp_path: Path, monkeypatch) -> None:
    buffer = io.BytesIO()
    terminal = io.TextIOWrapper(buffer, encoding="gbk", newline="\r\n")
    monkeypatch.setattr(sys, "stdout", terminal)
    managed = await ManagedProcess.start(
        [sys.executable, "-c", "print('中文 🔑', flush=True)"],
        cwd=tmp_path, env=child_env(), log_path=tmp_path / "output.log", console_prefix="",
    )
    assert await asyncio.wait_for(managed.wait(), 10) == 0
    assert not managed.forced
    output = buffer.getvalue().decode("gbk")
    assert output == "中文 " + chr(92) + "U0001f511\r\n"
    assert "🔑" in (tmp_path / "output.log").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_direct_python_process_preserves_venv_and_terminates_actual_pid():
    from src.common.python_process import python_environment, python_executable

    process = await asyncio.create_subprocess_exec(
        python_executable(), "-u", "-c",
        "import os,sys,time; print(os.getpid()); print(sys.prefix); time.sleep(60)",
        env=python_environment(os.environ), stdout=asyncio.subprocess.PIPE,
    )
    try:
        pid = int(await asyncio.wait_for(process.stdout.readline(), 10))
        prefix = (await asyncio.wait_for(process.stdout.readline(), 10)).decode().strip()
        assert pid == process.pid
        assert Path(prefix).resolve() == Path(sys.prefix).resolve()
    finally:
        process.terminate()
        await asyncio.wait_for(process.wait(), 10)
    if os.name == "nt":
        assert not windows_process_running(pid)
