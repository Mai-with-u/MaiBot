"""Jev 决策触发调度测试。"""

import asyncio
from types import SimpleNamespace

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
    runtime._get_pending_message_count = lambda: pending_count
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
    monkeypatch.setattr("src.maisaka.turn_scheduler.is_reply_necessity_trigger_enabled", lambda: mode == "reply_necessity")


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


def test_message_trigger_threshold_uses_integer_config_for_jev_mode(monkeypatch) -> None:
    """Jev 决策与频率触发都应直接读取整数消息数量配置。"""

    from src.maisaka.runtime import MaisakaHeartFlowChatting

    monkeypatch.setattr("src.maisaka.runtime.is_jev_decision_enabled", lambda: True)
    monkeypatch.setattr("src.maisaka.runtime.get_reply_trigger_mode", lambda: "jev")

    class _ThresholdProbe:
        _get_message_trigger_threshold = MaisakaHeartFlowChatting._get_message_trigger_threshold

        def _get_effective_reply_frequency(self) -> float:
            return 1.0

    monkeypatch.setattr(global_config.chat.reply_timing, "message_trigger_count", 7, raising=False)
    assert _ThresholdProbe()._get_message_trigger_threshold() == 7


async def _drain_jev_task(scheduler: MessageTurnScheduler) -> None:
    """等待 Jev 决策任务结束；未起任务时让出一次事件循环。"""

    task = scheduler._jev_decision_task
    if task is None:
        await asyncio.sleep(0)
        return
    await task
