from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

import importlib
import sys
import time

import pytest

from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.utils.episode_service import EpisodeService


class _DeterministicSegmentation:
    def __init__(self) -> None:
        self.calls: List[List[str]] = []

    @staticmethod
    def generation_signature() -> Dict[str, Any]:
        return {
            "segmentation_version": "outbox-test-v1",
            "mode": "deterministic",
            "temperature": 0.0,
            "max_tokens": 1024,
        }

    async def segment(self, **kwargs: Any) -> Dict[str, Any]:
        hashes = [str(item.get("hash", "") or "") for item in (kwargs.get("paragraphs") or [])]
        self.calls.append(hashes)
        return {
            "episodes": [
                {
                    "title": f"情景 {hashes[0]}",
                    "summary": "；".join(hashes),
                    "paragraph_hashes": hashes,
                    "participants": [],
                    "keywords": ["测试"],
                    "llm_confidence": 1.0,
                }
            ],
            "segmentation_model": "deterministic",
            "segmentation_version": "outbox-test-v1",
        }


def _service(store: Any, *, max_paragraphs: int = 20) -> tuple[EpisodeService, _DeterministicSegmentation]:
    segmentation = _DeterministicSegmentation()
    return (
        EpisodeService(
            metadata_store=store,
            plugin_config={
                "episode": {
                    "max_paragraphs_per_call": max_paragraphs,
                    "max_chars_per_call": 6000,
                    "source_time_window_hours": 24,
                }
            },
            segmentation_service=segmentation,
        ),
        segmentation,
    )


def _payload(source: str, paragraph_hash: str) -> Dict[str, Any]:
    return {
        "source": source,
        "title": "稳定快照",
        "summary": "稳定快照内容",
        "evidence_ids": [paragraph_hash],
        "paragraph_count": 1,
        "segmentation_model": "deterministic",
        "segmentation_version": "outbox-test-v1",
        "input_fingerprint": "stable-fingerprint",
    }


def test_source_revision_cas_rejects_stale_publish_and_keeps_old_snapshot(tmp_path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    source = "chat_summary:cas"
    try:
        paragraph_hash = store.add_paragraph(
            "来源级CAS测试",
            source=source,
            time_meta={"event_time": 100.0},
        )
        now = time.time() + 10.0
        store.enqueue_episode_source_rebuild(source, reason="initial", debounce_seconds=0.0, now=now)
        claim = store.claim_episode_source_rebuild_batch(
            generation_hash="generation-v1",
            limit=1,
            max_wait_seconds=0.0,
            now=now,
        )[0]
        first = store.publish_episode_source_rebuild(
            source,
            lease_token=claim["lease_token"],
            claimed_revision=claim["claimed_revision"],
            generation_hash="generation-v1",
            episodes_payloads=[_payload(source, paragraph_hash)],
            now=now,
        )
        assert first["published"] is True

        store.enqueue_episode_source_rebuild(source, reason="change-1", debounce_seconds=0.0, now=now + 1.0)
        assert len(store.query_episodes(source=source)) == 1
        stale_claim = store.claim_episode_source_rebuild_batch(
            generation_hash="generation-v1",
            limit=1,
            max_wait_seconds=0.0,
            now=now + 1.0,
        )[0]
        store.enqueue_episode_source_rebuild(source, reason="change-2", debounce_seconds=0.0, now=now + 2.0)
        stale = store.publish_episode_source_rebuild(
            source,
            lease_token=stale_claim["lease_token"],
            claimed_revision=stale_claim["claimed_revision"],
            generation_hash="generation-v1",
            episodes_payloads=[_payload(source, paragraph_hash)],
            now=now + 2.0,
        )

        assert stale == {
            "source": source,
            "published": False,
            "superseded": True,
            "episode_count": 0,
        }
        assert len(store.query_episodes(source=source)) == 1
        state = store.list_episode_source_rebuilds(limit=1)[0]
        assert state["status"] == "pending"
        assert int(state["desired_revision"]) > int(state["built_revision"])
    finally:
        store.close()


def test_expired_source_lease_is_reclaimed_without_accepting_old_token(tmp_path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    source = "chat_summary:lease"
    try:
        store.enqueue_episode_source_rebuild(source, reason="lease", debounce_seconds=0.0, now=100.0)
        first = store.claim_episode_source_rebuild_batch(
            generation_hash="generation-v1",
            limit=1,
            lease_seconds=1.0,
            max_wait_seconds=0.0,
            now=100.0,
        )[0]
        assert (
            store.claim_episode_source_rebuild_batch(
                generation_hash="generation-v1",
                limit=1,
                lease_seconds=1.0,
                max_wait_seconds=0.0,
                now=100.5,
            )
            == []
        )
        second = store.claim_episode_source_rebuild_batch(
            generation_hash="generation-v1",
            limit=1,
            lease_seconds=1.0,
            max_wait_seconds=0.0,
            now=102.0,
        )[0]

        assert second["lease_token"] != first["lease_token"]
        assert store.fail_episode_source_rebuild(
            source,
            lease_token=first["lease_token"],
            claimed_revision=first["claimed_revision"],
            error="old worker",
            now=102.0,
        ) is False
        assert store.fail_episode_source_rebuild(
            source,
            lease_token=second["lease_token"],
            claimed_revision=second["claimed_revision"],
            error="new worker",
            retry_backoff_seconds=0.0,
            now=102.0,
        ) is True
    finally:
        store.close()


def test_attempt_budget_is_isolated_by_revision_and_generation(tmp_path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    source = "chat_summary:retry-generation"
    try:
        store.enqueue_episode_source_rebuild(source, reason="initial", debounce_seconds=0.0, now=100.0)
        observed_retry_counts: List[int] = []
        for attempt in range(3):
            claim = store.claim_episode_source_rebuild_batch(
                generation_hash="generation-v1",
                limit=1,
                max_retry=3,
                max_wait_seconds=0.0,
                now=100.0 + attempt,
            )[0]
            observed_retry_counts.append(int(claim["retry_count"]))
            assert claim["retry_revision"] == claim["claimed_revision"]
            assert claim["retry_generation_hash"] == "generation-v1"
            assert store.fail_episode_source_rebuild(
                source,
                lease_token=claim["lease_token"],
                claimed_revision=claim["claimed_revision"],
                error=f"failure-{attempt}",
                retry_backoff_seconds=0.0,
                now=100.0 + attempt,
            ) is True

        assert observed_retry_counts == [0, 1, 2]
        assert store.claim_episode_source_rebuild_batch(
            generation_hash="generation-v1",
            limit=1,
            max_retry=3,
            max_wait_seconds=0.0,
            now=104.0,
        ) == []

        generation_claim = store.claim_episode_source_rebuild_batch(
            generation_hash="generation-v2",
            limit=1,
            max_retry=3,
            max_wait_seconds=0.0,
            now=104.0,
        )[0]
        assert generation_claim["retry_count"] == 0
        assert generation_claim["retry_generation_hash"] == "generation-v2"
        assert store.fail_episode_source_rebuild(
            source,
            lease_token=generation_claim["lease_token"],
            claimed_revision=generation_claim["claimed_revision"],
            error="generation-v2-failure",
            retry_backoff_seconds=300.0,
            now=104.0,
        ) is True

        previous_revision = int(generation_claim["claimed_revision"])
        store.enqueue_episode_source_rebuild(source, reason="new-data", debounce_seconds=0.0, now=105.0)
        revision_claim = store.claim_episode_source_rebuild_batch(
            generation_hash="generation-v2",
            limit=1,
            max_retry=3,
            max_wait_seconds=0.0,
            now=105.0,
        )[0]
        assert revision_claim["claimed_revision"] == previous_revision + 1
        assert revision_claim["retry_count"] == 0
        assert revision_claim["retry_revision"] == revision_claim["claimed_revision"]
    finally:
        store.close()


def test_attempt_budget_requires_at_least_one_attempt(tmp_path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    source = "chat_summary:single-attempt"
    try:
        store.enqueue_episode_source_rebuild(source, reason="initial", debounce_seconds=0.0, now=100.0)
        claim = store.claim_episode_source_rebuild_batch(
            generation_hash="generation-v1",
            limit=1,
            max_retry=1,
            max_wait_seconds=0.0,
            now=100.0,
        )[0]
        assert claim["retry_count"] == 0
        assert store.fail_episode_source_rebuild(
            source,
            lease_token=claim["lease_token"],
            claimed_revision=claim["claimed_revision"],
            error="single failure",
            retry_backoff_seconds=0.0,
            now=100.0,
        ) is True
        assert store.claim_episode_source_rebuild_batch(
            generation_hash="generation-v1",
            limit=1,
            max_retry=1,
            max_wait_seconds=0.0,
            now=101.0,
        ) == []

        with pytest.raises(ValueError, match="max_retry 必须至少为1"):
            store.claim_episode_source_rebuild_batch(
                generation_hash="generation-v1",
                limit=1,
                max_retry=0,
                max_wait_seconds=0.0,
                now=101.0,
            )
    finally:
        store.close()


def test_lease_renewal_extends_only_current_unchanged_claim(tmp_path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    source = "chat_summary:lease-heartbeat"
    try:
        store.enqueue_episode_source_rebuild(source, reason="lease", debounce_seconds=0.0, now=100.0)
        claim = store.claim_episode_source_rebuild_batch(
            generation_hash="generation-v1",
            limit=1,
            lease_seconds=1.0,
            max_wait_seconds=0.0,
            now=100.0,
        )[0]
        assert store.renew_episode_source_rebuild_lease(
            source,
            lease_token=claim["lease_token"],
            claimed_revision=claim["claimed_revision"],
            generation_hash="generation-v1",
            lease_seconds=1.0,
            now=100.8,
        ) is True
        assert store.claim_episode_source_rebuild_batch(
            generation_hash="generation-v1",
            limit=1,
            lease_seconds=1.0,
            max_wait_seconds=0.0,
            now=101.2,
        ) == []

        store.enqueue_episode_source_rebuild(source, reason="new-data", debounce_seconds=0.0, now=101.3)
        assert store.renew_episode_source_rebuild_lease(
            source,
            lease_token=claim["lease_token"],
            claimed_revision=claim["claimed_revision"],
            generation_hash="generation-v1",
            lease_seconds=1.0,
            now=101.4,
        ) is False
    finally:
        store.close()


def test_scoped_claim_does_not_consume_older_unrelated_source(tmp_path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        store.enqueue_episode_source_rebuild(
            "chat_summary:unrelated",
            reason="older",
            debounce_seconds=0.0,
            now=90.0,
        )
        store.enqueue_episode_source_rebuild(
            "chat_summary:target",
            reason="admin_target",
            debounce_seconds=0.0,
            now=100.0,
        )

        scoped_claim = store.claim_episode_source_rebuild_batch(
            generation_hash="generation-v1",
            sources=["chat_summary:target"],
            limit=1,
            max_wait_seconds=0.0,
            now=100.0,
        )
        assert [claim["source"] for claim in scoped_claim] == ["chat_summary:target"]

        global_claim = store.claim_episode_source_rebuild_batch(
            generation_hash="generation-v1",
            limit=1,
            max_wait_seconds=0.0,
            now=100.0,
        )
        assert [claim["source"] for claim in global_claim] == ["chat_summary:unrelated"]
    finally:
        store.close()


def test_materialization_config_change_claims_clean_source_without_data_revision(tmp_path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    source = "chat_summary:generation"
    original_service, _ = _service(store, max_paragraphs=20)
    changed_service, _ = _service(store, max_paragraphs=10)
    now = time.time() + 10.0
    try:
        paragraph_hash = store.add_paragraph(
            "物化配置版本测试",
            source=source,
            time_meta={"event_time": 100.0},
        )
        original_generation_hash = original_service.generation_hash()
        changed_generation_hash = changed_service.generation_hash()
        assert original_generation_hash != changed_generation_hash

        original_claim = store.claim_episode_source_rebuild_batch(
            generation_hash=original_generation_hash,
            limit=1,
            max_wait_seconds=0.0,
            now=now,
        )[0]
        assert store.publish_episode_source_rebuild(
            source,
            lease_token=original_claim["lease_token"],
            claimed_revision=original_claim["claimed_revision"],
            generation_hash=original_generation_hash,
            episodes_payloads=[_payload(source, paragraph_hash)],
            now=now,
        )["published"] is True
        clean_state = store.list_episode_source_rebuilds(limit=1)[0]
        assert clean_state["desired_revision"] == clean_state["built_revision"]

        changed_claim = store.claim_episode_source_rebuild_batch(
            generation_hash=changed_generation_hash,
            limit=1,
            max_wait_seconds=0.0,
            now=now + 1.0,
        )
        assert len(changed_claim) == 1
        assert changed_claim[0]["source"] == source
        assert changed_claim[0]["claimed_revision"] == clean_state["built_revision"]
    finally:
        store.close()


def test_source_grouping_is_batch_order_independent_and_removes_fragmentation() -> None:
    service, _ = _service(object())
    paragraphs = [
        {
            "hash": f"source-{source_index}-paragraph-{paragraph_index}",
            "source": f"chat_summary:source-{source_index}",
            "content": f"段落 {source_index}-{paragraph_index}",
            "event_time": float(paragraph_index),
        }
        for paragraph_index in range(20)
        for source_index in range(20)
    ]

    pending_batch_groups = sum(
        len(service.group_paragraphs(paragraphs[offset : offset + 20]))
        for offset in range(0, len(paragraphs), 20)
    )
    canonical_groups = service.group_paragraphs(paragraphs)
    reverse_groups = service.group_paragraphs(list(reversed(paragraphs)))

    def partition(groups: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        return {
            str(group["source"]): [str(item["hash"]) for item in group["paragraphs"]]
            for group in groups
        }

    assert pending_batch_groups == 400
    assert len(canonical_groups) == 20
    assert pending_batch_groups / len(canonical_groups) == 20.0
    assert partition(canonical_groups) == partition(reverse_groups)


def test_interval_sweep_frontier_survives_model_call_chunk_boundary() -> None:
    service = EpisodeService(
        metadata_store=object(),
        plugin_config={
            "episode": {
                "max_paragraphs_per_call": 2,
                "max_chars_per_call": 6000,
                "source_time_window_hours": 0,
            }
        },
        segmentation_service=_DeterministicSegmentation(),
    )
    source = "chat_summary:interval-frontier"
    paragraphs = [
        {
            "hash": "bridge",
            "source": source,
            "content": "跨越多个调用块的长事件",
            "event_time_start": 0.0,
            "event_time_end": 1000.0,
        },
        {"hash": "early", "source": source, "content": "早期事件", "event_time": 1.0},
        {"hash": "middle", "source": source, "content": "中期事件", "event_time": 500.0},
        {"hash": "late", "source": source, "content": "晚期事件", "event_time": 1050.0},
        {"hash": "detached", "source": source, "content": "独立事件", "event_time": 2000.0},
    ]

    groups = service.group_paragraphs(paragraphs)

    assert [[item["hash"] for item in group["paragraphs"]] for group in groups] == [
        ["bridge", "early"],
        ["middle", "late"],
        ["detached"],
    ]
    reversed_start, reversed_end, _, _ = service._compute_time_meta(
        [{"event_time_start": 200.0, "event_time_end": 100.0}]
    )
    assert (reversed_start, reversed_end) == (100.0, 200.0)


@pytest.mark.asyncio
async def test_full_source_planning_uses_one_batched_entity_query_for_400_paragraphs(tmp_path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    source = "chat_summary:sql-count"
    service, segmentation = _service(store)
    try:
        expected_hashes: List[str] = []
        for index in range(400):
            expected_hashes.append(
                store.add_paragraph(
                    f"SQL次数测试段落 {index}",
                    source=source,
                    time_meta={"event_time": float(index)},
                )
            )

        statements: List[str] = []
        store._conn.set_trace_callback(statements.append)
        plan = await service.plan_source_rebuild(source)
        store._conn.set_trace_callback(None)

        entity_queries = [
            sql
            for sql in statements
            if "JOIN entities" in sql and "paragraph_entities" in sql
        ]
        assigned = [
            str(paragraph_hash)
            for payload in plan["payloads"]
            for paragraph_hash in payload["evidence_ids"]
        ]
        assert len(entity_queries) == 1
        assert plan["group_count"] == 20
        assert plan["episode_count"] == 20
        assert len(segmentation.calls) == 20
        assert Counter(assigned) == Counter(expected_hashes)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_sync_rebuild_script_returns_failure_when_publish_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts_dir = Path(__file__).resolve().parents[2] / "src" / "A_memorix" / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    sys.modules.pop("rebuild_episodes", None)
    rebuild_script = importlib.import_module("rebuild_episodes")

    class FakeEpisodeService:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        @staticmethod
        def generation_signature() -> Dict[str, Any]:
            return {"generation": "script-test"}

        @staticmethod
        def generation_hash(signature: Dict[str, Any]) -> str:
            assert signature == {"generation": "script-test"}
            return "script-generation"

        async def plan_source_rebuild(self, source: str, **kwargs: Any) -> Dict[str, Any]:
            del kwargs
            return {
                "source": source,
                "payloads": [],
                "paragraph_count": 0,
                "group_count": 0,
                "fallback_count": 0,
            }

    class FakeStore:
        def claim_episode_source_rebuild_batch(self, **kwargs: Any) -> List[Dict[str, Any]]:
            del kwargs
            return [
                {
                    "source": "chat_summary:script",
                    "lease_token": "lease-script",
                    "claimed_revision": 1,
                }
            ]

        def renew_episode_source_rebuild_lease(self, *args: Any, **kwargs: Any) -> bool:
            del args, kwargs
            raise AssertionError("即时规划不应触发心跳")

        def publish_episode_source_rebuild(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
            del args, kwargs
            return {
                "source": "chat_summary:script",
                "published": False,
                "superseded": True,
                "episode_count": 0,
            }

    monkeypatch.setattr(rebuild_script, "EpisodeService", FakeEpisodeService)
    try:
        exit_code = await rebuild_script._run_rebuilds(
            FakeStore(),
            {},
            ["chat_summary:script"],
        )
    finally:
        sys.modules.pop("rebuild_episodes", None)

    assert exit_code == 1
