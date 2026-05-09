from typing import TYPE_CHECKING

from rich.traceback import install

import asyncio
import time

from src.A_memorix.host_service import a_memorix_host_service
from src.chat.message_receive.chat_manager import chat_manager
from src.chat.message_receive.bot import chat_bot
from src.chat.utils.statistic import OnlineTimeRecordTask, StatisticOutputTask
from src.common.i18n import t
from src.common.logger import get_logger
from src.common.message_server.server import Server, get_global_server
from src.config.config import config_manager, global_config
from src.emoji_system.emoji_manager import emoji_manager
from src.manager.async_task_manager import async_task_manager
from src.plugin_runtime.integration import get_plugin_runtime_manager
from src.prompt.prompt_manager import prompt_manager
from src.services.memory_diagnostics_service import MemoryDiagnosticsTask, is_memory_diagnostics_enabled
from src.services.memory_flow_service import memory_automation_service

# from src.api.main import start_api_server

# 导入插件运行时
# 导入消息API和traceback模块
# from src.chat.utils.token_statistics import TokenStatisticsTask

install(extra_lines=3)

logger = get_logger("main")


if TYPE_CHECKING:
    from maim_message import MessageServer
    from src.webui.webui_server import WebUIServer


class MainSystem:
    def __init__(self) -> None:
        # 使用消息API替代直接的FastAPI实例
        from src.common.message_server import get_global_api

        self.app: MessageServer = get_global_api()
        self.server: Server = get_global_server()
        self.webui_task: asyncio.Task[None] | None = None
        self.webui_server: WebUIServer | None = None  # 独立的 WebUI 服务器

    def _setup_webui_server(self) -> None:
        """设置独立的 WebUI 服务器"""
        from src.config.config import global_config

        if not global_config.webui.enabled:
            logger.info(t("startup.webui_disabled"))
            return

        try:
            from src.webui.webui_server import get_webui_server

            self.webui_server = get_webui_server()

        except Exception as e:
            logger.error(t("startup.webui_server_init_failed", error=e))

    async def initialize(self) -> None:
        """初始化系统组件"""
        logger.info(t("startup.waking_up", nickname=global_config.bot.nickname))

        self.webui_task = asyncio.create_task(self._run_webui_startup_sequence(), name="webui_startup")
        try:
            await self._init_components()
        except Exception:
            self.webui_task.cancel()
            await asyncio.gather(self.webui_task, return_exceptions=True)
            raise

        logger.info(t("startup.initialization_completed_banner", nickname=global_config.bot.nickname))

    async def _run_webui_startup_sequence(self) -> None:
        """启动 WebUI，同时允许主初始化并行执行。"""
        self._setup_webui_server()
        if self.webui_server:
            await self.webui_server.start()

    async def _init_components(self) -> None:
        """初始化其他组件"""
        init_start_time = time.time()

        await config_manager.start_file_watcher()
        a_memorix_host_service.register_config_reload_callback()
        prompt_manager.load_prompts()

        # 添加在线时间统计任务
        await async_task_manager.add_task(OnlineTimeRecordTask())

        # 添加统计信息输出任务
        await async_task_manager.add_task(StatisticOutputTask())

        # 添加遥测心跳任务
        from src.common.remote import TelemetryHeartBeatTask

        await async_task_manager.add_task(TelemetryHeartBeatTask())

        # 启动API服务器
        # start_api_server()
        # logger.info("API服务器启动成功")

        # 启动插件运行时（内置插件 + 第三方插件双子进程）
        await get_plugin_runtime_manager().start()
        await a_memorix_host_service.start()

        # 初始化表情管理器
        emoji_manager.load_emojis_from_db()
        logger.info(t("startup.emoji_manager_initialized"))

        # 初始化聊天管理器
        await chat_manager.initialize()
        asyncio.create_task(chat_manager.regularly_save_sessions())

        logger.info(t("startup.chat_manager_initialized"))
        await memory_automation_service.start()
        if is_memory_diagnostics_enabled():
            await async_task_manager.add_task(MemoryDiagnosticsTask())
            logger.info("内存诊断任务已启用")

        # await asyncio.sleep(0.5) #防止logger输出飞了

        # 将bot.py中的chat_bot.message_process消息处理函数注册到api.py的消息处理基类中
        self.app.register_message_handler(chat_bot.message_process)
        self.app.register_custom_message_handler("message_id_echo", chat_bot.echo_message_process)

        # 触发 ON_START 事件
        from src.core.event_bus import event_bus
        from src.core.types import EventType

        await event_bus.emit(event_type=EventType.ON_START)

        # 分发 ON_START 事件到插件运行时
        await get_plugin_runtime_manager().bridge_event("on_start")
        # logger.info("已触发 ON_START 事件")
        try:
            init_time = int(1000 * (time.time() - init_start_time))
            logger.info(t("startup.initialization_completed_cycles", init_time=init_time))
        except Exception as e:
            logger.error(t("startup.brain_external_world_failed", error=e))
            raise

    async def schedule_tasks(self) -> None:
        """调度定时任务"""
        try:
            tasks = [
                emoji_manager.periodic_emoji_maintenance(),
                self.app.run(),
                self.server.run(),
            ]

            # 如果 WebUI 服务器已初始化，添加到任务列表
            if self.webui_task:
                tasks.append(self.webui_task)
            elif self.webui_server:
                tasks.append(self.webui_server.start())

            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info(t("startup.schedule_cancelled"))
            raise

async def main() -> None:
    """主函数"""
    system = MainSystem()
    try:
        await system.initialize()
        await system.schedule_tasks()
    finally:
        emoji_manager.shutdown()
        await memory_automation_service.shutdown()
        await a_memorix_host_service.stop()
        await get_plugin_runtime_manager().bridge_event("on_stop")
        await get_plugin_runtime_manager().stop()
        await async_task_manager.stop_and_wait_all_tasks()
        emoji_manager.shutdown()
        await config_manager.stop_file_watcher()


if __name__ == "__main__":
    asyncio.run(main())
