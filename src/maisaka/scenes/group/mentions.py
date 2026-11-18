"""群聊提及识别：适配器标记、结构化 @、文本 @ 与群聊触发开关。"""

from typing import Tuple
import re

from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import _has_at_component_targeting_bot
from src.common.logger import get_logger
from src.config.config import global_config
from src.maisaka.scenes.mentions import detect_text_mention, mention_context

logger = get_logger("chat_utils")


def detect_mentions(message: SessionMessage) -> Tuple[bool, bool, float]:
    context = mention_context(message)
    text, platform, current_accounts = context.text, context.platform, context.current_accounts
    reply_probability = 0.0
    is_at = False
    is_mentioned = False

    # 1) 直接的 additional_config 标记
    add_cfg = message.message_info.additional_config or {}
    if isinstance(add_cfg, dict):
        if add_cfg.get("at_bot") or add_cfg.get("is_mentioned"):
            is_mentioned = True
            if add_cfg.get("at_bot"):
                is_at = True
            # 当提供数值型 is_mentioned 时，当作概率提升；布尔提及标记只负责标记命中。
            raw_mention_boost = add_cfg.get("is_mentioned")
            if raw_mention_boost not in (None, "") and not isinstance(raw_mention_boost, bool):
                reply_probability = float(raw_mention_boost)

    # 2) 已经在上游设置过的 message.is_at / message.is_mentioned
    if message.is_at:
        is_at = True
        is_mentioned = True
    if message.is_mentioned:
        is_mentioned = True

    # 3) 扫描分段：是否包含 mention_bot（适配器插入）
    def _has_mention_bot(seg) -> bool:
        try:
            if seg is None:
                return False
            if getattr(seg, "type", None) == "mention_bot":
                return True
            if getattr(seg, "type", None) == "seglist":
                for s in getattr(seg, "data", []) or []:
                    if _has_mention_bot(s):
                        return True
            return False
        except Exception:
            return False

    if _has_mention_bot(getattr(message, "message_segment", None)):
        is_at = True
        is_mentioned = True

    # 4) 结构化 @ 组件检测。处理后的文本可能只剩群名片，不能依赖文本里的显示名判断。
    if not is_at and _has_at_component_targeting_bot(message, platform):
        is_at = True
        is_mentioned = True

    # 5) 统一的 @ 检测逻辑
    if current_accounts and not is_at and not is_mentioned:
        for current_account in current_accounts:
            pattern = (
                rf"@<(.+?):{re.escape(current_account)}>"
                if platform == "qq"
                else rf"@{re.escape(current_account)}(\b|$)"
            )
            if re.search(pattern, text, flags=re.IGNORECASE):
                is_at = True
                is_mentioned = True
                break

    is_mentioned = detect_text_mention(context, is_mentioned)

    # 8) 概率设置
    reply_timing_config = global_config.chat.reply_timing
    if is_at and reply_timing_config.inevitable_at_reply:
        reply_probability = 1.0
        logger.debug("被@，回复概率设置为100%")
    elif is_mentioned and reply_timing_config.mentioned_bot_reply:
        reply_probability = max(reply_probability, 1.0)
        logger.debug("被提及，回复概率设置为100%")

    return is_mentioned, is_at, reply_probability
