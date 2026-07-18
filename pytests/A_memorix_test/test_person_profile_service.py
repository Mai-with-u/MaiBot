from types import SimpleNamespace
import json

import pytest

from src.A_memorix.core.utils import person_profile_service as profile_service_module
from src.A_memorix.core.utils.person_profile_service import PROFILE_CLASSIFICATION_REQUEST_TYPE, PersonProfileService
from src.A_memorix.core.utils.profile_text import parse_profile_sections


class FakeMetadataStore:
    def __init__(self) -> None:
        self.snapshots: list[dict] = []

    @staticmethod
    def get_latest_person_profile_snapshot(person_id: str):
        del person_id
        return None

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
    def get_relation_status_batch(relation_hashes):
        del relation_hashes
        return {}

    @staticmethod
    def get_person_profile_override(person_id: str):
        del person_id
        return None

    def upsert_person_profile_snapshot(self, **kwargs):
        self.snapshots.append(kwargs)
        return {
            "person_id": kwargs["person_id"],
            "profile_text": kwargs["profile_text"],
            "aliases": kwargs["aliases"],
            "relation_edges": kwargs["relation_edges"],
            "vector_evidence": kwargs["vector_evidence"],
            "evidence_ids": kwargs["evidence_ids"],
            "updated_at": 1.0,
            "expires_at": kwargs["expires_at"],
            "source_note": kwargs["source_note"],
        }


class FakeRetriever:
    async def retrieve(self, query: str, top_k: int):
        del query, top_k
        return [
            SimpleNamespace(
                hash_value="chat-summary-1",
                result_type="paragraph",
                score=0.95,
                content="机器人建议测试用户以后叫星灯。",
                metadata={"source_type": "chat_summary", "person_id": "person-1"},
            )
        ]


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
    assert "测试用户可能长期熬夜。" not in buckets["stable_facts"]
    assert "测试用户可能长期熬夜。" in buckets["uncertain_notes"]
    assert "测试用户似乎偏好蓝色。" in buckets["uncertain_notes"]
