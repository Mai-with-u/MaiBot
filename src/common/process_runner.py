"""Runner 的信号转发与有界退出；不加载 Worker 的业务依赖。"""

from collections.abc import Sequence

import os
import signal
import subprocess
import time

RESTART_EXIT_CODE = 42
WORKER_SHUTDOWN_TIMEOUT = 60.0


def supervise_worker(command: Sequence[str], env: dict[str, str], logger) -> int:
    """保留退出码 42 的重启语义；停止请求优先于尚未执行的重启。"""
    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        # handler 只记状态，不等待、不重复向正在清理的 Worker 发信号。
        stop_requested = True

    signals = [signal.SIGINT, signal.SIGTERM]
    if os.name == "nt":
        signals.append(signal.SIGBREAK)
    previous = {sig: signal.signal(sig, request_stop) for sig in signals}
    try:
        while not stop_requested:
            kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
            process = subprocess.Popen(command, env=env, **kwargs)
            deadline = None
            while process.poll() is None:
                if stop_requested and deadline is None:
                    deadline = time.monotonic() + WORKER_SHUTDOWN_TIMEOUT
                    logger.info("Runner 请求 Worker 优雅关闭，等待完成（最多 60 秒）")
                    try:
                        # Windows 的 terminate() 是强制结束，不是 SIGTERM。
                        # 独立控制台进程组通过 CTRL_BREAK 进入 Worker 的关闭 handler。
                        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
                    except ProcessLookupError:
                        pass  # Worker 已在 poll 与 send_signal 之间退出。
                    except OSError:
                        logger.error("Runner 无法发送受控停止信号；应用关闭未确认完成")
                        # 仍保留等待窗口；不可把发送失败当成已优雅退出。
                        # 若 Worker 自行结束则保留其退出码，否则到期后强制结束。
                if deadline is not None and time.monotonic() >= deadline:
                    logger.error("Worker 优雅关闭超时，强制终止；应用关闭未完成")
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        logger.error("Worker 强制终止后仍无法回收；应用关闭未完成")
                    return 1
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            code = process.returncode
            if stop_requested or code != RESTART_EXIT_CODE:
                # 在停止与 restart=42 竞态中不再拉起新 Worker，也不伪装成功。
                return code if code >= 0 else 128 - code
            logger.info("Worker 请求重启（退出码 42）")
            restart_at = time.monotonic() + 1.0
            while not stop_requested and time.monotonic() < restart_at:
                time.sleep(0.05)
        return 0
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
