"""Maisaka 消息触发策略。"""

from dataclasses import dataclass
from typing import Literal, Optional, Sequence, TYPE_CHECKING

from src.chat.message_receive.message import SessionMessage
from src.common.logger import get_logger

from .mode_policy import is_reply_necessity_trigger_enabled
from .turn_gates import FrequencyThresholdTurnGate, ReplyNecessityTurnGate

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting

logger = get_logger("maisaka_turn_scheduler")

TurnAction = Literal["enqueue", "wait", "delay"]


@dataclass(frozen=True, slots=True)
class TurnSnapshot:
    """一次消息触发判定所需的运行时快照。"""

    pending_count: int
    pending_messages: Sequence[SessionMessage]
    effective_frequency: float
    is_silent: bool
    has_forced_trigger: bool
    trigger_threshold: int
    log_prefix: str


@dataclass(frozen=True, slots=True)
class TurnDecision:
    """消息触发策略的判定结果。queue/wait 副作用由调用方执行。"""

    action: TurnAction
    delay_seconds: Optional[float] = None


class TurnPolicy:
    """根据快照判定静默、强制触发、空闲退避以及必要性/频率门。"""

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

    def decide(self, snapshot: TurnSnapshot) -> TurnDecision:
        """判定当前快照是否应进入内部循环、等待或延迟再检查。"""

        formatted_frequency = f"{snapshot.effective_frequency:.3f}"
        if snapshot.is_silent:
            logger.info(
                f"{snapshot.log_prefix} 回复频率调度: 频率={formatted_frequency} "
                f"pending={snapshot.pending_count} 判定=静默消费"
            )
            return TurnDecision(action="enqueue")

        if snapshot.has_forced_trigger:
            logger.info(
                f"{snapshot.log_prefix} 回复频率调度: 频率={formatted_frequency} "
                f"pending={snapshot.pending_count} 判定=强制触发"
            )
            return TurnDecision(action="enqueue")

        delay_seconds = self._runtime._idle_backoff.should_delay(snapshot.pending_count)
        if delay_seconds is not None:
            return TurnDecision(action="delay", delay_seconds=delay_seconds)

        trigger_threshold = snapshot.trigger_threshold
        schedule_detail = f"[频率: {formatted_frequency}][{snapshot.pending_count}/{trigger_threshold} 消息]"
        if is_reply_necessity_trigger_enabled():
            if self._should_trigger_by_reply_necessity(
                pending_messages=snapshot.pending_messages,
                trigger_threshold=trigger_threshold,
                formatted_frequency=formatted_frequency,
                pending_count=snapshot.pending_count,
            ):
                return TurnDecision(action="enqueue")
            return TurnDecision(action="wait")

        logger.info(f"{snapshot.log_prefix} 回复频率调度: {schedule_detail}")
        frequency_result = self._frequency_threshold_gate.evaluate(
            pending_count=snapshot.pending_count,
            trigger_threshold=trigger_threshold,
        )
        logger.info(f"{snapshot.log_prefix} 回复频率调度: {frequency_result.detail}")
        if frequency_result.should_trigger:
            return TurnDecision(action="enqueue")

        if frequency_result.decision == "delay" and frequency_result.delay_seconds is not None:
            return TurnDecision(action="delay", delay_seconds=frequency_result.delay_seconds)
        return TurnDecision(action="wait")

    def _should_trigger_by_reply_necessity(
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
