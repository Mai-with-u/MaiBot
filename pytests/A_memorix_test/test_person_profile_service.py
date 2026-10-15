from types import SimpleNamespace
from typing import List
import json

import pytest

from src.A_memorix.core.utils import person_profile_service as profile_service_module
from src.A_memorix.core.utils.person_profile_service import PROFILE_CLASSIFICATION_REQUEST_TYPE, PersonProfileService
from src.A_memorix.core.utils.profile_text import parse_profile_sections


class FakeMetadataStore:
    def __init__(self) -> None:
        self.snapshots: list[dict] = []
        self.refresh_count = 0
        self.fact_claims: list[dict] = [
            {
                "claim_id": "claim-1",
                "value_text": "测试用户喜欢猫。",
                "profile_section": "interaction_preferences",
                "authority": "direct_user",
                "status": "active",
            }
        ]
        self.alias_override = None

    def get_latest_person_profile_snapshot(self, person_id: str):
        return next(
            (snapshot for snapshot in reversed(self.snapshots) if snapshot["person_id"] == person_id),
            None,
        )

    @staticmethod
    def get_relations(**kwargs):
        del kwargs
        return []

    @staticmethod
    def get_paragraphs_by_source(source: str):
        if source == "person_fact:person-1":
            return [
                {
                    "hash": "person-fact-1",
                    "content": "测试用户喜欢猫。",
                    "source": source,
                    "metadata": {"source_type": "person_fact"},
                    "created_at": 2.0,
                    "updated_at": 2.0,
                }
            ]
        return []

    @staticmethod
    def get_paragraph(hash_value: str):
        if hash_value == "chat-summary-1":
            return {
                "hash": hash_value,
                "content": "机器人建议测试用户以后叫星灯。",
                "source": "chat_summary:session-1",
                "metadata": {"source_type": "chat_summary", "person_id": "person-1"},
                "word_count": 1,
            }
        if hash_value == "person-fact-1":
            return {
                "hash": hash_value,
                "content": "测试用户喜欢猫。",
                "source": "person_fact:person-1",
                "metadata": {"source_type": "person_fact"},
                "word_count": 1,
            }
        return None

    @staticmethod
    def get_paragraph_stale_relation_marks_batch(paragraph_hashes):
        del paragraph_hashes
        return {}

    @staticmethod
    def get_paragraphs_by_entity(entity_name: str):
        del entity_name
        return []

    @staticmethod
    def get_paragraph_entities(paragraph_hash: str):
        del paragraph_hash
        return []

    @staticmethod
    def get_relation_status_batch(relation_hashes):
        del relation_hashes
        return {}

    def list_person_profile_fact_claims(self, person_id: str, *, effective_at: float, limit: int):
        assert person_id == "person-1"
        assert effective_at > 0
        return [dict(item) for item in self.fact_claims[:limit]]

    @staticmethod
    def get_person_profile_override(person_id: str):
        del person_id
        return None

    def get_person_profile_alias_override(self, person_id: str):
        assert person_id == "person-1"
        return self.alias_override

    def upsert_person_profile_snapshot(self, **kwargs):
        snapshot = {
            "snapshot_id": len(self.snapshots) + 1,
            "person_id": kwargs["person_id"],
            "profile_version": len(self.snapshots) + 1,
            "profile_text": kwargs["profile_text"],
            "aliases": kwargs["aliases"],
            "relation_edges": kwargs["relation_edges"],
            "vector_evidence": kwargs["vector_evidence"],
            "evidence_ids": kwargs["evidence_ids"],
            "fact_claim_ids": kwargs["fact_claim_ids"],
            "evidence_fingerprint": kwargs["evidence_fingerprint"],
            "updated_at": 1.0,
            "expires_at": kwargs["expires_at"],
            "source_note": kwargs["source_note"],
        }
        self.snapshots.append(snapshot)
        return snapshot

    def refresh_person_profile_snapshot_cache(self, snapshot_id: int, **kwargs):
        snapshot = next(item for item in self.snapshots if item["snapshot_id"] == snapshot_id)
        snapshot.update(kwargs)
        self.refresh_count += 1
        return dict(snapshot)


class FakeRetriever:
    def __init__(self) -> None:
        self.score = 0.95
        self.content = "机器人建议测试用户以后叫星灯。"
        self.metadata = {"source_type": "chat_summary", "person_id": "person-1"}

    async def retrieve(self, query: str, top_k: int):
        del query, top_k
        return [
            SimpleNamespace(
                hash_value="chat-summary-1",
                result_type="paragraph",
                score=self.score,
                content=self.content,
                metadata=self.metadata,
            )
        ]


def test_person_profile_manual_aliases_replace_derived_aliases() -> None:
    metadata_store = FakeMetadataStore()
    metadata_store.alias_override = {
        "person_id": "person-1",
        "aliases": ["新别名", "正式称呼"],
    }
    service = PersonProfileService(metadata_store=metadata_store, retriever=FakeRetriever())
    service._get_derived_person_aliases = lambda person_id: (["旧昵称"], "正式称呼", ["记忆特征"])

    details = service.get_person_alias_details("person-1")

    assert details["derived_aliases"] == ["旧昵称"]
    assert details["manual_aliases"] == ["新别名", "正式称呼"]
    assert details["effective_aliases"] == ["新别名", "正式称呼"]
    assert details["has_override"] is True
    assert service.get_person_aliases("person-1") == (["新别名", "正式称呼"], "正式称呼", ["记忆特征"])


def test_person_profile_cooccurring_entities_are_suggestions_not_effective_aliases() -> None:
    metadata_store = FakeMetadataStore()
    queried_entities = []

    def get_paragraphs_by_entity(entity_name: str):
        queried_entities.append(entity_name)
        return [{"hash": "paragraph-1"}]

    metadata_store.get_paragraphs_by_entity = get_paragraphs_by_entity
    metadata_store.get_paragraph_entities = lambda paragraph_hash: [
        {"name": "测试用户"},
        {"name": "产品经理"},
        {"name": "向量索引"},
    ]
    service = PersonProfileService(metadata_store=metadata_store, retriever=FakeRetriever())
    service._get_derived_person_aliases = lambda person_id: (["测试用户"], "测试用户", [])

    details = service.get_person_alias_details("person-1")

    assert details["derived_aliases"] == ["测试用户"]
    assert details["suggested_aliases"] == ["产品经理", "向量索引"]
    assert details["effective_aliases"] == ["测试用户"]
    assert queried_entities == ["测试用户"]


def test_person_profile_alias_suggestions_merge_paragraphs_from_trusted_names() -> None:
    metadata_store = FakeMetadataStore()
    queried_entities = []

    def get_paragraphs_by_entity(entity_name: str):
        queried_entities.append(entity_name)
        return {
            "测试用户": [{"hash": "paragraph-1"}],
            "小测": [{"hash": "paragraph-1"}, {"hash": "paragraph-2"}],
        }.get(entity_name, [])

    metadata_store.get_paragraphs_by_entity = get_paragraphs_by_entity
    metadata_store.get_paragraph_entities = lambda paragraph_hash: {
        "paragraph-1": [{"name": "测试用户"}, {"name": "产品经理"}],
        "paragraph-2": [{"name": "小测"}, {"name": "摄影"}],
    }[paragraph_hash]
    service = PersonProfileService(metadata_store=metadata_store, retriever=FakeRetriever())
    service._get_derived_person_aliases = lambda person_id: (["测试用户", "小测"], "测试用户", [])

    details = service.get_person_alias_details("person-1")

    assert queried_entities == ["测试用户", "小测"]
    assert details["suggested_aliases"] == ["产品经理", "摄影"]


def test_person_profile_without_master_record_uses_person_id_as_trusted_alias(monkeypatch) -> None:
    class EmptyPersonSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            del exc_type, exc_value, traceback

        def exec(self, statement):
            del statement
            return self

        @staticmethod
        def first():
            return None

    metadata_store = FakeMetadataStore()
    metadata_store.get_paragraphs_by_entity = lambda entity_name: [{"hash": "paragraph-1"}]
    metadata_store.get_paragraph_entities = lambda paragraph_hash: [
        {"name": "person-1"},
        {"name": "产品经理"},
    ]
    monkeypatch.setattr(profile_service_module, "get_db_session", lambda **kwargs: EmptyPersonSession())
    service = PersonProfileService(metadata_store=metadata_store, retriever=FakeRetriever())

    details = service.get_person_alias_details("person-1")

    assert details["derived_aliases"] == ["person-1"]
    assert details["suggested_aliases"] == ["产品经理"]
    assert details["effective_aliases"] == ["person-1"]
    assert details["primary_name"] == "person-1"


@pytest.mark.asyncio
async def test_person_profile_keeps_chat_summary_as_recent_interaction_not_stable_profile():
    metadata_store = FakeMetadataStore()
    service = PersonProfileService(metadata_store=metadata_store, retriever=FakeRetriever())
    service.get_person_aliases = lambda person_id: (["测试用户"], "测试用户", [])
    service._resolve_profile_classification_model = lambda: None

    payload = await service.query_person_profile(person_id="person-1", top_k=6, force_refresh=True)

    assert payload["success"] is True
    profile_text = payload["profile_text"]
    sections = parse_profile_sections(profile_text)
    stable_sections = "\n".join(
        sections["身份设定"] + sections["关系设定"] + sections["稳定了解"] + sections["相处偏好"]
    )

    assert profile_text.startswith("# 人物画像")
    assert "测试用户喜欢猫" in "\n".join(sections["相处偏好"])
    assert "星灯" not in stable_sections
    assert "星灯" in "\n".join(sections["近期互动"])
    assert sections["维护备注"]


@pytest.mark.asyncio
async def test_profile_refresh_short_circuits_when_only_retrieval_score_changes() -> None:
    metadata_store = FakeMetadataStore()
    retriever = FakeRetriever()
    service = PersonProfileService(metadata_store=metadata_store, retriever=retriever)
    service.get_person_aliases = lambda person_id: (["测试用户", "小测"], "测试用户", ["喜欢简洁表达"])
    service._resolve_profile_classification_model = lambda: None

    first = await service.query_person_profile(person_id="person-1", top_k=6, force_refresh=True)
    retriever.score = 0.51
    second = await service.query_person_profile(person_id="person-1", top_k=6, force_refresh=True)

    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert second["evidence_unchanged"] is True
    assert second["profile_version"] == first["profile_version"] == 1
    assert len(metadata_store.snapshots) == 1
    assert metadata_store.refresh_count == 1


@pytest.mark.asyncio
async def test_profile_refresh_skips_llm_when_nested_retrieval_scores_change(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata_store = FakeMetadataStore()
    retriever = FakeRetriever()
    retriever.metadata.update(
        {
            "score_breakdown": {"graph_reliability": 0.19199791869543034, "graph_grounding_quality": 0.2953814133775851},
            "bm25_score": 0.7,
            "ppr_signal": 0.5,
            "ppr_focus_entity_count": 1,
            "ppr_boost": 0.03,
        }
    )
    service = PersonProfileService(metadata_store=metadata_store, retriever=retriever)
    service.get_person_aliases = lambda person_id: (["测试用户"], "测试用户", [])
    service._resolve_profile_classification_model = lambda: SimpleNamespace(
        task_name="memory",
        selected_model_name=None,
        task_config=SimpleNamespace(model_list=["test-model"]),
    )
    prompts: List[str] = []

    async def fake_generate(model, request_type, prompt, **kwargs):
        assert request_type == PROFILE_CLASSIFICATION_REQUEST_TYPE
        prompts.append(prompt)
        return SimpleNamespace(
            success=True,
            completion=SimpleNamespace(response=json.dumps({"recent_interactions": [retriever.content]})),
        )

    monkeypatch.setattr(profile_service_module, "generate_with_resolved_model", fake_generate)
    first = await service.query_person_profile(person_id="person-1", top_k=6, force_refresh=True)
    retriever.score = 0.51
    retriever.metadata.update(
        {
            "score_breakdown": {"graph_reliability": 0.19201712884036976, "graph_grounding_quality": 0.2954109674467227},
            "bm25_score": 0.9,
            "ppr_signal": 0.8,
            "ppr_focus_entity_count": 2,
            "ppr_boost": 0.048,
        }
    )
    second = await service.query_person_profile(person_id="person-1", top_k=6, force_refresh=True)

    assert second["evidence_unchanged"] is True
    assert second["profile_version"] == first["profile_version"] == 1
    assert metadata_store.refresh_count == 1
    assert len(metadata_store.snapshots) == len(prompts) == 1
    assert "score_breakdown" not in prompts[0]
    assert retriever.metadata["ppr_boost"] == 0.048


@pytest.mark.parametrize("field", ["source_type", "custom_evidence_note"])
def test_profile_fingerprint_tracks_non_scoring_evidence_metadata(field: str) -> None:
    service = PersonProfileService(metadata_store=FakeMetadataStore(), retriever=FakeRetriever())
    service._resolve_profile_classification_model = lambda: None
    evidence = {"hash": "p-1", "type": "paragraph", "content": "测试证据", "metadata": {"source_type": "chat_summary"}}
    inputs = {
        "person_id": "person-1",
        "primary_name": "测试用户",
        "aliases": ["测试用户"],
        "relation_edges": [],
        "vector_evidence": [evidence],
        "memory_traits": [],
        "fact_claims": [],
    }
    first = service._profile_evidence_fingerprint(**inputs)
    evidence["metadata"][field] = "person_fact"

    assert service._profile_evidence_fingerprint(**inputs) != first


@pytest.mark.asyncio
async def test_profile_refresh_creates_version_when_same_evidence_id_content_changes() -> None:
    metadata_store = FakeMetadataStore()
    retriever = FakeRetriever()
    service = PersonProfileService(metadata_store=metadata_store, retriever=retriever)
    service.get_person_aliases = lambda person_id: (["测试用户"], "测试用户", [])
    service._resolve_profile_classification_model = lambda: None

    first = await service.query_person_profile(person_id="person-1", top_k=6, force_refresh=True)
    retriever.content = "机器人建议测试用户以后叫月灯。"
    second = await service.query_person_profile(person_id="person-1", top_k=6, force_refresh=True)

    assert first["profile_version"] == 1
    assert second["from_cache"] is False
    assert second["profile_version"] == 2
    assert len(metadata_store.snapshots) == 2
    assert first["evidence_fingerprint"] != second["evidence_fingerprint"]


@pytest.mark.asyncio
async def test_profile_classification_uses_llm_buckets_and_guards_uncertain_stable_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = PersonProfileService(metadata_store=FakeMetadataStore(), retriever=FakeRetriever())
    service._resolve_profile_classification_model = lambda: SimpleNamespace(
        is_single_model=False,
        task_name="memory",
        task_config=SimpleNamespace(),
    )

    async def fake_generate_with_resolved_model(*args, **kwargs):
        model, request_type, prompt = args
        assert model.task_name == "memory"
        assert request_type == PROFILE_CLASSIFICATION_REQUEST_TYPE
        assert "测试用户喜欢直接沟通" in prompt
        assert kwargs["temperature"] == 0.1
        return SimpleNamespace(
            success=True,
            completion=SimpleNamespace(
                response=json.dumps(
                    {
                        "identity_settings": ["测试用户是画师。"],
                        "relationship_settings": ["测试用户把麦麦当搭档。"],
                        "stable_facts": ["测试用户可能长期熬夜。"],
                        "interaction_preferences": ["测试用户喜欢直接沟通。"],
                        "recent_interactions": ["测试用户刚聊过记忆优化。"],
                        "uncertain_notes": ["测试用户似乎偏好蓝色。"],
                    },
                    ensure_ascii=False,
                )
            ),
        )

    monkeypatch.setattr(profile_service_module, "generate_with_resolved_model", fake_generate_with_resolved_model)

    buckets = await service._classify_profile_evidence(
        person_id="person-1",
        primary_name="测试用户",
        aliases=["测试用户"],
        relation_edges=[],
        vector_evidence=[
            {
                "content": "测试用户喜欢直接沟通。",
                "metadata": {"source_type": "person_fact"},
            }
        ],
        memory_traits=[],
    )

    assert buckets["identity_settings"] == ["测试用户是画师。"]
    assert buckets["relationship_settings"] == ["测试用户把麦麦当搭档。"]
    assert "测试用户可能长期熬夜。" in buckets["stable_facts"]
    assert "测试用户似乎偏好蓝色。" in buckets["uncertain_notes"]


@pytest.mark.asyncio
async def test_profile_projects_ledger_claims_before_retrieval_evidence() -> None:
    metadata_store = FakeMetadataStore()
    metadata_store.fact_claims = [
        {
            "claim_id": f"claim-{index}",
            "value_text": f"稳定事实{index}。",
            "profile_section": "stable_facts",
            "authority": "direct_user",
            "status": "active",
        }
        for index in range(1, 9)
    ]
    service = PersonProfileService(metadata_store=metadata_store, retriever=FakeRetriever())
    service.get_person_aliases = lambda person_id: (["测试用户"], "测试用户", [])
    service._resolve_profile_classification_model = lambda: None

    payload = await service.query_person_profile(person_id="person-1", top_k=12, force_refresh=True)
    sections = parse_profile_sections(payload["profile_text"])
    stable_lines = [line for line in sections["稳定了解"] if line]

    assert stable_lines == [f"- 稳定事实{index}。" for index in range(1, 7)]
    assert payload["fact_claim_ids"] == [f"claim-{index}" for index in range(1, 9)]
    assert "星灯" not in "\n".join(sections["稳定了解"])


@pytest.mark.asyncio
async def test_model_derived_person_fact_is_confined_to_uncertain_projection() -> None:
    metadata_store = FakeMetadataStore()
    metadata_store.fact_claims = [
        {
            "claim_id": "claim-model-derived",
            "value_text": "模型改写称测试用户住在旧金山。",
            "profile_section": "uncertain_notes",
            "authority": "summary_derived",
            "stability": "uncertain",
            "status": "active",
        }
    ]
    service = PersonProfileService(metadata_store=metadata_store, retriever=FakeRetriever())
    service.get_person_aliases = lambda person_id: (["测试用户"], "测试用户", [])
    service._resolve_profile_classification_model = lambda: None

    payload = await service.query_person_profile(person_id="person-1", top_k=12, force_refresh=True)
    sections = parse_profile_sections(payload["profile_text"])

    assert "旧金山" not in "\n".join(sections["稳定了解"])
    assert "旧金山" in "\n".join(sections["不确定信息"])


@pytest.mark.asyncio
async def test_model_classification_can_write_stable_profile() -> None:
    metadata_store = FakeMetadataStore()
    metadata_store.fact_claims = []
    service = PersonProfileService(metadata_store=metadata_store, retriever=FakeRetriever())
    service.get_person_aliases = lambda person_id: (["测试用户"], "测试用户", [])

    async def model_classification(**kwargs):
        del kwargs
        return {
            "identity_settings": [],
            "relationship_settings": [],
            "stable_facts": ["模型断言测试用户住在旧金山。"],
            "interaction_preferences": [],
            "recent_interactions": [],
            "uncertain_notes": [],
        }

    service._classify_profile_evidence = model_classification

    payload = await service.query_person_profile(person_id="person-1", top_k=12, force_refresh=True)
    sections = parse_profile_sections(payload["profile_text"])

    assert "旧金山" in "\n".join(sections["稳定了解"])
    assert "旧金山" not in "\n".join(sections["不确定信息"])
