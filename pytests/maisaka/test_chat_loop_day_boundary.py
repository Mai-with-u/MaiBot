"""Maisaka Planner 跨日时间提示测试。"""

from datetime import datetime
from typing import List

from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextItem,
    ContextItemMeta,
    ContextTextPart,
    ContextToolCall,
    FunctionCallOutputItem,
    FunctionCallItem,
    RoleType,
    UserMessageItem,
    get_item_text,
)
from src.maisaka.chat_loop_service import MaisakaChatLoopService
from src.maisaka.context.messages import (
    LLMContextMessage,
    ReferenceMessage,
    ToolResultMessage,
    build_model_output_context_messages,
)


def _build_history_messages(history: List[LLMContextMessage]) -> List[ContextItem]:
    """构造请求并移除固定的 system 与末尾当前时间消息。"""

    service = MaisakaChatLoopService(chat_system_prompt="system")
    messages = service._build_request_messages(
        history,
        enable_visual_message=False,
        include_day_boundary_time_messages=True,
    )
    return messages[1:-1]


def _build_output_history(
    content: str,
    timestamp: datetime,
    tool_calls: list[tuple[str, str]],
) -> list[LLMContextMessage]:
    logical_turn_id = f"turn-{timestamp.timestamp()}"
    output_items = [
        AssistantMessageItem(
            meta=ContextItemMeta.create(
                logical_turn_id=logical_turn_id,
                timestamp=timestamp,
            ),
            parts=(ContextTextPart(content),),
        ),
        *[
            FunctionCallItem(
                meta=ContextItemMeta.create(
                    logical_turn_id=logical_turn_id,
                    timestamp=timestamp,
                ),
                tool_call=ContextToolCall.create(call_id=call_id, func_name=func_name),
            )
            for call_id, func_name in tool_calls
        ],
    ]
    return list(build_model_output_context_messages(output_items))


def test_application_history_envelope_keeps_one_stable_item_identity() -> None:
    reference = ReferenceMessage(content="长期参考", timestamp=datetime(2025, 1, 1, 12, 0, 0))
    tool_result = ToolResultMessage(
        content="工具结果",
        timestamp=datetime(2025, 1, 1, 12, 1, 0),
        tool_call_id="call-stable",
        logical_turn_id="turn-stable",
    )

    first_reference_item = reference.to_context_item()
    second_reference_item = reference.to_context_item()
    first_tool_item = tool_result.to_context_item()
    second_tool_item = tool_result.to_context_item()

    assert first_reference_item is not None
    assert second_reference_item is not None
    assert first_reference_item.meta.item_id == second_reference_item.meta.item_id
    assert first_tool_item.meta.item_id == second_tool_item.meta.item_id


def test_day_boundary_is_deferred_until_after_tool_result() -> None:
    history: List[LLMContextMessage] = [
        *_build_output_history(
            "调用表情工具",
            datetime(2026, 7, 20, 23, 59, 59),
            [("call_emoji", "send_emoji")],
        ),
        ToolResultMessage(
            content="表情包发送成功",
            timestamp=datetime(2026, 7, 21, 0, 0, 1),
            tool_call_id="call_emoji",
            tool_name="send_emoji",
            logical_turn_id=f"turn-{datetime(2026, 7, 20, 23, 59, 59).timestamp()}",
        ),
        ReferenceMessage(
            content="工具后的普通消息",
            timestamp=datetime(2026, 7, 21, 0, 0, 2),
            remaining_uses_value=None,
        ),
    ]

    messages = _build_history_messages(history)

    assert [type(message) for message in messages] == [
        AssistantMessageItem,
        FunctionCallItem,
        FunctionCallOutputItem,
        UserMessageItem,
        UserMessageItem,
    ]
    assert messages[2].call_id == "call_emoji"
    assert get_item_text(messages[3]) == "时间：2026-07-21 00:00:01"
    assert get_item_text(messages[4]) == "[参考消息]\n工具后的普通消息"


def test_day_boundary_is_deferred_until_after_all_tool_results() -> None:
    history: List[LLMContextMessage] = [
        *_build_output_history(
            "调用多个工具",
            datetime(2026, 7, 20, 23, 59, 59),
            [("call_first", "first_tool"), ("call_second", "second_tool")],
        ),
        ToolResultMessage(
            content="第一个工具执行成功",
            timestamp=datetime(2026, 7, 20, 23, 59, 59, 500000),
            tool_call_id="call_first",
            tool_name="first_tool",
            logical_turn_id=f"turn-{datetime(2026, 7, 20, 23, 59, 59).timestamp()}",
        ),
        ToolResultMessage(
            content="第二个工具执行成功",
            timestamp=datetime(2026, 7, 21, 0, 0, 1),
            tool_call_id="call_second",
            tool_name="second_tool",
            logical_turn_id=f"turn-{datetime(2026, 7, 20, 23, 59, 59).timestamp()}",
        ),
    ]

    messages = _build_history_messages(history)

    assert [type(message) for message in messages] == [
        AssistantMessageItem,
        FunctionCallItem,
        FunctionCallItem,
        FunctionCallOutputItem,
        FunctionCallOutputItem,
        UserMessageItem,
    ]
    assert [message.call_id for message in messages[3:5]] == ["call_first", "call_second"]
    assert get_item_text(messages[5]) == "时间：2026-07-21 00:00:01"


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
    assert get_item_text(messages[1]) == "时间：2026-07-21 00:00:01"
    assert get_item_text(messages[2]) == "[参考消息]\n跨日后消息"


def test_context_selection_drops_incomplete_tool_turn() -> None:
    output_history = _build_output_history(
        "一条正文",
        datetime(2026, 7, 20, 23, 59, 59),
        [("call_first", "first_tool"), ("call_second", "second_tool")],
    )
    trailing_message = ReferenceMessage(
        content="最新普通消息",
        timestamp=datetime(2026, 7, 21, 0, 0, 1),
        remaining_uses_value=None,
    )

    selected, _ = MaisakaChatLoopService.select_llm_context_messages(
        [*output_history, trailing_message],
        request_kind="planner",
        enable_visual_message=False,
        max_context_size=1,
    )

    assert selected == [trailing_message]
