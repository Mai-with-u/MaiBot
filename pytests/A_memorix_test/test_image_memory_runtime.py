from io import BytesIO
from pathlib import Path
from typing import Any, Dict

import asyncio
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

from src.A_memorix.core.image.asset_store import ImageAssetStore
from src.A_memorix.core.image.embedder import HostImageEmbedder
from src.A_memorix.core.image.fingerprints import build_image_embedding_fingerprint
from src.A_memorix.core.image.runtime import ImageMemoryRuntime
from src.A_memorix.core.storage import MetadataStore
from src.A_memorix.core.runtime.services.bundle_admin_service import MemoryBundleAdminService
from src.A_memorix.core.utils.hash import compute_hash, normalize_text
from pytests.A_memorix_test.test_memory_bundle_admin import _BundleKernel, _copy_bundle_for_install


def _png(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 8), color).save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize("stage", ["file", "metadata"])
def test_asset_publication_process_exit_is_reconciled(tmp_path: Path, stage: str) -> None:
    # 真正退出子进程，验证异常处理和finally都没有机会执行时的发布恢复。
    script = """
from pathlib import Path
import os
import sys
from pytests.A_memorix_test.test_image_memory_runtime import _runtime, _Embedder, _png
metadata, runtime = _runtime(Path(sys.argv[1]), _Embedder())
payload = _png((80, 90, 100))
if sys.argv[2] == 'file':
    runtime.asset_store.publish(payload, runtime.asset_store.inspect(payload))
else:
    runtime._publish_asset_only(payload)
os._exit(27)
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), stage],
        capture_output=True,
        timeout=60,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert child.returncode == 27, child.stderr.decode(errors="replace")
    metadata, runtime = _runtime(tmp_path, _Embedder())
    try:
        assert runtime.recovery["removed_files"] == 1
        assert not list(runtime.asset_store.assets_root.iterdir())
        assert metadata.image_memory_stats()["asset_count"] == 0
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_recovery_keeps_referenced_assets_and_reports_missing_file(tmp_path: Path) -> None:
    metadata, runtime = _runtime(tmp_path, _Embedder())
    try:
        image = await runtime.ingest(
            image_bytes=_png((80, 1, 2)), source_kind="chat", scope_type="chat", external_ref="keep", chat_id="real"
        )
        assert runtime.reconcile_assets()["removed_files"] == 0
        asset = metadata.get_image_asset(image["asset_id"])
        runtime.asset_store.delete(asset["storage_key"])
        recovery = runtime.reconcile_assets()
        assert len(recovery["issues"]) == 1
        assert metadata.image_memory_stats()["occurrence_count"] == 1
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_image_task_diagnostics_progress_and_timings(tmp_path: Path) -> None:
    metadata, runtime = _runtime(tmp_path, _Embedder())
    try:
        await runtime.ensure_embedding_space()
        image = await runtime.ingest(
            image_bytes=_png((90, 20, 5)), source_kind="chat", scope_type="chat", external_ref="diag", chat_id="real"
        )
        jobs = metadata.list_image_jobs(limit=10, offset=0, status="pending")
        assert jobs["total"] == 1
        assert jobs["items"][0]["asset_id"] == image["asset_id"]
        await runtime.process_jobs_once()
        assert runtime.status()["index_progress"] == {"total": 1, "ready": 1, "remaining": 0}
        assert runtime.status()["stats"]["storage_bytes"] > 0
        result = await runtime.search(
            content_hash=image["content_hash"], chat_ids=["real"], candidate_limit=8, similarity_threshold=0.72
        )
        assert {"scope", "embedding", "vector_search", "expansion", "total"} <= result["timings_ms"].keys()
        empty = await runtime.search(
            content_hash=image["content_hash"], chat_ids=["unrelated"], candidate_limit=8, similarity_threshold=0.72
        )
        assert empty["hits"] == []
        assert empty["related_memory_count"] == 0
        assert empty["timings_ms"]["embedding"] == 0
        assert empty["timings_ms"].keys() == result["timings_ms"].keys()
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_backfill_does_not_duplicate_or_revive_corrected_observation(tmp_path: Path) -> None:
    metadata, runtime = _runtime(tmp_path, _Embedder())
    try:
        kwargs = dict(
            image_bytes=_png((25, 35, 45)),
            source_kind="chat",
            scope_type="chat",
            external_ref="replay",
            chat_id="real",
            user_statement="原始说明",
        )
        first = await runtime.ingest(**kwargs)
        occurrence_id = first["occurrence_id"]
        observation = metadata.list_image_observations([occurrence_id])[0]
        runtime.add_observation(
            occurrence_id=occurrence_id,
            text="已修正说明",
            source_kind="manual",
            confirm_status="confirmed",
            evidence={},
            supersedes_id=observation["observation_id"],
        )
        await runtime.ingest(**kwargs)
        current = metadata.list_image_observations([occurrence_id])
        assert [item["text"] for item in current] == ["已修正说明"]
        assert metadata._conn.execute("SELECT COUNT(*) FROM image_observations").fetchone()[0] == 2
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_restart_rebuilds_missing_vector_projection(tmp_path: Path) -> None:
    metadata, runtime = _runtime(tmp_path, _Embedder())
    try:
        await runtime.ensure_embedding_space()
        image = await runtime.ingest(
            image_bytes=_png((9, 80, 12)),
            source_kind="chat",
            scope_type="chat",
            external_ref="lost-projection",
            chat_id="real",
        )
        await runtime.process_jobs_once()
        restarted = ImageMemoryRuntime(
            metadata_store=metadata,
            asset_store=runtime.asset_store,
            embedder=_Embedder(),
            vector_root=tmp_path / "missing-projection",
            config=runtime.config,
            persist_vector_store=runtime.persist_vector_store,
        )
        await restarted.ensure_embedding_space()
        assert metadata.list_image_jobs(limit=10, offset=0, status="pending")["total"] == 1
        assert (await restarted.process_jobs_once())["processed"] == 1
        assert image["asset_id"] in restarted.vector_store
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_background_jobs_train_index_at_image_threshold(tmp_path: Path) -> None:
    metadata, runtime = _runtime(tmp_path, _Embedder())
    runtime.config["min_train_threshold"] = 40
    runtime.config["job_batch_size"] = 40
    try:
        await runtime.ensure_embedding_space()
        for index in range(40):
            await runtime.ingest(
                image_bytes=_png((index, 100, 5)),
                source_kind="chat",
                scope_type="chat",
                external_ref=f"training:{index}",
                chat_id="real",
            )
        assert (await runtime.process_jobs_once())["processed"] == 40
        assert runtime.vector_store._is_trained
        assert runtime.vector_store._index.ntotal == 40
        assert runtime.vector_store._fallback_index.ntotal == 0
    finally:
        metadata.close()


class _Embedder:
    fingerprint = build_image_embedding_fingerprint(
        model="test-image",
        provider="test",
        dimension=2,
        preprocess_version="identity_v1",
    )

    async def probe(self) -> Dict[str, Any]:
        return {"available": True, "dimension": 2, "fingerprint": dict(self.fingerprint)}

    async def embed(self, image_bytes: bytes, *, mime_type: str, session_id: str = "") -> Dict[str, Any]:
        del mime_type, session_id
        with Image.open(BytesIO(image_bytes)) as image:
            red, green, _ = image.convert("RGB").getpixel((0, 0))
        vector = np.asarray([red + 1.0, green + 1.0], dtype=np.float32)
        vector /= np.linalg.norm(vector)
        return {"embedding": vector, "fingerprint": dict(self.fingerprint)}


class _BlockingEmbedder(_Embedder):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def embed(self, image_bytes: bytes, *, mime_type: str, session_id: str = "") -> Dict[str, Any]:
        self.started.set()
        await self.release.wait()
        return await super().embed(image_bytes, mime_type=mime_type, session_id=session_id)


def _runtime(tmp_path: Path, embedder: Any) -> tuple[MetadataStore, ImageMemoryRuntime]:
    metadata = MetadataStore(data_dir=tmp_path / "metadata")
    metadata.connect()
    runtime = ImageMemoryRuntime(
        metadata_store=metadata,
        asset_store=ImageAssetStore(tmp_path / "assets", max_bytes=1024 * 1024, max_pixels=1_000_000),
        embedder=embedder,
        vector_root=tmp_path / "vectors",
        config={"job_batch_size": 10, "job_max_retries": 2},
        persist_vector_store=lambda store, fingerprint: store.save(embedding_fingerprint=fingerprint),
    )
    return metadata, runtime


@pytest.mark.asyncio
async def test_search_excludes_current_occurrence_and_expands_real_memory(tmp_path: Path) -> None:
    metadata, runtime = _runtime(tmp_path, _Embedder())
    try:
        await runtime.ensure_embedding_space()
        first = await runtime.ingest(
            image_bytes=_png((230, 20, 0)),
            source_kind="chat",
            external_ref="chat:c:m1:0",
            scope_type="chat",
            chat_id="c",
            message_id="m1",
            component_path="0",
            user_statement="红色封面",
        )
        second = await runtime.ingest(
            image_bytes=_png((220, 25, 0)),
            source_kind="chat",
            external_ref="chat:c:m2:0",
            scope_type="chat",
            chat_id="c",
            message_id="m2",
            component_path="0",
        )
        await runtime.process_jobs_once()
        paragraph_text = "这本红色封面的书是在旧书店买的。"
        paragraph_hash = metadata.add_paragraph(paragraph_text, source="chat_summary:c")
        assert paragraph_hash == compute_hash(normalize_text(paragraph_text))
        runtime.link(
            occurrence_id=first["occurrence_id"],
            target_type="paragraph",
            target_id=paragraph_hash,
            link_kind="fact_evidence",
            evidence={"fact_id": "f1"},
        )

        result = await runtime.search(
            content_hash=second["content_hash"],
            current_occurrence_id=second["occurrence_id"],
            chat_ids=["c"],
            candidate_limit=5,
            similarity_threshold=0.9,
        )
        assert result["hits"][0]["match_kind"] == "visual_similar"
        assert result["hits"][0]["related_memories"][0]["content"] == paragraph_text
        assert all(
            occurrence["occurrence_id"] != second["occurrence_id"]
            for hit in result["hits"]
            for occurrence in hit["occurrences"]
        )
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_deleted_asset_cannot_be_published_after_embedding_await(tmp_path: Path) -> None:
    embedder = _BlockingEmbedder()
    metadata, runtime = _runtime(tmp_path, embedder)
    try:
        await runtime.ensure_embedding_space()
        ingested = await runtime.ingest(
            image_bytes=_png((20, 80, 210)),
            source_kind="chat",
            external_ref="chat:c:m1:0",
            scope_type="chat",
            chat_id="c",
            message_id="m1",
            component_path="0",
        )
        worker = asyncio.create_task(runtime.process_jobs_once())
        await embedder.started.wait()
        deleted = await runtime.delete_occurrence(ingested["occurrence_id"])
        embedder.release.set()
        await worker
        assert deleted["asset_released"] is True
        assert runtime.vector_store is not None
        assert ingested["asset_id"] not in runtime.vector_store
        assert metadata.image_memory_stats()["ready_vector_count"] == 0
    finally:
        metadata.close()


def test_asset_store_rejects_arbitrary_bytes(tmp_path: Path) -> None:
    store = ImageAssetStore(tmp_path / "assets", max_bytes=1024, max_pixels=1000)
    with pytest.raises(ValueError, match="无法解析图片"):
        store.inspect(b"not-an-image")


@pytest.mark.asyncio
async def test_reingest_rebuilds_deleted_image_vector(tmp_path: Path) -> None:
    metadata, runtime = _runtime(tmp_path, _Embedder())
    try:
        await runtime.ensure_embedding_space()
        payload = _png((42, 80, 100))
        first = await runtime.ingest(
            image_bytes=payload,
            source_kind="chat",
            external_ref="chat:c:old:0",
            scope_type="chat",
            chat_id="c",
            message_id="old",
        )
        assert (await runtime.process_jobs_once())["processed"] == 1
        await runtime.delete_occurrence(first["occurrence_id"])
        assert runtime.status()["stats"]["ready_vector_count"] == 0
        second = await runtime.ingest(
            image_bytes=payload,
            source_kind="chat",
            external_ref="chat:c:new:0",
            scope_type="chat",
            chat_id="c",
            message_id="new",
        )
        assert second["embedding_status"] == "pending"
        assert (await runtime.process_jobs_once())["processed"] == 1
        assert runtime.status()["stats"]["ready_vector_count"] == 1
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_invalid_bundle_vector_does_not_remove_existing_vector(tmp_path: Path) -> None:
    metadata, runtime = _runtime(tmp_path, _Embedder())
    try:
        await runtime.ensure_embedding_space()
        entry = await runtime.ingest(
            image_bytes=_png((80, 40, 10)),
            source_kind="chat",
            external_ref="chat:c:existing:0",
            scope_type="chat",
            chat_id="c",
        )
        await runtime.process_jobs_once()
        payload = BytesIO()
        np.savez_compressed(
            payload,
            ids=np.asarray(["existing", "unknown"]),
            vectors=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        )
        with pytest.raises(ValueError, match="未知资产"):
            await runtime.import_vectors(
                payload.getvalue(), asset_map={"existing": entry["asset_id"]}, fingerprint=runtime.fingerprint
            )
        assert entry["asset_id"] in runtime.vector_store
        assert runtime.status()["stats"]["ready_vector_count"] == 1
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_host_embedder_retries_probe_after_temporary_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    embedder = HostImageEmbedder(probe_retry_seconds=0.0)
    calls = 0
    fingerprint = dict(_Embedder.fingerprint)

    async def fake_request(image_bytes: bytes, *, mime_type: str, session_id: str = "") -> Dict[str, Any]:
        nonlocal calls
        del image_bytes, mime_type, session_id
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary provider failure")
        vector = np.asarray([0.0, 1.0] if calls == 4 else [1.0, 0.0], dtype=np.float32)
        return {"embedding": vector, "fingerprint": fingerprint}

    monkeypatch.setattr(embedder, "_request", fake_request)

    assert (await embedder.probe())["available"] is False
    assert (await embedder.probe())["available"] is True
    assert calls == 4


@pytest.mark.asyncio
async def test_host_embedder_rejects_fingerprint_change_during_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    embedder = HostImageEmbedder(probe_retry_seconds=60.0)
    calls = 0
    changed_fingerprint = build_image_embedding_fingerprint(
        model="other-image",
        provider="test",
        dimension=2,
        preprocess_version="identity_v1",
    )

    async def fake_request(image_bytes: bytes, *, mime_type: str, session_id: str = "") -> Dict[str, Any]:
        nonlocal calls
        del image_bytes, mime_type, session_id
        calls += 1
        fingerprint = changed_fingerprint if calls == 2 else _Embedder.fingerprint
        return {"embedding": np.asarray([1.0, 0.0], dtype=np.float32), "fingerprint": fingerprint}

    monkeypatch.setattr(embedder, "_request", fake_request)

    report = await embedder.probe()

    assert report["available"] is False
    assert report["message"] == "图片嵌入模型在探测期间返回了不同的嵌入空间"


@pytest.mark.asyncio
async def test_description_compensation_is_durable_and_idempotent(tmp_path: Path) -> None:
    metadata, runtime = _runtime(tmp_path, _Embedder())
    try:
        ingested = await runtime.ingest(
            image_bytes=_png((80, 90, 100)),
            source_kind="chat",
            external_ref="chat:c:m1:0",
            scope_type="chat",
            chat_id="c",
            message_id="m1",
            component_path="0",
            occurred_at=10.0,
        )
        targets = await runtime.apply_model_description(
            content_hash=ingested["content_hash"],
            text="[图片：一本放在桌上的书]",
        )
        assert len(targets) == 1
        await runtime.apply_model_description(
            content_hash=ingested["content_hash"],
            text="[图片：一本放在桌上的书]",
        )

        claimed = metadata.claim_image_description_compensations(
            lease_token="lease-1",
            lease_seconds=30,
            max_attempts=3,
            limit=10,
        )
        assert [(item["chat_id"], item["message_id"]) for item in claimed] == [("c", "m1")]
        assert metadata.complete_image_description_compensation(
            occurrence_id=ingested["occurrence_id"],
            description_hash=str(claimed[0]["description_hash"]),
            lease_token="lease-1",
            success=True,
        )
        assert (
            metadata.claim_image_description_compensations(
                lease_token="lease-2",
                lease_seconds=30,
                max_attempts=3,
                limit=10,
            )
            == []
        )
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_description_arriving_before_asset_is_applied_after_ingest(tmp_path: Path) -> None:
    metadata, runtime = _runtime(tmp_path, _Embedder())
    payload = _png((110, 120, 130))
    content_hash = ImageAssetStore.content_hash(payload)
    try:
        targets = await runtime.apply_model_description(
            content_hash=content_hash,
            text="[图片：一张提前完成识别的照片]",
        )
        assert targets == []
        assert metadata.image_memory_stats()["pending_unbound_description_count"] == 1

        ingested = await runtime.ingest(
            image_bytes=payload,
            source_kind="chat",
            external_ref="chat:c:early:0",
            scope_type="chat",
            chat_id="c",
            message_id="early",
            component_path="0",
            occurred_at=11.0,
        )

        assert metadata.image_memory_stats()["pending_unbound_description_count"] == 0
        assert metadata.image_memory_stats()["pending_description_count"] == 1
        observations = metadata.list_image_observations([ingested["occurrence_id"]])
        assert [item["text"] for item in observations] == ["[图片：一张提前完成识别的照片]"]
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_manual_observation_correction_and_link_invalidation(tmp_path: Path) -> None:
    metadata, runtime = _runtime(tmp_path, _Embedder())
    try:
        ingested = await runtime.ingest(
            image_bytes=_png((140, 150, 160)),
            source_kind="chat",
            external_ref="chat:c:manual:0",
            scope_type="chat",
            chat_id="c",
            message_id="manual",
            component_path="0",
            occurred_at=12.0,
        )
        first = runtime.add_observation(
            occurrence_id=ingested["occurrence_id"],
            text="一只白猫",
            source_kind="manual",
            confirm_status="confirmed",
        )["observation"]
        corrected = runtime.add_observation(
            occurrence_id=ingested["occurrence_id"],
            text="一只浅灰色的猫",
            source_kind="manual",
            confirm_status="confirmed",
            supersedes_id=first["observation_id"],
        )["observation"]
        assert corrected["version"] == 2
        assert [item["text"] for item in metadata.list_image_observations([ingested["occurrence_id"]])] == [
            "一只浅灰色的猫"
        ]

        paragraph_hash = metadata.add_paragraph("这只猫叫团团。", source="chat_summary:c")
        link = runtime.link(
            occurrence_id=ingested["occurrence_id"],
            target_type="paragraph",
            target_id=paragraph_hash,
            link_kind="fact_evidence",
            evidence={"fact_id": "cat-name"},
        )["link"]
        assert runtime.unlink(link["link_id"])["success"] is True
        assert metadata.list_image_memory_links([ingested["occurrence_id"]]) == []
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_package_description_does_not_create_unclaimable_compensation(tmp_path: Path) -> None:
    metadata, runtime = _runtime(tmp_path, _Embedder())
    try:
        ingested = await runtime.ingest(
            image_bytes=_png((30, 40, 50)),
            source_kind="package",
            external_ref="package:demo:image:0",
            scope_type="global",
            component_path="0",
        )

        targets = await runtime.apply_model_description(
            content_hash=ingested["content_hash"],
            text="[图片：记忆包中的风景照片]",
        )

        assert targets == []
        assert metadata.image_memory_stats()["pending_description_count"] == 0
        observations = metadata.list_image_observations([ingested["occurrence_id"]])
        assert [item["text"] for item in observations] == ["[图片：记忆包中的风景照片]"]
    finally:
        metadata.close()


@pytest.mark.asyncio
async def test_failed_bundle_record_import_releases_orphan_asset(tmp_path: Path) -> None:
    metadata, runtime = _runtime(tmp_path, _Embedder())
    payload = _png((1, 2, 3))
    content_hash = runtime.asset_store.content_hash(payload)
    records = {
        "assets": [{"asset_id": "source-asset", "content_hash": content_hash}],
        "occurrences": [{"occurrence_id": "source-occurrence", "asset_id": "source-asset"}],
        "observations": [],
        "links": [
            {
                "occurrence_id": "source-occurrence",
                "target_type": "paragraph",
                "target_id": "missing-paragraph",
            }
        ],
    }
    try:
        with pytest.raises(ValueError, match="无法映射"):
            await runtime.import_records(
                records=records,
                asset_bytes={content_hash: payload},
                installation_id="install-failed",
                scope_type="global",
                chat_id="",
                target_id_map={},
            )
        asset = metadata.get_image_asset_by_hash(content_hash)
        assert asset is not None
        assert asset["status"] == "deleted"
        assert not (runtime.asset_store.assets_root / str(asset["storage_key"])).exists()
    finally:
        metadata.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_link", [False, True])
async def test_pure_image_bundle_round_trip_has_no_phantom_occurrence(tmp_path: Path, with_link: bool) -> None:
    source_kernel = _BundleKernel(tmp_path / "bundle-source")
    target_kernel = _BundleKernel(tmp_path / "bundle-target")
    source_runtime = ImageMemoryRuntime(
        metadata_store=source_kernel.metadata_store,
        asset_store=ImageAssetStore(tmp_path / "bundle-source-assets", max_bytes=1024 * 1024, max_pixels=1000),
        embedder=_Embedder(),
        vector_root=tmp_path / "bundle-source-vectors",
        config={},
        persist_vector_store=lambda store, fingerprint: store.save(embedding_fingerprint=fingerprint),
    )
    target_runtime = ImageMemoryRuntime(
        metadata_store=target_kernel.metadata_store,
        asset_store=ImageAssetStore(tmp_path / "bundle-target-assets", max_bytes=1024 * 1024, max_pixels=1000),
        embedder=_Embedder(),
        vector_root=tmp_path / "bundle-target-vectors",
        config={},
        persist_vector_store=lambda store, fingerprint: store.save(embedding_fingerprint=fingerprint),
    )
    source_kernel.image_memory_runtime = source_runtime
    target_kernel.image_memory_runtime = target_runtime
    try:
        await source_runtime.ensure_embedding_space()
        await target_runtime.ensure_embedding_space()
        image = await source_runtime.ingest(
            image_bytes=_png((180, 30, 10)),
            source_kind="chat",
            external_ref="chat:old:m1:0",
            scope_type="chat",
            chat_id="old",
            message_id="m1",
            component_path="0",
            user_statement="旧书封面",
        )
        await source_runtime.process_jobs_once()
        if with_link:
            paragraph_id = source_kernel.metadata_store.add_paragraph("旧书封面对应周日读书会。", source="manual")
            source_runtime.link(
                occurrence_id=image["occurrence_id"],
                target_type="paragraph",
                target_id=paragraph_id,
                link_kind="fact_evidence",
                evidence={"fact_id": "f1"},
            )
        source_bundle_service = MemoryBundleAdminService(source_kernel)
        preview = await source_bundle_service.memory_bundle_admin(
            action="export",
            selector={"type": "image", "content_hashes": [image["content_hash"]]},
            include_image_related=False,
            preview=True,
        )
        assert preview["counts"]["paragraphs"] == 0
        assert preview["counts"]["image_links"] == 0
        assert not list(source_kernel.data_dir.rglob("*.amembundle"))
        exported = await source_bundle_service.memory_bundle_admin(
            action="export",
            selector={"type": "image", "content_hashes": [image["content_hash"]]},
            include_vectors=True,
            include_image_related=True,
            package={"id": "images.books", "version": "1.0.0", "name": "书封图片"},
        )
        assert exported["manifest"]["format_version"] == 2
        assert exported["counts"]["image_occurrences"] == 1
        assert exported["counts"]["paragraphs"] == int(with_link)
        loaded = source_bundle_service._load_bundle(Path(exported["path"]))
        assert "members" not in loaded
        assert any(name.startswith("images/assets/") for name in loaded["member_names"])

        installed = await MemoryBundleAdminService(target_kernel).memory_bundle_admin(
            action="import",
            path=str(_copy_bundle_for_install(exported["path"], target_kernel.data_dir)),
            scope_type="chat",
            chat_id="new",
        )
        assert installed["images"]["assets"] == 1
        assert installed["images"]["occurrences"] == 1
        assert installed["images"]["content_status"] == "installed"
        assert installed["images"]["retrieval_status"] == "ready"
        assert target_kernel.metadata_store.image_memory_stats()["occurrence_count"] == 1
        occurrence = target_kernel.metadata_store.list_visible_image_occurrences(chat_ids=["new"])[0]
        assert occurrence["source_kind"] == "imported_knowledge"
        if with_link:
            result = await target_runtime.search(
                content_hash=image["content_hash"], chat_ids=["new"], candidate_limit=8, similarity_threshold=0.72
            )
            assert result["hits"][0]["related_memories"][0]["content"] == "旧书封面对应周日读书会。"
        reexported = await MemoryBundleAdminService(target_kernel).memory_bundle_admin(
            action="export",
            selector={"type": "image", "content_hashes": [image["content_hash"]]},
            include_vectors=True,
            include_image_related=True,
            package={"id": "images.books.copy", "version": "1.0.0", "name": "书封图片副本"},
        )
        # 混合包的文字元数据会映射目标聊天流和安装来源，重新导出后内容摘要可变化。
        if not with_link:
            assert reexported["manifest"]["content_digest"] == exported["manifest"]["content_digest"]
        else:
            assert reexported["counts"]["paragraphs"] == 1
            assert reexported["counts"]["image_links"] == 1

        uninstalled = await MemoryBundleAdminService(target_kernel).memory_bundle_admin(
            action="uninstall",
            installation_id=installed["installation_id"],
        )
        assert uninstalled["success"] is True
        assert target_kernel.metadata_store.image_memory_stats()["asset_count"] == 0
    finally:
        source_kernel.metadata_store.close()
        target_kernel.metadata_store.close()


@pytest.mark.asyncio
async def test_image_bundle_stream_validation_supports_more_than_32_assets(tmp_path: Path) -> None:
    kernel = _BundleKernel(tmp_path / "many-images")
    runtime = ImageMemoryRuntime(
        metadata_store=kernel.metadata_store,
        asset_store=ImageAssetStore(tmp_path / "many-image-assets", max_bytes=1024 * 1024, max_pixels=1000),
        embedder=_Embedder(),
        vector_root=tmp_path / "many-image-vectors",
        config={},
        persist_vector_store=lambda store, fingerprint: store.save(embedding_fingerprint=fingerprint),
    )
    kernel.image_memory_runtime = runtime
    try:
        content_hashes = []
        for index in range(40):
            ingested = await runtime.ingest(
                image_bytes=_png((index, 255 - index, index * 3 % 256)),
                source_kind="chat",
                external_ref=f"chat:many:message-{index}:0",
                scope_type="chat",
                chat_id="many",
                message_id=f"message-{index}",
                component_path="0",
                occurred_at=float(index),
            )
            content_hashes.append(ingested["content_hash"])

        service = MemoryBundleAdminService(kernel)
        exported = await service.memory_bundle_admin(
            action="export",
            selector={"type": "image", "content_hashes": content_hashes},
            include_vectors=False,
            package={"id": "images.many", "version": "1.0.0", "name": "批量图片"},
        )
        loaded = service._load_bundle(Path(exported["path"]))

        assert exported["counts"]["image_assets"] == 40
        assert len(loaded["member_names"]) > 32
        assert "members" not in loaded
    finally:
        kernel.metadata_store.close()
