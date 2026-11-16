"""Maisaka 普通群聊门控 Hook、动态门控外部评分与单批静默消费的行为回归。"""

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, Mock

import asyncio

import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import GroupInfo, MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.maisaka.chat_loop_service import register_maisaka_hook_specs
from src.maisaka.context.messages import ContentContextMessage
from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.maisaka.runtime import MaisakaHeartFlowChatting
from src.maisaka.scenes.group.scheduling import GroupTurnScheduler, TURN_TRIGGER_EVALUATE_HOOK
from src.maisaka.scenes.private.scheduling import PrivateTurnScheduler
from src.maisaka.turn_trigger import gates as gates_module
from src.maisaka.turn_trigger import messages as messages_module
from src.maisaka.turn_trigger.dynamic_gate import DynamicGateDecision, DynamicReplyGate
from src.maisaka.turn_trigger.gates import DynamicReplyTurnGate, TurnGateResult
from src.plugin_runtime import integration as integration_module
from src.plugin_runtime.host.hook_dispatcher import HookDispatchResult
from src.plugin_runtime.host.hook_spec_registry import HookSpecRegistry


class _Control:
    """测试内可控的本地门控常量。"""

    def __init__(self, *, probability: float = 0.4, threshold: float = 0.55, frequency: float = 1.0) -> None:
        self.probability = probability
        self.threshold = threshold
        self.frequency = frequency


class _FakeManager:
    """替代插件运行时管理器：无订阅者返回原 kwargs，有插件返回 modified_kwargs 补丁。"""

    def __init__(
        self,
        *,
        modified: Optional[Dict[str, Any]] = None,
        entered: Optional[asyncio.Event] = None,
        release: Optional[asyncio.Event] = None,
        record: Optional[List[Any]] = None,
    ) -> None:
        self.modified = modified
        self.entered = entered
        self.release = release
        self.record = record if record is not None else []
        self.cancelled = False

    async def invoke_hook(self, hook_name: str, **kwargs: Any) -> HookDispatchResult:
        self.record.append((hook_name, dict(kwargs)))
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if self.modified is None:
            return HookDispatchResult(hook_name=hook_name, kwargs=dict(kwargs))
        return HookDispatchResult(hook_name=hook_name, kwargs=dict(self.modified))


def _make_message(mid: str, text: str = "内容", *, is_at: bool = False, is_mentioned: bool = False) -> SessionMessage:
    timestamp = datetime(2026, 1, 1, 12, 0, 0)
    message = SessionMessage(message_id=mid, timestamp=timestamp, platform="test", received_at=timestamp)
    message.session_id = "smoke-group"
    message.message_info = MessageInfo(
        user_info=UserInfo(user_id="u", user_nickname="测试用户"),
        group_info=GroupInfo(group_id="g", group_name="门控验证群"),
    )
    message.raw_message = MessageSequence([TextComponent(text)])
    message.processed_plain_text = text
    message.is_at = is_at
    message.is_mentioned = is_mentioned
    return message


def _build_runtime(messages: List[SessionMessage], control: _Control) -> MaisakaHeartFlowChatting:
    runtime = object.__new__(MaisakaHeartFlowChatting)
    runtime.message_cache = list(messages)
    runtime._last_processed_index = 0
    runtime._chat_history = []
    runtime._running = True
    runtime._focus_group = None
    runtime._agent_state = runtime._STATE_STOP
    runtime._message_turn_changed = asyncio.Event()
    runtime._internal_turn_queue = asyncio.Queue()
    runtime._cycle_counter = 1
    runtime._message_turn_scheduled = False
    runtime._pending_wait_started_at = None
    runtime._pending_wait_seconds = None
    runtime._pending_wait_tool_call_id = None
    runtime._pending_wait_logical_turn_id = None
    runtime._proactive_trigger_message = None
    runtime._proactive_logical_turn_id = None
    runtime.session_id = "smoke-group"
    runtime.log_prefix = "[test]"
    runtime.chat_stream = SimpleNamespace(is_group_session=True)
    runtime._fixed_message_debouncer = SimpleNamespace(consume=Mock())
    runtime._chat_loop_service = SimpleNamespace(planner_request_snapshot=None)
    runtime._message_turn_scheduler = SimpleNamespace(
        record_message_consumed=Mock(),
        accepted_wait_started_at=None,
    )
    runtime._get_effective_reply_frequency = lambda: control.frequency
    runtime._is_reply_frequency_silent = lambda: control.frequency <= 0
    runtime._has_forced_turn_trigger = lambda: False
    runtime._is_focus_mode_active_for_current_chat = lambda: False
    runtime._has_pending_messages = lambda: runtime._last_processed_index < len(runtime.message_cache)
    runtime._get_pending_message_count = lambda: len(runtime.message_cache) - runtime._last_processed_index
    runtime._get_message_quiet_period_remaining_seconds = lambda: 0.0
    runtime._has_pending_wait_tool_call = lambda: False
    runtime._get_pending_wait_tool_call_ids = lambda: set()
    runtime._is_message_debounce_enabled = lambda: False
    runtime._wait_for_message_quiet_period = AsyncMock()
    runtime._mark_message_turn_unscheduled = Mock()
    runtime._defer_message_turn_check = Mock()
    runtime._schedule_message_turn = Mock()
    runtime._enqueue_message_turn = Mock()
    runtime._prune_processed_message_cache = Mock()
    runtime._cancel_wait_timeout_task = Mock()
    runtime._update_stage_status = Mock()
    runtime._cancel_deferred_message_turn_task = Mock()
    runtime._enter_running_state = Mock()
    runtime._enter_stop_state = Mock()
    return runtime


def _install(monkeypatch: pytest.MonkeyPatch, control: _Control, manager: _FakeManager) -> None:
    """把外部依赖固定为确定性输入，宿主门控/调度/消费方法保持真实实现。"""

    monkeypatch.setattr(messages_module, "get_online_bot_accounts", lambda platform: set())
    monkeypatch.setattr(gates_module, "estimate_reply_probability", lambda inp: control.probability)

    def fake_low_evaluate(self: DynamicReplyGate, probability: float, frequency: float, now: float) -> DynamicGateDecision:
        return DynamicGateDecision(
            should_trigger=probability >= control.threshold,
            probability=probability,
            threshold=control.threshold,
            expected_replies=0.0,
            target_replies=0.0,
            actual_replies=0,
            keep_ratio=1.0,
        )

    monkeypatch.setattr(DynamicReplyGate, "evaluate", fake_low_evaluate)
    monkeypatch.setattr(integration_module, "get_plugin_runtime_manager", lambda: manager)


def _build_scheduler(monkeypatch: pytest.MonkeyPatch, runtime: MaisakaHeartFlowChatting, control: _Control, manager: _FakeManager) -> GroupTurnScheduler:
    _install(monkeypatch, control, manager)
    return GroupTurnScheduler(runtime)


# ────────────────────────── Hook 规格与门控结果语义 ──────────────────────────


def test_turn_trigger_evaluate_hook_spec_registered() -> None:
    registry = HookSpecRegistry()
    register_maisaka_hook_specs(registry)
    spec = registry.get_hook_spec(TURN_TRIGGER_EVALUATE_HOOK)
    assert spec is not None
    assert spec.allow_abort is False
    assert spec.allow_kwargs_mutation is True
    assert spec.allow_blocking is True
    assert spec.allow_observe is True
    assert spec.default_timeout_ms == 5000
    assert set(spec.parameters_schema["required"]) == {
        "session_id", "is_group_session", "frequency", "probability", "threshold",
        "decision", "detail", "delay_seconds", "consumed_count",
        "pending_messages", "recent_messages",
    }
    assert set(spec.parameters_schema["properties"]["decision"]["enum"]) == {"trigger", "wait", "delay", "consume"}
    assert "review" not in spec.parameters_schema["properties"]["decision"]["enum"]


def test_turn_gate_result_should_trigger_only_for_trigger() -> None:
    assert TurnGateResult(decision="trigger", detail="").should_trigger is True
    for decision in ("wait", "delay", "consume", "review"):
        assert TurnGateResult(decision=decision, detail="").should_trigger is False


# ────────────────────────── 动态门控外部评分 ──────────────────────────


def test_evaluate_finalize_controls_round_and_exposes_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control(probability=0.8)
    runtime = _build_runtime([_make_message("A")], control)
    _install(monkeypatch, control, _FakeManager())
    gate = DynamicReplyTurnGate(runtime)

    open_result = gate.evaluate(pending_messages=[_make_message("B")], frequency=1.0, finalize=False)
    assert open_result.decision == "trigger"
    assert open_result.probability == pytest.approx(0.8)
    assert open_result.threshold == pytest.approx(0.55)
    assert open_result.evaluated_at > 0
    assert gate._gate._open_round_started_at is not None  # 未提前关闭

    gate2 = DynamicReplyTurnGate(runtime)
    closed_result = gate2.evaluate(pending_messages=[_make_message("C")], frequency=1.0, finalize=True)
    assert closed_result.decision == "trigger"
    assert gate2._gate._open_round_started_at is None  # finalize=True 关闭轮次


def test_apply_external_probability_decision_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control(probability=0.8)
    runtime = _build_runtime([_make_message("A")], control)
    _install(monkeypatch, control, _FakeManager())
    gate = DynamicReplyTurnGate(runtime)

    # 未显式 decision：按概率与阈值比较
    assert gate.apply_external_probability(probability=0.9, frequency=1.0, evaluated_at=100.0).decision == "trigger"
    assert gate.apply_external_probability(probability=0.2, frequency=1.0, evaluated_at=100.0).decision == "wait"

    # 显式 decision 优先于比较结果
    wait_res = gate.apply_external_probability(probability=0.9, frequency=1.0, evaluated_at=100.0, decision_override="wait")
    assert wait_res.decision == "wait"
    trigger_res = gate.apply_external_probability(probability=0.1, frequency=1.0, evaluated_at=100.0, decision_override="trigger")
    assert trigger_res.decision == "trigger"

    # delay 直接返回、不关闭轮次
    delay_res = gate.apply_external_probability(probability=0.9, frequency=1.0, evaluated_at=100.0, decision_override="delay", delay_seconds=3.0)
    assert delay_res.decision == "delay" and delay_res.delay_seconds == pytest.approx(3.0)

    # consume 透传 consumed_count，交回消费成功后关闭
    consume_res = gate.apply_external_probability(probability=0.1, frequency=1.0, evaluated_at=100.0, decision_override="consume", consumed_count=2)
    assert consume_res.decision == "consume" and consume_res.consumed_count == 2

    # threshold_override 生效
    override_res = gate.apply_external_probability(probability=0.4, frequency=1.0, evaluated_at=100.0, threshold_override=0.7)
    assert override_res.threshold == pytest.approx(0.7) and override_res.decision == "wait"


# ────────────────────────── review 分支调度 ──────────────────────────


def test_schedule_review_enqueues_message_turn_without_wait_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control()
    runtime = _build_runtime([_make_message("A")], control)
    scheduler = _build_scheduler(monkeypatch, runtime, control, _FakeManager())
    monkeypatch.setattr(scheduler, "_evaluate", lambda pending_count: TurnGateResult(decision="review", detail="调度=插件门控评估"))

    scheduler.schedule_message_turn()

    runtime._enqueue_message_turn.assert_called_once_with()
    runtime._update_stage_status.assert_not_called()
    runtime._defer_message_turn_check.assert_not_called()


def test_private_scheduler_inherits_no_review(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control()
    runtime = _build_runtime([_make_message("A")], control)
    _install(monkeypatch, control, _FakeManager())
    scheduler = PrivateTurnScheduler(runtime)

    result = asyncio.run(scheduler.review_message_turn([_make_message("A")]))
    assert result is None
    assert scheduler.record_message_consumed() is None


# ────────────────────────── review_message_turn 行为 ──────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("probability,expected", [(0.4, "wait"), (0.8, "trigger")])
async def test_review_no_subscriber_preserves_local_decision(monkeypatch: pytest.MonkeyPatch, probability: float, expected: str) -> None:
    control = _Control(probability=probability)
    messages = [_make_message("A"), _make_message("B")]
    runtime = _build_runtime(messages, control)
    scheduler = _build_scheduler(monkeypatch, runtime, control, _FakeManager())

    result = await scheduler.review_message_turn(messages)
    assert result is not None
    assert result.decision == expected
    assert runtime._last_processed_index == 0
    assert runtime._chat_history == []


@pytest.mark.asyncio
async def test_review_payload_has_only_generic_fields_and_full_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control(probability=0.4)
    messages = [_make_message(f"M{i}", f"内容-{i}", is_at=(i == 0), is_mentioned=(i == 1)) for i in range(31)]
    runtime = _build_runtime(messages, control)
    runtime._chat_history = [
        SimpleNamespace(count_in_context=True, processed_plain_text="[群友A]早上好", source="user", timestamp=None, received_at=None),
        SimpleNamespace(count_in_context=True, processed_plain_text="[麦麦]大家好", source="guided_reply", timestamp=None, received_at=None),
        SimpleNamespace(count_in_context=False, processed_plain_text="[忽略]内部", source="internal", timestamp=None, received_at=None),
    ]
    record: List[Any] = []
    manager = _FakeManager(record=record)
    scheduler = _build_scheduler(monkeypatch, runtime, control, manager)

    result = await scheduler.review_message_turn(messages)
    assert result is not None and result.decision == "wait"

    hook_name, payload = record[0]
    assert hook_name == TURN_TRIGGER_EVALUATE_HOOK
    assert len(payload["pending_messages"]) == 31
    assert payload["pending_messages"][0]["is_at"] is True
    assert payload["pending_messages"][1]["is_mentioned"] is True
    assert payload["consumed_count"] == 31
    assert payload["is_group_session"] is True
    # 通用载荷不携带任何 Jev 服务商字段
    forbidden = {"api_key", "base_url", "model", "confidence", "jev", "profiles", "shadow_mode"}
    assert not (forbidden & set(payload))
    # 最近历史取 count_in_context 为真者，按 source 标注 is_self
    assert payload["recent_messages"] == [
        {"sender": "群友A", "text": "早上好", "is_self": False},
        {"sender": "麦麦", "text": "大家好", "is_self": True},
    ]


@pytest.mark.asyncio
async def test_review_modified_kwargs_patch_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    """插件只返回补丁字段时，缺失字段沿用 Hook 输入原值。"""
    control = _Control(probability=0.4)
    messages = [_make_message("A"), _make_message("B")]
    runtime = _build_runtime(messages, control)
    manager = _FakeManager(modified={"probability": 0.95, "threshold": 0.55, "detail": "融合"})
    scheduler = _build_scheduler(monkeypatch, runtime, control, manager)

    result = await scheduler.review_message_turn(messages)
    assert result is not None and result.decision == "trigger"  # 概率升高越过阈值


@pytest.mark.asyncio
async def test_review_fusion_upgrades_wait_and_downgrades_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    # wait 升到 0.9 → trigger 且关闭轮次
    runtime = _build_runtime([_make_message("A")], _Control(probability=0.4))
    scheduler = _build_scheduler(monkeypatch, runtime, _Control(probability=0.4), _FakeManager(modified={"probability": 0.9}))
    up = await scheduler.review_message_turn(runtime.message_cache)
    assert up is not None and up.decision == "trigger"
    assert scheduler._dynamic_reply_gate._gate._open_round_started_at is None

    # trigger 降到 0.1 → wait，轮次不提前关闭
    runtime2 = _build_runtime([_make_message("B")], _Control(probability=0.8))
    scheduler2 = _build_scheduler(monkeypatch, runtime2, _Control(probability=0.8), _FakeManager(modified={"probability": 0.1}))
    down = await scheduler2.review_message_turn(runtime2.message_cache)
    assert down is not None and down.decision == "wait"
    assert scheduler2._dynamic_reply_gate._gate._open_round_started_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,expected", [(0, 3), (2, 2), (99, 3)])
async def test_review_consumed_count_sanitization(monkeypatch: pytest.MonkeyPatch, raw: int, expected: int) -> None:
    control = _Control(probability=0.2)
    messages = [_make_message("A"), _make_message("B"), _make_message("C")]
    runtime = _build_runtime(messages, control)
    manager = _FakeManager(modified={"decision": "consume", "consumed_count": raw})
    scheduler = _build_scheduler(monkeypatch, runtime, control, manager)

    result = await scheduler.review_message_turn(messages)
    assert result is not None and result.decision == "consume"
    assert result.consumed_count == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "modified",
    [
        {"probability": True},
        {"probability": 1.5},
        {"probability": "0.4"},
        {"threshold": float("nan")},
        {"decision": "review"},
        {"delay_seconds": -1.0},
        {"consumed_count": True},
        {"consumed_count": -5},
    ],
)
async def test_review_illegal_kwargs_raise(monkeypatch: pytest.MonkeyPatch, modified: Dict[str, Any]) -> None:
    control = _Control(probability=0.4)
    messages = [_make_message("A")]
    runtime = _build_runtime(messages, control)
    scheduler = _build_scheduler(monkeypatch, runtime, control, _FakeManager(modified=modified))

    with pytest.raises(ValueError):
        await scheduler.review_message_turn(messages)


@pytest.mark.asyncio
async def test_review_event_invalidation_cancels_hook_without_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control(probability=0.2)
    messages = [_make_message("A"), _make_message("B")]
    runtime = _build_runtime(messages, control)
    entered = asyncio.Event()
    release = asyncio.Event()
    manager = _FakeManager(modified={"decision": "consume", "consumed_count": 2}, entered=entered, release=release)
    scheduler = _build_scheduler(monkeypatch, runtime, control, manager)

    task = asyncio.create_task(scheduler.review_message_turn(messages))
    await asyncio.wait_for(entered.wait(), timeout=2.0)  # hook 已进入、clear 已执行
    runtime._message_turn_changed.set()  # 模拟新消息登记
    result = await asyncio.wait_for(task, timeout=2.0)

    assert result is not None and result.decision == "delay" and result.delay_seconds == 0.0
    assert manager.cancelled is True  # 在途 hook 子任务被取消，无悬挂任务
    assert runtime._last_processed_index == 0 and runtime._chat_history == []  # 旧结果未提交


@pytest.mark.asyncio
async def test_review_batch_identity_change_keeps_new_message(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control(probability=0.2)
    messages = [_make_message("A"), _make_message("B")]
    runtime = _build_runtime(messages, control)
    entered = asyncio.Event()
    release = asyncio.Event()
    manager = _FakeManager(modified={"decision": "consume", "consumed_count": 2}, entered=entered, release=release)
    scheduler = _build_scheduler(monkeypatch, runtime, control, manager)

    task = asyncio.create_task(scheduler.review_message_turn(messages))
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    runtime.message_cache.append(_make_message("C"))  # 评估期间新增消息
    release.set()
    result = await asyncio.wait_for(task, timeout=2.0)

    assert result is not None and result.decision == "delay"
    assert runtime._last_processed_index == 0 and runtime._chat_history == []
    assert [m.message_id for m in runtime.message_cache] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_review_short_circuits_empty_forced_and_zero_frequency(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control(probability=0.4)
    runtime = _build_runtime([], control)
    manager = _FakeManager()
    scheduler = _build_scheduler(monkeypatch, runtime, control, manager)

    empty = await scheduler.review_message_turn([])
    assert empty is not None and empty.decision == "wait"

    # 强制触发直接放行，不调用插件
    runtime._has_forced_turn_trigger = lambda: True
    forced = await scheduler.review_message_turn([_make_message("A")])
    assert forced is not None and forced.decision == "trigger"
    runtime._has_forced_turn_trigger = lambda: False

    # 频率归零返回 delay=0
    runtime._get_effective_reply_frequency = lambda: 0.0
    zero = await scheduler.review_message_turn([_make_message("A")])
    assert zero is not None and zero.decision == "delay" and zero.delay_seconds == 0.0
    assert manager.record == []  # 上述路径都不应触发 Hook


@pytest.mark.asyncio
async def test_review_stop_or_focus_invalidates(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control(probability=0.2)
    messages = [_make_message("A")]
    runtime = _build_runtime(messages, control)
    entered = asyncio.Event()
    release = asyncio.Event()
    manager = _FakeManager(modified={"decision": "consume"}, entered=entered, release=release)
    scheduler = _build_scheduler(monkeypatch, runtime, control, manager)

    task = asyncio.create_task(scheduler.review_message_turn(messages))
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    runtime._focus_group = object()
    release.set()
    result = await asyncio.wait_for(task, timeout=2.0)
    assert result is not None and result.decision == "delay"


# ────────────────────────── _consume_gate_batch 行为 ──────────────────────────


async def _fake_build_history(message: SessionMessage, *, source_kind: str = "user") -> ContentContextMessage:
    return ContentContextMessage(
        raw_message=MessageSequence([TextComponent(message.processed_plain_text or "")]),
        visible_text=message.processed_plain_text or "",
        timestamp=message.timestamp,
        message_id=message.message_id,
    )


def _build_engine(monkeypatch: pytest.MonkeyPatch, runtime: MaisakaHeartFlowChatting) -> MaisakaReasoningEngine:
    engine = object.__new__(MaisakaReasoningEngine)
    engine._runtime = runtime
    monkeypatch.setattr(engine, "_build_history_message", _fake_build_history)
    monkeypatch.setattr(engine, "_post_process_chat_history_after_cycle", AsyncMock())
    build_context_summary = Mock(side_effect=AssertionError("静默消费不得请求摘要模型"))
    import src.maisaka.reasoning_engine as reasoning_module
    monkeypatch.setattr(reasoning_module, "build_context_summary", build_context_summary)
    return engine


@pytest.mark.asyncio
async def test_consume_batch_writes_history_and_advances_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control(probability=0.2)
    messages = [_make_message("A"), _make_message("B")]
    runtime = _build_runtime(messages, control)
    engine = _build_engine(monkeypatch, runtime)

    committed = await engine._consume_gate_batch(messages=messages, consumed_count=2)

    assert committed is True
    assert [m.message_id for m in runtime._chat_history] == ["A", "B"]
    assert runtime._last_processed_index == 2
    runtime._message_turn_scheduler.record_message_consumed.assert_called_once_with()
    runtime._fixed_message_debouncer.consume.assert_called_once_with()
    runtime._prune_processed_message_cache.assert_called_once_with()
    post = engine._post_process_chat_history_after_cycle
    post.assert_awaited_once()
    assert post.await_args.kwargs["enable_compaction"] is False
    assert post.await_args.kwargs["consume_references"] is False


@pytest.mark.asyncio
async def test_consume_batch_partial_leaves_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control(probability=0.2)
    messages = [_make_message("A"), _make_message("B"), _make_message("C")]
    runtime = _build_runtime(messages, control)
    engine = _build_engine(monkeypatch, runtime)

    committed = await engine._consume_gate_batch(messages=messages, consumed_count=1)

    assert committed is True
    assert [m.message_id for m in runtime._chat_history] == ["A"]
    assert runtime._last_processed_index == 1
    runtime._fixed_message_debouncer.consume.assert_not_called()  # 仍有 pending


@pytest.mark.asyncio
async def test_consume_batch_build_exception_keeps_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control(probability=0.2)
    messages = [_make_message("A"), _make_message("B")]
    runtime = _build_runtime(messages, control)
    engine = _build_engine(monkeypatch, runtime)

    call = {"n": 0}

    async def failing_build(message: SessionMessage, *, source_kind: str = "user") -> ContentContextMessage:
        call["n"] += 1
        if call["n"] == 2:
            raise RuntimeError("第二条构建失败")
        return await _fake_build_history(message)

    monkeypatch.setattr(engine, "_build_history_message", failing_build)

    with pytest.raises(RuntimeError, match="第二条构建失败"):
        await engine._consume_gate_batch(messages=messages, consumed_count=2)

    assert runtime._chat_history == []
    assert runtime._last_processed_index == 0
    engine._post_process_chat_history_after_cycle.assert_not_awaited()


@pytest.mark.asyncio
async def test_consume_batch_event_during_staging_discards(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control(probability=0.2)
    messages = [_make_message("A"), _make_message("B")]
    runtime = _build_runtime(messages, control)
    engine = _build_engine(monkeypatch, runtime)

    async def build_then_change(message: SessionMessage, *, source_kind: str = "user") -> ContentContextMessage:
        runtime._message_turn_changed.set()
        return await _fake_build_history(message)

    monkeypatch.setattr(engine, "_build_history_message", build_then_change)

    committed = await engine._consume_gate_batch(messages=messages, consumed_count=2)

    assert committed is False
    assert runtime._chat_history == []
    assert runtime._last_processed_index == 0


def test_gate_batch_unchanged_after_processed_prefix_prune(monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存头部裁剪只移动下标，pending 对象身份与顺序不变，不应误判为另一批。"""
    control = _Control()
    processed = _make_message("P")
    pending = [_make_message("A"), _make_message("B")]
    runtime = _build_runtime([processed, *pending], control)
    runtime._last_processed_index = 1
    engine = _build_engine(monkeypatch, runtime)

    assert engine._gate_batch_unchanged(tuple(pending)) is True

    del runtime.message_cache[0]
    runtime._last_processed_index = 0
    assert engine._gate_batch_unchanged(tuple(pending)) is True

    runtime.message_cache.append(_make_message("C"))
    assert engine._gate_batch_unchanged(tuple(pending)) is False


# ────────────────────────── 引擎入口：wait 与 trigger 分流 ──────────────────────────


@pytest.mark.asyncio
async def test_engine_wait_returns_empty_context_without_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control(probability=0.4)
    messages = [_make_message("A"), _make_message("B")]
    runtime = _build_runtime(messages, control)
    scheduler = _build_scheduler(monkeypatch, runtime, control, _FakeManager())
    runtime._message_turn_scheduler = scheduler
    engine = _build_engine(monkeypatch, runtime)
    ingest = AsyncMock()
    monkeypatch.setattr(engine, "_ingest_messages", ingest)
    await runtime._internal_turn_queue.put("message")

    context = await engine._prepare_turn_start_context("message")

    assert context.trigger_message is None
    assert context.cached_messages == []
    ingest.assert_not_awaited()
    assert runtime._last_processed_index == 0  # wait 不采集、不消费


@pytest.mark.asyncio
async def test_engine_trigger_continues_normal_planner_path(monkeypatch: pytest.MonkeyPatch) -> None:
    control = _Control(probability=0.8)
    messages = [_make_message("A"), _make_message("B")]
    runtime = _build_runtime(messages, control)
    scheduler = _build_scheduler(monkeypatch, runtime, control, _FakeManager())
    runtime._message_turn_scheduler = scheduler
    engine = _build_engine(monkeypatch, runtime)
    ingest = AsyncMock()
    monkeypatch.setattr(engine, "_ingest_messages", ingest)
    await runtime._internal_turn_queue.put("message")

    context = await engine._prepare_turn_start_context("message")

    assert context.trigger_message is messages[-1]
    assert context.cached_messages == messages
    ingest.assert_awaited_once_with(messages)
    assert runtime._last_processed_index == 2  # 走原采集流程推进游标
