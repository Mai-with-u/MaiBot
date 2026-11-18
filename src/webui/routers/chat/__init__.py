from typing import Tuple

from .routes import router
from .service import INTERNAL_CHAT_PLATFORM, WEBUI_CHAT_PLATFORM, ChatConnectionManager, chat_manager


def get_webui_chat_broadcaster() -> Tuple[ChatConnectionManager, str]:
    """获取 WebUI 聊天广播器，供外部模块使用。"""
    return chat_manager, INTERNAL_CHAT_PLATFORM


__all__ = [
    "ChatConnectionManager",
    "INTERNAL_CHAT_PLATFORM",
    "WEBUI_CHAT_PLATFORM",
    "chat_manager",
    "get_webui_chat_broadcaster",
    "router",
]
