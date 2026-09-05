"""WebUI 聊天路由包。"""

from typing import Tuple

from fastapi import APIRouter, Depends

from src.webui.dependencies import require_auth

from .chat_streams import router as chat_streams_router
from .local_chat import router as local_chat_router
from .service import ChatConnectionManager, WEBUI_CHAT_PLATFORM, chat_manager

# 前缀只挂在父路由上，避免子路由再次叠加 /api/chat
router = APIRouter(prefix="/api/chat", dependencies=[Depends(require_auth)])
router.include_router(local_chat_router)
router.include_router(chat_streams_router)


def get_webui_chat_broadcaster() -> Tuple[ChatConnectionManager, str]:
    """获取 WebUI 聊天广播器，供外部模块使用。"""
    return chat_manager, WEBUI_CHAT_PLATFORM


__all__ = [
    "ChatConnectionManager",
    "WEBUI_CHAT_PLATFORM",
    "chat_manager",
    "get_webui_chat_broadcaster",
    "router",
]
