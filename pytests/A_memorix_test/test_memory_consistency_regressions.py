"""验证缓存、工具预览和图片补偿在恢复路径上的一致性。"""

from types import SimpleNamespace
from typing import Any, Dict, List

import asyncio
import threading

import pytest

from src.A_memorix.core.embedding import api_adapter as adapter_module
from src.A_memorix.core.embedding.api_adapter import EmbeddingAPIAdapter
from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.utils.async_work import cancellation_safe_to_thread
from src.maisaka.builtin_tool import memory_result_preview
from src.maisaka.tool_result_store import ToolResultStore
from src.services import memory_flow_service
from src.services.memory_service import MemoryHit, MemorySearchResult


@pytest.mark.parametrize("length", [20, 5000])
def test_person_search_notice_survives_preview_and_full_result(monkeypatch, length):
    store = ToolResultStore()
    monkeypatch.setattr(memory_result_preview, "tool_result_store", store)
    result = MemorySearchResult(hits=[MemoryHit(content="甲" * length), MemoryHit(content="乙" * length)])
    notice = "提示：人物定向检索未命中，已自动降级为关键词检索。"
    content = result.to_text(truncate_content=False)
    preview = memory_result_preview.build_memory_result_preview(
        result,
        content,
        session_id="chat",
        limit=5,
        notice=notice,
    )
    assert preview.startswith(notice + "\n")
    assert len(preview) <= 2800
    assert "甲" in preview and "乙" in preview
    if length > 2800:
        assert store.get("chat", "r1").content == notice + "\n" + content
        assert store.get("other-chat", "r1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent", [False, True])
async def test_cache_keeps_actual_model_identity_during_recovery(monkeypatch, concurrent):
    adapter = EmbeddingAPIAdapter(default_dimension=4, enable_cache=True)
    monkeypatch.setattr(EmbeddingAPIAdapter, "_GLOBAL_TEXT_EMBEDDING_CACHE", {})
    state = {"failed": True}
    requests = []
    models = {
        name: SimpleNamespace(name=name, api_provider=name, model_identifier=name, extra_params={})
        for name in ("A", "B")
    }
    providers = {
        name: SimpleNamespace(name=name, client_type="openai", base_url=f"https://{name}.invalid") for name in models
    }

    class Client:
        async def get_embedding(self, request):
            name, text = request.model_info.name, request.embedding_input
            requests.append((name, text))
            if name == "A" and state["failed"] and text != "healthy":
                raise RuntimeError("primary unavailable")
            await asyncio.sleep(0)
            return SimpleNamespace(embedding=[1, 0, 0, 0] if name == "A" else [0, 1, 0, 0])

    monkeypatch.setattr(adapter, "_resolve_candidate_model_names", lambda: ["A", "B"])
    monkeypatch.setattr(adapter, "_find_model_info", lambda name: models[name])
    monkeypatch.setattr(adapter, "_find_provider", lambda name: providers[name])
    monkeypatch.setattr(adapter_module.client_registry, "get_client_class_instance", lambda provider: Client())
    texts = ["same", "healthy"] if concurrent else ["same"]
    initial = await adapter.encode(texts, dimensions=4)
    assert initial[0].tolist() == [0, 1, 0, 0]
    state["failed"] = False
    await adapter.encode("probe", dimensions=4, use_cache=False)
    recovered = await adapter.encode("same", dimensions=4)
    assert recovered.tolist() == [1, 0, 0, 0]
    count = len(requests)
    await adapter.encode("same", dimensions=4)
    assert len(requests) == count
    await adapter.encode("same", dimensions=4, use_cache=False)
    assert len(requests) == count + 1
    assert adapter._last_success_model_name == "A"


@pytest.mark.asyncio
async def test_cancellation_keeps_scope_locked_until_write_finishes():
    started, release = threading.Event(), threading.Event()
    lock = asyncio.Lock()

    def write():
        started.set()
        if not release.wait(timeout=3):
            raise TimeoutError("test worker was not released")

    async def hold_scope():
        async with lock:
            await cancellation_safe_to_thread(write)

    task = asyncio.create_task(hold_scope())
    try:
        assert await asyncio.to_thread(started.wait, 1)
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            assert lock.locked()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not lock.locked()


def _enqueue_description(store: MetadataStore, name: str) -> str:
    with store.transaction(immediate=True):
        asset = store.upsert_image_asset(
            content_hash=name,
            storage_key=name,
            mime_type="image/png",
            byte_size=1,
            width=1,
            height=1,
        )
        occurrence = store.upsert_image_occurrence(
            asset_id=asset["asset_id"],
            external_ref=name,
            source_kind="chat",
            scope_type="chat",
            chat_id="chat",
            message_id="msg",
            occurred_at=1.0,
        )
        store.enqueue_image_description_compensation(occurrence_id=occurrence["occurrence_id"], description_hash=name)
        return occurrence["occurrence_id"]


@pytest.mark.asyncio
async def test_compensation_reuses_durable_batch_after_partial_completion(tmp_path, monkeypatch):
    store = MetadataStore(data_dir=tmp_path / "metadata")
    store.connect()
    try:
        first = _enqueue_description(store, "first")
        second = _enqueue_description(store, "second")
        calls: List[str] = []
        generations: Dict[str, Any] = {}
        state = {"interrupt": True}

        async def image_memory(*, action, **kwargs):
            if action == "claim_description_compensations":
                items = store.claim_image_description_compensations(
                    lease_token="lease",
                    lease_seconds=30,
                    max_attempts=5,
                    limit=10,
                )
                return {"success": True, "items": items, "lease_token": "lease"}
            if kwargs["occurrence_id"] == second and state["interrupt"]:
                state["interrupt"] = False
                raise RuntimeError("completion interrupted")
            return {"success": store.complete_image_description_compensation(**kwargs)}

        async def ingest_summary(**kwargs):
            external_id = kwargs["external_id"]
            calls.append(external_id)
            return generations.setdefault(external_id, SimpleNamespace(success=True, detail=""))

        monkeypatch.setattr(
            memory_flow_service,
            "memory_service",
            SimpleNamespace(
                image_memory=image_memory,
                ingest_summary=ingest_summary,
            ),
        )
        service = object.__new__(memory_flow_service.ImageMemoryWritebackService)
        with pytest.raises(RuntimeError, match="completion interrupted"):
            await service._process_description_compensations()
        # 模拟重启和租约过期；新描述不能并入已有摘要的旧批次。
        store.close()
        store.connect()
        with store.transaction(immediate=True) as connection:
            connection.execute("UPDATE image_description_compensations SET lease_until=0 WHERE status='running'")
        _enqueue_description(store, "late")
        await service._process_description_compensations()
        assert calls[0] == calls[1]
        assert calls[2] != calls[0]
        assert len(generations) == 2
        assert (
            store._conn.execute(
                "SELECT status FROM image_description_compensations WHERE occurrence_id=?",
                (first,),
            ).fetchone()[0]
            == "done"
        )
        assert (
            store.claim_image_description_compensations(
                lease_token="again",
                lease_seconds=30,
                max_attempts=5,
                limit=10,
            )
            == []
        )
    finally:
        store.close()


def test_image_batch_identity_migrates_existing_database(tmp_path):
    store = MetadataStore(data_dir=tmp_path / "metadata")
    store.connect()
    _enqueue_description(store, "old")
    with store.transaction(immediate=True) as connection:
        connection.execute("ALTER TABLE image_description_compensations DROP COLUMN summary_external_id")
        connection.execute("DELETE FROM schema_migrations")
        connection.execute("INSERT INTO schema_migrations VALUES (29, 0)")
    store.close()
    store.connect()
    try:
        claimed = store.claim_image_description_compensations(
            lease_token="migrated",
            lease_seconds=30,
            max_attempts=3,
            limit=10,
        )
        assert len(claimed) == 1
        assert claimed[0]["summary_external_id"].startswith("image_description_compensation:")
    finally:
        store.close()
