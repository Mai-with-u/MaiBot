"""
Message Gateway 模块
适配器专用，用于将其他平台的消息转换为系统内部的消息格式，并将系统消息转换为其他平台的格式。
"""

from typing import Dict, Any, TYPE_CHECKING

from src.common.logger import get_logger
from .message_utils import PluginMessageUtils

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage
    from .component_registry import ComponentRegistry
    from .supervisor import PluginRunnerSupervisor

logger = get_logger("plugin_runtime.host.message_gateway")


class MessageGateway:
    def __init__(self, component_registry: "ComponentRegistry") -> None:
        self._component_registry = component_registry

    async def receive_external_message(self, external_message: Dict[str, Any]):
        """
        接收外部消息，转换为系统内部格式，并返回转换结果

        Args:
            external_message: 外部消息的字典格式数据

        Returns:
            转换后的 SessionMessage 对象
        """
        # 使用递归函数将外部消息字典转换为 SessionMessage
        try:
            session_message = PluginMessageUtils._build_session_message_from_dict(external_message)
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
        """
        接收系统内部消息，转换为外部格式，并返回转换结果

        Args:
            internal_message: 系统内部的 SessionMessage 对象

        Returns:
            转换是否成功
        """
        try:
            # 将 SessionMessage 转换为字典格式
            message_dict = PluginMessageUtils._session_message_to_dict(internal_message)
        except Exception as e:
            logger.error(f"转换内部消息失败：{e}")
            return False
        gateway_entry = self._component_registry.get_message_gateways(
            internal_message.platform,
            enabled_only=enabled_only,
            session_id=internal_message.session_id,
        )
        if not gateway_entry:
            logger.warning(f"未找到适配平台 {internal_message.platform} 的消息网关组件，无法发送消息到外部平台")
            return False
        args = {"platform": internal_message.platform, "message": message_dict}
        try:
            resp_envelope = await supervisor.invoke_plugin(
                "plugin.emit_event", gateway_entry.plugin_id, gateway_entry.name, args
            )
            logger.debug("信息发送成功")
        except Exception as e:
            logger.error(f"调用消息网关组件失败：{e}")
            return False

        # 更新为实际id（如果组件返回了新的id）
        actual_message_id = resp_envelope.payload.get("message_id")
        try:
            actual_message_id = str(actual_message_id)
        except Exception:
            actual_message_id = None
        internal_message.message_id = actual_message_id or internal_message.message_id
        if save_to_db:
            try:
                from src.common.utils.utils_message import MessageUtils

                MessageUtils.store_message_to_db(internal_message)
            except Exception as e:
                logger.error(f"保存消息到数据库失败: {e}")
        return True
