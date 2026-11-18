"""群聊与私聊复用的文本提及识别，不决定触发策略。"""

from dataclasses import dataclass
from typing import List
import re

from src.chat.message_receive.message import SessionMessage
from src.config.config import global_config
from src.services.bot_account_service import get_bot_accounts


@dataclass(frozen=True)
class MentionContext:
    text: str
    platform: str
    current_accounts: List[str]
    keywords: List[str]


def mention_context(message: SessionMessage) -> MentionContext:
    platform = str(message.platform or "").strip().lower()
    return MentionContext(
        text=message.processed_plain_text or "",
        platform=platform,
        current_accounts=list(get_bot_accounts(platform)),
        keywords=[global_config.bot.nickname] + global_config.bot.alias_names,
    )


def detect_text_mention(context: MentionContext, is_mentioned: bool) -> bool:
    text, current_accounts, keywords = context.text, context.current_accounts, context.keywords
    # 6) 统一的回复检测逻辑
    if not is_mentioned:
        # 通用回复格式：包含 "(你)" 或 "（你）"
        if re.search(r"\[回复 .*?\(你\)：", text) or re.search(r"\[回复 .*?（你）：", text):
            is_mentioned = True
        # ID 形式的回复检测
        elif current_accounts:
            for current_account in current_accounts:
                if re.search(rf"\[回复 (.+?)\({re.escape(current_account)}\)：(.+?)\]，说：", text):
                    is_mentioned = True
                    break
                if re.search(
                    rf"\[回复<(.+?)(?=:{re.escape(current_account)}>)\:{re.escape(current_account)}>：(.+?)\]，说：",
                    text,
                ):
                    is_mentioned = True
                    break

    # 7) 名称/别名 提及（去除 @/回复标记后再匹配）
    if not is_mentioned and keywords:
        msg_content = text
        # 去除各种 @ 与 回复标记，避免误判
        msg_content = re.sub(r"@(.+?)（(\d+)）", "", msg_content)
        msg_content = re.sub(r"@<(.+?)(?=:(\d+))\:(\d+)>", "", msg_content)
        msg_content = re.sub(r"\[回复 (.+?)\(((\d+)|未知id|你)\)：(.+?)\]，说：", "", msg_content)
        msg_content = re.sub(r"\[回复<(.+?)(?=:(\d+))\:(\d+)>：(.+?)\]，说：", "", msg_content)
        for kw in keywords:
            if kw and kw in msg_content:
                is_mentioned = True
                break

    return is_mentioned
