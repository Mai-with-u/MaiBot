"""Maisaka 历史消息轮次结束后处理。"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .compaction import ContextSummaryMessage
from .history import collect_tool_turn_anchor_indices, drop_leading_orphan_tool_results, normalize_tool_call_result_pairs
from .messages import (
    LLMContextMessage,
    ModelOutputContextMessage,
    ToolResultMediaMessage,
    ToolResultMessage,
)

# 达到配置的上下文条数即触发裁切，保留目标按配置条数计算。
TRIM_TARGET_RATIO = 0.2
MIN_RETAINED_CHAT_MESSAGES = 5


@dataclass(slots=True)
class HistoryPostProcessResult:
    """历史后处理结果。"""

    history: list[LLMContextMessage]
    removed_messages: list[LLMContextMessage]
    removed_count: int
    changed_count: int
    remaining_context_count: int


def process_chat_history_after_cycle(
    chat_history: list[LLMContextMessage],
    *,
    max_context_size: int,
    pending_call_ids: set[str] | None = None,
    consume_references: bool = True,
) -> HistoryPostProcessResult:
    """在每轮结束后统一执行历史裁切与清理。"""

    processed_history: list[LLMContextMessage] = []
    one_shot_removed_count = 0
    for message in chat_history:
        if consume_references and not message.consume_once():
            one_shot_removed_count += 1
            continue
        processed_history.append(message)
    processed_history, normalized_removed_count, moved_tool_result_count = _normalize_history_structure(
        processed_history,
        pending_call_ids=pending_call_ids,
    )
    remaining_context_count = sum(1 for message in processed_history if message.count_in_context)

    compact_removed_count = 0
    removed_messages: list[LLMContextMessage] = []
    if remaining_context_count >= max_context_size:
        target_context_count = max(MIN_RETAINED_CHAT_MESSAGES, int(max_context_size * TRIM_TARGET_RATIO))
        removed_messages = _trim_history_to_context_target(
            processed_history,
            target_context_count=target_context_count,
            pending_call_ids=pending_call_ids,
        )
        processed_history, removed_after_trim_count, moved_after_trim_count = _normalize_history_structure(
            processed_history,
            pending_call_ids=pending_call_ids,
        )
        compact_removed_count = len(removed_messages) + removed_after_trim_count
        moved_tool_result_count += moved_after_trim_count

    remaining_context_count = sum(1 for message in processed_history if message.count_in_context)
    removed_count = one_shot_removed_count + normalized_removed_count + compact_removed_count
    changed_count = removed_count + moved_tool_result_count
    return HistoryPostProcessResult(
        history=processed_history,
        removed_messages=removed_messages,
        removed_count=removed_count,
        changed_count=changed_count,
        remaining_context_count=remaining_context_count,
    )


def _normalize_history_structure(
    chat_history: list[LLMContextMessage],
    *,
    pending_call_ids: set[str] | None = None,
) -> tuple[list[LLMContextMessage], int, int]:
    """规范化历史消息结构，保证工具调用链符合 LLM 消息协议。"""

    processed_history, normalize_stats = normalize_tool_call_result_pairs(
        chat_history,
        pending_call_ids=pending_call_ids,
    )
    processed_history, leading_orphan_removed_count = drop_leading_orphan_tool_results(processed_history)
    removed_count = (
        normalize_stats["orphan_tool_results"]
        + normalize_stats["unanswered_tool_calls"]
        + normalize_stats["invalid_tool_turns"]
        + leading_orphan_removed_count
    )
    return (
        processed_history,
        removed_count,
        normalize_stats["moved_tool_results"],
    )


def _trim_history_to_context_target(
    chat_history: list[LLMContextMessage],
    *,
    target_context_count: int,
    pending_call_ids: Optional[Set[str]] = None,
) -> list[LLMContextMessage]:
    """选择连续历史前缀，保留最近五条真实聊天消息、最近用户回合和未完成的工具调用。"""

    remaining_count = sum(message.count_in_context for message in chat_history)
    if remaining_count <= target_context_count:
        return []

    turn_indexes: Dict[str, List[int]] = {}
    tool_turn_ids = _collect_tool_turn_ids(chat_history)
    media_turn_ids = {
        call.call_id: turn_id
        for message in chat_history if isinstance(message, ModelOutputContextMessage)
        if (turn_id := _get_logical_turn_id(message)) in tool_turn_ids
        for call in message.tool_calls
    }
    protected_start = max(0, len(chat_history) - 1)
    chat_message_indices: List[int] = []
    for index, message in enumerate(chat_history):
        if message.count_in_context:
            chat_message_indices.append(index)
            protected_start = index
        turn_id = (
            media_turn_ids.get(message.tool_call_id)
            if isinstance(message, ToolResultMediaMessage) else _get_logical_turn_id(message)
        )
        if turn_id in tool_turn_ids:
            turn_indexes.setdefault(turn_id, []).append(index)
    # 用户与麦麦实际收发的消息合计至少保留五条，工具结果和内部思考不占这个最低数量。
    # 不足五条时全部保留，裁切不能越过这条保护边界。
    if chat_message_indices:
        oldest_retained_index = chat_message_indices[max(0, len(chat_message_indices) - MIN_RETAINED_CHAT_MESSAGES)]
        protected_start = min(protected_start, oldest_retained_index)
    anchors = collect_tool_turn_anchor_indices(chat_history, tool_turn_ids)
    spans: List[Tuple[int, int]] = []
    for turn_id, indexes in turn_indexes.items():
        start = min(indexes[0], anchors.get(turn_id, indexes[0]))
        spans.append((start, indexes[-1] + 1))
    # user 消息触发的工具调用不能在裁切点两侧拆开，否则剩余历史会从
    # function call 开始，违反部分模型的消息顺序要求。
    for index, message in enumerate(chat_history):
        if isinstance(message, ModelOutputContextMessage) and any(
            call.call_id in (pending_call_ids or set()) for call in message.tool_calls
        ):
            turn_id = _get_logical_turn_id(message)
            protected_start = min(protected_start, anchors.get(turn_id, index))

    boundary = 0
    for candidate in range(1, protected_start + 1):
        message = chat_history[candidate - 1]
        if not isinstance(message, ContextSummaryMessage):
            remaining_count -= message.count_in_context
        if any(start < candidate < end for start, end in spans):
            continue
        boundary = candidate
        if remaining_count <= target_context_count:
            break

    removed_messages = [message for message in chat_history[:boundary] if not isinstance(message, ContextSummaryMessage)]
    chat_history[:boundary] = [message for message in chat_history[:boundary] if isinstance(message, ContextSummaryMessage)]
    return removed_messages


def _get_logical_turn_id(message: LLMContextMessage) -> str | None:
    """读取参与模型工具循环的历史条目逻辑轮次。"""

    if isinstance(message, ModelOutputContextMessage):
        return message.output_item.meta.logical_turn_id
    if isinstance(message, ToolResultMessage):
        return message.logical_turn_id
    return None


def _collect_tool_turn_ids(chat_history: list[LLMContextMessage]) -> set[str]:
    """收集至少包含一次 function call/output 的逻辑工具轮次。"""

    return {
        logical_turn_id
        for message in chat_history
        if isinstance(message, (ModelOutputContextMessage, ToolResultMessage))
        if (logical_turn_id := _get_logical_turn_id(message))
        if isinstance(message, ToolResultMessage) or bool(message.tool_calls)
    }
