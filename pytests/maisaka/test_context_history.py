from datetime import datetime

from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextItemMeta,
    ContextTextPart,
    ContextToolCall,
    FunctionCallItem,
    ReasoningItem,
    ReasoningRepresentation,
)
from src.maisaka.context.history import (
    drop_unanswered_tool_calls,
    normalize_tool_call_result_pairs,
    normalize_tool_result_order,
)
from src.maisaka.context.messages import ModelOutputContextMessage, ToolResultMessage
from src.maisaka.context.post_processor import _build_trimmed_assistant_tool_user_message
from src.maisaka.chat_loop_service import MaisakaChatLoopService


def _meta(item_id: str, logical_turn_id: str = "turn-1") -> ContextItemMeta:
    return ContextItemMeta.create(
        item_id=item_id,
        logical_turn_id=logical_turn_id,
    )


def _call(
    item_id: str,
    call_id: str,
    logical_turn_id: str = "turn-1",
) -> ModelOutputContextMessage:
    return ModelOutputContextMessage(
        output_item=FunctionCallItem(
            meta=_meta(item_id, logical_turn_id),
            tool_call=ContextToolCall.create(
                call_id=call_id,
                func_name="lookup",
                args={"call_id": call_id},
            ),
        )
    )


def _result(call_id: str, logical_turn_id: str = "turn-1") -> ToolResultMessage:
    return ToolResultMessage(
        content=f"result:{call_id}",
        timestamp=datetime.now(),
        tool_call_id=call_id,
        tool_name="lookup",
        logical_turn_id=logical_turn_id,
    )


def test_normalize_tool_result_order_keeps_parallel_calls_together() -> None:
    first_call = _call("call-item-1", "call-1")
    second_call = _call("call-item-2", "call-2")
    first_result = _result("call-1")
    second_result = _result("call-2")

    normalized, moved_count = normalize_tool_result_order(
        [first_call, second_call, second_result, first_result]
    )

    assert normalized == [first_call, second_call, first_result, second_result]
    assert moved_count == 2


def test_drop_unanswered_parallel_call_removes_entire_tool_turn() -> None:
    reasoning = ModelOutputContextMessage(
        output_item=ReasoningItem(
            meta=_meta("reasoning"),
            text_parts=("先查询",),
            representation=ReasoningRepresentation.RAW_TEXT,
        )
    )
    answered_call = _call("call-item-1", "call-1")
    unanswered_call = _call("call-item-2", "call-2")
    assistant = ModelOutputContextMessage(
        output_item=AssistantMessageItem(
            meta=_meta("assistant"),
            parts=(ContextTextPart("查询中"),),
        )
    )
    result = _result("call-1")

    filtered, removed_count = drop_unanswered_tool_calls(
        [reasoning, answered_call, unanswered_call, assistant, result]
    )

    assert removed_count == 1
    assert filtered == []


def test_parallel_tool_turn_folding_preserves_call_order_and_stable_id() -> None:
    first_call = _call("call-item-1", "call-1")
    second_call = _call("call-item-2", "call-2")
    folded = _build_trimmed_assistant_tool_user_message(
        [first_call, second_call],
        tool_result_by_call_id={
            "call-2": _result("call-2"),
            "call-1": _result("call-1"),
        },
    )

    assert folded is not None
    assert folded.message_id == "optimized_tool_history:turn-1"
    assert folded.visible_text.index("tool_call_id: call-1") < folded.visible_text.index("tool_call_id: call-2")
    assert folded.visible_text.index("result:call-1") < folded.visible_text.index("result:call-2")


def test_context_selection_keeps_complete_tool_turn_beyond_window() -> None:
    reasoning = ModelOutputContextMessage(
        output_item=ReasoningItem(
            meta=_meta("reasoning"),
            text_parts=("先查询",),
            representation=ReasoningRepresentation.RAW_TEXT,
        )
    )
    assistant = ModelOutputContextMessage(
        output_item=AssistantMessageItem(
            meta=_meta("assistant"),
            parts=(ContextTextPart("查询中"),),
        )
    )
    history = [
        reasoning,
        _call("call-item-1", "call-1"),
        _call("call-item-2", "call-2"),
        assistant,
        _result("call-1"),
        _result("call-2"),
    ]

    selected, selection_reason = MaisakaChatLoopService.select_llm_context_messages(
        history,
        request_kind="planner",
        max_context_size=1,
        enable_visual_message=False,
    )

    assert selected == history
    assert "tool_turn_overflow" in selection_reason


def test_history_protocol_removes_both_turns_when_call_and_output_turns_mismatch() -> None:
    call = _call("call-item", "call-1", "turn-call")
    result = _result("call-1", "turn-output")

    normalized, stats = normalize_tool_call_result_pairs([call, result])

    assert normalized == []
    assert stats["invalid_tool_turns"] == 2


def test_history_protocol_keeps_registered_pending_call() -> None:
    call = _call("call-item", "wait-call", "turn-wait")

    normalized, stats = normalize_tool_call_result_pairs(
        [call],
        pending_call_ids={"wait-call"},
    )

    assert normalized == [call]
    assert stats["unanswered_tool_calls"] == 0
    assert stats["invalid_tool_turns"] == 0
