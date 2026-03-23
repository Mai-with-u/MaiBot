"""
发送服务模块。

统一封装内部模块的出站消息发送逻辑：

1. 内部模块统一调用本模块。
2. send service 只负责构造和预处理消息。
3. 具体走插件链还是 legacy 旧链，由 Platform IO 内部统一决策。
"""

from typing import Any, Dict, List, Optional

from maim_message import Seg

import asyncio
import base64
import hashlib
import time
import traceback
from datetime import datetime

from src.chat.message_receive.chat_manager import BotChatSession
from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import calculate_typing_time, get_bot_account
from src.common.data_models.mai_message_data_model import GroupInfo, MaiMessage, MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import (
    AtComponent,
    DictComponent,
    EmojiComponent,
    ImageComponent,
    MessageSequence,
    ReplyComponent,
    StandardMessageComponents,
    TextComponent,
    VoiceComponent,
)
from src.common.logger import get_logger
from src.common.utils.utils_message import MessageUtils
from src.config.config import global_config
from src.platform_io import DeliveryBatch, get_platform_io_manager
from src.platform_io.route_key_factory import RouteKeyFactory

logger = get_logger("send_service")


def _inherit_platform_io_route_metadata(target_stream: BotChatSession) -> Dict[str, object]:
    """从目标会话继承 Platform IO 路由元数据。

    Args:
        target_stream: 当前消息要发送到的会话对象。

    Returns:
        Dict[str, object]: 可安全透传到出站消息 ``additional_config`` 中的
        路由辅助字段。
    """
    inherited_metadata: Dict[str, object] = {}

    context_message = target_stream.context.message if target_stream.context else None
    if context_message is None:
        return inherited_metadata

    additional_config = context_message.message_info.additional_config
    if not isinstance(additional_config, dict):
        return inherited_metadata

    for key in (*RouteKeyFactory.ACCOUNT_ID_KEYS, *RouteKeyFactory.SCOPE_KEYS):
        value = additional_config.get(key)
        if value is None:
            continue
        normalized_value = str(value).strip()
        if normalized_value:
            inherited_metadata[key] = value

    if target_stream.group_id:
        normalized_group_id = str(target_stream.group_id).strip()
        if normalized_group_id:
            inherited_metadata["platform_io_target_group_id"] = normalized_group_id

    if target_stream.user_id:
        normalized_user_id = str(target_stream.user_id).strip()
        if normalized_user_id:
            inherited_metadata["platform_io_target_user_id"] = normalized_user_id

    return inherited_metadata


def _build_component_from_seg(message_segment: Seg) -> StandardMessageComponents:
    """将单个消息段转换为内部消息组件。

    Args:
        message_segment: 待转换的消息段。

    Returns:
        StandardMessageComponents: 转换后的内部消息组件。
    """
    segment_type = str(message_segment.type or "").strip().lower()
    segment_data = message_segment.data

    if segment_type == "text":
        return TextComponent(text=str(segment_data or ""))

    if segment_type == "image":
        image_binary = base64.b64decode(str(segment_data or ""))
        return ImageComponent(
            binary_hash=hashlib.sha256(image_binary).hexdigest(),
            binary_data=image_binary,
        )

    if segment_type == "emoji":
        emoji_binary = base64.b64decode(str(segment_data or ""))
        return EmojiComponent(
            binary_hash=hashlib.sha256(emoji_binary).hexdigest(),
            binary_data=emoji_binary,
        )

    if segment_type == "voice":
        voice_binary = base64.b64decode(str(segment_data or ""))
        return VoiceComponent(
            binary_hash=hashlib.sha256(voice_binary).hexdigest(),
            binary_data=voice_binary,
        )

    if segment_type == "at":
        return AtComponent(target_user_id=str(segment_data or ""))

    if segment_type == "reply":
        return ReplyComponent(target_message_id=str(segment_data or ""))

    if segment_type == "dict" and isinstance(segment_data, dict):
        return DictComponent(data=segment_data)

    return DictComponent(data={"type": segment_type, "data": segment_data})


def _build_message_sequence_from_seg(message_segment: Seg) -> MessageSequence:
    """将消息段转换为内部消息组件序列。

    Args:
        message_segment: 待转换的消息段。

    Returns:
        MessageSequence: 转换后的消息组件序列。
    """
    if str(message_segment.type or "").strip().lower() == "seglist":
        raw_segments = message_segment.data
        if not isinstance(raw_segments, list):
            raise ValueError("seglist 类型的消息段数据必须是列表")
        components = [
            _build_component_from_seg(item)
            for item in raw_segments
            if isinstance(item, Seg)
        ]
        return MessageSequence(components=components)

    return MessageSequence(components=[_build_component_from_seg(message_segment)])


def _build_processed_plain_text(message: SessionMessage) -> str:
    """为出站消息构造轻量纯文本摘要。

    Args:
        message: 待发送的内部消息对象。

    Returns:
        str: 适用于日志与打字时长估算的纯文本摘要。
    """
    processed_parts: List[str] = []
    for component in message.raw_message.components:
        if isinstance(component, TextComponent):
            processed_parts.append(component.text)
            continue

        if isinstance(component, ImageComponent):
            processed_parts.append(component.content or "[图片]")
            continue

        if isinstance(component, EmojiComponent):
            processed_parts.append(component.content or "[表情]")
            continue

        if isinstance(component, VoiceComponent):
            processed_parts.append(component.content or "[语音]")
            continue

        if isinstance(component, AtComponent):
            at_target = component.target_user_cardname or component.target_user_nickname or component.target_user_id
            processed_parts.append(f"@{at_target}")
            continue

        if isinstance(component, ReplyComponent):
            processed_parts.append(component.target_message_content or "[回复消息]")
            continue

        if isinstance(component, DictComponent):
            raw_type = component.data.get("type") if isinstance(component.data, dict) else None
            if isinstance(raw_type, str) and raw_type.strip():
                processed_parts.append(f"[{raw_type.strip()}消息]")
            else:
                processed_parts.append("[自定义消息]")
            continue

    return " ".join(part for part in processed_parts if part)


def _build_outbound_session_message(
    message_segment: Seg,
    stream_id: str,
    display_message: str = "",
    reply_message: Optional[MaiMessage] = None,
    selected_expressions: Optional[List[int]] = None,
) -> Optional[SessionMessage]:
    """根据目标会话构建待发送的内部消息对象。

    Args:
        message_segment: 待发送的消息段。
        stream_id: 目标会话 ID。
        display_message: 用于界面展示的文本内容。
        reply_message: 被回复的锚点消息。
        selected_expressions: 可选的表情候选索引列表。

    Returns:
        Optional[SessionMessage]: 构建成功时返回内部消息对象；若目标会话或
        机器人账号不存在，则返回 ``None``。
    """
    target_stream = _chat_manager.get_session_by_session_id(stream_id)
    if target_stream is None:
        logger.error(f"[SendService] 未找到聊天流: {stream_id}")
        return None

    bot_user_id = get_bot_account(target_stream.platform)
    if not bot_user_id:
        logger.error(f"[SendService] 平台 {target_stream.platform} 未配置机器人账号，无法发送消息")
        return None

    current_time = time.time()
    message_id = f"send_api_{int(current_time * 1000)}"
    anchor_message = reply_message.deepcopy() if reply_message is not None else None

    group_info: Optional[GroupInfo] = None
    if target_stream.group_id:
        group_name = ""
        if (
            target_stream.context
            and target_stream.context.message
            and target_stream.context.message.message_info.group_info
        ):
            group_name = target_stream.context.message.message_info.group_info.group_name
        group_info = GroupInfo(
            group_id=target_stream.group_id,
            group_name=group_name,
        )

    additional_config: Dict[str, object] = _inherit_platform_io_route_metadata(target_stream)
    if selected_expressions is not None:
        additional_config["selected_expressions"] = selected_expressions

    outbound_message = SessionMessage(
        message_id=message_id,
        timestamp=datetime.fromtimestamp(current_time),
        platform=target_stream.platform,
    )
    outbound_message.message_info = MessageInfo(
        user_info=UserInfo(
            user_id=bot_user_id,
            user_nickname=global_config.bot.nickname,
        ),
        group_info=group_info,
        additional_config=additional_config,
    )
    outbound_message.raw_message = _build_message_sequence_from_seg(message_segment)
    outbound_message.session_id = target_stream.session_id
    outbound_message.display_message = display_message
    outbound_message.reply_to = anchor_message.message_id if anchor_message is not None else None
    outbound_message.is_emoji = message_segment.type == "emoji"
    outbound_message.is_picture = message_segment.type == "image"
    outbound_message.is_command = message_segment.type == "command"
    outbound_message.initialized = True
    return outbound_message


def _ensure_reply_component(message: SessionMessage, reply_message_id: str) -> None:
    """为消息补充回复组件。

    Args:
        message: 待发送的内部消息对象。
        reply_message_id: 被引用消息的 ID。
    """
    if message.raw_message.components:
        first_component = message.raw_message.components[0]
        if isinstance(first_component, ReplyComponent) and first_component.target_message_id == reply_message_id:
            return

    message.raw_message.components.insert(0, ReplyComponent(target_message_id=reply_message_id))


async def _prepare_message_for_platform_io(
    message: SessionMessage,
    *,
    typing: bool,
    set_reply: bool,
    reply_message_id: Optional[str],
) -> None:
    """为 Platform IO 发送链预处理消息。

    Args:
        message: 待发送的内部消息对象。
        typing: 是否模拟打字等待。
        set_reply: 是否构建引用回复组件。
        reply_message_id: 被引用消息的 ID。

    Raises:
        ValueError: 当要求设置引用回复但缺少 ``reply_message_id`` 时抛出。
    """
    if set_reply:
        if not reply_message_id:
            raise ValueError("set_reply=True 时必须提供 reply_message_id")
        _ensure_reply_component(message, reply_message_id)

    message.processed_plain_text = _build_processed_plain_text(message)
    if typing:
        typing_time = calculate_typing_time(
            input_string=message.processed_plain_text or "",
            is_emoji=message.is_emoji,
        )
        await asyncio.sleep(typing_time)


def _store_sent_message(message: SessionMessage) -> None:
    """将已成功发送的消息写入数据库。

    Args:
        message: 已成功发送的内部消息对象。
    """
    MessageUtils.store_message_to_db(message)


def _log_platform_io_failures(delivery_batch: DeliveryBatch) -> None:
    """输出 Platform IO 批量发送失败详情。

    Args:
        delivery_batch: Platform IO 返回的批量回执。
    """
    failed_details = "; ".join(
        f"driver={receipt.driver_id} status={receipt.status} error={receipt.error}"
        for receipt in delivery_batch.failed_receipts
    ) or "未命中任何发送路由"
    logger.warning(
        "[SendService] Platform IO 发送失败: platform=%s %s",
        delivery_batch.route_key.platform,
        failed_details,
    )


async def _send_via_platform_io(
    message: SessionMessage,
    *,
    typing: bool,
    set_reply: bool,
    reply_message_id: Optional[str],
    storage_message: bool,
    show_log: bool,
) -> bool:
    """通过 Platform IO 发送消息。

    Args:
        message: 待发送的内部消息对象。
        typing: 是否模拟打字等待。
        set_reply: 是否设置引用回复。
        reply_message_id: 被引用消息的 ID。
        storage_message: 发送成功后是否写入数据库。
        show_log: 是否输出发送成功日志。

    Returns:
        bool: 发送成功时返回 ``True``。
    """
    platform_io_manager = get_platform_io_manager()
    try:
        await platform_io_manager.ensure_send_pipeline_ready()
    except Exception as exc:
        logger.error(f"[SendService] 准备 Platform IO 发送管线失败: {exc}")
        logger.debug(traceback.format_exc())
        return False

    try:
        route_key = platform_io_manager.build_route_key_from_message(message)
    except Exception as exc:
        logger.warning(f"[SendService] 根据消息构造 Platform IO 路由键失败: {exc}")
        return False

    try:
        await _prepare_message_for_platform_io(
            message,
            typing=typing,
            set_reply=set_reply,
            reply_message_id=reply_message_id,
        )
        delivery_batch = await platform_io_manager.send_message(
            message,
            route_key,
            metadata={"show_log": False},
        )
    except Exception as exc:
        logger.error(f"[SendService] Platform IO 发送异常: {exc}")
        logger.debug(traceback.format_exc())
        return False

    if delivery_batch.has_success:
        if storage_message:
            _store_sent_message(message)
        if show_log:
            successful_driver_ids = [
                receipt.driver_id or "unknown"
                for receipt in delivery_batch.sent_receipts
            ]
            logger.info(
                "[SendService] 已通过 Platform IO 将消息发往平台 '%s' (drivers: %s)",
                route_key.platform,
                ", ".join(successful_driver_ids),
            )
        return True

    _log_platform_io_failures(delivery_batch)
    return False


async def send_session_message(
    message: SessionMessage,
    *,
    typing: bool = False,
    set_reply: bool = False,
    reply_message_id: Optional[str] = None,
    storage_message: bool = True,
    show_log: bool = True,
) -> bool:
    """统一发送一条内部消息。

    该方法是内部模块的统一发送入口：

    1. 构造并维护内部消息对象。
    2. 由 Platform IO 统一决定走插件链还是 legacy 旧链。
    3. send service 不再自行判断底层发送路径。

    Args:
        message: 待发送的内部消息对象。
        typing: 是否模拟打字等待。
        set_reply: 是否设置引用回复。
        reply_message_id: 被引用消息的 ID。
        storage_message: 发送成功后是否写入数据库。
        show_log: 是否输出发送日志。

    Returns:
        bool: 发送成功时返回 ``True``，否则返回 ``False``。
    """
    if not message.message_id:
        logger.error("[SendService] 消息缺少 message_id，无法发送")
        raise ValueError("消息缺少 message_id，无法发送")

    return await _send_via_platform_io(
        message,
        typing=typing,
        set_reply=set_reply,
        reply_message_id=reply_message_id,
        storage_message=storage_message,
        show_log=show_log,
    )


async def _send_to_target(
    message_segment: Seg,
    stream_id: str,
    display_message: str = "",
    typing: bool = False,
    set_reply: bool = False,
    reply_message: Optional[MaiMessage] = None,
    storage_message: bool = True,
    show_log: bool = True,
    selected_expressions: Optional[List[int]] = None,
) -> bool:
    """向指定目标构建并发送消息。

    Args:
        message_segment: 待发送的消息段。
        stream_id: 目标会话 ID。
        display_message: 用于界面展示的文本内容。
        typing: 是否显示输入中状态。
        set_reply: 是否在发送时附带引用回复。
        reply_message: 被回复的消息对象。
        storage_message: 是否将发送结果写入消息存储。
        show_log: 是否输出发送日志。
        selected_expressions: 可选的表情候选索引列表。

    Returns:
        bool: 发送成功返回 ``True``，否则返回 ``False``。
    """
    try:
        if set_reply and reply_message is None:
            logger.warning("[SendService] 使用引用回复，但未提供回复消息")
            return False

        if show_log:
            logger.debug(f"[SendService] 发送{message_segment.type}消息到 {stream_id}")

        outbound_message = _build_outbound_session_message(
            message_segment=message_segment,
            stream_id=stream_id,
            display_message=display_message,
            reply_message=reply_message,
            selected_expressions=selected_expressions,
        )
        if outbound_message is None:
            return False

        sent = await send_session_message(
            outbound_message,
            typing=typing,
            set_reply=set_reply,
            reply_message_id=reply_message.message_id if reply_message is not None else None,
            storage_message=storage_message,
            show_log=show_log,
        )
        if sent:
            logger.debug(f"[SendService] 成功发送消息到 {stream_id}")
            return True

        logger.error("[SendService] 发送消息失败")
        return False
    except Exception as exc:
        logger.error(f"[SendService] 发送消息时出错: {exc}")
        traceback.print_exc()
        return False


async def text_to_stream(
    text: str,
    stream_id: str,
    typing: bool = False,
    set_reply: bool = False,
    reply_message: Optional[MaiMessage] = None,
    storage_message: bool = True,
    selected_expressions: Optional[List[int]] = None,
) -> bool:
    """向指定流发送文本消息。

    Args:
        text: 要发送的文本内容。
        stream_id: 目标会话 ID。
        typing: 是否显示输入中状态。
        set_reply: 是否附带引用回复。
        reply_message: 被回复的消息对象。
        storage_message: 是否在发送成功后写入数据库。
        selected_expressions: 可选的表情候选索引列表。

    Returns:
        bool: 发送成功时返回 ``True``。
    """
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
    reply_message: Optional[MaiMessage] = None,
) -> bool:
    """向指定流发送表情消息。

    Args:
        emoji_base64: 表情图片的 Base64 内容。
        stream_id: 目标会话 ID。
        storage_message: 是否在发送成功后写入数据库。
        set_reply: 是否附带引用回复。
        reply_message: 被回复的消息对象。

    Returns:
        bool: 发送成功时返回 ``True``。
    """
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
    reply_message: Optional[MaiMessage] = None,
) -> bool:
    """向指定流发送图片消息。

    Args:
        image_base64: 图片的 Base64 内容。
        stream_id: 目标会话 ID。
        storage_message: 是否在发送成功后写入数据库。
        set_reply: 是否附带引用回复。
        reply_message: 被回复的消息对象。

    Returns:
        bool: 发送成功时返回 ``True``。
    """
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
    content: str | Dict[str, Any],
    stream_id: str,
    display_message: str = "",
    typing: bool = False,
    reply_message: Optional[MaiMessage] = None,
    set_reply: bool = False,
    storage_message: bool = True,
    show_log: bool = True,
) -> bool:
    """向指定流发送自定义类型消息。

    Args:
        message_type: 自定义消息类型。
        content: 自定义消息内容。
        stream_id: 目标会话 ID。
        display_message: 用于展示的文本内容。
        typing: 是否显示输入中状态。
        reply_message: 被回复的消息对象。
        set_reply: 是否附带引用回复。
        storage_message: 是否在发送成功后写入数据库。
        show_log: 是否输出发送日志。

    Returns:
        bool: 发送成功时返回 ``True``。
    """
    return await _send_to_target(
        message_segment=Seg(type=message_type, data=content),  # type: ignore[arg-type]
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
    reply_message: Optional[MaiMessage] = None,
    set_reply: bool = False,
    storage_message: bool = True,
    show_log: bool = True,
) -> bool:
    """向指定流发送消息组件序列。

    Args:
        reply_set: 待发送的消息组件序列。
        stream_id: 目标会话 ID。
        display_message: 用于展示的文本内容。
        typing: 是否显示输入中状态。
        reply_message: 被回复的消息对象。
        set_reply: 是否附带引用回复。
        storage_message: 是否在发送成功后写入数据库。
        show_log: 是否输出发送日志。

    Returns:
        bool: 全部组件发送成功时返回 ``True``。
    """
    success = True
    for component in reply_set.components:
        if isinstance(component, DictComponent):
            message_seg = Seg(type="dict", data=component.data)  # type: ignore[arg-type]
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
            success = False
            logger.error(f"[SendService] 发送消息组件失败，组件类型：{type(component).__name__}")
        set_reply = False

    return success
