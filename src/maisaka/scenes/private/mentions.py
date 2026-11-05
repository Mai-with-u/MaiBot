"""私聊只识别对话中的提及，不读取群聊 @ 与概率开关。"""

from typing import Tuple

from src.chat.message_receive.message import SessionMessage
from src.maisaka.scenes.mentions import detect_text_mention, mention_context


def detect_mentions(message: SessionMessage) -> Tuple[bool, bool, float]:
    # 适配器的显式提及和数值提升继续兼容，但 at_bot 不代表私聊 @。
    add_cfg = message.message_info.additional_config or {}
    mentioned = bool(message.is_mentioned)
    probability = 0.0
    if isinstance(add_cfg, dict) and add_cfg.get("is_mentioned"):
        mentioned = True
        raw_boost = add_cfg["is_mentioned"]
        # 数值型标记是概率提升；布尔标记只表示命中。
        if not isinstance(raw_boost, bool):
            probability = float(raw_boost)
    mentioned = detect_text_mention(mention_context(message), mentioned)
    return mentioned, False, probability
