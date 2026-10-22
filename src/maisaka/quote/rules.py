"""群聊与私聊固定引用概率，使用统一计数器快照。"""

from typing import Literal, TYPE_CHECKING

import math
import random

if TYPE_CHECKING:
    from .counter import ReplyQuoteContext

QuoteFrequency = Literal["never", "occasionally", "often"]


def quote_probability(
    *,
    text_length: int,
    previous_sender_self: bool,
    previous_seconds: float,
    frequency: QuoteFrequency,
    target_distance: int,
) -> float:
    """八类基础概率乘以频率档位和目标距离系数。"""
    if target_distance < 0:
        raise ValueError("引用目标距离不能为负数")
    if frequency == "never":
        return 0.0
    if frequency not in ("occasionally", "often"):
        raise ValueError(f"未知的群聊引用频率：{frequency}")
    if text_length == 0:
        if previous_sender_self:
            probability = 0.005913471588878362 if previous_seconds <= 3 else 0.013812173277286514
        else:
            # 前一分钟是否有人发言，等价于上一条发言距今是否不超过60秒。
            probability = 0.025472825783009516 if previous_seconds <= 60 else 0.0108627046866078
    elif previous_sender_self:
        probability = 0.011765162067321502 if previous_seconds <= 3 else 0.07760369512696548
    else:
        probability = 0.20934408508581265 if text_length <= 50 else 0.08614950202149084
    if target_distance == 0:
        distance_multiplier = 0.5
    elif target_distance <= 2:
        distance_multiplier = 1.0
    elif target_distance <= 5:
        distance_multiplier = 1.1
    else:
        distance_multiplier = 1.5
    return min(1.0, probability * (2 if frequency == "often" else 1) * distance_multiplier)


def private_quote_probability(
    *, text_length: int, previous_sender_self: bool, previous_seconds: float,
    previous_burst: int, target_distance: int, frequency: QuoteFrequency,
) -> float:
    """私聊固定公式，距离参数为中间隔的消息数。

    群聊上下文共用零基距离；转成公式中的往前第几条后，最多按第32条计算，
    避免超出拟合窗口继续外推。系数来自离线AI弱标签试验，不在线更新。
    """
    if target_distance < 0:
        raise ValueError("引用目标距离不能为负数")
    if frequency == "never":
        return 0.0
    if frequency not in ("occasionally", "often"):
        raise ValueError(f"未知的私聊引用频率：{frequency}")
    z = -4.176576611204103
    if previous_sender_self:
        z += -2.0390866101745555 if previous_seconds <= 3 else 0.407259409803155
    else:
        z += 0.6811570734750046 * math.log2(max(1, min(previous_burst, 16)))
    if text_length == 0:
        z -= 3.4527659205203576
    z += 0.521800209819322 * math.log2(min(target_distance + 1, 32))
    probability = 1 / (1 + math.exp(-z))
    return min(1.0, probability * (2 if frequency == "often" else 1))


def decide_quote(
    *, context: "ReplyQuoteContext | None", bot_user_id: str, text: str, frequency: QuoteFrequency, is_group: bool,
) -> tuple[bool, float]:
    """窗口内按公式抽签，窗口外或历史目标无序号时引用；不引用档优先。"""
    if frequency == "never":
        return False, 0.0
    if frequency not in ("occasionally", "often"):
        raise ValueError(f"未知的引用频率：{frequency}")
    if context is None or context.target_distance is None:
        return True, 1.0
    common = dict(
        text_length=len(text.strip()), previous_sender_self=context.previous_sender_id == bot_user_id,
        previous_seconds=context.previous_seconds, target_distance=context.target_distance, frequency=frequency,
    )
    probability = (
        quote_probability(**common) if is_group
        else private_quote_probability(previous_burst=context.previous_burst, **common)
    )
    return random.random() < probability, probability
