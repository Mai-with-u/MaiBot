"""定义 Platform IO 传输驱动的基础抽象协议。"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional

from src.platform_io.types import DeliveryReceipt, DriverDescriptor, InboundMessageEnvelope, RouteKey

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage

InboundHandler = Callable[[InboundMessageEnvelope], Awaitable[bool]]


class PlatformIODriver(ABC):
    """定义所有 Platform IO 驱动都必须实现的最小契约。

    当前实现故意保持接口很小，让中间层可以先落地，再逐步把 legacy
    与 plugin 路径的真实收发能力迁入这套协议之下。
    """

    def __init__(self, descriptor: DriverDescriptor) -> None:
        """使用驱动描述对象初始化驱动。

        Args:
            descriptor: 注册到 Broker 中的静态驱动元数据。
        """
        self._descriptor = descriptor
        self._inbound_handler: Optional[InboundHandler] = None

    @property
    def descriptor(self) -> DriverDescriptor:
        """返回当前驱动的描述对象。

        Returns:
            DriverDescriptor: 当前驱动实例对应的描述对象。
        """
        return self._descriptor

    @property
    def driver_id(self) -> str:
        """返回驱动标识。

        Returns:
            str: 当前驱动的唯一 ID。
        """
        return self._descriptor.driver_id

    def set_inbound_handler(self, handler: InboundHandler) -> None:
        """注册入站消息交回 Broker 的回调函数。

        Args:
            handler: 将规范化入站封装继续转发给 Broker 的异步回调。
        """
        self._inbound_handler = handler

    def clear_inbound_handler(self) -> None:
        """清除当前注册的入站回调函数。"""
        self._inbound_handler = None

    async def emit_inbound(self, envelope: InboundMessageEnvelope) -> bool:
        """将一条入站封装转交给 Broker 回调。

        Args:
            envelope: 由驱动产出的规范化入站封装。

        Returns:
            bool: 若 Broker 接受该入站消息则返回 ``True``，否则返回 ``False``。
        """

        if self._inbound_handler is None:
            return False
        return await self._inbound_handler(envelope)

    async def start(self) -> None:
        """启动驱动生命周期。

        子类后续若需要初始化逻辑，可以覆盖这个钩子。
        """
        return None

    async def stop(self) -> None:
        """停止驱动生命周期。

        子类后续若需要清理逻辑，可以覆盖这个钩子。
        """
        return None

    @abstractmethod
    async def send_message(
        self,
        message: "SessionMessage",
        route_key: RouteKey,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """通过具体驱动发送一条消息。

        Args:
            message: 要投递的内部会话消息。
            route_key: Broker 为本次投递选中的路由键。
            metadata: 本次出站投递可选的 Broker 侧元数据。

        Returns:
            DeliveryReceipt: 规范化后的投递结果。
        """
