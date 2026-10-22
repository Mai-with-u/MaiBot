from collections import OrderedDict
from typing import Dict

import asyncio
import time

from src.chat.message_receive.chat_manager import BotChatSession, chat_manager
from src.common.logger import get_logger
from src.core.local_operator import LOCAL_PLATFORM_BOT_IDS
from src.maisaka.runtime import MaisakaHeartFlowChatting
from src.maisaka.focus import focus_mode_manager
from src.platform_io.manager import get_platform_io_manager
from src.platform_io.types import RouteKey

logger = get_logger("heartflow")

HEARTFLOW_ACTIVE_RETENTION_SECONDS = 24 * 60 * 60
HEARTFLOW_MAX_ACTIVE_CHATS = 100


class HeartflowPolicyDeniedError(PermissionError):
    """适配器名单拒绝创建聊天运行实例。"""


class HeartflowManager:
    """管理 session 级别的 Maisaka 心流实例。"""

    def __init__(self) -> None:
        self.heartflow_chat_list: OrderedDict[str, MaisakaHeartFlowChatting] = OrderedDict()
        self._chat_create_locks: Dict[str, asyncio.Lock] = {}
        self._chat_last_active_at: Dict[str, float] = {}
        self._stopping = False

    async def get_or_create_heartflow_chat(self, session_id: str) -> MaisakaHeartFlowChatting:
        """获取或创建指定会话对应的 Maisaka runtime。"""
        focus_mode_manager.bind_loop()
        self._ensure_running()
        try:
            if chat := self.heartflow_chat_list.get(session_id):
                self._touch_chat(session_id)
                return chat

            create_lock = self._chat_create_locks.setdefault(session_id, asyncio.Lock())
            async with create_lock:
                self._ensure_running()
                if chat := self.heartflow_chat_list.get(session_id):
                    self._touch_chat(session_id)
                    return chat

                chat_session = chat_manager.get_session_by_session_id(session_id)
                if not chat_session:
                    raise ValueError(f"未找到 session_id={session_id} 对应的聊天流")
                if not await self._is_session_allowed(chat_session):
                    raise HeartflowPolicyDeniedError(f"适配器名单禁止创建聊天运行实例: {session_id}")
                self._ensure_running()

                new_chat = MaisakaHeartFlowChatting(session_id=session_id)
                await new_chat.start()
                self.heartflow_chat_list[session_id] = new_chat
                self._touch_chat(session_id)
                await self._evict_over_limit_chats(protected_session_id=session_id)
                return new_chat
        except HeartflowPolicyDeniedError as exc:
            logger.warning(str(exc))
            raise
        except Exception as exc:
            logger.error(f"创建心流聊天 {session_id} 失败: {exc}", exc_info=True)
            raise

    def _ensure_running(self) -> None:
        if self._stopping:
            raise RuntimeError("心流管理器正在关停，不能创建聊天运行实例")

    async def shutdown(self) -> None:
        """关闭创建入口，等待在途创建完成，再并发停止所有会话及其工具。"""
        self._stopping = True
        for lock in list(self._chat_create_locks.values()):
            async with lock:
                pass
        chats = list(self.heartflow_chat_list.values())
        results = await asyncio.gather(*(chat.stop() for chat in chats), return_exceptions=True)
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise RuntimeError(f"{len(failures)} 个聊天运行实例未能停止") from failures[0]
        self.heartflow_chat_list.clear()
        self._chat_last_active_at.clear()
        self._chat_create_locks.clear()

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
            if reason in {"clear_context_command", "adapter_policy_block"}:
                await focus_mode_manager.clear_context(session_id)
            await chat.stop()
            logger.info(f"已淘汰心流聊天 {session_id}: reason={reason}")
        except Exception as exc:
            logger.warning(f"淘汰心流聊天 {session_id} 失败: {exc}", exc_info=True)

    async def clear_chat_history_context(self, session_id: str) -> bool:
        """停止并移除当前会话运行时，使其短期历史上下文立即失效。"""

        if session_id not in self.heartflow_chat_list:
            await focus_mode_manager.clear_context(session_id)
            return False

        await self._evict_chat(session_id, reason="clear_context_command")
        return True

    async def close_policy_blocked_chats(self) -> None:
        """策略变更后停止并注销被禁用的运行实例，取消思考和所有唤醒任务。"""
        for session_id, chat in list(self.heartflow_chat_list.items()):
            if not await self._is_session_allowed(chat.chat_stream):
                await self._evict_chat(session_id, reason="adapter_policy_block")

    async def _is_session_allowed(self, session: BotChatSession) -> bool:
        """在运行实例的创建和注销入口检查适配器名单。"""
        # 本地控制台和 WebUI 聊天室不属于外部适配器名单。
        if session.platform in LOCAL_PLATFORM_BOT_IDS or session.platform == "webui":
            return True
        manager = get_platform_io_manager()
        await manager.ensure_send_pipeline_ready()
        return await manager.is_target_allowed(
            RouteKey(platform=session.platform, account_id=session.account_id, scope=session.scope),
            chat_type="group" if session.is_group_session else "private",
            target_id=(session.group_id if session.is_group_session else session.user_id) or "",
        )

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
