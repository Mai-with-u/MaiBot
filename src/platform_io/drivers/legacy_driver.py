"""提供 Platform IO 的 legacy 传输驱动实现。"""

from typing import TYPE_CHECKING, Any, Dict, Optional

from src.platform_io.drivers.base import PlatformIODriver
from src.platform_io.types import DeliveryReceipt, DeliveryStatus, DriverDescriptor, DriverKind, RouteKey

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage


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
