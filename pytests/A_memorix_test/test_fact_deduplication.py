"""去重、来源独立性和中断恢复使用真实 SQLite，不依赖本地实验目录。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import json

import numpy as np
import pytest

from src.A_memorix.core.runtime.services.ingest_service import MemoryIngestService
from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.utils.fact_deduplication import FactDeduplicator, fact_scope_lock, observation_id
from src.A_memorix.core.utils.summary_importer import SummaryImporter
from src.services import llm_service


@pytest.fixture
def store(tmp_path):
    value = MetadataStore(data_dir=tmp_path)
    value.connect()
    yield value
    value.close()


class Vectors:
    def search(self, query, k=8, allowed_ids=()):
        values = list(allowed_ids)[:k]
        return values, [0.9] * len(values)


def source(index=1, chat_id="chat-a"):
    message_ids = [f"m-{index}"]
    return {
        "platform": "qq",
        "chat_id": chat_id,
        "message_ids": message_ids,
        "observation_id": observation_id(platform="qq", chat_id=chat_id, message_ids=message_ids),
    }


async def seed(store, text="甲喜欢绿茶。", scope_type="person", scope_id="qq:1", index=1):
    dedup = FactDeduplicator(store)
    plan = await dedup.plan(
        scope_type=scope_type, scope_id=scope_id, facts=[{"fact_id": "f1", "text": text}], source=source(index)
    )
    paragraph = store.add_paragraph(
        content=text, source=f"{'person_fact' if scope_type == 'person' else 'chat_summary'}:{scope_id}"
    )
    claims = dedup.commit(plan, paragraph)
    return paragraph, claims[0]


def model(monkeypatch, decide, supports=None):
    requests = []

    async def generate(request):
        payload = json.loads(request.prompt[request.prompt.index("{") :])
        requests.append(payload)
        return SimpleNamespace(
            success=True,
            completion=SimpleNamespace(
                response=json.dumps(
                    {
                        "decisions": [
                            decide(fact, payload["candidates"][fact["fact_id"]]) for fact in payload["facts"]
                        ],
                        "relation_support": supports or {},
                    }
                )
            ),
        )

    monkeypatch.setattr(llm_service, "generate", generate)
    return requests


def coordinator(store):
    return FactDeduplicator(store, SimpleNamespace(encode=AsyncMock(return_value=np.ones(4))), Vectors())


@pytest.mark.asyncio
async def test_exact_repeat_adds_independent_sources_and_replays_once(store, monkeypatch):
    paragraph, claim_id = await seed(store)
    monkeypatch.setattr(llm_service, "generate", AsyncMock(side_effect=AssertionError("精确重复不应调用模型")))
    dedup = coordinator(store)
    plan = await dedup.plan(
        scope_type="person", scope_id="qq:1", facts=[{"fact_id": "f1", "text": "甲喜欢绿茶。"}], source=source(2)
    )
    assert plan["decisions"][0]["action"] == "reuse"
    assert dedup.commit(plan) == dedup.commit(plan) == [claim_id]
    assert len(store.query("SELECT * FROM paragraphs")) == 1
    evidence = store.get_fact_evidence(claim_id)
    assert len([row for row in evidence if row["evidence_type"] == "source"]) == 2
    snapshot = store.detach_fact_evidence_for_paragraphs([paragraph], reason="delete")
    assert store.get_fact_claim(claim_id)["status"] == "retracted"
    assert len(snapshot["evidence"]) == 3
    store.restore_fact_evidence_snapshot(snapshot, reason="undo")
    assert store.get_fact_claim(claim_id)["status"] == "active"
    assert len(store.get_fact_evidence(claim_id)) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_type,scope_id", [("person", "qq:2"), ("person", "telegram:1"), ("chat", "chat-b")])
async def test_identity_and_scope_isolation(store, monkeypatch, scope_type, scope_id):
    await seed(store)
    monkeypatch.setattr(llm_service, "generate", AsyncMock(side_effect=AssertionError("不应读取其他归属的候选")))
    plan = await coordinator(store).plan(
        scope_type=scope_type, scope_id=scope_id, facts=[{"fact_id": "f1", "text": "甲喜欢绿茶。"}], source=source(2)
    )
    assert plan["decisions"][0]["action"] == "keep"


@pytest.mark.asyncio
async def test_same_display_name_in_chat_cannot_merge_different_sender_ids(store, monkeypatch):
    dedup = coordinator(store)
    plan = await dedup.plan(
        scope_type="chat",
        scope_id="chat-a",
        facts=[{"fact_id": "f1", "text": "小明喜欢绿茶。", "person_ids": ["qq:1"]}],
        source=source(),
    )
    paragraph = store.add_paragraph(content="小明喜欢绿茶。", source="chat_summary:chat-a")
    dedup.commit(plan, paragraph)
    monkeypatch.setattr(llm_service, "generate", AsyncMock(side_effect=AssertionError("程序应先排除不同发送者")))
    incoming = await dedup.plan(
        scope_type="chat",
        scope_id="chat-a",
        facts=[{"fact_id": "f1", "text": "小明喜欢绿茶。", "person_ids": ["qq:2"]}],
        source=source(2),
    )
    assert incoming["decisions"][0]["action"] == "keep"


@pytest.mark.asyncio
async def test_paraphrase_reuses_single_fact_and_keeps_source(store, monkeypatch):
    paragraph, claim_id = await seed(store)
    calls = model(
        monkeypatch,
        lambda fact, candidates: {"fact_id": fact["fact_id"], "action": "reuse", "target_id": candidates[0]["id"]},
    )
    plan = await coordinator(store).plan(
        scope_type="person", scope_id="qq:1", facts=[{"fact_id": "f1", "text": "甲偏爱喝绿茶。"}], source=source(2)
    )
    assert FactDeduplicator(store).commit(plan) == [claim_id]
    assert len(calls) == 1 and len(calls[0]["candidates"]["f1"]) <= 8
    assert len(store.query("SELECT * FROM paragraphs")) == 1


@pytest.mark.asyncio
async def test_possible_duplicate_does_not_change_fact_truth(store, monkeypatch):
    await seed(store)
    model(
        monkeypatch,
        lambda fact, candidates: {
            "fact_id": fact["fact_id"],
            "action": "possible_duplicate",
            "candidate_ids": [candidates[0]["id"]],
        },
    )
    plan = await coordinator(store).plan(
        scope_type="person", scope_id="qq:1", facts=[{"fact_id": "f1", "text": "甲通常喝茶。"}], source=source(2)
    )
    paragraph = store.add_paragraph(content="甲通常喝茶。", source="person_fact:qq:1")
    claim = store.get_fact_claim(FactDeduplicator(store).commit(plan, paragraph)[0])
    assert claim["status"] == "active" and claim["stability"] == "stable"
    assert claim["profile_section"] == "stable_facts"
    assert any(row["metadata"].get("dedup_candidate_ids") for row in store.get_fact_evidence(claim["claim_id"]))
    assert len(store.query("SELECT * FROM fact_claims")) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["甲不喜欢绿茶。", "甲每天喝两杯绿茶。", "甲昨天喝了绿茶。"])
async def test_non_equivalent_facts_remain_independent(store, monkeypatch, text):
    paragraph, old_claim = await seed(store)
    model(monkeypatch, lambda fact, candidates: {"fact_id": fact["fact_id"], "action": "keep"})
    plan = await coordinator(store).plan(
        scope_type="person", scope_id="qq:1", facts=[{"fact_id": "f1", "text": text}], source=source(2)
    )
    paragraph = store.add_paragraph(content=text, source="person_fact:qq:1")
    claim_id = FactDeduplicator(store).commit(plan, paragraph)[0]
    assert claim_id != old_claim and store.get_fact_claim(old_claim)["status"] == "active"


@pytest.mark.asyncio
async def test_retracted_fact_is_not_revived(store, monkeypatch):
    paragraph, old_claim = await seed(store)
    store.retract_fact_claim(old_claim, reason="manual")
    monkeypatch.setattr(llm_service, "generate", AsyncMock(side_effect=AssertionError("无有效候选")))
    plan = await coordinator(store).plan(
        scope_type="person", scope_id="qq:1", facts=[{"fact_id": "f1", "text": "甲喜欢绿茶。"}], source=source(2)
    )
    new_claim = FactDeduplicator(store).commit(plan, paragraph)[0]
    assert new_claim != old_claim
    assert store.get_fact_claim(old_claim)["status"] == "retracted"


@pytest.mark.asyncio
async def test_remove_one_source_preserves_other_and_last_source_retracts(store):
    paragraph, claim_id = await seed(store)
    dedup = FactDeduplicator(store)
    plan = await dedup.plan(
        scope_type="person", scope_id="qq:1", facts=[{"fact_id": "f1", "text": "甲喜欢绿茶。"}], source=source(2)
    )
    dedup.commit(plan)
    store.update_paragraph_metadata(paragraph, {"dedup_managed": True, "dedup_phase": "complete"}, merge=True)
    first = store.detach_fact_evidence_for_sources([source()["observation_id"]], reason="source_deleted")
    assert first["retracted_claim_ids"] == []
    assert store.project_fact_paragraph(store.get_paragraph(paragraph)) is not None
    second = store.detach_fact_evidence_for_sources([source(2)["observation_id"]], reason="source_deleted")
    assert second["retracted_claim_ids"] == [claim_id]
    assert store.project_fact_paragraph(store.get_paragraph(paragraph)) is None
    store.restore_fact_evidence_snapshot(second, reason="undo")
    assert store.project_fact_paragraph(store.get_paragraph(paragraph))["content"] == "甲喜欢绿茶。"


@pytest.mark.asyncio
async def test_source_withdrawal_invalidates_materialized_episode_until_rebuild(store):
    paragraph, _ = await seed(store)
    with store.transaction() as database:
        database.execute(
            "UPDATE episode_rebuild_sources SET built_revision=desired_revision WHERE source='person_fact:qq:1'"
        )
    assert not store.is_episode_source_query_blocked("person_fact:qq:1")
    store.detach_fact_evidence_for_sources([source()["observation_id"]], reason="source_deleted")
    assert store.is_episode_source_query_blocked("person_fact:qq:1")
    store.enqueue_episode_source_rebuild("person_fact:qq:1", reason="paragraph_added")
    assert store.is_episode_source_query_blocked("person_fact:qq:1")
    with store.transaction() as database:
        database.execute(
            "UPDATE episode_rebuild_sources SET built_revision=desired_revision WHERE source='person_fact:qq:1'"
        )
    assert not store.is_episode_source_query_blocked("person_fact:qq:1")


@pytest.mark.asyncio
async def test_public_hits_omit_internal_decisions_and_possible_duplicates(store):
    from src.A_memorix.core.runtime.services.search_hit_processing_service import MemorySearchHitProcessingService

    paragraph, _ = await seed(store)
    store.update_paragraph_metadata(
        paragraph,
        {
            "dedup_managed": True,
            "dedup_phase": "complete",
            "dedup_candidate_ids": ["candidate"],
            "fact_dedup_decisions": [{"action": "keep"}],
        },
        merge=True,
    )
    kernel = SimpleNamespace(
        metadata_store=store,
        _filter_active_relation_hits=lambda hits: hits,
        _filter_episode_hits=lambda hits: hits,
        _filter_current_effective_hits=lambda hits: hits,
    )
    reader = MemorySearchHitProcessingService(kernel)
    result = reader._filter_user_visible_hits([{"type": "paragraph", "hash": paragraph, "content": "ignored"}])
    assert result[0]["content"] == "甲喜欢绿茶。"
    assert not any(key.startswith("dedup_") or key == "fact_dedup_decisions" for key in result[0]["metadata"])


@pytest.mark.asyncio
async def test_historical_mixed_paragraph_registers_only_matched_fact(store, monkeypatch):
    paragraph = store.add_paragraph(content="甲喜欢绿茶。乙住杭州。", source="chat_summary:chat-a")
    model(
        monkeypatch,
        lambda fact, candidates: {"fact_id": fact["fact_id"], "action": "reuse", "target_id": candidates[0]["id"]},
    )
    plan = await coordinator(store).plan(
        scope_type="chat", scope_id="chat-a", facts=[{"fact_id": "f1", "text": "甲偏爱喝绿茶。"}], source=source(2)
    )
    claim_id = FactDeduplicator(store).commit(plan)[0]
    claim = store.get_fact_claim(claim_id)
    assert claim["scope_type"] == "chat" and claim["scope_id"] == "chat-a"
    assert claim["value_text"] == "甲偏爱喝绿茶。" and claim["authority"] == "summary_derived"
    assert len(store.query("SELECT * FROM fact_claims")) == 1
    assert store.get_paragraph(paragraph)["content"] == "甲喜欢绿茶。乙住杭州。"


@pytest.mark.asyncio
async def test_batch_limits_and_input_dedup(store, monkeypatch):
    calls = model(monkeypatch, lambda fact, candidates: {"fact_id": fact["fact_id"], "action": "keep"})
    facts = [{"fact_id": f"f{i}", "text": f"甲收藏藏品{i}。"} for i in range(19)]
    plan = await coordinator(store).plan(scope_type="person", scope_id="qq:1", facts=facts, source=source())
    assert len(plan["decisions"]) == 19
    assert len(calls) == 3
    assert all(len(call["facts"]) <= 8 for call in calls)
    assert all(len(rows) <= 8 for call in calls for rows in call["candidates"].values())


@pytest.mark.asyncio
async def test_historical_candidates_do_not_crowd_out_same_batch_duplicate(store, monkeypatch):
    for index in range(8):
        text = f"甲收藏物件{index}。"
        paragraph = store.add_paragraph(content=text, source="person_fact:qq:1")
        store.upsert_fact_claim(
            scope_type="person",
            scope_id="qq:1",
            fact_key=str(index),
            value_text=text,
            evidence_type="paragraph",
            evidence_id=paragraph,
        )

    def decide(fact, candidates):
        if fact["fact_id"] == "f2":
            assert any(row["id"] == "input:f1" for row in candidates)
            return {"fact_id": "f2", "action": "reuse", "target_id": "input:f1"}
        return {"fact_id": "f1", "action": "keep"}

    model(monkeypatch, decide)
    plan = await coordinator(store).plan(
        scope_type="person",
        scope_id="qq:1",
        source=source(),
        facts=[
            {"fact_id": "f1", "text": "甲喜欢绿茶。"},
            {"fact_id": "f2", "text": "甲喜欢绿茶。"},
        ],
    )
    paragraph = store.add_paragraph(content="甲喜欢绿茶。", source="person_fact:qq:1")
    claims = FactDeduplicator(store).commit(plan, paragraph)
    assert claims[0] == claims[1]
    assert len(store.query("SELECT * FROM fact_claims")) == 9


@pytest.mark.asyncio
async def test_invalid_model_target_does_not_write(store, monkeypatch):
    await seed(store)
    model(
        monkeypatch, lambda fact, candidates: {"fact_id": fact["fact_id"], "action": "reuse", "target_id": "invented"}
    )
    with pytest.raises(ValueError, match="候选"):
        await coordinator(store).plan(
            scope_type="person", scope_id="qq:1", facts=[{"fact_id": "f1", "text": "甲偏爱喝绿茶。"}], source=source(2)
        )
    assert len(store.query("SELECT * FROM fact_claims")) == 1


@pytest.mark.asyncio
async def test_summary_partial_filter_and_checkpoint_recovery(store, monkeypatch):
    await seed(store, scope_type="chat", scope_id="chat-a")
    model(
        monkeypatch, lambda fact, candidates: {"fact_id": fact["fact_id"], "action": "keep"}, {"0": ["f1"], "1": ["f2"]}
    )
    facts = [
        {"fact_id": "f1", "text": "甲喜欢绿茶。", "image_evidence": []},
        {"fact_id": "f2", "text": "乙住杭州。", "image_evidence": []},
    ]
    plan = await coordinator(store).plan(
        scope_type="chat",
        scope_id="chat-a",
        facts=facts,
        source=source(2),
        relations=[
            {"subject": "甲", "predicate": "喜欢", "object": "绿茶"},
            {"subject": "乙", "predicate": "居住", "object": "杭州"},
        ],
    )
    payload = {
        "plan": plan,
        "metadata": {"external_id": "window-2", "trigger_message_count": 20},
        "entities": ["甲", "绿茶", "乙", "杭州"],
        "relations": [
            {"subject": "甲", "predicate": "喜欢", "object": "绿茶"},
            {"subject": "乙", "predicate": "居住", "object": "杭州"},
        ],
        "time_meta": {},
        "allowed_images": [],
    }
    store.record_summary_checkpoint(
        external_id="window-2", chat_id="chat-a", trigger_message_count=20, phase="pending", result=payload
    )
    captured = []

    async def write(text, entities, relations, stream_id, **kwargs):
        captured.append((text, entities, relations))
        return store.add_paragraph(content=text, source=f"chat_summary:{stream_id}", metadata=kwargs["metadata"])

    importer = SummaryImporter(None, SimpleNamespace(save=lambda: None), store, None, {})
    monkeypatch.setattr(importer, "_execute_import", write)
    monkeypatch.setattr(importer, "_persist_vector_store", lambda value: None)
    assert importer._existing_summary_result(stream_id="chat-a", metadata=payload["metadata"]) is None
    assert store.get_summary_checkpoint_count("chat-a") == 0
    result = await importer.import_from_stream("chat-a", metadata=payload["metadata"])
    assert result.success and captured == [("乙住杭州。", ["乙", "杭州"], [payload["relations"][1]])]
    assert store.get_summary_checkpoint_count("chat-a") == 20
    assert (await importer.import_from_stream("chat-a", metadata=payload["metadata"])).success
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_scope_lock_serializes_independent_coordinators(store):
    active = 0
    maximum = 0

    async def operation():
        nonlocal active, maximum
        async with fact_scope_lock(store, "person", "qq:1"):
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.001)
            active -= 1

    await asyncio.gather(operation(), operation())
    assert maximum == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["vector", "persist"])
async def test_person_receipt_resumes_without_rejudging_or_duplicating_evidence(store, monkeypatch, failure_stage):
    failed = False
    writes = []

    def persist():
        nonlocal failed
        if failure_stage == "persist" and not failed:
            failed = True
            raise RuntimeError("persist interrupted")

    kernel = SimpleNamespace(
        metadata_store=store,
        embedding_manager=None,
        vector_store=None,
        initialize=AsyncMock(),
        _is_chat_filtered=lambda **kwargs: False,
        _persist=persist,
        _enqueue_person_profile_refresh=lambda *args, **kwargs: None,
    )
    service = MemoryIngestService(kernel)

    async def raw(self, **kwargs):
        nonlocal failed
        writes.append(kwargs)
        if failure_stage == "vector" and not failed:
            failed = True
            raise RuntimeError("vector interrupted")
        paragraph = store.add_paragraph(content=kwargs["text"], source="person_fact:qq:1", metadata=kwargs["metadata"])
        return {"stored_ids": [paragraph]}

    monkeypatch.setattr(MemoryIngestService, "_ingest_text_raw", raw)
    generate = AsyncMock(side_effect=AssertionError("新事实和中断恢复不应调用模型"))
    monkeypatch.setattr(llm_service, "generate", generate)
    kwargs = {
        "external_id": "person-source",
        "source_type": "person_fact",
        "chat_id": "chat-a",
        "person_ids": ["qq:1"],
        "text": "甲喜欢绿茶。",
        "metadata": {"writeback_source": "memory_flow_service", "platform": "qq", "evidence_message_ids": ["m1"]},
    }
    with pytest.raises(RuntimeError, match="interrupted"):
        await service.ingest_text(**kwargs)
    receipt = store.query("SELECT * FROM external_memory_refs")[0]
    assert json.loads(receipt["metadata_json"])["dedup_phase"] == "pending"
    result = await service.ingest_text(**kwargs)
    assert result["success"]
    assert len(store.query("SELECT * FROM fact_claims")) == 1
    assert len(store.query("SELECT * FROM fact_evidence WHERE evidence_type='source'")) == 1
    before = len(writes)
    replay = await service.ingest_text(**kwargs)
    assert replay["detail"] == "dedup_replayed" and len(writes) == before


@pytest.mark.asyncio
async def test_all_duplicate_summary_has_no_paragraph_or_vector_write(store, monkeypatch):
    await seed(store, scope_type="chat", scope_id="chat-a")
    plan = await coordinator(store).plan(
        scope_type="chat", scope_id="chat-a", facts=[{"fact_id": "f1", "text": "甲喜欢绿茶。"}], source=source(2)
    )
    importer = SummaryImporter(None, SimpleNamespace(save=lambda: None), store, None, {})
    monkeypatch.setattr(importer, "_execute_import", AsyncMock(side_effect=AssertionError("全重复不应写段落")))
    monkeypatch.setattr(importer, "_persist_vector_store", lambda value: None)
    payload = {
        "plan": plan,
        "metadata": {"external_id": "duplicate-window", "trigger_message_count": 50},
        "entities": [],
        "relations": [],
        "time_meta": {},
        "allowed_images": [],
    }
    result = await importer._finish_fact_summary(payload)
    assert result.success and result.skipped
    assert len(store.query("SELECT * FROM paragraphs")) == 1
    assert store.get_summary_checkpoint_count("chat-a") == 50


def test_schema_28_upgrade_retains_completed_checkpoint_without_backfill(tmp_path, monkeypatch):
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    store.record_summary_checkpoint(external_id="old-window", chat_id="chat-a", trigger_message_count=10)
    with store.transaction() as database:
        database.execute("ALTER TABLE chat_summary_checkpoints DROP COLUMN phase")
        database.execute("ALTER TABLE chat_summary_checkpoints DROP COLUMN result_json")
        database.execute("DELETE FROM schema_migrations")
        database.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (28,0)")
    store.close()
    monkeypatch.setattr(store, "backfill_person_fact_claims", lambda: pytest.fail("不得批量回填历史事实"))
    store.connect()
    try:
        assert store.get_schema_version() == 29
        assert store.get_summary_checkpoint("old-window")["phase"] == "complete"
        assert store.get_summary_checkpoint_count("chat-a") == 10
    finally:
        store.close()


@pytest.mark.parametrize("budget", [180, 220])
def test_profile_projection_preserves_budget_after_removing_retracted_facts(store, budget):
    from src.A_memorix.core.utils.person_profile_service import PersonProfileService

    old = "失效的旧偏好。" * 80
    current = "当前偏好是绿茶。" * 80
    paragraph = store.add_paragraph(
        content=f"{old}\n{current}",
        source="person_fact:qq:1",
        metadata={"dedup_managed": True, "dedup_phase": "complete", "dedup_fact_texts": [old, current]},
    )
    for index, text in enumerate([old, current]):
        claim = store.upsert_fact_claim(
            scope_type="person",
            scope_id="qq:1",
            fact_key=f"budget-{index}",
            value_text=text,
            evidence_type="paragraph",
            evidence_id=paragraph,
        )
        if index == 0:
            store.retract_fact_claim(claim["claim_id"], reason="old preference removed")
    service = PersonProfileService(metadata_store=store, retriever=None)
    evidence = service._filter_stale_paragraph_evidence(
        [
            {
                "hash": paragraph,
                "type": "paragraph",
                "content": old[:budget],
                "source": "person_fact:qq:1",
                "metadata": {"dedup_managed": True},
            }
        ]
    )
    assert evidence[0]["content"] == current[:budget]
    buckets = service._classify_profile_evidence_rule_based(
        relation_edges=[],
        vector_evidence=evidence,
        memory_traits=[],
    )
    texts = [text for values in buckets.values() for text in values]
    assert texts == [current[:budget]]
