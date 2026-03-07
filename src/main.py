import asyncio
import time
from maim_message import MessageServer

from src.common.remote import TelemetryHeartBeatTask
from src.manager.async_task_manager import async_task_manager
from src.chat.utils.statistic import OnlineTimeRecordTask, StatisticOutputTask

# from src.chat.utils.token_statistics import TokenStatisticsTask
from src.chat.emoji_system.emoji_manager import emoji_manager
from src.chat.message_receive.chat_manager import chat_manager
from src.config.config import config_manager, global_config
from src.chat.message_receive.bot import chat_bot
from src.common.logger import get_logger
from src.common.message_server.server import get_global_server, Server
from src.chat.knowledge import lpmm_start_up
from rich.traceback import install

# from src.api.main import start_api_server

# 导入插件运行时
from src.plugin_runtime.integration import get_plugin_runtime_manager

# 导入消息API和traceback模块
from src.common.message_server import get_global_api
from src.dream.dream_agent import start_dream_scheduler
from src.bw_learner.expression_auto_check_task import ExpressionAutoCheckTask

from src.prompt.prompt_manager import prompt_manager

install(extra_lines=3)

logger = get_logger("main")


class MainSystem:
    def __init__(self):
        # 使用消息API替代直接的FastAPI实例
        self.app: MessageServer = get_global_api()
        self.server: Server = get_global_server()
        self.webui_server = None  # 独立的 WebUI 服务器

        # 设置独立的 WebUI 服务器
        self._setup_webui_server()

    def _setup_webui_server(self):
        """设置独立的 WebUI 服务器"""
        from src.config.config import global_config

        if not global_config.webui.enabled:
            logger.info("WebUI 已禁用")
            return

        try:
            from src.webui.webui_server import get_webui_server

            self.webui_server = get_webui_server()

        except Exception as e:
            logger.error(f"❌ 初始化 WebUI 服务器失败: {e}")

    async def initialize(self):
        """初始化系统组件"""
        logger.info(f"正在唤醒{global_config.bot.nickname}......")

        # 其他初始化任务
        await asyncio.gather(self._init_components())

        logger.info(f"""
--------------------------------
全部系统初始化完成，{global_config.bot.nickname}已成功唤醒
--------------------------------
如果想要自定义{global_config.bot.nickname}的功能,请查阅：https://docs.mai-mai.org/manual/usage/
或者遇到了问题，请访问我们的文档:https://docs.mai-mai.org/
--------------------------------
如果你想要编写或了解插件相关内容，请访问开发文档https://docs.mai-mai.org/develop/
--------------------------------
如果你需要查阅模型的消耗以及麦麦的统计数据，请访问根目录的maibot_statistics.html文件
""")

    async def _init_components(self):
        """初始化其他组件"""
        init_start_time = time.time()

        await config_manager.start_file_watcher()

        # 添加在线时间统计任务
        await async_task_manager.add_task(OnlineTimeRecordTask())

        # 添加统计信息输出任务
        await async_task_manager.add_task(StatisticOutputTask())

        # 添加遥测心跳任务
        await async_task_manager.add_task(TelemetryHeartBeatTask())

        # 添加表达方式自动检查任务
        await async_task_manager.add_task(ExpressionAutoCheckTask())

        # 启动API服务器
        # start_api_server()
        # logger.info("API服务器启动成功")

        # 启动LPMM
        lpmm_start_up()

        # 启动插件运行时（内置插件 + 第三方插件双子进程）
        await get_plugin_runtime_manager().start()

        # 初始化表情管理器
        emoji_manager.load_emojis_from_db()
        logger.info("表情包管理器初始化成功")

        # 初始化聊天管理器
        await chat_manager.initialize()
        asyncio.create_task(chat_manager.regularly_save_sessions())

        logger.info("聊天管理器初始化成功")

        # await asyncio.sleep(0.5) #防止logger输出飞了

        # 将bot.py中的chat_bot.message_process消息处理函数注册到api.py的消息处理基类中
        self.app.register_message_handler(chat_bot.message_process)
        self.app.register_custom_message_handler("message_id_echo", chat_bot.echo_message_process)

        prompt_manager.load_prompts()

        # 触发 ON_START 事件
        from src.core.event_bus import event_bus
        from src.core.types import EventType

        await event_bus.emit(event_type=EventType.ON_START)

        # 分发 ON_START 事件到插件运行时
        await get_plugin_runtime_manager().bridge_event("on_start")
        # logger.info("已触发 ON_START 事件")
        try:
            init_time = int(1000 * (time.time() - init_start_time))
            logger.info(f"初始化完成，神经元放电{init_time}次")
        except Exception as e:
            logger.error(f"启动大脑和外部世界失败: {e}")
            raise

    async def schedule_tasks(self):
        """调度定时任务"""
        try:
            tasks = [
                emoji_manager.periodic_emoji_maintenance(),
                start_dream_scheduler(),
                self.app.run(),
                self.server.run(),
            ]

            # 如果 WebUI 服务器已初始化，添加到任务列表
            if self.webui_server:
                tasks.append(self.webui_server.start())

            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("调度任务已取消")
            raise

    # async def forget_memory_task(self):
    #     """记忆遗忘任务"""
    #     while True:
    #         await asyncio.sleep(global_config.memory.forget_memory_interval)
    #         logger.info("[记忆遗忘] 开始遗忘记忆...")
    #         await self.hippocampus_manager.forget_memory(percentage=global_config.memory.memory_forget_percentage)  # type: ignore
    #         logger.info("[记忆遗忘] 记忆遗忘完成")


async def main():
    """主函数"""
    system = MainSystem()
    try:
        await asyncio.gather(
            system.initialize(),
            system.schedule_tasks(),
        )
    finally:
        await config_manager.stop_file_watcher()


if __name__ == "__main__":
    asyncio.run(main())
