"""SessionMessage 入站上下文工厂。

将真实会话消息收口为统一构造入口，但不同路径仍通过标志区分：
规划器前缀 vs Focus 原文唤醒、转发折叠 vs 不折叠、视觉回填 vs 不回填。
同一组标志的 Prompt 文本保持不变。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

import asyncio

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.message_component_data_model import (
    EmojiComponent,
    ImageComponent,
    MessageSequence,
    TextComponent,
)
from src.common.logger import get_logger

from .history import build_prefixed_message_sequence, build_session_message_visible_text
from .message_adapter import build_visible_text_from_sequence, format_speaker_content
from .messages import (
    ComplexSessionMessage,
    FOCUS_WAKEUP_SOURCE_KINDS,
    LLMContextMessage,
    SessionBackedMessage,
    contains_complex_message,
)
from .planner_messages import build_planner_prefix, build_planner_user_prefix_from_session_message

logger = get_logger("maisaka_inbound_factory")

FOCUS_SWITCH_SOURCE = "focus_switch"
RAW_FOCUS_INBOUND_SOURCE_KINDS = frozenset({*FOCUS_WAKEUP_SOURCE_KINDS, FOCUS_SWITCH_SOURCE})


@dataclass(frozen=True, slots=True)
class SessionInboundFlags:
    """SessionMessage 入站构造标志。路径不同则标志不同，不能混成同一输出。"""

    apply_planner_prefix: bool
    collapse_complex: bool
    hydrate_visual: bool

    @classmethod
    def planner_ingest(cls, *, hydrate_visual: bool) -> "SessionInboundFlags":
        """规划器入站：写入前缀，折叠转发，并按配置回填视觉二进制。"""

        return cls(apply_planner_prefix=True, collapse_complex=True, hydrate_visual=hydrate_visual)

    @classmethod
    def sent_message(cls) -> "SessionInboundFlags":
        """已发送回写：写入前缀，但不折叠转发、不回填视觉二进制。"""

        return cls(apply_planner_prefix=True, collapse_complex=False, hydrate_visual=False)

    @classmethod
    def raw_focus_wakeup(cls) -> "SessionInboundFlags":
        """Focus 唤醒/切换合成消息：保持原文，不写规划器前缀。"""

        return cls(apply_planner_prefix=False, collapse_complex=False, hydrate_visual=False)


def resolve_session_inbound_flags(
    source_kind: str,
    *,
    hydrate_visual: bool = False,
    sent_message: bool = False,
) -> SessionInboundFlags:
    """按来源路径解析入站标志，避免刷新历史时把原文唤醒改写成规划器前缀。"""

    if source_kind in RAW_FOCUS_INBOUND_SOURCE_KINDS:
        return SessionInboundFlags.raw_focus_wakeup()
    if sent_message:
        return SessionInboundFlags.sent_message()
    return SessionInboundFlags.planner_ingest(hydrate_visual=hydrate_visual)


def build_inbound_visible_text(
    message: SessionMessage,
    *,
    apply_planner_prefix: bool,
    source_kind: str = "user",
) -> str:
    """按是否写入规划器前缀构造可见文本。"""

    if not apply_planner_prefix:
        raw_text = (message.processed_plain_text or "").strip()
        if raw_text:
            return raw_text
        return build_visible_text_from_sequence(message.raw_message).strip()

    return build_session_message_visible_text(
        message,
        message.raw_message,
        include_reply_components=source_kind != "guided_reply",
    )


def _resolve_planner_prefix(
    message: SessionMessage,
    *,
    apply_planner_prefix: bool,
    source_kind: str,
    include_chat_id: bool,
) -> str:
    if not apply_planner_prefix:
        return ""
    return build_planner_user_prefix_from_session_message(
        message,
        include_chat_id=include_chat_id,
        is_self_message=source_kind == "guided_reply",
    )


def _build_inbound_sequence(
    message: SessionMessage,
    *,
    apply_planner_prefix: bool,
    planner_prefix: str,
) -> MessageSequence:
    if apply_planner_prefix:
        return build_prefixed_message_sequence(message.raw_message, planner_prefix)
    return message.raw_message


def _collapse_complex_inbound_message(
    message: SessionMessage,
    *,
    planner_prefix: str,
    visible_text: str,
    source_kind: str,
) -> Optional[ComplexSessionMessage]:
    return ComplexSessionMessage.from_session_message(
        message,
        planner_prefix=planner_prefix,
        visible_text=visible_text,
        source_kind=source_kind,
    )


def _wrap_session_backed_message(
    message: SessionMessage,
    *,
    inbound_sequence: MessageSequence,
    visible_text: str,
    source_kind: str,
) -> Optional[SessionBackedMessage]:
    if not inbound_sequence.components:
        return None
    return SessionBackedMessage.from_session_message(
        message,
        raw_message=inbound_sequence,
        visible_text=visible_text,
        source_kind=source_kind,
    )


async def hydrate_visual_components(
    planner_components: list[object],
    *,
    log_prefix: str = "",
) -> None:
    """在 Maisaka 真正需要图片或表情时，按需回填二进制数据。"""

    load_tasks: list[asyncio.Task[None]] = []
    for component in planner_components:
        if isinstance(component, ImageComponent) and not component.binary_data:
            load_tasks.append(asyncio.create_task(component.load_image_binary()))
            continue
        if isinstance(component, EmojiComponent) and not component.binary_data:
            load_tasks.append(asyncio.create_task(component.load_emoji_binary()))

    if not load_tasks:
        return

    results = await asyncio.gather(*load_tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logger.warning(f"{log_prefix} 回填图片或表情二进制数据失败，Maisaka 将退化为文本占位: {result}")


def _assemble_session_inbound_message(
    message: SessionMessage,
    *,
    flags: SessionInboundFlags,
    source_kind: str,
    include_chat_id: bool,
) -> tuple[str, str, Optional[LLMContextMessage], Optional[MessageSequence]]:
    """准备前缀、可见文本，并在需要时提前折叠转发消息。"""

    visible_text = build_inbound_visible_text(
        message,
        apply_planner_prefix=flags.apply_planner_prefix,
        source_kind=source_kind,
    )
    planner_prefix = _resolve_planner_prefix(
        message,
        apply_planner_prefix=flags.apply_planner_prefix,
        source_kind=source_kind,
        include_chat_id=include_chat_id,
    )
    if flags.collapse_complex and contains_complex_message(message.raw_message):
        return (
            visible_text,
            planner_prefix,
            _collapse_complex_inbound_message(
                message,
                planner_prefix=planner_prefix,
                visible_text=visible_text,
                source_kind=source_kind,
            ),
            None,
        )
    return (
        visible_text,
        planner_prefix,
        None,
        _build_inbound_sequence(
            message,
            apply_planner_prefix=flags.apply_planner_prefix,
            planner_prefix=planner_prefix,
        ),
    )


async def build_session_inbound_message(
    message: SessionMessage,
    *,
    flags: SessionInboundFlags,
    source_kind: str = "user",
    include_chat_id: bool = False,
    log_prefix: str = "",
) -> Optional[LLMContextMessage]:
    """按路径标志把 SessionMessage 构造成规划器历史消息。"""

    visible_text, _planner_prefix, collapsed_message, inbound_sequence = _assemble_session_inbound_message(
        message,
        flags=flags,
        source_kind=source_kind,
        include_chat_id=include_chat_id,
    )
    if flags.collapse_complex and contains_complex_message(message.raw_message):
        return collapsed_message
    if inbound_sequence is None:
        return None
    if flags.hydrate_visual:
        await hydrate_visual_components(inbound_sequence.components, log_prefix=log_prefix)
    return _wrap_session_backed_message(
        message,
        inbound_sequence=inbound_sequence,
        visible_text=visible_text,
        source_kind=source_kind,
    )


def build_session_inbound_message_sync(
    message: SessionMessage,
    *,
    flags: SessionInboundFlags,
    source_kind: str = "user",
    include_chat_id: bool = False,
) -> Optional[LLMContextMessage]:
    """同步构造入站历史；视觉回填必须走异步入口。"""

    if flags.hydrate_visual:
        raise ValueError("同步入站构造不能回填视觉二进制数据")

    visible_text, _planner_prefix, collapsed_message, inbound_sequence = _assemble_session_inbound_message(
        message,
        flags=flags,
        source_kind=source_kind,
        include_chat_id=include_chat_id,
    )
    if flags.collapse_complex and contains_complex_message(message.raw_message):
        return collapsed_message
    if inbound_sequence is None:
        return None
    return _wrap_session_backed_message(
        message,
        inbound_sequence=inbound_sequence,
        visible_text=visible_text,
        source_kind=source_kind,
    )


def build_planner_prefixed_history_message(
    *,
    speaker_name: str,
    timestamp: datetime,
    source_kind: str,
    body_sequence: MessageSequence,
    visible_body: str,
    group_card: str = "",
    message_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    quote_ids: Optional[Sequence[str]] = None,
    include_message_id: bool = True,
    include_chat_id: bool = False,
    is_self_message: bool = False,
) -> SessionBackedMessage:
    """构造带规划器前缀的合成历史消息。"""

    planner_prefix = build_planner_prefix(
        timestamp=timestamp,
        user_name=speaker_name,
        group_card=group_card,
        message_id=message_id,
        chat_id=chat_id,
        quote_ids=quote_ids,
        include_message_id=include_message_id,
        include_chat_id=include_chat_id,
        is_self_message=is_self_message,
    )
    return SessionBackedMessage(
        raw_message=build_prefixed_message_sequence(body_sequence, planner_prefix),
        visible_text=format_speaker_content(
            speaker_name,
            visible_body,
            timestamp,
            message_id if include_message_id else None,
        ),
        timestamp=timestamp,
        message_id=message_id,
        source_kind=source_kind,
    )


def build_session_backed_text_message(
    *,
    speaker_name: str,
    text: str,
    timestamp: datetime,
    source_kind: str,
    group_card: str = "",
    message_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    quote_ids: Optional[Sequence[str]] = None,
    include_message_id: bool = True,
    include_chat_id: bool = False,
    is_self_message: bool = False,
) -> SessionBackedMessage:
    """构造带规划器前缀的纯文本历史消息。"""

    return build_planner_prefixed_history_message(
        speaker_name=speaker_name,
        timestamp=timestamp,
        source_kind=source_kind,
        body_sequence=MessageSequence([TextComponent(text)]),
        visible_body=text,
        group_card=group_card,
        message_id=message_id,
        chat_id=chat_id,
        quote_ids=quote_ids,
        include_message_id=include_message_id,
        include_chat_id=include_chat_id,
        is_self_message=is_self_message,
    )
