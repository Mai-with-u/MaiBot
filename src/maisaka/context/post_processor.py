"""Maisaka 历史消息轮次结束后处理。"""

from dataclasses import dataclass
from math import ceil

from src.maisaka.memory.mid_term import is_mid_term_memory_message

from .history import drop_leading_orphan_tool_results, normalize_tool_call_result_pairs
from .messages import (
    LLMContextMessage,
    ModelOutputContextMessage,
    ToolResultMessage,
)

TRIM_TARGET_RATIO = 1.0
TRIM_THRESHOLD_RATIO = 2.0


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
) -> HistoryPostProcessResult:
    """在每轮结束后统一执行历史裁切与清理。"""

    processed_history: list[LLMContextMessage] = []
    one_shot_removed_count = 0
    for message in chat_history:
        if not message.consume_once():
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
    trim_threshold = ceil(max_context_size * TRIM_THRESHOLD_RATIO)
    if remaining_context_count > trim_threshold:
        target_context_count = max(1, int(max_context_size * TRIM_TARGET_RATIO))
        removed_messages = _trim_history_to_context_target(
            processed_history,
            target_context_count=target_context_count,
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
) -> list[LLMContextMessage]:
    """移除最早的一段历史，直到普通上下文消息数量降到目标值以内。"""

    remaining_context_count = sum(1 for message in chat_history if message.count_in_context)
    if remaining_context_count <= target_context_count:
        return []

    remove_indexes: list[int] = []
    visited_indexes: set[int] = set()
    tool_turn_ids = _collect_tool_turn_ids(chat_history)
    for index, message in enumerate(chat_history):
        if index in visited_indexes:
            continue
        if is_mid_term_memory_message(message):
            continue

        unit_indexes = {index}
        logical_turn_id = _get_logical_turn_id(message)
        if logical_turn_id in tool_turn_ids:
            unit_indexes = {
                candidate_index
                for candidate_index, candidate in enumerate(chat_history)
                if _get_logical_turn_id(candidate) == logical_turn_id
            }
        elif message.role == "user":
            # user 消息触发的工具调用不能在裁切点两侧拆开，否则剩余历史会从
            # function call 开始，违反部分模型的消息顺序要求。
            anchored_tool_turn_ids = _collect_following_tool_turn_ids(
                chat_history,
                user_index=index,
                tool_turn_ids=tool_turn_ids,
            )
            if anchored_tool_turn_ids:
                unit_indexes.update(
                    candidate_index
                    for candidate_index, candidate in enumerate(chat_history)
                    if _get_logical_turn_id(candidate) in anchored_tool_turn_ids
                )

        visited_indexes.update(unit_indexes)
        remove_indexes.extend(unit_indexes)
        remaining_context_count -= sum(1 for unit_index in unit_indexes if chat_history[unit_index].count_in_context)
        if remaining_context_count <= target_context_count:
            break

    if not remove_indexes:
        return []

    normalized_remove_indexes = sorted(set(remove_indexes))
    removed_messages = [chat_history[index] for index in normalized_remove_indexes]
    for index in reversed(normalized_remove_indexes):
        del chat_history[index]
    return removed_messages


def _collect_following_tool_turn_ids(
    chat_history: list[LLMContextMessage],
    *,
    user_index: int,
    tool_turn_ids: set[str],
) -> set[str]:
    """收集一条 user 消息之后、下一条 user 消息之前的工具轮次。"""

    following_tool_turn_ids: set[str] = set()
    for message in chat_history[user_index + 1 :]:
        if message.role == "user":
            break
        logical_turn_id = _get_logical_turn_id(message)
        if logical_turn_id in tool_turn_ids:
            following_tool_turn_ids.add(logical_turn_id)
    return following_tool_turn_ids


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
