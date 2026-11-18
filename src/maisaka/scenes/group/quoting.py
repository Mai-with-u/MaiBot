"""群聊固定引用概率。"""

from src.maisaka.scenes.base import QuoteFrequency


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
