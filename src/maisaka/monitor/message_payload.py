"""MaiSaka 监控面板消息展示载荷构造。"""

from base64 import b64encode
from collections.abc import Callable
from typing import Any, Optional

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.message_component_data_model import (
    EmojiComponent,
    ImageComponent,
    MessageSequence,
    ReplyComponent,
    VoiceComponent,
)
from src.common.logger import get_logger
from src.maisaka.visual.mode_utils import resolve_enable_visual_planner

from .voice_media import monitor_voice_item

logger = get_logger("maisaka_monitor_message_payload")

MAX_INLINE_MONITOR_MEDIA_BYTES = 2 * 1024 * 1024


def build_monitor_message_content(
    message: SessionMessage,
    *,
    refresh_visual_components: Callable[[SessionMessage], None],
    log_prefix: str = "",
) -> str:
    """生成监控面板使用的消息可见文本，媒体内容由 media payload 单独承载。"""

    has_reply = any(isinstance(component, ReplyComponent) for component in message.raw_message.components)
    has_media = has_monitor_message_media(message)

    if has_media:
        refresh_visual_components(message)

    if has_reply or has_media:
        try:
            from src.maisaka.context.message_adapter import build_visible_text_from_sequence

            body_sequence = MessageSequence([
                component
                for component in message.raw_message.components
                if not isinstance(component, (EmojiComponent, ImageComponent, ReplyComponent))
            ])
            body_text = build_visible_text_from_sequence(body_sequence).strip()
            if body_text or has_media:
                return body_text
        except Exception as exc:
            logger.debug(
                f"{log_prefix} 构造监控消息正文失败: "
                f"message_id={message.message_id} error={exc}"
            )

    plain_text = (message.processed_plain_text or "").strip()
    if plain_text:
        return plain_text

    try:
        from src.maisaka.context.message_adapter import build_visible_text_from_sequence

        refresh_visual_components(message)
        return build_visible_text_from_sequence(message.raw_message).strip()
    except Exception as exc:
        logger.debug(
            f"{log_prefix} 构造监控消息可见文本失败: "
            f"message_id={message.message_id} error={exc}"
        )
        return ""


def has_monitor_message_media(message: SessionMessage) -> bool:
    """判断监控面板是否需要为消息单独展示图片或表情。"""

    return any(isinstance(component, (EmojiComponent, ImageComponent)) for component in message.raw_message.components)


def build_monitor_message_media(message: SessionMessage, *, log_prefix: str = "") -> list[dict[str, Any]]:
    """构造监控面板的图片、表情和语音播放资源。"""

    try:
        default_original = resolve_enable_visual_planner()
    except Exception as exc:
        logger.debug(f"{log_prefix} 解析监控媒体默认展示模式失败: {exc}")
        default_original = False

    media_items: list[dict[str, Any]] = []
    for index, component in enumerate(message.raw_message.components):
        if isinstance(component, VoiceComponent):
            if component.binary_hash:
                media_items.append(monitor_voice_item(component.binary_hash, component.content, index))
            continue
        if isinstance(component, ImageComponent):
            media_kind = "image"
            label = "[图片，识别中.....]"
        elif isinstance(component, EmojiComponent):
            media_kind = "emoji"
            label = "[表情包]"
        else:
            continue

        if not component.binary_hash:
            continue

        media_items.append({
            "kind": media_kind,
            "hash": component.binary_hash,
            "text": component.content.strip() or label,
            "url": f"/api/webui/system/maisaka-monitor/media/{media_kind}/{component.binary_hash}",
            "data_url": build_monitor_media_data_url(component),
            "default_original": default_original,
            "index": index,
        })
    return media_items


def build_monitor_media_data_url(component: ImageComponent | EmojiComponent) -> str:
    """为未落库的小体积图片/表情生成观察面板可直接展示的 data URL。"""

    binary_data = bytes(component.binary_data or b"")
    if not binary_data or len(binary_data) > MAX_INLINE_MONITOR_MEDIA_BYTES:
        return ""
    mime_type = guess_monitor_media_mime_type(binary_data)
    return f"data:{mime_type};base64,{b64encode(binary_data).decode('ascii')}"


def guess_monitor_media_mime_type(binary_data: bytes) -> str:
    """根据文件头判断常见图片 MIME 类型。"""

    if binary_data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if binary_data.startswith(b"GIF8"):
        return "image/gif"
    if binary_data.startswith(b"RIFF") and b"WEBP" in binary_data[:16]:
        return "image/webp"
    if binary_data.startswith(b"BM"):
        return "image/bmp"
    return "image/png"


def build_monitor_reply_preview(
    message: SessionMessage,
    *,
    find_source_message_by_id: Callable[[str], Optional[SessionMessage]],
    build_source_content: Callable[[SessionMessage], str],
) -> Optional[dict[str, str]]:
    """构造监控面板中的回复引用预览。"""

    for component in message.raw_message.components:
        if not isinstance(component, ReplyComponent):
            continue

        target_message_id = str(component.target_message_id or "").strip()
        target_content = str(component.target_message_content or "").strip()
        target_sender = (
            str(component.target_message_sender_cardname or "").strip()
            or str(component.target_message_sender_nickname or "").strip()
            or str(component.target_message_sender_id or "").strip()
        )
        if not target_content and target_message_id:
            target_message = find_source_message_by_id(target_message_id)
            if target_message is not None:
                target_content = build_source_content(target_message).strip()
                target_user = target_message.message_info.user_info
                target_sender = (
                    target_user.user_cardname
                    or target_user.user_nickname
                    or target_user.user_id
                )

        return {
            "message_id": target_message_id,
            "sender_name": target_sender or "未知用户",
            "content": target_content or "原消息已无法访问",
        }

    return None
