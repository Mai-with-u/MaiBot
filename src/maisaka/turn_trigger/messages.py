"""消息触发前共享的在线自身账号过滤。"""

from typing import List, Sequence, Set

from src.chat.message_receive.message import SessionMessage
from src.common.logger import get_logger
from src.services.bot_account_service import get_online_bot_accounts

logger = get_logger("maisaka_turn_scheduler")


def filter_external_messages(pending_messages: Sequence[SessionMessage], *, log_prefix: str) -> List[SessionMessage]:
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
        logger.info(f"{log_prefix} {detail}")
    return external_messages

