from __future__ import annotations

from hashlib import blake2b, sha256
from pathlib import Path
from typing import Any, Dict, Sequence

import math
import unicodedata

import numpy as np
import pytest

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.storage.graph_store import HAS_SCIPY, GraphStore
from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.storage.vector_store import HAS_FAISS, VectorStore


pytestmark = pytest.mark.skipif(
    not HAS_FAISS or not HAS_SCIPY,
    reason="真实存储集成测试需要 Faiss 和 SciPy",
)

EMBEDDING_DIMENSION = 128
PARAGRAPH_TEXT = "林澈在杭州与顾遥共同维护离线知识库。"


class OfflineDeterministicEmbedding:
    """完全离线的确定性 embedding，用于驱动真实 Faiss 存储。"""

    def __init__(self, dimension: int) -> None:
        self.dimension = int(dimension)
        self.model_name = f"pytest-local-cjk-ngram-{self.dimension}"
        self._initialized = False
        self._encode_count = 0

    async def initialize(self) -> int:
        self._initialized = True
        return self.dimension

    def _encode_sync(self, text: str) -> np.ndarray:
        normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
        compact = "".join(normalized.split())
        vector = np.zeros(self.dimension, dtype=np.float32)
        for size, weight in ((1, 0.35), (2, 1.0), (3, 0.7)):
            for index in range(max(0, len(compact) - size + 1)):
                token = f"c{size}:{compact[index : index + size]}"
                digest = blake2b(token.encode("utf-8"), digest_size=16, person=b"memorix-test").digest()
                bucket = int.from_bytes(digest[:8], "little") % self.dimension
                sign = 1.0 if digest[8] & 1 else -1.0
                vector[bucket] += sign * weight
        norm = math.sqrt(float(np.dot(vector, vector)))
        if norm > 0.0:
            vector /= norm
        self._encode_count += 1
        return vector

    async def encode(self, texts: str | Sequence[str], **_: Any) -> np.ndarray:
        if not self._initialized:
            await self.initialize()
        if isinstance(texts, str):
            return self._encode_sync(texts)
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        return np.stack([self._encode_sync(text) for text in texts]).astype(np.float32, copy=False)

    async def encode_batch(self, texts: Sequence[str], **kwargs: Any) -> np.ndarray:
        return await self.encode(texts, **kwargs)

    def get_requested_dimension(self) -> int:
        return self.dimension

    def get_embedding_fingerprint(self, *, dimension: int | None = None) -> Dict[str, Any]:
        effective_dimension = int(dimension or self.dimension)
        payload = f"{self.model_name}:{effective_dimension}:offline"
        return {
            "hash": sha256(payload.encode("utf-8")).hexdigest(),
            "provider": "local",
            "model": self.model_name,
            "dimension": effective_dimension,
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "backend": "local_cjk_ngram",
            "model_name": self.model_name,
            "dimension": self.dimension,
            "encode_count": self._encode_count,
            "external_requests": 0,
        }


def _runtime_config(data_dir: Path) -> Dict[str, Any]:
    return {
        "storage": {"data_dir": str(data_dir.resolve())},
        "embedding": {
            "dimension": EMBEDDING_DIMENSION,
            "dimension_request_mode": "never",
            "enable_cache": False,
            "fallback": {
                "enabled": False,
                "allow_metadata_only_write": False,
            },
            "paragraph_vector_backfill": {"enabled": False},
        },
        "retrieval": {
            "vector_pools": {"mode": "single"},
            "relation_vectorization": {"enabled": True},
            "sparse": {"enabled": False},
        },
        "episode": {"enabled": False, "generation_enabled": False},
        "person_profile": {"enabled": False},
        "memory": {"enabled": False},
        "advanced": {"enable_auto_save": False},
    }


async def _open_runtime(data_dir: Path) -> SDKMemoryKernel:
    kernel = SDKMemoryKernel(
        plugin_root=Path.cwd(),
        config=_runtime_config(data_dir),
    )
    await kernel.initialize()
    await kernel._stop_background_tasks()

    embedding = OfflineDeterministicEmbedding(EMBEDDING_DIMENSION)
    await embedding.initialize()
    kernel.embedding_manager = embedding
    kernel.embedding_dimension = EMBEDDING_DIMENSION
    if kernel.relation_write_service is not None:
        kernel.relation_write_service.embedding_manager = embedding
    return kernel


async def _close_runtime(kernel: SDKMemoryKernel | None) -> None:
    if kernel is not None and kernel._initialized:
        await kernel.shutdown()


async def _simulate_hard_runtime_exit(kernel: SDKMemoryKernel) -> None:
    """不执行持久化，释放进程级资源以模拟操作系统完成硬退出。"""

    await kernel._stop_background_tasks()
    if kernel.import_task_manager is not None:
        await kernel.import_task_manager.shutdown()
    if kernel.retrieval_tuning_manager is not None:
        await kernel.retrieval_tuning_manager.shutdown()
    if kernel.metadata_store is not None:
        kernel.metadata_store.close()
    # 真实进程退出会自动释放 OS 文件锁并销毁全部可写对象。测试在同一
    # Python 进程内重启，必须显式复制这两个语义，不能调用正常 shutdown。
    kernel.metadata_store = None
    kernel.graph_store = None
    kernel.vector_store = None
    kernel.paragraph_vector_store = None
    kernel.graph_vector_store = None
    kernel.relation_write_service = None
    kernel._initialized = False
    kernel._runtime_writer_lock.release()


async def _seed_linked_memory(kernel: SDKMemoryKernel, *, source: str) -> Dict[str, Any]:
    metadata_store = kernel.metadata_store
    assert isinstance(metadata_store, MetadataStore)
    assert isinstance(kernel.graph_store, GraphStore)
    assert isinstance(kernel.vector_store, VectorStore)

    paragraph_hash = metadata_store.add_paragraph(
        PARAGRAPH_TEXT,
        source=source,
    )
    first_entity_hash = metadata_store.add_entity(
        "林澈",
        source_paragraph=paragraph_hash,
    )
    second_entity_hash = metadata_store.add_entity(
        "顾遥",
        source_paragraph=paragraph_hash,
    )
    relation_hash = metadata_store.add_relation(
        "林澈",
        "共同维护",
        "顾遥",
        source_paragraph=paragraph_hash,
    )
    metadata_store.upsert_external_memory_ref(
        external_id=f"real-storage:{source}",
        paragraph_hash=paragraph_hash,
        source_type="pytest-real-storage",
    )

    paragraph = metadata_store.get_paragraph(paragraph_hash)
    first_entity = metadata_store.get_entity(first_entity_hash)
    second_entity = metadata_store.get_entity(second_entity_hash)
    relation = metadata_store.get_relation(relation_hash)
    assert paragraph is not None
    assert first_entity is not None
    assert second_entity is not None
    assert relation is not None
    assert await kernel._ensure_paragraph_vector(paragraph)
    assert await kernel._ensure_entity_vector(first_entity)
    assert await kernel._ensure_entity_vector(second_entity)
    assert await kernel._ensure_relation_vector(relation)

    graph_counts = kernel._rebuild_graph_from_metadata()
    kernel._persist(force_vectors=True)
    assert graph_counts == {"node_count": 2, "edge_count": 1}

    vector_ids = [paragraph_hash, first_entity_hash, second_entity_hash, relation_hash]
    assert all(vector_id in kernel.vector_store for vector_id in vector_ids)
    assert kernel.vector_store.num_vectors == 4
    assert kernel.graph_store.get_edge_weight("林澈", "顾遥") > 0.0
    return {
        "source": source,
        "paragraph": paragraph_hash,
        "entities": [first_entity_hash, second_entity_hash],
        "relation": relation_hash,
        "vector_ids": vector_ids,
        "external_id": f"real-storage:{source}",
    }


async def _semantic_vector_hits(kernel: SDKMemoryKernel, *, limit: int = 10) -> list[str]:
    vector_store = kernel.vector_store
    embedding = kernel.embedding_manager
    assert isinstance(vector_store, VectorStore)
    query_vector = await embedding.encode(PARAGRAPH_TEXT)
    hits, scores = vector_store.search(query_vector, k=limit)
    assert len(hits) == len(scores)
    assert all(np.isfinite(score) for score in scores)
    return hits


def _mixed_selector(memory: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "paragraph_hashes": [memory["paragraph"]],
        "entity_hashes": list(memory["entities"]),
        "relation_hashes": [memory["relation"]],
    }


def _assert_fully_deleted(kernel: SDKMemoryKernel, memory: Dict[str, Any]) -> None:
    metadata_store = kernel.metadata_store
    graph_store = kernel.graph_store
    vector_store = kernel.vector_store
    assert isinstance(metadata_store, MetadataStore)
    assert isinstance(graph_store, GraphStore)
    assert isinstance(vector_store, VectorStore)

    assert metadata_store.get_paragraph(memory["paragraph"])["is_deleted"] == 1
    assert all(metadata_store.get_entity(item)["is_deleted"] == 1 for item in memory["entities"])
    assert metadata_store.get_relation(memory["relation"]) is None
    assert metadata_store.get_deleted_relation(memory["relation"]) is not None
    assert metadata_store.get_external_memory_ref(memory["external_id"]) is None
    assert all(vector_id not in vector_store for vector_id in memory["vector_ids"])
    assert vector_store.num_vectors == 0
    assert graph_store.num_nodes == 0
    assert graph_store.num_edges == 0


def _assert_fully_restored(kernel: SDKMemoryKernel, memory: Dict[str, Any]) -> None:
    metadata_store = kernel.metadata_store
    graph_store = kernel.graph_store
    vector_store = kernel.vector_store
    assert isinstance(metadata_store, MetadataStore)
    assert isinstance(graph_store, GraphStore)
    assert isinstance(vector_store, VectorStore)

    assert metadata_store.get_paragraph(memory["paragraph"])["is_deleted"] == 0
    assert all(metadata_store.get_entity(item)["is_deleted"] == 0 for item in memory["entities"])
    assert metadata_store.get_relation(memory["relation"]) is not None
    assert metadata_store.get_deleted_relation(memory["relation"]) is None
    assert metadata_store.get_external_memory_ref(memory["external_id"]) is not None
    assert all(vector_id in vector_store for vector_id in memory["vector_ids"])
    assert vector_store.num_vectors == 4
    assert graph_store.num_nodes == 2
    assert graph_store.num_edges == 1
    assert graph_store.get_edge_weight("林澈", "顾遥") > 0.0
    assert metadata_store.query(
        "SELECT paragraph_hash FROM paragraph_entities WHERE paragraph_hash = ?",
        (memory["paragraph"],),
    )
    assert metadata_store.query(
        "SELECT paragraph_hash FROM paragraph_relations WHERE paragraph_hash = ? AND relation_hash = ?",
        (memory["paragraph"], memory["relation"]),
    )


async def _delete_seeded_relation(kernel: SDKMemoryKernel, memory: Dict[str, Any]) -> None:
    result = await kernel._delete_admin_service._execute_delete_action(
        mode="relation",
        selector={"hashes": [memory["relation"]]},
        requested_by="pytest-real-storage",
        reason="prepare_relation_restore",
    )
    assert result["success"] is True
    assert result["deleted_relation_count"] == 1
    assert kernel.metadata_store.get_relation(memory["relation"]) is None
    assert kernel.metadata_store.get_deleted_relation(memory["relation"]) is not None
    assert memory["relation"] not in kernel.vector_store


@pytest.mark.asyncio
async def test_real_stores_delete_purge_restore_and_restart_round_trip(tmp_path: Path) -> None:
    """在 SQLite、Faiss、SciPy 图快照上验证完整删除和恢复闭环。"""

    data_dir = tmp_path / "real-round-trip"
    kernel: SDKMemoryKernel | None = await _open_runtime(data_dir)
    try:
        memory = await _seed_linked_memory(kernel, source="round-trip")
        result = await kernel._delete_admin_service._execute_delete_action(
            mode="mixed",
            selector=_mixed_selector(memory),
            requested_by="pytest-real-storage",
            reason="real_storage_round_trip",
        )
        operation_id = str(result["operation_id"])

        assert result["success"] is True
        assert result["deleted_paragraph_count"] == 1
        assert result["deleted_entity_count"] == 2
        assert result["deleted_relation_count"] == 1
        assert result["deleted_vector_count"] == 4
        assert result["cleanup"]["completed"] == 4
        assert result["cleanup"]["failed"] == 0
        assert kernel.metadata_store.summarize_storage_cleanup_jobs(operation_id) == {
            "completed": 4,
            "total": 4,
            "unfinished": 0,
        }
        _assert_fully_deleted(kernel, memory)
        assert await _semantic_vector_hits(kernel) == []

        connection = kernel.metadata_store.get_connection()
        connection.execute(
            "UPDATE paragraphs SET deleted_at = 0 WHERE hash = ?",
            (memory["paragraph"],),
        )
        connection.execute(
            f"UPDATE entities SET deleted_at = 0 WHERE hash IN ({','.join(['?'] * len(memory['entities']))})",
            tuple(memory["entities"]),
        )
        connection.execute(
            "UPDATE deleted_relations SET deleted_at = 0 WHERE hash = ?",
            (memory["relation"],),
        )
        connection.commit()
        purged = await kernel._delete_admin_service._purge_deleted_memory(grace_hours=0.0, limit=100)
        assert purged["purged_counts"] == {"relations": 1, "paragraphs": 1, "entities": 2}
        assert kernel.metadata_store.get_paragraph(memory["paragraph"]) is None
        assert all(kernel.metadata_store.get_entity(item) is None for item in memory["entities"])
        assert kernel.metadata_store.get_deleted_relation(memory["relation"]) is None
        relation_alias = str(memory["relation"])[:32]
        connection.execute("DELETE FROM relation_hash_aliases WHERE alias32 = ?", (relation_alias,))
        connection.commit()
        assert kernel.metadata_store.resolve_relation_hash_alias(relation_alias) == []

        operation = kernel.metadata_store.get_delete_operation(operation_id)
        assert operation is not None
        restored = await kernel._delete_admin_service._restore_delete_operation(operation)
        assert restored["success"] is True
        assert restored["restored_paragraphs"] == [memory["paragraph"]]
        assert sorted(restored["restored_entities"]) == sorted(memory["entities"])
        assert restored["restored_relations"] == [memory["relation"]]
        assert restored["cleanup"]["completed"] == 5
        assert kernel.metadata_store.resolve_relation_hash_alias(relation_alias) == [memory["relation"]]
        _assert_fully_restored(kernel, memory)
        restored_hits = await _semantic_vector_hits(kernel)
        assert len(restored_hits) == 4
        assert len(set(restored_hits)) == 4
        assert set(restored_hits) == set(memory["vector_ids"])
        assert restored_hits[0] == memory["paragraph"]
        kernel._persist(force_vectors=True)
    finally:
        await _close_runtime(kernel)
        kernel = None

    reloaded = await _open_runtime(data_dir)
    try:
        _assert_fully_restored(reloaded, memory)
        reloaded_hits = await _semantic_vector_hits(reloaded)
        assert len(reloaded_hits) == 4
        assert len(set(reloaded_hits)) == 4
        assert set(reloaded_hits) == set(memory["vector_ids"])
        assert reloaded_hits[0] == memory["paragraph"]
        operation = reloaded.metadata_store.get_delete_operation(operation_id)
        assert operation is not None
        assert operation["status"] == "restored"
        assert reloaded.metadata_store.summarize_storage_cleanup_jobs(operation_id) == {
            "completed": 9,
            "total": 9,
            "unfinished": 0,
        }
        assert reloaded.embedding_manager.stats()["external_requests"] == 0
    finally:
        await _close_runtime(reloaded)


@pytest.mark.asyncio
async def test_relation_restore_outbox_survives_hard_exit_before_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关系恢复事务提交后即使进程退出，Outbox 仍能补齐向量、图和 operation。"""

    async def leave_projection_pending(**_: Any) -> Dict[str, Any]:
        return {
            "claimed": 0,
            "completed": 0,
            "cancelled": 0,
            "failed": 0,
            "deleted_vectors": 0,
            "operations": {},
        }

    data_dir = tmp_path / "relation-restore-hard-exit"
    first: SDKMemoryKernel | None = await _open_runtime(data_dir)
    hard_exited = False
    try:
        memory = await _seed_linked_memory(first, source="relation-restore-hard-exit")
        await _delete_seeded_relation(first, memory)
        alias = str(memory["relation"])[:32]
        connection = first.metadata_store.get_connection()
        connection.execute("DELETE FROM relation_hash_aliases WHERE alias32 = ?", (alias,))
        connection.commit()
        assert first.metadata_store.resolve_relation_hash_alias(alias, include_deleted=True) == []

        with monkeypatch.context() as projection_patch:
            projection_patch.setattr(
                first._delete_admin_service,
                "_process_pending_storage_cleanup_jobs_serialized",
                leave_projection_pending,
            )
            restored = await first._v5_admin_service._restore_relation_hashes(
                [memory["relation"]],
                requested_by="pytest-real-storage",
                reason="hard_exit_boundary",
            )
        operation_id = str(restored["operation_id"])

        assert restored["success"] is False
        assert restored["status"] == "restore_pending"
        assert first.metadata_store.get_relation(memory["relation"]) is not None
        assert first.metadata_store.get_deleted_relation(memory["relation"]) is None
        assert memory["relation"] not in first.vector_store
        assert first.metadata_store.resolve_relation_hash_alias(alias) == [memory["relation"]]
        assert first.metadata_store.summarize_storage_cleanup_jobs(operation_id) == {
            "pending": 2,
            "total": 2,
            "unfinished": 2,
        }

        await _simulate_hard_runtime_exit(first)
        hard_exited = True
    finally:
        if not hard_exited:
            await _close_runtime(first)
        first = None

    restarted = await _open_runtime(data_dir)
    try:
        alias = str(memory["relation"])[:32]
        assert restarted.metadata_store.get_relation(memory["relation"]) is not None
        assert restarted.metadata_store.get_deleted_relation(memory["relation"]) is None
        assert memory["relation"] not in restarted.vector_store
        assert restarted.metadata_store.resolve_relation_hash_alias(alias) == [memory["relation"]]

        retried = await restarted._delete_admin_service.process_pending_storage_cleanup_jobs(
            operation_id=operation_id
        )

        assert retried["claimed"] == 2
        assert retried["completed"] == 2
        assert retried["failed"] == 0
        assert memory["relation"] in restarted.vector_store
        assert restarted.graph_store.get_edge_weight("林澈", "顾遥") > 0.0
        assert restarted.metadata_store.get_delete_operation(operation_id)["status"] == "restored"
    finally:
        await _close_runtime(restarted)


@pytest.mark.asyncio
async def test_relation_restore_outbox_retries_first_vector_save_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关系向量首次保存失败并硬退出后，重启只重试未完成投影并恢复一致性。"""

    data_dir = tmp_path / "relation-restore-save-retry"
    first: SDKMemoryKernel | None = await _open_runtime(data_dir)
    hard_exited = False
    try:
        memory = await _seed_linked_memory(first, source="relation-restore-save-retry")
        await _delete_seeded_relation(first, memory)
        vector_store = first.vector_store
        assert isinstance(vector_store, VectorStore)
        real_save = vector_store.save
        save_attempts = 0

        def fail_first_vector_save() -> None:
            nonlocal save_attempts
            save_attempts += 1
            if save_attempts == 1:
                raise OSError("injected relation restore vector save failure")
            real_save()

        monkeypatch.setattr(vector_store, "save", fail_first_vector_save)
        restored = await first._v5_admin_service._restore_relation_hashes(
            [memory["relation"]],
            requested_by="pytest-real-storage",
            reason="save_failure_boundary",
        )
        operation_id = str(restored["operation_id"])

        assert restored["success"] is False
        assert restored["status"] == "restore_pending"
        assert restored["cleanup"]["completed"] == 1
        assert restored["cleanup"]["failed"] == 1
        assert first.metadata_store.get_relation(memory["relation"]) is not None
        assert first.metadata_store.summarize_storage_cleanup_jobs(operation_id) == {
            "completed": 1,
            "failed": 1,
            "total": 2,
            "unfinished": 1,
        }

        await _simulate_hard_runtime_exit(first)
        hard_exited = True
    finally:
        if not hard_exited:
            await _close_runtime(first)
        first = None

    restarted = await _open_runtime(data_dir)
    try:
        assert restarted.metadata_store.get_relation(memory["relation"]) is not None
        assert restarted.metadata_store.get_deleted_relation(memory["relation"]) is None
        assert memory["relation"] not in restarted.vector_store
        connection = restarted.metadata_store.get_connection()
        connection.execute(
            "UPDATE storage_cleanup_jobs SET next_attempt_at = 0 WHERE operation_id = ? AND status = 'failed'",
            (operation_id,),
        )
        connection.commit()

        retried = await restarted._delete_admin_service.process_pending_storage_cleanup_jobs(
            operation_id=operation_id
        )

        assert retried["claimed"] == 1
        assert retried["completed"] == 1
        assert retried["failed"] == 0
        assert memory["relation"] in restarted.vector_store
        assert restarted.graph_store.get_edge_weight("林澈", "顾遥") > 0.0
        operation = restarted.metadata_store.get_delete_operation(operation_id)
        assert operation is not None
        assert operation["status"] == "restored"
        relation_jobs = [
            job
            for job in restarted.metadata_store.list_storage_cleanup_jobs(operation_id=operation_id)
            if job["action"] == "vector_upsert"
        ]
        assert len(relation_jobs) == 1
        assert relation_jobs[0]["status"] == "completed"
        assert relation_jobs[0]["attempt_count"] == 2
    finally:
        await _close_runtime(restarted)


@pytest.mark.asyncio
async def test_failed_delete_revalidation_does_not_leak_tombstone_into_later_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删除重验失权后必须回滚墓碑，后续同池补写和重载均不能把它带回磁盘。"""

    data_dir = tmp_path / "failed-delete-revalidation-isolation"
    kernel = await _open_runtime(data_dir)
    try:
        memory = await _seed_linked_memory(kernel, source="failed-delete-revalidation")
        metadata_store = kernel.metadata_store
        vector_store = kernel.vector_store
        service = kernel._delete_admin_service
        assert isinstance(metadata_store, MetadataStore)
        assert isinstance(vector_store, VectorStore)

        paragraph_item = service._snapshot_paragraph_item(memory["paragraph"])
        assert paragraph_item is not None
        delete_operation = metadata_store.create_delete_operation(
            mode="paragraph",
            selector={"hashes": [memory["paragraph"]]},
            items=[paragraph_item],
            status="pending_cleanup",
        )
        delete_operation_id = str(delete_operation["operation_id"])
        assert metadata_store.mark_as_deleted(
            [memory["paragraph"]],
            "paragraph",
            reason="revalidation_race",
        ) == 1
        metadata_store.enqueue_storage_cleanup_jobs(
            operation_id=delete_operation_id,
            jobs=[
                {
                    "resource_type": "paragraph",
                    "resource_id": "batch",
                    "action": "vector_delete",
                    "payload": {"paragraph_hashes": [memory["paragraph"]]},
                    "expected_state": {"operation_status": "pending_cleanup"},
                }
            ],
        )

        later_entity_hash = metadata_store.add_entity("稍后写入的合法实体")
        later_entity = metadata_store.get_entity(later_entity_hash)
        assert later_entity is not None
        restore_operation = metadata_store.create_delete_operation(
            mode="entity_restore",
            selector={"hash": later_entity_hash},
            items=[],
            status="restore_pending",
        )
        metadata_store.enqueue_storage_cleanup_jobs(
            operation_id=str(restore_operation["operation_id"]),
            jobs=[
                {
                    "resource_type": "entity",
                    "resource_id": later_entity_hash,
                    "action": "vector_upsert",
                    "payload": {"item": later_entity},
                    "expected_state": {"operation_status": "restore_pending"},
                }
            ],
        )

        delete_job = metadata_store.list_storage_cleanup_jobs(
            operation_id=delete_operation_id
        )[0]
        real_authorize = metadata_store.authorize_storage_cleanup_job
        delete_authorize_count = 0

        def reactivate_after_delete_authority(**kwargs: Any) -> Dict[str, Any]:
            nonlocal delete_authorize_count
            authority = real_authorize(**kwargs)
            if int(kwargs["job_id"]) == int(delete_job["job_id"]):
                delete_authorize_count += 1
                if delete_authorize_count == 1:
                    connection = metadata_store.get_connection()
                    connection.execute(
                        "UPDATE paragraphs SET is_deleted = 0, deleted_at = NULL WHERE hash = ?",
                        (memory["paragraph"],),
                    )
                    connection.commit()
            return authority

        monkeypatch.setattr(
            metadata_store,
            "authorize_storage_cleanup_job",
            reactivate_after_delete_authority,
        )
        result = await service.process_pending_storage_cleanup_jobs(limit=10)

        assert result["claimed"] == 2
        assert result["failed"] == 1
        assert result["completed"] == 1
        assert memory["paragraph"] in vector_store
        assert later_entity_hash in vector_store
        assert vector_store._generate_id(memory["paragraph"]) not in vector_store._deleted_ids
        assert await _semantic_vector_hits(kernel)
        assert memory["paragraph"] in await _semantic_vector_hits(kernel)

        reloaded = VectorStore(
            dimension=EMBEDDING_DIMENSION,
            data_dir=vector_store.data_dir,
        )
        reloaded.load()
        warmup = reloaded.warmup_index(force_train=False)

        assert warmup["ok"] is True
        assert memory["paragraph"] in reloaded
        assert later_entity_hash in reloaded
        assert reloaded._generate_id(memory["paragraph"]) not in reloaded._deleted_ids
        query_vector = await kernel.embedding_manager.encode(PARAGRAPH_TEXT)
        assert memory["paragraph"] in reloaded.search(query_vector, k=10)[0]
    finally:
        await _close_runtime(kernel)


@pytest.mark.asyncio
async def test_failed_vector_delete_save_restores_last_committed_store_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使 save 已写入错误墓碑后才抛错，checkpoint 也必须恢复上一提交。"""

    data_dir = tmp_path / "failed-delete-save-rollback"
    kernel = await _open_runtime(data_dir)
    try:
        memory = await _seed_linked_memory(kernel, source="failed-delete-save")
        metadata_store = kernel.metadata_store
        vector_store = kernel.vector_store
        service = kernel._delete_admin_service
        assert isinstance(metadata_store, MetadataStore)
        assert isinstance(vector_store, VectorStore)

        paragraph_item = service._snapshot_paragraph_item(memory["paragraph"])
        assert paragraph_item is not None
        operation = metadata_store.create_delete_operation(
            mode="paragraph",
            selector={"hashes": [memory["paragraph"]]},
            items=[paragraph_item],
            status="pending_cleanup",
        )
        operation_id = str(operation["operation_id"])
        assert metadata_store.mark_as_deleted(
            [memory["paragraph"]],
            "paragraph",
            reason="save_failure",
        ) == 1
        metadata_store.enqueue_storage_cleanup_jobs(
            operation_id=operation_id,
            jobs=[
                {
                    "resource_type": "paragraph",
                    "resource_id": "batch",
                    "action": "vector_delete",
                    "payload": {"paragraph_hashes": [memory["paragraph"]]},
                    "expected_state": {"operation_status": "pending_cleanup"},
                }
            ],
        )
        real_save = vector_store.save
        save_attempts = 0

        def fail_after_second_save(*args: Any, **kwargs: Any) -> None:
            nonlocal save_attempts
            save_attempts += 1
            real_save(*args, **kwargs)
            if save_attempts == 2:
                raise OSError("injected vector delete save failure after commit")

        monkeypatch.setattr(vector_store, "save", fail_after_second_save)
        result = await service.process_pending_storage_cleanup_jobs(
            operation_id=operation_id,
            limit=1,
        )

        assert result["failed"] == 1
        assert result["completed"] == 0
        assert result["deleted_vectors"] == 0
        assert save_attempts == 2
        assert memory["paragraph"] in vector_store
        assert vector_store._generate_id(memory["paragraph"]) not in vector_store._deleted_ids
        assert memory["paragraph"] in await _semantic_vector_hits(kernel)

        reloaded = VectorStore(
            dimension=EMBEDDING_DIMENSION,
            data_dir=vector_store.data_dir,
        )
        reloaded.load()
        reloaded.warmup_index(force_train=False)

        assert memory["paragraph"] in reloaded
        assert reloaded._generate_id(memory["paragraph"]) not in reloaded._deleted_ids
        query_vector = await kernel.embedding_manager.encode(PARAGRAPH_TEXT)
        assert memory["paragraph"] in reloaded.search(query_vector, k=10)[0]
    finally:
        await _close_runtime(kernel)


@pytest.mark.asyncio
async def test_real_outbox_retries_failed_faiss_delete_after_runtime_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模拟外部向量清理首次失败，验证持久化 Outbox 在重启后接续。"""

    data_dir = tmp_path / "real-crash-retry"
    first_kernel: SDKMemoryKernel | None = await _open_runtime(data_dir)
    try:
        memory = await _seed_linked_memory(first_kernel, source="crash-retry")
        vector_store = first_kernel.vector_store
        assert isinstance(vector_store, VectorStore)
        real_delete = vector_store.delete
        injected_failures = 0

        def fail_first_real_delete(ids: list[str]) -> int:
            nonlocal injected_failures
            if injected_failures == 0:
                injected_failures += 1
                raise OSError("injected Faiss cleanup interruption")
            return real_delete(ids)

        monkeypatch.setattr(vector_store, "delete", fail_first_real_delete)
        result = await first_kernel._delete_admin_service._execute_delete_action(
            mode="mixed",
            selector=_mixed_selector(memory),
            requested_by="pytest-real-storage",
            reason="real_storage_crash_retry",
        )
        operation_id = str(result["operation_id"])
        summary = first_kernel.metadata_store.summarize_storage_cleanup_jobs(operation_id)

        assert result["success"] is True
        assert result["cleanup"]["completed"] == 3
        assert result["cleanup"]["failed"] == 1
        assert summary == {
            "completed": 3,
            "failed": 1,
            "total": 4,
            "unfinished": 1,
        }
        assert memory["paragraph"] in vector_store
        assert all(item not in vector_store for item in [*memory["entities"], memory["relation"]])
        assert vector_store.num_vectors == 1
        assert memory["paragraph"] in await _semantic_vector_hits(first_kernel)
        assert first_kernel.graph_store.num_nodes == 0
        assert first_kernel.graph_store.num_edges == 0
    finally:
        await _close_runtime(first_kernel)
        first_kernel = None

    restarted = await _open_runtime(data_dir)
    try:
        assert memory["paragraph"] in restarted.vector_store
        connection = restarted.metadata_store.get_connection()
        connection.execute(
            "UPDATE storage_cleanup_jobs SET next_attempt_at = 0 WHERE operation_id = ? AND status = 'failed'",
            (operation_id,),
        )
        connection.commit()

        retried = await restarted._delete_admin_service.process_pending_storage_cleanup_jobs(
            operation_id=operation_id,
        )
        assert retried["claimed"] == 1
        assert retried["completed"] == 1
        assert retried["failed"] == 0
        assert retried["deleted_vectors"] == 1
        _assert_fully_deleted(restarted, memory)
        assert await _semantic_vector_hits(restarted) == []

        jobs = restarted.metadata_store.list_storage_cleanup_jobs(operation_id=operation_id, limit=20)
        paragraph_jobs = [
            job
            for job in jobs
            if job["action"] == "vector_delete" and job["resource_type"] == "paragraph"
        ]
        assert len(paragraph_jobs) == 1
        assert paragraph_jobs[0]["status"] == "completed"
        assert paragraph_jobs[0]["attempt_count"] == 2
        assert restarted.metadata_store.get_delete_operation(operation_id)["status"] == "completed"
        assert restarted.embedding_manager.stats()["external_requests"] == 0
        restarted._persist(force_vectors=True)
    finally:
        await _close_runtime(restarted)

    verified = await _open_runtime(data_dir)
    try:
        _assert_fully_deleted(verified, memory)
        assert await _semantic_vector_hits(verified) == []
        assert verified.metadata_store.summarize_storage_cleanup_jobs(operation_id)["unfinished"] == 0
    finally:
        await _close_runtime(verified)


@pytest.mark.asyncio
async def test_real_outbox_rebuilds_stale_graph_snapshot_after_hard_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """图快照落盘失败时，重启后的 Outbox 必须消除旧快照。"""

    data_dir = tmp_path / "real-graph-retry"
    first_kernel: SDKMemoryKernel | None = await _open_runtime(data_dir)
    hard_exited = False
    try:
        memory = await _seed_linked_memory(first_kernel, source="graph-retry")
        graph_store = first_kernel.graph_store
        assert isinstance(graph_store, GraphStore)
        real_save = graph_store.save
        injected_failures = 0

        def fail_first_graph_snapshot(data_dir: Path | None = None) -> None:
            nonlocal injected_failures
            if injected_failures == 0:
                injected_failures += 1
                raise OSError("injected graph snapshot interruption")
            real_save(data_dir)

        monkeypatch.setattr(graph_store, "save", fail_first_graph_snapshot)
        result = await first_kernel._delete_admin_service._execute_delete_action(
            mode="mixed",
            selector=_mixed_selector(memory),
            requested_by="pytest-real-storage",
            reason="real_graph_crash_retry",
        )
        operation_id = str(result["operation_id"])

        assert result["success"] is True
        assert result["cleanup"]["completed"] == 3
        assert result["cleanup"]["failed"] == 1
        assert result["deleted_vector_count"] == 4
        assert first_kernel.graph_store.num_nodes == 0
        assert first_kernel.graph_store.num_edges == 0
        assert all(vector_id not in first_kernel.vector_store for vector_id in memory["vector_ids"])
        assert first_kernel.metadata_store.summarize_storage_cleanup_jobs(operation_id) == {
            "completed": 3,
            "failed": 1,
            "total": 4,
            "unfinished": 1,
        }

        disk_graph = GraphStore(data_dir=data_dir / "graph")
        disk_graph.load()
        assert disk_graph.num_nodes == 2
        assert disk_graph.num_edges == 1
        assert disk_graph.get_edge_weight("林澈", "顾遥") > 0.0

        await _simulate_hard_runtime_exit(first_kernel)
        hard_exited = True
    finally:
        if not hard_exited:
            await _close_runtime(first_kernel)
        first_kernel = None

    restarted = await _open_runtime(data_dir)
    try:
        # 启动屏障以 SQLite metadata 为权威，开放检索前已消除磁盘旧图；
        # Outbox 任务仍保留失败状态和重试审计信息。
        assert restarted.graph_store.num_nodes == 0
        assert restarted.graph_store.num_edges == 0
        assert restarted.metadata_store.get_paragraph(memory["paragraph"])["is_deleted"] == 1
        assert all(vector_id not in restarted.vector_store for vector_id in memory["vector_ids"])

        connection = restarted.metadata_store.get_connection()
        connection.execute(
            "UPDATE storage_cleanup_jobs SET next_attempt_at = 0 WHERE operation_id = ? AND status = 'failed'",
            (operation_id,),
        )
        connection.commit()
        retried = await restarted._delete_admin_service.process_pending_storage_cleanup_jobs(
            operation_id=operation_id,
        )

        assert retried["claimed"] == 1
        assert retried["completed"] == 1
        assert retried["failed"] == 0
        assert retried["deleted_vectors"] == 0
        _assert_fully_deleted(restarted, memory)
        graph_jobs = [
            job
            for job in restarted.metadata_store.list_storage_cleanup_jobs(operation_id=operation_id, limit=20)
            if job["action"] == "graph_rebuild"
        ]
        assert len(graph_jobs) == 1
        assert graph_jobs[0]["status"] == "completed"
        assert graph_jobs[0]["attempt_count"] == 2
        restarted._persist(force_vectors=True)
    finally:
        await _close_runtime(restarted)

    verified = await _open_runtime(data_dir)
    try:
        _assert_fully_deleted(verified, memory)
        assert verified.metadata_store.summarize_storage_cleanup_jobs(operation_id) == {
            "completed": 4,
            "total": 4,
            "unfinished": 0,
        }
    finally:
        await _close_runtime(verified)


@pytest.mark.asyncio
async def test_completed_vector_jobs_are_durable_before_graph_job_across_hard_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """向量任务一旦完成，即使图任务前硬退出也不得丢失删除或恢复状态。"""

    async def leave_cleanup_pending(**_: Any) -> Dict[str, Any]:
        return {
            "claimed": 0,
            "completed": 0,
            "cancelled": 0,
            "failed": 0,
            "deleted_vectors": 0,
            "operations": {},
        }

    data_dir = tmp_path / "vector-job-durability"
    first_kernel: SDKMemoryKernel | None = await _open_runtime(data_dir)
    first_hard_exited = False
    try:
        memory = await _seed_linked_memory(first_kernel, source="vector-job-durability")
        with monkeypatch.context() as cleanup_patch:
            cleanup_patch.setattr(
                first_kernel._delete_admin_service,
                "_process_pending_storage_cleanup_jobs_serialized",
                leave_cleanup_pending,
            )
            delete_result = await first_kernel._delete_admin_service._execute_delete_action(
                mode="mixed",
                selector=_mixed_selector(memory),
                requested_by="pytest-real-storage",
                reason="vector_delete_barrier",
            )
        operation_id = str(delete_result["operation_id"])

        assert first_kernel.metadata_store.summarize_storage_cleanup_jobs(operation_id) == {
            "pending": 4,
            "total": 4,
            "unfinished": 4,
        }
        assert all(vector_id in first_kernel.vector_store for vector_id in memory["vector_ids"])
        assert first_kernel.graph_store.num_nodes == 2
        assert first_kernel.graph_store.num_edges == 1

        one_delete = await first_kernel._delete_admin_service.process_pending_storage_cleanup_jobs(
            operation_id=operation_id,
            limit=1,
        )
        assert one_delete == {
            "claimed": 1,
            "completed": 1,
            "cancelled": 0,
            "failed": 0,
            "deleted_vectors": 1,
            "operations": {
                operation_id: {
                    "completed": 1,
                    "pending": 3,
                    "total": 4,
                    "unfinished": 3,
                }
            },
        }
        assert memory["paragraph"] not in first_kernel.vector_store
        assert all(
            vector_id in first_kernel.vector_store
            for vector_id in [*memory["entities"], memory["relation"]]
        )
        assert first_kernel.graph_store.num_nodes == 2
        assert first_kernel.graph_store.num_edges == 1

        await _simulate_hard_runtime_exit(first_kernel)
        first_hard_exited = True
    finally:
        if not first_hard_exited:
            await _close_runtime(first_kernel)
        first_kernel = None

    delete_restarted: SDKMemoryKernel | None = await _open_runtime(data_dir)
    second_hard_exited = False
    try:
        assert memory["paragraph"] not in delete_restarted.vector_store
        assert all(
            vector_id in delete_restarted.vector_store
            for vector_id in [*memory["entities"], memory["relation"]]
        )
        assert delete_restarted.vector_store.num_vectors == 3
        assert delete_restarted.graph_store.num_nodes == 0
        assert delete_restarted.graph_store.num_edges == 0

        remaining_delete = await delete_restarted._delete_admin_service.process_pending_storage_cleanup_jobs(
            operation_id=operation_id,
            limit=10,
        )
        assert remaining_delete["claimed"] == 3
        assert remaining_delete["completed"] == 3
        assert remaining_delete["failed"] == 0
        assert remaining_delete["deleted_vectors"] == 3
        _assert_fully_deleted(delete_restarted, memory)
        assert delete_restarted.metadata_store.summarize_storage_cleanup_jobs(operation_id) == {
            "completed": 4,
            "total": 4,
            "unfinished": 0,
        }

        operation = delete_restarted.metadata_store.get_delete_operation(operation_id)
        assert operation is not None
        with monkeypatch.context() as cleanup_patch:
            cleanup_patch.setattr(
                delete_restarted._delete_admin_service,
                "_process_pending_storage_cleanup_jobs_serialized",
                leave_cleanup_pending,
            )
            restore_result = await delete_restarted._delete_admin_service._restore_delete_operation(operation)
        assert restore_result["success"] is False
        assert delete_restarted.metadata_store.get_delete_operation(operation_id)["status"] == "restore_pending"
        assert delete_restarted.metadata_store.summarize_storage_cleanup_jobs(operation_id) == {
            "completed": 4,
            "pending": 5,
            "total": 9,
            "unfinished": 5,
        }
        assert all(vector_id not in delete_restarted.vector_store for vector_id in memory["vector_ids"])
        assert delete_restarted.graph_store.num_nodes == 0
        assert delete_restarted.graph_store.num_edges == 0

        one_restore = await delete_restarted._delete_admin_service.process_pending_storage_cleanup_jobs(
            operation_id=operation_id,
            limit=1,
        )
        assert one_restore["claimed"] == 1
        assert one_restore["completed"] == 1
        assert one_restore["failed"] == 0
        assert one_restore["deleted_vectors"] == 0
        restored_entity_id = memory["entities"][0]
        assert restored_entity_id in delete_restarted.vector_store
        assert all(
            vector_id not in delete_restarted.vector_store
            for vector_id in [memory["entities"][1], memory["paragraph"], memory["relation"]]
        )
        assert delete_restarted.vector_store.num_vectors == 1
        assert delete_restarted.graph_store.num_nodes == 0
        assert delete_restarted.graph_store.num_edges == 0

        await _simulate_hard_runtime_exit(delete_restarted)
        second_hard_exited = True
    finally:
        if not second_hard_exited:
            await _close_runtime(delete_restarted)
        delete_restarted = None

    restore_restarted = await _open_runtime(data_dir)
    try:
        restored_entity_id = memory["entities"][0]
        assert restored_entity_id in restore_restarted.vector_store
        assert all(
            vector_id not in restore_restarted.vector_store
            for vector_id in [memory["entities"][1], memory["paragraph"], memory["relation"]]
        )
        assert restore_restarted.vector_store.num_vectors == 1
        # 恢复事务已提交全部 metadata，启动屏障会在剩余向量任务前重建权威图。
        assert restore_restarted.graph_store.num_nodes == 2
        assert restore_restarted.graph_store.num_edges == 1

        remaining_restore = await restore_restarted._delete_admin_service.process_pending_storage_cleanup_jobs(
            operation_id=operation_id,
            limit=10,
        )
        assert remaining_restore["claimed"] == 4
        assert remaining_restore["completed"] == 4
        assert remaining_restore["failed"] == 0
        _assert_fully_restored(restore_restarted, memory)
        hits = await _semantic_vector_hits(restore_restarted)
        assert len(hits) == 4
        assert len(set(hits)) == 4
        assert set(hits) == set(memory["vector_ids"])
        assert restore_restarted.metadata_store.summarize_storage_cleanup_jobs(operation_id) == {
            "completed": 9,
            "total": 9,
            "unfinished": 0,
        }
        restore_restarted._persist(force_vectors=True)
    finally:
        await _close_runtime(restore_restarted)

    verified = await _open_runtime(data_dir)
    try:
        _assert_fully_restored(verified, memory)
        assert verified.metadata_store.get_delete_operation(operation_id)["status"] == "restored"
    finally:
        await _close_runtime(verified)
