from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncio

import pytest

from src.A_memorix.core.retrieval import RetrievalResult
from src.A_memorix.core.runtime.sdk_memory_kernel import KernelSearchRequest, SDKMemoryKernel
from src.A_memorix.core.storage.graph_store import GraphStore
from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.utils.search_execution_service import (
    SearchExecutionRequest,
    SearchExecutionResult,
    SearchExecutionService,
)


def _relation_result(
    relation_hash: str,
    *,
    score: float,
    person_id: str = "person-target",
    chat_id: str = "session-current",
    metadata: dict[str, Any] | None = None,
) -> RetrievalResult:
    item_metadata: dict[str, Any] = {
        "person_ids": [person_id],
        "chat_id": chat_id,
        "source_type": "chat_summary",
        "source": f"chat_summary:{chat_id}",
    }
    if metadata:
        item_metadata.update(metadata)
    return RetrievalResult(
        hash_value=relation_hash,
        content=f"关系 {relation_hash}",
        score=score,
        result_type="relation",
        source="relation_search",
        metadata=item_metadata,
    )


def _prepare_search_kernel(kernel: SDKMemoryKernel, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_initialize() -> None:
        return None

    kernel.retriever = object()
    kernel.episode_retriever = object()  # type: ignore[assignment]
    kernel.aggregate_query_service = object()  # type: ignore[assignment]
    monkeypatch.setattr(kernel, "initialize", fake_initialize)


@pytest.mark.asyncio
async def test_search_execution_has_no_access_lifecycle_side_effect() -> None:
    reinforced: list[list[str]] = []

    class FakeRuntime:
        async def reinforce_access(self, relation_hashes: list[str]) -> None:
            reinforced.append(list(relation_hashes))

    class FakeRetriever:
        async def retrieve(self, **kwargs: Any) -> list[RetrievalResult]:
            del kwargs
            return [_relation_result("relation-candidate", score=1.0)]

    result = await SearchExecutionService.execute(
        retriever=FakeRetriever(),
        threshold_filter=None,
        plugin_config={
            "plugin_instance": FakeRuntime(),
            "retrieval": {
                "search": {
                    "smart_fallback": {"enabled": False},
                    "safe_content_dedup": {"enabled": False},
                }
            },
        },
        request=SearchExecutionRequest(
            caller="access-boundary-test",
            query="候选",
            top_k=1,
            use_threshold=False,
        ),
        enforce_chat_filter=False,
    )

    assert result.success is True
    assert [item.hash_value for item in result.results] == ["relation-candidate"]
    assert reinforced == []


@pytest.mark.asyncio
async def test_limit_five_reinforces_only_five_of_twenty_five_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    _prepare_search_kernel(kernel, monkeypatch)
    candidates = [_relation_result(f"relation-{index:02d}", score=100.0 - index) for index in range(25)]
    requested_top_k: list[int] = []
    reinforced: list[list[str]] = []

    async def fake_search_execution_for_chat_scope(**kwargs: Any) -> SimpleNamespace:
        requested_top_k.append(int(kwargs["top_k"]))
        return SimpleNamespace(success=True, error="", chat_filtered=False, results=candidates)

    async def fake_reinforce_access(relation_hashes: list[str]) -> None:
        reinforced.append(list(relation_hashes))

    monkeypatch.setattr(kernel, "_search_execution_for_chat_scope", fake_search_execution_for_chat_scope)
    monkeypatch.setattr(kernel._runtime_facade, "reinforce_access", fake_reinforce_access)

    result = await kernel.search_memory(
        KernelSearchRequest(
            query="关系",
            limit=5,
            chat_id="session-current",
            respect_filter=False,
        )
    )

    final_hashes = [str(item["hash"]) for item in result["hits"]]
    assert requested_top_k == [25]
    assert len(candidates) == 25
    assert final_hashes == [f"relation-{index:02d}" for index in range(5)]
    assert reinforced == [final_hashes]


@pytest.mark.asyncio
async def test_ten_chat_merge_reinforces_only_final_five_of_five_hundred_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    _prepare_search_kernel(kernel, monkeypatch)
    search_calls: list[dict[str, Any]] = []
    reinforced: list[list[str]] = []

    async def fake_search_execution_once(**kwargs: Any) -> SearchExecutionResult:
        source = str(kwargs["source"])
        chat_index = int(source.rsplit("-", 1)[1])
        top_k = int(kwargs["top_k"])
        search_calls.append({"source": source, "top_k": top_k})
        results = [
            _relation_result(
                f"relation-{chat_index:02d}-{index:02d}",
                score=float(chat_index * 100 + index),
                chat_id=f"session-{chat_index}",
            )
            for index in range(top_k)
        ]
        return SearchExecutionResult(success=True, results=results)

    async def fake_reinforce_access(relation_hashes: list[str]) -> None:
        reinforced.append(list(relation_hashes))

    monkeypatch.setattr(kernel, "_search_execution_once", fake_search_execution_once)
    monkeypatch.setattr(kernel._runtime_facade, "reinforce_access", fake_reinforce_access)

    result = await kernel.search_memory(
        KernelSearchRequest(
            query="跨聊天关系",
            limit=5,
            chat_id="session-0",
            shared_chat_ids=tuple(f"session-{index}" for index in range(1, 10)),
            respect_filter=False,
        )
    )

    final_hashes = [str(item["hash"]) for item in result["hits"]]
    assert len(search_calls) == 10
    assert {item["top_k"] for item in search_calls} == {50}
    assert sum(int(item["top_k"]) for item in search_calls) == 500
    assert len(final_hashes) == 5
    assert reinforced == [final_hashes]


class _FilteringMetadataStore:
    def __init__(self, metadata_by_hash: dict[str, dict[str, Any]]) -> None:
        self.metadata_by_hash = metadata_by_hash

    def get_relation_status_batch(self, hashes: list[str]) -> dict[str, dict[str, Any]]:
        return {
            relation_hash: {"is_inactive": relation_hash == "relation-inactive"}
            for relation_hash in hashes
        }

    def get_relations_by_hashes(self, hashes: list[str]) -> dict[str, dict[str, Any]]:
        return {
            relation_hash: {
                "hash": relation_hash,
                "metadata": dict(self.metadata_by_hash[relation_hash]),
            }
            for relation_hash in hashes
            if relation_hash in self.metadata_by_hash
        }

    def get_paragraphs_by_hashes(self, hashes: list[str]) -> dict[str, dict[str, Any]]:
        return {
            paragraph_hash: {
                "hash": paragraph_hash,
                "metadata": dict(self.metadata_by_hash[paragraph_hash]),
                "source": self.metadata_by_hash[paragraph_hash].get("source", ""),
            }
            for paragraph_hash in hashes
            if paragraph_hash in self.metadata_by_hash
        }

    def get_paragraphs_by_relation_hashes(self, hashes: list[str]) -> dict[str, list[dict[str, Any]]]:
        return {relation_hash: [] for relation_hash in hashes}

    def get_paragraph_relations(self, paragraph_hash: str) -> list[dict[str, Any]]:
        del paragraph_hash
        return []

    def get_paragraph_stale_relation_marks_batch(
        self,
        paragraph_hashes: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        return {paragraph_hash: [] for paragraph_hash in paragraph_hashes}


@pytest.mark.asyncio
async def test_person_visibility_and_type_filtered_hits_are_not_reinforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(
        plugin_root=Path.cwd(),
        config={
            "filter": {
                "retrieval": {
                    "chat_summary": {
                        "enabled": True,
                        "mode": "whitelist",
                        "chats": ["stream:session-current"],
                    }
                }
            }
        },
    )
    _prepare_search_kernel(kernel, monkeypatch)
    candidates = [
        _relation_result("relation-keep-1", score=10.0),
        _relation_result("relation-person", score=9.0, person_id="person-other"),
        _relation_result("relation-inactive", score=8.0),
        _relation_result("relation-expired", score=7.0, metadata={"memory_change": {"valid_to": 1.0}}),
        _relation_result("relation-type", score=6.0, chat_id="session-other"),
        RetrievalResult(
            hash_value="paragraph-keep",
            content="人物可见段落",
            score=5.0,
            result_type="paragraph",
            source="paragraph_search",
            metadata={
                "person_ids": ["person-target"],
                "chat_id": "session-current",
                "source_type": "chat_summary",
                "source": "chat_summary:session-current",
            },
        ),
        _relation_result("relation-keep-2", score=4.0),
    ]
    metadata_by_hash = {item.hash_value: dict(item.metadata) for item in candidates}
    kernel.metadata_store = _FilteringMetadataStore(metadata_by_hash)  # type: ignore[assignment]
    reinforced: list[list[str]] = []

    async def fake_search_execution_for_chat_scope(**kwargs: Any) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(success=True, error="", chat_filtered=False, results=candidates)

    async def fake_reinforce_access(relation_hashes: list[str]) -> None:
        reinforced.append(list(relation_hashes))

    monkeypatch.setattr(kernel, "_search_execution_for_chat_scope", fake_search_execution_for_chat_scope)
    monkeypatch.setattr(kernel._runtime_facade, "reinforce_access", fake_reinforce_access)

    result = await kernel.search_memory(
        KernelSearchRequest(
            query="可见关系",
            limit=10,
            chat_id="session-current",
            shared_chat_ids=("session-other",),
            person_id="person-target",
            respect_filter=True,
        )
    )

    final_hashes = [str(item["hash"]) for item in result["hits"]]
    assert final_hashes == ["relation-keep-1", "paragraph-keep", "relation-keep-2"]
    assert reinforced == [["relation-keep-1", "relation-keep-2"]]
    assert {
        "relation-person",
        "relation-inactive",
        "relation-expired",
        "relation-type",
    }.isdisjoint(reinforced[0])


@pytest.mark.asyncio
async def test_duplicate_final_relation_hash_is_reinforced_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    _prepare_search_kernel(kernel, monkeypatch)
    candidates = [
        _relation_result("relation-duplicate", score=10.0),
        _relation_result("relation-duplicate", score=9.0),
        _relation_result("relation-other", score=8.0),
    ]
    reinforced: list[list[str]] = []

    async def fake_search_execution_for_chat_scope(**kwargs: Any) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(success=True, error="", chat_filtered=False, results=candidates)

    async def fake_reinforce_access(relation_hashes: list[str]) -> None:
        reinforced.append(list(relation_hashes))

    monkeypatch.setattr(kernel, "_search_execution_for_chat_scope", fake_search_execution_for_chat_scope)
    monkeypatch.setattr(kernel._runtime_facade, "reinforce_access", fake_reinforce_access)

    result = await kernel.search_memory(
        KernelSearchRequest(query="重复关系", limit=3, respect_filter=False)
    )

    assert [item["hash"] for item in result["hits"]] == [
        "relation-duplicate",
        "relation-duplicate",
        "relation-other",
    ]
    assert reinforced == [["relation-duplicate", "relation-other"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["time", "hybrid"])
async def test_time_and_hybrid_reinforce_only_relations_after_final_limit(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    _prepare_search_kernel(kernel, monkeypatch)
    candidates = [
        _relation_result(f"{mode}-relation-{index}", score=20.0 - index)
        for index in range(10)
    ]
    executed_modes: list[str] = []
    reinforced: list[list[str]] = []

    async def fake_search_execution_for_chat_scope(**kwargs: Any) -> SimpleNamespace:
        executed_modes.append(str(kwargs["query_type"]))
        assert kwargs["top_k"] == 10
        return SimpleNamespace(success=True, error="", chat_filtered=False, results=candidates)

    async def fake_reinforce_access(relation_hashes: list[str]) -> None:
        reinforced.append(list(relation_hashes))

    monkeypatch.setattr(kernel, "_search_execution_for_chat_scope", fake_search_execution_for_chat_scope)
    monkeypatch.setattr(kernel._runtime_facade, "reinforce_access", fake_reinforce_access)

    result = await kernel.search_memory(
        KernelSearchRequest(
            query="时间关系",
            mode=mode,
            limit=2,
            chat_id="session-current",
            time_start=1_700_000_000.0,
            time_end=1_700_003_600.0,
            respect_filter=False,
        )
    )

    final_hashes = [str(item["hash"]) for item in result["hits"]]
    assert executed_modes == [mode]
    assert final_hashes == [f"{mode}-relation-0", f"{mode}-relation-1"]
    assert reinforced == [final_hashes]


@pytest.mark.asyncio
async def test_episode_results_never_submit_relation_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEpisodeRetriever:
        async def query(self, **kwargs: Any) -> list[dict[str, Any]]:
            del kwargs
            return [
                {
                    "episode_id": f"episode-{index}",
                    "title": f"Episode {index}",
                    "summary": f"情节记忆 {index}",
                    "lexical_score": 1.0 - index / 10.0,
                    "participants": ["person-target"],
                    "source": "chat_summary:session-current",
                }
                for index in range(3)
            ]

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    _prepare_search_kernel(kernel, monkeypatch)
    kernel.episode_retriever = FakeEpisodeRetriever()  # type: ignore[assignment]
    reinforced: list[list[str]] = []

    async def fake_reinforce_access(relation_hashes: list[str]) -> None:
        reinforced.append(list(relation_hashes))

    monkeypatch.setattr(kernel._runtime_facade, "reinforce_access", fake_reinforce_access)

    result = await kernel.search_memory(
        KernelSearchRequest(query="情节", mode="episode", limit=2, respect_filter=False)
    )

    assert [item["episode_id"] for item in result["hits"]] == ["episode-0", "episode-1"]
    assert reinforced == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["error", "chat_filtered", "empty"])
async def test_non_adopted_search_outcomes_do_not_submit_access(
    scenario: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    _prepare_search_kernel(kernel, monkeypatch)
    reinforced: list[list[str]] = []

    async def fake_search_execution_for_chat_scope(**kwargs: Any) -> SearchExecutionResult:
        del kwargs
        if scenario == "error":
            return SearchExecutionResult(
                success=False,
                error="retrieval failed",
                results=[_relation_result("relation-error", score=1.0)],
            )
        if scenario == "chat_filtered":
            return SearchExecutionResult(
                success=True,
                chat_filtered=True,
                results=[_relation_result("relation-filtered", score=1.0)],
            )
        return SearchExecutionResult(success=True, results=[])

    async def fake_reinforce_access(relation_hashes: list[str]) -> None:
        reinforced.append(list(relation_hashes))

    monkeypatch.setattr(kernel, "_search_execution_for_chat_scope", fake_search_execution_for_chat_scope)
    monkeypatch.setattr(kernel._runtime_facade, "reinforce_access", fake_reinforce_access)

    result = await kernel.search_memory(
        KernelSearchRequest(query="边界结果", limit=5, respect_filter=False)
    )

    if scenario == "error":
        assert result == {"summary": "", "hits": [], "error": "retrieval failed"}
    elif scenario == "chat_filtered":
        assert result == {"summary": "", "hits": [], "filtered": True}
    else:
        assert result == {"summary": "", "hits": []}
    assert reinforced == []


@pytest.mark.asyncio
async def test_single_flight_two_consumers_submit_two_accesses_but_one_cooldown_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockingRetriever:
        def __init__(self) -> None:
            self.calls = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def retrieve(self, **kwargs: Any) -> list[RetrievalResult]:
            del kwargs
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            return [
                RetrievalResult(
                    hash_value=relation_hash,
                    content="Alice 认识 Bob",
                    score=1.0,
                    result_type="relation",
                    source="relation_search",
                    metadata={},
                )
            ]

    metadata_store = MetadataStore(data_dir=tmp_path / "metadata")
    metadata_store.connect()
    try:
        relation_hash = metadata_store.add_relation("Alice", "认识", "Bob")
        kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
        _prepare_search_kernel(kernel, monkeypatch)
        retriever = BlockingRetriever()
        kernel.retriever = retriever  # type: ignore[assignment]
        kernel.metadata_store = metadata_store
        kernel.graph_store = GraphStore(data_dir=tmp_path / "graph")
        dedup_consumers = 0
        both_consumers_waiting = asyncio.Event()
        access_submissions: list[list[str]] = []
        original_execute_dedup = kernel.execute_request_with_dedup
        original_reinforce_access = kernel._runtime_facade.reinforce_access

        async def counted_execute_request_with_dedup(*args: Any, **kwargs: Any) -> tuple[bool, dict[str, Any]]:
            nonlocal dedup_consumers
            dedup_consumers += 1
            if dedup_consumers == 2:
                both_consumers_waiting.set()
            return await original_execute_dedup(*args, **kwargs)

        async def counted_reinforce_access(relation_hashes: list[str]) -> None:
            access_submissions.append(list(relation_hashes))
            await original_reinforce_access(relation_hashes)

        monkeypatch.setattr(kernel, "execute_request_with_dedup", counted_execute_request_with_dedup)
        monkeypatch.setattr(kernel._runtime_facade, "reinforce_access", counted_reinforce_access)
        request = KernelSearchRequest(query="谁认识 Bob", limit=1, respect_filter=False)

        first = asyncio.create_task(kernel.search_memory(request))
        await retriever.entered.wait()
        second = asyncio.create_task(kernel.search_memory(request))
        await both_consumers_waiting.wait()
        retriever.release.set()
        first_result, second_result = await asyncio.gather(first, second)

        status = metadata_store.get_relation_status_batch([relation_hash])[relation_hash]
        relation = metadata_store.get_relation(relation_hash)
        assert relation is not None
        assert retriever.calls == 1
        assert dedup_consumers == 2
        assert [item["hash"] for item in first_result["hits"]] == [relation_hash]
        assert [item["hash"] for item in second_result["hits"]] == [relation_hash]
        assert access_submissions == [[relation_hash], [relation_hash]]
        assert relation["access_count"] == 2
        assert status["lifecycle_revision"] == 1
        assert status["reinforcement_count"] == 0
    finally:
        metadata_store.close()


@pytest.mark.asyncio
async def test_aggregate_reinforces_only_post_mix_final_relations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAggregateQueryService:
        async def execute(self, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            return {
                "mixed_results": [
                    {
                        "hash": f"aggregate-relation-{index}",
                        "type": "relation",
                        "content": f"聚合关系 {index}",
                        "score": 100.0 - index,
                        "metadata": {},
                    }
                    for index in range(20)
                ]
            }

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    _prepare_search_kernel(kernel, monkeypatch)
    kernel.aggregate_query_service = FakeAggregateQueryService()  # type: ignore[assignment]
    reinforced: list[list[str]] = []

    async def fake_reinforce_access(relation_hashes: list[str]) -> None:
        reinforced.append(list(relation_hashes))

    monkeypatch.setattr(kernel._runtime_facade, "reinforce_access", fake_reinforce_access)

    result = await kernel.search_memory(
        KernelSearchRequest(query="聚合关系", mode="aggregate", limit=5, respect_filter=False)
    )

    final_hashes = [str(item["hash"]) for item in result["hits"]]
    assert final_hashes == [f"aggregate-relation-{index}" for index in range(5)]
    assert reinforced == [final_hashes]
