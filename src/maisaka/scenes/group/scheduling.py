"""群聊调度：频率门控、强制触发与空闲退避。"""

from typing import TYPE_CHECKING
import random
import time

from src.maisaka.turn_trigger.gates import DynamicReplyTurnGate, TurnGateResult
from src.maisaka.turn_trigger.idle_backoff import IdleBackoffController
from src.maisaka.turn_trigger.scheduler import MessageTurnScheduler

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting


class GroupTurnScheduler(MessageTurnScheduler):
    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        super().__init__(runtime)
        self._dynamic_reply_gate = DynamicReplyTurnGate(runtime)
        self._idle_backoff = IdleBackoffController(runtime)
        self._last_wait_batch = None

    def _resume_wait_on_message(self) -> bool:
        runtime = self._runtime
        batch = (runtime._pending_wait_started_at, len(runtime.message_cache))
        if batch == self._last_wait_batch:
            return False
        self._last_wait_batch = batch
        duration = runtime._pending_wait_seconds
        started = runtime._pending_wait_started_at
        if duration is None or started is None:
            return False
        progress = 1.0 if duration <= 0 else min(1.0, max(0.0, (time.time() - started) / duration))
        # p(t)=0.16t²+0.08t：起点 0%，半程 8%，到期 24%；定时到期仍必定唤醒。
        probability = 0.16 * progress * progress + 0.08 * progress
        return random.random() < probability

    def record_reply(self) -> None:
        self._dynamic_reply_gate.record_reply()

    def record_cycle_result(self, cycle_end_reason: str) -> None:
        self._idle_backoff.record_cycle_result(cycle_end_reason)

    def reset_backoff(self) -> None:
        self._idle_backoff.reset()

    def _evaluate_scene(self, pending_count: int, frequency: float) -> TurnGateResult:
        runtime = self._runtime
        if runtime._has_forced_turn_trigger():
            # @ 强制触发的回复同样占用动态门控的回复额度
            self._dynamic_reply_gate.record_forced_turn(runtime.message_cache[runtime._last_processed_index :])
            return TurnGateResult(decision="trigger", detail=f"调度=强制触发 频率={frequency:.3f}")

        backoff_seconds = self._idle_backoff.get_delay_seconds(pending_count)
        if backoff_seconds > 0:
            return TurnGateResult(
                decision="delay",
                detail=f"空闲退避中 剩余={backoff_seconds:.2f}s",
                delay_seconds=backoff_seconds,
            )

        dynamic_result = self._dynamic_reply_gate.evaluate(
            pending_messages=runtime.message_cache[runtime._last_processed_index :],
            frequency=frequency,
        )
        return TurnGateResult(
            decision=dynamic_result.decision,
            detail=f"频率={frequency:.3f} {dynamic_result.detail}",
        )
