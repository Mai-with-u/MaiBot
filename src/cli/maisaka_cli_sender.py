"""Maisaka CLI 展示适配。"""

from typing import Any, Dict, Optional

from rich.markdown import Markdown
from rich.panel import Panel

from src.chat.message_receive.message import SessionMessage
from src.common.logger import get_logger
from src.config.config import global_config
from src.core.local_operator import MAISAKA_CLI_PLATFORM
from src.platform_io import DeliveryReceipt, DeliveryStatus, DriverDescriptor, DriverKind, RouteKey
from src.platform_io.drivers.base import PlatformIODriver

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


class CliPlatformDriver(PlatformIODriver):
    """将 Maisaka CLI 平台的出站消息渲染到当前终端。"""

    DRIVER_ID = "local.maisaka_cli"

    def __init__(self) -> None:
        super().__init__(
            DriverDescriptor(
                driver_id=self.DRIVER_ID,
                kind=DriverKind.LOCAL,
                platform=CLI_PLATFORM_NAME,
            )
        )

    async def send_message(
        self,
        message: SessionMessage,
        route_key: RouteKey,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """渲染消息并返回标准 Platform IO 回执。"""

        content = message.processed_plain_text.strip() if message.processed_plain_text else ""
        if not content:
            component_names = [component.format_name for component in message.raw_message.components]
            content = f"[{', '.join(component_names)}]"

        render_cli_message(content)
        return DeliveryReceipt(
            internal_message_id=message.message_id,
            route_key=route_key,
            status=DeliveryStatus.SENT,
            driver_id=self.driver_id,
            driver_kind=self.descriptor.kind,
        )
