"""
Message Gateway 模块
适配器专用，用于将其他平台的消息转换为系统内部的消息格式，并将系统消息转换为其他平台的格式。
"""

from datetime import datetime
from typing import Dict, Any, TYPE_CHECKING, TypedDict, Optional, List

from src.common.logger import get_logger
from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import UserInfo, GroupInfo, MessageInfo
from src.common.data_models.message_component_data_model import MessageSequence

if TYPE_CHECKING:
    from .component_registry import ComponentRegistry
    from .supervisor import PluginRunnerSupervisor

logger = get_logger("plugin_runtime.host.message_gateway")


class UserInfoDict(TypedDict, total=False):
    user_id: str
    user_nickname: str
    user_cardname: Optional[str]


class GroupInfoDict(TypedDict, total=False):
    group_id: str
    group_name: str


class MessageInfoDict(TypedDict, total=False):
    user_info: UserInfoDict
    group_info: Optional[GroupInfoDict]
    additional_config: Dict[str, Any]


class MessageDict(TypedDict, total=False):
    message_id: str
    timestamp: str
    platform: str
    message_info: MessageInfoDict
    raw_message: List[Dict[str, Any]]
    is_mentioned: bool
    is_at: bool
    is_emoji: bool
    is_picture: bool
    is_command: bool
    is_notify: bool
    session_id: str
    reply_to: Optional[str]
    processed_plain_text: Optional[str]
    display_message: Optional[str]


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
            session_message = self._build_session_message_from_dict(external_message)
        except Exception as e:
            logger.error(f"转换外部消息失败: {e}")
            return
        from src.chat.message_receive.bot import chat_bot

        await chat_bot.receive_message(session_message)

    async def send_message_to_external(
        self,
        internal_message: SessionMessage,
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
            message_dict = self._session_message_to_dict(internal_message)
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

    def _message_info_to_dict(self, message_info: MessageInfo) -> MessageInfoDict:
        """
        将 MessageInfo 对象转换为字典格式

        Args:
            message_info: MessageInfo 对象

        Returns:
            字典格式的消息信息
        """
        user_info_dict = UserInfoDict(
            user_id=message_info.user_info.user_id,
            user_nickname=message_info.user_info.user_nickname,
            user_cardname=message_info.user_info.user_cardname,
        )

        group_info_dict: Optional[GroupInfoDict] = None
        if message_info.group_info:
            group_info_dict = GroupInfoDict(
                group_id=message_info.group_info.group_id,
                group_name=message_info.group_info.group_name,
            )

        return MessageInfoDict(
            user_info=user_info_dict,
            group_info=group_info_dict,
            additional_config=message_info.additional_config,
        )

    def _session_message_to_dict(self, session_message: SessionMessage) -> MessageDict:
        """
        将 SessionMessage 对象转换为字典格式（复用 MessageSequence.to_dict 方法）

        Args:
            session_message: SessionMessage 对象

        Returns:
            字典格式的消息
        """
        # 转换基本信息
        message_dict = MessageDict(
            message_id=session_message.message_id,
            timestamp=str(session_message.timestamp.timestamp()),  # 转换为时间戳字符串
            platform=session_message.platform,
            message_info=self._message_info_to_dict(session_message.message_info),
            raw_message=session_message.raw_message.to_dict(),  # 复用 MessageSequence.to_dict()
            is_mentioned=session_message.is_mentioned,
            is_at=session_message.is_at,
            is_emoji=session_message.is_emoji,
            is_picture=session_message.is_picture,
            is_command=session_message.is_command,
            is_notify=session_message.is_notify,
            session_id=session_message.session_id,
        )

        # 添加可选字段
        if session_message.reply_to is not None:
            message_dict["reply_to"] = session_message.reply_to
        if session_message.processed_plain_text is not None:
            message_dict["processed_plain_text"] = session_message.processed_plain_text
        if session_message.display_message is not None:
            message_dict["display_message"] = session_message.display_message

        return message_dict

    def _build_message_info_from_dict(self, message_info_dict: Dict[str, Any]) -> MessageInfo:
        """
        从字典构建 MessageInfo 对象

        Args:
            message_info_dict: 包含消息信息的字典

        Returns:
            MessageInfo 对象
        """
        # 构建用户信息
        user_info_dict = message_info_dict.get("user_info")
        if not user_info_dict or not isinstance(user_info_dict, dict):
            raise ValueError("消息字典中 'user_info' 字段无效")
        user_id = user_info_dict.get("user_id")
        user_nickname = user_info_dict.get("user_nickname")
        user_cardname = user_info_dict.get("user_cardname")
        if not isinstance(user_id, str) or not isinstance(user_nickname, str) or not user_id or not user_nickname:
            raise ValueError("消息字典中 'user_info' 字段缺少有效的 'user_id' 或 'user_nickname'")
        user_cardname = str(user_cardname) if user_cardname is not None else None
        user_info = UserInfo(user_id=user_id, user_nickname=user_nickname, user_cardname=user_cardname)

        # 构建群信息
        if group_info_dict := message_info_dict.get("group_info"):
            group_id = group_info_dict.get("group_id")
            group_name = group_info_dict.get("group_name")
            if not isinstance(group_id, str) or not isinstance(group_name, str) or not group_id or not group_name:
                raise ValueError("消息字典中 'group_info' 字段缺少有效的 'group_id' 或 'group_name'")
            group_info = GroupInfo(group_id=group_id, group_name=group_name)
        else:
            group_info = None

        # 获取额外配置
        additional_config: Dict[str, Any] = message_info_dict.get("additional_config", {})

        return MessageInfo(user_info=user_info, group_info=group_info, additional_config=additional_config)

    def _build_session_message_from_dict(self, message_dict: Dict[str, Any]) -> SessionMessage:
        """
        从字典构建 SessionMessage 对象（递归处理消息组件）

        Args:
            message_dict: 包含消息完整信息的字典

        Returns:
            SessionMessage 对象
        """
        # 提取基本信息
        message_id = message_dict["message_id"]
        timestamp_str: str = message_dict.get("timestamp", "")
        platform = message_dict["platform"]
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("消息字典中缺少有效的 'message_id' 字段")
        if not isinstance(platform, str) or not platform:
            raise ValueError("消息字典中缺少有效的 'platform' 字段")

        # 解析时间戳
        try:
            timestamp_float = float(timestamp_str)
            timestamp = datetime.fromtimestamp(timestamp_float)
        except (ValueError, TypeError):
            timestamp = datetime.now()  # 如果解析失败，使用当前时间

        # 创建 SessionMessage 实例
        session_message = SessionMessage(message_id=message_id, timestamp=timestamp, platform=platform)

        # 构建消息信息
        session_message.message_info = self._build_message_info_from_dict(message_dict["message_info"])

        # 构建原始消息组件序列（复用 MessageSequence.from_dict 方法）
        raw_message_data = message_dict["raw_message"]
        if isinstance(raw_message_data, list):
            session_message.raw_message = MessageSequence.from_dict(raw_message_data)
        else:
            raise ValueError("消息字典中 'raw_message' 字段必须是一个列表")

        # 设置其他可选属性
        session_message.is_mentioned = message_dict.get("is_mentioned", False)
        if not isinstance(session_message.is_mentioned, bool):
            session_message.is_mentioned = False
        session_message.is_at = message_dict.get("is_at", False)
        if not isinstance(session_message.is_at, bool):
            session_message.is_at = False
        session_message.is_emoji = message_dict.get("is_emoji", False)
        if not isinstance(session_message.is_emoji, bool):
            session_message.is_emoji = False
        session_message.is_picture = message_dict.get("is_picture", False)
        if not isinstance(session_message.is_picture, bool):
            session_message.is_picture = False
        session_message.is_command = message_dict.get("is_command", False)
        if not isinstance(session_message.is_command, bool):
            session_message.is_command = False
        session_message.is_notify = message_dict.get("is_notify", False)
        if not isinstance(session_message.is_notify, bool):
            session_message.is_notify = False
        session_message.reply_to = message_dict.get("reply_to")
        if session_message.reply_to is not None and not isinstance(session_message.reply_to, str):
            session_message.reply_to = None
        session_message.processed_plain_text = message_dict.get("processed_plain_text")
        if session_message.processed_plain_text is not None and not isinstance(
            session_message.processed_plain_text, str
        ):
            session_message.processed_plain_text = None
        session_message.display_message = message_dict.get("display_message")
        if session_message.display_message is not None and not isinstance(session_message.display_message, str):
            session_message.display_message = None

        return session_message
