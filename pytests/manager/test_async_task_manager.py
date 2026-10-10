import asyncio

import pytest

from src.manager.async_task_manager import AsyncTask, AsyncTaskManager


class BlockingTask(AsyncTask):
    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__(task_name="same-name")
        self.started = started
        self.release = release

    async def run(self) -> None:
        self.started.set()
        await self.release.wait()


class HoldingTask(AsyncTask):
    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__(task_name="same-name")
        self.started = started
        self.release = release

    async def run(self) -> None:
        self.started.set()
        await self.release.wait()


@pytest.mark.asyncio
async def test_replaced_task_callback_does_not_remove_new_task() -> None:
    manager = AsyncTaskManager()
    old_started = asyncio.Event()
    old_release = asyncio.Event()
    new_started = asyncio.Event()
    new_release = asyncio.Event()

    await manager.add_task(BlockingTask(old_started, old_release))
    await old_started.wait()
    old_task = manager.tasks["same-name"]

    await manager.add_task(HoldingTask(new_started, new_release))
    await new_started.wait()

    manager._remove_task_call_back(old_task)

    assert manager.get_tasks_status() == {"same-name": {"status": "running"}}

    new_release.set()
    await manager.stop_and_wait_all_tasks()
