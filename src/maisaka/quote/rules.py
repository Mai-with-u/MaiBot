"""共享引用抽签流程，兼容原有公式导入接口。"""

from typing import TYPE_CHECKING, Tuple
import random

from src.maisaka.scenes.base import QuoteFrequency
from src.maisaka.scenes.factory import get_scene
from src.maisaka.scenes.group.quoting import quote_probability as quote_probability
from src.maisaka.scenes.private.quoting import private_quote_probability as private_quote_probability

if TYPE_CHECKING:
    from .counter import ReplyQuoteContext


def decide_quote(
    *, context: "ReplyQuoteContext | None", bot_user_id: str, text: str, frequency: QuoteFrequency, is_group: bool,
) -> Tuple[bool, float]:
    """窗口内按公式抽签，窗口外或历史目标无序号时引用；不引用档优先。"""
    if frequency == "never":
        return False, 0.0
    if frequency not in ("occasionally", "often"):
        raise ValueError(f"未知的引用频率：{frequency}")
    if context is None or context.target_distance is None:
        return True, 1.0
    probability = get_scene(is_group).quote_probability(context, bot_user_id, text, frequency)
    return random.random() < probability, probability
