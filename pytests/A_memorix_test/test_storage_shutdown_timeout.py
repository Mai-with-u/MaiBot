"""阻塞写入时关闭有界失败，资源只在写入线程退出后释放。"""

from collections import deque
from types import SimpleNamespace

import asyncio
import threading

import pytest

from src.A_memorix.core.runtime.services.background_task_service import MemoryBackgroundTaskService
from src.A_memorix.core.runtime.services.runtime_lifecycle_service import MemoryRuntimeLifecycleService
from src.A_memorix.core.utils import async_work
from src.A_memorix.core.utils.retrieval_tuning_manager import RetrievalTuningManager
from src.A_memorix.core.utils.web_import_manager import ImportTaskManager


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["background", "import", "tuning"])
async def test_blocked_write_keeps_resources_until_shutdown_retry(monkeypatch, kind):
    monkeypatch.setattr(async_work, "TASK_STOP_TIMEOUT_SECONDS", 0.02)
    started, release = threading.Event(), threading.Event()
    lock = asyncio.Lock()
    closed = []

    def write():
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test write was not released")

    async def run():
        async with lock:
            await async_work.cancellation_safe_to_thread(write)

    worker = asyncio.create_task(run(), name="blocked-storage-write")
    try:
        assert await asyncio.to_thread(started.wait, 1)
        if kind == "background":
            kernel = SimpleNamespace(
                _background_lock=asyncio.Lock(),
                _background_tasks={"write": worker},
                import_task_manager=None,
                retrieval_tuning_manager=None,
                _close_runtime=lambda: closed.append(True),
            )
            kernel._stop_background_tasks = MemoryBackgroundTaskService(kernel)._stop_background_tasks
            shutdown = MemoryRuntimeLifecycleService(kernel)._shutdown_serialized
        elif kind == "import":
            manager = object.__new__(ImportTaskManager)
            manager._lock = asyncio.Lock()
            manager._tasks = {}
            manager._queue = deque()
            manager._worker_task = worker
            manager._cleanup_temp_root = lambda: closed.append(True)
            shutdown = manager.shutdown
        else:
            manager = object.__new__(RetrievalTuningManager)
            manager._worker_task = worker
            shutdown = manager.shutdown

        for _ in range(2):
            with pytest.raises(RuntimeError, match="blocked-storage-write"):
                await asyncio.wait_for(shutdown(), timeout=1)
            assert lock.locked()
            assert not worker.done()
            assert not closed
            if kind == "background":
                assert kernel._background_tasks["write"] is worker
            else:
                assert manager._worker_task is worker

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await worker
        assert not lock.locked()
        await shutdown()
        if kind in {"background", "import"}:
            assert closed == [True]
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_consumes_completed_task_errors():
    async def fail():
        raise OSError("storage failed")

    worker = asyncio.create_task(fail())
    await asyncio.sleep(0)
    await async_work.cancel_and_wait_for_tasks([worker], operation="test")
    await async_work.cancel_and_wait_for_tasks([], operation="test")
