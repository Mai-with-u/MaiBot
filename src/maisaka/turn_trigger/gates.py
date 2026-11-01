"""Maisaka 消息触发门控。"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Literal, Optional, Sequence, Set, TYPE_CHECKING
import time

from src.chat.message_receive.message import SessionMessage
from src.common.logger import get_logger
from src.services.bot_account_service import get_online_bot_accounts

from .dynamic_gate import DynamicReplyGate
from .reply_likelihood import (
    DEFAULT_SECONDS_SINCE_BOT_MESSAGE,
    ReplyLikelihoodInput,
    estimate_reply_probability,
    summarize_pending_texts,
)

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting


TurnGateDecision = Literal["trigger", "wait", "delay", "consume"]
logger = get_logger("maisaka_turn_scheduler")
RECENT_PRESENCE_WINDOW_SECONDS = 300.0
"""统计近期消息量与麦麦发言占比的时间窗口。"""


@dataclass(frozen=True)
class TurnGateResult:
    """单个触发门控的判定结果。"""

    decision: TurnGateDecision
    detail: str
    delay_seconds: float = 0.0
    probability: float = 0.0
    threshold: float = 0.0
    consumed_count: Optional[int] = None

    @property
    def should_trigger(self) -> bool:
        return self.decision == "trigger"

    @property
    def should_consume(self) -> bool:
        return self.decision == "consume"


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
            probability=gate_decision.probability,
            threshold=gate_decision.threshold,
        )

    def apply_external_probability(
        self,
        *,
        probability: float,
        frequency: float,
        threshold_override: Optional[float] = None,
        now: Optional[float] = None,
    ) -> TurnGateResult:
        """使用外部 Hook 融合后的概率更新当前虚拟轮次需求，并重新计算动态门控判定。"""

        eval_time = time.time() if now is None else now
        clamped_probability = min(1.0, max(0.0, float(probability)))
        self._gate.update_latest_proactive_demand(clamped_probability, eval_time)
        gate_decision = self._gate.evaluate(clamped_probability, frequency, eval_time)
        effective_threshold = (
            min(1.0, max(0.0, float(threshold_override)))
            if threshold_override is not None
            else gate_decision.threshold
        )
        should_trigger = clamped_probability >= effective_threshold
        if should_trigger:
            self._gate.close_round()
        decision_label = "进入Planner" if should_trigger else "等待更多消息"
        return TurnGateResult(
            decision="trigger" if should_trigger else "wait",
            detail=f"动态门控: {gate_decision.detail} 判定={decision_label}",
            probability=clamped_probability,
            threshold=effective_threshold,
        )

    def close_round(self) -> None:
        """结束当前虚拟轮次。"""

        self._gate.close_round()

    def _filter_external_messages(self, pending_messages: Sequence[SessionMessage]) -> List[SessionMessage]:
        online_accounts = {platform: get_online_bot_accounts(platform) for platform in {m.platform for m in pending_messages}}
        external_messages: List[SessionMessage] = []
        filtered_accounts: Set[str] = set()
        for message in pending_messages:
            user_id = str(message.message_info.user_info.user_id).strip()
            if user_id in online_accounts[message.platform]:
                filtered_accounts.add(f"platform={message.platform} 过滤bot_id = {user_id}（自身消息）")
            else:
                external_messages.append(message)
        for detail in sorted(filtered_accounts):
            logger.info(f"{self._runtime.log_prefix} {detail}")
        return external_messages

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
            message.received_at is not None
            and (current_time - message.received_at).total_seconds() <= RECENT_PRESENCE_WINDOW_SECONDS
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
            is_self_message = message.source == "guided_reply"
            # 外部消息按接收时刻统计活跃度；自身发言仍按本机发送时间计算。
            activity_time = message.timestamp if is_self_message else message.received_at
            if activity_time is None:
                continue
            elapsed_seconds = (current_time - activity_time).total_seconds()
            if is_self_message and seconds_since_bot is None:
                seconds_since_bot = max(0.0, elapsed_seconds)
            if elapsed_seconds > RECENT_PRESENCE_WINDOW_SECONDS:
                # 媒体处理可能乱序完成，历史列表不保证按接收时刻排序。
                continue
            recent_total_count += 1
            if is_self_message:
                recent_self_count += 1
        if seconds_since_bot is None:
            seconds_since_bot = DEFAULT_SECONDS_SINCE_BOT_MESSAGE
        return recent_self_count, recent_total_count, seconds_since_bot

