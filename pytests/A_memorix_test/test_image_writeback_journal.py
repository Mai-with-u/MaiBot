from pathlib import Path

import pytest

from src.services.image_writeback_journal import ImageWritebackJournal


def test_unfinished_job_survives_reopen_and_completed_job_is_not_replayed(tmp_path: Path):
    path = tmp_path / 'jobs.sqlite3'
    journal = ImageWritebackJournal(path)
    journal.enqueue('real-chat', 'message-1')
    assert journal.next_job()['message_id'] == 'message-1'
    journal.close()
    journal = ImageWritebackJournal(path)
    try:
        assert journal.next_job()['session_id'] == 'real-chat'
        journal.complete('real-chat', 'message-1')
        journal.enqueue('real-chat', 'message-1')
        assert journal.next_job() is None
        assert journal.list_jobs('done', 25, 0)['total'] == 1
    finally:
        journal.close()


def test_failure_keeps_error_caps_retries_and_can_be_requeued(tmp_path: Path, monkeypatch):
    monkeypatch.setattr('src.services.image_writeback_journal.time.time', lambda: 100)
    journal = ImageWritebackJournal(tmp_path / 'jobs.sqlite3')
    try:
        journal.enqueue('real-chat', 'message-1')
        journal.fail(journal.next_job(), '图片文件缺失', 2)
        assert journal.next_job() is None
        monkeypatch.setattr('src.services.image_writeback_journal.time.time', lambda: 103)
        job = journal.next_job()
        assert job['attempts'] == 1
        journal.fail(job, '模型不可用', 2)
        assert journal.next_job() is None
        failed = journal.list_jobs('failed', 25, 0)['items'][0]
        assert failed['last_error'] == '模型不可用'
        journal.close()
        journal = ImageWritebackJournal(tmp_path / 'jobs.sqlite3')
        assert journal.retry_failed() == 1
        assert journal.next_job()['attempts'] == 0
    finally:
        journal.close()


def test_message_identity_includes_real_session(tmp_path: Path):
    journal = ImageWritebackJournal(tmp_path / 'jobs.sqlite3')
    try:
        journal.enqueue('chat-a', 'same-message')
        journal.enqueue('chat-b', 'same-message')
        assert journal.list_jobs('', 1, 0)['total'] == 2
        assert len(journal.list_jobs('', 1, 1)['items']) == 1
        with pytest.raises(ValueError):
            journal.enqueue('', 'same-message')
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_worker_reloads_pending_message_after_restart(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import asyncio

    from src.services import memory_flow_service as flow

    path = tmp_path / 'jobs.sqlite3'
    journal = ImageWritebackJournal(path)
    journal.enqueue('real-chat', 'persisted-message')
    journal.close()
    service = flow.ImageMemoryWritebackService(path)
    monkeypatch.setattr(service, "_enabled", lambda: True)
    service._journal = ImageWritebackJournal(path)
    message = SimpleNamespace(session_id='real-chat', message_id='persisted-message')
    loaded = []

    def find(**kwargs):
        loaded.append(kwargs)
        return [message]

    async def handle(value):
        assert value is message
        service._stopping = True
        return True

    monkeypatch.setattr(flow, 'find_messages', find)
    monkeypatch.setattr(service, '_handle_message', AsyncMock(side_effect=handle))
    try:
        await asyncio.wait_for(service._worker_loop(), 2)
        assert loaded == [{'session_id': 'real-chat', 'message_id': 'persisted-message', 'limit': 1}]
        assert service._journal.list_jobs('done', 25, 0)['total'] == 1
    finally:
        service._journal.close()
