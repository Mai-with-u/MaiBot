"""桌宠对已存在聊天流的绑定；消息来源不冒用平台用户身份。"""

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from src.chat.message_receive.chat_manager import BotChatSession


def describe_conversation(session: "BotChatSession") -> Dict[str, str]:
    target = session.group_id or session.user_id or ""
    name = (session.group_name if session.group_id else session.user_cardname or session.user_nickname) or target
    return {
        "conversation_id": session.session_id,
        "name": name,
        "platform": session.platform,
        "target_id": target,
        "chat_type": "group" if session.group_id else "private",
    }


def require_current_conversation(current: str, expected: Optional[str]) -> None:
    if current != (expected or ""):
        raise ValueError("桌宠会话已切换，请确认当前会话后重新发送")


def route_relay_message(packet: Dict[str, Any], session: "BotChatSession") -> None:
    info = packet["message_info"]
    packet["account_id"] = session.account_id
    packet["scope"] = session.scope
    info["platform"] = session.platform
    info["user_info"]["platform"] = session.platform
    info["user_info"]["user_nickname"] = "桌宠 · " + info["user_info"]["user_nickname"]
    info["user_info"]["user_cardname"] = info["user_info"]["user_nickname"]
    if session.group_id:
        info["group_info"] = {
            "group_id": session.group_id,
            "group_name": session.group_name or "",
            "platform": session.platform,
        }


def resolve_conversation(session_id: str) -> "BotChatSession":
    from src.chat.message_receive.chat_manager import chat_manager
    from src.core.internal_chat import INTERNAL_CHAT_PLATFORM
    from src.core.local_operator import BOT_CONSOLE_PLATFORM, MAISAKA_CLI_PLATFORM

    session = chat_manager.get_existing_session_by_session_id(session_id)
    if session is None or session.platform in {INTERNAL_CHAT_PLATFORM, BOT_CONSOLE_PLATFORM, MAISAKA_CLI_PLATFORM}:
        raise ValueError("所选聊天会话已不存在或不可绑定")
    if not (session.group_id or session.user_id):
        raise ValueError("所选聊天会话缺少目标")
    return session
