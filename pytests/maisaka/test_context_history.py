from datetime import datetime
from types import SimpleNamespace

import pytest

from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextItemMeta,
    ContextTextPart,
    ContextToolCall,
    FunctionCallItem,
    FunctionCallOutputItem,
    ReasoningItem,
    ReasoningRepresentation,
    SystemMessageItem,
    UserMessageItem,
)
from src.maisaka.context.history import (
    drop_unanswered_tool_calls,
    normalize_tool_call_result_pairs,
    normalize_tool_result_order,
)
from src.maisaka.context.messages import ModelOutputContextMessage, SessionBackedMessage, ToolResultMessage
from src.maisaka.context.post_processor import _trim_history_to_context_target
from src.maisaka.chat_loop_service import MaisakaChatLoopService
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.plugin_runtime.hook_payloads import serialize_prompt_items


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


def _user(content: str) -> SessionBackedMessage:
    return SessionBackedMessage(
        raw_message=MessageSequence([TextComponent(content)]),
        visible_text=content,
        timestamp=datetime.now(),
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


def test_context_selection_restores_user_anchor_before_tool_turn() -> None:
    trigger = _user("触发工具调用")
    call = _call("call-item", "call-1")
    result = _result("call-1")
    trailing = _user("最新消息")
    history = [trigger, call, result, trailing]

    selected, _ = MaisakaChatLoopService.select_llm_context_messages(
        history,
        request_kind="planner",
        max_context_size=1,
        enable_visual_message=False,
    )
    request_items, _ = MaisakaChatLoopService(chat_system_prompt="system")._build_request_messages(
        selected,
        enable_visual_message=False,
    )

    assert selected == history
    assert [type(item) for item in request_items[:5]] == [
        SystemMessageItem,
        UserMessageItem,
        FunctionCallItem,
        FunctionCallOutputItem,
        UserMessageItem,
    ]


def test_context_selection_restores_one_user_anchor_for_parallel_calls() -> None:
    trigger = _user("触发并行工具调用")
    first_call = _call("call-item-1", "call-1")
    second_call = _call("call-item-2", "call-2")
    first_result = _result("call-1")
    second_result = _result("call-2")
    trailing = _user("最新消息")
    history = [trigger, first_call, second_call, first_result, second_result, trailing]

    selected, _ = MaisakaChatLoopService.select_llm_context_messages(
        history,
        request_kind="planner",
        max_context_size=1,
        enable_visual_message=False,
    )
    request_items, _ = MaisakaChatLoopService(chat_system_prompt="system")._build_request_messages(
        selected,
        enable_visual_message=False,
    )

    assert selected == history
    assert [type(item) for item in request_items[:7]] == [
        SystemMessageItem,
        UserMessageItem,
        FunctionCallItem,
        FunctionCallItem,
        FunctionCallOutputItem,
        FunctionCallOutputItem,
        UserMessageItem,
    ]


@pytest.mark.parametrize("max_context_size", [1, 2, 3, 4])
def test_context_selection_keeps_tool_turn_anchors_across_window_boundaries(
    max_context_size: int,
) -> None:
    history = [
        _user("第一轮触发消息"),
        _call("call-item-1", "call-1", "turn-1"),
        _result("call-1", "turn-1"),
        _user("第二轮触发消息"),
        _call("call-item-2", "call-2", "turn-2"),
        _result("call-2", "turn-2"),
        _user("最新消息"),
    ]

    selected, _ = MaisakaChatLoopService.select_llm_context_messages(
        history,
        request_kind="planner",
        max_context_size=max_context_size,
        enable_visual_message=False,
    )
    request_items, _ = MaisakaChatLoopService(chat_system_prompt="system")._build_request_messages(
        selected,
        enable_visual_message=False,
    )

    assert isinstance(request_items[1], UserMessageItem)
    assert sum(isinstance(item, FunctionCallItem) for item in request_items) == sum(
        isinstance(item, FunctionCallOutputItem) for item in request_items
    )


def test_request_rejects_function_call_history_without_user_anchor() -> None:
    service = MaisakaChatLoopService(chat_system_prompt="system")

    with pytest.raises(ValueError, match="function call 缺少前置 user/function output 锚点"):
        service._build_request_messages(
            [_call("call-item", "call-1"), _result("call-1")],
            enable_visual_message=False,
        )


def test_day_boundary_hint_does_not_count_as_function_call_anchor() -> None:
    previous_day = datetime(2026, 7, 20, 23, 59, 59)
    next_day = datetime(2026, 7, 21, 0, 0, 1)
    history = [
        ModelOutputContextMessage(
            output_item=AssistantMessageItem(
                meta=ContextItemMeta.create(item_id="assistant", logical_turn_id="turn-0", timestamp=previous_day),
                parts=(ContextTextPart("前一天的回复"),),
            )
        ),
        ModelOutputContextMessage(
            output_item=FunctionCallItem(
                meta=ContextItemMeta.create(item_id="call-item", logical_turn_id="turn-1", timestamp=next_day),
                tool_call=ContextToolCall.create(call_id="call-1", func_name="lookup", args={}),
            )
        ),
        ToolResultMessage(
            content="result:call-1",
            timestamp=next_day,
            tool_call_id="call-1",
            tool_name="lookup",
            logical_turn_id="turn-1",
        ),
    ]
    service = MaisakaChatLoopService(chat_system_prompt="system")

    with pytest.raises(ValueError, match="function call 缺少前置 user/function output 锚点"):
        service._build_request_messages(
            history,
            enable_visual_message=False,
            include_day_boundary_time_messages=True,
        )


@pytest.mark.asyncio
async def test_before_request_hook_items_without_user_anchor_are_ignored(monkeypatch) -> None:
    captured_requests: list[list] = []

    class FakeLLMClient:
        async def generate_response_with_context(self, context_factory, options) -> LLMResponseResult:
            del options
            captured_requests.append(list(context_factory(None)))
            return LLMResponseResult.from_portable_output(response="好的", model_name="test-model")

    class UnanchoredToolTurnRuntimeManager:
        async def invoke_hook(self, hook_name: str, **kwargs: object) -> SimpleNamespace:
            if hook_name == "maisaka.planner.before_request":
                items = list(kwargs["items"])
                tool_turn_items = serialize_prompt_items(
                    [
                        _call("call-item", "call-1").output_item,
                        FunctionCallOutputItem(
                            meta=_meta("output-item"),
                            call_id="call-1",
                            output="result:call-1",
                            tool_name="lookup",
                        ),
                    ]
                )
                kwargs["items"] = [items[0], *tool_turn_items]
            return SimpleNamespace(kwargs=kwargs)

    service = MaisakaChatLoopService(chat_system_prompt="system")
    monkeypatch.setattr(service, "_get_llm_chat_client", lambda request_kind: FakeLLMClient())
    monkeypatch.setattr(
        MaisakaChatLoopService,
        "_get_runtime_manager",
        staticmethod(lambda: UnanchoredToolTurnRuntimeManager()),
    )
    monkeypatch.setattr(
        PromptCLIVisualizer,
        "build_prompt_section_result",
        staticmethod(
            lambda *args, **kwargs: SimpleNamespace(
                panel=None,
                preview_access=SimpleNamespace(preview_web_uri=""),
            )
        ),
    )

    await service.chat_loop_step([_user("最新消息")], tool_definitions=[])

    assert len(captured_requests) == 1
    assert not any(isinstance(item, FunctionCallItem) for item in captured_requests[0])


def test_history_trimming_keeps_user_and_following_tool_turn_together() -> None:
    trigger = _user("触发工具调用")
    call = _call("call-item", "call-1")
    result = _result("call-1")
    latest = _user("最新消息")
    history = [trigger, call, result, latest]

    removed = _trim_history_to_context_target(history, target_context_count=2)

    assert removed == [trigger, call, result]
    assert history == [latest]


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
