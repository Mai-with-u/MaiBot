from __future__ import annotations

from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any, Dict, List, Tuple
import asyncio

import numpy as np
import pytest

from src.A_memorix.core.retrieval.dual_path import (
    DualPathRetriever,
    DualPathRetrieverConfig,
    RetrievalResult,
    TemporalQueryOptions,
    VectorPoolsConfig,
)
from src.A_memorix.core.retrieval.sparse_bm25 import SparseBM25Config
from src.A_memorix.core.storage.graph_store import GraphStore
from src.A_memorix.core.storage.vector_store import HAS_FAISS, VectorStore
from src.A_memorix.core.utils.episode_service import EpisodeService
from src.A_memorix.core.utils.search_execution_service import (
    SearchExecutionRequest,
    SearchExecutionService,
)


class _PluginStub:
    pass


class _ConcurrentRequestRetriever:
    def __init__(self) -> None:
        self.config = SimpleNamespace(enable_ppr=True)
        self._entered = 0
        self._both_entered = asyncio.Event()
        self.observed: Dict[str, Tuple[bool, bool]] = {}

    async def retrieve(
        self,
        *,
        query: str,
        top_k: int,
        temporal: Any,
        enable_ppr: bool,
    ) -> List[RetrievalResult]:
        del top_k, temporal
        self._entered += 1
        if self._entered == 2:
            self._both_entered.set()
        await self._both_entered.wait()
        self.observed[query] = (enable_ppr, bool(self.config.enable_ppr))
        return []


@pytest.mark.asyncio
async def test_search_execution_keeps_ppr_switch_request_local() -> None:
    retriever = _ConcurrentRequestRetriever()
    plugin_config = {
        "plugin_instance": _PluginStub(),
        "retrieval": {
            "search": {
                "smart_fallback": {"enabled": False},
                "safe_content_dedup": {"enabled": False},
            }
        },
    }

    async def execute(query: str, enable_ppr: bool) -> None:
        result = await SearchExecutionService.execute(
            retriever=retriever,
            threshold_filter=None,
            plugin_config=plugin_config,
            request=SearchExecutionRequest(
                caller="test",
                query=query,
                top_k=1,
                use_threshold=False,
                enable_ppr=enable_ppr,
            ),
            enforce_chat_filter=False,
        )
        assert result.success is True

    await asyncio.gather(execute("ppr-on", True), execute("ppr-off", False))

    assert retriever.observed == {
        "ppr-on": (True, True),
        "ppr-off": (False, True),
    }
    assert retriever.config.enable_ppr is True


@pytest.mark.asyncio
async def test_dual_path_retriever_propagates_request_ppr_switch() -> None:
    retriever = DualPathRetriever(
        vector_store=None,
        graph_store=GraphStore(),
        metadata_store=None,
        embedding_manager=None,
    )
    observed: List[bool] = []

    async def fake_dual_path(
        self: DualPathRetriever,
        query: str,
        top_k: int,
        temporal: Any = None,
        relation_intent: Any = None,
        enable_ppr: bool = True,
    ) -> List[RetrievalResult]:
        del self, query, top_k, temporal, relation_intent
        observed.append(enable_ppr)
        return []

    retriever._retrieve_dual_path = MethodType(fake_dual_path, retriever)

    await retriever.retrieve("on", enable_ppr=True)
    await retriever.retrieve("off", enable_ppr=False)

    assert observed == [True, False]
    assert retriever.config.enable_ppr is True


@pytest.mark.skipif(not HAS_FAISS, reason="Faiss 未安装")
def test_vector_add_rejects_cardinality_mismatch_without_state_change(tmp_path: Path) -> None:
    store = VectorStore(dimension=4, data_dir=tmp_path / "vectors")

    with pytest.raises(ValueError, match="Vector/ID count mismatch"):
        store.add(
            np.asarray([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32),
            ["id-1", "id-2"],
        )

    assert store.num_vectors == 0
    assert store._known_hashes == set()
    assert store._write_buffer_ids == []

    added = store.add(np.eye(4, dtype=np.float32)[:2], ["id-1", "id-2"])
    found, _scores = store.search(np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float32), k=2)

    assert added == 2
    assert set(found) == {"id-1", "id-2"}


class _EpisodeMetadataStub:
    def get_paragraph_entities(self, _hash_value: str) -> List[Dict[str, Any]]:
        return []


class _IncompleteEpisodeSegmentationStub:
    async def segment(self, **_kwargs: Any) -> Dict[str, Any]:
        return {
            "segmentation_model": "test",
            "segmentation_version": "test-v1",
            "episodes": [
                {
                    "paragraph_hashes": ["a", "b"],
                    "title": "一",
                    "summary": "a、b",
                },
                {
                    "paragraph_hashes": ["b"],
                    "title": "二",
                    "summary": "重复 b",
                },
            ],
        }


@pytest.mark.asyncio
async def test_episode_invalid_partition_falls_back_to_complete_partition() -> None:
    service = EpisodeService(
        metadata_store=_EpisodeMetadataStub(),
        segmentation_service=_IncompleteEpisodeSegmentationStub(),
    )
    group = {
        "source": "test",
        "_input_fingerprint": "fixed",
        "paragraphs": [
            {"hash": "a", "content": "A", "created_at": 1.0},
            {"hash": "b", "content": "B", "created_at": 2.0},
            {"hash": "c", "content": "C", "created_at": 3.0},
        ],
    }

    result = await service._build_episode_payloads_for_group(group)

    assert result["fallback_count"] == 1
    assert result["done_hashes"] == ["a", "b", "c"]
    assert len(result["payloads"]) == 1
    assert result["payloads"][0]["evidence_ids"] == ["a", "b", "c"]


class _EmbeddingStub:
    async def encode(self, _query: str) -> np.ndarray:
        return np.asarray([1.0], dtype=np.float32)


class _RankedVectorStore:
    dimension = 1

    def __init__(self, ids: List[str]) -> None:
        self.ids = ids
        self.requested_k: List[int] = []

    def search(self, _query: np.ndarray, k: int = 10) -> Tuple[List[str], List[float]]:
        self.requested_k.append(k)
        ids = self.ids[:k]
        return ids, [1.0 - index * 0.001 for index in range(len(ids))]


class _EmptyVectorStore:
    dimension = 1

    def search(self, _query: np.ndarray, k: int = 10) -> Tuple[List[str], List[float]]:
        del k
        return [], []


class _ScopedMetadataStore:
    def __init__(self, relevant_rank: int, total: int) -> None:
        self.paragraphs = {
            f"p{index:03d}": {
                "hash": f"p{index:03d}",
                "content": f"paragraph {index}",
                "source": "target" if index == relevant_rank else "other",
                "created_at": float(index),
                "word_count": 2,
            }
            for index in range(1, total + 1)
        }

    def get_paragraphs_by_hashes(self, hashes: List[str]) -> Dict[str, Dict[str, Any]]:
        return {hash_value: self.paragraphs[hash_value] for hash_value in hashes if hash_value in self.paragraphs}

    def get_relations_by_hashes(self, _hashes: List[str], include_inactive: bool = False) -> Dict[str, Any]:
        del include_inactive
        return {}

    def get_entities_by_hashes(self, _hashes: List[str]) -> Dict[str, Any]:
        return {}

    def get_paragraphs_by_relation_hashes(self, _hashes: List[str]) -> Dict[str, Any]:
        return {}

    def get_paragraphs_by_entity_hashes(self, _hashes: List[str]) -> Dict[str, Any]:
        return {}

    def get_paragraph_entities(self, _hash_value: str) -> List[Dict[str, Any]]:
        return []


@pytest.mark.asyncio
async def test_dual_pool_expands_candidates_before_source_filter() -> None:
    relevant_rank = 80
    ranked_ids = [f"p{index:03d}" for index in range(1, 501)]
    paragraph_store = _RankedVectorStore(ranked_ids)
    retriever = DualPathRetriever(
        vector_store=paragraph_store,
        paragraph_vector_store=paragraph_store,
        graph_vector_store=_EmptyVectorStore(),
        graph_store=GraphStore(),
        metadata_store=_ScopedMetadataStore(relevant_rank, len(ranked_ids)),
        embedding_manager=_EmbeddingStub(),
        config=DualPathRetrieverConfig(
            enable_ppr=False,
            sparse=SparseBM25Config(enabled=False),
            vector_pools=VectorPoolsConfig(mode="dual", paragraph_top_k=20, graph_top_k=1),
        ),
    )

    results = await retriever.retrieve(
        "target",
        top_k=50,
        temporal=TemporalQueryOptions(source="target", candidate_multiplier=8, max_scan=1000),
        enable_ppr=False,
    )

    assert paragraph_store.requested_k == [400]
    assert [item.hash_value for item in results] == ["p080"]


def test_full_ppr_canonicalizes_personalization_nodes() -> None:
    graph = GraphStore()
    graph.add_edges([("Alice", "Bob"), ("Bob", "Carol"), ("Carol", "Carol")])

    original_case = graph.compute_pagerank({"Alice": 1.0})
    canonical_case = graph.compute_pagerank({"alice": 1.0})
    uniform = graph.compute_pagerank()

    assert original_case == pytest.approx(canonical_case)
    assert original_case != pytest.approx(uniform)


def test_local_ppr_preserves_relative_edge_weights() -> None:
    graph = GraphStore()
    graph.add_edges(
        [("Seed", "Heavy"), ("Seed", "Light")],
        weights=[9.0, 1.0],
    )
    retriever = DualPathRetriever(
        vector_store=None,
        graph_store=graph,
        metadata_store=None,
        embedding_manager=None,
        config=DualPathRetrieverConfig(
            ppr_local_enabled=True,
            ppr_local_min_graph_nodes=0,
        ),
    )

    scores = retriever._compute_local_ppr_scores({"Seed": 1.0})

    assert scores["Heavy"] / scores["Light"] == pytest.approx(9.0, rel=1e-6)


def test_graph_edge_upsert_has_same_semantics_in_all_modes() -> None:
    edges = [("A", "B"), ("A", "B"), ("A", "C")]
    weights = [0.2, 0.8, 1.0]

    incremental = GraphStore()
    with incremental.batch_update():
        incremental.add_edges(edges, weights=weights, relation_hashes=["r1", "r2", "r3"])

    batch = GraphStore()
    batch.add_edges(edges, weights=weights, relation_hashes=["r1", "r2", "r3"])
    batch.add_edges([("A", "B")], weights=[0.8], relation_hashes=["r2"])

    assert incremental.get_edge_weight("A", "B") == pytest.approx(0.8)
    assert batch.get_edge_weight("A", "B") == pytest.approx(0.8)
    assert incremental.get_edge_weight("A", "C") == pytest.approx(1.0)
    assert batch.get_edge_weight("A", "C") == pytest.approx(1.0)
    assert incremental.get_relation_hashes_for_edge("A", "B") == {"r1", "r2"}
    assert batch.get_relation_hashes_for_edge("A", "B") == {"r1", "r2"}
