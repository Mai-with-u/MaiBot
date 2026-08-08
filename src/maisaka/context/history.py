"""Maisaka 历史消息处理辅助工具。"""

from typing import TYPE_CHECKING

from src.common.data_models.message_component_data_model import MessageSequence, ReplyComponent, TextComponent

from src.maisaka.context.message_adapter import build_visible_text_from_sequence, clone_message_sequence, format_speaker_content
from .messages import AssistantMessage, LLMContextMessage, SessionBackedMessage, ToolResultMessage

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage

TOOL_RESULT_MEDIA_SOURCE_KIND = "tool_result_media"
OPTIMIZED_TOOL_HISTORY_SOURCE_KIND = "optimized_tool_history"


def build_prefixed_message_sequence(
    source_sequence: MessageSequence,
    planner_prefix: str,
) -> MessageSequence:
    """基于原始消息序列构造带规划器前缀的新序列。"""

    planner_components = [
        component
        for component in clone_message_sequence(source_sequence).components
        if not isinstance(component, ReplyComponent)
    ]
    if planner_components and isinstance(planner_components[0], TextComponent):
        planner_components[0].text = f"{planner_prefix}{planner_components[0].text}"
    else:
        planner_components.insert(0, TextComponent(planner_prefix))
    return MessageSequence(planner_components)


def build_session_message_visible_text(
    message: "SessionMessage",
    source_sequence: MessageSequence | None = None,
    *,
    include_reply_components: bool = True,
) -> str:
    """将真实会话消息转换为 Maisaka 可见文本。"""

    normalized_sequence = source_sequence if source_sequence is not None else message.raw_message
    user_info = message.message_info.user_info
    speaker_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
    visible_message_id = None if message.is_notify else message.message_id

    visible_sequence = MessageSequence([])
    visible_sequence.text(
        format_speaker_content(
            speaker_name,
            "",
            message.timestamp,
            visible_message_id,
        )
    )
    for component in clone_message_sequence(normalized_sequence).components:
        if not include_reply_components and isinstance(component, ReplyComponent):
            continue
        visible_sequence.components.append(component)
    return build_visible_text_from_sequence(visible_sequence).strip()


def drop_leading_orphan_tool_results(
    chat_history: list[LLMContextMessage],
) -> tuple[list[LLMContextMessage], int]:
    """移除历史前缀中缺少对应 tool_call 的工具结果消息。"""

    if not chat_history:
        return chat_history, 0

    available_tool_call_ids = {
        tool_call.call_id
        for message in chat_history
        if isinstance(message, AssistantMessage)
        for tool_call in message.tool_calls
        if tool_call.call_id
    }

    first_valid_index = 0
    while first_valid_index < len(chat_history):
        message = chat_history[first_valid_index]
        if not isinstance(message, ToolResultMessage):
            break
        if message.tool_call_id in available_tool_call_ids:
            break
        first_valid_index += 1

    if first_valid_index == 0:
        return chat_history, 0
    return chat_history[first_valid_index:], first_valid_index


def drop_orphan_tool_results(
    chat_history: list[LLMContextMessage],
) -> tuple[list[LLMContextMessage], int]:
    """移除窗口任意位置中缺少对应 tool_call 的工具结果消息。"""

    if not chat_history:
        return chat_history, 0

    available_tool_call_ids = _collect_available_tool_call_ids(chat_history)
    folded_tool_call_ids = _collect_folded_tool_history_call_ids(chat_history)
    available_media_owner_ids = available_tool_call_ids | folded_tool_call_ids

    filtered_history: list[LLMContextMessage] = []
    removed_count = 0
    for message in chat_history:
        if isinstance(message, ToolResultMessage) and message.tool_call_id not in available_tool_call_ids:
            removed_count += 1
            continue
        if _is_orphan_tool_result_media_message(message, available_media_owner_ids):
            removed_count += 1
            continue
        filtered_history.append(message)

    return filtered_history, removed_count


def normalize_tool_call_result_pairs(
    chat_history: list[LLMContextMessage],
) -> tuple[list[LLMContextMessage], dict[str, int]]:
    """统一清理并排序工具调用与工具结果，保证发送给模型的工具协议配对完整。"""

    processed_history, orphan_tool_result_count = drop_orphan_tool_results(chat_history)
    processed_history, unanswered_tool_call_count = drop_unanswered_tool_calls(processed_history)
    processed_history, moved_tool_result_count = normalize_tool_result_order(processed_history)
    return processed_history, {
        "orphan_tool_results": orphan_tool_result_count,
        "unanswered_tool_calls": unanswered_tool_call_count,
        "moved_tool_results": moved_tool_result_count,
    }


def drop_unanswered_tool_calls(
    chat_history: list[LLMContextMessage],
) -> tuple[list[LLMContextMessage], int]:
    """移除缺少对应工具结果的 assistant tool_call，避免破坏模型请求协议。"""

    if not chat_history:
        return chat_history, 0

    answered_tool_call_ids = {
        message.tool_call_id
        for message in chat_history
        if isinstance(message, ToolResultMessage) and message.tool_call_id
    }
    if not answered_tool_call_ids:
        return _drop_all_tool_calls_without_results(chat_history)

    filtered_history: list[LLMContextMessage] = []
    removed_count = 0
    for message in chat_history:
        if not isinstance(message, AssistantMessage) or not message.tool_calls:
            filtered_history.append(message)
            continue

        kept_tool_calls = [
            tool_call
            for tool_call in message.tool_calls
            if tool_call.call_id in answered_tool_call_ids
        ]
        removed_count += len(message.tool_calls) - len(kept_tool_calls)
        if kept_tool_calls:
            if len(kept_tool_calls) == len(message.tool_calls):
                filtered_history.append(message)
            else:
                filtered_history.append(
                    AssistantMessage(
                        content=message.content,
                        timestamp=message.timestamp,
                        tool_calls=kept_tool_calls,
                        source_kind=message.source_kind,
                    )
                )
            continue

        if message.content.strip():
            filtered_history.append(
                AssistantMessage(
                    content=message.content,
                    timestamp=message.timestamp,
                    tool_calls=[],
                    source_kind=message.source_kind,
                )
            )

    return filtered_history, removed_count


def _drop_all_tool_calls_without_results(
    chat_history: list[LLMContextMessage],
) -> tuple[list[LLMContextMessage], int]:
    """处理窗口内完全没有 tool result 的快捷路径。"""

    filtered_history: list[LLMContextMessage] = []
    removed_count = 0
    for message in chat_history:
        if not isinstance(message, AssistantMessage) or not message.tool_calls:
            filtered_history.append(message)
            continue

        removed_count += len(message.tool_calls)
        if message.content.strip():
            filtered_history.append(
                AssistantMessage(
                    content=message.content,
                    timestamp=message.timestamp,
                    tool_calls=[],
                    source_kind=message.source_kind,
                )
            )

    return filtered_history, removed_count


def _collect_available_tool_call_ids(chat_history: list[LLMContextMessage]) -> set[str]:
    """收集仍保留原始 assistant tool_calls 的工具调用 ID。"""

    return {
        tool_call.call_id
        for message in chat_history
        if isinstance(message, AssistantMessage)
        for tool_call in message.tool_calls
        if tool_call.call_id
    }


def _collect_folded_tool_history_call_ids(chat_history: list[LLMContextMessage]) -> set[str]:
    """收集已折叠工具历史中仍可作为媒体归属锚点的工具调用 ID。"""

    call_ids: set[str] = set()
    for message in chat_history:
        if not isinstance(message, SessionBackedMessage):
            continue
        if message.source_kind != OPTIMIZED_TOOL_HISTORY_SOURCE_KIND:
            continue

        call_ids.update(_parse_folded_tool_history_call_ids(message.processed_plain_text))
    return call_ids


def _parse_folded_tool_history_call_ids(content: str) -> set[str]:
    """从折叠后的工具历史文本中提取 tool_call_id。"""

    call_ids: set[str] = set()
    for raw_line in content.splitlines():
        normalized_line = raw_line.strip()
        if not normalized_line.startswith("- tool_call_id:"):
            continue

        _, _, call_id = normalized_line.partition(":")
        normalized_call_id = call_id.strip()
        if normalized_call_id:
            call_ids.add(normalized_call_id)
    return call_ids


def _is_orphan_tool_result_media_message(
    message: LLMContextMessage,
    available_media_owner_ids: set[str],
) -> bool:
    """判断 tool result 拆分出的媒体消息是否已经失去对应 tool_call。"""

    if not isinstance(message, SessionBackedMessage):
        return False
    if message.source_kind != TOOL_RESULT_MEDIA_SOURCE_KIND:
        return False

    message_id = str(message.message_id or "").strip()
    if not message_id.startswith("tool_result:"):
        return False

    _, _, remaining = message_id.partition("tool_result:")
    tool_call_id, _, _ = remaining.rpartition(":")
    return bool(tool_call_id) and tool_call_id not in available_media_owner_ids


def normalize_tool_result_order(
    chat_history: list[LLMContextMessage],
) -> tuple[list[LLMContextMessage], int]:
    """把被其他消息隔开的 tool 结果移动到对应 assistant tool_calls 后面。"""

    if not chat_history:
        return chat_history, 0

    consumed_indexes: set[int] = set()
    normalized_history: list[LLMContextMessage] = []
    moved_count = 0

    for index, message in enumerate(chat_history):
        if index in consumed_indexes:
            continue

        normalized_history.append(message)
        if not isinstance(message, AssistantMessage) or not message.tool_calls:
            continue

        appended_tool_result_count = 0
        for tool_call in message.tool_calls:
            tool_call_id = str(tool_call.call_id or "").strip()
            if not tool_call_id:
                continue

            matching_index = _find_tool_result_index(
                chat_history,
                tool_call_id=tool_call_id,
                start_index=index + 1,
                consumed_indexes=consumed_indexes,
            )
            if matching_index is None:
                continue

            consumed_indexes.add(matching_index)
            normalized_history.append(chat_history[matching_index])
            expected_index = index + appended_tool_result_count + 1
            if matching_index != expected_index:
                moved_count += 1
            appended_tool_result_count += 1

    if moved_count <= 0:
        return chat_history, 0
    return normalized_history, moved_count


def _find_tool_result_index(
    chat_history: list[LLMContextMessage],
    *,
    tool_call_id: str,
    start_index: int,
    consumed_indexes: set[int],
) -> int | None:
    """查找指定 tool_call_id 对应的 tool 结果消息位置。"""

    for index in range(start_index, len(chat_history)):
        if index in consumed_indexes:
            continue
        message = chat_history[index]
        if not isinstance(message, ToolResultMessage):
            continue
        if message.tool_call_id == tool_call_id:
            return index
    return None
