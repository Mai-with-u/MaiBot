"""Jev 决策触发调度测试。"""

from types import SimpleNamespace
import asyncio
import time

import pytest

from src.config.config import global_config
from src.maisaka.turn_scheduler import MessageTurnScheduler

STATE_RUNNING = "running"


def _build_runtime(*, pending_count: int, trigger_threshold: int, is_group_chat: bool = True) -> SimpleNamespace:
    """构造调度所需的最小 runtime 替身。"""

    async def no_async() -> None:
        return None

    runtime = SimpleNamespace(
        session_id="test-session",
        log_prefix="[测试] ",
        _running=True,
        _agent_state="idle",
        _STATE_WAIT="wait",
        _STATE_RUNNING=STATE_RUNNING,
        _message_turn_scheduled=False,
        _last_processed_index=0,
        message_cache=[object()] * pending_count,
        chat_stream=SimpleNamespace(is_group_session=is_group_chat),
        _chat_history=[],
        _idle_backoff=SimpleNamespace(should_delay=lambda pending_count: False),
        _last_message_received_at=0.0,
        _last_external_message_received_at=0.0,
        enqueued=[],
        deferred=[],
    )
    runtime._get_pending_message_count = lambda: len(runtime.message_cache) - runtime._last_processed_index
    runtime._get_effective_reply_frequency = lambda: 1.0
    runtime._is_reply_frequency_silent = lambda: False
    runtime._has_forced_turn_trigger = lambda: False
    runtime._get_message_trigger_threshold = lambda: trigger_threshold
    runtime._get_recent_average_external_message_interval = lambda: None
    runtime._enqueue_message_turn = lambda: runtime.enqueued.append(True)
    runtime._defer_message_turn_check = lambda delay: runtime.deferred.append(delay)
    return runtime


@pytest.fixture
def scheduler_factory():
    def build(pending_count: int, trigger_threshold: int) -> tuple[MessageTurnScheduler, SimpleNamespace]:
        runtime = _build_runtime(pending_count=pending_count, trigger_threshold=trigger_threshold)
        scheduler = MessageTurnScheduler(runtime)
        # 用替身门控替换真实 Jev 门控，避免测试访问外网
        scheduler._jev_gate = _StubJevGate(runtime)
        return scheduler, runtime

    return build


class _StubJevGate:
    """记录调用并返回固定判定的 Jev 门控替身。"""

    def __init__(self, runtime: SimpleNamespace) -> None:
        self._runtime = runtime
        self.calls: list[int] = []
        self.should_reply = True

    async def evaluate(self, *, pending_messages):
        self.calls.append(len(pending_messages))
        from src.maisaka.turn_gates import TurnGateResult

        return TurnGateResult(
            decision="trigger" if self.should_reply else "wait",
            detail=f"stub pending={len(pending_messages)}",
        )


def _set_trigger_mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(
        "src.maisaka.turn_scheduler.is_jev_decision_enabled",
        lambda: mode in ("jev", "jev_batch"),
    )
    monkeypatch.setattr("src.maisaka.turn_scheduler.is_jev_batch_trigger_enabled", lambda: mode == "jev_batch")
    monkeypatch.setattr(
        "src.maisaka.turn_scheduler.is_reply_necessity_trigger_enabled", lambda: mode == "reply_necessity"
    )


@pytest.mark.asyncio
async def test_jev_mode_decides_on_every_message(monkeypatch, scheduler_factory) -> None:
    _set_trigger_mode(monkeypatch, "jev")
    scheduler, runtime = scheduler_factory(pending_count=1, trigger_threshold=3)

    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)

    assert scheduler._jev_gate.calls == [1]
    assert runtime.enqueued == [True]


@pytest.mark.asyncio
async def test_jev_batch_waits_until_threshold_reached(monkeypatch, scheduler_factory) -> None:
    _set_trigger_mode(monkeypatch, "jev_batch")
    scheduler, runtime = scheduler_factory(pending_count=2, trigger_threshold=5)

    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)

    assert scheduler._jev_gate.calls == []
    assert runtime.enqueued == []
    # 未达阈值且无空窗样本时不进入延迟检查
    assert runtime.deferred == []


@pytest.mark.asyncio
async def test_jev_batch_decides_when_threshold_reached(monkeypatch, scheduler_factory) -> None:
    _set_trigger_mode(monkeypatch, "jev_batch")
    scheduler, runtime = scheduler_factory(pending_count=5, trigger_threshold=5)

    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)

    assert scheduler._jev_gate.calls == [5]
    assert runtime.enqueued == [True]


@pytest.mark.asyncio
async def test_jev_ignore_does_not_enqueue(monkeypatch, scheduler_factory) -> None:
    _set_trigger_mode(monkeypatch, "jev")
    scheduler, runtime = scheduler_factory(pending_count=1, trigger_threshold=3)
    scheduler._jev_gate.should_reply = False

    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)

    assert scheduler._jev_gate.calls == [1]
    assert runtime.enqueued == []


@pytest.mark.asyncio
async def test_jev_failure_does_not_enqueue_and_logs(monkeypatch, scheduler_factory) -> None:
    _set_trigger_mode(monkeypatch, "jev")
    scheduler, runtime = scheduler_factory(pending_count=1, trigger_threshold=3)

    class _FailingGate:
        async def evaluate(self, *, pending_messages):
            raise RuntimeError("jev api unavailable")

    scheduler._jev_gate = _FailingGate()

    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)

    assert runtime.enqueued == []


@pytest.mark.asyncio
async def test_frequency_mode_does_not_touch_jev_gate(monkeypatch, scheduler_factory) -> None:
    _set_trigger_mode(monkeypatch, "frequency")
    scheduler, runtime = scheduler_factory(pending_count=3, trigger_threshold=3)

    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)

    assert scheduler._jev_gate.calls == []
    assert runtime.enqueued == [True]


def test_message_trigger_threshold_only_uses_integer_config_for_jev_batch(monkeypatch) -> None:
    """只有定量 Jev 决策读取整数消息数量，其余模式仍走各自阈值。"""

    from src.maisaka.runtime import MaisakaHeartFlowChatting

    class _ThresholdProbe:
        _get_message_trigger_threshold = MaisakaHeartFlowChatting._get_message_trigger_threshold

        def _get_effective_reply_frequency(self) -> float:
            return 1.0

    monkeypatch.setattr(global_config.chat.reply_timing, "message_trigger_count", 7, raising=False)

    def threshold_for(mode: str) -> int:
        monkeypatch.setattr("src.maisaka.runtime.is_jev_batch_trigger_enabled", lambda: mode == "jev_batch")
        monkeypatch.setattr("src.maisaka.runtime.is_jev_trigger_enabled", lambda: mode == "jev")
        monkeypatch.setattr("src.maisaka.runtime.is_reply_necessity_trigger_enabled", lambda: mode == "reply_necessity")
        return _ThresholdProbe()._get_message_trigger_threshold()

    # 定量 Jev 决策直接使用配置条数
    assert threshold_for("jev_batch") == 7
    # 逐条 Jev 决策每条消息都判断，阈值固定为 1
    assert threshold_for("jev") == 1
    # 频率触发与必要性触发仍按回复频率折算（频率 1.0 时均为 1）
    assert threshold_for("frequency") == 1
    assert threshold_for("reply_necessity") == 1


def test_jev_trigger_mode_disables_frequency_control(monkeypatch) -> None:
    """选择 Jev 决策后，频率滑块与分聊天流规则都不再生效。"""

    from src.maisaka.mode_policy import is_reply_frequency_control_enabled
    from src.maisaka.runtime import MaisakaHeartFlowChatting

    # 直接改触发模式，让 runtime 与 mode_policy 读到同一份配置；
    # 不能只 patch runtime 模块里的同名函数，否则断言读到的是另一个对象。
    monkeypatch.setattr(global_config.chat.reply_timing, "reply_trigger_mode", "jev", raising=False)

    class _FrequencyProbe:
        _get_effective_reply_frequency = MaisakaHeartFlowChatting._get_effective_reply_frequency

        def _get_base_reply_frequency(self) -> float:
            return 0.0

        def _is_focus_mode_active_for_current_chat(self) -> bool:
            return False

    def resolve_group_talk_value(*args, **kwargs) -> float:
        raise AssertionError("Jev 决策下不应再读取分聊天流频率规则")

    monkeypatch.setattr("src.maisaka.runtime.ChatConfigUtils.get_talk_value", resolve_group_talk_value)

    # 频率为 0（原本的静默接收）在 Jev 决策下也必须返回满频率，让 Jev 全权决定
    assert _FrequencyProbe()._get_effective_reply_frequency() == 1.0
    assert is_reply_frequency_control_enabled() is False


def test_frequency_trigger_mode_keeps_frequency_control(monkeypatch) -> None:
    """频率触发与必要性触发仍由频率控制决定。"""

    from src.maisaka.mode_policy import is_reply_frequency_control_enabled

    original_mode = global_config.chat.reply_timing.reply_trigger_mode
    try:
        for mode, expected in (("frequency", True), ("reply_necessity", True), ("jev", False), ("jev_batch", False)):
            monkeypatch.setattr(global_config.chat.reply_timing, "reply_trigger_mode", mode, raising=False)
            assert is_reply_frequency_control_enabled() is expected, mode
    finally:
        monkeypatch.setattr(global_config.chat.reply_timing, "reply_trigger_mode", original_mode, raising=False)


@pytest.mark.asyncio
async def test_jev_does_not_resubmit_without_new_messages(monkeypatch, scheduler_factory) -> None:
    """没有新消息时不再重复提交，避免反复把同一批消息送给 Jev。"""

    _set_trigger_mode(monkeypatch, "jev")
    scheduler, runtime = scheduler_factory(pending_count=2, trigger_threshold=3)
    scheduler._jev_gate.should_reply = False

    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)
    assert scheduler._jev_gate.calls == [2]

    # 没有新消息时再次调度：不应重复提交同一批消息
    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)
    assert scheduler._jev_gate.calls == [2]

    # 新消息到达后才再次提交；窗口内含积压消息，供 Jev 判断上下文
    runtime.message_cache.append(object())
    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)
    assert scheduler._jev_gate.calls == [2, 3]


@pytest.mark.asyncio
async def test_jev_submission_window_is_capped(monkeypatch, scheduler_factory) -> None:
    """积压很多时只提交最近一段消息，请求体不会随积压量无限增长。"""

    from src.maisaka.turn_scheduler import JEV_MAX_SUBMISSION_WINDOW

    _set_trigger_mode(monkeypatch, "jev")
    backlog = JEV_MAX_SUBMISSION_WINDOW + 30
    scheduler, runtime = scheduler_factory(pending_count=backlog, trigger_threshold=3)
    scheduler._jev_gate.should_reply = False

    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)

    assert scheduler._jev_gate.calls == [JEV_MAX_SUBMISSION_WINDOW]


@pytest.mark.asyncio
async def test_jev_batch_threshold_counts_only_new_messages(monkeypatch, scheduler_factory) -> None:
    """定量 Jev 决策只按上次判断之后的新消息计数。"""

    _set_trigger_mode(monkeypatch, "jev_batch")
    scheduler, runtime = scheduler_factory(pending_count=3, trigger_threshold=2)
    scheduler._jev_gate.should_reply = False

    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)
    assert len(scheduler._jev_gate.calls) == 1

    # 积压仍有 3 条但都已评估过，第 4 条到达后只新增 1 条，未达到阈值 2，不应触发
    runtime.message_cache.append(object())
    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)
    assert len(scheduler._jev_gate.calls) == 1

    # 再补一条，新消息达到阈值 2，才再次判断
    runtime.message_cache.append(object())
    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)
    assert len(scheduler._jev_gate.calls) == 2


@pytest.mark.asyncio
async def test_jev_backlog_limit_lets_planner_collect(monkeypatch, scheduler_factory) -> None:
    """未消费消息达到上限时放行一次 Planner，避免消息缓存无界增长。"""

    from src.maisaka.turn_scheduler import JEV_MAX_PENDING_BACKLOG

    _set_trigger_mode(monkeypatch, "jev")
    scheduler, runtime = scheduler_factory(pending_count=JEV_MAX_PENDING_BACKLOG, trigger_threshold=3)
    scheduler._jev_gate.should_reply = False

    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)

    # 直接放行 Planner，不再调用 Jev
    assert runtime.enqueued == [True]
    assert scheduler._jev_gate.calls == []


@pytest.mark.asyncio
async def test_jev_batch_triggers_on_idle_compensation(monkeypatch, scheduler_factory) -> None:
    """定量 Jev 决策在空窗补偿满足时必须继续判断，不能被提前返回吞掉。"""

    _set_trigger_mode(monkeypatch, "jev_batch")
    scheduler, runtime = scheduler_factory(pending_count=1, trigger_threshold=3)
    # 平均间隔 1s 且已空窗 100s：空窗折算量封顶为 threshold-1，等效消息数刚好达到阈值
    runtime._get_recent_average_external_message_interval = lambda: 1.0
    runtime._last_external_message_received_at = time.time() - 100

    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)

    assert len(scheduler._jev_gate.calls) == 1


@pytest.mark.asyncio
async def test_jev_failure_keeps_messages_unevaluated_and_defers_retry(monkeypatch, scheduler_factory) -> None:
    """评估失败时保留待处理消息，并安排延迟重试而不是立即重试。"""

    from src.maisaka.turn_scheduler import JEV_FAILURE_RETRY_DELAY_SECONDS

    _set_trigger_mode(monkeypatch, "jev")
    scheduler, runtime = scheduler_factory(pending_count=1, trigger_threshold=3)

    class _FailingGate:
        async def evaluate(self, *, pending_messages):
            raise RuntimeError("jev api unavailable")

    scheduler._jev_gate = _FailingGate()

    scheduler.schedule_message_turn()
    await _drain_jev_task(scheduler)

    # 失败不推进游标：这批消息仍算未评估，下次新消息到来时还会重试
    assert scheduler._jev_evaluated_pending_count == 0
    assert runtime.enqueued == []
    # 安排的是延迟重试，而不是立即重跑
    assert runtime.deferred == [JEV_FAILURE_RETRY_DELAY_SECONDS]


async def _drain_jev_task(scheduler: MessageTurnScheduler) -> None:
    """等待 Jev 决策任务结束；未起任务时让出一次事件循环。"""

    task = scheduler._jev_decision_task
    if task is None:
        await asyncio.sleep(0)
        return
    await task
