"""提供 Platform IO 的 legacy 传输驱动实现。"""

from typing import TYPE_CHECKING, Any, Dict, Optional

import traceback

from src.common.logger import get_logger
from src.platform_io.drivers.base import PlatformIODriver
from src.platform_io.types import DeliveryReceipt, DeliveryStatus, DriverDescriptor, DriverKind, RouteKey

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage

logger = get_logger("platform_io.legacy_driver")


def extract_legacy_ws_platform(message_data: Dict[str, Any]) -> str:
    """从 Legacy WS 入站字典中提取平台名。

    Args:
        message_data: 适配器上报的统一消息字典。

    Returns:
        str: 规范化后的平台名。

    Raises:
        TypeError: 当入站载荷不是字典时抛出。
        ValueError: 当字典中缺少有效 ``platform`` 时抛出。
    """
    if not isinstance(message_data, dict):
        raise TypeError("Legacy WS 入站消息必须是字典")

    message_info = message_data.get("message_info")
    if isinstance(message_info, dict):
        platform = str(message_info.get("platform") or "").strip()
        if platform:
            return platform

    platform = str(message_data.get("platform") or "").strip()
    if platform:
        return platform

    raise ValueError("Legacy WS 入站消息缺少 platform")


def _register_ws_inbound_handler() -> None:
    """把 Legacy WS 入站回调挂到 MessageServer。"""

    from src.common.message_server import register_inbound_handler

    register_inbound_handler(LegacyPlatformDriver.handle_ws_inbound)


def _normalize_legacy_user_and_group_ids(message_data: Dict[str, Any]) -> None:
    """把入站字典中的用户 ID、群 ID 规范成字符串。"""

    message_info = message_data["message_info"]
    if message_info.get("group_info") is not None:
        message_info["group_info"]["group_id"] = str(message_info["group_info"]["group_id"])
    if message_info.get("user_info") is not None:
        message_info["user_info"]["user_id"] = str(message_info["user_info"]["user_id"])


class LegacyPlatformDriver(PlatformIODriver):
    """面向 ``UniversalMessageSender`` 旧链的 Platform IO 驱动。"""

    def __init__(
        self,
        driver_id: str,
        platform: str,
        account_id: Optional[str] = None,
        scope: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """初始化一个 legacy 驱动描述对象。

        Args:
            driver_id: Broker 内的唯一驱动 ID。
            platform: 该 legacy 适配器链路负责的平台。
            account_id: 可选的账号 ID。
            scope: 可选的额外路由作用域。
            metadata: 可选的额外驱动元数据。
        """
        descriptor = DriverDescriptor(
            driver_id=driver_id,
            kind=DriverKind.LEGACY,
            platform=platform,
            account_id=account_id,
            scope=scope,
            metadata=metadata or {},
        )
        super().__init__(descriptor)

    async def start(self) -> None:
        """启动驱动，并确保 Legacy WS 入站已挂到 MessageServer。"""

        _register_ws_inbound_handler()

    @staticmethod
    async def handle_ws_inbound(message_data: Dict[str, Any]) -> None:
        """接收 MessageServer 的 WS 入站消息，并交给对应 legacy 驱动。

        Args:
            message_data: 适配器整理后的统一消息字典。
        """
        try:
            from src.platform_io.manager import get_platform_io_manager

            manager = get_platform_io_manager()
            driver = await manager.ensure_legacy_inbound_driver(message_data)
            await driver.emit_inbound(message_data)
        except Exception as exc:
            logger.error(f"处理 Legacy WS 入站消息失败: {exc}")
            traceback.print_exc()

    async def emit_inbound(self, message_data: Dict[str, Any]) -> bool:
        """将一条 Legacy WS 消息规范化后交给 Broker 入站回调。

        Args:
            message_data: 适配器整理后的统一消息字典。

        Returns:
            bool: Broker 接受该消息时返回 ``True``。

        Raises:
            RuntimeError: 当驱动尚未挂接入站回调时抛出。
        """
        from maim_message import MessageBase

        from src.chat.message_receive.bot import chat_bot
        from src.chat.message_receive.message import SessionMessage
        from src.platform_io.route_key_factory import RouteKeyFactory
        from src.platform_io.types import InboundMessageEnvelope

        if self._inbound_handler is None:
            raise RuntimeError(f"Legacy 驱动 {self.driver_id} 尚未挂接入站回调")

        await chat_bot._ensure_started()
        _normalize_legacy_user_and_group_ids(message_data)
        message = SessionMessage.from_maim_message(MessageBase.from_dict(message_data))
        envelope = InboundMessageEnvelope(
            route_key=RouteKeyFactory.from_session_message(message),
            driver_id=self.driver_id,
            driver_kind=self.descriptor.kind,
            external_message_id=message.message_id,
            session_message=message,
            payload=message_data,
        )
        return await self._inbound_handler(envelope)

    async def send_message(
        self,
        message: "SessionMessage",
        route_key: RouteKey,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """通过旧链发送一条已经过预处理的消息。

        Args:
            message: 要投递的内部会话消息。
            route_key: Broker 为本次投递选择的路由键。
            metadata: 本次出站投递可选的 Broker 侧元数据。

        Returns:
            DeliveryReceipt: 规范化后的发送回执。
        """
        from src.chat.message_receive.uni_message_sender import send_prepared_message_to_platform

        show_log = False
        if isinstance(metadata, dict):
            show_log = bool(metadata.get("show_log", False))

        try:
            sent = await send_prepared_message_to_platform(message, show_log=show_log)
        except Exception as exc:
            return DeliveryReceipt(
                internal_message_id=message.message_id,
                route_key=route_key,
                status=DeliveryStatus.FAILED,
                driver_id=self.driver_id,
                driver_kind=self.descriptor.kind,
                error=str(exc),
            )

        if not sent:
            return DeliveryReceipt(
                internal_message_id=message.message_id,
                route_key=route_key,
                status=DeliveryStatus.FAILED,
                driver_id=self.driver_id,
                driver_kind=self.descriptor.kind,
                error="旧链发送失败",
            )

        return DeliveryReceipt(
            internal_message_id=message.message_id,
            route_key=route_key,
            status=DeliveryStatus.SENT,
            driver_id=self.driver_id,
            driver_kind=self.descriptor.kind,
        )
