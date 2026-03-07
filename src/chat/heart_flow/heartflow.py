from typing import Optional, Dict

import traceback

from src.common.logger import get_logger
from src.chat.message_receive.chat_manager import chat_manager
from src.chat.heart_flow.heartFC_chat import HeartFChatting
from src.chat.brain_chat.brain_chat import BrainChatting

logger = get_logger("heartflow")


class Heartflow:
    """主心流协调器，负责初始化并协调聊天"""

    def __init__(self):
        self.heartflow_chat_list: Dict[str, HeartFChatting | BrainChatting] = {}

    async def get_or_create_heartflow_chat(self, session_id: str) -> Optional[HeartFChatting | BrainChatting]:
        """获取或创建一个新的HeartFChatting实例"""
        try:
            if chat := self.heartflow_chat_list.get(session_id):
                return chat
            chat_session = chat_manager.get_session_by_session_id(session_id)
            if not chat_session:
                raise ValueError(f"未找到 session_id={session_id} 的聊天流")
            new_chat = (
                HeartFChatting(session_id=session_id) if chat_session.group_id else BrainChatting(session_id=session_id)
            )
            await new_chat.start()
            self.heartflow_chat_list[session_id] = new_chat
            return new_chat
        except Exception as e:
            logger.error(f"创建心流聊天 {session_id} 失败: {e}", exc_info=True)
            traceback.print_exc()
        return None


heartflow = Heartflow()
