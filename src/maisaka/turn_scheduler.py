"""Maisaka 消息触发调度。"""

from typing import Sequence, TYPE_CHECKING

from src.chat.message_receive.message import SessionMessage
from src.common.logger import get_logger
from src.maisaka.focus import focus_mode_manager
from src.maisaka.mode_policy import is_reply_necessity_trigger_enabled
from src.maisaka.turn_gates import FrequencyThresholdTurnGate, ReplyNecessityTurnGate

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting

logger = get_logger("maisaka_turn_scheduler")


class MessageTurnScheduler:
    """决定外部消息何时进入 Maisaka 内部循环。"""

    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        self._runtime = runtime
        self._reply_necessity_gate = ReplyNecessityTurnGate(runtime)
        self._frequency_threshold_gate = FrequencyThresholdTurnGate(runtime)

    def score_reply_necessity(
        self,
        *,
        pending_messages: Sequence[SessionMessage],
        trigger_threshold: int,
    ) -> tuple[int, str]:
        """按当前 runtime 快照为待处理消息计算回复必要性评分。"""

        score_result = self._reply_necessity_gate.score(
            pending_messages=pending_messages,
            trigger_threshold=trigger_threshold,
        )
        return score_result.score, score_result.detail

    def should_trigger_by_reply_necessity(
        self,
        *,
        pending_messages: Sequence[SessionMessage],
        trigger_threshold: int,
        formatted_frequency: str,
        pending_count: int,
    ) -> bool:
        """判断新 Maisaka 是否应基于回复必要性进入 Planner。"""

        result = self._reply_necessity_gate.evaluate(
            pending_messages=pending_messages,
            trigger_threshold=trigger_threshold,
        )
        decision_label = "进入Planner" if result.should_trigger else "等待更多消息"
        schedule_detail = (
            f"[频率: {formatted_frequency}]"
            f"[{pending_count}/{trigger_threshold} 消息 | 压力: {result.pressure_score}]"
        )
        logger.info(
            f"{self._runtime.log_prefix}{schedule_detail}[{result.detail}][{decision_label}]"
        )
        return result.should_trigger

    def schedule_message_turn(self) -> None:
        runtime = self._runtime
        if not focus_mode_manager.can_decide(
            runtime.session_id,
            is_group_chat=runtime.chat_stream.is_group_session,
        ):
            logger.debug(f"{runtime.log_prefix} 当前不在 focus 状态，跳过 Maisaka 决策调度")
            return

        if runtime._agent_state == runtime._STATE_WAIT:
            if not runtime._is_reply_frequency_silent():
                if runtime.chat_stream.is_group_session:
                    return
                logger.info(f"{runtime.log_prefix} 私聊 wait 期间收到新消息，结束等待并进入 Planner")
                runtime._enter_running_state()
            else:
                runtime._enter_stop_state()

        if runtime._message_turn_scheduled:
            return

        pending_count = runtime._get_pending_message_count()
        if pending_count <= 0:
            return

        effective_frequency = runtime._get_effective_reply_frequency()
        formatted_frequency = f"{effective_frequency:.3f}"
        if runtime._is_reply_frequency_silent():
            logger.info(
                f"{runtime.log_prefix} 回复频率调度: 频率={formatted_frequency} "
                f"pending={pending_count} 判定=静默消费"
            )
            runtime._enqueue_message_turn()
            return

        if runtime._has_forced_turn_trigger():
            logger.info(
                f"{runtime.log_prefix} 回复频率调度: 频率={formatted_frequency} "
                f"pending={pending_count} 判定=强制触发"
            )
            runtime._enqueue_message_turn()
            return

        if runtime._idle_backoff.should_delay(pending_count):
            return

        trigger_threshold = runtime._get_message_trigger_threshold()
        schedule_detail = f"[频率: {formatted_frequency}][{pending_count}/{trigger_threshold} 消息]"
        if is_reply_necessity_trigger_enabled():
            if self.should_trigger_by_reply_necessity(
                pending_messages=runtime.message_cache[runtime._last_processed_index :],
                trigger_threshold=trigger_threshold,
                formatted_frequency=formatted_frequency,
                pending_count=pending_count,
            ):
                runtime._enqueue_message_turn()
            return

        logger.info(f"{runtime.log_prefix} 回复频率调度: {schedule_detail}")
        frequency_result = self._frequency_threshold_gate.evaluate(
            pending_count=pending_count,
            trigger_threshold=trigger_threshold,
        )
        logger.info(f"{runtime.log_prefix} 回复频率调度: {frequency_result.detail}")
        if frequency_result.should_trigger:
            runtime._enqueue_message_turn()
            return

        if frequency_result.decision == "delay" and frequency_result.delay_seconds is not None:
            runtime._defer_message_turn_check(frequency_result.delay_seconds)
