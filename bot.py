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
import sys
import time
import traceback

# 在业务导入前接管 Worker 信号；此时只记录停止请求，不调用尚未初始化的组件。
_active_main_loop: asyncio.AbstractEventLoop | None = None
_active_main_task: asyncio.Task[None] | None = None
_shutdown_signal_count: int = 0
_shutdown_task: asyncio.Task[bool] | None = None
_shutdown_deadline: float | None = None
SHUTDOWN_TIMEOUT = 50.0  # 为 Runner 的 60 秒硬截止保留余量。
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
    if _shutdown_signal_count > 1 or _shutdown_task is not None:
        return
    main_loop = _active_main_loop
    if main_loop is None or main_loop.is_closed():
        return

    request_shutdown("signal")
    try:
        main_loop.call_soon_threadsafe(_cancel_active_main_task_from_signal)
    except RuntimeError:
        return


def _cancel_active_main_task_from_signal() -> None:
    """在事件循环线程中取消当前主任务。"""

    if (
        _shutdown_task is not None
        or _active_main_task is None
        or _active_main_task.done()
        or _active_main_task.cancelling()
    ):
        return
    _active_main_task.cancel()


def _set_active_main_task(task: asyncio.Task[None]) -> None:
    """先发布任务，再消费停止标记；交接前的请求和交接后的回调都不会丢失。"""
    global _active_main_task
    _active_main_task = task
    if _shutdown_signal_count:
        request_shutdown("signal")
        _cancel_active_main_task_from_signal()


def _install_early_worker_signal_handlers() -> None:
    """允许慢速导入期间收到的停止请求在事件循环就绪后被消费。"""
    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    for sig in signals:
        signal.signal(sig, _mark_shutdown_and_interrupt)


if __name__ == "__main__" and os.environ.get("MAIBOT_WORKER_PROCESS") == "1":
    _install_early_worker_signal_handlers()


from src.common.i18n import set_locale, t, tn  # noqa: E402
from src.common.logger import get_logger, initialize_logging, shutdown_logging  # noqa: E402
from src.common.runtime_loop import set_main_loop  # noqa: E402
from src.common.process_runner import RESTART_EXIT_CODE, supervise_worker  # noqa: E402
from src.common.shutdown import application_signal_handlers, request_shutdown  # noqa: E402
from src.common.update_notice import emit_terminal_update_notice_if_needed  # noqa: E402
from src.config.legacy_upgrade_confirmation import require_legacy_upgrade_confirmation  # noqa: E402

# 设置工作目录为脚本所在目录
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
set_locale(os.getenv("MAIBOT_LOCALE", "zh-CN"))

# Runner 只需要基本日志；详细组件初始化由 Worker 输出。
is_worker = os.environ.get("MAIBOT_WORKER_PROCESS") == "1"
initialize_logging(verbose=is_worker)
install(extra_lines=3)
logger = get_logger("main")


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

    logger.info(t("startup.launching_script", script_file=script_file))
    cmd = [python_executable, script_file] + sys.argv[1:]
    sys.exit(supervise_worker(cmd, env, logger))


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
from src.manager.async_task_manager import async_task_manager  # noqa


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


async def graceful_shutdown(main_system: MainSystem | None = None) -> bool:
    global _shutdown_deadline
    _shutdown_deadline = time.monotonic() + SHUTDOWN_TIMEOUT
    try:
        request_shutdown("graceful_shutdown")
        logger.info(t("startup.shutdown_started"))
        success = True

        # 关闭 WebUI 服务器
        if main_system is not None and main_system.webui_server is not None:
            success = (
                await _await_shutdown_step(main_system.webui_server.shutdown(), timeout=5.0, step_name="关闭 WebUI")
                and success
            )

        from src.config.config import config_manager

        success = (
            await _await_shutdown_step(config_manager.stop_file_watcher(), timeout=5.0, step_name="停止配置监听")
            and success
        )
        if main_system is not None and main_system.app is not None:
            success = (
                await _await_shutdown_step(main_system.app.stop(), timeout=5.0, step_name="停止消息入口") and success
            )

        from src.core.event_bus import event_bus
        from src.core.types import EventType

        # 触发 ON_STOP 事件
        success = (
            await _await_shutdown_step(
                event_bus.emit(event_type=EventType.ON_STOP),
                timeout=5.0,
                step_name="触发 ON_STOP 事件",
            )
            and success
        )

        # 停止新版本插件运行时
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        success = (
            await _await_shutdown_step(
                get_plugin_runtime_manager().stop(),
                timeout=8.0,
                step_name="停止插件运行时",
            )
            and success
        )

        # 先停止记忆写入者，再等待内核持久化、关闭存储和释放 writer lock。
        # 必须早于 remaining_tasks 的粗粒度取消。
        from src.A_memorix.host_service import a_memorix_host_service
        from src.services.memory_flow_service import memory_automation_service
        from src.emoji_system.emoji_manager import emoji_manager
        from src.mcp_module.service import get_mcp_service

        success = (
            await _await_shutdown_step(memory_automation_service.shutdown(), timeout=5.0, step_name="停止记忆自动写入")
            and success
        )
        success = (
            await _await_shutdown_step(a_memorix_host_service.stop(), timeout=30.0, step_name="关闭 A_Memorix 并持久化")
            and success
        )
        try:
            emoji_manager.shutdown()
        except Exception:
            logger.exception("Emoji 清理失败，继续后续关闭步骤")
            success = False
        success = await _await_shutdown_step(get_mcp_service().close(), timeout=5.0, step_name="关闭 MCP") and success

        # 停止所有异步任务
        success = (
            await _await_shutdown_step(
                async_task_manager.stop_and_wait_all_tasks(),
                timeout=5.0,
                step_name="停止异步任务管理器任务",
            )
            and success
        )

        # 获取所有剩余任务，排除当前任务
        remaining_tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

        if remaining_tasks:
            logger.info(tn("startup.remaining_tasks_cancelling", len(remaining_tasks)))

            # 取消所有剩余任务
            for task in remaining_tasks:
                if not task.done():
                    task.cancel()

            # 等待所有任务完成，设置超时
            try:
                await asyncio.wait_for(
                    asyncio.gather(*remaining_tasks, return_exceptions=True), timeout=_shutdown_time_left(5.0)
                )
                logger.info(t("startup.remaining_tasks_cancelled"))
            except asyncio.TimeoutError:
                logger.warning(t("startup.remaining_tasks_cancel_timeout"))
                success = False
            except Exception as e:
                logger.error(t("startup.remaining_tasks_cancel_error", error=e))
                success = False

        if success:
            logger.info(t("startup.shutdown_completed"))
        else:
            logger.error("应用优雅关闭未完成；退出状态为失败")
        return success

    except Exception as e:
        logger.error(t("startup.shutdown_failed", error=e), exc_info=True)
        return False


def _shutdown_time_left(step_timeout: float) -> float:
    if _shutdown_deadline is None:
        return step_timeout
    return min(step_timeout, max(0.0, _shutdown_deadline - time.monotonic()))


async def _await_shutdown_step(awaitable, *, timeout: float, step_name: str) -> bool:
    """步骤共享关闭预算；不响应取消/阻塞事件循环的代码最终由 Runner 有界终止。"""

    try:
        await asyncio.wait_for(awaitable, timeout=_shutdown_time_left(timeout))
        return True
    except asyncio.TimeoutError:
        logger.warning(f"{step_name} 超时，继续执行后续关停步骤")
        return False
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(f"{step_name} 失败，继续执行后续关停步骤: {exc}", exc_info=True)
        return False


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
    global _shutdown_task
    if main_loop is None or main_loop.is_closed():
        return False

    try:
        if _shutdown_task is None:
            _shutdown_task = main_loop.create_task(graceful_shutdown(main_system))
        return _run_until_complete(main_loop, _shutdown_task)
    except KeyboardInterrupt:
        _print_interrupt_exit_notice()
    except asyncio.CancelledError:
        logger.error("应用优雅关闭任务被取消；关闭未完成")
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
    worker_signals = None
    try:
        # 获取MainSystem实例
        main_system = raw_main()

        # 创建事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        set_main_loop(loop)
        _active_main_loop = loop
        worker_signals = application_signal_handlers(_mark_shutdown_and_interrupt)
        worker_signals.__enter__()

        # 初始化 WebSocket 日志推送
        from src.common.logger import initialize_ws_handler

        initialize_ws_handler(loop)

        try:
            # 执行初始化和任务调度
            initialize_task = loop.create_task(main_system.initialize())
            _set_active_main_task(initialize_task)
            _run_until_complete(loop, initialize_task)
            main_tasks = loop.create_task(main_system.schedule_tasks())
            _set_active_main_task(main_tasks)
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
        # 覆盖初始化异常、正常任务返回及 restart=42；关闭失败不得伪装成成功。
        if loop is not None and not loop.is_closed():
            _cancel_main_task(loop, main_tasks)
            shutdown_completed = _run_graceful_shutdown(loop, main_system)
            if not shutdown_completed:
                exit_code = 1
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

        if worker_signals is not None:
            worker_signals.__exit__(None, None, None)

        # 使用 os._exit() 强制退出，避免被阻塞
        # 由于已经在 graceful_shutdown() 中完成了所有清理工作，这是安全的
        os._exit(exit_code)
