"""聊天数据迁移的主循环边界；只等待指定聊天流，在途消息不会丢弃。"""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Dict, Set, Tuple, TypeVar

import asyncio


T = TypeVar("T")


async def settled_thread(operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """取消协程时仍等事务线程结束，避免补偿与尚未提交的原操作竞跑。"""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class ChatMigrationGate:
    def __init__(self) -> None:
        self.blocked: Set[str] = set()
        self.active: Dict[str, int] = {}
        self.changed = asyncio.Event()
        self.owners: Dict[Tuple[asyncio.Task, str], int] = {}

    def _notify(self) -> None:
        self.changed.set()
        self.changed = asyncio.Event()

    @asynccontextmanager
    async def activity(self, session_id: str) -> AsyncIterator[None]:
        owner = (asyncio.current_task(), session_id)
        if owner in self.owners:
            self.owners[owner] += 1
            try:
                yield
            finally:
                self.owners[owner] -= 1
            return
        while session_id in self.blocked:
            await self.changed.wait()
        self.owners[owner] = 1
        self.active[session_id] = self.active.get(session_id, 0) + 1
        try:
            yield
        finally:
            del self.owners[owner]
            self.active[session_id] -= 1
            if not self.active[session_id]:
                del self.active[session_id]
            self._notify()

    async def pause(self, session_ids: Set[str]) -> None:
        if self.blocked.intersection(session_ids):
            raise ValueError("聊天流已有迁移任务，请先完成或恢复该任务")
        self.blocked.update(session_ids)
        try:
            async with asyncio.timeout(60):
                while any(self.active.get(key, 0) for key in session_ids):
                    await self.changed.wait()
        except BaseException:
            self.resume(session_ids)
            raise

    def resume(self, session_ids: Set[str]) -> None:
        self.blocked.difference_update(session_ids)
        self._notify()


chat_migration_gate = ChatMigrationGate()
