"""运行时 Hook 载荷序列化辅助。"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.llm_service_data_models import PromptMessage
from src.llm_models.payload_content.message import Message
from src.llm_models.payload_content.tool_option import ToolCall, ToolDefinitionInput, normalize_tool_options
from src.plugin_runtime.host.message_utils import PluginMessageUtils


def serialize_session_message(message: SessionMessage) -> Dict[str, Any]:
    """将会话消息序列化为 Hook 可传输载荷。

    Args:
        message: 待序列化的会话消息。

    Returns:
        Dict[str, Any]: 可通过插件运行时传输的消息字典。
    """

    return dict(PluginMessageUtils._session_message_to_dict(message))


def deserialize_session_message(raw_message: Any) -> SessionMessage:
    """从 Hook 载荷恢复会话消息。

    Args:
        raw_message: Hook 返回的消息字典。

    Returns:
        SessionMessage: 恢复后的会话消息对象。

    Raises:
        ValueError: 消息结构不合法时抛出。
    """

    if not isinstance(raw_message, dict):
        raise ValueError("Hook 返回的 `message` 必须是字典")
    return PluginMessageUtils._build_session_message_from_dict(raw_message)


def serialize_tool_calls(tool_calls: Sequence[ToolCall] | None) -> List[Dict[str, Any]]:
    """将工具调用列表序列化为 Hook 可传输载荷。

    Args:
        tool_calls: 原始工具调用列表。

    Returns:
        List[Dict[str, Any]]: 序列化后的工具调用列表。
    """

    if not tool_calls:
        return []

    return [
        {
            "id": tool_call.call_id,
            "function": {
                "name": tool_call.func_name,
                "arguments": dict(tool_call.args or {}),
            },
            **({"extra_content": tool_call.extra_content} if tool_call.extra_content else {}),
        }
        for tool_call in tool_calls
    ]


def deserialize_tool_calls(raw_tool_calls: Any) -> List[ToolCall]:
    """从 Hook 载荷恢复工具调用列表。

    Args:
        raw_tool_calls: Hook 返回的工具调用列表。

    Returns:
        List[ToolCall]: 恢复后的工具调用列表。

    Raises:
        ValueError: 结构不合法时抛出。
    """

    if raw_tool_calls in (None, []):
        return []
    if not isinstance(raw_tool_calls, list):
        raise ValueError("Hook 返回的 `tool_calls` 必须是列表")

    normalized_tool_calls: List[ToolCall] = []
    for raw_tool_call in raw_tool_calls:
        if not isinstance(raw_tool_call, dict):
            raise ValueError("Hook 返回的工具调用项必须是字典")

        function_info = raw_tool_call.get("function", {})
        if isinstance(function_info, dict):
            function_name = function_info.get("name")
            function_arguments = function_info.get("arguments")
        else:
            function_name = raw_tool_call.get("name")
            function_arguments = raw_tool_call.get("arguments")

        call_id = raw_tool_call.get("id") or raw_tool_call.get("call_id")
        if not isinstance(call_id, str) or not isinstance(function_name, str):
            raise ValueError("Hook 返回的工具调用缺少 `id` 或函数名称")

        extra_content = raw_tool_call.get("extra_content")
        normalized_tool_calls.append(
            ToolCall(
                call_id=call_id,
                func_name=function_name,
                args=function_arguments if isinstance(function_arguments, dict) else {},
                extra_content=extra_content if isinstance(extra_content, dict) else None,
            )
        )
    return normalized_tool_calls


def serialize_prompt_messages(messages: Sequence[Message]) -> List[PromptMessage]:
    """将 LLM 消息列表序列化为 Hook 可传输载荷。

    Args:
        messages: 原始 LLM 消息列表。

    Returns:
        List[PromptMessage]: 序列化后的消息字典列表。
    """

    serialized_messages: List[PromptMessage] = []
    for message in messages:
        serialized_message: PromptMessage = {
            "role": message.role.value,
            "content": message.content,
        }
        if message.tool_call_id:
            serialized_message["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            serialized_message["tool_calls"] = serialize_tool_calls(message.tool_calls)
        if message.reasoning_content:
            serialized_message["reasoning_content"] = message.reasoning_content
        serialized_messages.append(serialized_message)
    return serialized_messages


def deserialize_prompt_messages(raw_messages: Any) -> List[Message]:
    """从 Hook 载荷恢复 LLM 消息列表。

    Args:
        raw_messages: Hook 返回的消息列表。

    Returns:
        List[Message]: 恢复后的 LLM 消息列表。

    Raises:
        ValueError: 结构不合法时抛出。
    """

    if not isinstance(raw_messages, list):
        raise ValueError("Hook 返回的 `messages` 必须是列表")

    from src.services.llm_service import _build_message_from_dict

    normalized_messages: List[Message] = []
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            raise ValueError("Hook 返回的消息项必须是字典")
        normalized_messages.append(_build_message_from_dict(raw_message))
    return normalized_messages


def serialize_tool_definitions(tool_definitions: Sequence[ToolDefinitionInput]) -> List[Dict[str, Any]]:
    """将工具定义列表序列化为 Hook 可传输载荷。

    Args:
        tool_definitions: 原始工具定义列表。

    Returns:
        List[Dict[str, Any]]: 序列化后的工具定义列表。
    """

    normalized_tool_options = normalize_tool_options(list(tool_definitions))
    if not normalized_tool_options:
        return []
    return [tool_option.to_openai_function_schema() for tool_option in normalized_tool_options]
