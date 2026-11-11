"""Maisaka 消息触发调度。"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from src.common.logger import get_logger

from .gates import TurnGateResult

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting

logger = get_logger("maisaka_turn_scheduler")


class MessageTurnScheduler(ABC):
    """决定外部消息何时进入 Maisaka 内部循环。

    共同检查依次经过：消息防抖 → 快速反应续接 wait → 静默消费 → 场景专属判定。
    每一步只返回判定，由 ``schedule_message_turn`` 统一投递或延迟重查。
    """

    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        self._runtime = runtime
        self.accepted_wait_started_at = None

    def schedule_message_turn(self) -> None:
        runtime = self._runtime
        if runtime._message_turn_scheduled:
            return

        pending_count = runtime._get_pending_message_count()
        if pending_count <= 0:
            return

        result = self._evaluate(pending_count)
        logger.debug(f"{runtime.log_prefix} 回复调度 待处理={pending_count} {result.detail}")
        if result.decision == "delay":
            runtime._defer_message_turn_check(result.delay_seconds)
            return

        if result.should_trigger:
            if runtime._agent_state == runtime._STATE_WAIT:
                runtime._cancel_wait_timeout_task()
                # 保留挂起状态到消费触发时，避免原工具批次收尾清掉 wait 调用关联。
                runtime._update_stage_status("等待已打断", "收到新消息，准备继续思考")
            runtime._enqueue_message_turn()

    def _evaluate(self, pending_count: int) -> TurnGateResult:
        """按流水线顺序返回第一个给出结论的判定。"""

        runtime = self._runtime
        # 先收齐本批：后续门控按整批消息判断，防止只凭半批消息估计回复概率。
        quiet_seconds = runtime._get_message_quiet_period_remaining_seconds()
        if quiet_seconds > 0:
            return TurnGateResult(
                decision="delay",
                detail=f"等待消息防抖收齐本批 剩余={quiet_seconds:.3f}s",
                delay_seconds=quiet_seconds,
            )

        if runtime._agent_state == runtime._STATE_WAIT:
            if runtime._is_reply_frequency_silent():
                runtime._enter_stop_state()
            elif not self._resume_wait_on_message():
                # 本批已参与唤醒判断。保留消息缓存，后续新消息重新开防抖窗口。
                runtime._fixed_message_debouncer.consume()
                return TurnGateResult(decision="wait", detail="新消息未唤醒等待")
            else:
                self.accepted_wait_started_at = runtime._pending_wait_started_at
                return TurnGateResult(decision="trigger", detail="新消息唤醒等待")

        frequency = runtime._get_effective_reply_frequency()
        if runtime._is_reply_frequency_silent():
            return TurnGateResult(decision="trigger", detail=f"调度=静默消费 频率={frequency:.3f}")

        return self._evaluate_scene(pending_count, frequency)

    @abstractmethod
    def _resume_wait_on_message(self) -> bool:
        """处理非快速反应模式的 wait，返回是否继续调度。"""

    @abstractmethod
    def _evaluate_scene(self, pending_count: int, frequency: float) -> TurnGateResult:
        """执行场景专属的消息放行规则。"""

    @abstractmethod
    def record_reply(self) -> None:
        """记录一次成功回复。"""

    @abstractmethod
    def record_cycle_result(self, cycle_end_reason: str) -> None:
        """记录一轮推理结果。"""

    @abstractmethod
    def reset_backoff(self) -> None:
        """强制触发时重置场景退避状态。"""
