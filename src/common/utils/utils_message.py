from maim_message import MessageBase, Seg
from typing import List, Tuple, Optional, Dict, TYPE_CHECKING

import base64
import hashlib
import msgpack
import random
import re

from src.common.data_models.message_component_data_model import (
    MessageSequence,
    StandardMessageComponents,
    TextComponent,
    ImageComponent,
    EmojiComponent,
    VoiceComponent,
    AtComponent,
    ReplyComponent,
    DictComponent,
    UnknownUser,
    ForwardNodeComponent,
)
from src.config.config import global_config

from .math_utils import number_to_short_id

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage


class MessageUtils:
    @staticmethod
    def from_db_record_msg_to_MaiSeq(raw_content: bytes) -> MessageSequence:
        unpacked_data = msgpack.unpackb(raw_content)
        return MessageSequence.from_dict(unpacked_data)

    @staticmethod
    def from_MaiSeq_to_db_record_msg(msg: MessageSequence) -> bytes:
        dict_representation = msg.to_dict()
        return msgpack.packb(dict_representation)  # type: ignore

    @staticmethod
    def from_maim_message_segments_to_MaiSeq(message: "MessageBase") -> MessageSequence:
        """从maim_message.MessageBase.message_segment转换为MessageSequence"""
        raw_msg_seq = message.message_segment
        components: List[StandardMessageComponents] = []
        if not raw_msg_seq:
            return MessageSequence(components)
        if raw_msg_seq.type == "seglist":
            assert isinstance(raw_msg_seq.data, list), "seglist类型的message_segment数据应该是一个列表"
            components.extend(MessageUtils._parse_maim_message_segment_to_component(item) for item in raw_msg_seq.data)
        elif raw_msg_seq.type in {"text", "image", "emoji", "voice", "at", "reply"}:
            components.append(MessageUtils._parse_maim_message_segment_to_component(raw_msg_seq))
        else:
            raise NotImplementedError(f"暂时不支持的消息片段类型: {raw_msg_seq.type}")
        return MessageSequence(components)

    @staticmethod
    async def from_MaiSeq_to_maim_message_segments(msg_seq: MessageSequence) -> List[Seg]:
        """从MessageSequence转换为maim_message.MessageBase.message_segment格式的列表"""
        segments = []
        for component in msg_seq.components:
            if isinstance(component, DictComponent):
                seg = Seg(type="dict", data=component.data)  # type: ignore
            else:
                seg = await component.to_seg()
            segments.append(seg)
        return segments

    @staticmethod
    def _parse_maim_message_segment_to_component(seg: Seg) -> "StandardMessageComponents":
        if seg.type == "text":
            assert isinstance(seg.data, str), "text类型的seg数据应该是字符串"
            return TextComponent(text=seg.data)
        elif seg.type == "image":
            assert isinstance(seg.data, str), "image类型的seg数据应该是base64字符串"
            image_bytes = base64.b64decode(seg.data)
            binary_hash = hashlib.md5(image_bytes).hexdigest()
            return ImageComponent(binary_hash=binary_hash, binary_data=image_bytes)
        elif seg.type == "emoji":
            assert isinstance(seg.data, str), "emoji类型的seg数据应该是base64字符串"
            emoji_bytes = base64.b64decode(seg.data)
            binary_hash = hashlib.md5(emoji_bytes).hexdigest()
            return EmojiComponent(binary_hash=binary_hash, binary_data=emoji_bytes)
        elif seg.type == "voice":
            assert isinstance(seg.data, str), "voice类型的seg数据应该是base64字符串"
            voice_bytes = base64.b64decode(seg.data)
            binary_hash = hashlib.md5(voice_bytes).hexdigest()
            return VoiceComponent(binary_hash=binary_hash, binary_data=voice_bytes)
        elif seg.type == "at":
            assert isinstance(seg.data, str), "at类型的seg数据应该是字符串"
            return AtComponent(target_user_id=seg.data)
        elif seg.type == "reply":
            assert isinstance(seg.data, str), "reply类型的seg数据应该是字符串"
            return ReplyComponent(target_message_id=seg.data)
        else:
            raise NotImplementedError(f"暂时不支持的消息片段类型: {seg.type}")

    @staticmethod
    def check_ban_words(text: str) -> Tuple[bool, Optional[str]]:
        """检查消息是否包含过滤词

        Args:
            text: 待检查的文本

        Returns:
            bool: 是否包含过滤词
        """
        if not text:
            return False, None
        return next(
            ((True, word) for word in global_config.message_receive.ban_words if word in text),
            (False, None),
        )

    @staticmethod
    def check_ban_regex(text: str) -> Tuple[bool, Optional[str]]:
        """检查消息是否匹配过滤正则表达式

        Args:
            text: 待检查的文本
            chat: 聊天对象
            userinfo: 用户信息

        Returns:
            bool: 是否匹配过滤正则
        """
        # 检查text是否为None或空字符串
        if not text:
            return False, None
        return next(
            ((True, pattern) for pattern in global_config.message_receive.ban_msgs_regex if re.search(pattern, text)),
            (False, None),
        )

    @staticmethod
    def store_message_to_db(message: "SessionMessage"):
        """存储消息到数据库"""
        from src.common.database.database import get_db_session

        with get_db_session() as session:
            db_message = message.to_db_instance()
            session.add(db_message)

    @staticmethod
    async def build_readable_message(
        messages: List["SessionMessage"],
        *,
        anonymize: bool = False,
        show_lineno: bool = False,
        extract_pictures: bool = False,
        replace_bot_name: bool = False,
        target_bot_name: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Tuple[str, str]]]:
        """
        将消息构建为LLM可读的文本格式

        Args:
            messages (List[SessionMessage]): 消息列表
            anonymize (bool): 是否匿名化用户信息
            show_lineno (bool): 是否在每条消息前显示行号
            extract_pictures (bool): 是否提取图片信息并在文本中显示占位符
            replace_bot_name (bool): 是否将消息中的机器人名称替换为统一的占位符
            target_bot_name (Optional[str]): 如果replace_bot_name为True，指定要替换的机器人名称
        Returns:
            return (Tuple[str, Dict[str, Tuple[str, str]]]): 构建后的消息文本，以及映射表（匿名ID, 原始名称）
        """
        msg_list: List["SessionMessage"] = messages
        user_id_mapping: Dict[str, Tuple[str, str]] = {}  # user_id -> (匿名ID, 原始名称)
        copied: bool = False  # 标记是否已经复制过消息列表，避免不必要的复制开销
        img_map: Optional[Dict[str, Tuple[int, str]]] = None
        emoji_map: Optional[Dict[str, Tuple[int, str]]] = None
        if replace_bot_name and not target_bot_name:
            raise ValueError("当replace_bot_name为True时，必须指定target_bot_name参数")
        if anonymize or replace_bot_name:
            user_id_mapping = {}  # 利用弱引用直接传入并得到修改结果
            anonymous_messages: List["SessionMessage"] = []
            salt_str = str(random.randint(100000, 999999))  # 每次调用生成一个随机盐，确保匿名ID不可预测
            anonymous_messages.extend(
                MessageUtils._process_usr_info(
                    msg,
                    user_id_mapping,
                    salt_str,
                    anonymize,
                    replace_bot_name,
                    target_bot_name,
                )
                for msg in messages
            )
            msg_list = anonymous_messages
            copied = True

        processed_plain_texts: List[str] = []
        if extract_pictures:
            img_map = {}  # binary_hash -> (图片ID, 描述信息)
            emoji_map = {}  # binary_hash -> (表情ID, 描述信息)
            msg_list = [
                MessageUtils._extract_pictures_from_message(msg, img_map, emoji_map, copied) for msg in msg_list
            ]
            processed_plain_texts.extend(f"[图片{img_id}: {desc}]" for img_id, desc in img_map.values())
            processed_plain_texts.append("")  # 图片和表情之间添加一个换行，避免连在一起
            processed_plain_texts.extend(f"[表情{emoji_id}: {desc}]" for emoji_id, desc in emoji_map.values())
            processed_plain_texts.append("")  # 表情和消息文本之间添加两个换行，避免连在一起

        lineno_counter = 1
        for msg in msg_list:
            await msg.process()
            plain_text: str = msg.processed_plain_text  # type: ignore
            usr_info = msg.message_info.user_info
            usr_name = usr_info.user_cardname or usr_info.user_nickname or "未知用户"
            header = f"[{lineno_counter}] {usr_name}说：" if show_lineno else f"{usr_name}说："
            lineno_counter += 1
            processed_plain_texts.append("".join([header, plain_text]))

        return "\n".join(processed_plain_texts), user_id_mapping

    @staticmethod
    def _process_usr_info(
        message: "SessionMessage",
        anonymize_mapping: Dict[str, Tuple[str, str]],
        salt: str,
        anonymize: bool,
        replace_bot_name: bool,
        target_bot_name: Optional[str] = None,
    ):
        """处理消息中的用户信息，进行匿名化显示"""
        new_message = message.deepcopy()
        new_component_list = [
            MessageUtils._process_msg_component(
                component,
                anonymize_mapping,
                salt,
                anonymize,
                replace_bot_name,
                target_bot_name,
            )
            for component in new_message.raw_message.components
        ]
        new_message.raw_message.components = new_component_list
        msg_usr_info = message.message_info.user_info
        if anonymize:
            if msg_usr_info.user_id not in anonymize_mapping:
                num = len(anonymize_mapping) + 1
                anonymous_id = number_to_short_id(num, salt, length=6)
                original_name = msg_usr_info.user_cardname or msg_usr_info.user_nickname or msg_usr_info.user_id
                anonymize_mapping[msg_usr_info.user_id] = (anonymous_id, original_name)
            anonymous_name = anonymize_mapping[msg_usr_info.user_id][0]
            new_message.message_info.user_info.user_nickname = anonymous_name
            new_message.message_info.user_info.user_cardname = anonymous_name
        if replace_bot_name and target_bot_name and is_bot_self(msg_usr_info.user_id):
            new_message.message_info.user_info.user_nickname = target_bot_name
            new_message.message_info.user_info.user_cardname = target_bot_name
        return new_message

    @staticmethod
    def _process_msg_component(
        component: StandardMessageComponents,
        anonymize_mapping: Dict[str, Tuple[str, str]],
        salt: str,
        anonymize: bool,
        replace_bot_name: bool,
        target_bot_name: Optional[str] = None,
    ) -> StandardMessageComponents:
        """将消息组件中的用户信息匿名化"""
        if isinstance(component, AtComponent):
            return MessageUtils.__handle_at_component(
                component,
                anonymize_mapping,
                salt,
                anonymize,
                replace_bot_name,
                target_bot_name,
            )
        elif isinstance(component, ReplyComponent):
            return MessageUtils.__handle_reply_component(
                component,
                anonymize_mapping,
                salt,
                anonymize,
                replace_bot_name,
                target_bot_name,
            )
        elif isinstance(component, ForwardNodeComponent):
            return MessageUtils.__handle_forward_node_component(
                component,
                anonymize_mapping,
                salt,
                anonymize,
                replace_bot_name,
                target_bot_name,
            )
        return component

    @staticmethod
    def __handle_at_component(
        component: AtComponent,
        anonymize_mapping: Dict[str, Tuple[str, str]],
        salt: str,
        anonymize: bool,
        replace_bot_name: bool,
        target_bot_name: Optional[str] = None,
    ):
        user_id = component.target_user_id  # user_id一定存在
        if anonymize:
            if user_id not in anonymize_mapping:
                # 新人物? 编号 + 1，生成一个新的匿名ID
                num = len(anonymize_mapping) + 1
                anonymous_id = number_to_short_id(num, salt, length=6)
                original_name = component.target_user_cardname or component.target_user_nickname or user_id
                anonymize_mapping[user_id] = (anonymous_id, original_name)
            # 替换昵称和备注为匿名ID
            anonymous_name = anonymize_mapping[user_id][0]
            component.target_user_nickname = anonymous_name
            component.target_user_cardname = anonymous_name
        if replace_bot_name and target_bot_name and is_bot_self(user_id):
            component.target_user_nickname = target_bot_name
            component.target_user_cardname = target_bot_name
        return component

    @staticmethod
    def __handle_forward_node_component(
        component: ForwardNodeComponent,
        anonymize_mapping: Dict[str, Tuple[str, str]],
        salt: str,
        anonymize: bool,
        replace_bot_name: bool,
        target_bot_name: Optional[str] = None,
    ):
        for comp in component.forward_components:
            user_id = comp.user_id
            if not user_id:  # 如果转发节点的用户ID不存在，直接设置为未知用户
                comp.user_id = "unknown_user"
                comp.user_cardname = "未知用户"
                comp.user_nickname = "未知用户"
                continue
            if isinstance(user_id, UnknownUser):  # 如果用户ID是UnknownUser类型，直接设置为未知用户
                comp.user_id = "unknown_user"
                comp.user_cardname = "未知用户"
                comp.user_nickname = "未知用户"
                continue
            if anonymize:
                if user_id not in anonymize_mapping:
                    num = len(anonymize_mapping) + 1
                    anonymous_id = number_to_short_id(num, salt, length=6)
                    original_name = comp.user_cardname or comp.user_nickname or user_id
                    anonymize_mapping[user_id] = (anonymous_id, original_name)
                anonymous_name = anonymize_mapping[user_id][0]
                comp.user_nickname = anonymous_name
                comp.user_cardname = anonymous_name
            if replace_bot_name and target_bot_name and is_bot_self(user_id):
                comp.user_nickname = target_bot_name
                comp.user_cardname = target_bot_name
            comp.content = [  # 递归处理转发消息中的组件
                MessageUtils._process_msg_component(
                    c,
                    anonymize_mapping,
                    salt,
                    anonymize,
                    replace_bot_name,
                    target_bot_name,
                )
                for c in comp.content
            ]
        return component

    @staticmethod
    def __handle_reply_component(
        component: ReplyComponent,
        anonymize_mapping: Dict[str, Tuple[str, str]],
        salt: str,
        anonymize: bool,
        replace_bot_name: bool,
        target_bot_name: Optional[str] = None,
    ):
        if user_id := component.target_message_sender_id:
            if anonymize:
                if user_id not in anonymize_mapping:
                    num = len(anonymize_mapping) + 1
                    anonymous_id = number_to_short_id(num, salt, length=6)
                    original_name = (
                        component.target_message_sender_cardname or component.target_message_sender_nickname or user_id
                    )
                    anonymize_mapping[user_id] = (anonymous_id, original_name)
                anonymous_name = anonymize_mapping[user_id][0]
                component.target_message_sender_nickname = anonymous_name
                component.target_message_sender_cardname = anonymous_name
            if replace_bot_name and target_bot_name and is_bot_self(user_id):
                component.target_message_sender_nickname = target_bot_name
                component.target_message_sender_cardname = target_bot_name
        else:
            component.target_message_sender_nickname = "未知用户"  # 如果没有Reply消息的发送者ID，直接设置为未知用户
            component.target_message_sender_cardname = "未知用户"
        return component

    @staticmethod
    def _extract_pictures_from_message(
        message: "SessionMessage",
        img_map: Dict[str, Tuple[int, str]],
        emoji_map: Dict[str, Tuple[int, str]],
        copied: bool,
    ):
        """从消息中提取图片组件，返回列表包含(图片ID, 描述信息)"""
        if not copied:
            message = message.deepcopy()  # 避免修改原消息
        new_component_list: List[StandardMessageComponents] = []
        new_component_list.extend(
            MessageUtils._extract_pictures_from_component(component, img_map, emoji_map)
            for component in message.raw_message.components
        )
        message.raw_message.components = new_component_list
        return message

    @staticmethod
    def _extract_pictures_from_component(
        component: StandardMessageComponents,
        img_map: Dict[str, Tuple[int, str]],
        emoji_map: Dict[str, Tuple[int, str]],
    ) -> StandardMessageComponents:
        """从消息组件中提取图片信息"""
        if isinstance(component, ImageComponent):
            if component.binary_hash in img_map:
                img_id, _ = img_map[component.binary_hash]
            else:
                img_id = len(img_map) + 1
                img_map[component.binary_hash] = (img_id, component.content)
            component.content = f"图片{img_id}"
        elif isinstance(component, EmojiComponent):
            if component.binary_hash in emoji_map:
                emoji_id, _ = emoji_map[component.binary_hash]
            else:
                emoji_id = len(emoji_map) + 1
                emoji_map[component.binary_hash] = (emoji_id, component.content)
            component.content = f"表情{emoji_id}"
        elif isinstance(component, ForwardNodeComponent):
            for comp in component.forward_components:
                comp.content = [
                    MessageUtils._extract_pictures_from_component(c, img_map, emoji_map) for c in comp.content
                ]
        return component


# TODO: 这个函数的实现非常临时，后续需要替换为更完善的实现，比如直接从配置文件中读取机器人自己的ID，或者通过API获取机器人自己的信息等
def is_bot_self(user_id: str) -> bool:
    """
    判断用户ID是否是机器人自己

    临时方法，后续会替换为更完善的实现
    """
    return user_id == "bot_self"
