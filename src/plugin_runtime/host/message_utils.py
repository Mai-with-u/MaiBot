from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

import base64
import hashlib

from src.common.logger import get_logger
from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import UserInfo, GroupInfo, MessageInfo
from src.common.data_models.message_component_data_model import (
    AtComponent,
    DictComponent,
    EmojiComponent,
    ForwardComponent,
    ForwardNodeComponent,
    ImageComponent,
    MessageSequence,
    ReplyComponent,
    StandardMessageComponents,
    TextComponent,
    VoiceComponent,
)

logger = get_logger("plugin_runtime.host.message_utils")


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


class PluginMessageUtils:
    @staticmethod
    def _message_sequence_to_dict(message_sequence: MessageSequence) -> List[Dict[str, Any]]:
        """将消息组件序列转换为插件运行时使用的字典结构。

        Args:
            message_sequence: 待转换的消息组件序列。

        Returns:
            List[Dict[str, Any]]: 供插件运行时协议使用的消息段字典列表。
        """
        return [PluginMessageUtils._component_to_dict(component) for component in message_sequence.components]

    @staticmethod
    def _component_to_dict(component: StandardMessageComponents) -> Dict[str, Any]:
        """将单个消息组件转换为插件运行时字典结构。

        Args:
            component: 待转换的消息组件。

        Returns:
            Dict[str, Any]: 序列化后的消息组件字典。
        """
        if isinstance(component, TextComponent):
            return {"type": "text", "data": component.text}

        if isinstance(component, ImageComponent):
            serialized = {
                "type": "image",
                "data": component.content,
                "hash": component.binary_hash,
            }
            if component.binary_data:
                serialized["binary_data_base64"] = base64.b64encode(component.binary_data).decode("utf-8")
            return serialized

        if isinstance(component, EmojiComponent):
            serialized = {
                "type": "emoji",
                "data": component.content,
                "hash": component.binary_hash,
            }
            if component.binary_data:
                serialized["binary_data_base64"] = base64.b64encode(component.binary_data).decode("utf-8")
            return serialized

        if isinstance(component, VoiceComponent):
            serialized = {
                "type": "voice",
                "data": component.content,
                "hash": component.binary_hash,
            }
            if component.binary_data:
                serialized["binary_data_base64"] = base64.b64encode(component.binary_data).decode("utf-8")
            return serialized

        if isinstance(component, AtComponent):
            return {
                "type": "at",
                "data": {
                    "target_user_id": component.target_user_id,
                    "target_user_nickname": component.target_user_nickname,
                    "target_user_cardname": component.target_user_cardname,
                },
            }

        if isinstance(component, ReplyComponent):
            return {
                "type": "reply",
                "data": {
                    "target_message_id": component.target_message_id,
                    "target_message_content": component.target_message_content,
                    "target_message_sender_id": component.target_message_sender_id,
                    "target_message_sender_nickname": component.target_message_sender_nickname,
                    "target_message_sender_cardname": component.target_message_sender_cardname,
                },
            }

        if isinstance(component, ForwardNodeComponent):
            return {
                "type": "forward",
                "data": [PluginMessageUtils._forward_component_to_dict(item) for item in component.forward_components],
            }

        return {"type": "dict", "data": component.data}

    @staticmethod
    def _forward_component_to_dict(component: ForwardComponent) -> Dict[str, Any]:
        """将单个转发节点组件转换为字典结构。

        Args:
            component: 待转换的转发节点组件。

        Returns:
            Dict[str, Any]: 序列化后的转发节点字典。
        """
        return {
            "user_id": component.user_id,
            "user_nickname": component.user_nickname,
            "user_cardname": component.user_cardname,
            "message_id": component.message_id,
            "content": [PluginMessageUtils._component_to_dict(item) for item in component.content],
        }

    @staticmethod
    def _message_sequence_from_dict(raw_message_data: List[Dict[str, Any]]) -> MessageSequence:
        """从插件运行时字典结构恢复消息组件序列。

        Args:
            raw_message_data: 插件运行时消息段字典列表。

        Returns:
            MessageSequence: 恢复后的消息组件序列。
        """
        components = [PluginMessageUtils._component_from_dict(item) for item in raw_message_data]
        return MessageSequence(components=components)

    @staticmethod
    def _component_from_dict(item: Dict[str, Any]) -> StandardMessageComponents:
        """从插件运行时字典结构恢复单个消息组件。

        Args:
            item: 单个消息组件的字典表示。

        Returns:
            StandardMessageComponents: 恢复后的内部消息组件对象。
        """
        item_type = str(item.get("type") or "").strip()
        if item_type == "text":
            return TextComponent(text=str(item.get("data") or ""))

        if item_type == "image":
            return PluginMessageUtils._build_binary_component(ImageComponent, item)

        if item_type == "emoji":
            return PluginMessageUtils._build_binary_component(EmojiComponent, item)

        if item_type == "voice":
            return PluginMessageUtils._build_binary_component(VoiceComponent, item)

        if item_type == "at":
            item_data = item.get("data", {})
            if not isinstance(item_data, dict):
                item_data = {}
            return AtComponent(
                target_user_id=str(item_data.get("target_user_id") or ""),
                target_user_nickname=PluginMessageUtils._normalize_optional_string(item_data.get("target_user_nickname")),
                target_user_cardname=PluginMessageUtils._normalize_optional_string(item_data.get("target_user_cardname")),
            )

        if item_type == "reply":
            reply_data = item.get("data")
            if isinstance(reply_data, dict):
                return ReplyComponent(
                    target_message_id=str(reply_data.get("target_message_id") or ""),
                    target_message_content=PluginMessageUtils._normalize_optional_string(
                        reply_data.get("target_message_content")
                    ),
                    target_message_sender_id=PluginMessageUtils._normalize_optional_string(
                        reply_data.get("target_message_sender_id")
                    ),
                    target_message_sender_nickname=PluginMessageUtils._normalize_optional_string(
                        reply_data.get("target_message_sender_nickname")
                    ),
                    target_message_sender_cardname=PluginMessageUtils._normalize_optional_string(
                        reply_data.get("target_message_sender_cardname")
                    ),
                )
            return ReplyComponent(target_message_id=str(reply_data or ""))

        if item_type == "forward":
            forward_nodes: List[ForwardComponent] = []
            raw_forward_nodes = item.get("data", [])
            if isinstance(raw_forward_nodes, list):
                for node in raw_forward_nodes:
                    if not isinstance(node, dict):
                        continue
                    raw_content = node.get("content", [])
                    node_components: List[StandardMessageComponents] = []
                    if isinstance(raw_content, list):
                        node_components = [
                            PluginMessageUtils._component_from_dict(content)
                            for content in raw_content
                            if isinstance(content, dict)
                        ]
                    if not node_components:
                        node_components = [TextComponent(text="[empty forward node]")]
                    forward_nodes.append(
                        ForwardComponent(
                            user_nickname=str(node.get("user_nickname") or "未知用户"),
                            user_id=PluginMessageUtils._normalize_optional_string(node.get("user_id")),
                            user_cardname=PluginMessageUtils._normalize_optional_string(node.get("user_cardname")),
                            message_id=str(node.get("message_id") or ""),
                            content=node_components,
                        )
                    )
            if not forward_nodes:
                return DictComponent(data={"type": "forward", "data": item.get("data", [])})
            return ForwardNodeComponent(forward_components=forward_nodes)

        component_data = item.get("data")
        if isinstance(component_data, dict):
            return DictComponent(data=component_data)
        return DictComponent(data=item)

    @staticmethod
    def _build_binary_component(component_cls: Any, item: Dict[str, Any]) -> StandardMessageComponents:
        """从字典构造带二进制负载的消息组件。

        Args:
            component_cls: 目标组件类型。
            item: 消息组件字典。

        Returns:
            StandardMessageComponents: 构造后的组件对象。
        """
        content = str(item.get("data") or "")
        binary_hash = str(item.get("hash") or "")
        raw_binary_base64 = item.get("binary_data_base64")
        binary_data = b""
        if isinstance(raw_binary_base64, str) and raw_binary_base64:
            try:
                binary_data = base64.b64decode(raw_binary_base64)
            except Exception:
                binary_data = b""

        if not binary_hash and binary_data:
            binary_hash = hashlib.sha256(binary_data).hexdigest()

        return component_cls(binary_hash=binary_hash, content=content, binary_data=binary_data)

    @staticmethod
    def _normalize_optional_string(value: Any) -> Optional[str]:
        """将任意值规范化为可选字符串。

        Args:
            value: 待规范化的值。

        Returns:
            Optional[str]: 规范化后的字符串；若值为空则返回 ``None``。
        """
        if value is None:
            return None
        normalized_value = str(value)
        return normalized_value if normalized_value else None

    @staticmethod
    def _message_info_to_dict(message_info: MessageInfo) -> MessageInfoDict:
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

    @staticmethod
    def _session_message_to_dict(session_message: SessionMessage) -> MessageDict:
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
            message_info=PluginMessageUtils._message_info_to_dict(session_message.message_info),
            raw_message=PluginMessageUtils._message_sequence_to_dict(session_message.raw_message),
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

    @staticmethod
    def _build_message_info_from_dict(message_info_dict: Dict[str, Any]) -> MessageInfo:
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

    @staticmethod
    def _build_session_message_from_dict(message_dict: Dict[str, Any]) -> SessionMessage:
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
        session_message.message_info = PluginMessageUtils._build_message_info_from_dict(message_dict["message_info"])

        # 构建原始消息组件序列（复用 MessageSequence.from_dict 方法）
        raw_message_data = message_dict["raw_message"]
        if isinstance(raw_message_data, list):
            session_message.raw_message = PluginMessageUtils._message_sequence_from_dict(raw_message_data)
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
        session_message.session_id = message_dict.get("session_id", "")
        if not isinstance(session_message.session_id, str):
            session_message.session_id = ""
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
