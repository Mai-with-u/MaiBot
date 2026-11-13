"""正文动作补全的轻量边界、工具协议和真实执行衔接。"""

from datetime import datetime
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, Mock

import asyncio
import json

import pytest

from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.common.prompt_i18n import load_prompt
from src.llm_models.exceptions import LLMTaskTimeoutError, RespParseException
from src.llm_models.payload_content.context_item import (
    ContextItem, ContextItemBuilder, ContextItemMeta, ContextToolCall, FunctionCallItem, RoleType, get_item_text,
)
from src.maisaka.builtin_tool.reply import get_tool_spec as reply_spec
from src.maisaka.builtin_tool.wait import get_tool_spec as wait_spec
from src.maisaka.chat_loop_service import MaisakaChatLoopService
from src.maisaka.context.messages import LLMContextMessage, ReferenceMessage, SessionBackedMessage, ToolResultMessage
from src.maisaka.display.display_utils import format_tool_call_for_display
from src.maisaka.focus.tools import routed_schema
from src.maisaka.planner_action_judge import build_recent_chat_context, complete_missing_planner_action
from src.maisaka.reasoning_engine import CycleRuntimeState, MaisakaReasoningEngine


def text_items(text: str) -> List[ContextItem]:
    return [ContextItemBuilder().set_role(RoleType.Assistant)
            .set_meta(ContextItemMeta.create(logical_turn_id="turn")).add_text_content(text).build()]


def tool_definitions() -> List[Any]:
    return [reply_spec().to_llm_definition(), wait_spec().to_llm_definition()]


def chat_message(index: int, *, notify: bool = False) -> SessionBackedMessage:
    timestamp = datetime(2026, 11, 13, 12, 0, index)
    sequence = MessageSequence([TextComponent(f"聊天正文{index}")])
    original = SimpleNamespace(
        session_id="real-session", is_notify=notify, timestamp=timestamp, message_id=f"message-{index}",
        raw_message=sequence,
        message_info=SimpleNamespace(user_info=SimpleNamespace(
            user_cardname="", user_nickname=f"发言人{index}", user_id=str(index),
        )),
    )
    return SessionBackedMessage(
        raw_message=sequence, visible_text=f"聊天正文{index}", timestamp=timestamp,
        message_id=f"message-{index}", original_message=original,
    )


@pytest.fixture
def judge_client(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    generate = AsyncMock(return_value=LLMResponseResult(
        output_items=tuple(text_items(json.dumps({"action": "reply"}))), model_name="fast",
    ))
    factory = Mock(return_value=SimpleNamespace(generate_response_with_context=generate))
    monkeypatch.setattr("src.maisaka.planner_action_judge.LLMServiceClient", factory)
    return SimpleNamespace(generate=generate, factory=factory)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["reply", "wait"])
async def test_one_short_request_becomes_standard_tool_call(judge_client: SimpleNamespace, action: str) -> None:
    judge_client.generate.return_value.output_items = tuple(text_items(json.dumps({"action": action})))
    original = "我决定回复刚才的问题，解释如何部署。"
    items = text_items(original)
    interrupt = asyncio.Event()
    judgment = await complete_missing_planner_action(
        items, tool_definitions(), session_id="real-session", logical_turn_id="turn", interrupt_flag=interrupt,
    )
    assert judgment is not None
    judge_client.factory.assert_called_once_with(
        "fast_model", request_type="maisaka.planner_action_judge", session_id="real-session",
    )
    judge_client.generate.assert_awaited_once()
    kwargs = judge_client.generate.await_args.kwargs
    options = kwargs["options"]
    assert options.max_tokens is None and options.temperature == 0
    assert options.tool_options is None and options.interrupt_flag is interrupt
    context = kwargs["context_factory"](None)
    assert len(context) == 2
    assert json.loads(get_item_text(context[1])) == {"recent_chat": [], "planner_text": original}
    call = judgment.tool_call
    assert call.meta.logical_turn_id == "turn"
    assert call.tool_call.func_name == action
    assert call.tool_call.materialize_args() == ({"reply_reference": original} if action == "reply" else {})
    assert format_tool_call_for_display({
        "name": action, "extra_content": call.tool_call.materialize_extra_content(),
    })["source_label"] == "轻量判断器补全"
    assert len(items) == 1


@pytest.mark.asyncio
async def test_full_planner_text_is_sent_without_truncation(judge_client: SimpleNamespace) -> None:
    content = "开头" + "分析" * 4000 + "最终决定回复。"
    result = await complete_missing_planner_action(
        text_items(content), tool_definitions(), session_id="real", logical_turn_id="turn",
    )
    context = judge_client.generate.await_args.kwargs["context_factory"](None)
    assert json.loads(get_item_text(context[1]))["planner_text"] == content
    assert result.tool_call.tool_call.materialize_args()["reply_reference"] == content


@pytest.mark.asyncio
async def test_only_last_five_real_chat_messages_reach_judge(judge_client: SimpleNamespace) -> None:
    history: List[LLMContextMessage] = []
    for index in range(7):
        history.extend([
            chat_message(index),
            ToolResultMessage(
                content="不要带给判断器的工具结果", timestamp=datetime.now(),
                tool_call_id=f"call-{index}", logical_turn_id="turn",
            ),
            ReferenceMessage(content="不要带给判断器的参考信息", timestamp=datetime.now()),
        ])
    history.append(chat_message(8, notify=True))
    await complete_missing_planner_action(
        text_items("应该回复"), tool_definitions(), session_id="real", logical_turn_id="turn", chat_history=history,
    )
    context = judge_client.generate.await_args.kwargs["context_factory"](None)
    recent = json.loads(get_item_text(context[1]))["recent_chat"]
    assert len(recent) == 5
    for message, index in zip(recent, range(2, 7), strict=True):
        assert message["session_id"] == "real-session"
        assert f"发言人{index}" in message["text"] and message["text"].endswith(f"聊天正文{index}")
    assert "不要带给判断器" not in get_item_text(context[1])
    assert build_recent_chat_context([chat_message(1)]) == [
        {"session_id": "real-session", "text": chat_message(1).render_visible_text()},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["empty", "existing_call", "no_reply", "no_wait"])
async def test_no_request_without_eligible_missing_action(judge_client: SimpleNamespace, case: str) -> None:
    items = [] if case == "empty" else text_items("我要回复")
    definitions = tool_definitions()
    if case == "existing_call":
        items.append(FunctionCallItem(
            meta=ContextItemMeta.create(logical_turn_id="turn"),
            tool_call=ContextToolCall.create(call_id="real-call", func_name="query_memory"),
        ))
    elif case == "no_reply":
        definitions = [wait_spec().to_llm_definition()]
    elif case == "no_wait":
        definitions = [reply_spec().to_llm_definition()]
    assert await complete_missing_planner_action(
        items, definitions, session_id="real", logical_turn_id="turn",
    ) is None
    judge_client.generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    "", "reply", "reply 1", "stop", '```json\n{"action":"reply","target":null}\n```',
    "null", "[]", '{"target":null}', '{"action":"stop","target":null}',
    '{"action":"reply","target":1}', '{"action":"wait","target":1}',
    '{"action":"reply","target":null,"extra":true}',
])
async def test_invalid_decision_is_exposed_without_retries(judge_client: SimpleNamespace, answer: str) -> None:
    judge_client.generate.return_value.output_items = tuple(text_items(answer)) if answer else ()
    with pytest.raises(RespParseException, match="无效"):
        await complete_missing_planner_action(text_items("回复吧"), tool_definitions(), session_id="s", logical_turn_id="t")
    judge_client.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_timeout_cancels_the_request_without_fabricating_wait(
    judge_client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = asyncio.Event()

    async def pending(**kwargs: Any) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    judge_client.generate.side_effect = pending
    monkeypatch.setattr("src.maisaka.planner_action_judge.JUDGE_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(LLMTaskTimeoutError):
        await complete_missing_planner_action(text_items("回复吧"), tool_definitions(), session_id="s", logical_turn_id="t")
    assert cancelled.is_set()
    judge_client.generate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,action,session", [
    ('{"action":"reply","session_id":"second"}', "reply", "second"),
    ('{"action":"wait"}', "wait", "first"),
])
async def test_focus_uses_only_real_routed_sessions(
    judge_client: SimpleNamespace, answer: str, action: str, session: str,
) -> None:
    definitions = tool_definitions()
    for definition, spec in zip(definitions, [reply_spec(), wait_spec()], strict=True):
        schema = routed_schema(spec, "first")
        schema["properties"]["session_id"]["enum"] = ["first", "second"]
        definition["parameters_schema"] = schema
    judge_client.generate.return_value.output_items = tuple(text_items(answer))
    result = await complete_missing_planner_action(
        text_items("回复 session_id=second 的问题"), definitions, session_id="", logical_turn_id="focus-turn",
    )
    assert result.tool_call.tool_call.func_name == action
    assert result.tool_call.tool_call.materialize_args()["session_id"] == session


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [None, True, 0, "unknown", "", [], {}])
async def test_focus_rejects_invalid_reply_targets(judge_client: SimpleNamespace, target: Any) -> None:
    definitions = tool_definitions()
    for definition, spec in zip(definitions, [reply_spec(), wait_spec()], strict=True):
        schema = routed_schema(spec, "first")
        schema["properties"]["session_id"]["enum"] = ["first", "second"]
        definition["parameters_schema"] = schema
    judge_client.generate.return_value.output_items = tuple(text_items(json.dumps({"action": "reply", "session_id": target})))
    with pytest.raises(RespParseException, match="无效选项"):
        await complete_missing_planner_action(
            text_items("回复第二个会话"), definitions, session_id="", logical_turn_id="focus-turn",
        )
    judge_client.generate.assert_awaited_once()


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP"])
def test_judge_prompt_is_available_in_supported_languages(locale: str) -> None:
    prompt = load_prompt("planner_action_judge", locale=locale)
    assert "reply" in prompt and "wait" in prompt and "planner_text" in prompt
    assert '{"action":"reply"}' in prompt
    assert "reply_sessions" in prompt and "session_id" in prompt
    assert '{"action":"wait"}' in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["reply", "wait"])
@pytest.mark.parametrize("mode,request_kind,expected", [
    ("maisaka_chat", "planner", True),
    ("maisaka_chat_focus", "planner", True),
    ("maisaka_chat_passive", "planner", False),
    ("maisaka_chat_lightweight", "planner", False),
    ("maisaka_chat", "sub_agent", False),
])
async def test_service_preview_and_execution_receive_the_same_completed_call(
    judge_client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, mode: str, request_kind: str,
    expected: bool, answer: str,
) -> None:
    judge_client.generate.return_value.output_items = tuple(text_items(json.dumps({"action": answer})))
    monkeypatch.setattr("src.maisaka.chat_loop_service.global_config.agent.planner_enabled", False)
    monkeypatch.setattr("src.maisaka.chat_loop_service.global_config.debug.save_maisaka_reasoning", True)
    preview = Mock(return_value=SimpleNamespace(
        preview_access=SimpleNamespace(preview_web_uri="preview"), panel="preview",
    ))
    monkeypatch.setattr("src.maisaka.chat_loop_service.PromptCLIVisualizer.build_prompt_section_result", preview)
    service = MaisakaChatLoopService(session_id="real", is_group_chat=True)
    service._resolve_enable_visual_message = Mock(return_value=False)
    selected_history = [chat_message(1)]
    service.select_llm_context_messages = Mock(return_value=(selected_history, "test"))
    service._build_request_messages = Mock(return_value=([
        ContextItemBuilder().set_role(RoleType.System).add_text_content("system").build(),
    ], 0))
    service._build_planner_final_user_reminder = Mock(return_value="")
    service._get_chat_prompt_name = Mock(return_value=mode)
    service._log_prompt_cache_usage = Mock()
    service._get_runtime_manager = Mock(return_value=SimpleNamespace(invoke_hook=AsyncMock(
        return_value=SimpleNamespace(kwargs={}),
    )))
    generate = AsyncMock(return_value=LLMResponseResult(
        output_items=tuple(text_items("应该回复群友刚才的提问。")), model_name="planner",
    ))
    service._get_llm_chat_client = Mock(return_value=SimpleNamespace(generate_response_with_context=generate))
    response = await service.chat_loop_step(
        [], tool_definitions=tool_definitions(), logical_turn_id="turn", request_kind=request_kind,
    )
    generate.assert_awaited_once()
    assert bool(response.tool_calls) is expected
    if not expected:
        judge_client.generate.assert_not_awaited()
        assert "planner_action_judge" not in preview.call_args.kwargs["metadata"]
        return

    judge_client.generate.assert_awaited_once()
    judge_context = judge_client.generate.await_args.kwargs["context_factory"](None)
    assert json.loads(get_item_text(judge_context[1]))["recent_chat"] == build_recent_chat_context(selected_history)
    assert response.tool_calls[0].func_name == answer
    assert response.tool_calls[0].args == ({"reply_reference": "应该回复群友刚才的提问。"} if answer == "reply" else {})
    assert preview.call_args.kwargs["output_items"] == response.output_items
    assert preview.call_args.kwargs["metadata"]["planner_action_judge"]["action"] == answer

    runtime = SimpleNamespace(
        _has_pending_messages=lambda: False, _chat_history=[], _end_planner_continuation=Mock(),
        _reset_consecutive_wait_count=Mock(), _enter_stop_state=Mock(),
    )
    engine = MaisakaReasoningEngine(runtime)
    engine._turn_passive_replyer = False
    engine._should_replace_reasoning = Mock(return_value=False)
    engine._emit_planner_progress = AsyncMock()
    engine._handle_tool_calls = AsyncMock(return_value=(True, "reply:wait:end", [], []))
    state = CycleRuntimeState()
    assert await engine._handle_planner_response_actions(
        response=response, cycle_detail=SimpleNamespace(time_records={}), state=state, planner_extra_lines=[],
    )
    if answer == "reply":
        assert engine._handle_tool_calls.await_args.args[0][0].call_id == response.tool_calls[0].call_id
        assert len(runtime._chat_history) == 2
    else:
        engine._handle_tool_calls.assert_not_awaited()
        runtime._enter_stop_state.assert_called_once()
        assert runtime._chat_history == []
