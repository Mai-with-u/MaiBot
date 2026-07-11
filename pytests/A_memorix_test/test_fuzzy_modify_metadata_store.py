from pathlib import Path

import pytest

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.storage.metadata_store import MetadataStore, SCHEMA_VERSION


def test_fuzzy_modify_plan_and_superseded_metadata(tmp_path):
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        assert SCHEMA_VERSION == 15

        paragraph_hash = store.add_paragraph(
            "小明喜欢咖啡",
            source="person_fact:person-1",
            metadata={"person_ids": ["person-1"], "nested": {"old": True}},
        )
        relation_hash = store.add_relation(
            "小明",
            "喜欢",
            "咖啡",
            source_paragraph=paragraph_hash,
            metadata={"source_type": "person_fact"},
        )

        paragraph_meta = store.update_paragraph_metadata(
            paragraph_hash,
            {
                "nested": {"new": True},
                "memory_change": {
                    "change_id": "fuzzy-test",
                    "change_type": "superseded",
                    "valid_to": 1000.0,
                },
            },
        )
        assert paragraph_meta is not None
        assert paragraph_meta["nested"] == {"old": True, "new": True}
        assert paragraph_meta["memory_change"]["change_type"] == "superseded"

        relation_meta = store.update_relation_metadata(
            relation_hash,
            {"memory_change": {"change_id": "fuzzy-test", "valid_to": 1000.0}},
        )
        assert relation_meta is not None
        assert relation_meta["source_type"] == "person_fact"
        assert relation_meta["memory_change"]["change_id"] == "fuzzy-test"
        store.mark_relations_inactive([relation_hash], inactive_since=1000.0)
        assert store.get_relation_status_batch([relation_hash])[relation_hash]["is_inactive"] is True

        plan = store.create_fuzzy_modify_plan(
            request_text="把小明喜欢咖啡改成喜欢茶",
            scope="person_profile",
            target_person_id="person-1",
            plan={"operations": [{"action": "mark_superseded", "hash": paragraph_hash}]},
            preview={"candidates": [{"hash": paragraph_hash}]},
            confidence=0.92,
            requested_by="pytest",
        )
        fetched = store.get_fuzzy_modify_plan(plan["plan_id"])
        assert fetched is not None
        assert fetched["status"] == "awaiting_confirmation"
        assert fetched["preview"]["candidates"][0]["hash"] == paragraph_hash

        updated = store.update_fuzzy_modify_plan(
            plan["plan_id"],
            status="executed",
            execution={"stored_ids": ["new-hash"]},
            executed_at=1001.0,
        )
        assert updated is not None
        assert updated["status"] == "executed"
        assert updated["execution"]["stored_ids"] == ["new-hash"]
        listed = store.list_fuzzy_modify_plans(statuses=["executed"])
        assert [item["plan_id"] for item in listed] == [plan["plan_id"]]

        stale_mark = store.upsert_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
            reason="旧证据失效",
            source_type="memory_correction",
            source_id=plan["plan_id"],
            source_operation_id=f"{plan['plan_id']}:{paragraph_hash}:{relation_hash}",
        )
        assert stale_mark is not None
        assert stale_mark["source_type"] == "memory_correction"
        assert stale_mark["source_id"] == plan["plan_id"]

        fetched_mark = store.get_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
        )
        assert fetched_mark is not None
        assert fetched_mark["source_operation_id"] == f"{plan['plan_id']}:{paragraph_hash}:{relation_hash}"

        rollback = store.rollback_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
            expected_source_type="memory_correction",
            expected_source_id=plan["plan_id"],
            expected_source_operation_id=f"{plan['plan_id']}:{paragraph_hash}:{relation_hash}",
            previous_mark=None,
        )
        assert rollback["action"] == "deleted"
        assert store.get_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
        ) is None
    finally:
        store.close()


def test_fuzzy_modify_plan_claim_uses_execution_lease(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        plan = store.create_fuzzy_modify_plan(
            request_text="测试并发领取",
            scope="person_profile",
            plan={"operations": []},
        )

        claimed = store.claim_fuzzy_modify_plan(plan["plan_id"], stale_after_seconds=60.0)
        assert claimed is not None
        assert claimed["status"] == "executing"
        assert store.claim_fuzzy_modify_plan(plan["plan_id"], stale_after_seconds=60.0) is None

        store.get_connection().execute(
            "UPDATE memory_fuzzy_modify_plans SET updated_at = 0 WHERE plan_id = ?",
            (plan["plan_id"],),
        )
        store.get_connection().commit()
        reclaimed = store.claim_fuzzy_modify_plan(plan["plan_id"], stale_after_seconds=60.0)
        assert reclaimed is not None
        assert reclaimed["status"] == "executing"
    finally:
        store.close()


def test_stale_mark_rollback_restores_previous_source(tmp_path):
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        paragraph_hash = store.add_paragraph("小明喜欢咖啡", source="person_fact:person-1")
        relation_hash = store.add_relation("小明", "喜欢", "咖啡", source_paragraph=paragraph_hash)
        previous = store.upsert_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
            reason="反馈纠错",
            source_type="feedback_correction",
            source_id="task-1",
            source_operation_id=f"feedback_correction:task-1:{paragraph_hash}:{relation_hash}",
        )
        assert previous is not None
        operation_id = f"plan-1:{paragraph_hash}:{relation_hash}"
        store.upsert_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
            reason="记忆修正",
            source_type="memory_correction",
            source_id="plan-1",
            source_operation_id=operation_id,
        )

        rollback = store.rollback_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
            expected_source_type="memory_correction",
            expected_source_id="plan-1",
            expected_source_operation_id=operation_id,
            previous_mark=previous,
        )

        assert rollback["action"] == "restored"
        restored = store.get_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
        )
        assert restored is not None
        assert restored["source_type"] == "feedback_correction"
        assert restored["source_id"] == "task-1"
    finally:
        store.close()


def test_stale_mark_rollback_skips_source_mismatch(tmp_path):
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        paragraph_hash = store.add_paragraph("小明喜欢咖啡", source="person_fact:person-1")
        relation_hash = store.add_relation("小明", "喜欢", "咖啡", source_paragraph=paragraph_hash)
        operation_id = f"plan-1:{paragraph_hash}:{relation_hash}"
        store.upsert_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
            reason="记忆修正",
            source_type="memory_correction",
            source_id="plan-1",
            source_operation_id=operation_id,
        )
        store.upsert_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
            reason="后续反馈纠错",
            source_type="feedback_correction",
            source_id="task-2",
            source_operation_id=f"feedback_correction:task-2:{paragraph_hash}:{relation_hash}",
        )

        rollback = store.rollback_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
            expected_source_type="memory_correction",
            expected_source_id="plan-1",
            expected_source_operation_id=operation_id,
            previous_mark=None,
        )

        assert rollback["action"] == "skipped_due_to_source_mismatch"
        current = store.get_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
        )
        assert current is not None
        assert current["source_type"] == "feedback_correction"
    finally:
        store.close()


def test_fuzzy_modify_paragraph_cascade_marks_relation_inactive_and_records_entity(tmp_path):
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        paragraph_hash = store.add_paragraph("小明喜欢咖啡", source="person_fact:person-1")
        relation_hash = store.add_relation("小明", "喜欢", "咖啡", source_paragraph=paragraph_hash)
        entity_hash = store.add_entity("小明", source_paragraph=paragraph_hash)

        kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
        kernel.metadata_store = store
        result = kernel._mark_fuzzy_modify_target_superseded(
            operation={"target_type": "paragraph", "hash": paragraph_hash, "reason": "改成喜欢茶"},
            change_id="plan-cascade-1",
            changed_at=1000.0,
            changed_by="pytest",
            replacement_hashes=[],
            plan_id="plan-cascade-1",
        )

        cascade = result["cascade"]
        assert cascade["relations_marked_inactive"][0]["relation_hash"] == relation_hash
        assert store.get_relation_status_batch([relation_hash])[relation_hash]["is_inactive"] is True
        assert cascade["impacted_entities"][0]["entity_hash"] == entity_hash
        assert store.get_entity_status_batch([entity_hash])[entity_hash]["is_deleted"] is False
    finally:
        store.close()


def test_fuzzy_modify_paragraph_cascade_marks_stale_when_relation_has_other_support(tmp_path):
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        old_paragraph_hash = store.add_paragraph("小明喜欢咖啡", source="person_fact:person-1")
        other_paragraph_hash = store.add_paragraph("朋友也说小明喜欢咖啡", source="person_fact:person-1")
        relation_hash = store.add_relation("小明", "喜欢", "咖啡", source_paragraph=old_paragraph_hash)
        store.link_paragraph_relation(other_paragraph_hash, relation_hash)

        kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
        kernel.metadata_store = store
        result = kernel._mark_fuzzy_modify_target_superseded(
            operation={"target_type": "paragraph", "hash": old_paragraph_hash, "reason": "旧段落不准确"},
            change_id="plan-cascade-2",
            changed_at=1000.0,
            changed_by="pytest",
            replacement_hashes=[],
            plan_id="plan-cascade-2",
        )

        cascade = result["cascade"]
        assert cascade["relations_marked_stale"][0]["relation_hash"] == relation_hash
        assert store.get_relation_status_batch([relation_hash])[relation_hash]["is_inactive"] is False
        mark = store.get_paragraph_stale_relation_mark(
            paragraph_hash=old_paragraph_hash,
            relation_hash=relation_hash,
        )
        assert mark is not None
        assert mark["source_type"] == "memory_correction"
        assert mark["source_id"] == "plan-cascade-2"
    finally:
        store.close()


def test_fuzzy_modify_paragraph_cascade_ignores_superseded_other_support(tmp_path):
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        old_paragraph_hash = store.add_paragraph("小明喜欢咖啡", source="person_fact:person-1")
        superseded_paragraph_hash = store.add_paragraph("旧摘要也说小明喜欢咖啡", source="person_fact:person-1")
        relation_hash = store.add_relation("小明", "喜欢", "咖啡", source_paragraph=old_paragraph_hash)
        store.link_paragraph_relation(superseded_paragraph_hash, relation_hash)
        store.update_paragraph_metadata(
            superseded_paragraph_hash,
            {
                "memory_change": {
                    "change_id": "previous-correction",
                    "change_type": "mark_superseded",
                    "valid_to": 1000.0,
                }
            },
        )

        kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
        kernel.metadata_store = store
        result = kernel._mark_fuzzy_modify_target_superseded(
            operation={"target_type": "paragraph", "hash": old_paragraph_hash, "reason": "旧段落不准确"},
            change_id="plan-cascade-expired-support",
            changed_at=2000.0,
            changed_by="pytest",
            replacement_hashes=[],
            plan_id="plan-cascade-expired-support",
        )

        cascade = result["cascade"]
        assert cascade["relations_marked_inactive"][0]["relation_hash"] == relation_hash
        assert cascade["relations_marked_stale"] == []
        assert store.get_relation_status_batch([relation_hash])[relation_hash]["is_inactive"] is True
        assert store.get_paragraph_stale_relation_mark(
            paragraph_hash=old_paragraph_hash,
            relation_hash=relation_hash,
        ) is None
    finally:
        store.close()


def test_fuzzy_modify_paragraph_cascade_skips_pinned_relation(tmp_path):
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        paragraph_hash = store.add_paragraph("小明喜欢咖啡", source="person_fact:person-1")
        relation_hash = store.add_relation("小明", "喜欢", "咖啡", source_paragraph=paragraph_hash)
        store.update_relations_protection([relation_hash], is_pinned=True)

        kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
        kernel.metadata_store = store
        result = kernel._mark_fuzzy_modify_target_superseded(
            operation={"target_type": "paragraph", "hash": paragraph_hash, "reason": "旧段落不准确"},
            change_id="plan-cascade-3",
            changed_at=1000.0,
            changed_by="pytest",
            replacement_hashes=[],
            plan_id="plan-cascade-3",
        )

        cascade = result["cascade"]
        assert cascade["relations_skipped"][0]["action"] == "skipped_protected"
        assert store.get_relation_status_batch([relation_hash])[relation_hash]["is_inactive"] is False
        assert store.get_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
        ) is None
    finally:
        store.close()


def test_fuzzy_modify_paragraph_cascade_skips_temporarily_protected_relation(tmp_path):
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        paragraph_hash = store.add_paragraph("小明喜欢咖啡", source="person_fact:person-1")
        relation_hash = store.add_relation("小明", "喜欢", "咖啡", source_paragraph=paragraph_hash)
        store.update_relations_protection([relation_hash], protected_until=4_102_444_800.0)

        kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
        kernel.metadata_store = store
        result = kernel._mark_fuzzy_modify_target_superseded(
            operation={"target_type": "paragraph", "hash": paragraph_hash, "reason": "旧段落不准确"},
            change_id="plan-cascade-4",
            changed_at=1000.0,
            changed_by="pytest",
            replacement_hashes=[],
            plan_id="plan-cascade-4",
        )

        cascade = result["cascade"]
        assert cascade["relations_skipped"][0]["reason"] == "relation_is_temporarily_protected"
        assert store.get_relation_status_batch([relation_hash])[relation_hash]["is_inactive"] is False
        assert store.get_paragraph_stale_relation_mark(
            paragraph_hash=paragraph_hash,
            relation_hash=relation_hash,
        ) is None
    finally:
        store.close()


@pytest.mark.asyncio
async def test_fuzzy_modify_rollback_removes_owned_stale_mark(tmp_path):
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        old_paragraph_hash = store.add_paragraph(
            "小明喜欢咖啡",
            source="person_fact:person-1",
            metadata={"source_type": "person_fact", "keep": True},
        )
        other_paragraph_hash = store.add_paragraph("朋友也说小明喜欢咖啡", source="person_fact:person-1")
        relation_hash = store.add_relation("小明", "喜欢", "咖啡", source_paragraph=old_paragraph_hash)
        store.link_paragraph_relation(other_paragraph_hash, relation_hash)
        plan = store.create_fuzzy_modify_plan(
            request_text="小明不喜欢咖啡",
            scope="person_profile",
            target_person_id="person-1",
            plan={"operations": [{"action": "mark_superseded", "target_type": "paragraph", "hash": old_paragraph_hash}]},
            preview={"candidates": [{"hash": old_paragraph_hash}]},
            confidence=1.0,
            requested_by="pytest",
        )

        kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
        kernel.metadata_store = store
        kernel._rebuild_graph_from_metadata = lambda: None  # type: ignore[method-assign]
        kernel._persist = lambda: None  # type: ignore[method-assign]
        superseded = kernel._mark_fuzzy_modify_target_superseded(
            operation={"target_type": "paragraph", "hash": old_paragraph_hash, "reason": "旧段落不准确"},
            change_id=plan["plan_id"],
            changed_at=1000.0,
            changed_by="pytest",
            replacement_hashes=[],
            plan_id=plan["plan_id"],
        )
        store.update_fuzzy_modify_plan(
            plan["plan_id"],
            status="executed",
            execution={"stored_ids": [], "superseded_targets": [superseded]},
            executed_at=1001.0,
        )
        assert store.get_paragraph_stale_relation_mark(
            paragraph_hash=old_paragraph_hash,
            relation_hash=relation_hash,
        ) is not None

        rollback = await kernel._rollback_fuzzy_modify_action(plan_id=plan["plan_id"], requested_by="pytest")

        assert rollback["success"] is True
        assert store.get_paragraph_stale_relation_mark(
            paragraph_hash=old_paragraph_hash,
            relation_hash=relation_hash,
        ) is None
        restored = store.get_paragraph(old_paragraph_hash)
        assert restored is not None
        assert restored["metadata"] == {"source_type": "person_fact", "keep": True}
        assert rollback["rollback"]["stale_marks_deleted"][0]["relation_hash"] == relation_hash
    finally:
        store.close()
