import asyncio
import time
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

    # 最大缓存的聊天实例数量
    MAX_CHAT_INSTANCES = 100
    # 不活跃阈值（秒），超过此时间未访问且已停止的实例将被清理
    INACTIVE_THRESHOLD = 3600  # 1小时

    def __init__(self):
        self.heartflow_chat_list: Dict[Any, HeartFChatting | BrainChatting] = {}
        self._last_access_time: Dict[Any, float] = {}
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start_cleanup_task(self):
        """启动定期清理任务"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

    async def _periodic_cleanup(self):
        """定期清理不活跃的聊天实例"""
        while True:
            await asyncio.sleep(600)  # 每10分钟检查一次
            try:
                await self._cleanup_inactive_chats()
            except Exception as e:
                logger.error(f"清理不活跃聊天实例时出错: {e}")

    async def _cleanup_inactive_chats(self):
        """清理不活跃且已停止的聊天实例"""
        now = time.time()
        to_remove = []

        for chat_id, last_time in list(self._last_access_time.items()):
            # 检查是否超过不活跃阈值
            if now - last_time > self.INACTIVE_THRESHOLD:
                chat = self.heartflow_chat_list.get(chat_id)
                # 只有当实例已停止运行时才清理
                if chat is None or not getattr(chat, 'running', False):
                    to_remove.append(chat_id)

        for chat_id in to_remove:
            self.heartflow_chat_list.pop(chat_id, None)
            self._last_access_time.pop(chat_id, None)

        if to_remove:
            logger.info(f"已清理 {len(to_remove)} 个不活跃的聊天实例")

    def _enforce_max_size(self):
        """强制限制最大缓存数量"""
        while len(self.heartflow_chat_list) > self.MAX_CHAT_INSTANCES:
            # 找到最久未访问的实例
            oldest_chat_id = min(self._last_access_time.keys(), key=lambda k: self._last_access_time[k])
            chat = self.heartflow_chat_list.get(oldest_chat_id)

            # 尝试停止实例
            if chat and getattr(chat, 'running', False):
                chat.running = False

            self.heartflow_chat_list.pop(oldest_chat_id, None)
            self._last_access_time.pop(oldest_chat_id, None)
            logger.debug(f"移除最久未访问的聊天实例: {oldest_chat_id}")

    async def get_or_create_heartflow_chat(self, chat_id: Any) -> Optional[HeartFChatting | BrainChatting]:
        """获取或创建一个新的HeartFChatting实例"""
        try:
            # 更新访问时间
            self._last_access_time[chat_id] = time.time()

            if chat_id in self.heartflow_chat_list:
                if chat := self.heartflow_chat_list.get(chat_id):
                    return chat
            else:
                chat_stream: ChatStream | None = get_chat_manager().get_stream(chat_id)
                if not chat_stream:
                    raise ValueError(f"未找到 chat_id={chat_id} 的聊天流")
                if chat_stream.group_info:
                    new_chat = HeartFChatting(chat_id=chat_id)
                else:
                    new_chat = BrainChatting(chat_id=chat_id)
                await new_chat.start()
                self.heartflow_chat_list[chat_id] = new_chat

                # 检查并限制最大数量
                self._enforce_max_size()

                return new_chat
        except Exception as e:
            logger.error(f"创建心流聊天 {chat_id} 失败: {e}", exc_info=True)
            traceback.print_exc()
            return None


heartflow = Heartflow()
