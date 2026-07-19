from pathlib import Path

import pytest

from src.A_memorix.core.storage.metadata_fact import FACT_SCHEMA_STATEMENTS
from src.A_memorix.core.storage.metadata_store import MetadataStore


def _fact_store(tmp_path: Path) -> MetadataStore:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    cursor = store._conn.cursor()
    for statement in FACT_SCHEMA_STATEMENTS:
        cursor.execute(statement)
    store._conn.commit()
    return store


def test_fact_claim_exact_retry_reuses_identity_and_accumulates_evidence(tmp_path: Path) -> None:
    store = _fact_store(tmp_path)
    try:
        first = store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="diet",
            value_text="不是素食主义者",
            polarity="negative",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id="paragraph-1",
            observed_at=10.0,
        )
        second = store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="DIET",
            value_text="  不是素食主义者  ",
            polarity="negative",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id="paragraph-2",
            observed_at=20.0,
        )
        duplicate = store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="diet",
            value_text="不是素食主义者",
            polarity="negative",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id="paragraph-2",
            observed_at=30.0,
        )

        assert first["claim_id"] == second["claim_id"]
        assert duplicate["claim_id"] == second["claim_id"]
        assert first["created"] is True
        assert second["reinforced"] is True
        assert duplicate["idempotent"] is True
        assert store.query("SELECT COUNT(*) AS c FROM fact_claims")[0]["c"] == 1
        assert len(store.get_fact_evidence(first["claim_id"])) == 2
        assert [item["transition_type"] for item in store.get_fact_transitions(first["claim_id"])] == [
            "assert",
            "reinforce",
        ]
    finally:
        store.close()


def test_refuting_evidence_is_audited_without_implicitly_changing_truth_state(tmp_path: Path) -> None:
    store = _fact_store(tmp_path)
    try:
        claim = store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="occupation",
            value_text="用户是测试工程师。",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id="paragraph-support",
            observed_at=10.0,
        )

        refute = store.add_fact_evidence(
            claim["claim_id"],
            evidence_type="paragraph",
            evidence_id="paragraph-refute",
            stance="refute",
            reason="相反证据待人工裁决",
            observed_at=20.0,
        )
        duplicate = store.add_fact_evidence(
            claim["claim_id"],
            evidence_type="paragraph",
            evidence_id="paragraph-refute",
            stance="refute",
            observed_at=30.0,
        )

        assert refute["created"] is True
        assert duplicate["idempotent"] is True
        assert store.get_fact_claim(claim["claim_id"])["status"] == "active"
        assert [item["stance"] for item in store.get_fact_evidence(claim["claim_id"])] == [
            "refute",
            "support",
        ]
        assert [item["transition_type"] for item in store.get_fact_transitions(claim["claim_id"])] == [
            "assert",
            "refute_evidence",
        ]
        with pytest.raises(ValueError, match="add_fact_evidence"):
            store.upsert_fact_claim(
                scope_type="person",
                scope_id="person-1",
                fact_key="occupation",
                value_text="用户是测试工程师。",
                cardinality="single",
                evidence_type="paragraph",
                evidence_id="paragraph-invalid-refute",
                evidence_stance="refute",
            )
        with pytest.raises(ValueError, match="cardinality"):
            store.upsert_fact_claim(
                scope_type="person",
                scope_id="person-1",
                fact_key="occupation",
                value_text="用户是测试工程师。",
                cardinality="set",
                evidence_type="paragraph",
                evidence_id="paragraph-other",
            )
    finally:
        store.close()


def test_single_fact_requires_explicit_supersession(tmp_path: Path) -> None:
    store = _fact_store(tmp_path)
    try:
        old = store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="residence",
            value_text="现居北京",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id="paragraph-old",
            observed_at=10.0,
        )
        ambiguous = store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="residence",
            value_text="现居上海",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id="paragraph-new",
            observed_at=20.0,
        )

        assert old["status"] == "active"
        assert ambiguous["status"] == "conflicted"
        assert ambiguous["conflicting_claim_ids"] == [old["claim_id"]]
        assert [item["claim_id"] for item in store.list_current_person_fact_claims("person-1")] == [
            old["claim_id"]
        ]

        replacement = store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="residence",
            value_text="现居上海",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id="paragraph-correction",
            supersedes_claim_ids=[old["claim_id"]],
            reason="用户明确更正",
            observed_at=30.0,
        )

        assert replacement["status"] == "active"
        assert replacement["superseded_claim_ids"] == [old["claim_id"]]
        assert store.get_fact_claim(old["claim_id"])["status"] == "superseded"
        assert [item["claim_id"] for item in store.list_current_person_fact_claims("person-1")] == [
            replacement["claim_id"]
        ]

        store.retract_fact_claim(replacement["claim_id"], reason="回滚新值", retracted_at=40.0)
        restored = store.restore_fact_claim(old["claim_id"], reason="回滚旧值", restored_at=40.0)

        assert restored["status"] == "active"
        assert [item["claim_id"] for item in store.list_current_person_fact_claims("person-1")] == [
            old["claim_id"]
        ]
    finally:
        store.close()


def test_fact_projection_order_is_stable_under_repeated_reads(tmp_path: Path) -> None:
    store = _fact_store(tmp_path)
    try:
        for index in reversed(range(100)):
            store.upsert_fact_claim(
                scope_type="person",
                scope_id="person-1",
                fact_key=f"stable-{index}",
                value_text=f"稳定事实{index}",
                cardinality="set",
                evidence_type="paragraph",
                evidence_id=f"paragraph-{index}",
                observed_at=float(index + 1),
            )

        baseline = [item["claim_id"] for item in store.list_current_person_fact_claims("person-1")]
        store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="stable-50",
            value_text="稳定事实50",
            cardinality="set",
            evidence_type="paragraph",
            evidence_id="paragraph-50-reinforce",
            observed_at=1000.0,
        )
        for _ in range(100):
            current = [item["claim_id"] for item in store.list_current_person_fact_claims("person-1")]
            assert current == baseline
        assert len(baseline) == 100
    finally:
        store.close()


def test_person_fact_backfill_preserves_content_without_semantic_guessing(tmp_path: Path) -> None:
    store = _fact_store(tmp_path)
    try:
        paragraph_hashes = [
            store.add_paragraph(
                content=text,
                source="person_fact:person-1",
                metadata={"person_id": "person-1", "evidence_source": "user_supported"},
                knowledge_type="factual",
            )
            for text in ("用户不是素食主义者。", "用户的职业是测试工程师。", "用户居住在旧金山。")
        ]

        result = store.backfill_person_fact_claims()
        claims = store.list_person_profile_fact_claims("person-1")
        transition_counts = {
            item["claim_id"]: len(store.get_fact_transitions(item["claim_id"])) for item in claims
        }
        retry = store.backfill_person_fact_claims()

        assert result == {"scanned": 3, "migrated": 3, "retracted": 0}
        assert retry == result
        assert store.list_current_person_fact_claims("person-1") == []
        assert {item["value_text"] for item in claims} == {
            "用户不是素食主义者。",
            "用户的职业是测试工程师。",
            "用户居住在旧金山。",
        }
        assert {item["authority"] for item in claims} == {"summary_derived"}
        assert {item["stability"] for item in claims} == {"uncertain"}
        assert {item["profile_section"] for item in claims} == {"uncertain_notes"}
        assert {
            item["claim_id"]: len(store.get_fact_transitions(item["claim_id"])) for item in claims
        } == transition_counts
        assert all(str(item["fact_key"]).startswith("statement:") for item in claims)
        for paragraph_hash in paragraph_hashes:
            paragraph = store.get_paragraph(paragraph_hash)
            assert len(paragraph["metadata"]["fact_claim_ids"]) == 1
    finally:
        store.close()


def test_fact_evidence_detach_and_restore_are_transaction_friendly(tmp_path: Path) -> None:
    store = _fact_store(tmp_path)
    try:
        claim = store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="occupation",
            value_text="用户是测试工程师。",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id="paragraph-1",
            observed_at=10.0,
        )
        store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="occupation",
            value_text="用户是测试工程师。",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id="paragraph-2",
            observed_at=20.0,
        )

        with store.transaction(immediate=True) as connection:
            detached = store.detach_fact_evidence_for_paragraphs(
                ["paragraph-1", "paragraph-2"],
                reason="paragraph_delete",
                conn=connection,
                detached_at=30.0,
            )

        assert detached["detached_evidence_count"] == 2
        assert detached["retracted_claim_ids"] == [claim["claim_id"]]
        assert store.get_fact_claim(claim["claim_id"])["status"] == "retracted"
        assert store.get_fact_evidence(claim["claim_id"]) == []

        with store.transaction(immediate=True) as connection:
            restored = store.restore_fact_evidence_snapshot(
                detached,
                conn=connection,
                reason="paragraph_delete_rollback",
                restored_at=40.0,
            )

        assert restored["restored_claim_ids"] == [claim["claim_id"]]
        assert restored["restored_evidence_count"] == 2
        assert store.get_fact_claim(claim["claim_id"])["status"] == "active"
        assert len(store.get_fact_evidence(claim["claim_id"])) == 2
        assert [item["transition_type"] for item in store.get_fact_transitions(claim["claim_id"])][-2:] == [
            "detach_evidence",
            "restore_evidence",
        ]
    finally:
        store.close()


def test_detaching_one_of_multiple_supports_does_not_retract_claim(tmp_path: Path) -> None:
    store = _fact_store(tmp_path)
    try:
        claim = store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="occupation",
            value_text="用户是测试工程师。",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id="paragraph-1",
            observed_at=10.0,
        )
        store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="occupation",
            value_text="用户是测试工程师。",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id="paragraph-2",
            observed_at=20.0,
        )

        detached = store.detach_fact_evidence_for_paragraphs(
            ["paragraph-1"],
            reason="single_evidence_superseded",
            detached_at=30.0,
        )

        assert detached["detached_evidence_count"] == 1
        assert detached["retracted_claim_ids"] == []
        assert store.get_fact_claim(claim["claim_id"])["status"] == "active"
        assert [item["evidence_id"] for item in store.get_fact_evidence(claim["claim_id"])] == ["paragraph-2"]
    finally:
        store.close()


def test_stale_evidence_snapshot_does_not_overwrite_newer_supersession(tmp_path: Path) -> None:
    store = _fact_store(tmp_path)
    try:
        old = store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="occupation",
            value_text="用户是测试工程师。",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id="paragraph-old",
            observed_at=10.0,
        )
        detached = store.detach_fact_evidence_for_paragraphs(
            ["paragraph-old"],
            reason="paragraph_delete",
            detached_at=20.0,
        )
        replacement = store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="occupation",
            value_text="用户是产品经理。",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id="paragraph-new",
            supersedes_claim_ids=[old["claim_id"]],
            reason="用户确认职业变更",
            observed_at=30.0,
        )

        restored = store.restore_fact_evidence_snapshot(
            detached,
            reason="late_delete_rollback",
            restored_at=40.0,
        )
        duplicate = store.restore_fact_evidence_snapshot(
            detached,
            reason="late_delete_rollback_retry",
            restored_at=50.0,
        )

        assert replacement["status"] == "active"
        assert store.get_fact_claim(old["claim_id"])["status"] == "superseded"
        assert restored == {"restored_claim_ids": [], "restored_evidence_count": 1}
        assert duplicate == {"restored_claim_ids": [], "restored_evidence_count": 0}
        assert len(store.get_fact_evidence(old["claim_id"])) == 1
    finally:
        store.close()


def test_physical_paragraph_purge_detaches_fact_evidence(tmp_path: Path) -> None:
    store = _fact_store(tmp_path)
    try:
        paragraph_hash = store.add_paragraph(
            "测试用户是测试工程师。",
            source="person_fact:person-1",
        )
        claim = store.upsert_fact_claim(
            scope_type="person",
            scope_id="person-1",
            fact_key="occupation",
            value_text="测试用户是测试工程师。",
            cardinality="single",
            evidence_type="paragraph",
            evidence_id=paragraph_hash,
        )

        assert store.mark_as_deleted([paragraph_hash], "paragraph", reason="test_delete") == 1
        assert store.physically_delete_paragraphs([paragraph_hash]) == 1

        assert store.get_paragraph(paragraph_hash) is None
        assert store.get_fact_evidence(claim["claim_id"]) == []
        assert store.get_fact_claim(claim["claim_id"])["status"] == "retracted"
    finally:
        store.close()
