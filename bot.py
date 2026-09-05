# raise RuntimeError("System Not Ready")
from pathlib import Path
from rich.traceback import install
from typing import TypeVar

import asyncio
import hashlib
import os
import platform
# import shutil
import signal
import subprocess
import sys
import time
import traceback

from src.common.i18n import set_locale, t
from src.common.logger import get_logger, initialize_logging, shutdown_logging
from src.common.runtime_loop import set_main_loop
from src.common.shutdown import request_shutdown
from src.common.update_notice import emit_terminal_update_notice_if_needed
from src.config.legacy_upgrade_confirmation import require_legacy_upgrade_confirmation

# 设置工作目录为脚本所在目录
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
set_locale(os.getenv("MAIBOT_LOCALE", "zh-CN"))

# 检查是否是 Worker 进程，只在 Worker 进程中输出详细的初始化信息
# Runner 进程只需要基本的日志功能，不需要详细的初始化日志
is_worker = os.environ.get("MAIBOT_WORKER_PROCESS") == "1"
initialize_logging(verbose=is_worker)
install(extra_lines=3)
logger = get_logger("main")

# 定义重启退出码
RESTART_EXIT_CODE = 42
_active_main_loop: asyncio.AbstractEventLoop | None = None
_active_main_task: asyncio.Task[None] | None = None
_shutdown_signal_count: int = 0
_RunResultT = TypeVar("_RunResultT")
# print("-----------------------------------------")
# print("\n\n\n\n\n")
# print(t("startup.dev_branch_warning"))
# print("\n\n\n\n\n")
# print("-----------------------------------------")


def _print_interrupt_exit_notice() -> None:
    """在日志系统不可用或正在退出时，用最小输出提示 Ctrl+C 退出。"""

    print("\n收到 Ctrl+C，中断退出。")


def _mark_shutdown_and_interrupt(_signum: int, _frame: object) -> None:
    """收到中断信号时标记关停，并请求主任务取消。"""

    global _shutdown_signal_count
    _shutdown_signal_count += 1
    request_shutdown("signal")
    main_loop = _active_main_loop
    if main_loop is None or main_loop.is_closed():
        return

    try:
        main_loop.call_soon_threadsafe(_cancel_active_main_task_from_signal)
    except RuntimeError:
        return


def _cancel_active_main_task_from_signal() -> None:
    """在事件循环线程中取消当前主任务。"""

    if _active_main_task is None or _active_main_task.done():
        return
    _active_main_task.cancel()


def run_runner_process():
    """
    Runner 进程逻辑：作为守护进程运行，负责启动和监控 Worker 进程。
    处理重启请求 (退出码 42) 和 Ctrl+C 信号。
    """
    script_file = sys.argv[0]
    python_executable = sys.executable

    # 设置环境变量，标记子进程为 Worker 进程
    env = os.environ.copy()
    env["MAIBOT_WORKER_PROCESS"] = "1"

    while True:
        logger.info(t("startup.launching_script", script_file=script_file))

        # 启动子进程 (Worker)
        # 使用 sys.executable 确保使用相同的 Python 解释器
        cmd = [python_executable, script_file] + sys.argv[1:]

        process = subprocess.Popen(cmd, env=env)

        try:
            # 等待子进程结束
            return_code = process.wait()

            if return_code == RESTART_EXIT_CODE:
                logger.info(t("startup.restart_requested", exit_code=RESTART_EXIT_CODE))
                time.sleep(1)  # 稍作等待
                continue
            else:
                logger.info(t("startup.program_exited", return_code=return_code))
                sys.exit(return_code)

        except KeyboardInterrupt:
            # 向子进程发送终止信号
            if process.poll() is None:
                # 在 Windows 上，Ctrl+C 通常已经发送给了子进程（如果它们共享控制台）
                # 但为了保险，我们可以尝试 terminate
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning(t("startup.child_process_force_kill"))
                    process.kill()
            sys.exit(0)


# 检查是否是 Worker 进程
# 如果没有设置 MAIBOT_WORKER_PROCESS 环境变量，说明是直接运行的脚本，
# 此时应该作为 Runner 运行。
if os.environ.get("MAIBOT_WORKER_PROCESS") != "1":
    if __name__ == "__main__":
        require_legacy_upgrade_confirmation(Path(script_dir))
        run_runner_process()
    # 如果作为模块导入，不执行 Runner 逻辑，但也不应该执行下面的 Worker 逻辑
    sys.exit(0)

# 以下是 Worker 进程的逻辑

# 最早期初始化日志系统，确保所有后续模块都使用正确的日志格式
# 注意：Runner 进程已经在第 37 行初始化了日志系统，但 Worker 进程是独立进程，需要重新初始化
# 由于 Runner 和 Worker 是不同进程，它们有独立的内存空间，所以都会初始化一次
# 这是正常的，但为了避免重复的初始化日志，我们在 initialize_logging() 中添加了防重复机制
# 不过由于是不同进程，每个进程仍会初始化一次，这是预期的行为

require_legacy_upgrade_confirmation(Path(script_dir))
asyncio.run(emit_terminal_update_notice_if_needed())

logger.info(t("startup.worker_dir_set", script_dir=script_dir))

from src.main import MainSystem  # noqa


# logger = get_logger("main")


# install(extra_lines=3)

# 设置工作目录为脚本所在目录
# script_dir = os.path.dirname(os.path.abspath(__file__))
# os.chdir(script_dir)
confirm_logger = get_logger("confirm")
# 获取没有加载env时的环境变量
env_mask = {key: os.getenv(key) for key in os.environ}

uvicorn_server = None
driver = None
app = None
loop = None


def print_opensource_notice():
    """打印开源项目提示，防止倒卖"""
    from colorama import init, Fore, Style

    init()

    notice_lines = [
        "",
        f"{Fore.CYAN}{'═' * 70}{Style.RESET_ALL}",
        f"{Fore.GREEN}{t('startup.opensource_title')}{Style.RESET_ALL}",
        f"{Fore.CYAN}{'─' * 70}{Style.RESET_ALL}",
        f"{Fore.YELLOW}{t('startup.opensource_free_notice')}{Style.RESET_ALL}",
        f"{Fore.WHITE}{t('startup.opensource_scamming_notice')}{Style.RESET_ALL}",
        "",
        f"{Fore.WHITE}{t('startup.opensource_repo')}{Fore.BLUE}{t('startup.opensource_repo_value')} {Style.RESET_ALL}",
        f"{Fore.WHITE}{t('startup.opensource_docs')}{Fore.BLUE}{t('startup.opensource_docs_value')} {Style.RESET_ALL}",
        f"{Fore.WHITE}{t('startup.opensource_group')}{Fore.BLUE}{t('startup.opensource_group_value')}{Style.RESET_ALL}",
        f"{Fore.CYAN}{'─' * 70}{Style.RESET_ALL}",
        f"{Fore.RED}  ⚠ {t('startup.opensource_resale_warning').strip()}{Style.RESET_ALL}",
        f"{Fore.CYAN}{'═' * 70}{Style.RESET_ALL}",
        "",
    ]

    for line in notice_lines:
        print(line)


def easter_egg():
    # 彩蛋
    from colorama import init, Fore

    init()
    text = t("startup.easter_egg")
    rainbow_colors = [Fore.RED, Fore.YELLOW, Fore.GREEN, Fore.CYAN, Fore.BLUE, Fore.MAGENTA]
    rainbow_text = ""
    for i, char in enumerate(text):
        rainbow_text += rainbow_colors[i % len(rainbow_colors)] + char
    print(rainbow_text)


async def graceful_shutdown(main_system: MainSystem | None = None):
    try:
        from src.core.process_shutdown import run_process_shutdown

        webui_server = None if main_system is None else main_system.webui_server
        await run_process_shutdown("stepwise_timeout", webui_server=webui_server)
    except Exception as e:
        logger.error(t("startup.shutdown_failed", error=e), exc_info=True)


def _cancel_main_task(main_loop: asyncio.AbstractEventLoop | None, main_task: asyncio.Task[None] | None) -> None:
    """取消主调度任务，并等待取消结果落地。"""
    if main_loop is None or main_task is None or main_task.done() or main_loop.is_closed():
        return

    main_task.cancel()
    try:
        _run_until_complete(main_loop, main_task)
    except asyncio.CancelledError:
        pass


def _is_windows_proactor_cancel_race(error: BaseException) -> bool:
    """判断是否为 Windows Proactor 在连接取消时产生的事件循环内部竞态。"""
    if sys.platform != "win32" or not isinstance(error, asyncio.InvalidStateError):
        return False

    return any(
        frame.f_code.co_filename.replace("\\", "/").lower().endswith("/asyncio/windows_events.py")
        for frame, _ in traceback.walk_tb(error.__traceback__)
    )


def _run_until_complete(
    main_loop: asyncio.AbstractEventLoop,
    future: asyncio.Future[_RunResultT],
) -> _RunResultT:
    """运行 Future；在 Windows Proactor 瞬时状态竞争时继续驱动事件循环。"""
    while not future.done():
        try:
            main_loop.run_until_complete(future)
        except asyncio.InvalidStateError as e:
            if not _is_windows_proactor_cancel_race(e):
                raise
            logger.debug("忽略 Windows Proactor 瞬时 InvalidStateError，继续运行事件循环。", exc_info=True)
    return future.result()


def _run_graceful_shutdown(
    main_loop: asyncio.AbstractEventLoop | None,
    main_system: MainSystem | None,
) -> bool:
    """在同步入口中执行异步优雅关闭。"""
    if main_loop is None or main_loop.is_closed():
        return False

    try:
        shutdown_task = main_loop.create_task(graceful_shutdown(main_system))
        _run_until_complete(main_loop, shutdown_task)
        return True
    except KeyboardInterrupt:
        _print_interrupt_exit_notice()
    except Exception as ge:
        logger.error(t("startup.graceful_shutdown_error", error=ge))
    return False


def _calculate_file_hash(file_path: Path, file_type: str) -> str:
    """计算文件的MD5哈希值"""
    if not file_path.exists():
        logger.error(t("startup.file_not_found", file_type=file_type))
        raise FileNotFoundError(t("startup.file_not_found", file_type=file_type))

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _check_agreement_status(file_hash: str, confirm_file: Path, env_var: str) -> tuple[bool, bool]:
    """检查协议确认状态

    Returns:
        tuple[bool, bool]: (已确认, 未更新)
    """
    # 检查环境变量确认
    if file_hash == os.getenv(env_var):
        return True, False

    # 检查确认文件
    if confirm_file.exists():
        with open(confirm_file, "r", encoding="utf-8") as f:
            confirmed_content = f.read()
        if file_hash == confirmed_content:
            return True, False

    return False, True


def _prompt_user_confirmation(eula_hash: str, privacy_hash: str) -> None:
    """提示用户确认协议"""
    confirm_logger.critical(t("startup.agreement_reconfirm"))
    confirm_logger.critical(
        t(
            "startup.agreement_confirm_prompt",
            eula_hash=eula_hash,
            privacy_hash=privacy_hash,
        )
    )

    while True:
        user_input = input().strip().lower()
        if user_input in ["同意", "confirmed"]:
            return
        confirm_logger.critical(t("startup.agreement_confirm_retry"))


def _save_confirmations(eula_updated: bool, privacy_updated: bool, eula_hash: str, privacy_hash: str) -> None:
    """保存用户确认结果"""
    if eula_updated:
        logger.info(
            t(
                "startup.agreement_updated",
                agreement_name=t("startup.eula_name"),
                file_hash=eula_hash,
            )
        )
        Path("eula.confirmed").write_text(eula_hash, encoding="utf-8")

    if privacy_updated:
        logger.info(
            t(
                "startup.agreement_updated",
                agreement_name=t("startup.privacy_name"),
                file_hash=privacy_hash,
            )
        )
        Path("privacy.confirmed").write_text(privacy_hash, encoding="utf-8")


def check_eula():
    """检查EULA和隐私条款确认状态"""
    # 计算文件哈希值
    eula_hash = _calculate_file_hash(Path("EULA.md"), "EULA.md")
    privacy_hash = _calculate_file_hash(Path("PRIVACY.md"), "PRIVACY.md")

    # 检查确认状态
    eula_confirmed, eula_updated = _check_agreement_status(eula_hash, Path("eula.confirmed"), "EULA_AGREE")
    privacy_confirmed, privacy_updated = _check_agreement_status(
        privacy_hash, Path("privacy.confirmed"), "PRIVACY_AGREE"
    )

    # 早期返回：如果都已确认且未更新
    if eula_confirmed and privacy_confirmed:
        return

    # 如果有更新，需要重新确认
    if eula_updated or privacy_updated:
        _prompt_user_confirmation(eula_hash, privacy_hash)
        _save_confirmations(eula_updated, privacy_updated, eula_hash, privacy_hash)


def raw_main():
    # 利用 TZ 环境变量设定程序工作的时区
    if platform.system().lower() != "windows":
        time.tzset()  # type: ignore

    # 打印开源提示（防止倒卖）
    print_opensource_notice()

    check_eula()
    logger.info(t("startup.eula_privacy_checked"))

    easter_egg()

    # 返回MainSystem实例
    return MainSystem()


if __name__ == "__main__":
    exit_code = 0  # 用于记录程序最终的退出状态
    main_system: MainSystem | None = None
    main_tasks: asyncio.Task[None] | None = None
    shutdown_completed = False
    try:
        # 获取MainSystem实例
        main_system = raw_main()

        # 创建事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        set_main_loop(loop)
        _active_main_loop = loop
        signal.signal(signal.SIGINT, _mark_shutdown_and_interrupt)

        # 初始化 WebSocket 日志推送
        from src.common.logger import initialize_ws_handler

        initialize_ws_handler(loop)

        try:
            # 执行初始化和任务调度
            initialize_task = loop.create_task(main_system.initialize())
            _active_main_task = initialize_task
            _run_until_complete(loop, initialize_task)
            main_tasks = loop.create_task(main_system.schedule_tasks())
            _active_main_task = main_tasks
            _run_until_complete(loop, main_tasks)

        except KeyboardInterrupt:
            request_shutdown("keyboard_interrupt")
            try:
                logger.warning(t("startup.interrupt_received"))
            except KeyboardInterrupt:
                raise

            # 取消主任务
            _cancel_main_task(loop, main_tasks)

            # 执行优雅关闭
            shutdown_completed = _run_graceful_shutdown(loop, main_system)
        except asyncio.CancelledError:
            request_shutdown("task_cancelled")
            try:
                logger.warning(t("startup.interrupt_received"))
            except KeyboardInterrupt:
                pass

            shutdown_completed = _run_graceful_shutdown(loop, main_system)
        # 新增：检测外部请求关闭

    except SystemExit as e:
        # 捕获 SystemExit (例如 sys.exit()) 并保留退出代码
        if isinstance(e.code, int):
            exit_code = e.code
        else:
            exit_code = 1 if e.code else 0
        if exit_code == RESTART_EXIT_CODE:
            logger.info(t("startup.restart_signal_received"))

    except KeyboardInterrupt:
        request_shutdown("keyboard_interrupt")
        _print_interrupt_exit_notice()
    except Exception as e:
        try:
            logger.error(t("startup.main_error", error=f"{str(e)} {str(traceback.format_exc())}"))
        except KeyboardInterrupt:
            _print_interrupt_exit_notice()
        if not shutdown_completed:
            _cancel_main_task(loop, main_tasks)
            shutdown_completed = _run_graceful_shutdown(loop, main_system)
        exit_code = 1  # 标记发生错误
    finally:
        try:
            # 确保 loop 在任何情况下都尝试关闭（如果存在且未关闭）
            if "loop" in locals() and loop and not loop.is_closed():
                _active_main_task = None
                _active_main_loop = None
                set_main_loop(None)
                loop.close()
                print(t("startup.event_loop_closed"))

            # 关闭日志系统，释放文件句柄
            try:
                shutdown_logging()
            except Exception as e:
                print(t("startup.logging_shutdown_error", error=e))

            print(t("startup.prepare_exit"))
        except KeyboardInterrupt:
            _print_interrupt_exit_notice()

        # 使用 os._exit() 强制退出，避免被阻塞
        # 由于已经在 graceful_shutdown() 中完成了所有清理工作，这是安全的
        os._exit(exit_code)
