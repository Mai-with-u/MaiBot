"""按回复频率动态调整 Planner 放行阈值。

让窗口内实际的 reply 次数贴近“不设门控时预计的 reply 次数 × 回复频率”：
预计次数来自 reply_likelihood 给出的概率，实际次数按 Planner 成功调用 reply 的次数统计。
"""

from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Sequence, Set, Tuple

REPLY_WINDOW_SECONDS = 3600.0
"""统计预计回复数与实际回复数的滑动窗口长度。"""

FORCED_TURN_EXPECTED_REPLIES = 0.91
"""一次 @ 强制触发预计产生的 reply 次数（拟合数据中的实测均值）。"""

MIN_WINDOW_SCORE_COUNT = 20
"""窗口内已评分的批次少于该值时，改用离线统计的静态阈值表。"""

VIRTUAL_ROUND_SECONDS = 40.0
"""估算“不设门控时 Planner 会判定几次”所用的轮次时长。

不设门控时 Planner 并不是每条消息判一次：思考耗时、空闲退避与 wait 会把消息攒成一批（拟合数据中
平均每批 2.9 条）。把同一时长内到达的消息归为一个虚拟轮次、只计一次预计回复数；
按 40 秒切分时，切出的轮次数与拟合数据中的实际 Planner 轮次数最接近。
"""

MAX_COUNTED_MESSAGE_IDS = 2000
"""已计入预计回复数的消息 ID 最多保留多少个，用于避免同一条消息被重复计入。"""

# 离线统计：保留多少比例的主动回复时对应的概率阈值，窗口数据不足时使用
_STATIC_KEEP_RATIO_THRESHOLDS: Tuple[Tuple[float, float], ...] = (
    (0.0, 1.0),
    (0.1, 0.605),
    (0.2, 0.482),
    (0.3, 0.430),
    (0.4, 0.376),
    (0.5, 0.336),
    (0.6, 0.313),
    (0.7, 0.290),
    (0.8, 0.250),
    (0.9, 0.206),
    (1.0, 0.0),
)


def _static_threshold(keep_ratio: float) -> float:
    """按离线统计表线性插值出保留比例对应的概率阈值。"""

    for (low_ratio, low_threshold), (high_ratio, high_threshold) in zip(
        _STATIC_KEEP_RATIO_THRESHOLDS, _STATIC_KEEP_RATIO_THRESHOLDS[1:], strict=False
    ):
        if keep_ratio <= high_ratio:
            progress = (keep_ratio - low_ratio) / (high_ratio - low_ratio)
            return low_threshold + (high_threshold - low_threshold) * progress
    return 0.0


@dataclass(frozen=True, slots=True)
class DynamicGateDecision:
    """动态门控的一次判定。"""

    should_trigger: bool
    probability: float
    threshold: float
    expected_replies: float
    """窗口内不设门控时预计的 reply 次数。"""

    target_replies: float
    """窗口内按回复频率折算后的目标 reply 次数。"""

    actual_replies: int
    """窗口内实际的 reply 次数。"""

    keep_ratio: float
    """当前允许保留的主动回复比例。"""

    @property
    def detail(self) -> str:
        return (
            f"概率={self.probability:.2f} 阈值={self.threshold:.2f} "
            f"预计回复={self.expected_replies:.1f} 目标={self.target_replies:.1f} "
            f"已回复={self.actual_replies} 保留比例={self.keep_ratio:.2f}"
        )


class DynamicReplyGate:
    """单个聊天流的动态回复门控状态。

    预计回复数按虚拟轮次累计：没被 @ 的消息每个轮次计一次函数概率，被 @ 的强制触发计一次实测均值。
    阈值由两部分决定：
    - 前馈：目标回复数先扣除 @ 强制触发预计占用的部分，剩余额度占主动回复预计数的比例即保留比例；
    - 反馈：窗口内实际 reply 次数高于目标就收紧保留比例。只收紧不放宽：一波消息刚到时预计数先涨、
      实际回复还没跟上，若据此放宽，等这波消息过去就没有机会再收回来，回复会系统性偏多。
    然后在窗口内的评分里，按概率从高到低累计到保留比例处，得到当前阈值。
    """

    def __init__(self, window_seconds: float = REPLY_WINDOW_SECONDS) -> None:
        self._window_seconds = window_seconds
        self._proactive_scores: Deque[Tuple[float, float]] = deque()
        self._forced_expectations: Deque[Tuple[float, float]] = deque()
        self._reply_times: Deque[float] = deque()
        self._counted_message_ids: Set[str] = set()
        self._counted_message_order: Deque[str] = deque()
        self._open_round_started_at: float | None = None

    def take_uncounted_message_ids(self, message_ids: Sequence[str]) -> List[str]:
        """返回尚未计入预计回复数的消息 ID，并把它们标记为已计入。"""

        new_ids: List[str] = []
        for message_id in message_ids:
            if not message_id or message_id in self._counted_message_ids:
                continue
            new_ids.append(message_id)
            self._counted_message_ids.add(message_id)
            self._counted_message_order.append(message_id)
        while len(self._counted_message_order) > MAX_COUNTED_MESSAGE_IDS:
            self._counted_message_ids.discard(self._counted_message_order.popleft())
        return new_ids

    def is_round_open(self, now: float) -> bool:
        """当前是否仍处在一个尚未结束的虚拟轮次内。"""

        return self._open_round_started_at is not None and now - self._open_round_started_at < VIRTUAL_ROUND_SECONDS

    def record_proactive_demand(self, probability: float, now: float) -> None:
        """记录当前虚拟轮次预计带来的 reply 次数。

        轮次仍未结束时，覆盖该轮次已记录的概率（同一轮次只计一次）；否则开启新的轮次。
        """

        if self.is_round_open(now) and self._proactive_scores:
            round_started_at, _ = self._proactive_scores[-1]
            self._proactive_scores[-1] = (round_started_at, probability)
            return
        self._open_round_started_at = now
        self._proactive_scores.append((now, probability))

    def close_round(self) -> None:
        """Planner 已实际运行，结束当前虚拟轮次；之后到达的消息属于下一个轮次。"""

        self._open_round_started_at = None

    def record_forced_turn(self, now: float) -> None:
        """记录一次 @ 强制触发预计带来的 reply 次数；Planner 随即运行，当前虚拟轮次同时结束。"""

        self.close_round()
        self._forced_expectations.append((now, FORCED_TURN_EXPECTED_REPLIES))

    def record_reply(self, now: float) -> None:
        """记录 Planner 实际调用了一次 reply。"""

        self._reply_times.append(now)

    def _prune(self, now: float) -> None:
        expire_before = now - self._window_seconds
        for timed_values in (self._proactive_scores, self._forced_expectations):
            while timed_values and timed_values[0][0] < expire_before:
                timed_values.popleft()
        while self._reply_times and self._reply_times[0] < expire_before:
            self._reply_times.popleft()

    def evaluate(self, probability: float, frequency: float, now: float) -> DynamicGateDecision:
        """判断当前这批待处理消息是否放行进入 Planner。"""

        self._prune(now)
        proactive_scores = [score for _, score in self._proactive_scores]
        expected_proactive = sum(proactive_scores)
        expected_forced = sum(value for _, value in self._forced_expectations)
        expected_total = expected_proactive + expected_forced
        actual_replies = len(self._reply_times)
        normalized_frequency = min(1.0, max(0.0, frequency))
        target_replies = normalized_frequency * expected_total

        if normalized_frequency >= 1.0:
            keep_ratio = 1.0
        elif expected_proactive <= 0:
            keep_ratio = normalized_frequency
        else:
            feedforward_ratio = (target_replies - expected_forced) / expected_proactive
            feedback_factor = min(1.0, max(0.0, 1.0 + (target_replies - actual_replies) / max(1.0, target_replies)))
            keep_ratio = min(1.0, max(0.0, feedforward_ratio * feedback_factor))

        threshold = self._resolve_threshold(proactive_scores, expected_proactive, keep_ratio)
        return DynamicGateDecision(
            should_trigger=probability >= threshold,
            probability=probability,
            threshold=threshold,
            expected_replies=expected_total,
            target_replies=target_replies,
            actual_replies=actual_replies,
            keep_ratio=keep_ratio,
        )

    @staticmethod
    def _resolve_threshold(proactive_scores: Sequence[float], expected_proactive: float, keep_ratio: float) -> float:
        if keep_ratio >= 1.0:
            return 0.0
        if keep_ratio <= 0.0:
            return 1.0
        if len(proactive_scores) < MIN_WINDOW_SCORE_COUNT:
            return _static_threshold(keep_ratio)

        # 按概率从高到低累计，累计到保留额度时的概率即阈值
        budget = keep_ratio * expected_proactive
        accumulated = 0.0
        for score in sorted(proactive_scores, reverse=True):
            accumulated += score
            if accumulated >= budget:
                return score
        return 0.0
