"""Maisaka 历史消息轮次结束后处理。"""

from dataclasses import dataclass
from json import dumps, loads
from math import ceil

from .history import drop_leading_orphan_tool_results, normalize_tool_call_result_pairs
from .messages import (
    AssistantMessage,
    ComplexSessionMessage,
    FOCUS_WAKEUP_SOURCE_KINDS,
    LLMContextMessage,
    SessionBackedMessage,
    ToolResultMessage,
)
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.maisaka.memory.mid_term import is_mid_term_memory_message

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
        processed_history
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
                processed_history
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
            processed_history
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
    assistant_indexes = [
        index
        for index, message in enumerate(chat_history)
        if isinstance(message, AssistantMessage)
    ]
    remove_count = len(assistant_indexes) - normalized_keep_count
    if remove_count <= 0:
        return []

    remove_indexes = set(assistant_indexes[:remove_count])
    removed_messages = [
        message
        for index, message in enumerate(chat_history)
        if index in remove_indexes
    ]
    tool_result_by_call_id = {
        message.tool_call_id: message
        for message in chat_history
        if isinstance(message, ToolResultMessage) and message.tool_call_id
    }
    preserved_tool_result_ids = {
        tool_call.call_id
        for message in removed_messages
        if isinstance(message, AssistantMessage)
        for tool_call in message.tool_calls
        if tool_call.call_id in tool_result_by_call_id
    }

    optimized_history: list[LLMContextMessage] = []
    for index, message in enumerate(chat_history):
        if index in remove_indexes:
            if isinstance(message, AssistantMessage):
                preserved_message = _build_trimmed_assistant_tool_user_message(
                    message,
                    tool_result_by_call_id=tool_result_by_call_id,
                )
                if preserved_message is not None:
                    optimized_history.append(preserved_message)
            continue
        if isinstance(message, ToolResultMessage) and message.tool_call_id in preserved_tool_result_ids:
            continue
        optimized_history.append(message)

    chat_history[:] = optimized_history
    return removed_messages


def _build_trimmed_assistant_tool_user_message(
    assistant_message: AssistantMessage,
    *,
    tool_result_by_call_id: dict[str, ToolResultMessage],
) -> SessionBackedMessage | None:
    """将被优化裁掉的 assistant 工具链折叠成普通 user 消息，避免破坏 tool 协议配对。"""

    if not assistant_message.tool_calls:
        return None

    tool_sections: list[str] = []
    for tool_call in assistant_message.tool_calls:
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
    message_id = f"optimized_tool_history:{assistant_message.timestamp.timestamp()}"
    if len(folded_text) > FOLDED_TOOL_COMPLEX_MESSAGE_THRESHOLD:
        return ComplexSessionMessage(
            raw_message=MessageSequence([TextComponent(folded_text)]),
            visible_text=folded_text,
            timestamp=assistant_message.timestamp,
            message_id=message_id,
            source_kind="optimized_tool_history",
            prompt_text=folded_text,
            complex_message_type="tool_history",
        )

    return SessionBackedMessage(
        raw_message=MessageSequence([TextComponent(folded_text)]),
        visible_text=folded_text,
        timestamp=assistant_message.timestamp,
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
) -> tuple[list[LLMContextMessage], int, int]:
    """规范化历史消息结构，保证工具调用链符合 LLM 消息协议。"""

    processed_history, normalize_stats = normalize_tool_call_result_pairs(chat_history)
    processed_history, leading_orphan_removed_count = drop_leading_orphan_tool_results(processed_history)
    removed_count = (
        normalize_stats["orphan_tool_results"]
        + normalize_stats["unanswered_tool_calls"]
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
    for index, message in enumerate(chat_history):
        if is_mid_term_memory_message(message):
            continue

        remove_indexes.append(index)
        if message.count_in_context:
            remaining_context_count -= 1
            if remaining_context_count <= target_context_count:
                break

    if not remove_indexes:
        return []

    removed_messages = [chat_history[index] for index in remove_indexes]
    for index in reversed(remove_indexes):
        del chat_history[index]
    return removed_messages
