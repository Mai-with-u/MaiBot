"""Maisaka 表情工具内置能力。"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from src.common.data_models.image_data_model import MaiEmoji
from src.common.logger import get_logger
from src.common.utils.image_path import resolve_stored_image_path
from src.common.utils.utils_image import ImageUtils
from src.services import send_service

from .emoji_manager import (
    _normalize_emoji_tag_text,
    _serialize_emoji_for_hook,
    emoji_manager,
)

logger = get_logger("emoji_maisaka_tool")

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage

EmojiSelector = Callable[
    [str, str, Sequence[str] | None, int],
    Awaitable[tuple[MaiEmoji | None, str]],
]


@dataclass(slots=True)
class MaisakaEmojiSendResult:
    """Maisaka 表情发送结果。"""

    success: bool
    message: str
    emoji_base64: str = ""
    description: str = ""
    emotions: list[str] = field(default_factory=list)
    requested_emotion: str = ""
    matched_emotion: str = ""
    sent_message: Optional["SessionMessage"] = None


def _get_runtime_manager() -> Any:
    """获取插件运行时管理器。

    Returns:
        Any: 插件运行时管理器单例。
    """

    from src.plugin_runtime.integration import get_plugin_runtime_manager

    return get_plugin_runtime_manager()


def _coerce_positive_int(value: Any, default: int) -> int:
    """将任意值安全转换为正整数。

    Args:
        value: 待转换的值。
        default: 转换失败时使用的默认值。

    Returns:
        int: 规范化后的正整数。
    """

    try:
        normalized_value = int(value)
    except (TypeError, ValueError):
        return default
    return normalized_value if normalized_value > 0 else default


def _normalize_context_texts(context_texts: Sequence[str] | None) -> list[str]:
    """清洗 Hook 和调用链传入的上下文文本列表。

    Args:
        context_texts: 原始上下文文本序列。

    Returns:
        list[str]: 过滤空白后的上下文文本列表。
    """

    if not context_texts:
        return []
    return [str(item).strip() for item in context_texts if str(item).strip()]


def _resolve_selected_emoji(raw_value: Any) -> Optional[MaiEmoji]:
    """根据 Hook 返回值解析目标表情包对象。

    Args:
        raw_value: Hook 返回的 ``selected_emoji`` 或 ``selected_emoji_hash``。

    Returns:
        Optional[MaiEmoji]: 命中的表情包对象；未命中时返回 ``None``。
    """

    raw_hash: str = ""
    if isinstance(raw_value, dict):
        raw_hash = str(raw_value.get("file_hash") or raw_value.get("hash") or "").strip()
    elif isinstance(raw_value, str):
        raw_hash = raw_value.strip()

    if not raw_hash:
        return None

    for emoji in emoji_manager.emojis:
        if emoji.file_hash == raw_hash:
            return emoji
    return None


def _normalize_emotions(emoji: MaiEmoji) -> list[str]:
    """提取并清洗单个表情的情绪标签。"""
    if emoji.description:
        return _normalize_emoji_tag_text(emoji.description)
    return []


async def send_emoji_for_maisaka(
    *,
    stream_id: str,
    emoji_selector: EmojiSelector,
    requested_emotion: str = "",
    reasoning: str = "",
    context_texts: Sequence[str] | None = None,
) -> MaisakaEmojiSendResult:
    """为 Maisaka 选择并发送一个表情。"""

    normalized_requested_emotion = requested_emotion.strip()
    normalized_reasoning = reasoning.strip()
    normalized_context_texts = _normalize_context_texts(context_texts)
    sample_size = 20

    before_select_result = await _get_runtime_manager().invoke_hook(
        "emoji.maisaka.before_select",
        stream_id=stream_id,
        requested_emotion=normalized_requested_emotion,
        reasoning=normalized_reasoning,
        context_texts=list(normalized_context_texts),
        sample_size=sample_size,
        abort_message="表情选择已被 Hook 中止。",
    )
    if before_select_result.aborted:
        abort_message = str(before_select_result.kwargs.get("abort_message") or "表情选择已被 Hook 中止。").strip()
        return MaisakaEmojiSendResult(
            success=False,
            message=abort_message or "表情选择已被 Hook 中止。",
            requested_emotion=normalized_requested_emotion,
        )

    before_select_kwargs = before_select_result.kwargs
    normalized_requested_emotion = str(
        before_select_kwargs.get("requested_emotion", normalized_requested_emotion) or ""
    ).strip()
    normalized_reasoning = str(before_select_kwargs.get("reasoning", normalized_reasoning) or "").strip()
    if isinstance(before_select_kwargs.get("context_texts"), list):
        normalized_context_texts = _normalize_context_texts(before_select_kwargs.get("context_texts"))
    sample_size = _coerce_positive_int(before_select_kwargs.get("sample_size"), sample_size)

    selected_emoji, matched_emotion = await emoji_selector(
        normalized_requested_emotion,
        normalized_reasoning,
        normalized_context_texts,
        sample_size,
    )
    after_select_result = await _get_runtime_manager().invoke_hook(
        "emoji.maisaka.after_select",
        stream_id=stream_id,
        requested_emotion=normalized_requested_emotion,
        reasoning=normalized_reasoning,
        context_texts=list(normalized_context_texts),
        sample_size=sample_size,
        selected_emoji=_serialize_emoji_for_hook(selected_emoji),
        selected_emoji_hash=str(selected_emoji.file_hash or "").strip() if selected_emoji is not None else "",
        matched_emotion=matched_emotion,
        abort_message="表情发送已被 Hook 中止。",
    )
    if after_select_result.aborted:
        abort_message = str(after_select_result.kwargs.get("abort_message") or "表情发送已被 Hook 中止。").strip()
        return MaisakaEmojiSendResult(
            success=False,
            message=abort_message or "表情发送已被 Hook 中止。",
            requested_emotion=normalized_requested_emotion,
            matched_emotion=matched_emotion,
        )

    after_select_kwargs = after_select_result.kwargs
    normalized_requested_emotion = str(
        after_select_kwargs.get("requested_emotion", normalized_requested_emotion) or ""
    ).strip()
    matched_emotion = str(after_select_kwargs.get("matched_emotion", matched_emotion) or "").strip()
    override_emoji = _resolve_selected_emoji(after_select_kwargs.get("selected_emoji_hash"))
    if override_emoji is None:
        override_emoji = _resolve_selected_emoji(after_select_kwargs.get("selected_emoji"))
    if override_emoji is not None:
        selected_emoji = override_emoji

    if selected_emoji is None:
        return MaisakaEmojiSendResult(
            success=False,
            message="当前表情包库中没有可用表情。",
            requested_emotion=normalized_requested_emotion,
            matched_emotion=matched_emotion,
        )

    try:
        emoji_base64 = ImageUtils.image_path_to_base64(str(resolve_stored_image_path(selected_emoji.full_path)))
        if not emoji_base64:
            raise ValueError("表情图片转换为 base64 失败")
    except Exception as exc:
        return MaisakaEmojiSendResult(
            success=False,
            message=f"发送表情包失败：{exc}",
            description=selected_emoji.description.strip(),
            emotions=_normalize_emotions(selected_emoji),
            requested_emotion=normalized_requested_emotion,
            matched_emotion=matched_emotion,
        )

    try:
        sent_message = await send_service.emoji_to_stream_with_message(
            emoji_base64=emoji_base64,
            stream_id=stream_id,
            storage_message=True,
            set_reply=False,
            reply_message=None,
            sync_to_maisaka_history=True,
            maisaka_source_kind="guided_reply",
        )
        sent = sent_message is not None
    except Exception as exc:
        return MaisakaEmojiSendResult(
            success=False,
            message=f"发送表情包时发生异常：{exc}",
            description=selected_emoji.description.strip(),
            emotions=_normalize_emotions(selected_emoji),
            requested_emotion=normalized_requested_emotion,
            matched_emotion=matched_emotion,
        )

    description = selected_emoji.description.strip()
    emotions = _normalize_emotions(selected_emoji)
    if not sent:
        return MaisakaEmojiSendResult(
            success=False,
            message="发送表情包失败。",
            description=description,
            emotions=emotions,
            requested_emotion=normalized_requested_emotion,
            matched_emotion=matched_emotion,
        )

    success_message = (
        f"已发送表情包：{description}（情绪：{', '.join(emotions)}）"
        if emotions
        else f"已发送表情包：{description}"
    )
    return MaisakaEmojiSendResult(
        success=True,
        message=success_message,
        emoji_base64=emoji_base64,
        description=description,
        emotions=emotions,
        requested_emotion=normalized_requested_emotion,
        matched_emotion=matched_emotion,
        sent_message=sent_message,
    )
