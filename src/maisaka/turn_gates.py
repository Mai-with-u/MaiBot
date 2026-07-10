"""Maisaka 消息触发门控。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional, Sequence, TYPE_CHECKING
import time

from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import is_bot_self
from src.maisaka.reply_necessity import (
    REPLY_NECESSITY_TRIGGER_SCORE,
    ReplyNecessityInput,
    ReplyNecessityScore,
    score_reply_necessity,
)

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting


TurnGateDecision = Literal["trigger", "wait", "delay"]


@dataclass(frozen=True)
class TurnGateResult:
    """单个触发门控的判定结果。"""

    decision: TurnGateDecision
    detail: str
    delay_seconds: Optional[float] = None
    pressure_score: Optional[int] = None

    @property
    def should_trigger(self) -> bool:
        return self.decision == "trigger"


class ReplyNecessityTurnGate:
    """按回复必要性评分决定是否进入 Planner。"""

    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        self._runtime = runtime

    def score(
        self,
        *,
        pending_messages: Sequence[SessionMessage],
        trigger_threshold: int,
    ) -> ReplyNecessityScore:
        """按当前 runtime 快照为待处理消息计算回复必要性评分。"""

        runtime = self._runtime
        external_messages = [
            message
            for message in pending_messages
            if not is_bot_self(message.platform, message.message_info.user_info.user_id)
        ]
        average_interval = runtime._get_recent_average_external_message_interval()
        if average_interval is not None and average_interval > 0:
            last_external_received_at = runtime._last_external_message_received_at or runtime._last_message_received_at
            idle_seconds = max(0.0, time.time() - last_external_received_at)
            idle_reached_average = idle_seconds >= average_interval
        else:
            idle_seconds = 0.0
            idle_reached_average = False

        recent_self_replies, recent_window_messages = self._count_recent_presence_messages()
        return score_reply_necessity(
            ReplyNecessityInput(
                texts=[(message.processed_plain_text or "").strip() for message in external_messages],
                pending_count=len(external_messages),
                trigger_threshold=trigger_threshold,
                has_at=any(message.is_at for message in external_messages),
                has_mention=any(message.is_mentioned for message in external_messages),
                is_group_chat=runtime.chat_stream.is_group_session,
                focus_active=runtime._is_focus_mode_active_for_current_chat(),
                recent_self_replies=recent_self_replies,
                recent_window_messages=recent_window_messages,
                effective_frequency=runtime._get_effective_reply_frequency(),
                idle_seconds=idle_seconds,
                idle_reached_average=idle_reached_average,
            )
        )

    def evaluate(
        self,
        *,
        pending_messages: Sequence[SessionMessage],
        trigger_threshold: int,
    ) -> TurnGateResult:
        """返回回复必要性门控的触发判定。"""

        score_result = self.score(
            pending_messages=pending_messages,
            trigger_threshold=trigger_threshold,
        )
        decision = "trigger" if score_result.score >= REPLY_NECESSITY_TRIGGER_SCORE else "wait"
        gate_detail = f"必要性: {score_result.detail} 评分阈值={REPLY_NECESSITY_TRIGGER_SCORE}"
        return TurnGateResult(decision=decision, detail=gate_detail, pressure_score=score_result.pressure_score)

    def _count_recent_presence_messages(self, window_seconds: float = 300.0) -> tuple[int, int]:
        """统计最近一段时间内麦麦发言数和总消息数。"""

        now = datetime.now()
        recent_self_count = 0
        recent_total_count = 0
        for message in reversed(self._runtime._chat_history):
            if (now - message.timestamp).total_seconds() > window_seconds:
                break
            if not message.count_in_context:
                continue
            recent_total_count += 1
            if message.source == "guided_reply":
                recent_self_count += 1
        return recent_self_count, recent_total_count


class FrequencyThresholdTurnGate:
    """按回复频率折算消息阈值，并用空窗补偿辅助触发。"""

    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        self._runtime = runtime

    def evaluate(
        self,
        *,
        pending_count: int,
        trigger_threshold: int,
    ) -> TurnGateResult:
        """返回频率阈值门控的触发判定。"""

        if pending_count >= trigger_threshold:
            return TurnGateResult(
                decision="trigger",
                detail=f"pending={pending_count} 达到阈值={trigger_threshold} 判定=进入Planner",
            )

        idle_compensation_triggered, delay_seconds, idle_detail = self._calculate_idle_compensation(
            pending_count=pending_count,
            trigger_threshold=trigger_threshold,
        )
        if idle_compensation_triggered:
            return TurnGateResult(
                decision="trigger",
                detail=f"{idle_detail} 判定=空窗补偿进入Planner",
            )

        if delay_seconds is not None:
            return TurnGateResult(
                decision="delay",
                detail=f"{idle_detail} 判定=延迟检查",
                delay_seconds=delay_seconds,
            )

        return TurnGateResult(decision="wait", detail=f"{idle_detail} 判定=等待更多消息")

    def _calculate_idle_compensation(
        self,
        *,
        pending_count: int,
        trigger_threshold: int,
    ) -> tuple[bool, Optional[float], str]:
        """在新消息不足阈值时，按空窗时间折算补齐触发条件，并返回下次检查延迟。

        空窗折算量被限制在 ``trigger_threshold - 1`` 以内，确保至少要有一条真实新消息
        才可能触发，杜绝纯靠沉默累积反复唤醒回复。
        """

        # 与下方折算封顶互为双保险：纯沉默（pending_count == 0）一律不触发。
        if pending_count < 1:
            return False, None, "pending=0，不允许纯沉默触发"

        runtime = self._runtime
        average_message_interval = runtime._get_recent_average_external_message_interval()
        if average_message_interval is None or average_message_interval <= 0:
            return False, None, "平均消息间隔不可用，无法进行空窗补偿"

        last_external_received_at = runtime._last_external_message_received_at or runtime._last_message_received_at
        idle_seconds = max(0.0, time.time() - last_external_received_at)
        # 即便空窗无限长，也不能让纯沉默跨过阈值。
        idle_equivalent_count = min(
            idle_seconds / average_message_interval,
            float(max(0, trigger_threshold - 1)),
        )
        equivalent_message_count = pending_count + idle_equivalent_count
        detail = (
            f"平均间隔={average_message_interval:.2f}s "
            f"空窗={idle_seconds:.2f}s "
            f"空窗折算={idle_equivalent_count:.2f} "
            f"等效消息数={equivalent_message_count:.2f}/{trigger_threshold}"
        )
        if equivalent_message_count >= trigger_threshold:
            return True, None, detail

        delay_seconds = max(0.0, (trigger_threshold - pending_count) * average_message_interval - idle_seconds)
        return False, delay_seconds, f"{detail} 延迟={delay_seconds:.2f}s"
