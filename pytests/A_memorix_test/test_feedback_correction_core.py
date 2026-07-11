from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

IMPORT_ERROR: str | None = None

try:
    from src.A_memorix.core.retrieval.sparse_bm25 import SparseBM25Config, SparseBM25Index
    from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
    from src.A_memorix.core.utils import feedback_policy
except SystemExit as exc:
    IMPORT_ERROR = f"config initialization exited during import: {exc}"
    SparseBM25Config = None  # type: ignore[assignment]
    SparseBM25Index = None  # type: ignore[assignment]
    SDKMemoryKernel = None  # type: ignore[assignment]
    feedback_policy = None  # type: ignore[assignment]


pytestmark = pytest.mark.skipif(IMPORT_ERROR is not None, reason=IMPORT_ERROR or "")


@pytest.mark.asyncio
async def test_kernel_enqueue_feedback_task_delegates_to_metadata_store(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    def fake_enqueue_feedback_task(**kwargs):
        captured.update(kwargs)
        return {
            "id": 1,
            "query_tool_id": kwargs["query_tool_id"],
            "session_id": kwargs["session_id"],
            "query_timestamp": kwargs["query_timestamp"],
            "due_at": kwargs["due_at"],
            "query_snapshot": kwargs["query_snapshot"],
        }

    monkeypatch.setattr(
        feedback_policy,
        "_integration_config",
        lambda: SimpleNamespace(
            feedback_correction_enabled=True,
            feedback_correction_window_hours=12.0,
        ),
    )

    query_time = datetime(2026, 4, 9, 10, 30, 0)
    kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
    kernel.metadata_store = SimpleNamespace(enqueue_feedback_task=fake_enqueue_feedback_task)

    payload = await kernel.enqueue_feedback_task(
        query_tool_id="tool-query-1",
        session_id="session-1",
        query_timestamp=query_time,
        structured_content={"query": "Alice 喜欢什么", "hits": [{"hash": "relation-1"}]},
    )

    assert payload["success"] is True
    assert payload["queued"] is True
    assert captured["query_tool_id"] == "tool-query-1"
    assert captured["session_id"] == "session-1"
    assert captured["query_snapshot"]["query"] == "Alice 喜欢什么"
    assert captured["query_snapshot"]["hits"] == [{"hash": "relation-1"}]
    assert captured["due_at"] == pytest.approx(query_time.timestamp() + 12 * 3600, rel=0, abs=1e-6)


@pytest.mark.asyncio
async def test_kernel_enqueue_feedback_task_skipped_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feedback_policy,
        "_integration_config",
        lambda: SimpleNamespace(feedback_correction_enabled=False),
    )

    kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
    kernel.metadata_store = SimpleNamespace(enqueue_feedback_task=lambda **kwargs: kwargs)

    payload = await kernel.enqueue_feedback_task(
        query_tool_id="tool-query-2",
        session_id="session-1",
        query_timestamp=datetime.now(),
        structured_content={"hits": [{"hash": "relation-1"}]},
    )

    assert payload["success"] is False
    assert payload["reason"] == "feedback_correction_disabled"


@pytest.mark.asyncio
async def test_apply_feedback_decision_resolves_paragraph_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    action_logs: list[Dict[str, Any]] = []
    forgotten_hashes: list[str] = []
    ingested_payloads: list[Dict[str, Any]] = []
    stale_marks: list[Dict[str, Any]] = []
    paragraph_lookup_calls: list[list[str]] = []
    episode_sources: list[str] = []
    profile_refresh_ids: list[str] = []

    kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
    monkeypatch.setattr(
        feedback_policy,
        "_integration_config",
        lambda: SimpleNamespace(
            feedback_correction_auto_apply_threshold=0.85,
            feedback_correction_paragraph_mark_enabled=True,
            feedback_correction_episode_rebuild_enabled=True,
            feedback_correction_profile_refresh_enabled=True,
        ),
    )
    kernel.metadata_store = SimpleNamespace(
        get_paragraph_relations=lambda paragraph_hash: (
            [
                {
                    "hash": "relation-1",
                    "subject": "测试用户",
                    "predicate": "最喜欢的颜色是",
                    "object": "蓝色",
                }
            ]
            if paragraph_hash == "paragraph-1"
            else []
        ),
        get_relation_status_batch=lambda hashes: {
            str(hash_value): {"is_inactive": str(hash_value) in forgotten_hashes} for hash_value in hashes
        },
        get_paragraph_hashes_by_relation_hashes=lambda hashes: (
            paragraph_lookup_calls.append(list(hashes))
            or ({"relation-1": ["paragraph-1"]} if "relation-1" in hashes else {})
        ),
        get_paragraph_stale_relation_mark=lambda **kwargs: None,
        upsert_paragraph_stale_relation_mark=lambda **kwargs: stale_marks.append(kwargs) or kwargs,
        enqueue_episode_source_rebuild=lambda source, reason="": episode_sources.append(source) or True,
        enqueue_person_profile_refresh=lambda **kwargs: profile_refresh_ids.append(kwargs["person_id"]) or kwargs,
        get_paragraph=lambda paragraph_hash: (
            {"hash": "paragraph-1", "source": "chat_feedback_test_seed:session-1"}
            if paragraph_hash == "paragraph-1"
            else None
        ),
        append_feedback_action_log=lambda **kwargs: action_logs.append(kwargs),
    )
    kernel._apply_v5_relation_action = lambda *, action, hashes, strength=1.0: (  # type: ignore[method-assign]
        forgotten_hashes.extend([str(item) for item in hashes]),
        {"success": True, "action": action, "hashes": list(hashes), "strength": strength},
    )[1]
    kernel._resolve_feedback_related_person_ids = lambda **kwargs: ["person-1"]  # type: ignore[method-assign]
    kernel._query_relation_rows_by_hashes = lambda relation_hashes, include_inactive=False: [  # type: ignore[method-assign]
        {
            "hash": "relation-1",
            "subject": "测试用户",
            "predicate": "最喜欢的颜色是",
            "object": "蓝色",
        }
    ]

    async def _fake_ingest_feedback_relations(**kwargs):
        ingested_payloads.append(kwargs)
        return {"success": True, "stored_ids": ["relation-2"]}

    kernel._ingest_feedback_relations = _fake_ingest_feedback_relations  # type: ignore[method-assign]

    payload = await kernel._apply_feedback_decision(
        task_id=1,
        query_tool_id="tool-query-1",
        session_id="session-1",
        decision={
            "decision": "correct",
            "confidence": 0.97,
            "target_hashes": ["paragraph-1"],
            "corrected_relations": [
                {
                    "subject": "测试用户",
                    "predicate": "最喜欢的颜色是",
                    "object": "绿色",
                    "confidence": 0.99,
                }
            ],
            "reason": "用户明确纠正为绿色",
        },
        hit_map={
            "paragraph-1": {
                "hash": "paragraph-1",
                "type": "paragraph",
                "content": "测试用户 最喜欢的颜色是 蓝色",
                "linked_relation_hashes": ["relation-1"],
            }
        },
    )

    assert payload["applied"] is True
    assert payload["relation_hashes"] == ["relation-1"]
    assert forgotten_hashes == ["relation-1"]
    assert ingested_payloads[0]["relation_hashes"] == ["relation-1"]
    assert payload["stale_paragraph_hashes"] == ["paragraph-1"]
    assert "chat_feedback_test_seed:session-1" in payload["episode_rebuild_sources"]
    assert "chat_summary:session-1" in payload["episode_rebuild_sources"]
    assert payload["profile_refresh_person_ids"] == ["person-1"]
    assert stale_marks[0]["paragraph_hash"] == "paragraph-1"
    assert paragraph_lookup_calls == [["relation-1"]]
    assert {item["action_type"] for item in action_logs} == {
        "forget_relation",
        "ingest_correction",
        "mark_stale_paragraph",
        "enqueue_episode_rebuild",
        "enqueue_profile_refresh",
    }


def test_filter_active_relation_hits_removes_inactive_relations(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
    monkeypatch.setattr(
        feedback_policy,
        "_integration_config",
        lambda: SimpleNamespace(feedback_correction_paragraph_hard_filter_enabled=True),
    )
    kernel.metadata_store = SimpleNamespace(
        get_relation_status_batch=lambda hashes: {
            "r-active": {"is_inactive": False},
            "r-inactive": {"is_inactive": True},
            "r-para-inactive": {"is_inactive": True},
            "r-stale-active": {"is_inactive": False},
            "r-stale-inactive": {"is_inactive": True},
        },
        get_paragraph_relations=lambda paragraph_hash: (
            [{"hash": "r-para-inactive"}] if paragraph_hash == "p-inactive" else []
        ),
        get_paragraph_stale_relation_marks_batch=lambda paragraph_hashes: {
            "p-stale": [{"relation_hash": "r-stale-inactive"}],
            "p-restored": [{"relation_hash": "r-stale-active"}],
        },
    )

    hits = [
        {"hash": "r-active", "type": "relation", "content": "A 喜欢 B"},
        {"hash": "r-inactive", "type": "relation", "content": "A 讨厌 B"},
        {"hash": "p-1", "type": "paragraph", "content": "段落证据"},
        {"hash": "p-inactive", "type": "paragraph", "content": "失活段落证据"},
        {"hash": "p-stale", "type": "paragraph", "content": "被标脏段落"},
        {"hash": "p-restored", "type": "paragraph", "content": "恢复可见段落"},
    ]

    filtered = kernel._filter_active_relation_hits(hits)

    assert [item["hash"] for item in filtered] == ["r-active", "p-1", "p-restored"]


def test_sparse_relation_search_requests_active_only() -> None:
    captured: Dict[str, Any] = {}

    class FakeMetadataStore:
        def fts_search_relations_bm25(self, **kwargs):
            captured.update(kwargs)
            return []

    index = SparseBM25Index(
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
        config=SparseBM25Config(enabled=True, lazy_load=False),
    )
    index._loaded = True
    index._conn = object()  # type: ignore[assignment]

    result = index.search_relations("测试纠错", k=5)

    assert result == []
    assert captured["include_inactive"] is False


@pytest.mark.asyncio
async def test_feedback_task_rollback_restores_snapshots_and_requeues_followups() -> None:
    action_logs: list[Dict[str, Any]] = []
    queued_sources: list[str] = []
    queued_profiles: list[str] = []
    deleted_marks: list[tuple[str, str]] = []
    deleted_paragraphs: list[str] = []
    relation_statuses: Dict[str, Dict[str, Any]] = {
        "rel-old": {
            "is_inactive": True,
            "weight": 0.0,
            "is_pinned": False,
            "protected_until": 0.0,
            "last_reinforced": None,
            "inactive_since": 1.0,
        },
        "rel-new": {
            "is_inactive": False,
            "weight": 1.0,
            "is_pinned": False,
            "protected_until": 0.0,
            "last_reinforced": None,
            "inactive_since": None,
        },
    }
    current_task: Dict[str, Any] = {
        "id": 1,
        "query_tool_id": "tool-query-rollback",
        "session_id": "session-1",
        "status": "applied",
        "rollback_status": "none",
        "query_snapshot": {"query": "测试用户最喜欢的颜色是什么"},
        "decision_payload": {"decision": "correct", "confidence": 0.97},
        "rollback_plan": {
            "forgotten_relations": [
                {
                    "hash": "rel-old",
                    "subject": "测试用户",
                    "predicate": "最喜欢的颜色是",
                    "object": "蓝色",
                    "before_status": {
                        "is_inactive": False,
                        "weight": 0.8,
                        "is_pinned": False,
                        "protected_until": 0.0,
                        "last_reinforced": None,
                        "inactive_since": None,
                    },
                }
            ],
            "corrected_write": {
                "paragraph_hashes": ["paragraph-new"],
                "corrected_relations": [
                    {
                        "hash": "rel-new",
                        "subject": "测试用户",
                        "predicate": "最喜欢的颜色是",
                        "object": "绿色",
                        "existed_before": False,
                        "before_status": {},
                    }
                ],
            },
            "stale_marks": [{"paragraph_hash": "paragraph-old", "relation_hash": "rel-old"}],
            "episode_sources": ["chat_summary:session-1"],
            "profile_person_ids": ["person-1"],
        },
    }

    class _Conn:
        def cursor(self):
            return self

        def execute(self, *_args, **_kwargs):
            return self

        def commit(self):
            return None

    metadata_store = SimpleNamespace(
        get_feedback_task_by_id=lambda task_id: current_task if int(task_id) == 1 else None,
        mark_feedback_task_rollback_running=lambda **kwargs: (
            current_task.update({"rollback_status": "running"}) or current_task
        ),
        finalize_feedback_task_rollback=lambda **kwargs: (
            current_task.update(
                {
                    "rollback_status": kwargs["rollback_status"],
                    "rollback_result": kwargs.get("rollback_result") or {},
                    "rollback_error": kwargs.get("rollback_error", ""),
                }
            )
            or current_task
        ),
        get_relation_status_batch=lambda hashes: {
            hash_value: dict(relation_statuses[hash_value]) for hash_value in hashes if hash_value in relation_statuses
        },
        restore_relation_status_from_snapshot=lambda hash_value, snapshot: (
            relation_statuses.update({hash_value: dict(snapshot)}) or dict(snapshot)
        ),
        append_feedback_action_log=lambda **kwargs: action_logs.append(kwargs),
        mark_as_deleted=lambda hashes, type_: deleted_paragraphs.extend(list(hashes)) or len(list(hashes)),
        get_paragraph=lambda paragraph_hash: {"hash": paragraph_hash, "source": "chat_summary:session-1"},
        get_connection=lambda: _Conn(),
        delete_external_memory_refs_by_paragraphs=lambda hashes: [
            {"paragraph_hash": str(hash_value), "external_id": f"external:{hash_value}"} for hash_value in hashes
        ],
        update_relations_protection=lambda hashes, **kwargs: None,
        mark_relations_inactive=lambda hashes, inactive_since=None: [
            relation_statuses.__setitem__(
                hash_value,
                {
                    **relation_statuses.get(hash_value, {}),
                    "is_inactive": True,
                    "inactive_since": inactive_since,
                },
            )
            for hash_value in hashes
        ],
        rollback_paragraph_stale_relation_mark=lambda **kwargs: (
            deleted_marks.append((kwargs["paragraph_hash"], kwargs["relation_hash"]))
            or {
                "success": True,
                "action": "deleted",
                "paragraph_hash": kwargs["paragraph_hash"],
                "relation_hash": kwargs["relation_hash"],
            }
        ),
        enqueue_episode_source_rebuild=lambda source, reason="": queued_sources.append(source) or True,
        enqueue_person_profile_refresh=lambda **kwargs: queued_profiles.append(kwargs["person_id"]) or kwargs,
        list_feedback_action_logs=lambda task_id: action_logs if int(task_id) == 1 else [],
    )

    kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
    kernel.metadata_store = metadata_store

    async def _noop_initialize() -> None:
        return None

    kernel.initialize = _noop_initialize  # type: ignore[method-assign]
    kernel._rebuild_graph_from_metadata = lambda: None  # type: ignore[method-assign]
    kernel._persist = lambda: None  # type: ignore[method-assign]

    payload = await kernel._rollback_feedback_task(
        task_id=1,
        requested_by="pytest",
        reason="manual rollback",
    )

    assert payload["success"] is True
    assert relation_statuses["rel-old"]["is_inactive"] is False
    assert relation_statuses["rel-new"]["is_inactive"] is True
    assert deleted_paragraphs == ["paragraph-new"]
    assert deleted_marks == [("paragraph-old", "rel-old")]
    assert queued_sources == ["chat_summary:session-1"]
    assert queued_profiles == ["person-1"]
    assert current_task["rollback_status"] == "rolled_back"
    assert {item["action_type"] for item in action_logs} >= {
        "rollback_restore_relation",
        "rollback_revert_corrected_relation",
        "rollback_delete_correction_paragraph",
        "rollback_clear_stale_mark",
        "rollback_enqueue_episode_rebuild",
        "rollback_enqueue_profile_refresh",
    }


def test_fuzzy_modify_candidate_filter_blocks_protected_relation() -> None:
    kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
    kernel.metadata_store = SimpleNamespace(
        get_relation=lambda hash_value, include_inactive=True: {"hash": hash_value},
        get_relation_status_batch=lambda hashes: {
            hashes[0]: {
                "is_inactive": False,
                "is_pinned": True,
                "protected_until": 0.0,
            }
        },
    )

    candidate = {"target_type": "relation", "hash": "relation-1"}
    assert kernel._is_fuzzy_modify_candidate_mutable(candidate, {"deletable": True}) is False


def test_fuzzy_modify_plan_logs_candidate_miss(caplog: pytest.LogCaptureFixture) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path("."), config={})

    with caplog.at_level("WARNING"):
        plan = kernel._normalize_fuzzy_modify_plan(
            {
                "confidence": 0.9,
                "operations": [
                    {
                        "action": "mark_superseded",
                        "candidate_id": "paragraph:missing",
                        "hash": "missing",
                    }
                ],
            },
            request_text="修正记忆",
            scope="person_profile",
            person_id="person-1",
            chat_id="",
            candidates=[{"candidate_id": "paragraph:known", "target_type": "paragraph", "hash": "known"}],
        )

    assert plan["operations"] == []
    assert "候选集外" in caplog.text


def test_fuzzy_modify_superseded_change_type_matches_operation() -> None:
    previous_metadata = {"source_type": "person_fact"}
    updated_metadata: dict[str, Any] = {}
    kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
    kernel.metadata_store = SimpleNamespace(
        get_paragraph=lambda hash_value: {"hash": hash_value, "metadata": previous_metadata},
        update_paragraph_metadata=lambda hash_value, patch, merge=True: (
            updated_metadata.update(patch) or {**previous_metadata, **updated_metadata}
        ),
        get_paragraph_relations=lambda paragraph_hash: [],
        get_paragraph_entities=lambda paragraph_hash: [],
        get_relation_status_batch=lambda hashes: {},
    )

    result = kernel._mark_fuzzy_modify_target_superseded(
        operation={"target_type": "paragraph", "hash": "paragraph-1"},
        change_id="fuzzy-1",
        changed_at=1000.0,
        changed_by="pytest",
        replacement_hashes=["new-paragraph"],
        plan_id="fuzzy-1",
    )

    assert result["updated_metadata"]["memory_change"]["change_type"] == "mark_superseded"


@pytest.mark.asyncio
async def test_fuzzy_modify_execute_recovers_stale_executing_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    plan_updates: list[dict[str, Any]] = []
    kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
    kernel.metadata_store = SimpleNamespace(
        get_fuzzy_modify_plan=lambda plan_id: {
            "plan_id": plan_id,
            "status": "executing",
            "confidence": 1.0,
            "execution": {"attempt": {"status": "executing", "started_at": 1.0}},
            "plan": {"operations": []},
        },
        claim_fuzzy_modify_plan=lambda plan_id: {
            "plan_id": plan_id,
            "status": "executing",
            "confidence": 1.0,
            "execution": {"attempt": {"status": "executing", "started_at": 1.0}},
            "plan": {"operations": []},
        },
        update_fuzzy_modify_plan=lambda plan_id, **kwargs: (
            plan_updates.append({"plan_id": plan_id, **kwargs}) or {"plan_id": plan_id, **kwargs}
        ),
    )

    async def fake_apply(**kwargs: Any) -> dict[str, Any]:
        return {"success": True, "stored_ids": ["new-hash"]}

    monkeypatch.setattr(kernel, "_apply_fuzzy_modify_plan", fake_apply)
    result = await kernel._execute_fuzzy_modify_action(plan_id="fuzzy-1", confirmed=True, requested_by="pytest")

    assert result["success"] is True
    assert plan_updates[0]["status"] == "executing"
    assert plan_updates[0]["execution"]["attempt"]["recovered_from_stale_executing"] is True
    assert plan_updates[-1]["status"] == "executed"
    assert plan_updates[-1]["execution"]["attempt"]["status"] == "finished"


@pytest.mark.asyncio
async def test_fuzzy_modify_rollback_reports_delete_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    plan_updates: list[dict[str, Any]] = []
    kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
    kernel.metadata_store = SimpleNamespace(
        get_fuzzy_modify_plan=lambda plan_id: {
            "plan_id": plan_id,
            "status": "executed",
            "execution": {"stored_ids": ["paragraph-1"]},
        },
        get_paragraph=lambda hash_value: {"hash": hash_value},
        get_relation=lambda hash_value: None,
        update_fuzzy_modify_plan=lambda plan_id, **kwargs: (
            plan_updates.append({"plan_id": plan_id, **kwargs}) or {"plan_id": plan_id, **kwargs}
        ),
    )

    async def fake_execute_delete_action(**kwargs):
        return {"success": False, "error": "delete failed"}

    monkeypatch.setattr(kernel, "_execute_delete_action", fake_execute_delete_action)

    result = await kernel._rollback_fuzzy_modify_action(plan_id="fuzzy-1", requested_by="pytest")

    assert result["success"] is False
    assert result["rollback"]["success"] is False
    assert result["error"] == "delete failed"
    assert plan_updates[-1]["status"] == "rollback_failed"
