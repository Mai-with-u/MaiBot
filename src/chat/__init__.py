"""
MaiBot模块系统
包含聊天、情绪、记忆、日程等功能模块
"""

from src.chat.emoji_system.emoji_manager import emoji_manager
from src.chat.message_receive.chat_manager import chat_manager

# 导出主要组件供外部使用
__all__ = [
    "chat_manager",
    "emoji_manager",
]
