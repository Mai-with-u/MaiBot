from maim_message import MessageBase, Seg
from typing import List, Tuple, Optional, Dict, TYPE_CHECKING, Callable
from datetime import datetime

import base64
import hashlib
import msgpack
import random
import re

from sqlmodel import select, col

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
from src.common.logger import get_logger
from src.config.config import global_config

from .math_utils import number_to_short_id, TimestampMode, translate_timestamp_to_human_readable
from .system_utils import is_bot_self

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage

logger = get_logger("message_utils")


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
        """存储消息到数据库，此方法没有update机制"""
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
        timestamp_mode: Optional[TimestampMode | str] = None,
        show_message_id_prefix: bool = False,
        read_mark_time: Optional[float] = None,
        truncate_message: bool = False,
        truncate_func: Optional[Callable[[float], Tuple[Optional[int], str]]] = None,
        show_actions: bool = False,
    ) -> Tuple[str, Dict[str, Tuple[str, str]], List[str]]:
        """
        将消息构建为LLM可读的文本格式

        Args:
            messages (List[SessionMessage]): 消息列表
            anonymize (bool): 是否匿名化用户信息
            show_lineno (bool): 是否在每条消息前显示行号
            extract_pictures (bool): 是否提取图片信息并在文本中显示占位符
            replace_bot_name (bool): 是否将消息中的机器人名称替换为统一的占位符
            target_bot_name (Optional[str]): 如果replace_bot_name为True，指定要替换的机器人名称，比如可以把机器人名称替换为“你”
            timestamp_mode (Optional[TimestampMode]): 时间戳显示模式，默认为None表示不显示时间戳
            show_message_id_prefix (bool): 是否在每条消息前显示消息ID前缀
            truncate_message (bool): 是否启用消息文本截断功能，截断过长的消息文本
            truncate_func (Optional[Callable[[float], Tuple[Optional[int], str]]]) 截断函数，接受消息的百分位位置(0-1)，返回一个元组(文本长度限制(可为None表不切割), 替换内容)
            show_actions (bool): 是否显示Action组件内容
        Returns:
            return (Tuple[str, Dict[str, Tuple[str, str]], List[str]]): 构建后的消息文本，映射表 {用户ID: (匿名ID, 原始名称)}，消息编号列表
        """
        msg_list: List["SessionMessage"] = messages
        user_id_mapping: Dict[str, Tuple[str, str]] = {}  # user_id -> (匿名ID, 原始名称)
        message_ids: List[str] = []  # 存储消息编号的列表
        copied: bool = False  # 标记是否已经复制过消息列表，避免不必要的复制开销
        img_map: Optional[Dict[str, Tuple[int, str]]] = None
        emoji_map: Optional[Dict[str, Tuple[int, str]]] = None
        if replace_bot_name and not target_bot_name:
            raise ValueError("当replace_bot_name为True时，必须指定target_bot_name参数")

        # 匿名化和机器人名称处理
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

        # 将图片提取到内容最前面
        if extract_pictures:
            img_map = {}  # binary_hash -> (图片ID, 描述信息)
            emoji_map = {}  # binary_hash -> (表情ID, 描述信息)
            msg_list = [
                MessageUtils._extract_pictures_from_message(msg, img_map, emoji_map, copied) for msg in msg_list
            ]
            processed_plain_texts.append("图片信息和表情信息：")
            processed_plain_texts.extend(f"[图片{img_id}: {desc}]" for img_id, desc in img_map.values())
            processed_plain_texts.append("")  # 图片和表情之间添加一个换行，避免连在一起
            processed_plain_texts.extend(f"[表情包{emoji_id}: {desc}]" for emoji_id, desc in emoji_map.values())
            processed_plain_texts.extend(("", "聊天记录信息："))

        # 获取动作记录文本列表
        action_messages: List[Tuple[float, str]] = []
        if show_actions and messages:
            min_time = msg_list[0].timestamp.timestamp()
            max_time = msg_list[-1].timestamp.timestamp()
            session_id = msg_list[0].session_id
            action_messages = MessageUtils._generate_action_readable(min_time, max_time, session_id)

        msg_count = len(msg_list)
        read_mark_added_flag: bool = False  # 标记是否已经添加过已读标签，确保只添加一次
        action_idx: int = 0  # 动作记录的索引，用于双指针遍历

        for i, msg in enumerate(msg_list):
            await msg.process()
            plain_text: str = msg.processed_plain_text  # type: ignore
            msg_time = msg.timestamp.timestamp()

            # 使用双指针插入动作记录
            while action_idx < len(action_messages) and action_messages[action_idx][0] <= msg_time:
                processed_plain_texts.append(
                    MessageUtils._build_action_str_single(action_messages[action_idx], timestamp_mode)
                )
                action_idx += 1

            if truncate_message:  # 消息截断逻辑
                percentile = i / msg_count
                if not read_mark_time:  # 没有已读标签
                    plain_text = MessageUtils._truncate_message(
                        percentile,
                        plain_text,
                        truncate_func or MessageUtils._default_truncate_func,
                    )
                elif msg.timestamp.timestamp() < read_mark_time:
                    plain_text = MessageUtils._truncate_message(
                        percentile,
                        plain_text,
                        truncate_func or MessageUtils._default_truncate_func,
                    )
                elif not read_mark_added_flag:
                    read_mark_added_flag = True
                    processed_plain_texts.append("\n--- 以上消息是你已经看过，请关注以下未读的新消息---\n")
            header, message_id = MessageUtils._build_line_header(
                msg,
                show_message_id_prefix,
                i + 1,
                show_lineno,
                timestamp_mode,
            )
            if message_id is not None:
                message_ids.append(message_id)
            processed_plain_texts.append("".join([header, plain_text]))

        # 处理剩余的动作记录（时间在最后一条消息之后的动作）
        while action_idx < len(action_messages):
            processed_plain_texts.append(
                MessageUtils._build_action_str_single(action_messages[action_idx], timestamp_mode)
            )
            action_idx += 1

        return "\n".join(processed_plain_texts), user_id_mapping, message_ids

    @staticmethod
    def _build_line_header(
        message: "SessionMessage",
        show_message_id_prefix: bool,
        counter: int,
        show_lineno: bool,
        timestamp_mode: Optional[str | TimestampMode] = None,
    ) -> Tuple[str, Optional[str]]:
        usr_info = message.message_info.user_info
        usr_name = usr_info.user_cardname or usr_info.user_nickname or "未知用户"
        header_parts = [f"{usr_name}说："]
        message_id = None
        if show_message_id_prefix:
            rand_id = f"{counter}{random.randint(10, 99)}"
            message_id = f"m{rand_id}"
            header_parts.insert(0, f"[消息ID: {message_id}]")
        if timestamp_mode:
            timestamp_str = translate_timestamp_to_human_readable(message.timestamp.timestamp(), mode=timestamp_mode)
            header_parts.insert(0, f"[{timestamp_str}]")
        if show_lineno:
            header_parts.insert(0, f"[{counter}]")
        return " ".join(header_parts), message_id

    @staticmethod
    def _truncate_message(
        percentile: float, original_content: str, truncate_func: Callable[[float], Tuple[Optional[int], str]]
    ):
        limit, replacement = truncate_func(percentile)
        if limit:
            return f"{original_content[:limit]}{replacement}"
        else:
            return original_content

    @staticmethod
    def _default_truncate_func(percentile: float) -> Tuple[int, str]:
        """默认的截断函数，根据消息在消息列表中的位置返回不同的截断长度和替换内容"""
        if percentile < 0.3:
            return 400, "......（内容太长了）"
        elif percentile < 0.5:
            return 200, "......（内容太长了）"
        elif percentile < 0.8:
            return 100, "......（有点记不清了）"
        else:
            return 50, "......（记不清了）"

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
        platform = message.platform
        new_component_list = [
            MessageUtils._process_msg_component(
                component,
                anonymize_mapping,
                salt,
                platform,
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
        if replace_bot_name and target_bot_name and is_bot_self(platform, msg_usr_info.user_id):
            new_message.message_info.user_info.user_nickname = target_bot_name
            new_message.message_info.user_info.user_cardname = target_bot_name
        return new_message

    @staticmethod
    def _process_msg_component(
        component: StandardMessageComponents,
        anonymize_mapping: Dict[str, Tuple[str, str]],
        salt: str,
        platform: str,
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
                platform,
                anonymize,
                replace_bot_name,
                target_bot_name,
            )
        elif isinstance(component, ReplyComponent):
            return MessageUtils.__handle_reply_component(
                component,
                anonymize_mapping,
                salt,
                platform,
                anonymize,
                replace_bot_name,
                target_bot_name,
            )
        elif isinstance(component, ForwardNodeComponent):
            return MessageUtils.__handle_forward_node_component(
                component,
                anonymize_mapping,
                salt,
                platform,
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
        platform: str,
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
        if replace_bot_name and target_bot_name and is_bot_self(platform, user_id):
            component.target_user_nickname = target_bot_name
            component.target_user_cardname = target_bot_name
        return component

    @staticmethod
    def __handle_forward_node_component(
        component: ForwardNodeComponent,
        anonymize_mapping: Dict[str, Tuple[str, str]],
        salt: str,
        platform: str,
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
            if replace_bot_name and target_bot_name and is_bot_self(platform, user_id):
                comp.user_nickname = target_bot_name
                comp.user_cardname = target_bot_name
            comp.content = [  # 递归处理转发消息中的组件
                MessageUtils._process_msg_component(
                    c,
                    anonymize_mapping,
                    salt,
                    platform,
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
        platform: str,
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
            if replace_bot_name and target_bot_name and is_bot_self(platform, user_id):
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

    @staticmethod
    def _generate_action_readable(min_time: float, max_time: float, session_id: str) -> List[Tuple[float, str]]:
        """
        获取消息时间范围内的动作记录，并构建动作文本列表

        Args:
            messages: 消息列表，用于确定时间范围和session_id
            timestamp_mode: 时间戳显示模式，默认为None表示不显示时间戳

        Returns:
            List[Tuple[float, str]]: 按时间排序的动作文本列表，每个元素为 (timestamp, action_text)
        """
        from src.common.database.database import get_db_session
        from src.common.database.database_model import ActionRecord

        # 获取这个时间范围内的动作记录，并匹配session_id
        try:
            with get_db_session() as session:
                actions_in_range = session.exec(
                    select(ActionRecord)
                    .where(col(ActionRecord.timestamp) >= datetime.fromtimestamp(min_time))
                    .where(col(ActionRecord.timestamp) <= datetime.fromtimestamp(max_time))
                    .where(col(ActionRecord.session_id) == session_id)
                    .order_by(col(ActionRecord.timestamp))
                ).all()

            # 获取最新消息之后的第一个动作记录
            with get_db_session() as session:
                action_after_latest = session.exec(
                    select(ActionRecord)
                    .where(col(ActionRecord.timestamp) > datetime.fromtimestamp(max_time))
                    .where(col(ActionRecord.session_id) == session_id)
                    .order_by(col(ActionRecord.timestamp))
                    .limit(1)
                ).all()
        except Exception as e:
            logger.error(f"查询动作记录失败: {e}")
            return []

        # 合并两部分动作记录
        actions = list(actions_in_range) + list(action_after_latest)

        # 构建动作文本列表
        action_messages: List[Tuple[float, str]] = []
        for action in actions:
            if action_display_prompt := action.action_display_prompt or "":
                action_time = action.timestamp.timestamp()
                action_messages.append((action_time, action_display_prompt))

        return action_messages

    @staticmethod
    def _build_action_str_single(
        action_content: Tuple[float, str], timestamp_mode: Optional[str | TimestampMode] = None
    ) -> str:
        action_time, action_text = action_content
        action_header = "你执行了: "
        if timestamp_mode:
            timestamp_str = translate_timestamp_to_human_readable(action_time, mode=timestamp_mode)
            action_header = f"[{timestamp_str}] {action_header}"
        return f"{action_header}{action_text}"
