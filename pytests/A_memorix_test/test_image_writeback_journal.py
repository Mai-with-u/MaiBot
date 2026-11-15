from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import get_ident

import pytest

from src.services.image_writeback_journal import ImageWritebackJournal


def test_unfinished_job_survives_reopen_and_completed_job_is_not_replayed(tmp_path: Path):
    path = tmp_path / "jobs.sqlite3"
    journal = ImageWritebackJournal(path)
    journal.enqueue("real-chat", "message-1")
    assert journal.next_job()["message_id"] == "message-1"
    journal = ImageWritebackJournal(path)
    assert journal.next_job()["session_id"] == "real-chat"
    journal.complete("real-chat", "message-1")
    journal.enqueue("real-chat", "message-1")
    assert journal.next_job() is None
    assert journal.list_jobs("done", 25, 0)["total"] == 1


def test_failure_keeps_error_caps_retries_and_can_be_requeued(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("src.services.image_writeback_journal.time.time", lambda: 100)
    journal = ImageWritebackJournal(tmp_path / "jobs.sqlite3")
    journal.enqueue("real-chat", "message-1")
    journal.fail(journal.next_job(), "图片文件缺失", 2)
    assert journal.next_job() is None
    monkeypatch.setattr("src.services.image_writeback_journal.time.time", lambda: 103)
    job = journal.next_job()
    assert job["attempts"] == 1
    journal.fail(job, "模型不可用", 2)
    assert journal.next_job() is None
    failed = journal.list_jobs("failed", 25, 0)["items"][0]
    assert failed["last_error"] == "模型不可用"
    journal = ImageWritebackJournal(tmp_path / "jobs.sqlite3")
    assert journal.retry_failed() == 1
    assert journal.next_job()["attempts"] == 0


def test_message_identity_includes_real_session(tmp_path: Path):
    journal = ImageWritebackJournal(tmp_path / "jobs.sqlite3")
    journal.enqueue("chat-a", "same-message")
    journal.enqueue("chat-b", "same-message")
    assert journal.list_jobs("", 1, 0)["total"] == 2
    assert len(journal.list_jobs("", 1, 1)["items"]) == 1
    with pytest.raises(ValueError):
        journal.enqueue("", "same-message")


@pytest.mark.asyncio
async def test_worker_reloads_pending_message_after_restart(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import asyncio

    from src.services import memory_flow_service as flow

    path = tmp_path / "jobs.sqlite3"
    journal = ImageWritebackJournal(path)
    journal.enqueue("real-chat", "persisted-message")
    service = flow.ImageMemoryWritebackService(path)
    monkeypatch.setattr(service, "_enabled", lambda: True)
    service._journal = ImageWritebackJournal(path)
    message = SimpleNamespace(session_id="real-chat", message_id="persisted-message")
    loaded = []
    loop_thread = get_ident()

    def find(**kwargs):
        assert get_ident() != loop_thread
        loaded.append(kwargs)
        return [message]

    async def handle(value):
        assert value is message
        service._stopping = True
        return True

    monkeypatch.setattr(flow, "find_messages", find)
    monkeypatch.setattr(service, "_handle_message", AsyncMock(side_effect=handle))
    await asyncio.wait_for(service._worker_loop(), 2)
    assert loaded == [{"session_id": "real-chat", "message_id": "persisted-message", "limit": 1}]
    assert service._journal.list_jobs("done", 25, 0)["total"] == 1


def test_journal_supports_concurrent_calls_from_other_threads(tmp_path: Path):
    journal = ImageWritebackJournal(tmp_path / "jobs.sqlite3")
    creator_thread = get_ident()

    def enqueue(index: int) -> None:
        assert get_ident() != creator_thread
        journal.enqueue("real-chat", str(index))
        journal.enqueue("real-chat", str(index))

    def fail(index: int) -> None:
        journal.fail({"session_id": "real-chat", "message_id": str(index), "attempts": 0}, "error", 1)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(enqueue, range(20)))
        assert pool.submit(journal.list_jobs, "pending", 25, 0).result()["total"] == 20
        assert pool.submit(journal.next_job).result() is not None
        list(pool.map(fail, range(20)))
        assert pool.submit(journal.retry_failed).result() == 20
        list(pool.map(lambda index: journal.complete("real-chat", str(index)), range(20)))
        assert pool.submit(journal.next_job).result() is None
        assert pool.submit(journal.list_jobs, "done", 25, 0).result()["total"] == 20


@pytest.mark.asyncio
async def test_enqueue_runs_database_work_outside_event_loop(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from src.services import memory_flow_service as flow

    service = flow.ImageMemoryWritebackService(tmp_path / "jobs.sqlite3")
    journal = ImageWritebackJournal(service._journal_path)
    service._journal = journal
    monkeypatch.setattr(service, "_enabled", lambda: True)
    monkeypatch.setattr(flow, "iter_message_image_components", lambda components: iter([object()]))
    loop_thread = get_ident()
    original_enqueue = journal.enqueue

    def enqueue(session_id: str, message_id: str) -> None:
        assert get_ident() != loop_thread
        original_enqueue(session_id, message_id)

    monkeypatch.setattr(journal, "enqueue", enqueue)
    message = SimpleNamespace(
        session_id="real-chat", message_id="image-message", raw_message=SimpleNamespace(components=[])
    )
    await service.enqueue(message)
    assert journal.next_job()["message_id"] == "image-message"


@pytest.mark.asyncio
async def test_concurrent_start_creates_one_pair_of_background_tasks(tmp_path: Path, monkeypatch):
    from unittest.mock import AsyncMock

    import asyncio

    from src.services import memory_flow_service as flow

    service = flow.ImageMemoryWritebackService(tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(service, "_enabled", lambda: True)
    journal = ImageWritebackJournal(service._journal_path)
    arrivals = 0
    initialized = asyncio.Event()

    async def initialize(*args):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            initialized.set()
        await initialized.wait()
        return journal

    async def wait_for_shutdown():
        await asyncio.Event().wait()

    worker = AsyncMock(side_effect=wait_for_shutdown)
    compensation = AsyncMock(side_effect=wait_for_shutdown)
    monkeypatch.setattr(flow.asyncio, "to_thread", initialize)
    monkeypatch.setattr(service, "_worker_loop", worker)
    monkeypatch.setattr(service, "_compensation_loop", compensation)
    try:
        await asyncio.wait_for(asyncio.gather(service.start(), service.start()), 2)
        await asyncio.sleep(0)
        assert worker.call_count == 1
        assert compensation.call_count == 1
    finally:
        await service.shutdown()
