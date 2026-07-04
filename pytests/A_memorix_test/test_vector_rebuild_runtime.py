from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import asyncio
import hashlib
import json
import numpy as np
import pytest

from src.A_memorix.core.runtime import sdk_memory_kernel as kernel_module
from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel


class _FakeEmbeddingManager:
    def __init__(self, dimension: int = 8, model_name: str = "fake-embedding") -> None:
        self.default_dimension = dimension
        self.model_name = model_name
        self.encode_calls: list[Any] = []
        self.detect_calls = 0

    async def _detect_dimension(self) -> int:
        self.detect_calls += 1
        return self.default_dimension

    async def encode(self, text: Any, **kwargs: Any) -> np.ndarray:
        del kwargs
        self.encode_calls.append(text)

        def _encode_one(raw: Any) -> np.ndarray:
            content = str(raw or "")
            vector = np.zeros(self.default_dimension, dtype=np.float32)
            for index, byte in enumerate(content.encode("utf-8")):
                vector[index % self.default_dimension] += float((byte % 17) + 1)
            norm = float(np.linalg.norm(vector))
            if norm > 0:
                vector /= norm
            return vector

        if isinstance(text, (list, tuple)):
            return np.stack([_encode_one(item) for item in text]).astype(np.float32)
        return _encode_one(text).astype(np.float32)

    async def encode_batch(self, texts: Any, **kwargs: Any) -> np.ndarray:
        return await self.encode(texts, **kwargs)

    def get_embedding_fingerprint(self, *, dimension: int | None = None) -> Dict[str, Any]:
        effective_dimension = int(dimension or self.default_dimension)
        raw = f"{self.model_name}|fake-provider|{effective_dimension}|explicit"
        return {
            "version": 1,
            "hash": f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}",
            "model": self.model_name,
            "provider": "fake-provider",
            "dimension": effective_dimension,
            "dimension_request_mode": "explicit",
            "source": "configured",
        }


def _kernel_config(data_dir: Path, dimension: int) -> dict[str, Any]:
    return {
        "storage": {"data_dir": str(data_dir.resolve())},
        "advanced": {"enable_auto_save": False},
        "embedding": {
            "dimension": dimension,
            "batch_size": 2,
            "paragraph_vector_backfill": {"enabled": False},
        },
        "retrieval": {
            "relation_vectorization": {"enabled": True},
            "vector_pools": {"mode": "single"},
            "sparse": {"enabled": False},
            "enable_ppr": False,
            "enable_parallel": False,
        },
    }


def _default_dual_kernel_config(data_dir: Path, dimension: int) -> dict[str, Any]:
    config = _kernel_config(data_dir, dimension)
    config["retrieval"].pop("vector_pools", None)
    return config


def _dual_kernel_config(data_dir: Path, dimension: int) -> dict[str, Any]:
    config = _kernel_config(data_dir, dimension)
    config["retrieval"]["vector_pools"] = {"mode": "dual"}
    return config


async def _fake_runtime_self_check(**kwargs: Any) -> dict[str, Any]:
    vector_store = kwargs["vector_store"]
    embedding_manager = kwargs["embedding_manager"]
    dimension = int(embedding_manager.default_dimension)
    return {
        "ok": int(vector_store.dimension) == dimension,
        "message": "ok",
        "configured_dimension": dimension,
        "requested_dimension": dimension,
        "vector_store_dimension": int(vector_store.dimension),
        "detected_dimension": dimension,
        "encoded_dimension": dimension,
        "elapsed_ms": 0.0,
        "sample_text": "test",
        "checked_at": 1_777_000_000.0,
    }


async def _wait_background_task(kernel: SDKMemoryKernel, name: str) -> None:
    task = kernel._background_tasks.get(name)
    if task is None:
        return
    await asyncio.wait_for(task, timeout=2.0)


def test_dual_auto_migration_progress_tracks_stable_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = 100.0
    monkeypatch.setattr(kernel_module.time, "time", lambda: now)
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(tmp_path / "a_memorix_data", 8),
    )
    kernel._dual_vector_auto_migration_status.update(
        {
            "running": True,
            "started_at": 100.0,
            "progress": {},
        }
    )

    kernel._update_dual_vector_auto_migration_stage(
        "prepare_rebuild",
        total=10,
        counts={"paragraphs": 4, "entities": 3, "relations": 3},
    )
    progress = kernel._dual_vector_auto_migration_status["progress"]
    assert kernel._dual_vector_auto_migration_status["stage"] == "prepare_rebuild"
    assert progress["total"] == 10
    assert progress["processed"] == 0
    assert progress["percent"] == 0.0
    assert progress["elapsed_seconds"] == 0.0
    assert progress["estimated_remaining_seconds"] is None

    now = 110.0
    kernel._update_dual_vector_auto_migration_stage("paragraphs_done", paragraph_done=4)
    progress = kernel._dual_vector_auto_migration_status["progress"]
    assert progress["processed"] == 4
    assert progress["percent"] == 40.0
    assert progress["elapsed_seconds"] == 10.0
    assert progress["estimated_remaining_seconds"] == 15

    completed_progress = kernel._normalize_dual_vector_auto_migration_progress(
        progress,
        now=120.0,
        completed=True,
        success=True,
    )
    assert completed_progress["processed"] == 10
    assert completed_progress["percent"] == 100.0
    assert completed_progress["estimated_remaining_seconds"] is None


def test_dual_auto_migration_progress_has_no_eta_without_total(
    tmp_path: Path,
) -> None:
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(tmp_path / "a_memorix_data", 8),
    )
    kernel._dual_vector_auto_migration_status.update({"started_at": 100.0})

    progress = kernel._normalize_dual_vector_auto_migration_progress(
        {"processed": 5},
        now=110.0,
        explicit_processed=True,
    )

    assert progress["total"] == 0
    assert progress["processed"] == 5
    assert progress["percent"] == 0.0
    assert progress["estimated_remaining_seconds"] is None


def test_dual_auto_migration_progress_caps_running_percent(
    tmp_path: Path,
) -> None:
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(tmp_path / "a_memorix_data", 8),
    )
    kernel._dual_vector_auto_migration_status.update({"started_at": 100.0})

    progress = kernel._normalize_dual_vector_auto_migration_progress(
        {
            "total": 100,
            "processed": 100,
        },
        now=110.0,
        explicit_processed=True,
    )

    assert progress["processed"] == 100
    assert progress["percent"] == 99.5
    assert progress["estimated_remaining_seconds"] is None


def test_dual_auto_migration_progress_counts_failed_items_as_processed(
    tmp_path: Path,
) -> None:
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(tmp_path / "a_memorix_data", 8),
    )
    kernel._dual_vector_auto_migration_status.update({"started_at": 100.0})

    progress = kernel._normalize_dual_vector_auto_migration_progress(
        {
            "total": 10,
            "paragraph_done": 3,
            "paragraph_failed": 1,
            "entity_done": 2,
            "entity_failed": 1,
        },
        now=110.0,
    )

    assert progress["processed"] == 7
    assert progress["percent"] == 70.0
    assert progress["estimated_remaining_seconds"] == 5


def test_dual_auto_migration_progress_coerces_invalid_values(
    tmp_path: Path,
) -> None:
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(tmp_path / "a_memorix_data", 8),
    )
    kernel._dual_vector_auto_migration_status.update({"started_at": 100.0})

    progress = kernel._normalize_dual_vector_auto_migration_progress(
        {
            "total": "bad",
            "counts": {"paragraphs": 2, "entities": "3", "relations": -10},
            "processed": "bad",
        },
        now=110.0,
        explicit_processed=True,
    )

    assert progress["total"] == 5
    assert progress["processed"] == 0
    assert progress["percent"] == 0.0
    assert progress["estimated_remaining_seconds"] is None


@pytest.mark.asyncio
async def test_runtime_admin_rebuild_all_vectors_replaces_existing_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: fake_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )

    await kernel.initialize()
    assert kernel.metadata_store is not None
    assert kernel.vector_store is not None

    paragraph_hash = kernel.metadata_store.add_paragraph("用户喜欢蓝色围巾", source="test")
    entity_hash = kernel.metadata_store.add_entity("蓝色围巾")
    relation_hash = kernel.metadata_store.add_relation("用户", "喜欢", "蓝色围巾")
    inactive_relation_hash = kernel.metadata_store.add_relation("旧用户", "忘记", "旧围巾")
    kernel.metadata_store.mark_relations_inactive([inactive_relation_hash])
    old_summary_importer_store = kernel.summary_importer.vector_store
    old_profile_store = kernel.person_profile_service.vector_store
    old_episode_retriever = kernel.episode_retriever.retriever
    kernel.vector_store.add(
        np.ones((1, fake_embedding_manager.default_dimension), dtype=np.float32),
        ["stale-vector"],
    )
    kernel.vector_store.save()

    preview = await kernel.memory_runtime_admin(action="rebuild_all_vectors", dry_run=True)
    assert preview["success"] is True
    assert preview["counts"] == {"paragraphs": 1, "entities": 1, "relations": 1}

    result = await kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=2)
    assert result["success"] is True
    assert result["done"] == 3
    assert result["failed"] == 0
    assert paragraph_hash in kernel.vector_store
    assert entity_hash in kernel.vector_store
    assert relation_hash in kernel.vector_store
    assert inactive_relation_hash not in kernel.vector_store
    assert "stale-vector" not in kernel.vector_store
    assert kernel.summary_importer.vector_store is kernel.vector_store
    assert kernel.person_profile_service.vector_store is kernel.vector_store
    assert kernel.episode_retriever.retriever is kernel.retriever
    assert kernel.summary_importer.vector_store is not old_summary_importer_store
    assert kernel.person_profile_service.vector_store is not old_profile_store
    assert kernel.episode_retriever.retriever is not old_episode_retriever

    config = await kernel.memory_runtime_admin(action="get_config")
    assert config["vector_rebuild_required"] is False
    assert config["stored_vector_dimension"] == fake_embedding_manager.default_dimension
    assert config["embedding_fingerprint_status"] == "matched"
    assert config["stored_embedding_fingerprint"]["hash"] == config["embedding_fingerprint"]["hash"]

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_dual_rebuild_copies_existing_single_pool_vectors_without_embedding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: fake_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await kernel.initialize()
    assert kernel.metadata_store is not None
    assert kernel.vector_store is not None
    assert kernel.paragraph_vector_store is not None
    assert kernel.graph_vector_store is not None

    paragraph_hash = kernel.metadata_store.add_paragraph("用户喜欢蓝色围巾", source="test")
    entity_hash = kernel.metadata_store.add_entity("蓝色围巾")
    relation_hash = kernel.metadata_store.add_relation("用户", "喜欢", "蓝色围巾")

    legacy_vectors = np.eye(3, fake_embedding_manager.default_dimension, dtype=np.float32)
    kernel.vector_store.add(legacy_vectors, [paragraph_hash, entity_hash, relation_hash])
    kernel._save_vector_store(kernel.vector_store)
    fake_embedding_manager.encode_calls.clear()

    result = await kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=2)

    assert result["success"] is True
    assert result["migration"]["paragraphs"] == {"copied": 1, "encoded": 0, "missing": 0}
    assert result["migration"]["entities"] == {"copied": 1, "encoded": 0, "missing": 0}
    assert result["migration"]["relations"] == {"copied": 1, "encoded": 0, "missing": 0}
    assert fake_embedding_manager.encode_calls == []
    assert paragraph_hash not in kernel.vector_store
    assert entity_hash not in kernel.vector_store
    assert relation_hash not in kernel.vector_store
    assert paragraph_hash in kernel.paragraph_vector_store
    assert f"entity:{entity_hash}" in kernel.graph_vector_store
    assert f"relation:{relation_hash}" in kernel.graph_vector_store
    assert kernel._dual_vector_pools_enabled() is True
    manifest_path = data_dir / "vectors" / "dual_ready.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "ready"
    assert manifest["paragraph_vectors"] == 1
    assert manifest["graph_vectors"] == 2
    assert not list((data_dir / "vectors").glob("dual_build_*"))
    assert kernel.retriever.config.vector_pools.mode == "dual"

    await kernel.shutdown()

    reloaded_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_reloaded",
        config=_dual_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await reloaded_kernel.initialize()
    assert reloaded_kernel._dual_vector_pools_enabled() is True
    assert reloaded_kernel.retriever.config.vector_pools.mode == "dual"
    assert reloaded_kernel.paragraph_vector_store is not None
    assert reloaded_kernel.graph_vector_store is not None
    assert paragraph_hash in reloaded_kernel.paragraph_vector_store
    assert f"entity:{entity_hash}" in reloaded_kernel.graph_vector_store

    await reloaded_kernel.shutdown()


@pytest.mark.asyncio
async def test_dual_rebuild_reencodes_when_single_pool_fingerprint_mismatches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_embedding_manager = _FakeEmbeddingManager(dimension=8, model_name="fake-embedding-a")
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: first_embedding_manager)
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    first_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_first",
        config=_kernel_config(data_dir, first_embedding_manager.default_dimension),
    )
    await first_kernel.initialize()
    assert first_kernel.metadata_store is not None
    paragraph_hash = first_kernel.metadata_store.add_paragraph("需要重编码的段落", source="test")
    result = await first_kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=2, include_relations=False)
    assert result["success"] is True
    assert paragraph_hash in first_kernel.vector_store
    await first_kernel.shutdown()

    second_embedding_manager = _FakeEmbeddingManager(dimension=8, model_name="fake-embedding-b")
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: second_embedding_manager)

    second_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_second",
        config=_dual_kernel_config(data_dir, second_embedding_manager.default_dimension),
    )
    await second_kernel.initialize()
    try:
        result = await second_kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=2, include_relations=False)
        assert result["success"] is True
        assert result["migration"]["paragraphs"]["copied"] == 0
        assert result["migration"]["paragraphs"]["encoded"] == 1
        config = await second_kernel.memory_runtime_admin(action="get_config")
        assert config["embedding_fingerprint_status"] == "matched"
        assert config["vector_rebuild_required"] is False
    finally:
        await second_kernel.shutdown()


@pytest.mark.asyncio
async def test_dual_rebuild_encodes_only_missing_single_pool_vectors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: fake_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await kernel.initialize()
    assert kernel.metadata_store is not None
    assert kernel.vector_store is not None
    assert kernel.paragraph_vector_store is not None
    assert kernel.graph_vector_store is not None

    copied_paragraph_hash = kernel.metadata_store.add_paragraph("已有旧向量段落", source="test")
    missing_paragraph_hash = kernel.metadata_store.add_paragraph("缺失旧向量段落", source="test")
    copied_entity_hash = kernel.metadata_store.add_entity("已有旧向量实体")
    missing_entity_hash = kernel.metadata_store.add_entity("缺失旧向量实体")

    kernel.vector_store.add(
        np.eye(2, fake_embedding_manager.default_dimension, dtype=np.float32),
        [copied_paragraph_hash, copied_entity_hash],
    )
    kernel._save_vector_store(kernel.vector_store)
    fake_embedding_manager.encode_calls.clear()

    result = await kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=8, include_relations=False)

    assert result["success"] is True
    assert result["migration"]["paragraphs"] == {"copied": 1, "encoded": 1, "missing": 1}
    assert result["migration"]["entities"] == {"copied": 1, "encoded": 1, "missing": 1}
    assert fake_embedding_manager.encode_calls == [["缺失旧向量段落"], ["缺失旧向量实体"]]
    assert copied_paragraph_hash in kernel.paragraph_vector_store
    assert missing_paragraph_hash in kernel.paragraph_vector_store
    assert f"entity:{copied_entity_hash}" in kernel.graph_vector_store
    assert f"entity:{missing_entity_hash}" in kernel.graph_vector_store
    manifest_path = data_dir / "vectors" / "dual_ready.json"
    assert manifest_path.exists()

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_dual_rebuild_backfills_writes_created_during_pool_activation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: fake_embedding_manager)
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await kernel.initialize()
    assert kernel.metadata_store is not None
    assert kernel.vector_store is not None

    original_paragraph_hash = kernel.metadata_store.add_paragraph("迁移开始前的段落", source="test")
    original_entity_hash = kernel.metadata_store.add_entity("迁移开始前的实体")
    original_relation_hash = kernel.metadata_store.add_relation("用户", "喜欢", "迁移开始前的实体")
    kernel.vector_store.add(
        np.eye(3, fake_embedding_manager.default_dimension, dtype=np.float32),
        [original_paragraph_hash, original_entity_hash, original_relation_hash],
    )
    kernel._save_vector_store(kernel.vector_store)

    original_activate = kernel._activate_dual_vector_build_dirs
    late_hashes: dict[str, str] = {}

    def _activate_with_late_write(build_root: Path) -> None:
        paragraph_hash = kernel.metadata_store.add_paragraph("迁移期间新增的段落", source="test")
        entity_hash = kernel.metadata_store.add_entity("迁移期间新增的实体")
        relation_hash = kernel.metadata_store.add_relation("用户", "提到", "迁移期间新增的实体")
        kernel.vector_store.add(
            np.eye(3, fake_embedding_manager.default_dimension, dtype=np.float32),
            [paragraph_hash, entity_hash, relation_hash],
        )
        kernel._save_vector_store(kernel.vector_store)
        late_hashes.update(
            {
                "paragraph": paragraph_hash,
                "entity": entity_hash,
                "relation": relation_hash,
            }
        )
        original_activate(build_root)

    monkeypatch.setattr(kernel, "_activate_dual_vector_build_dirs", _activate_with_late_write)

    result = await kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=2)

    assert result["success"] is True
    assert kernel._dual_vector_pools_enabled() is True
    assert late_hashes["paragraph"] in kernel.paragraph_vector_store
    assert f"entity:{late_hashes['entity']}" in kernel.graph_vector_store
    assert f"relation:{late_hashes['relation']}" in kernel.graph_vector_store
    config = await kernel.memory_runtime_admin(action="get_config")
    assert config["vector_pools"]["paragraph_pool"]["num_vectors"] == 2
    assert config["vector_pools"]["graph_pool"]["num_vectors"] == 4

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_dual_initialize_without_ready_manifest_falls_back_to_single_pool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: fake_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await kernel.initialize()
    assert kernel.vector_store is not None
    kernel.vector_store.add(
        np.ones((1, fake_embedding_manager.default_dimension), dtype=np.float32),
        ["legacy-paragraph"],
    )
    kernel.vector_store.save()
    await kernel.shutdown()

    paragraph_dir = data_dir / "vectors" / "paragraph"
    graph_dir = data_dir / "vectors" / "graph"
    paragraph_dir.mkdir(parents=True, exist_ok=True)
    graph_dir.mkdir(parents=True, exist_ok=True)
    stale_build_dir = data_dir / "vectors" / "dual_build_stale"
    stale_build_dir.mkdir(parents=True, exist_ok=True)

    dual_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_dual",
        config=_dual_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await dual_kernel.initialize()

    assert dual_kernel._dual_vector_pools_enabled() is False
    assert dual_kernel.retriever.config.vector_pools.mode == "single"
    assert not stale_build_dir.exists()

    await dual_kernel.shutdown()


@pytest.mark.asyncio
async def test_default_dual_mode_starts_auto_migration_without_blocking_initialize(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    release = asyncio.Event()
    rebuild_started = asyncio.Event()
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: fake_embedding_manager)
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)
    monkeypatch.setattr(kernel_module, "DUAL_VECTOR_AUTO_MIGRATION_INITIAL_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(kernel_module, "DUAL_VECTOR_AUTO_MIGRATION_LOCK_RETRY_DELAYS_SECONDS", ())

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_default_dual_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    original_rebuild = kernel._rebuild_all_vectors

    async def _blocked_rebuild(**kwargs: Any) -> dict[str, Any]:
        rebuild_started.set()
        await release.wait()
        return await original_rebuild(**kwargs)

    monkeypatch.setattr(kernel, "_rebuild_all_vectors", _blocked_rebuild)
    await kernel.initialize()
    try:
        assert kernel.retriever.config.vector_pools.mode == "single"
        assert kernel._dual_vector_pools_enabled() is False
        await asyncio.wait_for(rebuild_started.wait(), timeout=1.0)
        config = await kernel.memory_runtime_admin(action="get_config")
        assert config["vector_pools"]["configured_mode"] == "dual"
        assert config["vector_pools_effective_mode"] == "single"
        assert config["vector_pools"]["auto_migration"]["running"] is True
    finally:
        release.set()
        await _wait_background_task(kernel, "dual_vector_auto_migration")
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_dual_auto_migration_switches_to_dual_when_rebuild_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: fake_embedding_manager)
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)
    monkeypatch.setattr(kernel_module, "DUAL_VECTOR_AUTO_MIGRATION_INITIAL_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(kernel_module, "DUAL_VECTOR_AUTO_MIGRATION_LOCK_RETRY_DELAYS_SECONDS", ())

    single_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_single",
        config=_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await single_kernel.initialize()
    assert single_kernel.metadata_store is not None
    paragraph_hash = single_kernel.metadata_store.add_paragraph("自动迁移段落", source="test")
    entity_hash = single_kernel.metadata_store.add_entity("自动迁移实体")
    rebuild = await single_kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=2, include_relations=False)
    assert rebuild["success"] is True
    await single_kernel.shutdown()

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await kernel.initialize()
    await _wait_background_task(kernel, "dual_vector_auto_migration")

    assert kernel._dual_vector_pools_enabled() is True
    assert kernel.retriever.config.vector_pools.mode == "dual"
    assert (data_dir / "vectors" / "dual_ready.json").exists()
    assert paragraph_hash in kernel.paragraph_vector_store
    assert f"entity:{entity_hash}" in kernel.graph_vector_store
    config = await kernel.memory_runtime_admin(action="get_config")
    assert config["vector_pools"]["auto_migration"]["success"] is True
    assert config["vector_pools_effective_mode"] == "dual"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_dual_auto_migration_failure_keeps_single_pool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: fake_embedding_manager)
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)
    monkeypatch.setattr(kernel_module, "DUAL_VECTOR_AUTO_MIGRATION_INITIAL_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(kernel_module, "DUAL_VECTOR_AUTO_MIGRATION_LOCK_RETRY_DELAYS_SECONDS", ())

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )

    async def _failed_rebuild(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"success": False, "error": "embedding_down", "detail": "embedding_down"}

    monkeypatch.setattr(kernel, "_rebuild_all_vectors", _failed_rebuild)
    await kernel.initialize()
    await _wait_background_task(kernel, "dual_vector_auto_migration")

    assert kernel._dual_vector_pools_enabled() is False
    assert kernel.retriever.config.vector_pools.mode == "single"
    assert not (data_dir / "vectors" / "dual_ready.json").exists()
    config = await kernel.memory_runtime_admin(action="get_config")
    assert config["vector_pools"]["auto_migration"]["success"] is False
    assert config["vector_pools"]["auto_migration"]["last_error"] == "embedding_down"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_dual_auto_migration_waits_for_manual_rebuild_lock_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    sleep_calls: list[float] = []
    rebuild_calls = 0
    release_lock = asyncio.Event()
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: fake_embedding_manager)
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)
    monkeypatch.setattr(kernel_module, "DUAL_VECTOR_AUTO_MIGRATION_INITIAL_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(kernel_module, "DUAL_VECTOR_AUTO_MIGRATION_LOCK_RETRY_DELAYS_SECONDS", (0.01,))

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )

    async def _fast_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if seconds > 0:
            release_lock.set()
            await asyncio.sleep(0)

    async def _fake_rebuild(**kwargs: Any) -> dict[str, Any]:
        nonlocal rebuild_calls
        del kwargs
        rebuild_calls += 1
        kernel._dual_vector_pools_ready = True
        return {"success": True}

    monkeypatch.setattr(kernel, "_sleep_background", _fast_sleep)
    monkeypatch.setattr(kernel, "_rebuild_all_vectors", _fake_rebuild)

    async with kernel._vector_rebuild_lock:
        await kernel.initialize()
        await asyncio.wait_for(release_lock.wait(), timeout=1.0)
    await _wait_background_task(kernel, "dual_vector_auto_migration")

    assert rebuild_calls == 1
    assert 0.01 in sleep_calls
    assert kernel._dual_vector_pools_enabled() is True

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_failed_dual_rebuild_keeps_single_pool_and_drops_temp_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: fake_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await kernel.initialize()
    assert kernel.metadata_store is not None
    kernel.metadata_store.add_paragraph("无法编码的段落", source="test")

    async def _failing_encode(**kwargs: Any):
        ids = [item_id for item_id, _text in kwargs["items"]]
        return 0, len(ids), "embedding_down", [], ids

    monkeypatch.setattr(kernel, "_encode_and_add_rebuild_vectors", _failing_encode)
    result = await kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=1, include_relations=False)

    assert result["success"] is False
    assert result["failed"] == 1
    assert not (data_dir / "vectors" / "dual_ready.json").exists()
    assert not list((data_dir / "vectors").glob("dual_build_*"))
    assert kernel._dual_vector_pools_enabled() is False
    assert kernel.retriever.config.vector_pools.mode == "single"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_runtime_admin_rebuild_all_vectors_rejects_concurrent_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: fake_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )

    await kernel.initialize()
    assert kernel.metadata_store is not None
    kernel.metadata_store.add_paragraph("并发重建测试", source="test")

    original_encode = kernel._encode_and_add_rebuild_vectors
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_encode(**kwargs: Any):
        if not started.is_set():
            started.set()
            await release.wait()
        return await original_encode(**kwargs)

    monkeypatch.setattr(kernel, "_encode_and_add_rebuild_vectors", _slow_encode)
    first_task = asyncio.create_task(kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=1))
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        second = await kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=1)
        assert second["success"] is False
        assert second["error"] == "vector_rebuild_running"
    finally:
        release.set()

    first = await first_task
    assert first["success"] is True

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_initialize_dimension_mismatch_keeps_vector_store_empty_until_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "a_memorix_data"
    first_embedding_manager = _FakeEmbeddingManager(dimension=8)
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: first_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    first_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_first",
        config=_kernel_config(data_dir, first_embedding_manager.default_dimension),
    )
    await first_kernel.initialize()
    assert first_kernel.vector_store is not None
    first_kernel.vector_store.add(
        np.ones((1, first_embedding_manager.default_dimension), dtype=np.float32),
        ["old-dimension-vector"],
    )
    first_kernel.vector_store.save()
    await first_kernel.shutdown()

    second_embedding_manager = _FakeEmbeddingManager(dimension=12)
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: second_embedding_manager,
    )

    second_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_second",
        config=_kernel_config(data_dir, second_embedding_manager.default_dimension),
    )
    await second_kernel.initialize()
    assert second_kernel.vector_store is not None
    assert second_kernel.vector_store.dimension == first_embedding_manager.default_dimension
    assert second_kernel.vector_store.num_vectors == 1
    assert "old-dimension-vector" in second_kernel.vector_store

    config = await second_kernel.memory_runtime_admin(action="get_config")
    assert config["vector_rebuild_required"] is True
    assert config["embedding_fingerprint_status"] == "mismatched"

    recover = await second_kernel.memory_runtime_admin(action="recover_embedding")
    assert recover["success"] is False
    assert recover["detail"] == "dimension_mismatch"

    config = await second_kernel.memory_runtime_admin(action="get_config")
    assert config["embedding_dimension"] == second_embedding_manager.default_dimension
    assert config["vector_rebuild_required"] is True
    assert config["stored_vector_dimension"] == first_embedding_manager.default_dimension
    assert config["embedding_degraded"] is True

    await second_kernel.shutdown()

    third_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_third",
        config=_kernel_config(data_dir, second_embedding_manager.default_dimension),
    )
    await third_kernel.initialize()
    assert third_kernel.vector_store is not None
    assert third_kernel.vector_store.dimension == first_embedding_manager.default_dimension

    config = await third_kernel.memory_runtime_admin(action="get_config")
    assert config["vector_rebuild_required"] is True
    assert config["embedding_fingerprint_status"] == "mismatched"

    recover = await third_kernel.memory_runtime_admin(action="recover_embedding")
    assert recover["detail"] == "dimension_mismatch"

    result = await third_kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=2)
    assert result["success"] is True

    await third_kernel.shutdown()

    fourth_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_fourth",
        config=_kernel_config(data_dir, second_embedding_manager.default_dimension),
    )
    await fourth_kernel.initialize()

    config = await fourth_kernel.memory_runtime_admin(action="get_config")
    assert config["vector_rebuild_required"] is False
    assert config["embedding_fingerprint_status"] == "matched"
    assert config["stored_vector_dimension"] == second_embedding_manager.default_dimension

    await fourth_kernel.shutdown()


@pytest.mark.asyncio
async def test_rebuild_all_vectors_detects_new_embedding_dimension_without_recover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "a_memorix_data"
    first_embedding_manager = _FakeEmbeddingManager(dimension=8)
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: first_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    first_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_first",
        config=_kernel_config(data_dir, first_embedding_manager.default_dimension),
    )
    await first_kernel.initialize()
    assert first_kernel.metadata_store is not None
    paragraph_hash = first_kernel.metadata_store.add_paragraph("用户喜欢蓝色围巾", source="test")
    assert first_kernel.vector_store is not None
    first_kernel.vector_store.add(
        np.ones((1, first_embedding_manager.default_dimension), dtype=np.float32),
        [paragraph_hash],
    )
    first_kernel.vector_store.save()
    await first_kernel.shutdown()

    second_embedding_manager = _FakeEmbeddingManager(dimension=12)
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: second_embedding_manager,
    )

    second_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_second",
        config=_kernel_config(data_dir, second_embedding_manager.default_dimension),
    )
    await second_kernel.initialize()
    try:
        assert second_kernel.vector_store is not None
        assert second_kernel.vector_store.dimension == first_embedding_manager.default_dimension

        result = await second_kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=2)

        assert result["success"] is True
        assert result["failed"] == 0
        assert result["embedding_dimension"] == second_embedding_manager.default_dimension
        assert result["stored_vector_dimension"] == second_embedding_manager.default_dimension
        assert second_kernel.vector_store.dimension == second_embedding_manager.default_dimension
        assert paragraph_hash in second_kernel.vector_store
    finally:
        await second_kernel.shutdown()


@pytest.mark.asyncio
async def test_dual_rebuild_detects_new_embedding_dimension_without_recover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "a_memorix_data"
    first_embedding_manager = _FakeEmbeddingManager(dimension=8, model_name="fake-embedding-a")
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: first_embedding_manager)
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    first_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_first",
        config=_dual_kernel_config(data_dir, first_embedding_manager.default_dimension),
    )
    await first_kernel.initialize()
    try:
        assert first_kernel.metadata_store is not None
        paragraph_hash = first_kernel.metadata_store.add_paragraph("双池换维度段落", source="test")
        entity_hash = first_kernel.metadata_store.add_entity("双池换维度实体")
        result = await first_kernel.memory_runtime_admin(
            action="rebuild_all_vectors",
            batch_size=2,
            include_relations=False,
        )
        assert result["success"] is True
        assert first_kernel._dual_vector_pools_enabled() is True
    finally:
        await first_kernel.shutdown()

    second_embedding_manager = _FakeEmbeddingManager(dimension=12, model_name="fake-embedding-b")
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: second_embedding_manager)

    second_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_second",
        config=_dual_kernel_config(data_dir, second_embedding_manager.default_dimension),
    )
    await second_kernel.initialize()
    try:
        config = await second_kernel.memory_runtime_admin(action="get_config")
        assert config["stored_vector_dimension"] == first_embedding_manager.default_dimension
        assert config["embedding_dimension"] == second_embedding_manager.default_dimension
        assert config["vector_rebuild_required"] is True

        preview = await second_kernel.memory_runtime_admin(action="rebuild_all_vectors", dry_run=True)
        assert preview["embedding_dimension"] == second_embedding_manager.default_dimension
        assert preview["stored_vector_dimension"] == first_embedding_manager.default_dimension

        result = await second_kernel.memory_runtime_admin(
            action="rebuild_all_vectors",
            batch_size=2,
            include_relations=False,
        )

        assert result["success"] is True
        assert result["failed"] == 0
        assert result["migration"]["paragraphs"]["copied"] == 0
        assert result["migration"]["paragraphs"]["encoded"] == 1
        assert result["embedding_dimension"] == second_embedding_manager.default_dimension
        assert result["stored_vector_dimension"] == second_embedding_manager.default_dimension
        assert second_kernel.vector_store is not None
        assert second_kernel.vector_store.dimension == second_embedding_manager.default_dimension
        assert second_kernel.paragraph_vector_store is not None
        assert second_kernel.graph_vector_store is not None
        assert second_kernel.paragraph_vector_store.dimension == second_embedding_manager.default_dimension
        assert second_kernel.graph_vector_store.dimension == second_embedding_manager.default_dimension
        assert paragraph_hash in second_kernel.paragraph_vector_store
        assert f"entity:{entity_hash}" in second_kernel.graph_vector_store
        manifest = json.loads((data_dir / "vectors" / "dual_ready.json").read_text(encoding="utf-8"))
        assert manifest["dimension"] == second_embedding_manager.default_dimension
    finally:
        await second_kernel.shutdown()


@pytest.mark.asyncio
async def test_plain_vector_store_save_preserves_existing_embedding_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: fake_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await kernel.initialize()
    try:
        assert kernel.vector_store is not None
        kernel.vector_store.add(
            np.ones((1, fake_embedding_manager.default_dimension), dtype=np.float32),
            ["fingerprint-preserved"],
        )
        kernel._save_vector_store(kernel.vector_store)

        meta_path = data_dir / "vectors" / "vectors_metadata.json"
        first_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        first_fingerprint = dict(first_meta["embedding_fingerprint"])

        kernel.vector_store.save()

        second_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert second_meta["embedding_fingerprint"] == first_fingerprint

        config = await kernel.memory_runtime_admin(action="get_config")
        assert config["embedding_fingerprint_status"] == "matched"
        assert config["vector_rebuild_required"] is False
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_runtime_auto_stamps_missing_embedding_fingerprint_when_dimension_matches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: fake_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await kernel.initialize()
    try:
        assert kernel.vector_store is not None
        kernel.vector_store.add(
            np.ones((1, fake_embedding_manager.default_dimension), dtype=np.float32),
            ["missing-fingerprint"],
        )
        kernel.vector_store.save()
        meta_path = data_dir / "vectors" / "vectors_metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.pop("embedding_fingerprint", None)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, sort_keys=True), encoding="utf-8")

        assert kernel._stored_vectors_compatible_with_current_embedding(kernel.vector_store) is True

        config = await kernel.memory_runtime_admin(action="get_config")
        assert config["embedding_fingerprint_status"] == "matched"
        assert config["stored_embedding_fingerprint"]["hash"] == config["embedding_fingerprint"]["hash"]
        assert config["vector_rebuild_required"] is False
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_runtime_does_not_auto_stamp_missing_embedding_fingerprint_when_dimension_mismatches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: fake_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await kernel.initialize()
    try:
        assert kernel.vector_store is not None
        kernel.vector_store.add(
            np.ones((1, fake_embedding_manager.default_dimension), dtype=np.float32),
            ["dimension-mismatch"],
        )
        kernel.vector_store.save()
        meta_path = data_dir / "vectors" / "vectors_metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.pop("embedding_fingerprint", None)
        meta["dimension"] = fake_embedding_manager.default_dimension + 1
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, sort_keys=True), encoding="utf-8")

        assert kernel._stored_vectors_compatible_with_current_embedding(kernel.vector_store) is False

        config = await kernel.memory_runtime_admin(action="get_config")
        assert config["vector_rebuild_required"] is True
        assert config["embedding_fingerprint_status"] == "missing"
        assert config["stored_vector_dimension"] == fake_embedding_manager.default_dimension + 1
        assert config["embedding_dimension"] == fake_embedding_manager.default_dimension
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_dual_ready_manifest_rejects_mismatched_embedding_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_embedding_manager = _FakeEmbeddingManager(dimension=8, model_name="fake-embedding-a")
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: first_embedding_manager)
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    first_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_first",
        config=_dual_kernel_config(data_dir, first_embedding_manager.default_dimension),
    )
    await first_kernel.initialize()
    try:
        assert first_kernel.metadata_store is not None
        first_kernel.metadata_store.add_paragraph("双池旧指纹段落", source="test")
        first_kernel.metadata_store.add_entity("双池旧指纹实体")
        result = await first_kernel.memory_runtime_admin(
            action="rebuild_all_vectors",
            batch_size=2,
            include_relations=False,
        )
        assert result["success"] is True
        assert first_kernel._dual_vector_pools_enabled() is True
    finally:
        await first_kernel.shutdown()

    second_embedding_manager = _FakeEmbeddingManager(dimension=8, model_name="fake-embedding-b")
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: second_embedding_manager)
    second_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_second",
        config=_dual_kernel_config(data_dir, second_embedding_manager.default_dimension),
    )
    await second_kernel.initialize()
    try:
        assert second_kernel._dual_vector_pools_enabled() is False
        assert second_kernel.retriever.config.vector_pools.mode == "single"
        config = await second_kernel.memory_runtime_admin(action="get_config")
        assert config["vector_pools"]["configured_mode"] == "dual"
        assert config["vector_pools_effective_mode"] == "single"
        assert config["embedding_fingerprint_status"] == "mismatched"
        assert config["vector_rebuild_required"] is True
    finally:
        await second_kernel.shutdown()


@pytest.mark.asyncio
async def test_initialize_defers_real_embedding_self_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    self_check_calls: list[dict[str, Any]] = []

    async def _recording_runtime_self_check(**kwargs: Any) -> dict[str, Any]:
        self_check_calls.append(kwargs)
        return await _fake_runtime_self_check(**kwargs)

    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: fake_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _recording_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )

    await kernel.initialize()
    try:
        assert fake_embedding_manager.encode_calls == []
        assert fake_embedding_manager.detect_calls == 0
        assert self_check_calls == []
        report = kernel._runtime_facade._runtime_self_check_report
        assert report["code"] == "startup_self_check_deferred"

        result = await kernel.memory_runtime_admin(action="self_check")
        assert result["success"] is True
        assert len(self_check_calls) == 1
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_deferred_self_check_marks_dimension_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=12)
    data_dir = tmp_path / "a_memorix_data"

    async def _dimension_mismatch_self_check(**kwargs: Any) -> dict[str, Any]:
        vector_store = kwargs["vector_store"]
        return {
            "ok": False,
            "message": "dimension mismatch",
            "configured_dimension": 12,
            "requested_dimension": 12,
            "vector_store_dimension": int(vector_store.dimension),
            "detected_dimension": 12,
            "encoded_dimension": 12,
            "elapsed_ms": 0.0,
            "sample_text": "test",
            "checked_at": 1_777_000_000.0,
        }

    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: fake_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _dimension_mismatch_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, 8),
    )

    await kernel.initialize()
    try:
        assert kernel.vector_store is not None
        assert kernel.vector_store.dimension == 8
        assert kernel._is_embedding_degraded() is False

        result = await kernel.memory_runtime_admin(action="self_check")
        assert result["success"] is False

        config = await kernel.memory_runtime_admin(action="get_config")
        assert config["embedding_dimension"] == 12
        assert config["vector_rebuild_required"] is True
        assert config["stored_vector_dimension"] == 8
        assert config["embedding_degraded"] is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_runtime_config_requires_rebuild_when_embedding_model_fingerprint_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_embedding_manager = _FakeEmbeddingManager(dimension=8, model_name="fake-embedding-a")
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: first_embedding_manager,
    )
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    first_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_first",
        config=_kernel_config(data_dir, first_embedding_manager.default_dimension),
    )
    await first_kernel.initialize()
    assert first_kernel.metadata_store is not None
    first_kernel.metadata_store.add_paragraph("用户喜欢蓝色围巾", source="test")
    result = await first_kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=2)
    assert result["success"] is True
    first_config = await first_kernel.memory_runtime_admin(action="get_config")
    assert first_config["embedding_fingerprint_status"] == "matched"
    await first_kernel.shutdown()

    second_embedding_manager = _FakeEmbeddingManager(dimension=8, model_name="fake-embedding-b")
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: second_embedding_manager,
    )

    second_kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_second",
        config=_kernel_config(data_dir, second_embedding_manager.default_dimension),
    )
    await second_kernel.initialize()
    try:
        config = await second_kernel.memory_runtime_admin(action="get_config")
        assert config["stored_vector_dimension"] == second_embedding_manager.default_dimension
        assert config["embedding_dimension"] == second_embedding_manager.default_dimension
        assert config["embedding_fingerprint_status"] == "mismatched"
        assert config["vector_rebuild_required"] is True
        assert "模型指纹" in config["vector_rebuild_message"]
    finally:
        await second_kernel.shutdown()


# ── 风险验证测试 ──


@pytest.mark.asyncio
async def test_dual_migration_cleans_legacy_single_pool_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """双池迁移完成后清理旧单池文件，避免三池长期冗余。"""
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: fake_embedding_manager)
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await kernel.initialize()
    assert kernel.metadata_store is not None

    ph = kernel.metadata_store.add_paragraph("旧池向量段落", source="test")
    eh = kernel.metadata_store.add_entity("旧池实体")
    rh = kernel.metadata_store.add_relation("用户", "喜欢", "旧池关系")

    kernel.vector_store.add(
        np.eye(3, fake_embedding_manager.default_dimension, dtype=np.float32), [ph, eh, rh]
    )
    kernel.vector_store.save()

    result = await kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=2)
    assert result["success"] is True

    assert not kernel.vector_store.has_data()
    assert kernel.paragraph_vector_store.has_data()
    assert kernel.graph_vector_store.has_data()
    assert not (data_dir / "vectors" / "vectors.bin").exists()
    assert not (data_dir / "vectors" / "vectors_ids.bin").exists()
    assert not (data_dir / "vectors" / "vectors_metadata.json").exists()
    assert not (data_dir / "vectors" / "vectors_metadata.pkl").exists()
    assert (data_dir / "vectors" / "dual_ready.json").exists()

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_dual_ready_manifest_recovers_when_deleted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: fake_embedding_manager)
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_dual_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await kernel.initialize()
    assert kernel.metadata_store is not None
    kernel.metadata_store.add_paragraph("manifest 自愈段落", source="test")
    kernel.metadata_store.add_entity("manifest 自愈实体")
    result = await kernel.memory_runtime_admin(action="rebuild_all_vectors", batch_size=2, include_relations=False)
    assert result["success"] is True
    await kernel.shutdown()

    manifest_path = data_dir / "vectors" / "dual_ready.json"
    manifest_path.unlink()

    reloaded = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root_reloaded",
        config=_dual_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await reloaded.initialize()
    assert reloaded._dual_vector_pools_enabled() is True
    assert reloaded.retriever.config.vector_pools.mode == "dual"
    assert manifest_path.exists()

    await reloaded.shutdown()


@pytest.mark.asyncio
async def test_get_vectors_returns_correct_subset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """验证风险 #3：get_vectors 正确性和边界行为。"""
    fake_embedding_manager = _FakeEmbeddingManager(dimension=8)
    data_dir = tmp_path / "a_memorix_data"
    monkeypatch.setattr(kernel_module, "create_embedding_api_adapter", lambda **kw: fake_embedding_manager)
    monkeypatch.setattr(kernel_module, "run_embedding_runtime_self_check", _fake_runtime_self_check)

    kernel = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, fake_embedding_manager.default_dimension),
    )
    await kernel.initialize()
    assert kernel.vector_store is not None

    vectors = np.eye(4, fake_embedding_manager.default_dimension, dtype=np.float32)
    kernel.vector_store.add(vectors, ["a", "b", "c", "d"])
    kernel.vector_store.save()

    result = kernel.vector_store.get_vectors(["a", "c", "x"])
    assert len(result) == 2
    assert "a" in result
    assert "c" in result
    assert "x" not in result
    assert result["a"].dtype == np.float32

    assert kernel.vector_store.get_vectors([]) == {}
    assert kernel.vector_store.get_vectors(["z", "y"]) == {}
    batches = list(kernel.vector_store.iter_vectors_by_ids(["a", "b", "c"], batch_size=1))
    assert [list(batch.keys()) for batch in batches] == [["a"], ["b"], ["c"]]

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_filter_current_effective_hits_expired_paragraph(tmp_path: Path) -> None:
    """验证风险 #2：_filter_current_effective_hits 对过期段落生效，无配置开关控制。"""
    from src.A_memorix.core.runtime import sdk_memory_kernel

    kernel = sdk_memory_kernel.SDKMemoryKernel.__new__(sdk_memory_kernel.SDKMemoryKernel)
    kernel.metadata_store = None

    hits = [
        {"hash": "p-active", "type": "paragraph", "content": "", "metadata": {}},
        {"hash": "p-expired", "type": "paragraph", "content": "",
         "metadata": {"memory_change": {"valid_to": 1.0, "change_type": "superseded"}}},
    ]
    filtered = kernel._filter_current_effective_hits(hits)
    assert len(filtered) == 1
    assert filtered[0]["hash"] == "p-active"


@pytest.mark.asyncio
async def test_filter_current_effective_hits_all_expired(tmp_path: Path) -> None:
    """验证过期的 paragraph + relation 全部被过滤。"""
    from src.A_memorix.core.runtime import sdk_memory_kernel

    kernel = sdk_memory_kernel.SDKMemoryKernel.__new__(sdk_memory_kernel.SDKMemoryKernel)
    kernel.metadata_store = None
    hits = [
        {"hash": "p1", "type": "paragraph", "content": "", "metadata": {"memory_change": {"valid_to": 1.0}}},
        {"hash": "r1", "type": "relation", "content": "", "metadata": {"memory_change": {"valid_to": 1.0}}},
    ]
    assert kernel._filter_current_effective_hits(hits) == []


@pytest.mark.asyncio
async def test_filter_current_effective_hits_keeps_valid_to_null_or_future(tmp_path: Path) -> None:
    """验证 valid_to=None 或未来时间正确保留。"""
    from src.A_memorix.core.runtime import sdk_memory_kernel
    import time

    kernel = sdk_memory_kernel.SDKMemoryKernel.__new__(sdk_memory_kernel.SDKMemoryKernel)
    kernel.metadata_store = None
    future = time.time() + 86400
    hits = [
        {"hash": "p1", "type": "paragraph", "content": "", "metadata": {}},
        {"hash": "p2", "type": "paragraph", "content": "", "metadata": {"memory_change": {"valid_to": None}}},
        {"hash": "p3", "type": "paragraph", "content": "", "metadata": {"memory_change": {"valid_to": future}}},
    ]
    assert len(kernel._filter_current_effective_hits(hits)) == 3


@pytest.mark.asyncio
async def test_filter_current_effective_hits_skips_store_when_no_fuzzy_changes(tmp_path: Path) -> None:
    """未执行过模糊修改时，常规检索不回表查询当前有效性。"""
    from unittest.mock import MagicMock

    from src.A_memorix.core.runtime import sdk_memory_kernel

    kernel = sdk_memory_kernel.SDKMemoryKernel.__new__(sdk_memory_kernel.SDKMemoryKernel)
    fake_store = MagicMock()
    fake_store.list_fuzzy_modify_plans.return_value = []
    kernel.metadata_store = fake_store
    kernel._current_effective_filter_cache = {"checked_at": 0.0, "needed": False}

    hits = [
        {"hash": "p-stored", "type": "paragraph", "content": "", "metadata": {}},
        {"hash": "r-stored", "type": "relation", "content": "", "metadata": {}},
    ]
    filtered = kernel._filter_current_effective_hits(hits)
    assert [item["hash"] for item in filtered] == ["p-stored", "r-stored"]
    fake_store.get_paragraphs_by_hashes.assert_not_called()
    fake_store.get_relations_by_hashes.assert_not_called()


@pytest.mark.asyncio
async def test_filter_current_effective_hits_uses_stored_metadata_after_fuzzy_change(tmp_path: Path) -> None:
    """执行过模糊修改后，从库中批量读取最新 metadata。"""
    from unittest.mock import MagicMock

    from src.A_memorix.core.runtime import sdk_memory_kernel

    kernel = sdk_memory_kernel.SDKMemoryKernel.__new__(sdk_memory_kernel.SDKMemoryKernel)
    fake_store = MagicMock()
    fake_store.list_fuzzy_modify_plans.return_value = [{"plan_id": "fuzzy-1"}]
    fake_store.get_paragraphs_by_hashes.return_value = {
        "p-stored": {
            "hash": "p-stored",
            "content": "",
            "metadata": {"memory_change": {"valid_to": 1.0}},
        }
    }
    fake_store.get_relations_by_hashes.return_value = {
        "r-stored": {
            "hash": "r-stored",
            "metadata": {"memory_change": {"valid_to": 1.0}},
        }
    }
    kernel.metadata_store = fake_store
    kernel._current_effective_filter_cache = {"checked_at": 0.0, "needed": False}

    hits = [
        {"hash": "p-stored", "type": "paragraph", "content": "", "metadata": {}},
        {"hash": "r-stored", "type": "relation", "content": "", "metadata": {}},
    ]
    assert kernel._filter_current_effective_hits(hits) == []
