# raise RuntimeError("System Not Ready")
from pathlib import Path

from dotenv import load_dotenv
from rich.traceback import install

import asyncio
import hashlib
import os
import platform
# import shutil
import subprocess
import sys
import time
import traceback

from src.common.i18n import set_locale, t, tn
from src.common.logger import get_logger, initialize_logging, shutdown_logging

# 设置工作目录为脚本所在目录
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
set_locale(os.getenv("MAIBOT_LOCALE", "zh-CN"))

env_path = Path(__file__).parent / ".env"
template_env_path = Path(__file__).parent / "template" / "template.env"

if env_path.exists():
    load_dotenv(str(env_path), override=True)
else:
    print("[WIP] no .env file found, and templates is not ready yet.")
    raise
    # try:
    #     if template_env_path.exists():
    #         shutil.copyfile(template_env_path, env_path)
    #         print(t("startup.env_created"))
    #         load_dotenv(str(env_path), override=True)
    #     else:
    #         print(t("startup.env_template_missing"))
    #         raise FileNotFoundError(t("startup.env_file_missing"))
    # except Exception as e:
    #     print(t("startup.env_auto_create_failed", error=e))
    #     raise

set_locale(os.getenv("MAIBOT_LOCALE", "zh-CN"))

# 检查是否是 Worker 进程，只在 Worker 进程中输出详细的初始化信息
# Runner 进程只需要基本的日志功能，不需要详细的初始化日志
is_worker = os.environ.get("MAIBOT_WORKER_PROCESS") == "1"
initialize_logging(verbose=is_worker)
install(extra_lines=3)
logger = get_logger("main")

# 定义重启退出码
RESTART_EXIT_CODE = 42
print("-----------------------------------------")
print("\n\n\n\n\n")
print(t("startup.dev_branch_warning"))
print("\n\n\n\n\n")
print("-----------------------------------------")


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
        logger.info(t("startup.compiling_shaders"))

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
        run_runner_process()
    # 如果作为模块导入，不执行 Runner 逻辑，但也不应该执行下面的 Worker 逻辑
    sys.exit(0)

# 以下是 Worker 进程的逻辑

# 最早期初始化日志系统，确保所有后续模块都使用正确的日志格式
# 注意：Runner 进程已经在第 37 行初始化了日志系统，但 Worker 进程是独立进程，需要重新初始化
# 由于 Runner 和 Worker 是不同进程，它们有独立的内存空间，所以都会初始化一次
# 这是正常的，但为了避免重复的初始化日志，我们在 initialize_logging() 中添加了防重复机制
# 不过由于是不同进程，每个进程仍会初始化一次，这是预期的行为

from src.main import MainSystem  # noqa
from src.manager.async_task_manager import async_task_manager  # noqa


# logger = get_logger("main")


# install(extra_lines=3)

# 设置工作目录为脚本所在目录
# script_dir = os.path.dirname(os.path.abspath(__file__))
# os.chdir(script_dir)
logger.info(t("startup.worker_dir_set", script_dir=script_dir))


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


async def graceful_shutdown():  # sourcery skip: use-named-expression
    try:
        logger.info(t("startup.shutdown_started"))

        # 关闭 WebUI 服务器
        # try:
        #     from src.webui.webui_server import get_webui_server

        #     webui_server = get_webui_server()
        #     if webui_server and webui_server._server:
        #         await webui_server.shutdown()
        # except Exception as e:
        #     logger.warning(f"关闭 WebUI 服务器时出错: {e}")

        from src.core.event_bus import event_bus
        from src.core.types import EventType

        # 触发 ON_STOP 事件
        await event_bus.emit(event_type=EventType.ON_STOP)

        # 停止新版本插件运行时
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        await get_plugin_runtime_manager().stop()

        # 停止所有异步任务
        await async_task_manager.stop_and_wait_all_tasks()

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
                await asyncio.wait_for(asyncio.gather(*remaining_tasks, return_exceptions=True), timeout=15.0)
                logger.info(t("startup.remaining_tasks_cancelled"))
            except asyncio.TimeoutError:
                logger.warning(t("startup.remaining_tasks_cancel_timeout"))
            except Exception as e:
                logger.error(t("startup.remaining_tasks_cancel_error", error=e))

        logger.info(t("startup.shutdown_completed"))

    except Exception as e:
        logger.error(t("startup.shutdown_failed", error=e), exc_info=True)


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
    try:
        # 获取MainSystem实例
        main_system = raw_main()

        # 创建事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 初始化 WebSocket 日志推送
        from src.common.logger import initialize_ws_handler

        initialize_ws_handler(loop)

        try:
            # 执行初始化和任务调度
            loop.run_until_complete(main_system.initialize())
            # Schedule tasks returns a future that runs forever.
            # We can run console_input_loop concurrently.
            main_tasks = loop.create_task(main_system.schedule_tasks())
            loop.run_until_complete(main_tasks)

        except KeyboardInterrupt:
            logger.warning(t("startup.interrupt_received"))

            # 取消主任务
            if "main_tasks" in locals() and main_tasks and not main_tasks.done():
                main_tasks.cancel()
                try:
                    loop.run_until_complete(main_tasks)
                except asyncio.CancelledError:
                    pass

            # 执行优雅关闭
            if loop and not loop.is_closed():
                try:
                    loop.run_until_complete(graceful_shutdown())
                except Exception as ge:
                    logger.error(t("startup.graceful_shutdown_error", error=ge))
        # 新增：检测外部请求关闭

    except SystemExit as e:
        # 捕获 SystemExit (例如 sys.exit()) 并保留退出代码
        if isinstance(e.code, int):
            exit_code = e.code
        else:
            exit_code = 1 if e.code else 0
        if exit_code == RESTART_EXIT_CODE:
            logger.info(t("startup.restart_signal_received"))

    except Exception as e:
        logger.error(t("startup.main_error", error=f"{str(e)} {str(traceback.format_exc())}"))
        exit_code = 1  # 标记发生错误
    finally:
        # 确保 loop 在任何情况下都尝试关闭（如果存在且未关闭）
        if "loop" in locals() and loop and not loop.is_closed():
            loop.close()
            print(t("startup.event_loop_closed"))

        # 关闭日志系统，释放文件句柄
        try:
            shutdown_logging()
        except Exception as e:
            print(t("startup.logging_shutdown_error", error=e))

        print(t("startup.prepare_exit"))

        # 使用 os._exit() 强制退出，避免被阻塞
        # 由于已经在 graceful_shutdown() 中完成了所有清理工作，这是安全的
        os._exit(exit_code)
