from collections import OrderedDict
from typing import Dict

import asyncio
import time
import traceback

from src.chat.message_receive.chat_manager import chat_manager
from src.common.logger import get_logger
from src.maisaka.runtime import MaisakaHeartFlowChatting

logger = get_logger("heartflow")

HEARTFLOW_ACTIVE_RETENTION_SECONDS = 24 * 60 * 60
HEARTFLOW_MAX_ACTIVE_CHATS = 100


class HeartflowManager:
    """管理 session 级别的 Maisaka 心流实例。"""

    def __init__(self) -> None:
        self.heartflow_chat_list: OrderedDict[str, MaisakaHeartFlowChatting] = OrderedDict()
        self._chat_create_locks: Dict[str, asyncio.Lock] = {}
        self._chat_last_active_at: Dict[str, float] = {}

    async def get_or_create_heartflow_chat(self, session_id: str) -> MaisakaHeartFlowChatting:
        """获取或创建指定会话对应的 Maisaka runtime。"""
        try:
            if chat := self.heartflow_chat_list.get(session_id):
                self._touch_chat(session_id)
                return chat

            create_lock = self._chat_create_locks.setdefault(session_id, asyncio.Lock())
            async with create_lock:
                if chat := self.heartflow_chat_list.get(session_id):
                    self._touch_chat(session_id)
                    return chat

                chat_session = chat_manager.get_session_by_session_id(session_id)
                if not chat_session:
                    raise ValueError(f"未找到 session_id={session_id} 对应的聊天流")

                new_chat = MaisakaHeartFlowChatting(session_id=session_id)
                await new_chat.start()
                self.heartflow_chat_list[session_id] = new_chat
                self._touch_chat(session_id)
                await self._evict_over_limit_chats(protected_session_id=session_id)
                return new_chat
        except Exception as exc:
            logger.error(f"创建心流聊天 {session_id} 失败: {exc}", exc_info=True)
            traceback.print_exc()
            raise

    def _touch_chat(self, session_id: str) -> None:
        """记录会话最近活跃时间，并维护心流实例的 LRU 顺序。"""
        self._chat_last_active_at[session_id] = time.time()
        self.heartflow_chat_list.move_to_end(session_id)

    async def _evict_over_limit_chats(self, *, protected_session_id: str) -> None:
        """当实例数量超过上限时，仅淘汰 24 小时内无消息的旧会话。"""
        while len(self.heartflow_chat_list) > HEARTFLOW_MAX_ACTIVE_CHATS:
            session_id = self._find_evictable_session_id(protected_session_id=protected_session_id)
            if session_id is None:
                return
            await self._evict_chat(session_id, reason="cache_limit")

    def _find_evictable_session_id(self, *, protected_session_id: str) -> str | None:
        """按 LRU 查找超过活跃保护窗口的可淘汰会话。"""
        expire_before = time.time() - HEARTFLOW_ACTIVE_RETENTION_SECONDS
        for session_id in self.heartflow_chat_list:
            if session_id == protected_session_id:
                continue
            last_active_at = self._chat_last_active_at.get(session_id, 0.0)
            if last_active_at <= expire_before:
                return session_id
        return None

    async def _evict_chat(self, session_id: str, *, reason: str) -> None:
        """停止并移除指定会话的心流实例。"""
        chat = self.heartflow_chat_list.pop(session_id, None)
        self._chat_last_active_at.pop(session_id, None)
        lock = self._chat_create_locks.get(session_id)
        if lock is not None and not lock.locked():
            self._chat_create_locks.pop(session_id, None)
        if chat is None:
            return

        try:
            await chat.stop()
            logger.info(f"已淘汰心流聊天 {session_id}: reason={reason}")
        except Exception as exc:
            logger.warning(f"淘汰心流聊天 {session_id} 失败: {exc}", exc_info=True)

    async def clear_chat_history_context(self, session_id: str) -> bool:
        """停止并移除当前会话运行时，使其短期历史上下文立即失效。"""

        if session_id not in self.heartflow_chat_list:
            return False

        await self._evict_chat(session_id, reason="clear_context_command")
        return True

    def adjust_talk_frequency(self, session_id: str, frequency: float) -> None:
        """调整指定聊天流的说话频率。"""
        chat = self.heartflow_chat_list.get(session_id)
        if chat:
            self._touch_chat(session_id)
            chat.adjust_talk_frequency(frequency)
            logger.info(f"已调整聊天 {session_id} 的说话频率为 {frequency}")
        else:
            logger.warning(f"无法调整频率，未找到 session_id={session_id} 的聊天流")


heartflow_manager = HeartflowManager()
