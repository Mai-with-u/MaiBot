from __future__ import annotations

from typing import Any, Callable, Coroutine, Dict

import asyncio

from .base import KernelServiceBase


class MemoryRequestDedupService(KernelServiceBase):
    async def execute_request_with_dedup(
        self,
        request_key: str,
        executor: Callable[[], Coroutine[Any, Any, Dict[str, Any]]],
    ) -> tuple[bool, Dict[str, Any]]:
        token = str(request_key or "").strip()
        if not token:
            return False, await executor()

        existing = self._request_dedup_tasks.get(token)
        if existing is not None:
            return True, await asyncio.shield(existing)

        task = asyncio.create_task(executor())
        self._request_dedup_tasks[token] = task

        def _remove_completed_task(completed: asyncio.Task) -> None:
            current = self._request_dedup_tasks.get(token)
            if current is completed:
                self._request_dedup_tasks.pop(token, None)

        task.add_done_callback(_remove_completed_task)
        payload = await asyncio.shield(task)
        return False, payload
