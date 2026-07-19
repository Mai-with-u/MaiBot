"""Maisaka CLI 展示适配。"""

from rich.markdown import Markdown
from rich.panel import Panel

from src.common.logger import get_logger
from src.config.config import global_config
from src.core.local_operator import MAISAKA_CLI_PLATFORM

from .console import console

CLI_PLATFORM_NAME = MAISAKA_CLI_PLATFORM

logger = get_logger("maisaka_cli_sender")


def render_cli_message(content: str, *, title: str = "") -> None:
    """将 CLI 私聊实例的消息展示到终端。"""
    preview_text = content.strip() or "..."
    console.print(
        Panel(
            Markdown(preview_text),
            title=title or global_config.bot.nickname.strip() or "MaiSaka",
            border_style="magenta",
            padding=(1, 2),
        )
    )
    logger.info(f"[CLI] 已将消息输出到终端: content={preview_text!r}")
