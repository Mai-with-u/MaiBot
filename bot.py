import asyncio
import hashlib
import os
import time
import platform
import traceback
import shutil
import sys
import subprocess
from dotenv import load_dotenv
from pathlib import Path
from rich.traceback import install
from src.common.logger import initialize_logging, get_logger, shutdown_logging

# 设置工作目录为脚本所在目录
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

env_path = Path(__file__).parent / ".env"
template_env_path = Path(__file__).parent / "template" / "template.env"

if env_path.exists():
    load_dotenv(str(env_path), override=True)
else:
    try:
        if template_env_path.exists():
            shutil.copyfile(template_env_path, env_path)
            print("未找到.env，已从 template/template.env 自动创建")
            load_dotenv(str(env_path), override=True)
        else:
            print("未找到.env文件，也未找到模板 template/template.env")
            raise FileNotFoundError(".env 文件不存在，请创建并配置所需的环境变量")
    except Exception as e:
        print(f"自动创建 .env 失败: {e}")
        raise

# 检查是否是 Worker 进程，只在 Worker 进程中输出详细的初始化信息
# Runner 进程只需要基本的日志功能，不需要详细的初始化日志
is_worker = os.environ.get("MAIBOT_WORKER_PROCESS") == "1"
initialize_logging(verbose=is_worker)
install(extra_lines=3)
logger = get_logger("main")

# 定义重启退出码
RESTART_EXIT_CODE = 42


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
        logger.info(f"正在启动 {script_file}...")
        logger.info("正在编译着色器：1/114514")

        # 启动子进程 (Worker)
        # 使用 sys.executable 确保使用相同的 Python 解释器
        cmd = [python_executable, script_file] + sys.argv[1:]

        process = subprocess.Popen(cmd, env=env)

        try:
            # 等待子进程结束
            return_code = process.wait()

            if return_code == RESTART_EXIT_CODE:
                logger.info("检测到重启请求 (退出码 42)，正在重启...")
                time.sleep(1)  # 稍作等待
                continue
            else:
                logger.info(f"程序已退出 (退出码 {return_code})")
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
                    logger.warning("子进程未响应，强制关闭...")
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
logger.info(f"已设置工作目录为: {script_dir}")


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
        f"{Fore.GREEN}  ★ MaiBot - 开源 AI 聊天机器人 ★{Style.RESET_ALL}",
        f"{Fore.CYAN}{'─' * 70}{Style.RESET_ALL}",
        f"{Fore.YELLOW}  本项目是完全免费的开源软件，基于 GPL-3.0 协议发布{Style.RESET_ALL}",
        f"{Fore.WHITE}  如果有人向你「出售本软件」，你被骗了！{Style.RESET_ALL}",
        "",
        f"{Fore.WHITE}  官方仓库: {Fore.BLUE}https://github.com/MaiM-with-u/MaiBot {Style.RESET_ALL}",
        f"{Fore.WHITE}  官方文档: {Fore.BLUE}https://docs.mai-mai.org {Style.RESET_ALL}",
        f"{Fore.WHITE}  官方群聊: {Fore.BLUE}1006149251{Style.RESET_ALL}",
        f"{Fore.CYAN}{'─' * 70}{Style.RESET_ALL}",
        f"{Fore.RED}  ⚠ 将本软件作为「商品」倒卖、隐瞒开源性质均违反协议！{Style.RESET_ALL}",
        f"{Fore.CYAN}{'═' * 70}{Style.RESET_ALL}",
        "",
    ]

    for line in notice_lines:
        print(line)


def easter_egg():
    # 彩蛋
    from colorama import init, Fore

    init()
    text = "多年以后，面对AI行刑队，张三将会回想起他2023年在会议上讨论人工智能的那个下午"
    rainbow_colors = [Fore.RED, Fore.YELLOW, Fore.GREEN, Fore.CYAN, Fore.BLUE, Fore.MAGENTA]
    rainbow_text = ""
    for i, char in enumerate(text):
        rainbow_text += rainbow_colors[i % len(rainbow_colors)] + char
    print(rainbow_text)


async def graceful_shutdown():  # sourcery skip: use-named-expression
    try:
        logger.info("正在优雅关闭麦麦...")

        # 关闭 WebUI 服务器
        try:
            from src.webui.webui_server import get_webui_server

            webui_server = get_webui_server()
            if webui_server and webui_server._server:
                await webui_server.shutdown()
        except Exception as e:
            logger.warning(f"关闭 WebUI 服务器时出错: {e}")

        from src.plugin_system.core.events_manager import events_manager
        from src.plugin_system.base.component_types import EventType

        # 触发 ON_STOP 事件
        await events_manager.handle_mai_events(event_type=EventType.ON_STOP)

        # 停止所有异步任务
        await async_task_manager.stop_and_wait_all_tasks()

        # 获取所有剩余任务，排除当前任务
        remaining_tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

        if remaining_tasks:
            logger.info(f"正在取消 {len(remaining_tasks)} 个剩余任务...")

            # 取消所有剩余任务
            for task in remaining_tasks:
                if not task.done():
                    task.cancel()

            # 等待所有任务完成，设置超时
            try:
                await asyncio.wait_for(asyncio.gather(*remaining_tasks, return_exceptions=True), timeout=15.0)
                logger.info("所有剩余任务已成功取消")
            except asyncio.TimeoutError:
                logger.warning("等待任务取消超时，强制继续关闭")
            except Exception as e:
                logger.error(f"等待任务取消时发生异常: {e}")

        logger.info("麦麦优雅关闭完成")

    except Exception as e:
        logger.error(f"麦麦关闭失败: {e}", exc_info=True)


def _calculate_file_hash(file_path: Path, file_type: str) -> str:
    """计算文件的MD5哈希值"""
    if not file_path.exists():
        logger.error(f"{file_type} 文件不存在")
        raise FileNotFoundError(f"{file_type} 文件不存在")

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
    confirm_logger.critical("EULA或隐私条款内容已更新，请在阅读后重新确认，继续运行视为同意更新后的以上两款协议")
    confirm_logger.critical(
        f'输入"同意"或"confirmed"或设置环境变量"EULA_AGREE={eula_hash}"和"PRIVACY_AGREE={privacy_hash}"继续运行'
    )

    while True:
        user_input = input().strip().lower()
        if user_input in ["同意", "confirmed"]:
            return
        confirm_logger.critical('请输入"同意"或"confirmed"以继续运行')


def _save_confirmations(eula_updated: bool, privacy_updated: bool, eula_hash: str, privacy_hash: str) -> None:
    """保存用户确认结果"""
    if eula_updated:
        logger.info(f"更新EULA确认文件{eula_hash}")
        Path("eula.confirmed").write_text(eula_hash, encoding="utf-8")

    if privacy_updated:
        logger.info(f"更新隐私条款确认文件{privacy_hash}")
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
    logger.info("检查EULA和隐私条款完成")

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
            logger.warning("收到中断信号，正在优雅关闭...")

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
                    logger.error(f"优雅关闭时发生错误: {ge}")
        # 新增：检测外部请求关闭

    except SystemExit as e:
        # 捕获 SystemExit (例如 sys.exit()) 并保留退出代码
        if isinstance(e.code, int):
            exit_code = e.code
        else:
            exit_code = 1 if e.code else 0
        if exit_code == RESTART_EXIT_CODE:
            logger.info("收到重启信号，准备退出并请求重启...")

    except Exception as e:
        logger.error(f"主程序发生异常: {str(e)} {str(traceback.format_exc())}")
        exit_code = 1  # 标记发生错误
    finally:
        # 确保 loop 在任何情况下都尝试关闭（如果存在且未关闭）
        if "loop" in locals() and loop and not loop.is_closed():
            loop.close()
            print("[主程序] 事件循环已关闭")

        # 关闭日志系统，释放文件句柄
        try:
            shutdown_logging()
        except Exception as e:
            print(f"关闭日志系统时出错: {e}")

        print("[主程序] 准备退出...")

        # 使用 os._exit() 强制退出，避免被阻塞
        # 由于已经在 graceful_shutdown() 中完成了所有清理工作，这是安全的
        os._exit(exit_code)
