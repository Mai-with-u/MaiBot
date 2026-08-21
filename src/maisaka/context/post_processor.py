"""Maisaka 历史消息轮次结束后处理。"""

from dataclasses import dataclass
from json import dumps, loads
from math import ceil
from typing import cast

from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.maisaka.memory.mid_term import is_mid_term_memory_message

from .history import drop_leading_orphan_tool_results, normalize_tool_call_result_pairs
from .messages import (
    ComplexSessionMessage,
    FOCUS_WAKEUP_SOURCE_KINDS,
    LLMContextMessage,
    ModelOutputContextMessage,
    SessionBackedMessage,
    ToolResultMessage,
)

TRIM_TARGET_RATIO = 1.0
TRIM_THRESHOLD_RATIO = 2.0
ASSISTANT_OPTIMIZATION_KEEP_COUNT = 3
FOLDED_TOOL_COMPLEX_MESSAGE_THRESHOLD = 1024
TRIMMED_TOOL_CALL_DROP_NAMES = {"continue", "finish", "no_action", "reply", "wait"}


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
    enable_context_optimization: bool = False,
    pending_call_ids: set[str] | None = None,
) -> HistoryPostProcessResult:
    """在每轮结束后统一执行历史裁切与清理。"""

    processed_history: list[LLMContextMessage] = []
    one_shot_removed_count = 0
    for message in chat_history:
        if message.source in FOCUS_WAKEUP_SOURCE_KINDS:
            one_shot_removed_count += 1
            continue
        if not message.consume_once():
            one_shot_removed_count += 1
            continue
        processed_history.append(message)
    processed_history, normalized_removed_count, moved_tool_result_count = _normalize_history_structure(
        processed_history,
        pending_call_ids=pending_call_ids,
    )
    remaining_context_count = sum(1 for message in processed_history if message.count_in_context)

    optimized_removed_count = 0
    if enable_context_optimization:
        optimized_removed_messages = _trim_assistant_history_to_latest(
            processed_history,
            keep_count=ASSISTANT_OPTIMIZATION_KEEP_COUNT,
        )
        if optimized_removed_messages:
            processed_history, removed_after_optimize_count, moved_after_optimize_count = _normalize_history_structure(
                processed_history,
                pending_call_ids=pending_call_ids,
            )
            optimized_removed_count = len(optimized_removed_messages) + removed_after_optimize_count
            moved_tool_result_count += moved_after_optimize_count
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
    removed_count = one_shot_removed_count + normalized_removed_count + optimized_removed_count + compact_removed_count
    changed_count = removed_count + moved_tool_result_count
    return HistoryPostProcessResult(
        history=processed_history,
        removed_messages=removed_messages,
        removed_count=removed_count,
        changed_count=changed_count,
        remaining_context_count=remaining_context_count,
    )


def _trim_assistant_history_to_latest(
    chat_history: list[LLMContextMessage],
    *,
    keep_count: int,
) -> list[LLMContextMessage]:
    """只保留最新的若干条 assistant 历史消息。"""

    normalized_keep_count = max(0, keep_count)
    tool_turn_ids = _collect_tool_turn_ids(chat_history)
    output_unit_ids: list[tuple[str, str]] = []
    for message in chat_history:
        if not isinstance(message, ModelOutputContextMessage):
            continue
        logical_turn_id = message.output_item.meta.logical_turn_id
        unit_id = (
            ("turn", logical_turn_id)
            if logical_turn_id in tool_turn_ids
            else ("item", message.output_item.meta.item_id)
        )
        if unit_id not in output_unit_ids:
            output_unit_ids.append(unit_id)
    remove_count = len(output_unit_ids) - normalized_keep_count
    if remove_count <= 0:
        return []

    removed_unit_ids = set(output_unit_ids[:remove_count])
    remove_indexes = {
        index
        for index, message in enumerate(chat_history)
        if isinstance(message, ModelOutputContextMessage)
        and (
            (
                "turn",
                message.output_item.meta.logical_turn_id,
            )
            if message.output_item.meta.logical_turn_id in tool_turn_ids
            else ("item", message.output_item.meta.item_id)
        )
        in removed_unit_ids
    }
    removed_messages = [message for index, message in enumerate(chat_history) if index in remove_indexes]
    tool_result_by_call_id = {
        message.tool_call_id: message
        for message in chat_history
        if isinstance(message, ToolResultMessage) and message.tool_call_id
    }
    preserved_tool_result_ids = {
        tool_call.call_id
        for message in removed_messages
        if isinstance(message, ModelOutputContextMessage)
        for tool_call in message.tool_calls
        if tool_call.call_id in tool_result_by_call_id
    }

    folded_message_by_index: dict[int, SessionBackedMessage] = {}
    for unit_kind, unit_id in removed_unit_ids:
        if unit_kind != "turn":
            continue
        unit_indexes = [
            index
            for index, message in enumerate(chat_history)
            if isinstance(message, ModelOutputContextMessage)
            and message.output_item.meta.logical_turn_id == unit_id
            and index in remove_indexes
        ]
        if not unit_indexes:
            continue
        unit_messages = [
            cast(ModelOutputContextMessage, chat_history[index])
            for index in unit_indexes
        ]
        folded_message = _build_trimmed_assistant_tool_user_message(
            unit_messages,
            tool_result_by_call_id=tool_result_by_call_id,
        )
        if folded_message is not None:
            folded_message_by_index[unit_indexes[0]] = folded_message

    optimized_history: list[LLMContextMessage] = []
    for index, message in enumerate(chat_history):
        if index in remove_indexes:
            if folded_message := folded_message_by_index.get(index):
                optimized_history.append(folded_message)
            continue
        if isinstance(message, ToolResultMessage) and message.tool_call_id in preserved_tool_result_ids:
            continue
        optimized_history.append(message)

    chat_history[:] = optimized_history
    return removed_messages


def _build_trimmed_assistant_tool_user_message(
    assistant_messages: list[ModelOutputContextMessage],
    *,
    tool_result_by_call_id: dict[str, ToolResultMessage],
) -> SessionBackedMessage | None:
    """把完整逻辑工具轮次折叠成一条普通 user 消息。"""

    tool_calls = [tool_call for message in assistant_messages for tool_call in message.tool_calls]
    if not tool_calls:
        return None

    tool_sections: list[str] = []
    for tool_call in tool_calls:
        if tool_call.func_name in TRIMMED_TOOL_CALL_DROP_NAMES:
            continue

        tool_result = tool_result_by_call_id.get(tool_call.call_id)
        if tool_call.func_name == "tool_search":
            tool_sections.append(_format_trimmed_tool_search_call(tool_call.args or {}, tool_result))
            continue

        args_text = dumps(tool_call.args or {}, ensure_ascii=False, sort_keys=True)
        section_lines = [
            f"- tool_call_id: {tool_call.call_id}",
            f"  tool_name: {tool_call.func_name}",
            f"  args: {args_text}",
        ]
        if tool_result is not None:
            result_status = "success" if tool_result.success else "failed"
            section_lines.extend(
                [
                    f"  result_status: {result_status}",
                    f"  result: {tool_result.content}",
                ]
            )
        tool_sections.append("\n".join(section_lines))

    if not tool_sections:
        return None

    folded_text = "[已折叠的历史工具调用]\n" + "\n".join(tool_sections)
    first_message = assistant_messages[0]
    logical_turn_id = first_message.output_item.meta.logical_turn_id
    if not logical_turn_id:
        raise ValueError("折叠工具历史时缺少 logical_turn_id")
    message_id = f"optimized_tool_history:{logical_turn_id}"
    if len(folded_text) > FOLDED_TOOL_COMPLEX_MESSAGE_THRESHOLD:
        return ComplexSessionMessage(
            raw_message=MessageSequence([TextComponent(folded_text)]),
            visible_text=folded_text,
            timestamp=first_message.timestamp,
            message_id=message_id,
            source_kind="optimized_tool_history",
            prompt_text=folded_text,
            complex_message_type="tool_history",
        )

    return SessionBackedMessage(
        raw_message=MessageSequence([TextComponent(folded_text)]),
        visible_text=folded_text,
        timestamp=first_message.timestamp,
        message_id=message_id,
        source_kind="optimized_tool_history",
    )


def _format_trimmed_tool_search_call(
    args: dict,
    tool_result: ToolResultMessage | None,
) -> str:
    """以更短的形式保留 tool_search 结果，供后续恢复 deferred tool 激活状态。"""

    query = str(args.get("query", "") or "").strip()
    matched_tool_names = _parse_tool_search_result_tool_names(tool_result.content if tool_result is not None else "")
    matched_text = ", ".join(matched_tool_names) if matched_tool_names else "无"
    if query:
        return f"- tool_search: {matched_text} (query={query})"
    return f"- tool_search: {matched_text}"


def _parse_tool_search_result_tool_names(content: str) -> list[str]:
    """从 tool_search 的结果文本中提取工具名，折叠时只保留最关键的信息。"""

    try:
        structured_content = loads(content)
    except (TypeError, ValueError):
        structured_content = None

    if isinstance(structured_content, dict):
        raw_tool_names = structured_content.get("matched_tool_names")
        if isinstance(raw_tool_names, list):
            return [str(tool_name).strip() for tool_name in raw_tool_names if str(tool_name).strip()]

    matched_tool_names: list[str] = []
    for raw_line in content.splitlines():
        normalized_line = raw_line.strip()
        if not normalized_line.startswith("- "):
            continue
        normalized_name = normalized_line[2:].split("（", 1)[0].strip()
        if normalized_name:
            matched_tool_names.append(normalized_name)
    return matched_tool_names


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

        unit_indexes = [index]
        logical_turn_id = _get_logical_turn_id(message)
        if logical_turn_id in tool_turn_ids:
            unit_indexes = [
                candidate_index
                for candidate_index, candidate in enumerate(chat_history)
                if _get_logical_turn_id(candidate) == logical_turn_id
            ]

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
