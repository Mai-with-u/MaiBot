"""私聊调度：消息唤醒与直接放行，不维护群聊频率额度及退避。"""

from src.common.logger import get_logger
from src.maisaka.turn_trigger.gates import TurnGateResult
from src.maisaka.turn_trigger.messages import filter_external_messages
from src.maisaka.turn_trigger.scheduler import MessageTurnScheduler

logger = get_logger("maisaka_turn_scheduler")


class PrivateTurnScheduler(MessageTurnScheduler):
    def _resume_wait_on_message(self) -> bool:
        logger.info(f"{self._runtime.log_prefix} 私聊 wait 期间收到新消息，结束等待并进入 Planner")
        self._runtime._enter_running_state()
        return True

    def record_reply(self) -> None:
        """私聊不统计群聊动态门控的回复额度。"""

    def record_cycle_result(self, cycle_end_reason: str) -> None:
        """私聊的空闲结束不产生退避。"""

    def reset_backoff(self) -> None:
        """私聊没有需要重置的退避状态。"""

    def _evaluate_scene(self, pending_count: int, frequency: float) -> TurnGateResult:
        runtime = self._runtime
        if runtime._has_forced_turn_trigger():
            return TurnGateResult(decision="trigger", detail=f"调度=强制触发 频率={frequency:.3f}")
        messages = filter_external_messages(
            runtime.message_cache[runtime._last_processed_index :],
            log_prefix=runtime.log_prefix,
        )
        if not messages:
            return TurnGateResult(decision="wait", detail="没有新的外部消息 判定=等待更多消息")
        # 拟合数据只覆盖群聊；私聊里每条消息都是对麦麦说的，不设门控。
        return TurnGateResult(decision="trigger", detail="私聊不设动态门控 判定=进入Planner")
