"""
MaiSaka CLI and conversation loop.
"""

from datetime import datetime

import asyncio

from rich import box
from rich.panel import Panel
from rich.text import Text

from src.chat.heart_flow.heartflow_manager import heartflow_manager
from src.chat.heart_flow.heartflow_message_processor import HeartFCMessageReceiver
from src.chat.message_receive.chat_manager import BotChatSession, chat_manager
from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.config.config import config_manager

from .maisaka_cli_sender import CLI_PLATFORM_NAME
from .console import console
from .input_reader import InputReader


class BufferCLI:
    """Maisaka 命令行交互入口。"""

    _CLI_PLATFORM = CLI_PLATFORM_NAME
    _CLI_USER_ID = "maisaka_user"

    def __init__(self) -> None:
        self._reader = InputReader()
        self._message_receiver = HeartFCMessageReceiver()
        self._session: BotChatSession | None = None

    @staticmethod
    def _get_current_model_name() -> str:
        """读取当前 planner 模型名。"""
        try:
            model_task_config = config_manager.get_model_config().model_task_config
            if model_task_config.planner.model_list:
                return model_task_config.planner.model_list[0]
        except Exception:
            pass
        return "未配置"

    def _show_banner(self) -> None:
        """渲染启动横幅。"""
        banner = Text()
        banner.append("MaiSaka", style="bold cyan")
        banner.append(" v2.0\n", style="muted")
        banner.append(f"模型: {self._get_current_model_name()}\n", style="muted")
        banner.append("输入内容开始对话 | Ctrl+C 退出", style="muted")
        console.print(Panel(banner, box=box.DOUBLE_EDGE, border_style="cyan", padding=(1, 2)))
        console.print()

    @staticmethod
    def _build_cli_session_message(
        *,
        user_text: str,
        timestamp: datetime,
    ) -> SessionMessage:
        """构造一条供 heartflow 复用的 CLI 用户消息。"""
        message = SessionMessage(
            message_id=f"maisaka_cli_{int(timestamp.timestamp() * 1000)}",
            timestamp=timestamp,
            platform=BufferCLI._CLI_PLATFORM,
        )
        message.message_info = MessageInfo(
            user_info=UserInfo(
                user_id=BufferCLI._CLI_USER_ID,
                user_nickname="用户",
                user_cardname=None,
            ),
            group_info=None,
            additional_config={},
        )
        message.raw_message = MessageSequence([TextComponent(text=user_text)])
        message.processed_plain_text = user_text
        message.initialized = True
        return message

    async def _dispatch_input(self, user_text: str) -> None:
        """将 CLI 输入转发到 heartflow 路径。"""
        message = self._build_cli_session_message(
            user_text=user_text,
            timestamp=datetime.now(),
        )
        chat_manager.register_message(message)
        self._session = await chat_manager.get_or_create_session(
            platform=self._CLI_PLATFORM,
            user_id=self._CLI_USER_ID,
        )
        await self._message_receiver.process_message(message)

    async def run(self) -> None:
        """主交互循环。"""
        self._reader.start(asyncio.get_event_loop())
        self._show_banner()

        try:
            while True:
                console.print("[bold cyan]> [/bold cyan]", end="")
                raw_input = await self._reader.get_line()
                if raw_input is None:
                    console.print("\n[muted]再见[/muted]")
                    break

                user_text = raw_input.strip()
                if not user_text:
                    continue

                await self._dispatch_input(user_text)
        finally:
            if self._session is not None:
                try:
                    await heartflow_manager.release_chat(self._session.session_id, reason="cli_exit")
                except Exception as exc:
                    # 退出清理为尽力而为：停止失败不应中断退出流程
                    console.print(f"[bold red]释放心流运行时失败: {exc}[/bold red]")
