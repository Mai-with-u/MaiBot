from datetime import datetime
from types import SimpleNamespace

import asyncio
import pytest

from src.core.tooling import ToolAvailabilityContext, ToolExecutionResult, ToolInvocation
from src.llm_models.payload_content.tool_option import ToolCall
from src.maisaka.builtin_tool import get_timing_tools
from src.maisaka.chat_loop_service import ChatResponse, MaisakaChatLoopService
from src.maisaka.context_messages import AssistantMessage, TIMING_GATE_INVALID_TOOL_HINT_SOURCE
from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.maisaka.runtime import MaisakaHeartFlowChatting


def _build_chat_response(tool_calls: list[ToolCall]) -> ChatResponse:
    return ChatResponse(
        content="The model returned an invalid timing tool.",
        tool_calls=tool_calls,
        request_messages=[],
        raw_message=AssistantMessage(
            content="",
            timestamp=datetime.now(),
            source_kind="perception",
        ),
        selected_history_count=1,
        tool_count=len(tool_calls),
        prompt_tokens=10,
        built_message_count=1,
        completion_tokens=3,
        total_tokens=13,
        prompt_section=None,
    )


def _build_runtime_stub(*, is_group_chat: bool) -> SimpleNamespace:
    return SimpleNamespace(
        _force_next_timing_continue=False,
        _chat_history=[],
        session_id="test-session",
        chat_stream=SimpleNamespace(
            session_id="test-session",
            stream_id="test-stream",
            is_group_session=is_group_chat,
            group_id="group-1" if is_group_chat else "",
            user_id="user-1",
            platform="qq",
        ),
        _chat_loop_service=SimpleNamespace(build_prompt_template_context=lambda: {}),
        log_prefix="[test]",
        stopped=False,
    )


def test_timing_gate_tools_expose_wait_only_in_private_chat() -> None:
    private_tool_names = {
        tool_definition["name"]
        for tool_definition in get_timing_tools(ToolAvailabilityContext(is_group_chat=False))
    }
    group_tool_names = {
        tool_definition["name"]
        for tool_definition in get_timing_tools(ToolAvailabilityContext(is_group_chat=True))
    }

    assert private_tool_names == {"continue", "no_reply", "wait"}
    assert group_tool_names == {"continue", "no_reply"}


@pytest.mark.asyncio
async def test_timing_gate_invalid_tool_defaults_to_no_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _build_runtime_stub(is_group_chat=True)

    def _enter_stop_state() -> None:
        runtime.stopped = True

    runtime._enter_stop_state = _enter_stop_state
    engine = MaisakaReasoningEngine(runtime)  # type: ignore[arg-type]

    call_count = 0

    async def _fake_timing_gate_sub_agent(**kwargs: object) -> ChatResponse:
        nonlocal call_count
        del kwargs
        call_count += 1
        return _build_chat_response([
            ToolCall(call_id="invalid-timing-tool", func_name="finish", args={}),
        ])

    async def _fail_invoke_tool_call(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("invalid timing tools must not be executed")

    monkeypatch.setattr(engine, "_run_timing_gate_sub_agent", _fake_timing_gate_sub_agent)
    monkeypatch.setattr(engine, "_invoke_tool_call", _fail_invoke_tool_call)

    action, response, tool_results, tool_monitor_results = await engine._run_timing_gate(object())  # type: ignore[arg-type]

    assert action == "no_reply"
    assert call_count == 3
    assert response.tool_calls[0].func_name == "finish"
    assert runtime.stopped is True
    assert tool_monitor_results == []
    assert len(runtime._chat_history) == 1
    assert runtime._chat_history[0].source == TIMING_GATE_INVALID_TOOL_HINT_SOURCE
    assert "finish" in runtime._chat_history[0].processed_plain_text
    assert tool_results == [
        "- retry [非法 Timing 工具]: 返回了 finish，将重试 (1/3)",
        "- retry [非法 Timing 工具]: 返回了 finish，将重试 (2/3)",
        "- no_reply [非法 Timing 工具]: 返回了 finish，已停止本轮并等待新消息",
    ]


@pytest.mark.asyncio
async def test_timing_gate_invalid_tool_retries_until_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _build_runtime_stub(is_group_chat=True)

    def _enter_stop_state() -> None:
        runtime.stopped = True

    runtime._enter_stop_state = _enter_stop_state
    engine = MaisakaReasoningEngine(runtime)  # type: ignore[arg-type]
    responses = [
        _build_chat_response([ToolCall(call_id="invalid-timing-tool", func_name="finish", args={})]),
        _build_chat_response([ToolCall(call_id="valid-timing-tool", func_name="continue", args={})]),
    ]

    async def _fake_timing_gate_sub_agent(**kwargs: object) -> ChatResponse:
        del kwargs
        return responses.pop(0)

    async def _fake_invoke_tool_call(
        tool_call: ToolCall,
        latest_thought: str,
        anchor_message: object,
        *,
        append_history: bool = True,
        store_record: bool = True,
    ) -> tuple[ToolInvocation, ToolExecutionResult, None]:
        del latest_thought, anchor_message, append_history, store_record
        return (
            ToolInvocation(tool_name=tool_call.func_name, call_id=tool_call.call_id),
            ToolExecutionResult(
                tool_name=tool_call.func_name,
                success=True,
                content="继续执行主流程",
                metadata={"timing_action": "continue"},
            ),
            None,
        )

    monkeypatch.setattr(engine, "_run_timing_gate_sub_agent", _fake_timing_gate_sub_agent)
    monkeypatch.setattr(engine, "_invoke_tool_call", _fake_invoke_tool_call)

    action, response, tool_results, tool_monitor_results = await engine._run_timing_gate(object())  # type: ignore[arg-type]

    assert action == "continue"
    assert response.tool_calls[0].func_name == "continue"
    assert runtime.stopped is False
    assert len(runtime._chat_history) == 2
    assert all(message.source != TIMING_GATE_INVALID_TOOL_HINT_SOURCE for message in runtime._chat_history)
    assert tool_results == [
        "- retry [非法 Timing 工具]: 返回了 finish，将重试 (1/3)",
        "- continue [成功]: 继续执行主流程",
    ]
    assert tool_monitor_results[0]["tool_name"] == "continue"


@pytest.mark.asyncio
async def test_timing_gate_group_chat_treats_wait_as_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _build_runtime_stub(is_group_chat=True)

    def _enter_stop_state() -> None:
        runtime.stopped = True

    runtime._enter_stop_state = _enter_stop_state
    engine = MaisakaReasoningEngine(runtime)  # type: ignore[arg-type]

    async def _fake_timing_gate_sub_agent(**kwargs: object) -> ChatResponse:
        tool_definitions = kwargs["tool_definitions"]
        assert {tool_definition["name"] for tool_definition in tool_definitions} == {"continue", "no_reply"}
        return _build_chat_response([
            ToolCall(call_id="disabled-wait", func_name="wait", args={"seconds": 3}),
        ])

    async def _fail_invoke_tool_call(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("群聊中禁用的 wait 不应被执行")

    monkeypatch.setattr(engine, "_run_timing_gate_sub_agent", _fake_timing_gate_sub_agent)
    monkeypatch.setattr(engine, "_invoke_tool_call", _fail_invoke_tool_call)

    action, _, tool_results, _ = await engine._run_timing_gate(object())  # type: ignore[arg-type]

    assert action == "no_reply"
    assert runtime.stopped is True
    assert tool_results[-1] == "- no_reply [非法 Timing 工具]: 返回了 wait，已停止本轮并等待新消息"


def test_timing_gate_invalid_tool_hint_keeps_only_latest() -> None:
    old_hint = SimpleNamespace(source=TIMING_GATE_INVALID_TOOL_HINT_SOURCE)
    runtime = SimpleNamespace(_chat_history=[old_hint])
    engine = MaisakaReasoningEngine(runtime)  # type: ignore[arg-type]

    engine._append_timing_gate_invalid_tool_hint("finish")
    engine._append_timing_gate_invalid_tool_hint("reply")

    assert len(runtime._chat_history) == 1
    hint_message = runtime._chat_history[0]
    assert hint_message.source == TIMING_GATE_INVALID_TOOL_HINT_SOURCE
    assert "reply" in hint_message.processed_plain_text
    assert "finish" not in hint_message.processed_plain_text


def test_timing_gate_invalid_tool_hint_only_visible_to_timing_gate() -> None:
    runtime = SimpleNamespace(_chat_history=[])
    engine = MaisakaReasoningEngine(runtime)  # type: ignore[arg-type]
    engine._append_timing_gate_invalid_tool_hint("finish")
    hint_message = runtime._chat_history[0]

    timing_history = MaisakaChatLoopService._filter_history_for_request_kind(
        [hint_message],
        request_kind="timing_gate",
    )
    planner_history = MaisakaChatLoopService._filter_history_for_request_kind(
        [hint_message],
        request_kind="planner",
    )

    assert timing_history == [hint_message]
    assert planner_history == []


def test_assistant_message_keeps_reasoning_content_for_llm_history() -> None:
    assistant_message = AssistantMessage(
        content="正式回复",
        timestamp=datetime.now(),
        reasoning_content="上一轮推理内容",
    )

    llm_message = assistant_message.to_llm_message()

    assert llm_message is not None
    assert llm_message.reasoning_content == "上一轮推理内容"


def test_timing_gate_filter_preserves_reasoning_content_when_trimming_tool_calls() -> None:
    timing_call = ToolCall(call_id="timing-call", func_name="continue", args={})
    planner_call = ToolCall(call_id="planner-call", func_name="reply", args={})
    assistant_message = AssistantMessage(
        content="正式回复",
        timestamp=datetime.now(),
        tool_calls=[timing_call, planner_call],
        reasoning_content="上一轮推理内容",
    )

    planner_history = MaisakaChatLoopService._filter_history_for_request_kind(
        [assistant_message],
        request_kind="planner",
    )

    assert len(planner_history) == 1
    filtered_message = planner_history[0]
    assert isinstance(filtered_message, AssistantMessage)
    assert filtered_message.reasoning_content == "上一轮推理内容"
    assert [tool_call.func_name for tool_call in filtered_message.tool_calls] == ["reply"]


def test_forced_timing_trigger_bypasses_message_frequency_threshold() -> None:
    runtime = SimpleNamespace(
        _STATE_WAIT="wait",
        _agent_state="stop",
        _message_turn_scheduled=False,
        _internal_turn_queue=asyncio.Queue(),
        _has_pending_messages=lambda: True,
        _get_pending_message_count=lambda: 1,
        _has_forced_timing_trigger=lambda: True,
        _cancel_deferred_message_turn_task=lambda: None,
    )

    def _fail_get_message_trigger_threshold() -> int:
        raise AssertionError("@/提及必回不应被普通聊天频率阈值拦住")

    runtime._get_message_trigger_threshold = _fail_get_message_trigger_threshold

    MaisakaHeartFlowChatting._schedule_message_turn(runtime)  # type: ignore[arg-type]

    assert runtime._message_turn_scheduled is True
    assert runtime._internal_turn_queue.get_nowait() == "message"


def test_finish_tool_is_not_written_back_to_history() -> None:
    finish_call = ToolCall(call_id="finish-call", func_name="finish", args={})
    reply_call = ToolCall(call_id="reply-call", func_name="reply", args={})
    assistant_message = AssistantMessage(
        content="当前不需要继续回复。",
        timestamp=datetime.now(),
        tool_calls=[finish_call, reply_call],
    )
    runtime = SimpleNamespace(_chat_history=[assistant_message])
    engine = MaisakaReasoningEngine(runtime)  # type: ignore[arg-type]

    engine._append_tool_execution_result(
        finish_call,
        ToolExecutionResult(
            tool_name="finish",
            success=True,
            content="当前对话循环已结束本轮思考，等待新的消息到来。",
        ),
    )

    assert runtime._chat_history == [assistant_message]
    assert [tool_call.func_name for tool_call in assistant_message.tool_calls] == ["reply"]


def test_finish_tool_removes_empty_assistant_history_message() -> None:
    finish_call = ToolCall(call_id="finish-call", func_name="finish", args={})
    assistant_message = AssistantMessage(
        content="",
        timestamp=datetime.now(),
        tool_calls=[finish_call],
    )
    runtime = SimpleNamespace(_chat_history=[assistant_message])
    engine = MaisakaReasoningEngine(runtime)  # type: ignore[arg-type]

    engine._append_tool_execution_result(
        finish_call,
        ToolExecutionResult(tool_name="finish", success=True),
    )

    assert runtime._chat_history == []


def test_timing_gate_head_trim_keeps_short_history() -> None:
    messages = [
        AssistantMessage(content="第一条消息", timestamp=datetime.now()),
        AssistantMessage(content="第二条消息", timestamp=datetime.now()),
    ]

    trimmed_messages = MaisakaHeartFlowChatting._drop_head_context_messages(
        messages,
        drop_context_count=3,
    )

    assert trimmed_messages == messages


def test_timing_gate_head_trim_keeps_history_within_config_limit() -> None:
    messages = [
        AssistantMessage(content=f"消息 {index}", timestamp=datetime.now())
        for index in range(10)
    ]

    trimmed_messages = MaisakaHeartFlowChatting._drop_head_context_messages(
        messages,
        drop_context_count=7,
        trim_threshold_context_count=10,
    )

    assert trimmed_messages == messages


def test_timing_gate_head_trim_applies_after_config_limit_exceeded() -> None:
    messages = [
        AssistantMessage(content=f"消息 {index}", timestamp=datetime.now())
        for index in range(11)
    ]

    trimmed_messages = MaisakaHeartFlowChatting._drop_head_context_messages(
        messages,
        drop_context_count=7,
        trim_threshold_context_count=10,
    )

    assert trimmed_messages == messages[7:]
