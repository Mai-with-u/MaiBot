from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import json

import pytest

from src.A_memorix.core.storage import MetadataStore
from src.A_memorix.core.utils import summary_importer as summary_module
from src.A_memorix.core.utils.summary_importer import SummaryImporter
from src.A_memorix.host_service import AMemorixHostService
from src.common import runtime_loop
from src.llm_models.openai_compat import validate_image_embedding_transport
from src.maisaka.memory import image_injector
from src.services import memory_flow_service as flow

from pytests.A_memorix_test.test_image_memory_runtime import _Embedder, _png, _runtime


@pytest.mark.asyncio
async def test_empty_summary_checkpoint_survives_reopen(tmp_path: Path, monkeypatch):
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        store.record_summary_checkpoint(external_id="empty-20", chat_id="real", trigger_message_count=20)
    finally:
        store.close()
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        importer = SummaryImporter(None, None, store, None, {})
        result = await importer.import_from_stream("real", metadata={"external_id": "empty-20"})
        assert result.success and result.skipped
        assert store.get_paragraphs_by_source("chat_summary:real") == []
        monkeypatch.setattr(
            flow.memory_service_module.a_memorix_host_service,
            "_ensure_kernel",
            AsyncMock(return_value=SimpleNamespace(metadata_store=store)),
        )
        count = await flow.ChatSummaryWritebackService()._load_last_trigger_message_count(
            session_id="real",
            total_message_count=21,
        )
        assert count == 20
    finally:
        store.close()


def test_schema25_upgrade_adds_empty_summary_checkpoints(tmp_path):
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        with store.transaction(immediate=True) as database:
            database.execute("DROP TABLE chat_summary_checkpoints")
            database.execute("DELETE FROM schema_migrations WHERE version > 25")
        store.set_schema_version(25)
    finally:
        store.close()
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        assert store.get_schema_version() == 26
        store.record_summary_checkpoint(external_id="empty", chat_id="real", trigger_message_count=30)
        assert store.get_summary_checkpoint_count("real") == 30
    finally:
        store.close()


@pytest.mark.asyncio
async def test_invalid_image_evidence_is_rejected_before_import(tmp_path, monkeypatch):
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        importer = SummaryImporter(None, None, store, None, {})
        monkeypatch.setattr(importer, "_ensure_runtime_self_check", AsyncMock(return_value=(True, "")))
        monkeypatch.setattr(
            importer,
            "_resolve_summary_model_task",
            lambda: (
                "memory",
                SimpleNamespace(model_list=["test"], temperature=0.1, max_tokens=100),
            ),
        )
        monkeypatch.setattr(summary_module.message_api, "get_messages_by_time_in_chat", lambda **kw: [object()])
        monkeypatch.setattr(summary_module.message_api, "build_readable_messages", lambda messages: "用户说明图片")
        response = {
            "summary": "图片说明",
            "entities": [],
            "relations": [],
            "facts": [
                {
                    "fact_id": "f1",
                    "text": "图片说明",
                    "image_evidence": [{"message_id": "unknown", "component_path": "0"}],
                },
            ],
        }
        monkeypatch.setattr(
            summary_module.llm_api,
            "generate",
            AsyncMock(
                return_value=SimpleNamespace(
                    success=True,
                    completion=SimpleNamespace(response=json.dumps(response)),
                )
            ),
        )
        execute = AsyncMock()
        monkeypatch.setattr(importer, "_execute_import", execute)
        result = await importer.import_from_stream("real")
        assert not result.success and "窗口外图片证据" in result.detail
        execute.assert_not_awaited()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_unavailable_embedding_reports_error_but_keeps_exact_match(tmp_path, monkeypatch):
    metadata, runtime = _runtime(tmp_path, _Embedder())
    monkeypatch.setattr(
        runtime.embedder, "probe", AsyncMock(return_value={"available": False, "message": "图片模型未配置"})
    )
    try:
        empty = await runtime.search(chat_ids=["real"], candidate_limit=5, similarity_threshold=0.7)
        assert empty["success"] is False and empty["error"] == "图片模型未配置"
        image = await runtime.ingest(
            image_bytes=_png((1, 2, 3)), source_kind="chat", scope_type="chat", external_ref="real:1:0", chat_id="real"
        )
        exact = await runtime.search(
            content_hash=image["content_hash"], chat_ids=["real"], candidate_limit=5, similarity_threshold=0.7
        )
        assert exact["success"] is True and exact["hits"][0]["match_kind"] == "exact_hash"
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_injector_propagates_service_failure_payload(monkeypatch):
    monkeypatch.setattr(
        image_injector, "iter_message_image_components", lambda components: iter([("0", components[0])])
    )
    monkeypatch.setattr(
        image_injector.memory_service, "image_memory", AsyncMock(return_value={"success": False, "error": "检索超时"})
    )
    message = SimpleNamespace(
        message_id="m", raw_message=SimpleNamespace(components=[SimpleNamespace(binary_hash="hash")])
    )
    with pytest.raises(RuntimeError, match="检索超时"):
        await image_injector.ImageMemoryInjector().build_injection_message(session_id="real", source_messages=[message])


@pytest.mark.asyncio
async def test_image_writeback_hot_enable_and_total_switch(tmp_path, monkeypatch):
    config = SimpleNamespace(plugin=SimpleNamespace(enabled=False), image_memory=SimpleNamespace(enabled=True))
    monkeypatch.setattr(flow, "global_config", SimpleNamespace(a_memorix=config))
    service = flow.ImageMemoryWritebackService(tmp_path / "jobs.db")
    gate = asyncio.Event()
    monkeypatch.setattr(service, "_worker_loop", gate.wait)
    monkeypatch.setattr(service, "_compensation_loop", gate.wait)
    monkeypatch.setattr(flow, "iter_message_image_components", lambda components: iter([("0", object())]))
    message = SimpleNamespace(session_id="real", message_id="m", raw_message=SimpleNamespace(components=[]))
    try:
        await service.start()
        await service.enqueue(message)
        assert service._journal is None
        config.plugin.enabled = True
        await service.enqueue(message)
        assert service._journal.list_jobs("", 10, 0)["total"] == 1
        config.plugin.enabled = False
        message.message_id = "m2"
        await service.enqueue(message)
        assert service._journal.list_jobs("", 10, 0)["total"] == 1
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_component_failure_does_not_skip_remaining_images(tmp_path, monkeypatch):
    from src.chat.image_system.image_manager import image_manager

    components = [SimpleNamespace(binary_data=b"image", binary_hash=str(i), content="") for i in range(2)]
    monkeypatch.setattr(
        flow, "iter_message_image_components", lambda items: iter([(str(i), item) for i, item in enumerate(items)])
    )
    monkeypatch.setattr(image_manager, "get_cached_image_description", lambda image_hash: "")
    ingest = AsyncMock(side_effect=[{"success": False, "error": "图片损坏"}, {"success": True}])
    monkeypatch.setattr(flow.memory_service, "image_memory", ingest)
    message = SimpleNamespace(
        session_id="real", message_id="m", timestamp=datetime.now(), raw_message=SimpleNamespace(components=components)
    )
    with pytest.raises(ExceptionGroup) as error:
        await flow.ImageMemoryWritebackService(tmp_path / "jobs.db")._handle_message(message)
    assert ingest.await_count == 2
    assert str(error.value.exceptions[0]) == "图片损坏"
    assert all(component.binary_data == b"" for component in components)


@pytest.mark.asyncio
async def test_host_dispatch_from_thread_runs_on_main_loop(monkeypatch):
    main = asyncio.get_running_loop()
    monkeypatch.setattr(runtime_loop, "_main_loop", main)
    host = AMemorixHostService()

    async def invoke(component_name, args):
        assert asyncio.get_running_loop() is main
        return {"success": True}

    monkeypatch.setattr(host, "_invoke", invoke)
    result = await asyncio.to_thread(lambda: asyncio.run(host.invoke("image_memory", {})))
    assert result["success"]


@pytest.mark.parametrize(
    "url", ["https://provider.example/v1", "http://127.0.0.1:8000/v1", "http://[::1]:8000/v1", "localhost:8000/v1"]
)
def test_image_transport_allows_https_and_loopback(url):
    validate_image_embedding_transport(url)


@pytest.mark.parametrize("url", ["http://provider.example/v1", "provider.example/v1", "http://192.168.1.2:8000/v1"])
def test_image_transport_requires_https_for_remote_provider(url):
    with pytest.raises(ValueError, match="HTTPS"):
        validate_image_embedding_transport(url)


@pytest.mark.asyncio
async def test_retry_resets_failed_embedding_and_compensation_jobs(tmp_path):
    metadata, runtime = _runtime(tmp_path, _Embedder())
    try:
        await runtime.ensure_embedding_space()
        await runtime.ingest(
            image_bytes=_png((7, 8, 9)),
            source_kind="chat",
            scope_type="chat",
            external_ref="real:m:0",
            chat_id="real",
            message_id="m",
            occurred_at=100.0,
        )
        image = metadata.list_image_assets(limit=10, offset=0)[0]
        await runtime.apply_model_description(content_hash=image["content_hash"], text="图片描述")
        with metadata.transaction(immediate=True) as database:
            database.execute("UPDATE image_embedding_jobs SET status='failed', attempt_count=6")
            database.execute("UPDATE image_description_compensations SET status='failed', attempt_count=6")
        assert metadata.retry_failed_image_jobs() == {"embedding": 1, "description": 1}
        assert (await runtime.process_jobs_once())["processed"] == 1
        assert (
            len(
                metadata.claim_image_description_compensations(
                    lease_token="retry",
                    lease_seconds=30,
                    max_attempts=6,
                    limit=10,
                )
            )
            == 1
        )
        assert metadata.retry_failed_image_jobs() == {"embedding": 0, "description": 0}
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_compensation_identity_distinguishes_occurrences(tmp_path, monkeypatch):
    items = [
        {
            "occurrence_id": occurrence,
            "description_hash": "same",
            "chat_id": "real",
            "message_id": "m",
            "occurred_at": 1.0,
        }
        for occurrence in ("first", "second")
    ]
    monkeypatch.setattr(
        flow.memory_service,
        "image_memory",
        AsyncMock(
            side_effect=[
                {"success": True, "lease_token": "lease", "items": items},
                {"success": True},
                {"success": True},
            ]
        ),
    )
    summary = AsyncMock(return_value=SimpleNamespace(success=True))
    monkeypatch.setattr(flow.memory_service, "ingest_summary", summary)
    await flow.ImageMemoryWritebackService(tmp_path / "jobs.db")._process_description_compensations()
    keys = [call.kwargs["external_id"] for call in summary.await_args_list]
    assert len(set(keys)) == 2


@pytest.mark.asyncio
async def test_image_manager_shutdown_waits_for_description_sync(monkeypatch):
    from src.chat.image_system.image_manager import ImageManager

    monkeypatch.setattr(ImageManager, "cleanup_legacy_image_registration_records", lambda self: None)
    manager = ImageManager()
    gate = asyncio.Event()
    sync = asyncio.create_task(gate.wait())
    manager._description_sync_tasks.add(sync)
    sync.add_done_callback(manager._description_sync_tasks.discard)
    shutdown = asyncio.create_task(manager.shutdown())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not shutdown.done()
    gate.set()
    await shutdown
    assert not manager._description_sync_tasks
