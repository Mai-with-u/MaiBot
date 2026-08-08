"""Maisaka Planner 跨日时间提示测试。"""

from datetime import datetime
from typing import List

from src.llm_models.payload_content.message import Message, RoleType
from src.llm_models.payload_content.tool_option import ToolCall
from src.maisaka.chat_loop_service import MaisakaChatLoopService
from src.maisaka.context.messages import AssistantMessage, LLMContextMessage, ReferenceMessage, ToolResultMessage


def _build_history_messages(history: List[LLMContextMessage]) -> List[Message]:
    """构造请求并移除固定的 system 与末尾当前时间消息。"""

    service = MaisakaChatLoopService(chat_system_prompt="system")
    messages = service._build_request_messages(
        history,
        enable_visual_message=False,
        include_day_boundary_time_messages=True,
    )
    return messages[1:-1]


def test_day_boundary_is_deferred_until_after_tool_result() -> None:
    history: List[LLMContextMessage] = [
        AssistantMessage(
            content="调用表情工具",
            timestamp=datetime(2026, 7, 20, 23, 59, 59),
            tool_calls=[ToolCall(call_id="call_emoji", func_name="send_emoji", args={})],
        ),
        ToolResultMessage(
            content="表情包发送成功",
            timestamp=datetime(2026, 7, 21, 0, 0, 1),
            tool_call_id="call_emoji",
            tool_name="send_emoji",
        ),
        ReferenceMessage(
            content="工具后的普通消息",
            timestamp=datetime(2026, 7, 21, 0, 0, 2),
            remaining_uses_value=None,
        ),
    ]

    messages = _build_history_messages(history)

    assert [message.role for message in messages] == [
        RoleType.Assistant,
        RoleType.Tool,
        RoleType.User,
        RoleType.User,
    ]
    assert messages[1].tool_call_id == "call_emoji"
    assert messages[2].get_text_content() == "时间：2026-07-21 00:00:01"
    assert messages[3].get_text_content() == "[参考消息]\n工具后的普通消息"


def test_day_boundary_is_deferred_until_after_all_tool_results() -> None:
    history: List[LLMContextMessage] = [
        AssistantMessage(
            content="调用多个工具",
            timestamp=datetime(2026, 7, 20, 23, 59, 59),
            tool_calls=[
                ToolCall(call_id="call_first", func_name="first_tool", args={}),
                ToolCall(call_id="call_second", func_name="second_tool", args={}),
            ],
        ),
        ToolResultMessage(
            content="第一个工具执行成功",
            timestamp=datetime(2026, 7, 20, 23, 59, 59, 500000),
            tool_call_id="call_first",
            tool_name="first_tool",
        ),
        ToolResultMessage(
            content="第二个工具执行成功",
            timestamp=datetime(2026, 7, 21, 0, 0, 1),
            tool_call_id="call_second",
            tool_name="second_tool",
        ),
    ]

    messages = _build_history_messages(history)

    assert [message.role for message in messages] == [
        RoleType.Assistant,
        RoleType.Tool,
        RoleType.Tool,
        RoleType.User,
    ]
    assert [message.tool_call_id for message in messages[1:3]] == ["call_first", "call_second"]
    assert messages[3].get_text_content() == "时间：2026-07-21 00:00:01"


def test_day_boundary_stays_before_regular_context_message() -> None:
    history: List[LLMContextMessage] = [
        ReferenceMessage(
            content="跨日前消息",
            timestamp=datetime(2026, 7, 20, 23, 59, 59),
            remaining_uses_value=None,
        ),
        ReferenceMessage(
            content="跨日后消息",
            timestamp=datetime(2026, 7, 21, 0, 0, 1),
            remaining_uses_value=None,
        ),
    ]

    messages = _build_history_messages(history)

    assert [message.role for message in messages] == [RoleType.User, RoleType.User, RoleType.User]
    assert messages[1].get_text_content() == "时间：2026-07-21 00:00:01"
    assert messages[2].get_text_content() == "[参考消息]\n跨日后消息"
