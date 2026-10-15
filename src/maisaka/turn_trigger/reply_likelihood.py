"""估计一批新消息进入 Planner 后会调用 reply 的概率。

权重由无必要性门控期间的 Planner 首轮记录拟合得到（scripts/explore_reply_necessity.py），
样本只包含“有新的外部消息且没有 @ 麦麦”的轮次；被 @ 的消息由强制触发直接放行，不经过本函数。
重新拟合后只需要更新本文件中的权重，以及 dynamic_gate.py 里的离线阈值表。
"""

from dataclasses import dataclass
from math import exp, log1p
from typing import Sequence, Tuple

MAX_PENDING_COUNT = 20
"""待处理消息数进入函数前的上限，与拟合时的截断一致。"""

DEFAULT_SECONDS_SINCE_BOT_MESSAGE = 3600.0
"""上下文中没有麦麦发言时采用的间隔，与拟合时的缺省值一致。"""

MEDIA_PLACEHOLDER_PREFIXES = ("[表情包", "[图片", "[消息类型]", "[CQ:image", "[语音", "[文件]", "[卡片")
"""只有媒体占位、没有文字内容的消息前缀。"""

# 拟合权重：logit = 截距 + Σ 权重 × 特征
_INTERCEPT = -1.2005
_WEIGHT_MENTION_BOT = 1.5852
_WEIGHT_AT_OTHER = -1.1157
_WEIGHT_QUESTION_MARK = 0.4807
_WEIGHT_PLACEHOLDER_ONLY = -1.0281
_WEIGHT_RECENT_SELF_RATIO = 2.6525
_WEIGHT_RECENT_MESSAGE_COUNT = -0.0367
_WEIGHT_LOG_SECONDS_SINCE_BOT = 0.0571
_WEIGHT_PENDING_COUNT = 0.1012


@dataclass(frozen=True, slots=True)
class ReplyLikelihoodInput:
    """估计 reply 概率所需的对话快照。"""

    mention_bot: bool
    """新消息中是否提到了麦麦的名字。"""

    at_other: bool
    """新消息中是否 @ 了麦麦以外的人。"""

    has_question_mark: bool
    """新消息中是否带问号。"""

    placeholder_only: bool
    """新消息是否全是表情、图片等媒体占位。"""

    recent_self_ratio: float
    """最近 5 分钟的消息里麦麦发言所占比例。"""

    recent_message_count: int
    """最近 5 分钟的消息条数。"""

    seconds_since_bot_message: float
    """距离麦麦上一次发言的秒数。"""

    pending_count: int
    """本批新的外部消息条数。"""


def summarize_pending_texts(texts: Sequence[str], *, has_at_bot: bool) -> Tuple[bool, bool, bool]:
    """从新消息文本得到 (at_other, has_question_mark, placeholder_only)。"""

    stripped_texts = [text.strip() for text in texts if text.strip()]
    at_other = not has_at_bot and any("@" in text for text in stripped_texts)
    has_question_mark = any("?" in text or "？" in text for text in stripped_texts)
    placeholder_only = bool(stripped_texts) and all(
        text.startswith(MEDIA_PLACEHOLDER_PREFIXES) for text in stripped_texts
    )
    return at_other, has_question_mark, placeholder_only


def estimate_reply_probability(likelihood_input: ReplyLikelihoodInput) -> float:
    """估计这批消息进入 Planner 后会调用 reply 的概率。"""

    logit = (
        _INTERCEPT
        + _WEIGHT_MENTION_BOT * float(likelihood_input.mention_bot)
        + _WEIGHT_AT_OTHER * float(likelihood_input.at_other)
        + _WEIGHT_QUESTION_MARK * float(likelihood_input.has_question_mark)
        + _WEIGHT_PLACEHOLDER_ONLY * float(likelihood_input.placeholder_only)
        + _WEIGHT_RECENT_SELF_RATIO * min(1.0, max(0.0, likelihood_input.recent_self_ratio))
        + _WEIGHT_RECENT_MESSAGE_COUNT * max(0, likelihood_input.recent_message_count)
        + _WEIGHT_LOG_SECONDS_SINCE_BOT * log1p(max(0.0, likelihood_input.seconds_since_bot_message))
        + _WEIGHT_PENDING_COUNT * min(MAX_PENDING_COUNT, max(0, likelihood_input.pending_count))
    )
    return 1.0 / (1.0 + exp(-logit))
