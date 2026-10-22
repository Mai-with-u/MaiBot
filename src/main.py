from typing import TYPE_CHECKING, Any, Optional

from rich.traceback import install

import asyncio
import sys
import time

from src.common.i18n import t
from src.common.logger import get_logger
from src.common.runtime_loop import set_main_loop
from src.common.startup_diagnostics import diagnostic_stage, monitor_startup_loop
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
        self.watchdog_task: Optional[asyncio.Task[None]] = None

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

        # 主循环看门狗提前覆盖启动阶段；独立采样线程补充阻塞当时的调用栈。
        # WebUI 循环另有一份看门狗，挂在 WebUI 服务器启动处。
        from src.common.event_loop_watchdog import start_watchdog

        self.watchdog_task = start_watchdog("main")
        from src.common.memory_diagnostics import start_memory_diagnostics

        start_memory_diagnostics()

        try:
            # 启动现场采样默认开启，完成初始化后关闭；常驻看门狗仍由原配置控制。
            with monitor_startup_loop():
                from src.services.tool_record_cleanup_service import run_startup_tool_record_vacuum_if_needed

                with diagnostic_stage("startup.database_maintenance"):
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
        with diagnostic_stage("startup.import_plugin_runtime"):
            from src.plugin_runtime.integration import get_plugin_runtime_manager

        plugin_runtime_manager = get_plugin_runtime_manager()
        plugin_runtime_task = asyncio.create_task(plugin_runtime_manager.start(), name="plugin_runtime_start")
        with diagnostic_stage("startup.wait_runner_spawn"):
            await _wait_for_plugin_runners_spawned(plugin_runtime_manager, plugin_runtime_task)

        # 数据库启动维护已完成；让 WebUI 在线程中加载，与后续组件初始化并行，
        # 无需等待全部插件就绪后才开放管理页面。
        with diagnostic_stage("startup.webui"):
            self._start_webui_server()

        with diagnostic_stage("startup.import_memory_host"):
            from src.A_memorix.host_service import a_memorix_host_service
        with diagnostic_stage("startup.import_mcp"):
            from src.mcp_module.service import get_mcp_service
        with diagnostic_stage("startup.import_expression_vector"):
            from src.services.expression_vector_service import expression_vector_service

        a_memorix_host_service.register_config_reload_callback()
        get_mcp_service().register_config_reload_callback()
        with diagnostic_stage("startup.expression_vector_schedule"):
            expression_vector_service.start()
        a_memorix_task = asyncio.create_task(a_memorix_host_service.start(), name="a_memorix_start")

        with diagnostic_stage("startup.first_background_dispatch"):
            await asyncio.sleep(0)
        # 模板扫描、路径解析和文件读取在线程中完成，避免占住主循环导致 Runner 握手超时。
        with diagnostic_stage("startup.load_prompts"):
            await asyncio.to_thread(prompt_manager.load_prompts)

        from src.emoji_system.emoji_manager import emoji_manager

        emoji_load_task = asyncio.create_task(asyncio.to_thread(emoji_manager.load_emojis_from_db), name="emoji_load_from_db")

        # 启动API服务器
        # start_api_server()
        # logger.info("API服务器启动成功")

        try:
            with diagnostic_stage("startup.wait_components"):
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

        from src.agent.service import agent_service

        await agent_service.start()

        # await asyncio.sleep(0.5) #防止logger输出飞了

        # 触发 ON_START 事件，事件总线会统一桥接到 IPC 插件运行时。
        from src.core.event_bus import event_bus
        from src.core.types import EventType

        await event_bus.emit(event_type=EventType.ON_START)
        # logger.info("已触发 ON_START 事件")

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
            from src.maisaka.display.prompt_preview_logger import PromptPreviewLogger
            from src.services.image_path_maintenance_service import (
                run_image_path_maintenance_background,
                should_schedule_image_path_maintenance_background,
            )
            from src.webui.routers.data_transfer import periodic_transfer_temp_cleanup

            # 在独立线程中巡检推理图片，空闲时也能清理旧版本遗留的孤立缓存。
            PromptPreviewLogger.start()

            tasks = [
                emoji_manager.periodic_emoji_maintenance(),
                periodic_emoji_cache_cleanup(),
                periodic_image_cache_cleanup(),
                periodic_transfer_temp_cleanup(),
            ]
            # 插件式适配器通过 SDK 通信，不依赖 maim_message 的网络监听服务。
            if global_config.maim_message.enabled:
                self._register_message_handlers()
                if self.app is None or self.server is None:
                    raise RuntimeError("消息服务未初始化")
                tasks.extend([self.app.run(), self.server.run()])
            else:
                logger.info("maim_message 已关闭，跳过旧版 WS 和新版 API 服务启动")
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
        from src.chat.heart_flow.heartflow_manager import heartflow_manager
        from src.common.logger import stop_ws_broadcasts
        from src.common.shutdown import request_shutdown
        from src.agent.service import agent_service

        request_shutdown("main_finally")
        await stop_ws_broadcasts()
        if system.app is not None:
            await system.app.stop()
        await heartflow_manager.shutdown()
        await agent_service.stop()
        if system.webui_server:
            await system.webui_server.shutdown()
        if system.watchdog_task is not None:
            system.watchdog_task.cancel()
            system.watchdog_task = None
        from src.A_memorix.host_service import a_memorix_host_service
        from src.chat.image_system.image_manager import image_manager
        from src.emoji_system.emoji_manager import emoji_manager
        from src.mcp_module.service import get_mcp_service
        from src.plugin_runtime.integration import get_plugin_runtime_manager
        from src.services.expression_vector_service import expression_vector_service
        from src.services.memory_flow_service import memory_automation_service

        emoji_manager.shutdown()
        await image_manager.shutdown()
        await expression_vector_service.stop()
        await memory_automation_service.shutdown()
        await a_memorix_host_service.stop()
        await get_plugin_runtime_manager().bridge_event("on_stop")
        await get_plugin_runtime_manager().stop()
        await async_task_manager.stop_and_wait_all_tasks()
        await get_mcp_service().close()
        await config_manager.stop_file_watcher()
        set_main_loop(None)


if __name__ == "__main__":
    asyncio.run(main())
