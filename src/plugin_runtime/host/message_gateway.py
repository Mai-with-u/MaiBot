"""
Message Gateway 模块
适配器专用，用于将其他平台的消息转换为系统内部的消息格式，并将系统消息转换为其他平台的格式。
"""

from typing import TYPE_CHECKING, Any, Dict

from src.common.logger import get_logger
from src.platform_io import DeliveryStatus, get_platform_io_manager

from .message_utils import PluginMessageUtils

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage
    from .component_registry import ComponentRegistry
    from .supervisor import PluginRunnerSupervisor

logger = get_logger("plugin_runtime.host.message_gateway")


class MessageGateway:
    """Host 侧消息网关包装器。"""

    def __init__(self, component_registry: "ComponentRegistry") -> None:
        """初始化消息网关。

        Args:
            component_registry: 组件注册表。
        """
        self._component_registry = component_registry

    def build_session_message(self, external_message: Dict[str, Any]) -> "SessionMessage":
        """将标准消息字典转换为 ``SessionMessage``。

        Args:
            external_message: 外部消息的字典格式数据。

        Returns:
            SessionMessage: 转换后的内部消息对象。

        Raises:
            ValueError: 消息字典不合法时抛出。
        """
        return PluginMessageUtils._build_session_message_from_dict(external_message)

    def build_message_dict(self, internal_message: "SessionMessage") -> Dict[str, Any]:
        """将 ``SessionMessage`` 转换为标准消息字典。

        Args:
            internal_message: 内部消息对象。

        Returns:
            Dict[str, Any]: 供适配器插件消费的标准消息字典。
        """
        return dict(PluginMessageUtils._session_message_to_dict(internal_message))

    async def receive_external_message(self, external_message: Dict[str, Any]) -> None:
        """接收外部消息并送入主消息链。

        Args:
            external_message: 外部消息的字典格式数据。
        """
        try:
            session_message = self.build_session_message(external_message)
        except Exception as e:
            logger.error(f"转换外部消息失败: {e}")
            return

        from src.chat.message_receive.bot import chat_bot

        await chat_bot.receive_message(session_message)

    async def send_message_to_external(
        self,
        internal_message: "SessionMessage",
        supervisor: "PluginRunnerSupervisor",
        *,
        enabled_only: bool = True,
        save_to_db: bool = True,
    ) -> bool:
        """将内部消息通过 Platform IO 发送到外部平台。

        Args:
            internal_message: 系统内部的 ``SessionMessage`` 对象。
            supervisor: 当前持有该消息网关的 Supervisor。
            enabled_only: 兼容旧签名的保留参数，当前由 Platform IO 统一裁决。
            save_to_db: 发送成功后是否写入数据库。

        Returns:
            bool: 是否发送成功。
        """
        del enabled_only
        del supervisor

        platform_io_manager = get_platform_io_manager()
        if not platform_io_manager.is_started:
            logger.warning("Platform IO 尚未启动，无法通过适配器链路发送消息")
            return False

        route_key = platform_io_manager.build_route_key_from_message(internal_message)
        receipt = await platform_io_manager.send_message(internal_message, route_key)
        if receipt.status != DeliveryStatus.SENT:
            logger.warning(f"通过适配器链路发送消息失败: {receipt.error or receipt.status}")
            return False

        internal_message.message_id = receipt.external_message_id or internal_message.message_id
        if save_to_db:
            try:
                from src.common.utils.utils_message import MessageUtils

                MessageUtils.store_message_to_db(internal_message)
            except Exception as e:
                logger.error(f"保存消息到数据库失败: {e}")
        return True
