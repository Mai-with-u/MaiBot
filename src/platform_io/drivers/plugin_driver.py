"""提供 Platform IO 的 plugin 传输驱动骨架。"""

from typing import TYPE_CHECKING, Any, Dict, Optional

from src.platform_io.drivers.base import PlatformIODriver
from src.platform_io.types import DeliveryReceipt, DriverDescriptor, DriverKind, RouteKey

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage


class PluginPlatformDriver(PlatformIODriver):
    """面向 ``MessageGateway`` 插件链路的 Platform IO 驱动骨架。"""

    def __init__(
        self,
        driver_id: str,
        platform: str,
        account_id: Optional[str] = None,
        scope: Optional[str] = None,
        plugin_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """初始化一个 plugin 驱动描述对象。

        Args:
            driver_id: Broker 内的唯一驱动 ID。
            platform: 该 plugin 适配器链路负责的平台。
            account_id: 可选的账号 ID 或 self ID。
            scope: 可选的额外路由作用域。
            plugin_id: 拥有该适配器实现的插件 ID，可为空。
            metadata: 可选的额外驱动元数据。
        """
        descriptor = DriverDescriptor(
            driver_id=driver_id,
            kind=DriverKind.PLUGIN,
            platform=platform,
            account_id=account_id,
            scope=scope,
            plugin_id=plugin_id,
            metadata=metadata or {},
        )
        super().__init__(descriptor)

    async def send_message(
        self,
        message: "SessionMessage",
        route_key: RouteKey,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """通过 plugin 传输路径发送消息。

        Args:
            message: 要投递的内部会话消息。
            route_key: Broker 为本次投递选择的路由键。
            metadata: 本次出站投递可选的 Broker 侧元数据。

        Returns:
            DeliveryReceipt: 由驱动返回的规范化回执。

        Raises:
            NotImplementedError: 当前仍处于骨架阶段，尚未真正接入 MessageGateway。
        """
        raise NotImplementedError("PluginPlatformDriver 仅完成地基实现，尚未接入 MessageGateway")
