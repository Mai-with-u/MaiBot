"""Maisaka 消息触发门控。"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Literal, Optional, Sequence, Set, TYPE_CHECKING
import time

from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import is_bot_self

from .dynamic_gate import DynamicReplyGate
from .reply_likelihood import (
    DEFAULT_SECONDS_SINCE_BOT_MESSAGE,
    ReplyLikelihoodInput,
    estimate_reply_probability,
    summarize_pending_texts,
)

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting


TurnGateDecision = Literal["trigger", "wait", "delay"]
RECENT_PRESENCE_WINDOW_SECONDS = 300.0
"""统计近期消息量与麦麦发言占比的时间窗口。"""


@dataclass(frozen=True)
class TurnGateResult:
    """单个触发门控的判定结果。"""

    decision: TurnGateDecision
    detail: str
    delay_seconds: Optional[float] = None

    @property
    def should_trigger(self) -> bool:
        return self.decision == "trigger"


class DynamicReplyTurnGate:
    """按估计的 reply 概率与回复频率动态决定是否进入 Planner。"""

    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        self._runtime = runtime
        self._gate = DynamicReplyGate()
        self._round_message_ids: Set[str] = set()
        """当前虚拟轮次内已到达的外部消息 ID。"""

    def record_reply(self) -> None:
        """记录 Planner 实际调用了一次 reply。"""

        self._gate.record_reply(time.time())

    def record_forced_turn(self, pending_messages: Sequence[SessionMessage]) -> None:
        """@ 强制触发时，把这批消息计入预计回复数；同一批消息只计一次。"""

        external_messages = self._filter_external_messages(pending_messages)
        if self._gate.take_uncounted_message_ids([message.message_id for message in external_messages]):
            self._gate.record_forced_turn(time.time())

    def evaluate(
        self,
        *,
        pending_messages: Sequence[SessionMessage],
        frequency: float,
    ) -> TurnGateResult:
        """返回动态门控的触发判定。"""

        external_messages = self._filter_external_messages(pending_messages)
        if not external_messages:
            return TurnGateResult(decision="wait", detail="没有新的外部消息 判定=等待更多消息")
        if not self._runtime.chat_stream.is_group_session:
            # 拟合数据只覆盖群聊；私聊里每条消息都是对麦麦说的，不设门控
            return TurnGateResult(decision="trigger", detail="私聊不设动态门控 判定=进入Planner")

        now = time.time()
        new_message_ids = set(
            self._gate.take_uncounted_message_ids([message.message_id for message in external_messages])
        )
        if new_message_ids:
            # 预计回复数按虚拟轮次计：只用本轮次内到达的消息估计，被门控拦下而积压的旧消息不重复计入
            if not self._gate.is_round_open(now):
                self._round_message_ids = set()
            self._round_message_ids |= new_message_ids
            round_messages = [message for message in external_messages if message.message_id in self._round_message_ids]
            self._gate.record_proactive_demand(
                estimate_reply_probability(
                    self._build_likelihood_input(round_messages, now, pending_messages=external_messages)
                ),
                now,
            )

        probability = estimate_reply_probability(
            self._build_likelihood_input(external_messages, now, pending_messages=external_messages)
        )
        gate_decision = self._gate.evaluate(probability, frequency, now)
        if gate_decision.should_trigger:
            self._gate.close_round()
        decision_label = "进入Planner" if gate_decision.should_trigger else "等待更多消息"
        return TurnGateResult(
            decision="trigger" if gate_decision.should_trigger else "wait",
            detail=f"动态门控: {gate_decision.detail} 判定={decision_label}",
        )

    def _filter_external_messages(self, pending_messages: Sequence[SessionMessage]) -> List[SessionMessage]:
        return [
            message
            for message in pending_messages
            if not is_bot_self(message.platform, message.message_info.user_info.user_id)
        ]

    def _build_likelihood_input(
        self,
        messages: Sequence[SessionMessage],
        now: float,
        *,
        pending_messages: Sequence[SessionMessage],
    ) -> ReplyLikelihoodInput:
        """按当前 runtime 快照为一批外部消息构造概率函数的输入。"""

        texts = [(message.processed_plain_text or "") for message in messages]
        at_other, has_question_mark, placeholder_only = summarize_pending_texts(
            texts,
            has_at_bot=any(message.is_at for message in messages),
        )
        recent_self_count, recent_history_count, seconds_since_bot = self._summarize_recent_history(now)
        # 待处理消息尚未写入内部历史，只补上最近 5 分钟内的消息，避免旧积压持续压低概率。
        # 近期活跃度属于整个聊天流；虚拟轮次评分与放行评分必须使用同一批待处理消息统计。
        current_time = datetime.fromtimestamp(now)
        recent_pending_count = sum(
            (current_time - message.timestamp).total_seconds() <= RECENT_PRESENCE_WINDOW_SECONDS
            for message in pending_messages
        )
        recent_message_count = recent_history_count + recent_pending_count
        return ReplyLikelihoodInput(
            mention_bot=any(message.is_mentioned for message in messages),
            at_other=at_other,
            has_question_mark=has_question_mark,
            placeholder_only=placeholder_only,
            recent_self_ratio=recent_self_count / max(1, recent_message_count),
            recent_message_count=recent_message_count,
            seconds_since_bot_message=seconds_since_bot,
            pending_count=len(messages),
        )

    def _summarize_recent_history(self, now: float) -> tuple[int, int, float]:
        """返回近期麦麦发言数、近期历史消息数，以及距麦麦上一次发言的秒数。"""

        current_time = datetime.fromtimestamp(now)
        recent_self_count = 0
        recent_total_count = 0
        seconds_since_bot: Optional[float] = None
        for message in reversed(self._runtime._chat_history):
            if not message.count_in_context:
                continue
            elapsed_seconds = (current_time - message.timestamp).total_seconds()
            is_self_message = message.source == "guided_reply"
            if is_self_message and seconds_since_bot is None:
                seconds_since_bot = max(0.0, elapsed_seconds)
            if elapsed_seconds > RECENT_PRESENCE_WINDOW_SECONDS:
                # 窗口外只需要继续找到麦麦最近一次发言
                if seconds_since_bot is not None:
                    break
                continue
            recent_total_count += 1
            if is_self_message:
                recent_self_count += 1
        if seconds_since_bot is None:
            seconds_since_bot = DEFAULT_SECONDS_SINCE_BOT_MESSAGE
        return recent_self_count, recent_total_count, seconds_since_bot


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
