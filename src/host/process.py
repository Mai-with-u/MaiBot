"""异步管理一个 Bot 的进程树，并提供不依赖控制台的 Python 启停门闩。"""

from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Mapping, Optional, Sequence, Tuple

import _thread
import asyncio
import codecs
import io
import os
import runpy
import signal
import subprocess
import sys
import threading
import time

if TYPE_CHECKING:
    from .windows_job import WindowsJob


def gated_python_command(command: Sequence[str]) -> List[str]:
    """只接受当前解释器的 -m/-c 入口，门闩放行后在同一 PID 中执行目标。"""
    from src.common.python_process import python_executable

    if len(command) < 3 or command[1] not in {"-m", "-c"}:
        raise ValueError("受管进程只支持 Python -m 或 -c 入口")
    if Path(command[0]).resolve() != Path(sys.executable).resolve():
        raise ValueError("受管进程必须使用当前 Python 解释器")
    return [python_executable(), "-u", str(Path(__file__).resolve()), "--gate", *command[1:]]


def gated_python_environment(env: Mapping[str, str]) -> Dict[str, str]:
    from src.common.python_process import python_environment

    # 直接执行门闩脚本时不依赖项目导入路径；宿主构造命令时才加载此模块。
    # 绕过 redirector，避免在 Job 绑定前派生不受监管的解释器。
    return python_environment(env)


def release_process(process: subprocess.Popen) -> None:
    if process.stdin is None:
        raise RuntimeError("受管进程缺少控制管道")
    process.stdin.write(b"start\n")
    process.stdin.flush()


def request_process_stop(process: subprocess.Popen) -> None:
    if process.stdin is None:
        raise RuntimeError("受管进程缺少控制管道")
    process.stdin.write(b"stop\n")
    process.stdin.flush()


class ManagedProcess:
    def __init__(self, process: subprocess.Popen, job: Optional["WindowsJob"]) -> None:
        self._process = process
        self._job = job
        self._tree_lock = threading.Lock()
        self._tree_closed = False
        self._stop_lock = asyncio.Lock()
        self.forced = False
        self.stop_error: Optional[str] = None
        self._console_task: Optional[asyncio.Task[None]] = None
        self._wait_task = asyncio.create_task(self._wait_and_cleanup())

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> Optional[int]:
        return self._process.poll()

    @classmethod
    async def start(
        cls, command: Sequence[str], *, cwd: Path, env: Mapping[str, str], log_path: Path,
        console_prefix: Optional[str] = None,
    ) -> "ManagedProcess":
        # 从本次启动前的文件末尾开始转发，重启不会重放历史日志。
        offset = 0
        if console_prefix is not None:
            def log_size() -> int:
                try:
                    return log_path.stat().st_size
                except FileNotFoundError:
                    return 0
            offset = await asyncio.to_thread(log_size)
        # Popen 和日志文件操作在线程执行；取消启动时仍须拿回句柄并回收，不能遗留已启动的 Bot。
        task = asyncio.create_task(asyncio.to_thread(cls._spawn, command, cwd, dict(env), log_path))
        try:
            process, job = await asyncio.shield(task)
        except asyncio.CancelledError:
            process, job = await task
            managed = cls(process, job)
            await managed.stop(timeout=0)
            raise
        managed = cls(process, job)
        if console_prefix is not None:
            managed._console_task = asyncio.create_task(managed._relay_console(log_path, offset, console_prefix))
        return managed

    async def _relay_console(self, log_path: Path, offset: int, prefix: str) -> None:
        """增量转发已落盘的输出；文件读取和终端写入都不占用宿主事件循环。"""
        decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder("utf-8")(errors="replace"), translate=True)
        line_start = True

        def forward(final: bool = False) -> int:
            nonlocal offset, line_start
            with log_path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read(65536)
            offset += len(chunk)
            text = decoder.decode(chunk, final=final and not chunk)
            if prefix and text:
                parts = []
                for part in text.splitlines(keepends=True):
                    parts.append((prefix if line_start else "") + part)
                    line_start = part.endswith("\n")
                text = "".join(parts)
            if text:
                # 重定向后的 Windows stdout 可能是 GBK；不可编码的符号不能中断进程回收。
                if sys.stdout.encoding:
                    text = text.encode(sys.stdout.encoding, errors="backslashreplace").decode(sys.stdout.encoding)
                sys.stdout.write(text)
                sys.stdout.flush()
            return len(chunk)

        while True:
            finished = self.returncode is not None
            size = await asyncio.to_thread(forward, finished)
            if finished and not size:
                return
            if not size:
                await asyncio.sleep(0.1)

    @staticmethod
    def _spawn(
        command: Sequence[str], cwd: Path, env: Dict[str, str], log_path: Path,
    ) -> Tuple[subprocess.Popen, Optional["WindowsJob"]]:
        gated_command = gated_python_command(command)
        job = None
        process = None
        if os.name == "nt":
            from .windows_job import WindowsJob

            job = WindowsJob()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("ab", buffering=0) as log:
                process = subprocess.Popen(
                    gated_command, cwd=cwd, env=gated_python_environment(env),
                    stdin=subprocess.PIPE, stdout=log, stderr=log,
                    creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0,
                    start_new_session=os.name != "nt",
                )
            if job is not None:
                job.assign(process.pid)
            release_process(process)
            return process, job
        except BaseException:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                if process.stdin is not None:
                    process.stdin.close()
            if job is not None:
                job.close()
            raise

    def _record_forced(self, message: str) -> None:
        self.forced = True
        self.stop_error = message if self.stop_error is None else f"{self.stop_error}; {message}"

    def _terminate_tree(self) -> None:
        with self._tree_lock:
            if self._tree_closed:
                return
            if self._job is not None:
                self._job.terminate()
            else:
                try:
                    os.killpg(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # 整个进程组已经退出。

    def _cleanup_tree(self) -> None:
        with self._tree_lock:
            if self._tree_closed:
                return
            try:
                if self._job is not None:
                    # 主进程已经退出；以子进程句柄为准，核实残留并等待回收完成。
                    remaining = self._job.live_processes()
                    if remaining:
                        details = "; ".join(f"PID={pid} 程序={path}" for pid, path in remaining.items())
                        self._record_forced(f"主进程退出后仍有子进程存活，已强制回收进程树: {details}")
                        self._job.terminate()
                    self._job.wait_terminated(5)
                else:
                    try:
                        os.killpg(self.pid, 0)
                    except ProcessLookupError:
                        return
                    self._record_forced("主进程退出后仍有子进程存活，已强制回收进程树")
                    os.killpg(self.pid, signal.SIGKILL)
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            os.killpg(self.pid, 0)
                        except ProcessLookupError:
                            break
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Bot 进程组强制回收超时")
                        time.sleep(0.02)
            finally:
                if self._job is not None:
                    self._job.close()
                if self._process.stdin is not None:
                    self._process.stdin.close()
                self._tree_closed = True

    async def _wait_and_cleanup(self) -> int:
        # 不为每个长时间运行的 Bot 占一个 executor 线程，避免实例数多时耗尽线程池。
        while self._process.poll() is None:
            await asyncio.sleep(0.05)
        await asyncio.to_thread(self._cleanup_tree)
        if self._console_task is not None:
            await self._console_task
        assert self._process.returncode is not None
        return self._process.returncode

    async def wait(self) -> int:
        return await asyncio.shield(self._wait_task)

    async def stop(self, timeout: float = 75) -> int:
        async with self._stop_lock:
            if self.returncode is None:
                try:
                    await asyncio.to_thread(request_process_stop, self._process)
                except (BrokenPipeError, OSError) as exc:
                    if self.returncode is None:
                        self._record_forced(f"停止控制管道不可用: {exc}")
                        await asyncio.to_thread(self._terminate_tree)
            try:
                return await asyncio.wait_for(asyncio.shield(self._wait_task), timeout)
            except asyncio.TimeoutError:
                if self._wait_task.done():
                    return self._wait_task.result()  # 回收本身的超时应原样暴露，不误报成停止等待超时。
                self._record_forced(f"优雅停止超过 {timeout:g} 秒，已强制终止进程树")
                await asyncio.to_thread(self._terminate_tree)
                return await asyncio.wait_for(asyncio.shield(self._wait_task), 6)


def _run_gate(args: Sequence[str]) -> None:
    if len(args) < 2 or args[0] not in {"-m", "-c"}:
        raise ValueError("无效的受管 Python 入口")
    # 控制管道使用私有 fd；业务子进程不能继承它作为 stdin，否则 Windows venv
    # redirector 可能等待同一管道，且业务 input() 会误消费宿主指令。
    control_input = os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0)
    if control_input.readline() != b"start\n":
        raise SystemExit("宿主未授权启动")
    with open(os.devnull, "rb") as null_input:
        os.dup2(null_input.fileno(), sys.stdin.fileno())
    if os.name == "nt":
        from ctypes import wintypes

        import ctypes
        import msvcrt

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
        kernel.SetStdHandle.restype = wintypes.BOOL
        if not kernel.SetStdHandle(-10 & 0xFFFFFFFF, msvcrt.get_osfhandle(sys.stdin.fileno())):
            raise ctypes.WinError(ctypes.get_last_error())

    def stop_before_handlers(_signum, _frame) -> None:
        # 业务关闭 handler 尚未接管时，宿主取消启动是正常停止；之后由业务保留真实退出码。
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop_before_handlers)

    def watch_control() -> None:
        command = control_input.readline()
        if command not in (b"stop\n", b""):
            print("无效的宿主控制指令，停止受管进程", file=sys.stderr, flush=True)
        # 在 Python 主线程运行目标自己的 SIGTERM handler，不要求存在 Windows 控制台。
        _thread.interrupt_main(signal.SIGTERM)
        if os.name != "nt":
            # 宿主异常退出时没有外层 stop 超时；给 Worker 完成清理的窗口，再终止本进程组。
            time.sleep(80)
            os.killpg(os.getpgrp(), signal.SIGKILL)

    threading.Thread(target=watch_control, name="host-control", daemon=True).start()
    # 非交互启动所需的确认应在运行前完成，业务 stdin 现在只会读到 EOF。
    sys.path[0] = os.getcwd()
    if args[0] == "-m":
        sys.argv = [args[1], *args[2:]]
        runpy.run_module(args[1], run_name="__main__", alter_sys=True)
    else:
        sys.argv = ["-c", *args[2:]]
        exec(compile(args[1], "<string>", "exec"), {"__name__": "__main__", "__package__": None})


if __name__ == "__main__":
    if sys.argv[1:2] != ["--gate"]:
        raise SystemExit("此入口仅用于宿主管理的内部进程")
    _run_gate(sys.argv[2:])
