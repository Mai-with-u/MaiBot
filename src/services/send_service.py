"""
发送服务模块

提供发送各种类型消息的核心功能。
"""

import traceback
import time
from typing import Optional, Union, Dict, List, TYPE_CHECKING, Tuple

from maim_message import MessageBase, BaseMessageInfo, Seg

from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.chat.message_receive.message import MessageSending
from src.chat.message_receive.uni_message_sender import UniversalMessageSender
from src.common.data_models.mai_message_data_model import MaiMessage, UserInfo
from src.common.data_models.message_data_model import ReplyContentType
from src.common.logger import get_logger
from src.config.config import global_config

if TYPE_CHECKING:
    from src.common.data_models.database_data_model import DatabaseMessages
    from src.common.data_models.message_data_model import ForwardNode, ReplyContent, ReplySetModel

logger = get_logger("send_service")


# =============================================================================
# 内部实现函数
# =============================================================================


async def _send_to_target(
    message_segment: Seg,
    stream_id: str,
    display_message: str = "",
    typing: bool = False,
    set_reply: bool = False,
    reply_message: Optional["DatabaseMessages"] = None,
    storage_message: bool = True,
    show_log: bool = True,
    selected_expressions: Optional[List[int]] = None,
) -> bool:
    """向指定目标发送消息的内部实现"""
    try:
        if set_reply and not reply_message:
            logger.warning("[SendService] 使用引用回复，但未提供回复消息")
            return False

        if show_log:
            logger.debug(f"[SendService] 发送{message_segment.type}消息到 {stream_id}")

        target_stream = _chat_manager.get_session_by_session_id(stream_id)
        if not target_stream:
            logger.error(f"[SendService] 未找到聊天流: {stream_id}")
            return False

        message_sender = UniversalMessageSender()

        current_time = time.time()
        message_id = f"send_api_{int(current_time * 1000)}"

        bot_user_info = UserInfo(
            user_id=global_config.bot.qq_account,
            user_nickname=global_config.bot.nickname,
        )

        reply_to_platform_id = ""
        anchor_message: Optional[MaiMessage] = None
        if reply_message:
            anchor_message = db_message_to_mai_message(reply_message)
            if anchor_message:
                logger.debug(f"[SendService] 找到匹配的回复消息，发送者: {anchor_message.message_info.user_info.user_id}")
                reply_to_platform_id = (
                    f"{anchor_message.platform}:{anchor_message.message_info.user_info.user_id}"
                )

        sender_info = None
        if target_stream.context and target_stream.context.message:
            sender_info = target_stream.context.message.message_info.user_info

        bot_message = MessageSending(
            message_id=message_id,
            session=target_stream,
            bot_user_info=bot_user_info,
            sender_info=sender_info,
            message_segment=message_segment,
            display_message=display_message,
            reply=anchor_message,
            is_head=True,
            is_emoji=(message_segment.type == "emoji"),
            thinking_start_time=current_time,
            reply_to=reply_to_platform_id,
            selected_expressions=selected_expressions,
        )

        sent_msg = await message_sender.send_message(
            bot_message,
            typing=typing,
            set_reply=set_reply,
            storage_message=storage_message,
            show_log=show_log,
        )

        if sent_msg:
            logger.debug(f"[SendService] 成功发送消息到 {stream_id}")
            return True
        else:
            logger.error("[SendService] 发送消息失败")
            return False

    except Exception as e:
        logger.error(f"[SendService] 发送消息时出错: {e}")
        traceback.print_exc()
        return False


def db_message_to_mai_message(message_obj: "DatabaseMessages") -> Optional[MaiMessage]:
    """将数据库消息重建为 MaiMessage 对象，用于回复引用。"""
    from datetime import datetime

    from src.common.data_models.mai_message_data_model import GroupInfo, MessageInfo
    from src.common.data_models.message_component_data_model import MessageSequence

    user_info = UserInfo(
        user_id=message_obj.user_info.user_id or "",
        user_nickname=message_obj.user_info.user_nickname or "",
        user_cardname=message_obj.user_info.user_cardname,
    )

    group_info = None
    if message_obj.chat_info.group_info:
        group_info = GroupInfo(
            group_id=message_obj.chat_info.group_info.group_id or "",
            group_name=message_obj.chat_info.group_info.group_name or "",
        )

    msg = MaiMessage(
        message_id=message_obj.message_id,
        timestamp=datetime.fromtimestamp(message_obj.time) if message_obj.time else datetime.now(),
    )
    msg.message_info = MessageInfo(user_info=user_info, group_info=group_info)
    msg.platform = message_obj.chat_info.platform or ""
    msg.session_id = message_obj.chat_info.stream_id or ""
    msg.processed_plain_text = message_obj.processed_plain_text
    msg.raw_message = MessageSequence(components=[])
    msg.initialized = True
    return msg


# =============================================================================
# 公共函数 - 预定义类型的发送函数
# =============================================================================


async def text_to_stream(
    text: str,
    stream_id: str,
    typing: bool = False,
    set_reply: bool = False,
    reply_message: Optional["DatabaseMessages"] = None,
    storage_message: bool = True,
    selected_expressions: Optional[List[int]] = None,
) -> bool:
    """向指定流发送文本消息"""
    return await _send_to_target(
        message_segment=Seg(type="text", data=text),
        stream_id=stream_id,
        display_message="",
        typing=typing,
        set_reply=set_reply,
        reply_message=reply_message,
        storage_message=storage_message,
        selected_expressions=selected_expressions,
    )


async def emoji_to_stream(
    emoji_base64: str,
    stream_id: str,
    storage_message: bool = True,
    set_reply: bool = False,
    reply_message: Optional["DatabaseMessages"] = None,
) -> bool:
    """向指定流发送表情包"""
    return await _send_to_target(
        message_segment=Seg(type="emoji", data=emoji_base64),
        stream_id=stream_id,
        display_message="",
        typing=False,
        storage_message=storage_message,
        set_reply=set_reply,
        reply_message=reply_message,
    )


async def image_to_stream(
    image_base64: str,
    stream_id: str,
    storage_message: bool = True,
    set_reply: bool = False,
    reply_message: Optional["DatabaseMessages"] = None,
) -> bool:
    """向指定流发送图片"""
    return await _send_to_target(
        message_segment=Seg(type="image", data=image_base64),
        stream_id=stream_id,
        display_message="",
        typing=False,
        storage_message=storage_message,
        set_reply=set_reply,
        reply_message=reply_message,
    )


async def command_to_stream(
    command: Union[str, dict],
    stream_id: str,
    storage_message: bool = True,
    display_message: str = "",
) -> bool:
    """向指定流发送命令"""
    return await _send_to_target(
        message_segment=Seg(type="command", data=command),  # type: ignore
        stream_id=stream_id,
        display_message=display_message,
        typing=False,
        storage_message=storage_message,
        set_reply=False,
    )


async def custom_to_stream(
    message_type: str,
    content: str | Dict,
    stream_id: str,
    display_message: str = "",
    typing: bool = False,
    reply_message: Optional["DatabaseMessages"] = None,
    set_reply: bool = False,
    storage_message: bool = True,
    show_log: bool = True,
) -> bool:
    """向指定流发送自定义类型消息"""
    return await _send_to_target(
        message_segment=Seg(type=message_type, data=content),  # type: ignore
        stream_id=stream_id,
        display_message=display_message,
        typing=typing,
        reply_message=reply_message,
        set_reply=set_reply,
        storage_message=storage_message,
        show_log=show_log,
    )


async def custom_reply_set_to_stream(
    reply_set: "ReplySetModel",
    stream_id: str,
    display_message: str = "",
    typing: bool = False,
    reply_message: Optional["DatabaseMessages"] = None,
    set_reply: bool = False,
    storage_message: bool = True,
    show_log: bool = True,
) -> bool:
    """向指定流发送混合型消息集"""
    flag: bool = True
    for reply_content in reply_set.reply_data:
        status: bool = False
        message_seg, need_typing = _parse_content_to_seg(reply_content)
        status = await _send_to_target(
            message_segment=message_seg,
            stream_id=stream_id,
            display_message=display_message,
            typing=bool(need_typing and typing),
            reply_message=reply_message,
            set_reply=set_reply,
            storage_message=storage_message,
            show_log=show_log,
        )
        if not status:
            flag = False
            logger.error(
                f"[SendService] 发送{repr(reply_content.content_type)}消息失败，消息内容：{str(reply_content.content)[:100]}"
            )

    return flag


def _parse_content_to_seg(reply_content: "ReplyContent") -> Tuple[Seg, bool]:
    """把 ReplyContent 转换为 Seg 结构"""
    content_type = reply_content.content_type
    if content_type == ReplyContentType.TEXT:
        text_data: str = reply_content.content  # type: ignore
        return Seg(type="text", data=text_data), True
    elif content_type == ReplyContentType.IMAGE:
        return Seg(type="image", data=reply_content.content), False  # type: ignore
    elif content_type == ReplyContentType.EMOJI:
        return Seg(type="emoji", data=reply_content.content), False  # type: ignore
    elif content_type == ReplyContentType.COMMAND:
        return Seg(type="command", data=reply_content.content), False  # type: ignore
    elif content_type == ReplyContentType.VOICE:
        return Seg(type="voice", data=reply_content.content), False  # type: ignore
    elif content_type == ReplyContentType.HYBRID:
        hybrid_message_list_data: List[ReplyContent] = reply_content.content  # type: ignore
        assert isinstance(hybrid_message_list_data, list), "混合类型内容必须是列表"
        sub_seg_list: List[Seg] = []
        for sub_content in hybrid_message_list_data:
            sub_content_type = sub_content.content_type
            sub_content_data = sub_content.content

            if sub_content_type == ReplyContentType.TEXT:
                sub_seg_list.append(Seg(type="text", data=sub_content_data))  # type: ignore
            elif sub_content_type == ReplyContentType.IMAGE:
                sub_seg_list.append(Seg(type="image", data=sub_content_data))  # type: ignore
            elif sub_content_type == ReplyContentType.EMOJI:
                sub_seg_list.append(Seg(type="emoji", data=sub_content_data))  # type: ignore
            else:
                logger.warning(f"[SendService] 混合类型中不支持的子内容类型: {repr(sub_content_type)}")
                continue
        return Seg(type="seglist", data=sub_seg_list), True
    elif content_type == ReplyContentType.FORWARD:
        forward_message_list_data: List["ForwardNode"] = reply_content.content  # type: ignore
        assert isinstance(forward_message_list_data, list), "转发类型内容必须是列表"
        forward_message_list: List[Dict] = []
        for forward_node in forward_message_list_data:
            message_segment = Seg(type="id", data=forward_node.content)  # type: ignore
            user_info: Optional[UserInfo] = None
            if forward_node.user_id and forward_node.user_nickname:
                assert isinstance(forward_node.content, list), "转发节点内容必须是列表"
                user_info = UserInfo(user_id=forward_node.user_id, user_nickname=forward_node.user_nickname)
                single_node_content: List[Seg] = []
                for sub_content in forward_node.content:
                    if sub_content.content_type != ReplyContentType.FORWARD:
                        sub_seg, _ = _parse_content_to_seg(sub_content)
                        single_node_content.append(sub_seg)
                message_segment = Seg(type="seglist", data=single_node_content)
            forward_message_list.append(
                MessageBase(
                    message_segment=message_segment, message_info=BaseMessageInfo(user_info=user_info)
                ).to_dict()
            )
        return Seg(type="forward", data=forward_message_list), False  # type: ignore
    else:
        message_type_in_str = content_type.value if isinstance(content_type, ReplyContentType) else str(content_type)
        return Seg(type=message_type_in_str, data=reply_content.content), True  # type: ignore
