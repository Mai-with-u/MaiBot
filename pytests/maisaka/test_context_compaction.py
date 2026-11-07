"""摘要任务模式、严格校验及普通聊天提交链路的回归测试。"""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import ValidationError

import json
import pytest

from src.agent.config import AgentConfig
from src.agent.planner import ACPPlannerSnapshot
from src.chat.message_receive.message import SessionMessage
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.config.model_configs import ModelInfo
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.payload_content.context_item import (
    ContextItemBuilder,
    ContextItemMeta,
    ProviderActivityItem,
    RoleType,
    get_item_text,
)
from src.llm_models.payload_content.tool_option import ToolCall, ToolOption
from src.llm_models.request_snapshot import serialize_response_request_snapshot
from src.maisaka import reasoning_engine
from src.maisaka.context import compaction
from src.maisaka.context.messages import SessionBackedMessage, build_context_items_from_history_entry


FACT = "周五前提交 A 学校材料"
STATE = {
    "topics": ["研究生申请"],
    "background": ["已选定 A 学校"],
    "progress": [],
    "pending": [FACT],
    "agreements": [],
}


def _fixture():
    """构造旧摘要及十条原文，让关键事实只出现在即将裁切的部分。"""
    timestamp = datetime(2026, 11, 6, 12)
    previous = compaction.ContextSummaryMessage(content="用户打算申请研究生", timestamp=timestamp)
    messages = []
    for index in range(10):
        text = FACT if index == 0 else f"近期消息 {index}"
        original = SessionMessage(message_id=f"m{index}", timestamp=timestamp, platform="test")
        messages.append(
            SessionBackedMessage(
                original_message=original,
                message_id=original.message_id,
                raw_message=MessageSequence([TextComponent(text)]),
                visible_text=text,
                timestamp=timestamp + timedelta(seconds=index),
            )
        )
    history = [previous, *messages]
    items = [
        ContextItemBuilder().set_role(RoleType.User).add_text_content(m.processed_plain_text).build() for m in history
    ]
    request = ResponseRequest(
        model_info=ModelInfo(model_type="llm", name="test", model_identifier="test", api_provider="test"),
        context_items=items,
        tool_options=[ToolOption(name="stop", description="结束思考")],
    )
    snapshot = compaction.PlannerRequestSnapshot(request=request, history=tuple(history))
    return SimpleNamespace(previous=previous, messages=messages, history=history, request=request, snapshot=snapshot)


def _derive(fixture):
    """从原 Planner 快照派生覆盖前五条原文的摘要请求。"""
    return compaction._build_compaction_request(
        fixture.messages[:5],
        planner_snapshot=fixture.snapshot,
        previous_summary=fixture.previous,
        recent_messages=fixture.messages[5:],
    )


@pytest.mark.parametrize("has_tools", [True, False])
def test_native_summary_disables_tools_without_mutating_planner(has_tools):
    """摘要统一声明禁用工具，同时保留原快照和完整消息前缀。"""
    fixture = _fixture()
    if not has_tools:
        fixture.request.tool_options = None
    before = deepcopy(fixture.request)
    derived = _derive(fixture)
    assert derived.tool_choice == "none"
    assert derived.tool_options == before.tool_options
    assert derived.context_items[:-1] == before.context_items
    assert "不调用工具" in get_item_text(derived.context_items[-1])
    assert fixture.request == before
    assert derived is not fixture.request
    assert serialize_response_request_snapshot(derived)["tool_choice"] == "none"


@pytest.mark.parametrize("note", ["", " \t\n"])
def test_blank_note_is_ignored_without_mutating_input(note):
    """忽略无信息的 note 时，不改变调用方传入的摘要字典。"""
    payload = {**deepcopy(STATE), "note": note}
    before = deepcopy(payload)
    result = compaction.ConversationState.model_validate(payload)
    assert result.model_dump() == STATE
    assert payload == before


@pytest.mark.parametrize("extra", [{"note": "new fact"}, {"note": None}, {"note": []}, {"other": ""}])
def test_nonblank_or_unknown_extra_fields_remain_rejected(extra):
    """空 note 的兼容规则不接受其他附加字段或非空内容。"""
    with pytest.raises(ValidationError):
        compaction.ConversationState.model_validate({**STATE, **extra})


@pytest.mark.parametrize(
    "payload",
    [
        {key: value for key, value in STATE.items() if key != "pending"},
        {**STATE, "pending": "not a list"},
    ],
)
def test_blank_note_does_not_relax_required_fields(payload):
    """即使带空 note，摘要核心字段仍须齐全且类型正确。"""
    with pytest.raises(ValidationError):
        compaction.ConversationState.model_validate({**payload, "note": ""})


def _install_response(monkeypatch, result, before_return=None):
    """记录派生请求并注入固定结果，可在返回前模拟上下文变更。"""
    calls = []

    async def generate(_self, request):
        """观察真实摘要请求，在响应交回之前执行可选的并发操作。"""
        calls.append(request)
        if before_return:
            before_return()
        return result

    monkeypatch.setattr(compaction.LLMServiceClient, "generate_response_from_snapshot", generate)
    monkeypatch.setattr(compaction, "save_compaction_prompt_preview", lambda *args, **kwargs: None)
    return calls


async def _summarize(fixture):
    """沿真实摘要入口压缩前半段历史，保留旧摘要和近期原文参数。"""
    return await compaction.build_context_summary(
        fixture.messages[:5],
        planner_snapshot=fixture.snapshot,
        previous_summary=fixture.previous,
        recent_messages=fixture.messages[5:],
        session_id="test",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["invalid JSON", json.dumps({key: [] for key in STATE})])
async def test_invalid_or_empty_summary_remains_rejected(monkeypatch, payload):
    """非法 JSON 或没有任何有效条目的摘要不得作为压缩结果提交。"""
    _install_response(monkeypatch, LLMResponseResult.from_portable_output(response=payload))
    with pytest.raises(ValueError):
        await _summarize(_fixture())


@pytest.mark.asyncio
async def test_backend_ignoring_tool_choice_is_still_rejected(monkeypatch):
    """后端忽略禁用工具的约束时，函数调用响应仍须拒收。"""
    result = LLMResponseResult.from_portable_output(
        tool_calls=[ToolCall(call_id="stop-1", func_name="stop", args={})],
    )
    calls = _install_response(monkeypatch, result)
    with pytest.raises(ValueError, match="禁止的工具调用"):
        await _summarize(_fixture())
    assert calls[0].tool_choice == "none"


@pytest.mark.asyncio
async def test_native_tool_activity_remains_rejected_even_with_valid_summary(monkeypatch):
    """有效摘要文本不能掩盖后端同时返回的原生搜索活动。"""
    fixture = _fixture()
    fixture.request.tool_options = None
    fixture.request.extra_params = {"body": {"tools": [{"type": "web_search_preview"}]}}
    before = deepcopy(fixture.request)
    text_result = LLMResponseResult.from_portable_output(response=json.dumps(STATE))
    result = LLMResponseResult(
        output_items=(
            *text_result.output_items,
            ProviderActivityItem(
                meta=ContextItemMeta.create(logical_turn_id="native-search"),
                provider_type="web_search",
                call_id="search-1",
                status="completed",
            ),
        )
    )
    calls = _install_response(monkeypatch, result)
    with pytest.raises(ValueError, match="禁止的工具调用"):
        await _summarize(fixture)
    assert calls[0].tool_choice == "none"
    assert calls[0].extra_params == before.extra_params
    assert fixture.request == before


def _engine(fixture, monkeypatch):
    """保留真实收尾逻辑，仅替换与摘要提交无关的事件和学习服务。"""
    runtime = SimpleNamespace(
        _chat_history=list(fixture.history),
        _max_context_size=10,
        _get_pending_wait_tool_call_ids=lambda: set(),
        _chat_loop_service=SimpleNamespace(planner_request_snapshot=fixture.snapshot),
        session_id="test",
        log_prefix="[test]",
        _monitor_run_id="run-1",
        _log_history_trimmed=lambda *args: None,
        _trigger_trimmed_history_learning=AsyncMock(),
    )
    instance = object.__new__(reasoning_engine.MaisakaReasoningEngine)
    instance._runtime = runtime
    monkeypatch.setattr(reasoning_engine, "emit_flow_step", AsyncMock())
    return instance, runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{}, {"note": ""}])
async def test_summary_reaches_downstream_context_after_history_trim(monkeypatch, extra):
    """裁切前半段原文后，新摘要应保留事实并能转换成下游消息。"""
    fixture = _fixture()
    engine, runtime = _engine(fixture, monkeypatch)
    calls = _install_response(
        monkeypatch,
        LLMResponseResult.from_portable_output(
            response=json.dumps({**STATE, **extra}, ensure_ascii=False),
        ),
    )
    await engine._post_process_chat_history_after_cycle(SimpleNamespace(time_records={}, cycle_id="cycle-1"))
    summary, *recent = runtime._chat_history
    assert isinstance(summary, compaction.ContextSummaryMessage)
    assert FACT in summary.content
    assert summary is not fixture.previous
    assert all(a is b for a, b in zip(recent, fixture.messages[5:], strict=True))
    assert runtime._chat_loop_service.planner_request_snapshot is None
    assert len(calls) == 1
    assert calls[0].tool_choice == "none"
    runtime._trigger_trimmed_history_learning.assert_awaited_once()
    downstream_items = build_context_items_from_history_entry(summary)
    assert FACT in "\n".join(get_item_text(item) for item in downstream_items)


@pytest.mark.asyncio
async def test_clear_during_summary_still_cancels_commit(monkeypatch):
    """压缩期间清空历史时，过期摘要不得重新写回已清空的会话。"""
    fixture = _fixture()
    engine, runtime = _engine(fixture, monkeypatch)

    def clear():
        """模拟用户在模型响应返回前清空会话和 Planner 快照。"""
        runtime._chat_history = []
        runtime._chat_loop_service.planner_request_snapshot = None

    _install_response(monkeypatch, LLMResponseResult.from_portable_output(response=json.dumps(STATE)), clear)
    await engine._post_process_chat_history_after_cycle(SimpleNamespace(time_records={}, cycle_id="cycle-1"))
    assert runtime._chat_history == []
    runtime._trigger_trimmed_history_learning.assert_not_awaited()


@pytest.mark.asyncio
async def test_acp_summary_keeps_its_separate_tool_free_path(monkeypatch):
    """ACP 摘要继续使用无工具入口，并保持其历史标识和原快照。"""
    fixture = _fixture()
    original = ACPPlannerSnapshot(
        config=AgentConfig(), context_items=list(fixture.request.context_items), history_item_ids=["old"]
    )
    fixture.snapshot = compaction.PlannerRequestSnapshot(request=original, history=tuple(fixture.history))
    before = deepcopy(original)
    generate = AsyncMock(
        return_value=LLMResponseResult.from_portable_output(response=json.dumps({**STATE, "note": ""}))
    )
    monkeypatch.setattr(compaction, "generate_planner_response", generate)
    monkeypatch.setattr(compaction, "save_compaction_prompt_preview", lambda *args, **kwargs: None)
    result = await _summarize(fixture)
    assert FACT in result.content
    assert generate.call_args.args[3] == []
    assert generate.call_args.kwargs["history_item_ids"] == ["old"]
    assert original == before
