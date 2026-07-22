from typing import Any, Dict, List, Optional

import pytest

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.storage.metadata_store import MetadataStore


class _FakeMetadataStore:
    def __init__(self) -> None:
        self.episode_sources: List[str] = []
        self.profile_refreshes: List[Dict[str, str]] = []
        self.external_refs: List[Dict[str, Any]] = []
        self.paragraphs: List[Dict[str, Any]] = []
        self.fact_claims: List[Dict[str, Any]] = []
        self.paragraph_metadata_updates: List[Dict[str, Any]] = []

    def get_external_memory_ref(self, external_id: str) -> Optional[Dict[str, Any]]:
        del external_id
        return None

    def add_paragraph(self, **kwargs: Any) -> str:
        self.paragraphs.append(dict(kwargs))
        source = str(kwargs.get("source", "") or "").strip()
        if source:
            self.episode_sources.append(source)
        return f"paragraph-{len(self.paragraphs)}"

    def add_entity(self, *, name: str, source_paragraph: str) -> str:
        return f"entity:{name}:{source_paragraph}"

    def upsert_external_memory_ref(self, **kwargs: Any) -> None:
        self.external_refs.append(dict(kwargs))

    def upsert_fact_claim(self, **kwargs: Any) -> Dict[str, Any]:
        payload = {"claim_id": f"claim-{len(self.fact_claims) + 1}", **kwargs}
        self.fact_claims.append(payload)
        return payload

    def update_paragraph_metadata(self, paragraph_hash: str, patch: Dict[str, Any], *, merge: bool):
        self.paragraph_metadata_updates.append(
            {"paragraph_hash": paragraph_hash, "patch": dict(patch), "merge": merge}
        )
        return dict(patch)

    def enqueue_person_profile_refresh(
        self,
        *,
        person_id: str,
        reason: str = "",
        source_query_tool_id: str = "",
    ) -> Dict[str, Any]:
        payload = {
            "person_id": person_id,
            "reason": reason,
            "source_query_tool_id": source_query_tool_id,
        }
        self.profile_refreshes.append(payload)
        return payload


class _FakeProfileRefreshRequestStore:
    def __init__(self, request: Dict[str, Any] | None) -> None:
        self.request = request

    def get_person_profile_refresh_request(self, person_id: str) -> Dict[str, Any] | None:
        assert person_id == "person-1"
        return self.request


class _FakeVectorResult:
    @staticmethod
    async def upsert_relation_with_vector(**kwargs: Any) -> Dict[str, Any]:
        del kwargs
        raise AssertionError("relations are not expected in this test")


async def _fake_initialize() -> None:
    return None


def _build_kernel(tmp_path, config: Dict[str, Any]) -> tuple[SDKMemoryKernel, _FakeMetadataStore]:
    kernel = SDKMemoryKernel(plugin_root=tmp_path, config=config)
    metadata_store = _FakeMetadataStore()
    kernel.metadata_store = metadata_store  # type: ignore[assignment]
    kernel.vector_store = object()  # type: ignore[assignment]
    kernel.graph_store = object()  # type: ignore[assignment]
    kernel.embedding_manager = object()
    kernel.relation_write_service = _FakeVectorResult()  # type: ignore[assignment]
    kernel.initialize = _fake_initialize  # type: ignore[method-assign]
    kernel._persist = lambda *args, **kwargs: None  # type: ignore[method-assign]

    async def fake_vector_write(**kwargs: Any) -> Dict[str, Any]:
        del kwargs
        return {}

    async def fake_entity_vector(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return True

    async def fail_episode_processing(**kwargs: Any) -> Dict[str, Any]:
        del kwargs
        raise AssertionError("ingest_text must not process episode pending synchronously")

    async def fail_profile_refresh(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        del args, kwargs
        raise AssertionError("ingest_text must enqueue profile refresh instead of refreshing immediately")

    kernel._write_paragraph_vector_or_enqueue = fake_vector_write  # type: ignore[method-assign]
    kernel._ensure_entity_vector = fake_entity_vector  # type: ignore[method-assign]
    kernel.process_episode_source_rebuild_batch = fail_episode_processing  # type: ignore[method-assign]
    kernel.refresh_person_profile = fail_profile_refresh  # type: ignore[method-assign]
    return kernel, metadata_store


@pytest.mark.asyncio
async def test_person_fact_ingest_skips_episode_and_debounces_profile_refresh(tmp_path) -> None:
    kernel, metadata_store = _build_kernel(tmp_path, config={})

    result = await kernel.ingest_text(
        external_id="fact-1",
        source_type="person_fact",
        text="测试用户喜欢猫。",
        person_ids=["person-1"],
        participants=["测试用户"],
        metadata={
            "writeback_source": "memory_flow_service",
            "evidence_source": "user_supported",
            "fact_claim": {
                "authority": "manual",
                "stability": "stable",
                "profile_section": "stable_facts",
            },
        },
    )

    assert result["stored_ids"] == ["paragraph-1"]
    assert result["fact_claim_ids"] == ["claim-1"]
    assert metadata_store.fact_claims[0]["value_text"] == "测试用户喜欢猫。"
    assert metadata_store.fact_claims[0]["evidence_id"] == "paragraph-1"
    assert metadata_store.fact_claims[0]["authority"] == "summary_derived"
    assert metadata_store.fact_claims[0]["stability"] == "uncertain"
    assert metadata_store.fact_claims[0]["profile_section"] == "uncertain_notes"
    assert metadata_store.episode_sources == ["person_fact:person-1"]
    assert metadata_store.profile_refreshes == [
        {
            "person_id": "person-1",
            "reason": "person_fact",
            "source_query_tool_id": "",
        }
    ]


@pytest.mark.asyncio
async def test_only_explicit_manual_confirmation_promotes_person_fact_to_stable(tmp_path) -> None:
    kernel, metadata_store = _build_kernel(tmp_path, config={})

    await kernel.ingest_text(
        external_id="fact-confirmed",
        source_type="person_fact",
        text="测试用户喜欢猫。",
        person_ids=["person-1"],
        metadata={
            "writeback_source": "memory_flow_service",
            "evidence_source": "user_supported",
            "fact_claim": {
                "trust": "manual_confirmed",
                "fact_key": "preference:pet",
                "authority": "manual",
                "stability": "stable",
            },
        },
    )

    claim = metadata_store.fact_claims[0]
    assert claim["fact_key"] == "preference:pet"
    assert claim["authority"] == "manual"
    assert claim["stability"] == "stable"
    assert claim["profile_section"] == "stable_facts"


@pytest.mark.asyncio
async def test_memory_ingest_marks_source_without_synchronous_processing(tmp_path) -> None:
    kernel, metadata_store = _build_kernel(tmp_path, config={})

    result = await kernel.ingest_text(
        external_id="memory-1",
        source_type="memory",
        text="用户今天讨论了绿色围巾。",
        chat_id="session-1",
    )

    assert result["stored_ids"] == ["paragraph-1"]
    assert metadata_store.episode_sources == ["memory:session-1"]
    assert metadata_store.profile_refreshes == []


@pytest.mark.asyncio
async def test_episode_generation_disabled_keeps_source_mutation_for_later_rebuild(tmp_path) -> None:
    kernel, metadata_store = _build_kernel(
        tmp_path,
        config={"episode": {"generation_enabled": False}},
    )

    await kernel.ingest_text(
        external_id="memory-1",
        source_type="memory",
        text="用户今天讨论了绿色围巾。",
        chat_id="session-1",
    )

    assert metadata_store.episode_sources == ["memory:session-1"]


def test_person_profile_refresh_queue_debounce_and_retry_backoff(tmp_path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        first = store.enqueue_person_profile_refresh(person_id="person-1", reason="first")
        second = store.enqueue_person_profile_refresh(person_id="person-1", reason="second")

        assert first is not None
        assert second is not None
        assert second["reason"] == "second"
        assert store.fetch_person_profile_refresh_batch(limit=10, debounce_seconds=3600) == []

        ready = store.fetch_person_profile_refresh_batch(limit=10, debounce_seconds=0)
        assert [row["person_id"] for row in ready] == ["person-1"]

        assert store.mark_person_profile_refresh_running("person-1", requested_at=ready[0]["requested_at"])
        assert store.mark_person_profile_refresh_failed(
            "person-1",
            "boom",
            requested_at=ready[0]["requested_at"],
        )
        assert (
            store.fetch_person_profile_refresh_batch(
                limit=10,
                max_retry=3,
                retry_backoff_seconds=3600,
            )
            == []
        )
        retry_ready = store.fetch_person_profile_refresh_batch(
            limit=10,
            max_retry=3,
            retry_backoff_seconds=0,
        )
        assert [row["person_id"] for row in retry_ready] == ["person-1"]
        assert retry_ready[0]["retry_count"] == 1
    finally:
        store.close()


def test_has_pending_person_profile_refresh_ignores_failed_after_max_retry(tmp_path) -> None:
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path,
        config={"person_profile": {"max_retry": 3}},
    )

    kernel.metadata_store = _FakeProfileRefreshRequestStore(  # type: ignore[assignment]
        {"person_id": "person-1", "status": "pending", "retry_count": 0}
    )
    assert kernel._has_pending_person_profile_refresh("person-1") is True

    kernel.metadata_store = _FakeProfileRefreshRequestStore(  # type: ignore[assignment]
        {"person_id": "person-1", "status": "running", "retry_count": 0}
    )
    assert kernel._has_pending_person_profile_refresh("person-1") is True

    kernel.metadata_store = _FakeProfileRefreshRequestStore(  # type: ignore[assignment]
        {"person_id": "person-1", "status": "failed", "retry_count": 2}
    )
    assert kernel._has_pending_person_profile_refresh("person-1") is True

    kernel.metadata_store = _FakeProfileRefreshRequestStore(  # type: ignore[assignment]
        {"person_id": "person-1", "status": "failed", "retry_count": 3}
    )
    assert kernel._has_pending_person_profile_refresh("person-1") is False
