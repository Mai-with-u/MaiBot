from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict
from weakref import WeakValueDictionary

import asyncio
import time
import traceback

from src.chat.message_receive.chat_manager import chat_manager
from src.common.logger import get_logger
from src.maisaka.runtime import MaisakaHeartFlowChatting

logger = get_logger("heartflow")

HEARTFLOW_ACTIVE_RETENTION_SECONDS = 24 * 60 * 60
HEARTFLOW_MAX_ACTIVE_CHATS = 100
HEARTFLOW_DRAIN_TIMEOUT_SECONDS = 30.0
"""等待在途使用者结束的时限：超时视为租约泄漏，保留运行时并向上抛出异常。"""


class HeartflowManager:
    """管理 session 级别的 Maisaka 心流实例。"""

    def __init__(self) -> None:
        self.heartflow_chat_list: OrderedDict[str, MaisakaHeartFlowChatting] = OrderedDict()
        # 使用弱值字典保存会话创建锁：等待中的协程持有强引用，锁用完且无人引用时自动回收，避免按 session 无限累积。
        self._chat_create_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._chat_last_active_at: Dict[str, float] = {}
        self._chat_usage_counts: Dict[str, int] = {}
        """会话运行时的在途使用者计数：淘汰与释放需等待归零，避免运行时停止后仍被写入缓存。"""
        self._chat_drain_events: Dict[str, asyncio.Event] = {}
        """使用者计数归零时置位的事件，供淘汰流程等待排空。"""

    async def get_or_create_heartflow_chat(self, session_id: str) -> MaisakaHeartFlowChatting:
        """获取或创建指定会话对应的 Maisaka runtime。

        注意：返回的运行时不携带使用租约，跨 ``await`` 持有它的调用方
        应改用 :meth:`borrow_chat`，否则释放/淘汰流程可能在持有期间停止运行时。
        """
        try:
            async with self._borrow_create_lock(session_id):
                chat = await self._get_or_create_locked(session_id)
            # 创建完成后在锁外触发上限淘汰：淘汰需要逐个获取其他会话的创建锁，
            # 若在本会话锁内进行，两个互为淘汰目标的会话可能相互等待造成死锁。
            await self._evict_over_limit_chats(protected_session_id=session_id)
            return chat
        except Exception as exc:
            logger.error(f"创建心流聊天 {session_id} 失败: {exc}", exc_info=True)
            traceback.print_exc()
            raise

    @asynccontextmanager
    async def borrow_chat(self, session_id: str) -> AsyncIterator[MaisakaHeartFlowChatting]:
        """以使用租约方式借用会话运行时。

        租约在本会话创建锁保护下登记，保证登记时运行时必然在册；
        租约持有期间 ``release_chat``、历史上下文清理与超限淘汰都会
        等待本使用者结束后才真正停止运行时。
        """
        async with self._borrow_create_lock(session_id):
            chat = await self._get_or_create_locked(session_id)
            self._acquire_chat_usage(session_id)
        try:
            # 锁外触发上限淘汰：淘汰需要逐个获取其他会话的创建锁，避免死锁；
            # 与 yield 同处一个 try/finally，保证调用方在此期间被取消时租约仍被释放
            await self._evict_over_limit_chats(protected_session_id=session_id)
            yield chat
        finally:
            self.release_chat_usage(session_id)

    def _acquire_chat_usage(self, session_id: str) -> None:
        """登记一个在途使用者（必须在本会话创建锁内调用）。"""
        self._chat_usage_counts[session_id] = self._chat_usage_counts.get(session_id, 0) + 1
        drain_event = self._chat_drain_events.get(session_id)
        if drain_event is not None:
            drain_event.clear()

    def release_chat_usage(self, session_id: str) -> None:
        """注销一个已结束的在途使用者；计数归零时唤醒等待排空的淘汰流程。"""
        count = self._chat_usage_counts.get(session_id, 0)
        if count <= 1:
            self._chat_usage_counts.pop(session_id, None)
            drain_event = self._chat_drain_events.get(session_id)
            if drain_event is not None:
                drain_event.set()
        else:
            self._chat_usage_counts[session_id] = count - 1

    async def _wait_chat_drained(self, session_id: str) -> None:
        """等待指定会话的在途使用者全部结束。

        超过 :data:`HEARTFLOW_DRAIN_TIMEOUT_SECONDS` 仍未排空视为租约泄漏，
        抛出 ``TimeoutError`` 并保留现场，避免释放流程持锁无限阻塞。
        """
        while self._chat_usage_counts.get(session_id, 0) > 0:
            drain_event = self._chat_drain_events.setdefault(session_id, asyncio.Event())
            try:
                await asyncio.wait_for(drain_event.wait(), timeout=HEARTFLOW_DRAIN_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"等待会话 {session_id} 的在途使用者结束超时"
                    f"（{HEARTFLOW_DRAIN_TIMEOUT_SECONDS}s），可能存在未释放的使用租约"
                ) from exc

    async def _get_or_create_locked(self, session_id: str) -> MaisakaHeartFlowChatting:
        """获取或创建运行时；调用方必须已持有该会话的创建锁。"""
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
        return new_chat

    def _touch_chat(self, session_id: str) -> None:
        """记录会话最近活跃时间，并维护心流实例的 LRU 顺序。"""
        self._chat_last_active_at[session_id] = time.time()
        self.heartflow_chat_list.move_to_end(session_id)

    async def _evict_over_limit_chats(self, *, protected_session_id: str) -> None:
        """当实例数量超过上限时，仅淘汰 24 小时内无消息的旧会话。

        每个候选会话在其创建锁保护下重验资格后再淘汰，
        避免等待锁期间该会话已被其他流程删除或重新活跃。
        """
        while len(self.heartflow_chat_list) > HEARTFLOW_MAX_ACTIVE_CHATS:
            session_id = self._find_evictable_session_id(protected_session_id=protected_session_id)
            if session_id is None:
                return
            async with self._borrow_create_lock(session_id):
                if session_id not in self.heartflow_chat_list:
                    continue
                last_active_at = self._chat_last_active_at.get(session_id, 0.0)
                if last_active_at > time.time() - HEARTFLOW_ACTIVE_RETENTION_SECONDS:
                    continue
                try:
                    await self._evict_chat_locked(session_id, reason="cache_limit")
                except Exception as exc:
                    # 单个会话淘汰失败不阻塞整体流程：刷新活跃时间使其本轮不再满足候选条件，
                    # 待其空闲后由下次触发重试
                    logger.warning(f"淘汰心流聊天 {session_id} 失败: {exc}", exc_info=True)
                    self._touch_chat(session_id)

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

    async def _evict_chat_locked(self, session_id: str, *, reason: str) -> bool:
        """停止并移除指定会话的心流实例；调用方必须已持有该会话的创建锁。

        先等待在途使用者归零再停止运行时，且仅在停止成功后才移除列表与簿记；
        停止失败时保留运行并向上传播异常，避免误报成功或产生第二个运行时。

        Args:
            session_id: 目标会话 ID。
            reason: 淘汰原因，用于日志。

        Returns:
            bool: 会话存在运行时且已成功停止移除时为 True；运行时不存在时为 False。
        """
        chat = self.heartflow_chat_list.get(session_id)
        if chat is None:
            return False

        # 等待在途使用者（如消息入库流程）结束，避免运行时停止后仍被写入缓存与状态
        await self._wait_chat_drained(session_id)
        await chat.stop()

        self.heartflow_chat_list.pop(session_id, None)
        self._chat_last_active_at.pop(session_id, None)
        # 等待方在 await 前已持有事件的本地引用，此处回收条目是安全的，避免簿记按 session 无限累积
        self._chat_drain_events.pop(session_id, None)
        logger.info(f"已淘汰心流聊天 {session_id}: reason={reason}")
        return True

    def _borrow_create_lock(self, session_id: str) -> asyncio.Lock:
        """获取指定会话的创建锁；弱值字典在无人引用时自动回收条目。"""
        create_lock = self._chat_create_locks.get(session_id)
        if create_lock is None:
            create_lock = asyncio.Lock()
            self._chat_create_locks[session_id] = create_lock
        return create_lock

    async def release_chat(self, session_id: str, *, reason: str) -> None:
        """停止并移除指定会话的心流实例及其活跃时间簿记（供聊天流删除、CLI 退出等外部场景调用）。

        与 ``get_or_create_heartflow_chat`` 基于同一把创建锁串行化，
        避免释放完成后，并发进行中的创建流程又把运行时写回列表（复活已删除会话）；
        会先等待在途使用者结束再停止运行时。停止失败时异常向上传播，由调用方决定后续处理。
        外部不应直接操作 ``heartflow_chat_list``，否则会遗留运行中的后台任务和簿记数据。
        """
        async with self._borrow_create_lock(session_id):
            await self._evict_chat_locked(session_id, reason=reason)

    async def clear_chat_history_context(self, session_id: str) -> bool:
        """停止并移除当前会话运行时，使其短期历史上下文立即失效。

        与创建流程共用同一把会话锁，并在锁内检查运行时是否存在：
        避免创建流程持锁停在 ``await new_chat.start()`` 时，此处锁外检查误判为不存在，
        随后创建流程又把运行时写回列表的竞态。停止失败时异常向上传播。
        """
        async with self._borrow_create_lock(session_id):
            if session_id not in self.heartflow_chat_list:
                return False
            await self._evict_chat_locked(session_id, reason="clear_context_command")
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
