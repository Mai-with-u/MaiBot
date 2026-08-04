from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import hashlib
import json
import pickle
import shutil
import sqlite3

import numpy as np
import pytest

from src.A_memorix.core.runtime import sdk_memory_kernel as kernel_module
from src.A_memorix.core.runtime.models import KernelSearchRequest
from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.storage.format_migration import FORMAT_MIGRATION_VERSION
from src.A_memorix.core.storage.graph_store import GraphStore
from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.storage.vector_store import VectorStore


class _FakeEmbeddingManager:
    def __init__(self, dimension: int) -> None:
        self.default_dimension = dimension
        self.model_name = "migration-runtime-test"

    async def _detect_dimension(self) -> int:
        return self.default_dimension

    async def encode(self, text: Any, **kwargs: Any) -> np.ndarray:
        del kwargs
        if isinstance(text, (list, tuple)):
            return np.stack([_vector_for(str(item), self.default_dimension) for item in text])
        return _vector_for(str(text), self.default_dimension)

    async def encode_batch(self, texts: Any, **kwargs: Any) -> np.ndarray:
        return await self.encode(texts, **kwargs)

    def get_embedding_fingerprint(self, *, dimension: Optional[int] = None) -> Dict[str, Any]:
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


def _vector_for(text: str, dimension: int) -> np.ndarray:
    vector = np.zeros(dimension, dtype=np.float32)
    for index, byte in enumerate(text.encode("utf-8")):
        vector[index % dimension] += float((byte % 17) + 1)
    norm = float(np.linalg.norm(vector))
    if norm == 0:
        vector[0] = 1.0
        return vector
    return vector / norm


def _kernel_config(data_dir: Path, *, sparse_enabled: bool) -> Dict[str, Any]:
    return {
        "storage": {"data_dir": str(data_dir.resolve())},
        "advanced": {"enable_auto_save": False},
        "embedding": {
            "dimension": 8,
            "batch_size": 2,
            "paragraph_vector_backfill": {"enabled": False},
        },
        "episode": {"enabled": False},
        "person_profile": {"enabled": False},
        "retrieval": {
            "relation_vectorization": {"enabled": False},
            "vector_pools": {"mode": "single"},
            "sparse": {"enabled": sparse_enabled},
            "enable_ppr": False,
            "enable_parallel": False,
        },
    }


def _patch_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        kernel_module,
        "create_embedding_api_adapter",
        lambda **kwargs: _FakeEmbeddingManager(int(kwargs["default_dimension"])),
    )


def _migration_record(data_dir: Path) -> Optional[Tuple[float, str]]:
    db_path = data_dir / "metadata" / "metadata.db"
    conn = sqlite3.connect(str(db_path))
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'storage_format_migrations'"
        ).fetchone()
        if table is None:
            return None
        row = conn.execute(
            "SELECT applied_at, summary_json FROM storage_format_migrations WHERE version = ?",
            (FORMAT_MIGRATION_VERSION,),
        ).fetchone()
        return (float(row[0]), str(row[1])) if row is not None else None
    finally:
        conn.close()


def _build_current_metadata(data_dir: Path, content: str) -> Tuple[str, str]:
    store = MetadataStore(data_dir=data_dir / "metadata")
    store.connect()
    paragraph_hash = store.add_paragraph(
        content=content,
        source="manual",
        metadata={"chat_id": "migration-chat", "legacy_marker": "保留"},
    )
    relation_hash = store.add_relation(
        subject="旧节点甲",
        predicate="关联",
        obj="旧节点乙",
        source_paragraph=paragraph_hash,
        metadata={"origin": "legacy"},
    )
    store.close()
    return paragraph_hash, relation_hash


def _turn_metadata_into_legacy_blob(data_dir: Path, paragraph_hash: str) -> None:
    db_path = data_dir / "metadata" / "metadata.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE paragraphs SET metadata = ? WHERE hash = ?",
            (pickle.dumps({"chat_id": "migration-chat", "legacy_marker": "保留"}), paragraph_hash),
        )
        conn.commit()
    finally:
        conn.close()


def _build_legacy_vector(data_dir: Path, paragraph_hash: str, content: str) -> None:
    vector_dir = data_dir / "vectors"
    store = VectorStore(dimension=8, data_dir=vector_dir, buffer_size=1)
    store.add(vectors=np.asarray([_vector_for(content, 8)]), ids=[paragraph_hash])
    store.save(embedding_fingerprint=_FakeEmbeddingManager(8).get_embedding_fingerprint())

    json_path = vector_dir / "vectors_metadata.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    payload.pop("binary_commit", None)
    with (vector_dir / "vectors_metadata.pkl").open("wb") as handle:
        pickle.dump(payload, handle)
    json_path.unlink()


def _build_legacy_graph(data_dir: Path, relation_hash: str) -> None:
    graph_dir = data_dir / "graph"
    store = GraphStore(data_dir=graph_dir)
    store.add_edges([("旧节点甲", "旧节点乙")], relation_hashes=[relation_hash])
    store.save()

    metadata_path = graph_dir / "graph_metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["edge_hash_map"] = {(0, 1): {relation_hash}}
    with (graph_dir / "graph_metadata.pkl").open("wb") as handle:
        pickle.dump(payload, handle)
    metadata_path.unlink()
    (graph_dir / "graph_snapshot.json").unlink()
    shutil.rmtree(graph_dir / "graph_snapshots")

    conn = sqlite3.connect(str(data_dir / "metadata" / "metadata.db"))
    try:
        conn.execute("DELETE FROM graph_edge_relation_map")
        conn.commit()
    finally:
        conn.close()


def _paragraph_contents(result: Dict[str, Any]) -> set[str]:
    return {
        str(item.get("content", ""))
        for item in result.get("hits", [])
        if str(item.get("type", "")) == "paragraph"
    }


@pytest.mark.asyncio
async def test_full_legacy_migration_remains_readable_writable_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "a_memorix_data"
    old_content = "旧版迁移后仍能检索的海棠会议记录"
    new_content = "升级后新写入的青松项目记录"
    paragraph_hash, relation_hash = _build_current_metadata(data_dir, old_content)
    _build_legacy_vector(data_dir, paragraph_hash, old_content)
    _build_legacy_graph(data_dir, relation_hash)
    _turn_metadata_into_legacy_blob(data_dir, paragraph_hash)
    _patch_embedding(monkeypatch)

    first = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, sparse_enabled=True),
    )
    await first.initialize()
    try:
        status = first._runtime_capability_status()
        assert status["runtime_ready"] is True
        assert status["capabilities"]["metadata"] is True
        assert status["capabilities"]["vector_read"] is True
        assert status["capabilities"]["graph"] is True
        assert first.metadata_store is not None
        assert first.vector_store is not None
        assert first.graph_store is not None
        assert first.metadata_store.get_paragraph(paragraph_hash)["metadata"]["legacy_marker"] == "保留"
        assert paragraph_hash in first.vector_store
        assert first.graph_store.get_edge_weight("旧节点甲", "旧节点乙") == pytest.approx(1.0)
        assert first.graph_store.get_relation_hashes_for_edge("旧节点甲", "旧节点乙") == {relation_hash}

        old_search = await first.search_memory(
            KernelSearchRequest(query=old_content, limit=5, respect_filter=False)
        )
        assert old_content in _paragraph_contents(old_search)

        write_result = await first.ingest_text(
            external_id="post-migration-write",
            source_type="manual",
            text=new_content,
            respect_filter=False,
        )
        assert len(write_result["stored_ids"]) == 1
        new_hash = write_result["stored_ids"][0]
        assert first.metadata_store.get_paragraph(new_hash)["content"] == new_content
        assert new_hash in first.vector_store
    finally:
        await first.shutdown()

    first_record = _migration_record(data_dir)
    assert first_record is not None
    assert not (data_dir / "vectors" / "vectors_metadata.pkl").exists()
    assert not (data_dir / "graph" / "graph_metadata.pkl").exists()

    second = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, sparse_enabled=True),
    )
    await second.initialize()
    try:
        assert _migration_record(data_dir) == first_record
        assert second.metadata_store is not None
        assert second.vector_store is not None
        assert second.metadata_store.count_paragraphs() == 2
        assert paragraph_hash in second.vector_store
        assert new_hash in second.vector_store
        restarted_search = await second.search_memory(
            KernelSearchRequest(query=new_content, limit=5, respect_filter=False)
        )
        assert new_content in _paragraph_contents(restarted_search)
    finally:
        await second.shutdown()


@pytest.mark.asyncio
async def test_corrupt_legacy_vector_does_not_block_new_writes_or_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "a_memorix_data"
    old_content = "损坏旧向量存在时仍可读取的元数据"
    paragraph_hash, _ = _build_current_metadata(data_dir, old_content)
    corrupt_path = data_dir / "vectors" / "vectors_metadata.pkl"
    corrupt_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_bytes = b"not-a-valid-legacy-pickle"
    corrupt_path.write_bytes(corrupt_bytes)
    _patch_embedding(monkeypatch)

    first = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, sparse_enabled=True),
    )
    await first.initialize()
    try:
        assert first.is_runtime_ready() is True
        assert first.metadata_store is not None
        assert first.metadata_store.get_paragraph(paragraph_hash)["content"] == old_content

        write_result = await first.ingest_text(
            external_id="write-beside-corrupt-legacy",
            source_type="manual",
            text="损坏旧向量旁的新写入仍然成功",
            respect_filter=False,
        )
        assert len(write_result["stored_ids"]) == 1
        new_hash = write_result["stored_ids"][0]
        assert first.metadata_store.get_paragraph(new_hash) is not None
        assert first.vector_store is not None
        assert new_hash in first.vector_store
    finally:
        await first.shutdown()

    assert corrupt_path.read_bytes() == corrupt_bytes
    assert _migration_record(data_dir) is None

    second = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, sparse_enabled=True),
    )
    await second.initialize()
    try:
        assert second.is_runtime_ready() is True
        assert second.metadata_store is not None
        assert second.vector_store is not None
        assert second.metadata_store.count_paragraphs() == 2
        assert new_hash in second.vector_store
        search_result = await second.search_memory(
            KernelSearchRequest(query="损坏旧向量旁的新写入仍然成功", limit=5, respect_filter=False)
        )
        assert "损坏旧向量旁的新写入仍然成功" in _paragraph_contents(search_result)
    finally:
        await second.shutdown()


@pytest.mark.asyncio
async def test_corrupt_current_vector_keeps_metadata_and_sparse_flow_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "a_memorix_data"
    old_content = "当前向量损坏后仍能通过稀疏通道查到的银杏资料"
    paragraph_hash, _ = _build_current_metadata(data_dir, old_content)
    vector_dir = data_dir / "vectors"
    vector_dir.mkdir(parents=True, exist_ok=True)
    corrupt_path = vector_dir / "vectors_metadata.json"
    corrupt_text = "{broken-current-vector-metadata"
    corrupt_path.write_text(corrupt_text, encoding="utf-8")
    (vector_dir / "vectors.bin").write_bytes(b"opaque-vector-data")
    (vector_dir / "vectors_ids.bin").write_bytes(b"opaque-id-data")
    _patch_embedding(monkeypatch)

    first = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, sparse_enabled=True),
    )
    await first.initialize()
    try:
        status = first._runtime_capability_status()
        assert status["runtime_ready"] is True
        assert status["retrieval_ready"] is True
        assert status["degraded"] is True
        assert status["capabilities"]["metadata"] is True
        assert status["capabilities"]["sparse"] is True
        assert status["capabilities"]["vector_read"] is False
        assert status["capabilities"]["vector_write"] is False

        old_search = await first.search_memory(
            KernelSearchRequest(query=old_content, limit=5, respect_filter=False)
        )
        assert old_content in _paragraph_contents(old_search)

        write_result = await first.ingest_text(
            external_id="metadata-write-during-vector-degradation",
            source_type="manual",
            text="向量损坏期间新增的白桦资料",
            respect_filter=False,
        )
        assert write_result["detail"] == "vector_degraded_write"
        new_hash = write_result["stored_ids"][0]
        assert first.metadata_store is not None
        assert first.metadata_store.get_paragraph(new_hash)["content"] == "向量损坏期间新增的白桦资料"
    finally:
        await first.shutdown()

    assert corrupt_path.read_text(encoding="utf-8") == corrupt_text
    assert _migration_record(data_dir) is None

    second = SDKMemoryKernel(
        plugin_root=tmp_path / "plugin_root",
        config=_kernel_config(data_dir, sparse_enabled=True),
    )
    await second.initialize()
    try:
        status = second._runtime_capability_status()
        assert status["capabilities"]["vector_read"] is False
        assert status["capabilities"]["sparse"] is True
        assert second.metadata_store is not None
        assert second.metadata_store.count_paragraphs() == 2
        restarted_search = await second.search_memory(
            KernelSearchRequest(query="向量损坏期间新增的白桦资料", limit=5, respect_filter=False)
        )
        assert "向量损坏期间新增的白桦资料" in _paragraph_contents(restarted_search)
    finally:
        await second.shutdown()
