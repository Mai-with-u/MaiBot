import asyncio
import traceback
from typing import Any, Optional, Dict

from src.chat.message_receive.chat_stream import get_chat_manager
from src.common.logger import get_logger
from src.chat.heart_flow.heartFC_chat import HeartFChatting
from src.chat.brain_chat.brain_chat import BrainChatting
from src.chat.message_receive.chat_stream import ChatStream

logger = get_logger("heartflow")


class Heartflow:
    """主心流协调器，负责初始化并协调聊天"""

    def __init__(self):
        self.heartflow_chat_list: Dict[Any, HeartFChatting | BrainChatting] = {}
        self._create_locks: Dict[Any, asyncio.Lock] = {}

    async def get_or_create_heartflow_chat(self, chat_id: Any) -> Optional[HeartFChatting | BrainChatting]:
        """获取或创建一个新的HeartFChatting实例"""
        if chat_id in self.heartflow_chat_list:
            if chat := self.heartflow_chat_list.get(chat_id):
                return chat

        # 获取 per-chat_id 锁，防止并发创建多个实例
        if chat_id not in self._create_locks:
            self._create_locks[chat_id] = asyncio.Lock()
        async with self._create_locks[chat_id]:
            # 在等待锁期间可能已被其他协程创建
            if chat_id in self.heartflow_chat_list:
                if chat := self.heartflow_chat_list.get(chat_id):
                    return chat
            try:
                chat_stream: ChatStream | None = get_chat_manager().get_stream(chat_id)
                if not chat_stream:
                    raise ValueError(f"未找到 chat_id={chat_id} 的聊天流")
                if chat_stream.group_info:
                    new_chat = HeartFChatting(chat_id=chat_id)
                else:
                    new_chat = BrainChatting(chat_id=chat_id)
                await new_chat.start()
                self.heartflow_chat_list[chat_id] = new_chat
                return new_chat
            except Exception as e:
                logger.error(f"创建心流聊天 {chat_id} 失败: {e}", exc_info=True)
                traceback.print_exc()
                return None


heartflow = Heartflow()
