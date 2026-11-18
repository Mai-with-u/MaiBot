"""群聊调度：每批概率查看、内部衰减与静默判定。"""

from math import ceil
from typing import TYPE_CHECKING, Optional, Tuple
import asyncio
import random
import time

from src.common.logger import get_logger
from src.maisaka.turn_trigger.gates import TurnGateResult
from src.maisaka.turn_trigger.messages import filter_external_messages
from src.maisaka.turn_trigger.scheduler import MAX_INTERNAL_ROUNDS, MessageTurnScheduler
from src.maisaka.turn_trigger.view_frequency import (
    ViewFrequencyController,
    mean_message_interval,
    predict_message_interval,
)

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting

logger = get_logger("maisaka_turn_scheduler")
LOW_FREQUENCY_ROUND_LIMIT = 8


class GroupTurnScheduler(MessageTurnScheduler):
    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        super().__init__(runtime)
        self.view_frequency = ViewFrequencyController()
        self._logical_turn_id: Optional[str] = None
        self._in_planner_turn = False
        self._completed_rounds = 0
        self._round_allowance = 0
        self._round_debt = 0
        self._last_batch: Optional[Tuple[str, ...]] = None
        self._silence_task: Optional[asyncio.Task[None]] = None
        self._silence_triggered = False
        self._silence_handled_at = 0.0
        self._previous_context_interval: Optional[float] = None
        self._current_context_interval: Optional[float] = None

    @property
    def allows_timed_wakeup(self) -> bool:
        return self._round_debt == 0

    def schedule_message_turn(self) -> None:
        # 运行中由下一轮接入消息。wait 工具可先于本轮记账进入等待态，待收尾后再判定。
        if not self._in_planner_turn and self._runtime._agent_state != self._runtime._STATE_RUNNING:
            super().schedule_message_turn()

    def _resume_wait_on_message(self) -> bool:
        runtime = self._runtime
        return self._evaluate_scene(
            runtime._get_pending_message_count(), runtime._get_effective_reply_frequency()
        ).should_trigger

    def record_reply(self) -> None:
        """查看概率不再根据实际回复次数调整。"""

    def record_cycle_result(self, cycle_end_reason: str) -> None:
        """连续查看由内部频率限制，不再叠加空闲退避。"""

    def reset_backoff(self) -> None:
        """强制触发不重置正在衰减的内部频率。"""

    def _evaluate_scene(self, pending_count: int, frequency: float) -> TurnGateResult:
        runtime = self._runtime
        if runtime._has_forced_turn_trigger():
            return TurnGateResult(decision="trigger", detail=f"调度=强制查看 外部频率={frequency:.3f}")
        messages = filter_external_messages(
            runtime.message_cache[runtime._last_processed_index :], log_prefix=runtime.log_prefix
        )
        batch = tuple(message.message_id for message in messages)
        if not batch or batch == self._last_batch:
            return TurnGateResult(decision="wait", detail="本批已判定，等待新批次或静默判定")
        self._last_batch = batch
        probability = self.view_frequency.probability(frequency)
        accepted = self.view_frequency.decide(frequency, random.random())
        # 本批即使未放行也已结束防抖，后续消息重新开批；缓存留作下次查看上下文。
        runtime._fixed_message_debouncer.consume()
        if accepted and self._round_debt:
            self._round_debt -= 1
            self._arm_silence_check()
            return TurnGateResult(decision="wait", detail=f"本批查看抵扣 1 轮欠债 剩余={self._round_debt}")
        self._arm_silence_check()
        return TurnGateResult(
            decision="trigger" if accepted else "wait",
            detail=(
                f"概率查看 外部频率={frequency:.3f} 内部频率={self.view_frequency.internal_frequency:.3f} "
                f"放行概率={probability:.3f} 阶段={self.view_frequency.phase}"
            ),
        )

    def start_planner_turn(self, logical_turn_id: str) -> int:
        self.close()
        self._in_planner_turn = True
        frequency = min(1.0, self._runtime._get_effective_reply_frequency())
        self.view_frequency.activate(frequency)
        if logical_turn_id != self._logical_turn_id:
            self._logical_turn_id = logical_turn_id
            self._completed_rounds = 0
            self._round_allowance = ceil(frequency * MAX_INTERNAL_ROUNDS)
            self._runtime._max_internal_rounds = LOW_FREQUENCY_ROUND_LIMIT if frequency < 0.5 else MAX_INTERNAL_ROUNDS
        return self._completed_rounds

    def record_planner_run(self) -> None:
        # Focus 自己维护统一循环，不参与普通群聊的频率状态。
        if self._runtime._is_focus_mode_active_for_current_chat():
            return
        self._completed_rounds += 1
        if self._completed_rounds > self._round_allowance:
            self._round_debt += 1
        self._previous_context_interval = self._current_context_interval
        self._current_context_interval = self._context_interval()
        self.view_frequency.record_planner_run()

    def finish_planner_turn(self) -> None:
        self._in_planner_turn = False
        if not self._runtime._has_pending_wait_tool_call():
            self._logical_turn_id = None
        # 最后一轮模型或工具执行期间到达的消息可能尚未接入，退出后仍须为其完成一次判定。
        self._runtime._schedule_message_turn()
        self._arm_silence_check()

    def observe_message(self) -> None:
        self._arm_silence_check()

    def consume_silence_trigger(self) -> bool:
        triggered = self._silence_triggered
        self._silence_triggered = False
        return triggered

    def close(self) -> None:
        if self._silence_task is not None:
            self._silence_task.cancel()
            self._silence_task = None

    def _context_interval(self) -> Optional[float]:
        runtime = self._runtime
        # 只读取上下文窗口的接收时间，定时调度不构建包含图片处理的模型请求。
        received_times = []
        context_count = 0
        for message in reversed(runtime._chat_history):
            if not message.count_in_context:
                continue
            if message.source == "user" and message.received_at is not None:
                received_times.append(message.received_at.timestamp())
            context_count += 1
            if context_count >= runtime._max_context_size:
                break
        received_times.extend(
            message.received_at.timestamp()
            for message in filter_external_messages(
                runtime.message_cache[runtime._last_processed_index :], log_prefix=runtime.log_prefix
            )
            if message.received_at is not None
        )
        return mean_message_interval(sorted(received_times)[-runtime._max_context_size :])

    def _arm_silence_check(self) -> None:
        self.close()
        runtime = self._runtime
        if (
            not runtime._running
            or self._in_planner_turn
            or not self.allows_timed_wakeup
            or runtime._focus_group is not None
            or runtime._agent_state == runtime._STATE_RUNNING
            or runtime._message_turn_scheduled
            or runtime._is_reply_frequency_silent()
            or runtime._last_message_received_at <= self._silence_handled_at
        ):
            return
        interval = predict_message_interval(self._context_interval(), self._previous_context_interval)
        if interval is not None:
            self._silence_task = asyncio.create_task(self._check_silence(interval))

    async def _check_silence(self, interval: float) -> None:
        runtime = self._runtime
        received_at = runtime._last_message_received_at
        deadline = received_at + interval
        try:
            while runtime._running:
                await asyncio.sleep(max(0.0, deadline - time.time()))
                if (
                    runtime._last_message_received_at != received_at
                    or not self.allows_timed_wakeup
                    or runtime._focus_group is not None
                    or runtime._agent_state == runtime._STATE_RUNNING
                    or runtime._message_turn_scheduled
                    or runtime._is_reply_frequency_silent()
                ):
                    return
                quiet_seconds = runtime._get_message_quiet_period_remaining_seconds()
                if quiet_seconds > 0:
                    deadline = time.time() + quiet_seconds
                    continue
                frequency = runtime._get_effective_reply_frequency()
                probability = self.view_frequency.probability(frequency)
                accepted = self.view_frequency.decide(frequency, random.random())
                logger.debug(
                    f"{runtime.log_prefix} 静默查看判定 间隔={interval:.3f}s 概率={probability:.3f} 放行={accepted}"
                )
                if accepted:
                    self._silence_handled_at = received_at
                    self._silence_triggered = True
                    if runtime._agent_state == runtime._STATE_WAIT:
                        self.accepted_wait_started_at = runtime._pending_wait_started_at
                        runtime._cancel_wait_timeout_task()
                    runtime._enqueue_message_turn()
                    return
                # 同一截止点只判一次；仍然静默则预测下一个消息时刻，继续给恢复期推进机会。
                deadline = time.time() + interval
        except asyncio.CancelledError:
            return
        finally:
            if self._silence_task is asyncio.current_task():
                self._silence_task = None
