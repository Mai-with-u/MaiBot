"""Runner 的信号转发与有界退出；不加载 Worker 的业务依赖。"""

from collections.abc import Sequence
from typing import Callable, Optional

import os
import signal
import subprocess
import time

RESTART_EXIT_CODE = 42
WORKER_SHUTDOWN_TIMEOUT = 60.0
# Runner 请求「用新代码重新启动 Runner」时使用的退出码，只在 Runner 与守护它的上级进程之间使用。
RUNNER_RELAUNCH_EXIT_CODE = 43
# 上级进程要等 Runner 走完它自己的 Worker 关闭流程（60 秒等待 + 强制终止），因此留出余量。
RUNNER_SHUTDOWN_TIMEOUT = WORKER_SHUTDOWN_TIMEOUT + 15.0
WORKER_PROCESS_ENV = "MAIBOT_WORKER_PROCESS"
RUNNER_SUPERVISED_ENV = "MAIBOT_RUNNER_SUPERVISED"


def supervise_worker(
    command: Sequence[str],
    env: dict[str, str],
    logger,
    before_restart: Optional[Callable[[], Optional[int]]] = None,
    restart_code: int = RESTART_EXIT_CODE,
    shutdown_timeout: Optional[float] = None,
    child_name: str = "Worker",
) -> int:
    """保留退出码 42 的重启语义；停止请求优先于尚未执行的重启。

    before_restart 在旧 Worker 退出之后、新 Worker 启动之前同步执行（如自更新）；
    执行期间收到的停止请求会在它返回后生效。它返回退出码时不再拉起新 Worker，
    直接以该退出码结束监督。

    restart_code、shutdown_timeout、child_name 用于监督 Worker 以外的子进程（见 run_runner）。
    """
    stop_requested = False
    timeout = WORKER_SHUTDOWN_TIMEOUT if shutdown_timeout is None else shutdown_timeout

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
                    deadline = time.monotonic() + timeout
                    logger.info(f"正在请求 {child_name} 优雅关闭，等待完成（最多 {timeout:g} 秒）")
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
                    logger.error(f"{child_name} 优雅关闭超时，强制终止；应用关闭未完成")
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        logger.error(f"{child_name} 强制终止后仍无法回收；应用关闭未完成")
                    return 1
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            code = process.returncode
            if stop_requested or code != restart_code:
                # 在停止与 restart=42 竞态中不再拉起新 Worker，也不伪装成功。
                return code if code >= 0 else 128 - code
            logger.info(f"{child_name} 请求重启（退出码 {restart_code}）")
            if before_restart is not None:
                handoff_code = before_restart()
                # 停止请求优先：before_restart 执行期间收到停止时不再交接，直接退出。
                if handoff_code is not None and not stop_requested:
                    return handoff_code
            restart_at = time.monotonic() + 1.0
            while not stop_requested and time.monotonic() < restart_at:
                time.sleep(0.05)
        return 0
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run_runner(command: Sequence[str], env: dict[str, str], logger, apply_update: Callable[[], bool]) -> int:
    """Runner 主流程：监督 Worker，并在自更新成功后让新代码的 Runner 接手。

    apply_update 在 Worker 请求重启时调用，返回 True 表示刚刚完成了一次自更新。
    此时当前进程里的 Runner 代码和已加载的依赖都是旧的，于是：

    - 没有上级进程时，当前进程退化为上级，只负责拉起并守护新 Runner；
    - 已有上级进程时，以 RUNNER_RELAUNCH_EXIT_CODE 退出，由上级重新拉起 Runner。

    这样无论更新多少次，进程层级都固定为「上级 → Runner → Worker」。
    不直接用 exec 替换进程，是因为 Windows 上 exec 会让原进程退出，终端和服务管理器会认为程序已经结束。
    """
    update_applied = False

    def before_restart() -> Optional[int]:
        nonlocal update_applied
        if not apply_update():
            return None
        update_applied = True
        return RUNNER_RELAUNCH_EXIT_CODE

    worker_env = dict(env)
    worker_env[WORKER_PROCESS_ENV] = "1"
    code = supervise_worker(command, worker_env, logger, before_restart=before_restart)
    if not update_applied:
        # Worker 自己以这个退出码结束不代表要重启 Runner，不能被上级误当成交接请求。
        return 1 if code == RUNNER_RELAUNCH_EXIT_CODE else code
    if code != RUNNER_RELAUNCH_EXIT_CODE:
        return code  # 更新期间收到了停止请求
    if env.get(RUNNER_SUPERVISED_ENV) == "1":
        return RUNNER_RELAUNCH_EXIT_CODE

    logger.info("自更新完成，由新版本的 Runner 接手；当前进程只负责守护它")
    supervised_env = dict(env)
    supervised_env[RUNNER_SUPERVISED_ENV] = "1"
    return supervise_worker(
        command,
        supervised_env,
        logger,
        restart_code=RUNNER_RELAUNCH_EXIT_CODE,
        shutdown_timeout=RUNNER_SHUTDOWN_TIMEOUT,
        child_name="Runner",
    )
