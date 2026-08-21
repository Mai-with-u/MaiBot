from pathlib import Path
from typing import Any

import pytest

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.storage.graph_store import GraphStore
from src.A_memorix.core.storage.metadata_store import MetadataStore


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
        jobs = metadata_store.query(
            "SELECT relation_hash, status, last_error FROM relation_graph_projection_jobs"
        )
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
