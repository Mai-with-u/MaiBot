"""
发送服务模块

提供发送各种类型消息的核心功能。
"""

from typing import Dict, List, Optional, TYPE_CHECKING

import time
import traceback

from maim_message import BaseMessageInfo, GroupInfo as MaimGroupInfo, MessageBase, Seg, UserInfo as MaimUserInfo

from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.chat.message_receive.message import SessionMessage
from src.chat.message_receive.uni_message_sender import UniversalMessageSender
from src.chat.utils.utils import get_bot_account
from src.common.data_models.mai_message_data_model import MaiMessage
from src.common.data_models.message_component_data_model import DictComponent, MessageSequence
from src.common.logger import get_logger
from src.config.config import global_config

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage

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
    reply_message: Optional["SessionMessage"] = None,
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

        anchor_message: Optional[MaiMessage] = None
        if reply_message:
            anchor_message = reply_message.deepcopy()
            if anchor_message:
                logger.debug(
                    f"[SendService] 找到匹配的回复消息，发送者: {anchor_message.message_info.user_info.user_id}"
                )

        group_info = None
        if target_stream.group_id:
            group_name = ""
            if target_stream.context and target_stream.context.message and target_stream.context.message.message_info.group_info:
                group_name = target_stream.context.message.message_info.group_info.group_name
            group_info = MaimGroupInfo(
                group_id=target_stream.group_id,
                group_name=group_name,
                platform=target_stream.platform,
            )

        additional_config: dict[str, object] = {}
        if selected_expressions is not None:
            additional_config["selected_expressions"] = selected_expressions
        bot_user_id = get_bot_account(target_stream.platform)
        if not bot_user_id:
            logger.error(f"[SendService] 平台 {target_stream.platform} 未配置机器人账号，无法发送消息")
            return False

        maim_message = MessageBase(
            message_info=BaseMessageInfo(
                platform=target_stream.platform,
                message_id=message_id,
                time=current_time,
                user_info=MaimUserInfo(
                    user_id=bot_user_id,
                    user_nickname=global_config.bot.nickname,
                    platform=target_stream.platform,
                ),
                group_info=group_info,
                additional_config=additional_config,
            ),
            message_segment=message_segment,
        )
        bot_message = SessionMessage.from_maim_message(maim_message)
        bot_message.session_id = target_stream.session_id
        bot_message.display_message = display_message
        bot_message.reply_to = anchor_message.message_id if anchor_message else None
        bot_message.is_emoji = message_segment.type == "emoji"
        bot_message.is_picture = message_segment.type == "image"
        bot_message.is_command = message_segment.type == "command"

        sent_msg = await message_sender.send_message(
            bot_message,
            typing=typing,
            set_reply=set_reply,
            reply_message_id=anchor_message.message_id if anchor_message else None,
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


# =============================================================================
# 公共函数 - 预定义类型的发送函数
# =============================================================================


async def text_to_stream(
    text: str,
    stream_id: str,
    typing: bool = False,
    set_reply: bool = False,
    reply_message: Optional["SessionMessage"] = None,
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
    reply_message: Optional["SessionMessage"] = None,
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
    reply_message: Optional["SessionMessage"] = None,
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


async def custom_to_stream(
    message_type: str,
    content: str | Dict,
    stream_id: str,
    display_message: str = "",
    typing: bool = False,
    reply_message: Optional["SessionMessage"] = None,
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
    reply_set: MessageSequence,
    stream_id: str,
    display_message: str = "",
    typing: bool = False,
    reply_message: Optional["SessionMessage"] = None,
    set_reply: bool = False,
    storage_message: bool = True,
    show_log: bool = True,
) -> bool:
    """向指定流发送消息组件序列。"""
    flag: bool = True
    for component in reply_set.components:
        if isinstance(component, DictComponent):
            message_seg = Seg(type="dict", data=component.data)  # type: ignore
        else:
            message_seg = await component.to_seg()
        status = await _send_to_target(
            message_segment=message_seg,
            stream_id=stream_id,
            display_message=display_message,
            typing=typing,
            reply_message=reply_message,
            set_reply=set_reply,
            storage_message=storage_message,
            show_log=show_log,
        )
        if not status:
            flag = False
            logger.error(f"[SendService] 发送消息组件失败，组件类型：{type(component).__name__}")
        set_reply = False

    return flag
