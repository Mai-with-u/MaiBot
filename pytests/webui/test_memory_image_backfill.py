from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from pytests.A_memorix_test.test_image_memory_runtime import _png
from src.A_memorix.core.image.asset_store import ImageAssetStore
from src.webui.routers import memory as routes


@pytest.mark.asyncio
async def test_writeback_jobs_show_real_chat_names_and_retry(monkeypatch, tmp_path):
    from src.services.image_writeback_journal import ImageWritebackJournal
    from src.services.memory_flow_service import ImageMemoryWritebackService

    service = ImageMemoryWritebackService(tmp_path / "jobs.sqlite3")
    journal = ImageWritebackJournal(service._journal_path)
    try:
        journal.enqueue("real", "message-1")
        journal.fail(journal.next_job(), "原图缓存已缺失", max_attempts=1)
    finally:
        journal.close()
    monkeypatch.setattr(routes.memory_automation_service, "image_writeback", service)
    monkeypatch.setattr(routes, "_find_real_chat_session", lambda key: object() if key == "real" else None)
    monkeypatch.setattr(routes, "_get_chat_name", lambda *args: "测试读书会")

    payload = routes.list_image_writeback_jobs(limit=25, offset=0, status="failed")
    assert payload["total"] == 1
    assert payload["items"][0]["chat_name"] == "测试读书会"
    assert payload["items"][0]["last_error"] == "原图缓存已缺失"
    assert routes.retry_image_writeback_jobs() == {"success": True, "count": 1}
    assert service.list_jobs("failed")["total"] == 0
    assert service.list_jobs("pending")["total"] == 1
    assert service._worker_task is None


class _Image:
    def __init__(self, payload: bytes, *, missing: bool = False) -> None:
        self.payload = payload
        self.binary_data = b""
        self.missing = missing

    async def load_image_binary(self) -> None:
        if self.missing:
            raise FileNotFoundError("原图缓存已缺失")
        self.binary_data = self.payload


@pytest.mark.asyncio
async def test_preview_classifies_each_image_without_writing_and_execution_keeps_valid_siblings(monkeypatch, tmp_path):
    good = _png((20, 30, 40))
    messages = {
        1: SimpleNamespace(
            session_id="real",
            message_id="m1",
            raw_message=SimpleNamespace(components=[_Image(good), _Image(b"", missing=True), _Image(b"broken")]),
        ),
        2: SimpleNamespace(
            session_id="unknown", message_id="m2", raw_message=SimpleNamespace(components=[_Image(good)])
        ),
        3: SimpleNamespace(session_id="real", message_id="", raw_message=SimpleNamespace(components=[_Image(good)])),
    }
    records = [SimpleNamespace(id=key) for key in messages]

    @contextmanager
    def session(**kwargs):
        yield SimpleNamespace(exec=lambda statement: SimpleNamespace(all=lambda: records))

    monkeypatch.setattr(routes, "get_db_session", session)
    monkeypatch.setattr(routes.SessionMessage, "from_db_instance", lambda row: messages[row.id])
    monkeypatch.setattr(routes, "iter_message_image_components", lambda parts: enumerate(parts))
    monkeypatch.setattr(routes, "_find_real_chat_session", lambda key: object() if key == "real" else None)
    monkeypatch.setattr(routes, "_get_chat_name", lambda *args: "测试读书会")
    runtime = SimpleNamespace(asset_store=ImageAssetStore(tmp_path, max_bytes=10000, max_pixels=1000))
    monkeypatch.setattr(routes, "get_runtime_kernel", lambda: SimpleNamespace(image_memory_runtime=runtime))
    written = []

    async def write(message, *, component_paths):
        written.extend((message.message_id, path) for path in component_paths)

    monkeypatch.setattr(routes.memory_automation_service.image_writeback, "_handle_message", write)
    preview = await routes.backfill_image_memories(limit=100, before_id=None, upper_id=None, preview=True)
    assert written == []
    assert preview["counts"] == {
        "ready": 1,
        "missing_file": 1,
        "invalid_image": 1,
        "missing_chat": 1,
        "missing_source": 1,
    }
    assert preview["upper_id"] == 3
    executed = await routes.backfill_image_memories(limit=100, before_id=None, upper_id=3, preview=False)
    assert written == [("m1", 0)]
    assert executed["processed_messages"] == 1
    assert executed["failed_messages"] == 3
    assert executed["counts"]["processed"] == 1
