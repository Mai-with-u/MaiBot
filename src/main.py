from typing import TYPE_CHECKING, Any

from rich.traceback import install

import asyncio
import sys
import time

from src.common.i18n import t
from src.common.logger import get_logger
from src.common.runtime_loop import set_main_loop
from src.config.config import config_manager, global_config
from src.manager.async_task_manager import async_task_manager
from src.prompt.prompt_manager import prompt_manager

# from src.api.main import start_api_server

# 导入插件运行时
# 导入消息API和traceback模块
# from src.chat.utils.token_statistics import TokenStatisticsTask

install(extra_lines=3)

logger = get_logger("main")


if TYPE_CHECKING:
    from maim_message import MessageServer
    from src.common.message_server.server import Server
    from src.webui.webui_server import ThreadedWebUIServer


async def _wait_for_plugin_runners_spawned(
    plugin_runtime_manager: Any,
    plugin_runtime_task: asyncio.Task[None],
    timeout: float = 1.0,
) -> None:
    """让插件 Runner 子进程先拉起，以便和后续重初始化并行。"""

    deadline = asyncio.get_running_loop().time() + timeout
    while not plugin_runtime_task.done():
        supervisors = list(getattr(plugin_runtime_manager, "supervisors", []))
        if supervisors and all(getattr(supervisor, "_runner_process", None) is not None for supervisor in supervisors):
            return
        if asyncio.get_running_loop().time() >= deadline:
            return
        await asyncio.sleep(0.02)


class MainSystem:
    def __init__(self) -> None:
        # 使用消息API替代直接的FastAPI实例
        self.app: MessageServer | None = None
        self.server: Server | None = None
        self.webui_server: ThreadedWebUIServer | None = None  # 独立线程中的 WebUI 服务器
        self._message_handlers_registered = False

    def _ensure_message_server(self) -> None:
        """按需初始化消息 API，避免阻塞主启动链路的早期阶段。"""

        if self.app is not None and self.server is not None:
            return

        from src.common.message_server import get_global_api
        from src.common.message_server.server import get_global_server

        self.app = get_global_api()
        self.server = get_global_server()

    def _register_message_handlers(self) -> None:
        """注册主消息处理器；消息服务实际调度前完成即可。"""

        if self._message_handlers_registered:
            return

        self._ensure_message_server()
        if self.app is None:
            raise RuntimeError("消息 API 初始化失败")

        from src.chat.message_receive.bot import chat_bot

        self.app.register_message_handler(chat_bot.message_process)
        self.app.register_custom_message_handler("message_id_echo", chat_bot.echo_message_process)
        self._message_handlers_registered = True

    def _start_webui_server(self) -> None:
        """启动独立线程中的 WebUI 服务器。"""
        from src.config.config import global_config

        if not global_config.webui.enabled:
            logger.info(t("startup.webui_disabled"))
            return

        try:
            from src.webui.webui_server import get_threaded_webui_server

            self.webui_server = get_threaded_webui_server()
            self.webui_server.start()

        except Exception as e:
            logger.error(t("startup.webui_server_init_failed", error=e))

    async def initialize(self) -> None:
        """初始化系统组件"""
        logger.info(t("startup.waking_up", nickname=global_config.bot.nickname))

        try:
            from src.services.tool_record_cleanup_service import run_startup_tool_record_vacuum_if_needed

            await asyncio.to_thread(run_startup_tool_record_vacuum_if_needed)
            await self._init_components()
        except Exception:
            if self.webui_server:
                await self.webui_server.shutdown()
            raise

        logger.info(t("startup.initialization_completed_banner", nickname=global_config.bot.nickname))

    async def _init_components(self) -> None:
        """初始化其他组件"""
        init_start_time = time.time()

        await config_manager.start_file_watcher()

        # 插件 Runner 启动最重，尽早发起以便和后续初始化并行。
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        plugin_runtime_manager = get_plugin_runtime_manager()
        plugin_runtime_task = asyncio.create_task(plugin_runtime_manager.start(), name="plugin_runtime_start")
        await _wait_for_plugin_runners_spawned(plugin_runtime_manager, plugin_runtime_task)

        from src.A_memorix.host_service import a_memorix_host_service

        a_memorix_host_service.register_config_reload_callback()
        a_memorix_task = asyncio.create_task(a_memorix_host_service.start(), name="a_memorix_start")

        await asyncio.sleep(0)
        prompt_manager.load_prompts()

        from src.emoji_system.emoji_manager import emoji_manager

        emoji_load_task = asyncio.create_task(asyncio.to_thread(emoji_manager.load_emojis_from_db), name="emoji_load_from_db")

        # 启动API服务器
        # start_api_server()
        # logger.info("API服务器启动成功")

        try:
            await asyncio.gather(plugin_runtime_task, a_memorix_task)
            await emoji_load_task
        except Exception:
            for task in (plugin_runtime_task, a_memorix_task, emoji_load_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                plugin_runtime_task,
                a_memorix_task,
                emoji_load_task,
                return_exceptions=True,
            )
            raise

        # 初始化表情管理器
        logger.info(t("startup.emoji_manager_initialized"))

        # 初始化聊天管理器
        from src.chat.message_receive.chat_manager import chat_manager
        from src.services.memory_flow_service import memory_automation_service

        await chat_manager.initialize()
        asyncio.create_task(chat_manager.regularly_save_sessions())

        logger.info(t("startup.chat_manager_initialized"))
        await memory_automation_service.start()

        # await asyncio.sleep(0.5) #防止logger输出飞了

        # 触发 ON_START 事件，事件总线会统一桥接到 IPC 插件运行时。
        from src.core.event_bus import event_bus
        from src.core.types import EventType

        await event_bus.emit(event_type=EventType.ON_START)
        # logger.info("已触发 ON_START 事件")

        self._start_webui_server()

        from src.chat.utils.statistic import OnlineTimeRecordTask, StatisticOutputTask

        # 添加在线时间统计任务
        await async_task_manager.add_task(OnlineTimeRecordTask())

        # 添加统计信息输出任务
        await async_task_manager.add_task(StatisticOutputTask())

        # 添加遥测心跳与统计上传任务
        from src.common.remote import TelemetryHeartBeatTask, TelemetryStatsUploadTask

        await async_task_manager.add_task(TelemetryHeartBeatTask())
        await async_task_manager.add_task(TelemetryStatsUploadTask())

        try:
            init_time = int(1000 * (time.time() - init_start_time))
            logger.info(t("startup.initialization_completed_cycles", init_time=init_time))
        except Exception as e:
            logger.error(t("startup.brain_external_world_failed", error=e))
            raise

    async def schedule_tasks(self) -> None:
        """调度定时任务"""
        try:
            from src.chat.image_system.image_cache_cleanup import periodic_image_cache_cleanup
            from src.emoji_system.emoji_cache_cleanup import periodic_emoji_cache_cleanup
            from src.emoji_system.emoji_manager import emoji_manager
            from src.services.image_path_maintenance_service import (
                run_image_path_maintenance_background,
                should_schedule_image_path_maintenance_background,
            )

            self._register_message_handlers()
            if self.app is None or self.server is None:
                raise RuntimeError("消息服务未初始化")

            tasks = [
                emoji_manager.periodic_emoji_maintenance(),
                periodic_emoji_cache_cleanup(),
                periodic_image_cache_cleanup(),
                self.app.run(),
                self.server.run(),
            ]
            if global_config.debug.enable_console_input:
                if sys.stdin.isatty():
                    from src.cli.bot_console import BotConsole

                    logger.info("已启用终端输入，可输入普通消息或 /clear、/pm 等指令；输入 exit() 关闭终端输入")
                    tasks.append(BotConsole().run())
                else:
                    logger.warning("终端输入已配置为启用，但当前 stdin 不是交互式终端，已跳过")
            image_path_maintenance_needed = await asyncio.to_thread(should_schedule_image_path_maintenance_background)
            if image_path_maintenance_needed:
                tasks.append(run_image_path_maintenance_background())

            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info(t("startup.schedule_cancelled"))
            raise


async def main() -> None:
    """主函数"""
    set_main_loop(asyncio.get_running_loop())
    system = MainSystem()
    try:
        await system.initialize()
        await system.schedule_tasks()
    finally:
        if system.webui_server:
            await system.webui_server.shutdown()
        from src.A_memorix.host_service import a_memorix_host_service
        from src.emoji_system.emoji_manager import emoji_manager
        from src.plugin_runtime.integration import get_plugin_runtime_manager
        from src.services.memory_flow_service import memory_automation_service

        emoji_manager.shutdown()
        await memory_automation_service.shutdown()
        await a_memorix_host_service.stop()
        await get_plugin_runtime_manager().bridge_event("on_stop")
        await get_plugin_runtime_manager().stop()
        await async_task_manager.stop_and_wait_all_tasks()
        await config_manager.stop_file_watcher()
        set_main_loop(None)


if __name__ == "__main__":
    asyncio.run(main())
