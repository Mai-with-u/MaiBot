"""人物事实可信写回和历史重验的真实存储测试。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from src.A_memorix.core.runtime.services.ingest_service import MemoryIngestService
from src.A_memorix.core.runtime.services.profile_admin_service import MemoryProfileAdminService
from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.utils.person_profile_service import PersonProfileService
from src.A_memorix.core.utils.profile_text import build_profile_injection_text, build_structured_profile_text
from src.services import person_fact_reverification, person_fact_verifier


def _message(message_id: str, user_id: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        message_id=message_id,
        platform="test",
        user_id=user_id,
        message_info=SimpleNamespace(user_info=SimpleNamespace(user_id=user_id)),
        processed_plain_text=text,
    )


def test_direct_person_fact_requires_same_sender_stream_and_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = {"m1": _message("m1", "alice", "我喜欢猫。"), "m2": _message("m2", "bob", "我喜欢猫。")}
    monkeypatch.setattr(person_fact_verifier, "get_person_id", lambda platform, user_id: f"{platform}:{user_id}")
    monkeypatch.setattr(
        person_fact_verifier,
        "find_messages",
        lambda **kwargs: (
            [messages[kwargs["message_id"]]]
            if kwargs["session_id"] == "stream-1" and kwargs["message_id"] in messages
            else []
        ),
    )

    common = dict(fact="她喜欢猫。", evidence_quote="我喜欢猫。", person_id="test:alice", person_name="Alice")
    assert person_fact_verifier.verify_direct_person_fact(**common, evidence_message_id="m1", session_id="stream-1")
    assert not person_fact_verifier.verify_direct_person_fact(**common, evidence_message_id="m2", session_id="stream-1")
    assert not person_fact_verifier.verify_direct_person_fact(**common, evidence_message_id="m1", session_id="stream-2")
    assert not person_fact_verifier.verify_direct_person_fact(
        **{**common, "evidence_quote": "我喜欢狗。"}, evidence_message_id="m1", session_id="stream-1"
    )
    assert not person_fact_verifier.verify_direct_person_fact(
        **{**common, "fact": "她喜欢狗。"}, evidence_message_id="m1", session_id="stream-1"
    )
    assert not person_fact_verifier.verify_direct_person_fact(
        **{**common, "evidence_quote": "我朋友喜欢猫。", "fact": "她朋友喜欢猫。"},
        evidence_message_id="m1",
        session_id="stream-1",
    )
    messages["m1"].processed_plain_text = "朋友转述说：我喜欢猫。"
    assert not person_fact_verifier.verify_direct_person_fact(**common, evidence_message_id="m1", session_id="stream-1")
    for text in ("我喜欢猫吗？", "我喜欢猫。才怪", "我喜欢猫，但现在不喜欢了。"):
        messages["m1"].processed_plain_text = text
        assert not person_fact_verifier.verify_direct_person_fact(
            **{**common, "evidence_quote": "我喜欢猫"}, evidence_message_id="m1", session_id="stream-1"
        )
    monkeypatch.setattr(person_fact_verifier, "is_bot_self", lambda platform, user_id: True)
    assert not person_fact_verifier.verify_direct_person_fact(**common, evidence_message_id="m1", session_id="stream-1")


def test_verified_repeat_promotes_existing_uncertain_claim(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    writer = MemoryIngestService(SimpleNamespace(metadata_store=store))
    try:
        content = "她喜欢猫。"
        paragraph_hash = store.add_paragraph(content=content, source="person_fact:test:alice", knowledge_type="factual")
        writer._write_person_fact_claims(
            paragraph_hash=paragraph_hash,
            content=content,
            person_ids=["test:alice"],
            metadata={
                "evidence_message_ids": ["old"],
                "fact_claim": {"stability": "uncertain", "profile_section": "uncertain_notes"},
            },
            timestamp=None,
        )
        assert store.list_current_person_fact_claims("test:alice") == []
        writer._write_person_fact_claims(
            paragraph_hash=paragraph_hash,
            content=content,
            person_ids=["test:alice"],
            metadata={"evidence_message_ids": ["new"], "fact_claim": {"trust": "server_verified"}},
            timestamp=None,
        )
        assert len(store.list_current_person_fact_claims("test:alice", limit=300)) == 1
    finally:
        store.close()


def test_relevant_uncertain_facts_are_labeled_beside_stable_profile() -> None:
    profile_text = build_structured_profile_text(
        person_id="test:alice",
        primary_name="Alice",
        aliases=["Alice"],
        stable_facts=["她住在杭州。"],
        uncertain_notes=["她喜欢猫。"],
    )
    injection = build_profile_injection_text(
        profile_text,
        uncertain_candidates=["她喜欢猫。", "她使用青轴键盘。", "她喜欢猫。"],
        include_uncertain_fallback=False,
    )
    assert "## 稳定了解" in injection
    assert "## 不确定信息（未确认，不可当作确定事实）" in injection
    assert injection.count("她喜欢猫。") == 1
    assert "她使用青轴键盘。" in injection


@pytest.mark.asyncio
async def test_257_uncertain_facts_remain_searchable_and_historical_recheck_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MetadataStore(data_dir=tmp_path / "metadata")
    store.connect()
    writer = MemoryIngestService(SimpleNamespace(metadata_store=store))
    try:
        for index in range(257):
            text = f"她收藏藏品{index:03d}。"
            metadata = {
                "person_id": "test:alice",
                "person_name": "Alice",
                "chat_id": "stream-1",
                "evidence_source": "user_supported",
                "evidence_message_ids": [f"m-{index}"],
                "fact_claim": {
                    "stability": "uncertain",
                    "profile_section": "uncertain_notes",
                    "authority": "summary_derived",
                },
            }
            paragraph_hash = store.add_paragraph(
                content=text, source="person_fact:test:alice", metadata=metadata, knowledge_type="factual"
            )
            writer._write_person_fact_claims(
                paragraph_hash=paragraph_hash,
                content=text,
                person_ids=["test:alice"],
                metadata=metadata,
                timestamp=None,
            )
        service = MemoryProfileAdminService(SimpleNamespace(metadata_store=store))
        candidates = service._uncertain_profile_candidates("test:alice", "你还收藏藏品256吗")
        assert candidates["uncertain_fact_count"] == 257
        assert any("藏品256" in item["text"] for item in candidates["uncertain_candidates"])
        assert len(store.list_uncertain_person_fact_claims("test:alice", limit=10)) == 10

        async def initialize() -> None:
            return None

        admin = MemoryProfileAdminService(
            SimpleNamespace(
                metadata_store=store,
                person_profile_service=PersonProfileService(metadata_store=store),
                initialize=initialize,
            )
        )
        profile = await admin.memory_profile_admin(
            action="query", person_id="test:alice", context_text="你还收藏藏品256吗", force_refresh=True
        )
        assert profile["success"] is True
        assert profile["uncertain_fact_count"] == 257
        assert any("藏品256" in item["text"] for item in profile["uncertain_candidates"])

        host = SimpleNamespace(get_runtime_data_dir=lambda: tmp_path)
        monkeypatch.setattr(person_fact_reverification, "a_memorix_host_service", host)
        monkeypatch.setattr(person_fact_verifier, "get_person_id", lambda platform, user_id: f"{platform}:{user_id}")

        def find_messages(**kwargs: Any) -> list[SimpleNamespace]:
            if kwargs["session_id"] == "stream-1" and kwargs["message_id"] == "m-256":
                return [_message("m-256", "alice", "我收藏藏品256。")]
            return []

        monkeypatch.setattr(person_fact_verifier, "find_messages", find_messages)

        async def fact_admin(*, action: str, **kwargs: Any) -> Dict[str, Any]:
            claim_id = str(kwargs["claim_id"])
            if action == "get":
                return {"claim": store.get_fact_claim(claim_id)}
            claim = store.update_fact_claim_classification(
                claim_id,
                authority=str(kwargs["authority"]),
                stability=str(kwargs["stability"]),
                profile_section=str(kwargs["profile_section"]),
                confidence=float(kwargs["confidence"]),
                valid_from=None,
                valid_to=None,
                reason=str(kwargs["reason"]),
            )
            return {"success": True, "claim": claim}

        monkeypatch.setattr(person_fact_reverification.memory_service, "fact_admin", fact_admin)
        cursor = ""
        promoted = 0
        while True:
            result = await person_fact_reverification.reclassify_historical_person_facts(cursor, limit=37)
            cursor = result["next_cursor"]
            promoted += result["promoted"]
            if not result["has_more"]:
                break
        assert promoted == 257
        assert len(store.list_current_person_fact_claims("test:alice", limit=300)) == 257
        assert len(store.list_uncertain_person_fact_claims("test:alice")) == 0
        assert (await person_fact_reverification.reclassify_historical_person_facts("", limit=300))["promoted"] == 0
    finally:
        store.close()
