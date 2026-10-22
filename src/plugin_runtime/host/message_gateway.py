"""Host 侧消息网关包装器。"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict

from src.common.logger import get_logger
from src.platform_io import get_platform_io_manager

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
        from .message_utils import PluginMessageUtils

        return PluginMessageUtils._build_session_message_from_dict(external_message)

    def build_message_dict(self, internal_message: "SessionMessage") -> Dict[str, Any]:
        """将 ``SessionMessage`` 转换为标准消息字典。

        Args:
            internal_message: 内部消息对象。

        Returns:
            Dict[str, Any]: 供消息网关插件消费的标准消息字典。
        """
        from .message_utils import PluginMessageUtils

        return dict(PluginMessageUtils._session_message_to_dict(internal_message))

    async def receive_external_message(self, external_message: Dict[str, Any]) -> None:
        """接收外部消息并送入主消息链。

        Args:
            external_message: 外部消息的字典格式数据。
        """
        received_at = datetime.now()
        try:
            session_message = self.build_session_message(external_message)
            session_message.received_at = received_at
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
            enabled_only: 兼容旧签名的保留参数，当前未使用。
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
        delivery_batch = await platform_io_manager.send_message(internal_message, route_key)
        if not delivery_batch.has_success:
            logger.warning("通过消息网关链路发送消息失败: 未命中任何成功回执")
            return False

        first_successful_receipt = delivery_batch.sent_receipts[0]
        external_message_id = str(first_successful_receipt.external_message_id or "").strip()
        if external_message_id:
            internal_message.message_id = external_message_id
        if save_to_db:
            try:
                from src.common.utils.utils_message import MessageUtils

                await MessageUtils.store_message_to_db_async(internal_message)
            except Exception as e:
                logger.error(f"保存消息到数据库失败: {e}")
        return True
