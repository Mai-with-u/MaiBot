from pathlib import Path
from typing import Any, Dict

import pytest

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.storage.graph_store import GraphStore
from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.utils.hash import compute_hash


def _runtime(tmp_path: Path) -> tuple[SDKMemoryKernel, MetadataStore, GraphStore]:
    metadata_store = MetadataStore(data_dir=tmp_path / "metadata")
    metadata_store.connect()
    graph_store = GraphStore(data_dir=tmp_path / "graph")
    kernel = SDKMemoryKernel(plugin_root=tmp_path, config={})
    kernel.metadata_store = metadata_store
    kernel.graph_store = graph_store
    kernel.relation_write_service = None
    return kernel, metadata_store, graph_store


async def _disable_initialize() -> None:
    return None


@pytest.mark.asyncio
async def test_create_edge_commits_metadata_then_publishes_audited_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, metadata_store, graph_store = _runtime(tmp_path)
    monkeypatch.setattr(kernel, "initialize", _disable_initialize)
    try:
        created = await kernel.memory_graph_admin(
            action="create_edge",
            subject="Alice",
            predicate="喜欢",
            object="Bob",
            confidence=0.8,
            reason="pytest_create",
            updated_by="pytest",
        )

        relation_hash = metadata_store.compute_relation_hash("Alice", "喜欢", "Bob")
        relation = metadata_store.get_relation(relation_hash)
        assert created["success"] is True
        assert created["created"] is True
        assert created["projection"]["status"] == "completed"
        assert created["vector_projection"]["status"] == "disabled"
        assert relation is not None
        assert relation["confidence"] == pytest.approx(0.8)
        assert graph_store.get_relation_hashes_for_edge("Alice", "Bob") == {relation_hash}
        assert metadata_store.count_claimable_relation_graph_projection_jobs() == 0

        operation = metadata_store.query(
            "SELECT action, reason, updated_by FROM memory_v5_operations WHERE operation_id = ?",
            (created["operation"]["operation_id"],),
        )[0]
        assert operation == {
            "action": "graph_create_edge",
            "reason": "pytest_create",
            "updated_by": "pytest",
        }

        repeated = await kernel.memory_graph_admin(
            action="create_edge",
            subject="Alice",
            predicate="喜欢",
            object="Bob",
            confidence=0.2,
        )
        assert repeated["created"] is False
        assert metadata_store.get_relation(relation_hash)["confidence"] == pytest.approx(0.8)

        with metadata_store.transaction(immediate=True) as conn:
            conn.execute(
                """
                UPDATE relations
                SET is_inactive = 1,
                    inactive_since = retention_anchor_at,
                    inactive_reason = 'pytest',
                    lifecycle_revision = lifecycle_revision + 1
                WHERE hash = ?
                """,
                (relation_hash,),
            )
        reactivated = await kernel.memory_graph_admin(
            action="create_edge",
            subject="Alice",
            predicate="喜欢",
            object="Bob",
            confidence=0.2,
        )
        assert reactivated["reactivated"] is True
        assert bool(metadata_store.get_relation(relation_hash)["is_inactive"]) is False
        assert graph_store.get_relation_hashes_for_edge("Alice", "Bob") == {relation_hash}
    finally:
        metadata_store.close()


@pytest.mark.asyncio
async def test_create_edge_keeps_authoritative_write_and_retry_job_when_graph_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, metadata_store, graph_store = _runtime(tmp_path)
    monkeypatch.setattr(kernel, "initialize", _disable_initialize)

    def fail_save(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise OSError("graph disk unavailable")

    monkeypatch.setattr(graph_store, "save", fail_save)
    try:
        result = await kernel.memory_graph_admin(
            action="create_edge",
            subject="Alice",
            predicate="认识",
            object="Carol",
            confidence=0.6,
        )

        relation_hash = metadata_store.compute_relation_hash("Alice", "认识", "Carol")
        assert result["success"] is True
        assert result["projection"]["status"] == "pending_retry"
        assert "graph disk unavailable" in result["projection"]["error"]
        assert metadata_store.get_relation(relation_hash) is not None
        jobs = metadata_store.query("SELECT relation_hash, status, last_error FROM relation_graph_projection_jobs")
        assert jobs == [
            {
                "relation_hash": relation_hash,
                "status": "failed",
                "last_error": "graph disk unavailable",
            }
        ]
    finally:
        metadata_store.close()


@pytest.mark.asyncio
async def test_update_edge_weight_changes_metadata_confidence_without_rebuilding_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, metadata_store, graph_store = _runtime(tmp_path)
    monkeypatch.setattr(kernel, "initialize", _disable_initialize)
    relation_hash = metadata_store.add_relation("Alice", "持有", "Map", confidence=0.3)

    def unexpected_save(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("置信度修改不应保存结构图")

    monkeypatch.setattr(graph_store, "save", unexpected_save)
    try:
        result = await kernel.memory_graph_admin(
            action="update_edge_weight",
            hash=relation_hash,
            weight=0.9,
            reason="pytest_confidence",
            updated_by="pytest",
        )

        assert result["success"] is True
        assert result["confidence"] == pytest.approx(0.9)
        assert result["previous_confidence"] == {relation_hash: pytest.approx(0.3)}
        assert result["projection"]["status"] == "not_required"
        assert metadata_store.get_relation(relation_hash)["confidence"] == pytest.approx(0.9)
        assert result["operation"]["action"] == "graph_update_relation_confidence"
    finally:
        metadata_store.close()


@pytest.mark.asyncio
async def test_edge_mutations_reject_non_numeric_weight_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, metadata_store, _ = _runtime(tmp_path)
    monkeypatch.setattr(kernel, "initialize", _disable_initialize)
    relation_hash = metadata_store.add_relation("Alice", "持有", "Map", confidence=0.3)
    try:
        created = await kernel.memory_graph_admin(
            action="create_edge",
            subject="Alice",
            predicate="认识",
            object="Bob",
            confidence="invalid",
        )
        updated = await kernel.memory_graph_admin(
            action="update_edge_weight",
            hash=relation_hash,
            weight={"invalid": True},
        )

        assert created == {"success": False, "error": "confidence 必须位于[0, 1]"}
        assert updated == {"success": False, "error": "weight 必须位于[0, 1]"}
        assert metadata_store.get_relation(relation_hash)["confidence"] == pytest.approx(0.3)
    finally:
        metadata_store.close()


@pytest.mark.asyncio
async def test_create_node_is_idempotent_and_does_not_increment_appearance_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, metadata_store, _ = _runtime(tmp_path)
    monkeypatch.setattr(kernel, "initialize", _disable_initialize)
    try:
        first = await kernel.memory_graph_admin(action="create_node", name="Alice")
        second = await kernel.memory_graph_admin(action="create_node", name="Alice")

        entity = metadata_store.get_entity(first["node"]["hash"])
        assert first["created"] is True
        assert second["created"] is False
        assert entity is not None
        assert entity["appearance_count"] == 1
    finally:
        metadata_store.close()


@pytest.mark.asyncio
async def test_graph_audit_failure_rolls_back_authoritative_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, metadata_store, graph_store = _runtime(tmp_path)
    monkeypatch.setattr(kernel, "initialize", _disable_initialize)
    relation_hash = metadata_store.add_relation("Alice", "持有", "Map", confidence=0.3)
    original_entity_hash = metadata_store.add_entity("Before")

    def fail_record_operation(**kwargs: Any) -> Dict[str, Any]:
        del kwargs
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(metadata_store, "record_v5_operation", fail_record_operation)
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await kernel.memory_graph_admin(action="create_node", name="Rollback Node")
        assert metadata_store.get_entity(compute_hash("rollback node")) is None

        with pytest.raises(RuntimeError, match="audit unavailable"):
            await kernel.memory_graph_admin(
                action="create_edge",
                subject="Alice",
                predicate="认识",
                object="Bob",
            )
        rolled_back_relation = metadata_store.compute_relation_hash("Alice", "认识", "Bob")
        assert metadata_store.get_relation(rolled_back_relation) is None
        assert metadata_store.count_claimable_relation_graph_projection_jobs() == 0
        assert graph_store.num_nodes == 0

        with pytest.raises(RuntimeError, match="audit unavailable"):
            await kernel.memory_graph_admin(
                action="update_edge_weight",
                hash=relation_hash,
                weight=0.9,
            )
        assert metadata_store.get_relation(relation_hash)["confidence"] == pytest.approx(0.3)

        renamed = await kernel.memory_graph_admin(
            action="rename_node",
            name="Before",
            new_name="After",
        )
        assert renamed == {"success": False, "error": "rename failed: audit unavailable"}
        assert metadata_store.get_entity(original_entity_hash) is not None
        assert metadata_store.get_entity(compute_hash("after")) is None
    finally:
        metadata_store.close()


@pytest.mark.asyncio
async def test_rename_node_reports_vector_invalidation_failure_without_hiding_committed_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, metadata_store, _graph_store = _runtime(tmp_path)
    monkeypatch.setattr(kernel, "initialize", _disable_initialize)
    original_hash = metadata_store.add_entity("Before")

    def fail_vector_invalidation(**kwargs: Any) -> int:
        del kwargs
        raise RuntimeError("vector store unavailable")

    async def complete_vector_rebuild(**kwargs: Any) -> Dict[str, Any]:
        del kwargs
        return {"status": "completed", "entity": {"ready": True}, "relations": {}}

    monkeypatch.setattr(kernel._graph_admin_service, "_delete_vectors_by_type", fail_vector_invalidation)
    monkeypatch.setattr(kernel._graph_admin_service, "_rebuild_renamed_vectors", complete_vector_rebuild)
    try:
        result = await kernel.memory_graph_admin(
            action="rename_node",
            name="Before",
            new_name="After",
        )

        assert result["success"] is True
        assert metadata_store.get_entity(original_hash) is None
        assert metadata_store.get_entity(compute_hash("after")) is not None
        assert result["vector_projection"]["status"] == "failed"
        assert result["vector_projection"]["invalidation"] == {
            "status": "failed",
            "entity_hashes": [compute_hash("after")],
            "relation_hashes": [],
            "error": "vector store unavailable",
        }
        assert result["vector_projection"]["rebuild"]["status"] == "completed"
    finally:
        metadata_store.close()


@pytest.mark.asyncio
async def test_node_detail_derives_active_evidence_without_rewriting_appearance_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel, metadata_store, graph_store = _runtime(tmp_path)
    monkeypatch.setattr(kernel, "initialize", _disable_initialize)
    try:
        live_paragraph = metadata_store.add_paragraph("Alice 喜欢观星", source="source-live")
        deleted_paragraph = metadata_store.add_paragraph("Alice 喜欢咖啡", source="source-deleted")
        entity_hash = metadata_store.add_entity("Alice", source_paragraph=live_paragraph)
        metadata_store.add_entity("Alice", source_paragraph=deleted_paragraph)
        graph_store.add_nodes(["Alice"])
        metadata_store.mark_as_deleted(
            [deleted_paragraph],
            "paragraph",
            reason="pytest_source_delete",
        )

        detail = await kernel.memory_graph_admin(action="node_detail", node_id="Alice")
        entity = metadata_store.get_entity(entity_hash)

        assert detail["success"] is True
        assert detail["node"]["active_evidence_count"] == 1
        assert detail["node"]["appearance_count"] == 2
        assert entity is not None
        assert entity["appearance_count"] == 2
    finally:
        metadata_store.close()
