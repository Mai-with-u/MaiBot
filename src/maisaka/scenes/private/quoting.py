"""私聊固定引用概率。"""

import math

from src.maisaka.scenes.base import QuoteFrequency


def private_quote_probability(
    *,
    text_length: int,
    previous_sender_self: bool,
    previous_seconds: float,
    previous_burst: int,
    target_distance: int,
    frequency: QuoteFrequency,
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
