from pathlib import Path
from typing import Any, Callable, Dict, Sequence

import asyncio
import sqlite3

import pytest

from src.A_memorix.core.runtime.services.delete_admin_service import MemoryDeleteAdminService
from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.utils.memory_lifecycle_policy import (
    RelationLifecycleEvent,
    RelationLifecyclePolicy,
)


class _DeleteKernel:
    def __init__(self, metadata_store: MetadataStore) -> None:
        self.metadata_store = metadata_store
        self.vector_store = _DeleteVectorStore()
        self.graph_store = _DeleteGraphStore()
        self.vector_delete_calls: list[Dict[str, list[str]]] = []
        self.graph_rebuild_count = 0
        self.persist_count = 0
        self.vector_restore_calls: list[tuple[str, str]] = []
        self.fail_vector_deletes_remaining = 0
        self.relation_vectors_enabled = True
        self._storage_cleanup_lock = asyncio.Lock()

    def _cfg(self, key: str, default: Any = None) -> Any:
        return default

    def _dual_vector_pools_enabled(self) -> bool:
        return False

    def _save_vector_store(self, store: "_DeleteVectorStore") -> None:
        assert store is self.vector_store
        store.save()
        self.persist_count += 1

    def _delete_vectors_by_type(
        self,
        *,
        paragraph_hashes: Sequence[str] = (),
        entity_hashes: Sequence[str] = (),
        relation_hashes: Sequence[str] = (),
    ) -> int:
        payload = {
            "paragraph_hashes": list(paragraph_hashes),
            "entity_hashes": list(entity_hashes),
            "relation_hashes": list(relation_hashes),
        }
        self.vector_delete_calls.append(payload)
        if self.fail_vector_deletes_remaining > 0:
            self.fail_vector_deletes_remaining -= 1
            raise RuntimeError("injected vector delete failure")
        for hash_value in (
            *paragraph_hashes,
            *entity_hashes,
            *relation_hashes,
        ):
            self.vector_store.active_hashes.discard(str(hash_value))
        return sum(len(values) for values in payload.values())

    def _rebuild_graph_from_metadata(self) -> Dict[str, int]:
        self.graph_rebuild_count += 1
        return {"nodes": 0, "edges": 0}

    def _persist(self) -> None:
        self.persist_count += 1

    async def _ensure_paragraph_vector(
        self,
        paragraph: Dict[str, Any],
        *,
        before_vector_write: Callable[[], None] | None = None,
    ) -> bool:
        hash_value = str(paragraph["hash"])
        self.vector_restore_calls.append(("paragraph", hash_value))
        if hash_value in self.vector_store.active_hashes:
            return True
        if before_vector_write is not None:
            before_vector_write()
        self.vector_store.active_hashes.add(hash_value)
        self.vector_store.known_hashes.add(hash_value)
        return True

    async def _ensure_entity_vector(
        self,
        entity: Dict[str, Any],
        *,
        before_vector_write: Callable[[], None] | None = None,
    ) -> bool:
        hash_value = str(entity["hash"])
        self.vector_restore_calls.append(("entity", hash_value))
        if hash_value in self.vector_store.active_hashes:
            return True
        if before_vector_write is not None:
            before_vector_write()
        self.vector_store.active_hashes.add(hash_value)
        self.vector_store.known_hashes.add(hash_value)
        return True

    async def _ensure_relation_vector(
        self,
        relation: Dict[str, Any],
        *,
        before_vector_write: Callable[[], None] | None = None,
    ) -> bool:
        hash_value = str(relation["hash"])
        self.vector_restore_calls.append(("relation", hash_value))
        if hash_value in self.vector_store.active_hashes:
            return True
        if before_vector_write is not None:
            before_vector_write()
        self.vector_store.active_hashes.add(hash_value)
        self.vector_store.known_hashes.add(hash_value)
        return True


class _DeleteVectorStore:
    def __init__(self) -> None:
        self.save_count = 0
        self.active_hashes: set[str] = set()
        self.known_hashes: set[str] = set()
        self.committed_active_hashes: set[str] = set()
        self.committed_known_hashes: set[str] = set()
        self.save_history: list[frozenset[str]] = []
        self._checkpoint: Dict[str, Any] | None = None

    def save(self) -> None:
        self.save_count += 1
        self.known_hashes.update(self.active_hashes)
        self.committed_active_hashes = set(self.active_hashes)
        self.committed_known_hashes = set(self.known_hashes)
        self.save_history.append(frozenset(self.active_hashes))

    def is_tombstoned(self, hash_value: str) -> bool:
        return hash_value in self.known_hashes and hash_value not in self.active_hashes

    def delete(self, ids: Sequence[str]) -> int:
        deleted = 0
        for hash_value in ids:
            token = str(hash_value)
            if token in self.active_hashes:
                self.active_hashes.remove(token)
                self.known_hashes.add(token)
                deleted += 1
        return deleted

    def restore(self, ids: Sequence[str]) -> int:
        restored = 0
        for hash_value in ids:
            token = str(hash_value)
            if token in self.known_hashes and token not in self.active_hashes:
                self.active_hashes.add(token)
                restored += 1
        return restored

    def begin_cleanup_checkpoint(self) -> str:
        if self._checkpoint is not None:
            raise RuntimeError("nested fake checkpoint")
        token = f"checkpoint-{self.save_count}"
        self._checkpoint = {
            "token": token,
            "active_hashes": set(self.committed_active_hashes),
            "known_hashes": set(self.committed_known_hashes),
        }
        return token

    def commit_cleanup_checkpoint(self, checkpoint_token: str) -> None:
        if self._checkpoint is None or self._checkpoint["token"] != checkpoint_token:
            raise RuntimeError("fake checkpoint token mismatch")
        self._checkpoint = None

    def rollback_cleanup_checkpoint(self, checkpoint_token: str) -> None:
        if self._checkpoint is None or self._checkpoint["token"] != checkpoint_token:
            raise RuntimeError("fake checkpoint token mismatch")
        self.active_hashes = set(self._checkpoint["active_hashes"])
        self.known_hashes = set(self._checkpoint["known_hashes"])
        self.committed_active_hashes = set(self.active_hashes)
        self.committed_known_hashes = set(self.known_hashes)
        self._checkpoint = None


class _DeleteGraphStore:
    def __init__(self) -> None:
        self.save_count = 0

    def save(self) -> None:
        self.save_count += 1


@pytest.fixture
def metadata_store(tmp_path: Path) -> MetadataStore:
    store = MetadataStore(tmp_path)
    store.connect()
    try:
        yield store
    finally:
        store.close()


def _prepare_pending_paragraph_delete(
    metadata_store: MetadataStore,
    service: MemoryDeleteAdminService,
    paragraph_hash: str,
) -> Dict[str, Any]:
    item = service._snapshot_paragraph_item(paragraph_hash)
    assert item is not None
    operation = metadata_store.create_delete_operation(
        mode="paragraph",
        selector={"hashes": [paragraph_hash]},
        items=[item],
        status="pending_cleanup",
    )
    operation_id = str(operation["operation_id"])
    assert metadata_store.mark_as_deleted(
        [paragraph_hash],
        "paragraph",
        reason="concurrency_test",
    ) == 1
    metadata_store.enqueue_storage_cleanup_jobs(
        operation_id=operation_id,
        jobs=service._delete_cleanup_jobs(
            paragraph_hashes=[paragraph_hash],
            entity_hashes=[],
            relation_hashes=[],
        ),
    )
    refreshed = metadata_store.get_delete_operation(operation_id)
    assert refreshed is not None
    return refreshed


def test_paragraph_gc_requires_explicit_expiration(metadata_store: MetadataStore) -> None:
    ordinary_hash = metadata_store.add_paragraph("没有实体和关系的普通纯文本", source="ordinary")
    permanent_hash = metadata_store.add_paragraph("需要永久保留的纯文本", source="permanent")
    external_hash = metadata_store.add_paragraph("由外部系统引用的纯文本", source="external")

    expires_at = 100.0
    metadata_store.set_paragraph_expiration(
        [permanent_hash, external_hash],
        expires_at=expires_at,
        reason="test_ttl",
    )
    metadata_store.set_permanence(permanent_hash, "paragraph", True)
    metadata_store.upsert_external_memory_ref(
        external_id="external:test:1",
        paragraph_hash=external_hash,
        source_type="test",
    )

    assert metadata_store.get_expired_paragraph_hashes(now=10_000.0) == []
    assert metadata_store.get_paragraph(ordinary_hash)["is_deleted"] == 0

    metadata_store.set_paragraph_expiration(
        [ordinary_hash],
        expires_at=expires_at,
        reason="test_ttl",
    )
    assert metadata_store.get_expired_paragraph_hashes(now=10_000.0) == [ordinary_hash]


def test_external_reference_foreign_key_rejects_dangling_target(metadata_store: MetadataStore) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        metadata_store.upsert_external_memory_ref(
            external_id="external:missing",
            paragraph_hash="f" * 64,
            source_type="test",
        )

    paragraph_hash = metadata_store.add_paragraph("外部引用级联测试", source="external")
    metadata_store.upsert_external_memory_ref(
        external_id="external:cascade",
        paragraph_hash=paragraph_hash,
        source_type="test",
    )
    metadata_store.physically_delete_paragraphs([paragraph_hash])
    assert metadata_store.get_external_memory_ref("external:cascade") is None


def test_cleanup_outbox_transaction_rolls_back_as_a_unit(metadata_store: MetadataStore) -> None:
    with pytest.raises(RuntimeError, match="inject rollback"):
        with metadata_store.transaction(immediate=True) as connection:
            operation = metadata_store.create_delete_operation(
                mode="paragraph",
                selector={"hash": "x"},
                items=[],
                status="prepared",
            )
            metadata_store.enqueue_storage_cleanup_jobs(
                operation_id=str(operation["operation_id"]),
                jobs=[
                    {
                        "resource_type": "paragraph",
                        "resource_id": "batch",
                        "action": "vector_delete",
                        "payload": {"paragraph_hashes": ["x"]},
                        "expected_state": {"operation_status": "pending_cleanup"},
                    }
                ],
                conn=connection,
            )
            raise RuntimeError("inject rollback")

    assert metadata_store.list_delete_operations(limit=10) == []
    assert metadata_store.list_storage_cleanup_jobs(limit=10) == []


def test_cleanup_outbox_recovers_expired_lease(metadata_store: MetadataStore) -> None:
    operation = metadata_store.create_delete_operation(
        mode="paragraph",
        selector={"hash": "x"},
        items=[],
        status="pending_cleanup",
    )
    operation_id = str(operation["operation_id"])
    metadata_store.enqueue_storage_cleanup_jobs(
        operation_id=operation_id,
        jobs=[
            {
                "resource_type": "paragraph",
                "resource_id": "batch",
                "action": "vector_delete",
                "payload": {"paragraph_hashes": ["x"]},
                "expected_state": {"operation_status": "pending_cleanup"},
            }
        ],
    )
    created_at = float(metadata_store.list_storage_cleanup_jobs(operation_id=operation_id)[0]["created_at"])

    first = metadata_store.claim_storage_cleanup_jobs(
        worker_token="worker-1",
        lease_seconds=10.0,
        now=created_at,
    )
    assert len(first) == 1
    assert metadata_store.claim_storage_cleanup_jobs(worker_token="worker-2", now=created_at + 5.0) == []
    second = metadata_store.claim_storage_cleanup_jobs(worker_token="worker-2", now=created_at + 11.0)
    assert len(second) == 1
    assert second[0]["attempt_count"] == 2
    assert metadata_store.complete_storage_cleanup_job(
        job_id=int(second[0]["job_id"]),
        worker_token="worker-2",
    )


@pytest.mark.asyncio
async def test_cleanup_expected_state_blocks_stale_delete_side_effect(
    metadata_store: MetadataStore,
) -> None:
    operation = metadata_store.create_delete_operation(
        mode="paragraph",
        selector={"hash": "p"},
        items=[],
        status="restored",
    )
    operation_id = str(operation["operation_id"])
    metadata_store.enqueue_storage_cleanup_jobs(
        operation_id=operation_id,
        jobs=[
            {
                "resource_type": "paragraph",
                "resource_id": "p",
                "action": "vector_delete",
                "payload": {"paragraph_hashes": ["p"]},
                "expected_state": {"operation_status": "pending_cleanup"},
            }
        ],
    )
    kernel = _DeleteKernel(metadata_store)
    kernel.vector_store.active_hashes.add("p")
    service = MemoryDeleteAdminService(kernel)

    result = await service.process_pending_storage_cleanup_jobs(operation_id=operation_id)

    assert result["cancelled"] == 1
    assert result["completed"] == 0
    assert kernel.vector_delete_calls == []
    assert kernel.vector_store.active_hashes == {"p"}
    jobs = metadata_store.list_storage_cleanup_jobs(operation_id=operation_id)
    assert [job["status"] for job in jobs] == ["cancelled"]


@pytest.mark.asyncio
async def test_restore_refuses_to_cancel_running_delete_job(
    metadata_store: MetadataStore,
) -> None:
    paragraph_hash = metadata_store.add_paragraph("运行中删除任务不能被恢复取消", source="running")
    kernel = _DeleteKernel(metadata_store)
    service = MemoryDeleteAdminService(kernel)
    operation = _prepare_pending_paragraph_delete(metadata_store, service, paragraph_hash)
    operation_id = str(operation["operation_id"])
    claimed = metadata_store.claim_storage_cleanup_jobs(
        worker_token="external-worker",
        operation_id=operation_id,
        limit=1,
    )
    assert len(claimed) == 1

    with pytest.raises(RuntimeError, match="仍在运行"):
        await service._restore_delete_operation(operation)

    refreshed = metadata_store.get_delete_operation(operation_id)
    assert refreshed is not None
    assert refreshed["status"] == "pending_cleanup"
    assert metadata_store.get_paragraph(paragraph_hash)["is_deleted"] == 1
    assert {job["status"] for job in metadata_store.list_storage_cleanup_jobs(operation_id=operation_id)} == {
        "pending",
        "running",
    }
    assert kernel.vector_restore_calls == []


@pytest.mark.asyncio
async def test_direct_relation_restore_refuses_running_old_delete_job(
    metadata_store: MetadataStore,
) -> None:
    relation_hash = metadata_store.add_relation("运行关系", "关联", "运行目标")
    kernel = _DeleteKernel(metadata_store)
    service = MemoryDeleteAdminService(kernel)
    item = service._snapshot_relation_item(relation_hash)
    assert item is not None
    operation = metadata_store.create_delete_operation(
        mode="relation",
        selector={"hashes": [relation_hash]},
        items=[item],
        status="pending_cleanup",
    )
    operation_id = str(operation["operation_id"])
    assert metadata_store.backup_and_delete_relations([relation_hash]) == 1
    metadata_store.enqueue_storage_cleanup_jobs(
        operation_id=operation_id,
        jobs=service._delete_cleanup_jobs(
            paragraph_hashes=[],
            entity_hashes=[],
            relation_hashes=[relation_hash],
        ),
    )
    claimed = metadata_store.claim_storage_cleanup_jobs(
        worker_token="external-relation-worker",
        operation_id=operation_id,
        limit=1,
    )
    assert len(claimed) == 1

    with pytest.raises(RuntimeError, match="仍在运行"):
        await service.restore_deleted_relations([relation_hash])

    assert metadata_store.get_relation(relation_hash) is None
    assert metadata_store.get_deleted_relation(relation_hash) is not None
    assert metadata_store.get_delete_operation(operation_id)["status"] == "pending_cleanup"
    assert len(metadata_store.list_delete_operations(limit=10)) == 1


@pytest.mark.asyncio
async def test_restore_cancellation_and_rows_roll_back_together(
    metadata_store: MetadataStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paragraph_hash = metadata_store.add_paragraph("恢复事务必须整体回滚", source="rollback")
    kernel = _DeleteKernel(metadata_store)
    service = MemoryDeleteAdminService(kernel)
    operation = _prepare_pending_paragraph_delete(metadata_store, service, paragraph_hash)
    operation_id = str(operation["operation_id"])

    def fail_restore(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("inject restore rollback")

    monkeypatch.setattr(metadata_store, "restore_table_row_from_snapshot", fail_restore)
    with pytest.raises(RuntimeError, match="inject restore rollback"):
        await service._restore_delete_operation(operation)

    refreshed = metadata_store.get_delete_operation(operation_id)
    assert refreshed is not None
    assert refreshed["status"] == "pending_cleanup"
    assert metadata_store.get_paragraph(paragraph_hash)["is_deleted"] == 1
    jobs = metadata_store.list_storage_cleanup_jobs(operation_id=operation_id)
    assert {job["status"] for job in jobs} == {"pending"}
    assert {job["action"] for job in jobs} == {"vector_delete", "graph_rebuild"}


@pytest.mark.asyncio
async def test_delete_worker_and_restore_commit_in_serial_order(
    metadata_store: MetadataStore,
) -> None:
    paragraph_hash = metadata_store.add_paragraph("删除恢复外部提交顺序", source="serialized")
    kernel = _DeleteKernel(metadata_store)
    kernel.vector_store.active_hashes.add(paragraph_hash)
    service = MemoryDeleteAdminService(kernel)
    stale_operation = _prepare_pending_paragraph_delete(metadata_store, service, paragraph_hash)
    operation_id = str(stale_operation["operation_id"])

    await kernel._storage_cleanup_lock.acquire()
    worker_task = asyncio.create_task(
        service.process_pending_storage_cleanup_jobs(operation_id=operation_id)
    )
    await asyncio.sleep(0)
    assert {job["status"] for job in metadata_store.list_storage_cleanup_jobs(operation_id=operation_id)} == {
        "pending"
    }
    restore_task = asyncio.create_task(service._restore_delete_operation(stale_operation))
    await asyncio.sleep(0)
    assert not worker_task.done()
    assert not restore_task.done()
    kernel._storage_cleanup_lock.release()

    worker_result, restore_result = await asyncio.gather(worker_task, restore_task)

    assert worker_result["completed"] == 2
    assert restore_result["success"] is True
    assert stale_operation["status"] == "pending_cleanup"
    refreshed = metadata_store.get_delete_operation(operation_id)
    assert refreshed is not None
    assert refreshed["status"] == "restored"
    assert metadata_store.get_paragraph(paragraph_hash)["is_deleted"] == 0
    assert paragraph_hash in kernel.vector_store.active_hashes
    assert kernel.vector_store.save_history == [
        frozenset({paragraph_hash}),
        frozenset(),
        frozenset(),
        frozenset({paragraph_hash}),
    ]


@pytest.mark.asyncio
async def test_cleanup_outbox_persists_one_vector_pool_once_per_claimed_batch(
    metadata_store: MetadataStore,
) -> None:
    operation = metadata_store.create_delete_operation(
        mode="mixed",
        selector={"hashes": ["p", "e", "r"]},
        items=[],
        status="pending_cleanup",
    )
    operation_id = str(operation["operation_id"])
    metadata_store.enqueue_storage_cleanup_jobs(
        operation_id=operation_id,
        jobs=[
            {
                "resource_type": "paragraph",
                "resource_id": "p",
                "action": "vector_delete",
                "payload": {"paragraph_hashes": ["p"]},
                "expected_state": {"operation_status": "pending_cleanup"},
            },
            {
                "resource_type": "entity",
                "resource_id": "e",
                "action": "vector_delete",
                "payload": {"entity_hashes": ["e"]},
                "expected_state": {"operation_status": "pending_cleanup"},
            },
            {
                "resource_type": "relation",
                "resource_id": "r",
                "action": "vector_delete",
                "payload": {"relation_hashes": ["r"]},
                "expected_state": {"operation_status": "pending_cleanup"},
            },
        ],
    )
    kernel = _DeleteKernel(metadata_store)
    service = MemoryDeleteAdminService(kernel)

    result = await service.process_pending_storage_cleanup_jobs(operation_id=operation_id)

    assert result["claimed"] == 3
    assert result["completed"] == 3
    assert result["failed"] == 0
    assert result["deleted_vectors"] == 3
    # 一次提交批次入口基线，一次提交该批删除；三个任务仍只共享一个外部变更提交。
    assert kernel.vector_store.save_count == 2
    assert len(kernel.vector_delete_calls) == 3
    assert metadata_store.summarize_storage_cleanup_jobs(operation_id) == {
        "completed": 3,
        "total": 3,
        "unfinished": 0,
    }


@pytest.mark.asyncio
async def test_delete_revalidation_failure_rolls_back_vector_checkpoint(
    metadata_store: MetadataStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paragraph_hash = metadata_store.add_paragraph("重验失败后仍可检索", source="revalidate")
    kernel = _DeleteKernel(metadata_store)
    kernel.vector_store.active_hashes.add(paragraph_hash)
    service = MemoryDeleteAdminService(kernel)
    operation = _prepare_pending_paragraph_delete(
        metadata_store,
        service,
        paragraph_hash,
    )
    operation_id = str(operation["operation_id"])
    real_authorize = metadata_store.authorize_storage_cleanup_job
    authorize_count = 0

    def reactivate_after_first_authority(**kwargs: Any) -> Dict[str, Any]:
        nonlocal authorize_count
        authority = real_authorize(**kwargs)
        authorize_count += 1
        if authorize_count == 1:
            connection = metadata_store.get_connection()
            connection.execute(
                "UPDATE paragraphs SET is_deleted = 0, deleted_at = NULL WHERE hash = ?",
                (paragraph_hash,),
            )
            connection.commit()
        return authority

    monkeypatch.setattr(
        metadata_store,
        "authorize_storage_cleanup_job",
        reactivate_after_first_authority,
    )

    result = await service.process_pending_storage_cleanup_jobs(
        operation_id=operation_id,
        limit=1,
    )

    assert result["failed"] == 1
    assert result["completed"] == 0
    assert result["deleted_vectors"] == 0
    assert paragraph_hash in kernel.vector_store.active_hashes
    assert paragraph_hash in kernel.vector_store.committed_active_hashes
    assert kernel.vector_store._checkpoint is None


@pytest.mark.asyncio
async def test_vector_upsert_losing_authority_after_await_never_mutates_store(
    metadata_store: MetadataStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_hash = metadata_store.add_entity("等待期间失去授权")
    entity = metadata_store.get_entity(entity_hash)
    assert entity is not None
    operation = metadata_store.create_delete_operation(
        mode="entity_restore",
        selector={"hash": entity_hash},
        items=[],
        status="restore_pending",
    )
    operation_id = str(operation["operation_id"])
    metadata_store.enqueue_storage_cleanup_jobs(
        operation_id=operation_id,
        jobs=[
            {
                "resource_type": "entity",
                "resource_id": entity_hash,
                "action": "vector_upsert",
                "payload": {"item": entity},
                "expected_state": {"operation_status": "restore_pending"},
            }
        ],
    )
    kernel = _DeleteKernel(metadata_store)
    service = MemoryDeleteAdminService(kernel)

    async def lose_authority_before_write(
        item: Dict[str, Any],
        *,
        before_vector_write: Callable[[], None] | None = None,
    ) -> bool:
        await asyncio.sleep(0)
        metadata_store.update_delete_operation_state(
            operation_id,
            status="superseded",
        )
        assert before_vector_write is not None
        before_vector_write()
        kernel.vector_store.active_hashes.add(str(item["hash"]))
        return True

    monkeypatch.setattr(kernel, "_ensure_entity_vector", lose_authority_before_write)

    result = await service.process_pending_storage_cleanup_jobs(operation_id=operation_id)

    assert result["failed"] == 1
    assert entity_hash not in kernel.vector_store.active_hashes
    assert entity_hash not in kernel.vector_store.known_hashes
    assert kernel.vector_store.save_count == 0
    assert kernel.vector_store._checkpoint is None


@pytest.mark.asyncio
async def test_cleanup_outbox_retries_external_failure_after_metadata_commit(
    metadata_store: MetadataStore,
) -> None:
    paragraph_hash = metadata_store.add_paragraph("故障注入删除测试", source="failure-retry")
    kernel = _DeleteKernel(metadata_store)
    kernel.fail_vector_deletes_remaining = 1
    service = MemoryDeleteAdminService(kernel)

    first = await service._execute_delete_action(
        mode="paragraph",
        selector={"hashes": [paragraph_hash]},
        requested_by="test",
        reason="fault_injection",
    )
    operation_id = str(first["operation_id"])
    operation_after_failure = metadata_store.get_delete_operation(operation_id)
    jobs_after_failure = metadata_store.list_storage_cleanup_jobs(operation_id=operation_id)

    assert first["success"] is True
    assert first["cleanup"]["failed"] == 1
    assert metadata_store.get_paragraph(paragraph_hash)["is_deleted"] == 1
    assert operation_after_failure is not None
    assert operation_after_failure["status"] == "pending_cleanup"
    assert {job["status"] for job in jobs_after_failure} == {"completed", "failed"}

    connection = metadata_store.get_connection()
    connection.execute(
        "UPDATE storage_cleanup_jobs SET next_attempt_at = 0 WHERE operation_id = ? AND status = 'failed'",
        (operation_id,),
    )
    connection.commit()
    retried = await service.process_pending_storage_cleanup_jobs(operation_id=operation_id)
    completed_operation = metadata_store.get_delete_operation(operation_id)

    assert retried["claimed"] == 1
    assert retried["completed"] == 1
    assert retried["failed"] == 0
    assert metadata_store.summarize_storage_cleanup_jobs(operation_id)["unfinished"] == 0
    assert completed_operation is not None
    assert completed_operation["status"] == "completed"
    assert len(kernel.vector_delete_calls) == 2
    assert kernel.vector_store.save_count == 3
    assert kernel.graph_store.save_count == 1


@pytest.mark.asyncio
async def test_direct_relation_restore_removes_only_restored_hash_from_failed_delete_batch(
    metadata_store: MetadataStore,
) -> None:
    first_hash = metadata_store.add_relation("恢复实体", "关联", "恢复目标")
    second_hash = metadata_store.add_relation("保留删除实体", "关联", "保留删除目标")
    kernel = _DeleteKernel(metadata_store)
    kernel.vector_store.active_hashes.update({first_hash, second_hash})
    kernel.fail_vector_deletes_remaining = 1
    service = MemoryDeleteAdminService(kernel)

    deleted = await service._execute_delete_action(
        mode="relation",
        selector={"hashes": [first_hash, second_hash]},
        requested_by="test",
        reason="batch_delete_failure",
    )
    delete_operation_id = str(deleted["operation_id"])
    assert deleted["cleanup"]["failed"] == 1

    restored = await service.restore_deleted_relations(
        [first_hash],
        requested_by="test",
        reason="partial_direct_restore",
    )

    assert restored["success"] is True
    assert first_hash in kernel.vector_store.active_hashes
    old_jobs = metadata_store.list_storage_cleanup_jobs(operation_id=delete_operation_id)
    failed_job = next(job for job in old_jobs if job["action"] == "vector_delete")
    assert failed_job["status"] == "failed"
    assert failed_job["payload"] == {"relation_hashes": [second_hash]}
    assert metadata_store.get_delete_operation(delete_operation_id)["status"] == "pending_cleanup"

    connection = metadata_store.get_connection()
    connection.execute(
        "UPDATE storage_cleanup_jobs SET next_attempt_at = 0 WHERE job_id = ?",
        (int(failed_job["job_id"]),),
    )
    connection.commit()
    retried = await service.process_pending_storage_cleanup_jobs(operation_id=delete_operation_id)

    assert retried["claimed"] == 1
    assert retried["completed"] == 1
    assert first_hash in kernel.vector_store.active_hashes
    assert second_hash not in kernel.vector_store.active_hashes
    assert kernel.vector_delete_calls[-1] == {
        "paragraph_hashes": [],
        "entity_hashes": [],
        "relation_hashes": [second_hash],
    }


@pytest.mark.asyncio
async def test_reactivated_paragraph_blocks_stale_failed_vector_delete(
    metadata_store: MetadataStore,
) -> None:
    paragraph_hash = metadata_store.add_paragraph("同一哈希正式复活", source="reactivated")
    paragraph_snapshot = metadata_store.get_paragraph(paragraph_hash)
    assert paragraph_snapshot is not None
    kernel = _DeleteKernel(metadata_store)
    kernel.vector_store.active_hashes.add(paragraph_hash)
    kernel.fail_vector_deletes_remaining = 1
    service = MemoryDeleteAdminService(kernel)

    deleted = await service._execute_delete_action(
        mode="paragraph",
        selector={"hashes": [paragraph_hash]},
        requested_by="test",
        reason="reactivation_delete_failure",
    )
    operation_id = str(deleted["operation_id"])
    assert deleted["cleanup"]["failed"] == 1
    assert len(kernel.vector_delete_calls) == 1
    with metadata_store.transaction(immediate=True) as conn:
        metadata_store.restore_table_row_from_snapshot(
            "paragraphs",
            paragraph_snapshot,
            conn=conn,
        )

    connection = metadata_store.get_connection()
    connection.execute(
        "UPDATE storage_cleanup_jobs SET next_attempt_at = 0 WHERE operation_id = ? AND status = 'failed'",
        (operation_id,),
    )
    connection.commit()
    retried = await service.process_pending_storage_cleanup_jobs(operation_id=operation_id)

    assert retried["claimed"] == 1
    assert retried["completed"] == 1
    assert retried["deleted_vectors"] == 0
    assert len(kernel.vector_delete_calls) == 1
    assert paragraph_hash in kernel.vector_store.active_hashes
    assert metadata_store.get_delete_operation(operation_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_terminal_job_atomically_settles_operation_and_global_reconcile_repairs_gap(
    metadata_store: MetadataStore,
) -> None:
    operation = metadata_store.create_delete_operation(
        mode="paragraph",
        selector={"hash": "atomic"},
        items=[],
        status="pending_cleanup",
    )
    operation_id = str(operation["operation_id"])
    metadata_store.enqueue_storage_cleanup_jobs(
        operation_id=operation_id,
        jobs=[
            {
                "resource_type": "paragraph",
                "resource_id": "batch",
                "action": "vector_delete",
                "payload": {"paragraph_hashes": ["atomic"]},
                "expected_state": {"operation_status": "pending_cleanup"},
            }
        ],
    )
    claimed = metadata_store.claim_storage_cleanup_jobs(worker_token="atomic-worker")
    assert len(claimed) == 1
    assert metadata_store.complete_storage_cleanup_job(
        job_id=int(claimed[0]["job_id"]),
        worker_token="atomic-worker",
    )
    assert metadata_store.get_delete_operation(operation_id)["status"] == "completed"

    gap_operation = metadata_store.create_delete_operation(
        mode="paragraph",
        selector={"hash": "gap"},
        items=[],
        status="pending_cleanup",
    )
    gap_operation_id = str(gap_operation["operation_id"])
    metadata_store.enqueue_storage_cleanup_jobs(
        operation_id=gap_operation_id,
        jobs=[
            {
                "resource_type": "paragraph",
                "resource_id": "batch",
                "action": "vector_delete",
                "payload": {"paragraph_hashes": ["gap"]},
                "expected_state": {"operation_status": "pending_cleanup"},
            }
        ],
    )
    connection = metadata_store.get_connection()
    connection.execute(
        """
        UPDATE storage_cleanup_jobs
        SET status = 'completed', completed_at = updated_at
        WHERE operation_id = ?
        """,
        (gap_operation_id,),
    )
    connection.commit()
    kernel = _DeleteKernel(metadata_store)
    service = MemoryDeleteAdminService(kernel)

    reconciled = await service.process_pending_storage_cleanup_jobs()

    assert reconciled["claimed"] == 0
    assert gap_operation_id in reconciled["operations"]
    assert metadata_store.get_delete_operation(gap_operation_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_relation_restore_skips_vector_job_when_default_vectorization_is_disabled(
    metadata_store: MetadataStore,
    tmp_path: Path,
) -> None:
    default_kernel = SDKMemoryKernel(plugin_root=tmp_path, config={})
    assert default_kernel.relation_vectors_enabled is False

    relation_hash = metadata_store.add_relation("无向量实体", "关联", "无向量目标")
    kernel = _DeleteKernel(metadata_store)
    kernel.relation_vectors_enabled = False
    service = MemoryDeleteAdminService(kernel)
    deleted = await service._execute_delete_action(
        mode="relation",
        selector={"hashes": [relation_hash]},
        requested_by="test",
        reason="no_relation_vector",
    )
    assert deleted["success"] is True

    restored = await service.restore_deleted_relations([relation_hash])

    assert restored["success"] is True
    assert restored["status"] == "restored"
    assert metadata_store.get_relation(relation_hash)["vector_state"] == "none"
    assert kernel.vector_restore_calls == []
    restore_jobs = metadata_store.list_storage_cleanup_jobs(
        operation_id=str(restored["operation_id"])
    )
    assert [job["action"] for job in restore_jobs] == ["graph_restore"]

    operation_relation_hash = metadata_store.add_relation("无向量实体甲", "关联", "无向量目标乙")
    deleted_operation = await service._execute_delete_action(
        mode="relation",
        selector={"hashes": [operation_relation_hash]},
        requested_by="test",
        reason="no_relation_vector_operation_restore",
    )
    operation = metadata_store.get_delete_operation(str(deleted_operation["operation_id"]))
    assert operation is not None

    operation_restored = await service._restore_delete_operation(operation)

    assert operation_restored["success"] is True
    assert metadata_store.get_relation(operation_relation_hash)["vector_state"] == "none"
    operation_jobs = metadata_store.list_storage_cleanup_jobs(
        operation_id=str(operation["operation_id"])
    )
    restore_actions = [
        job["action"]
        for job in operation_jobs
        if job["expected_state"].get("operation_status") == "restore_pending"
    ]
    assert restore_actions == ["graph_restore"]


@pytest.mark.asyncio
async def test_pending_relation_vector_restore_converges_to_none_after_disable(
    metadata_store: MetadataStore,
) -> None:
    relation_hash = metadata_store.add_relation("配置切换实体", "关联", "配置切换目标")
    assert metadata_store.set_relation_vector_state(relation_hash, "pending")
    relation = metadata_store.get_relation(relation_hash)
    assert relation is not None
    operation = metadata_store.create_delete_operation(
        mode="relation_restore",
        selector={"hash": relation_hash},
        items=[],
        status="restore_pending",
    )
    operation_id = str(operation["operation_id"])
    metadata_store.enqueue_storage_cleanup_jobs(
        operation_id=operation_id,
        jobs=[
            {
                "resource_type": "relation",
                "resource_id": relation_hash,
                "action": "vector_upsert",
                "payload": {"item": relation},
                "expected_state": {"operation_status": "restore_pending"},
            }
        ],
    )
    kernel = _DeleteKernel(metadata_store)
    kernel.relation_vectors_enabled = False
    service = MemoryDeleteAdminService(kernel)

    result = await service.process_pending_storage_cleanup_jobs(
        operation_id=operation_id,
    )

    assert result["completed"] == 1
    assert result["failed"] == 0
    assert metadata_store.get_relation(relation_hash)["vector_state"] == "none"
    assert metadata_store.get_delete_operation(operation_id)["status"] == "restored"
    assert kernel.vector_restore_calls == []


@pytest.mark.asyncio
async def test_partial_direct_restore_keeps_original_batch_operation_restorable(
    metadata_store: MetadataStore,
) -> None:
    first_hash = metadata_store.add_relation("批量实体甲", "关系一", "批量目标")
    second_hash = metadata_store.add_relation("批量实体乙", "关系二", "批量目标")
    kernel = _DeleteKernel(metadata_store)
    kernel.vector_store.active_hashes.update({first_hash, second_hash})
    service = MemoryDeleteAdminService(kernel)
    deleted = await service._execute_delete_action(
        mode="relation",
        selector={"hashes": [first_hash, second_hash]},
        requested_by="test",
        reason="partial_direct_restore",
    )
    original_operation_id = str(deleted["operation_id"])
    assert metadata_store.get_delete_operation(original_operation_id)["status"] == "completed"

    direct_restore = await service.restore_deleted_relations([first_hash])

    assert direct_restore["success"] is True
    original_after_direct = metadata_store.get_delete_operation(original_operation_id)
    assert original_after_direct is not None
    assert original_after_direct["status"] == "completed"
    assert metadata_store.get_relation(first_hash) is not None
    assert metadata_store.get_relation(second_hash) is None

    operation_restore = await service._restore_delete_operation(original_after_direct)

    assert operation_restore["success"] is True
    assert metadata_store.get_relation(first_hash) is not None
    assert metadata_store.get_relation(second_hash) is not None
    assert metadata_store.get_delete_operation(original_operation_id)["status"] == "restored"


@pytest.mark.asyncio
async def test_vector_delete_batch_is_saved_before_vector_upsert_await(
    metadata_store: MetadataStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_hash = metadata_store.add_entity("等待向量恢复")
    delete_operation = metadata_store.create_delete_operation(
        mode="paragraph",
        selector={"hash": "deleted-before-await"},
        items=[],
        status="pending_cleanup",
    )
    restore_operation = metadata_store.create_delete_operation(
        mode="entity_restore",
        selector={"hash": entity_hash},
        items=[],
        status="restore_pending",
    )
    metadata_store.enqueue_storage_cleanup_jobs(
        operation_id=str(delete_operation["operation_id"]),
        jobs=[
            {
                "resource_type": "paragraph",
                "resource_id": "batch",
                "action": "vector_delete",
                "payload": {"paragraph_hashes": ["deleted-before-await"]},
                "expected_state": {"operation_status": "pending_cleanup"},
            }
        ],
    )
    entity = metadata_store.get_entity(entity_hash)
    assert entity is not None
    metadata_store.enqueue_storage_cleanup_jobs(
        operation_id=str(restore_operation["operation_id"]),
        jobs=[
            {
                "resource_type": "entity",
                "resource_id": entity_hash,
                "action": "vector_upsert",
                "payload": {"item": entity},
                "expected_state": {"operation_status": "restore_pending"},
            }
        ],
    )
    kernel = _DeleteKernel(metadata_store)
    kernel.vector_store.active_hashes.add("deleted-before-await")
    service = MemoryDeleteAdminService(kernel)

    async def assert_delete_is_saved_before_await(
        item: Dict[str, Any],
        *,
        before_vector_write: Callable[[], None] | None = None,
    ) -> bool:
        assert kernel.vector_store.save_count == 2
        assert before_vector_write is not None
        before_vector_write()
        kernel.vector_store.active_hashes.add(str(item["hash"]))
        kernel.vector_store.known_hashes.add(str(item["hash"]))
        return True

    monkeypatch.setattr(kernel, "_ensure_entity_vector", assert_delete_is_saved_before_await)
    result = await service.process_pending_storage_cleanup_jobs()

    assert result["claimed"] == 2
    assert result["completed"] == 2
    assert kernel.vector_store.save_count == 4


@pytest.mark.asyncio
async def test_delete_purge_restore_round_trip_uses_snapshot_and_outbox(
    metadata_store: MetadataStore,
) -> None:
    paragraph_hash = metadata_store.add_paragraph("可恢复删除的完整正文", source="round-trip")
    metadata_store.upsert_external_memory_ref(
        external_id="external:round-trip",
        paragraph_hash=paragraph_hash,
        source_type="test",
    )
    kernel = _DeleteKernel(metadata_store)
    service = MemoryDeleteAdminService(kernel)

    deleted = await service._execute_delete_action(
        mode="paragraph",
        selector={"hashes": [paragraph_hash]},
        requested_by="test",
        reason="round_trip_test",
    )
    assert deleted["success"] is True
    operation_id = str(deleted["operation_id"])
    assert metadata_store.get_paragraph(paragraph_hash)["is_deleted"] == 1
    assert metadata_store.get_external_memory_ref("external:round-trip") is None
    assert metadata_store.summarize_storage_cleanup_jobs(operation_id)["unfinished"] == 0

    metadata_store.get_connection().execute(
        "UPDATE paragraphs SET deleted_at = 0 WHERE hash = ?",
        (paragraph_hash,),
    )
    metadata_store.get_connection().commit()
    purged = await service._purge_deleted_memory(grace_hours=0.0, limit=100)
    assert purged["purged_counts"]["paragraphs"] == 1
    assert metadata_store.get_paragraph(paragraph_hash) is None

    operation = metadata_store.get_delete_operation(operation_id)
    assert operation is not None
    restored = await service._restore_delete_operation(operation)
    assert restored["success"] is True
    restored_paragraph = metadata_store.get_paragraph(paragraph_hash)
    assert restored_paragraph is not None
    assert restored_paragraph["content"] == "可恢复删除的完整正文"
    assert restored_paragraph["is_deleted"] == 0
    assert metadata_store.get_external_memory_ref("external:round-trip") is not None
    assert ("paragraph", paragraph_hash) in kernel.vector_restore_calls
    assert kernel.vector_store.save_count == 4
    assert kernel.graph_store.save_count == 2


@pytest.mark.asyncio
async def test_lifecycle_delete_precondition_rejects_reinforced_candidate(
    metadata_store: MetadataStore,
) -> None:
    policy = RelationLifecyclePolicy(
        half_life_hours=24.0,
        freeze_threshold=0.1,
        revive_threshold=0.15,
        access_alpha=0.05,
        reinforce_alpha=0.5,
        weaken_alpha=0.5,
    )
    relation_hash = metadata_store.add_relation("候选实体", "关联", "目标实体")
    connection = metadata_store.get_connection()
    connection.execute(
        """
        UPDATE relations
        SET retention_strength = 0.01,
            retention_anchor_at = 0.0,
            next_lifecycle_at = NULL,
            lifecycle_revision = 7,
            is_inactive = 1,
            inactive_since = 1.0,
            inactive_reason = 'decay',
            is_permanent = 0,
            is_pinned = 0,
            protected_until = NULL
        WHERE hash = ?
        """,
        (relation_hash,),
    )
    connection.commit()
    candidates = metadata_store.get_decay_prune_candidate_rows(
        cutoff_time=50.0,
        now=100.0,
        policy=policy,
    )
    assert [item["hash"] for item in candidates] == [relation_hash]

    metadata_store.apply_relation_lifecycle_event(
        [relation_hash],
        event=RelationLifecycleEvent.REINFORCE,
        policy=policy,
        now=101.0,
    )
    expected_states = {
        relation_hash: {
            key: candidates[0][key]
            for key in (
                "expected_lifecycle_revision",
                "expected_retention_strength",
                "expected_retention_anchor_at",
                "expected_inactive_since",
                "expected_inactive_reason",
                "expected_is_inactive",
                "expected_is_permanent",
                "expected_is_pinned",
                "expected_protected_until",
            )
        }
    }
    kernel = _DeleteKernel(metadata_store)
    service = MemoryDeleteAdminService(kernel)
    result = await service._execute_delete_action(
        mode="relation",
        selector={
            "hashes": [relation_hash],
            "expected_relation_states": expected_states,
        },
        requested_by="test",
        reason="lifecycle_decay_archive",
    )

    assert result["success"] is True
    assert result["deleted_relation_count"] == 0
    assert result["stale_relation_hashes"] == [relation_hash]
    assert metadata_store.get_relation(relation_hash) is not None
    assert metadata_store.get_deleted_relation(relation_hash) is None
    assert metadata_store.list_delete_operations(limit=10) == []
    assert kernel.vector_delete_calls == []


@pytest.mark.asyncio
async def test_entity_delete_marks_linked_episode_source_dirty(
    metadata_store: MetadataStore,
) -> None:
    source = "entity-delete-source"
    paragraph_hash = metadata_store.add_paragraph("实体删除应触发来源重建", source=source)
    entity_hash = metadata_store.add_entity("待删除实体", source_paragraph=paragraph_hash)
    before = {
        row["source"]: int(row["desired_revision"])
        for row in metadata_store.list_episode_source_rebuilds(limit=20)
    }[source]

    kernel = _DeleteKernel(metadata_store)
    service = MemoryDeleteAdminService(kernel)
    result = await service._execute_delete_action(
        mode="entity",
        selector={"hashes": [entity_hash]},
        requested_by="test",
        reason="entity_delete_test",
    )
    after = {
        row["source"]: int(row["desired_revision"])
        for row in metadata_store.list_episode_source_rebuilds(limit=20)
    }[source]

    assert result["success"] is True
    assert result["sources"] == [source]
    assert after == before + 1
