"""Gemini 工具结果的角色映射回归测试。"""

from typing import Tuple

from src.llm_models.model_client.gemini_client import _convert_messages
from src.llm_models.payload_content.context_item import (
    ContextItemMeta,
    ContextTextPart,
    ContextToolCall,
    FunctionCallItem,
    FunctionCallOutputItem,
    RoleType,
    SystemMessageItem,
    UserMessageItem,
)


def _text_user(text: str) -> UserMessageItem:
    return UserMessageItem(meta=ContextItemMeta.create(), parts=(ContextTextPart(text),))


def _tool_round(call_id: str, func_name: str) -> Tuple[FunctionCallItem, FunctionCallOutputItem]:
    logical_turn_id = f"turn-{call_id}"
    return (
        FunctionCallItem(
            meta=ContextItemMeta.create(logical_turn_id=logical_turn_id),
            tool_call=ContextToolCall.create(call_id=call_id, func_name=func_name),
        ),
        FunctionCallOutputItem(
            meta=ContextItemMeta.create(logical_turn_id=logical_turn_id),
            call_id=call_id,
            output='{"ok": true}',
            tool_name=func_name,
        ),
    )


def test_tool_result_uses_user_role_for_gemini() -> None:
    call, output = _tool_round("call-1", "get_time")

    contents, system_instruction = _convert_messages(
        [
            SystemMessageItem(meta=ContextItemMeta.create(), parts=(ContextTextPart("system"),)),
            _text_user("现在几点？"),
            call,
            output,
        ]
    )

    assert system_instruction == "system"
    assert [content.role for content in contents] == ["user", "model", "user"]
    function_response = contents[-1].parts[0].function_response
    assert function_response is not None
    assert function_response.id == "call-1"
    assert function_response.name == "get_time"
    assert function_response.response == {"ok": True}
    assert output.role == RoleType.Tool


def test_multiple_tool_rounds_use_user_role_for_results() -> None:
    first_call, first_output = _tool_round("call-1", "get_time")
    second_call, second_output = _tool_round("call-2", "get_date")

    contents, _ = _convert_messages(
        [
            _text_user("先看时间再看日期"),
            first_call,
            first_output,
            second_call,
            second_output,
        ]
    )

    assert [content.role for content in contents] == ["user", "model", "user", "model", "user"]
    assert contents[2].parts[0].function_response.id == "call-1"
    assert contents[4].parts[0].function_response.id == "call-2"
