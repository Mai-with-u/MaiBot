from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pytest

from src.A_memorix.core.retrieval import (
    DualPathRetriever,
    DualPathRetrieverConfig,
    RetrievalResult,
    SparseBM25Config,
    VectorPoolsConfig,
)


class _FakeVectorStore:
    def __init__(self, ids: list[str], scores: list[float], dimension: int = 4) -> None:
        self.ids = ids
        self.scores = scores
        self.dimension = dimension

    def search(self, query: np.ndarray, k: int = 10, filter_deleted: bool = True):
        del query, filter_deleted
        return self.ids[:k], self.scores[:k]


class _FakeEmbeddingManager:
    async def encode(self, text: Any, **kwargs: Any) -> np.ndarray:
        del text, kwargs
        return np.ones(4, dtype=np.float32)


class _FakeMetadataStore:
    def __init__(self) -> None:
        self.paragraphs = {
            "p-direct": {"hash": "p-direct", "content": "Alice 喜欢红茶", "word_count": 4},
            "p-relation": {"hash": "p-relation", "content": "Alice 和 Bob 是同事", "word_count": 6},
            "p-entity": {"hash": "p-entity", "content": "Bob 常去图书馆", "word_count": 5},
        }
        self.relations = {
            "r-1": {
                "hash": "r-1",
                "subject": "Alice",
                "predicate": "同事",
                "object": "Bob",
                "confidence": 1.0,
            }
        }
        self.entities = {
            "e-1": {"hash": "e-1", "name": "Bob"},
        }
        self.relation_paragraphs = {"r-1": ["p-relation"]}
        self.entity_paragraphs = {"e-1": ["p-entity"]}

    def get_paragraphs_by_hashes(self, hashes):
        return {hash_value: self.paragraphs[hash_value] for hash_value in hashes if hash_value in self.paragraphs}

    def get_relations_by_hashes(self, hashes, include_inactive: bool = True):
        del include_inactive
        return {hash_value: self.relations[hash_value] for hash_value in hashes if hash_value in self.relations}

    def get_entities_by_hashes(self, hashes):
        return {hash_value: self.entities[hash_value] for hash_value in hashes if hash_value in self.entities}

    def get_paragraphs_by_relation_hashes(self, hashes):
        return {
            hash_value: [
                self.paragraphs[paragraph_hash]
                for paragraph_hash in self.relation_paragraphs.get(hash_value, [])
                if paragraph_hash in self.paragraphs
            ]
            for hash_value in hashes
        }

    def get_paragraphs_by_entity_hashes(self, hashes):
        return {
            hash_value: [
                self.paragraphs[paragraph_hash]
                for paragraph_hash in self.entity_paragraphs.get(hash_value, [])
                if paragraph_hash in self.paragraphs
            ]
            for hash_value in hashes
        }

    def get_paragraph_hashes_by_relation_hashes(self, hashes):
        return {
            hash_value: [
                paragraph_hash
                for paragraph_hash in self.relation_paragraphs.get(hash_value, [])
                if paragraph_hash in self.paragraphs
            ]
            for hash_value in hashes
        }

    def get_paragraph_entities_by_hashes(self, hashes):
        return {hash_value: [] for hash_value in hashes}


class _FakeGraphStore:
    def get_nodes(self):
        return []


class _FakeSparseIndex:
    def __init__(self) -> None:
        self.search_count = 0

    def search(self, query: str, k: int):
        del query, k
        self.search_count += 1
        return []


@pytest.mark.asyncio
async def test_dual_vector_pool_maps_graph_hits_to_paragraph_evidence() -> None:
    paragraph_store = _FakeVectorStore(["p-direct"], [0.9])
    graph_store = _FakeVectorStore(["relation:r-1", "entity:e-1"], [0.8, 0.7])
    metadata_store = _FakeMetadataStore()
    retriever = DualPathRetriever(
        vector_store=paragraph_store,
        paragraph_vector_store=paragraph_store,
        graph_vector_store=graph_store,
        graph_store=_FakeGraphStore(),
        metadata_store=metadata_store,
        embedding_manager=_FakeEmbeddingManager(),
        config=DualPathRetrieverConfig(
            enable_ppr=False,
            enable_parallel=False,
            sparse=SparseBM25Config(enabled=False),
            vector_pools=VectorPoolsConfig(mode="dual"),
        ),
    )

    results = await retriever.retrieve("Alice 和 Bob 的关系", top_k=5)
    by_hash = {item.hash_value: item for item in results}

    assert set(by_hash) >= {"p-direct", "p-relation", "p-entity"}
    relation_meta = by_hash["p-relation"].metadata
    entity_meta = by_hash["p-entity"].metadata
    assert relation_meta["evidence_items"][0]["type"] == "relation"
    assert relation_meta["evidence_items"][0]["hash"] == "r-1"
    assert relation_meta["evidence_items"][0]["grounding_factor"] == pytest.approx(1.0)
    assert entity_meta["evidence_items"][0]["type"] == "entity"
    assert entity_meta["evidence_items"][0]["hash"] == "e-1"
    assert entity_meta["evidence_items"][0]["grounding_factor"] == pytest.approx(1.0)
    assert relation_meta["score_breakdown"]["graph_evidence"] > 0
    assert by_hash["p-direct"].metadata["score_breakdown"]["semantic"] == pytest.approx(0.9)
    assert "time_meta" not in by_hash["p-direct"].metadata


@pytest.mark.asyncio
async def test_dual_graph_evidence_truncates_by_score_after_type_normalization() -> None:
    paragraph_store = _FakeVectorStore([], [])
    graph_store = _FakeVectorStore(
        ["entity:e-high-a", "entity:e-low", "entity:e-high-c"],
        [0.9, 0.3, 0.8],
    )
    metadata_store = _FakeMetadataStore()
    metadata_store.paragraphs.update(
        {
            "p-high-a": {"hash": "p-high-a", "content": "A 的高分证据", "word_count": 4},
            "p-low": {"hash": "p-low", "content": "B 的低分证据", "word_count": 4},
            "p-high-c": {"hash": "p-high-c", "content": "C 的高分证据", "word_count": 4},
        }
    )
    metadata_store.entities.update(
        {
            "e-high-a": {"hash": "e-high-a", "name": "A"},
            "e-low": {"hash": "e-low", "name": "B"},
            "e-high-c": {"hash": "e-high-c", "name": "C"},
        }
    )
    metadata_store.entity_paragraphs.update(
        {
            "e-high-a": ["p-high-a"],
            "e-low": ["p-low"],
            "e-high-c": ["p-high-c"],
        }
    )
    retriever = DualPathRetriever(
        vector_store=paragraph_store,
        paragraph_vector_store=paragraph_store,
        graph_vector_store=graph_store,
        graph_store=_FakeGraphStore(),
        metadata_store=metadata_store,
        embedding_manager=_FakeEmbeddingManager(),
        config=DualPathRetrieverConfig(
            enable_ppr=False,
            enable_parallel=False,
            sparse=SparseBM25Config(enabled=False),
            vector_pools=VectorPoolsConfig(
                mode="dual",
                graph_top_k=3,
                graph_expand_paragraph_k=2,
                entity_expand_per_hit=1,
            ),
        ),
    )

    results = await retriever.retrieve("测试图谱证据截断", top_k=5)
    by_hash = {item.hash_value: item for item in results}

    assert "p-high-a" in by_hash
    assert "p-high-c" in by_hash
    assert "p-low" not in by_hash
    high_a_evidence = by_hash["p-high-a"].metadata["evidence_items"][0]
    high_c_evidence = by_hash["p-high-c"].metadata["evidence_items"][0]
    assert high_a_evidence["normalized_score"] == pytest.approx(1.0)
    assert high_c_evidence["normalized_score"] == pytest.approx((0.8 - 0.3) / (0.9 - 0.3))
    assert (
        by_hash["p-high-a"].metadata["score_breakdown"]["graph_evidence"]
        >= (by_hash["p-high-c"].metadata["score_breakdown"]["graph_evidence"])
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("semantic_score", "expected_sparse_searches"), [(0.9, 0), (0.4, 1)])
async def test_dual_vector_pool_auto_sparse_uses_semantic_score(
    semantic_score: float,
    expected_sparse_searches: int,
) -> None:
    paragraph_store = _FakeVectorStore(["p-direct"], [semantic_score])
    graph_store = _FakeVectorStore([], [])
    sparse_index = _FakeSparseIndex()
    retriever = DualPathRetriever(
        vector_store=paragraph_store,
        paragraph_vector_store=paragraph_store,
        graph_vector_store=graph_store,
        graph_store=_FakeGraphStore(),
        metadata_store=_FakeMetadataStore(),
        embedding_manager=_FakeEmbeddingManager(),
        sparse_index=sparse_index,
        config=DualPathRetrieverConfig(
            enable_ppr=False,
            enable_parallel=False,
            sparse=SparseBM25Config(enabled=True, mode="auto"),
            vector_pools=VectorPoolsConfig(mode="dual"),
        ),
    )

    await retriever.retrieve("Alice 喜欢什么", top_k=5)

    assert sparse_index.search_count == expected_sparse_searches


@pytest.mark.parametrize(
    ("evidence", "content", "expected_factor"),
    [
        (
            {"type": "relation", "subject": "Alice", "predicate": "同事", "object": "Bob"},
            "Alice 和 Bob 是同事",
            1.0,
        ),
        (
            {"type": "relation", "subject": "Alice", "predicate": "同事", "object": "Bob"},
            "Alice 和 Bob 一起工作",
            0.78,
        ),
        (
            {"type": "relation", "subject": "Alice", "predicate": "同事", "object": "Bob"},
            "Alice 喜欢红茶",
            0.55,
        ),
        (
            {"type": "relation", "subject": "Alice", "predicate": "同事", "object": "Bob"},
            "这里提到同事关系",
            0.4,
        ),
        (
            {"type": "relation", "subject": "Alice", "predicate": "同事", "object": "Bob"},
            "没有任何对应信息",
            0.3,
        ),
        ({"type": "entity", "name": "Bob"}, "Bob 常去图书馆", 1.0),
        ({"type": "entity", "name": "Bob"}, "Alice 常去图书馆", 0.4),
    ],
)
def test_graph_evidence_grounding_factor(
    evidence: Dict[str, str],
    content: str,
    expected_factor: float,
) -> None:
    factor = DualPathRetriever._graph_evidence_grounding_factor(
        evidence,
        {"content": content},
    )

    assert factor == pytest.approx(expected_factor)


def _build_graph_reliability_retriever() -> DualPathRetriever:
    paragraph_store = _FakeVectorStore([], [])
    return DualPathRetriever(
        vector_store=paragraph_store,
        paragraph_vector_store=paragraph_store,
        graph_vector_store=paragraph_store,
        graph_store=_FakeGraphStore(),
        metadata_store=_FakeMetadataStore(),
        embedding_manager=_FakeEmbeddingManager(),
        config=DualPathRetrieverConfig(
            enable_ppr=False,
            enable_parallel=False,
            vector_pools=VectorPoolsConfig(mode="dual"),
        ),
    )


def _build_reliability_candidate(
    hash_value: str,
    *,
    semantic_score: float,
    graph_score: float,
    relation_hash: str = "",
    grounding_factor: float = 0.0,
) -> RetrievalResult:
    evidence_items = []
    if relation_hash:
        evidence_items.append(
            {
                "type": "relation",
                "hash": relation_hash,
                "normalized_score": 1.0,
                "grounding_factor": grounding_factor,
            }
        )
    return RetrievalResult(
        hash_value=hash_value,
        content=hash_value,
        score=0.0,
        result_type="paragraph",
        source="dual_vector_pool",
        metadata={
            "score_breakdown": {
                "semantic": semantic_score,
                "sparse": 0.0,
                "graph_evidence": graph_score,
            },
            "evidence_items": evidence_items,
        },
    )


def test_graph_reliability_keeps_high_weight_for_independently_supported_relations() -> None:
    retriever = _build_graph_reliability_retriever()
    candidates = [
        _build_reliability_candidate(
            "p-1",
            semantic_score=0.9,
            graph_score=0.9,
            relation_hash="r-1",
            grounding_factor=1.0,
        ),
        _build_reliability_candidate(
            "p-2",
            semantic_score=0.8,
            graph_score=0.8,
            relation_hash="r-2",
            grounding_factor=1.0,
        ),
    ]

    semantic_weight, sparse_weight, graph_weight, estimate = retriever._calibrate_dual_pool_weights(
        candidates,
        semantic_weight=0.45,
        sparse_weight=0.15,
        graph_weight=0.40,
        scan_limit=10,
    )

    assert estimate.score > 0.95
    assert estimate.grounded_relation_count == 2
    assert graph_weight > 0.38
    assert semantic_weight + sparse_weight + graph_weight == pytest.approx(1.0)


def test_graph_reliability_reduces_unsupported_graph_weight_to_floor() -> None:
    retriever = _build_graph_reliability_retriever()
    retriever.config.vector_pools.graph_weight = 0.40
    candidates = [
        _build_reliability_candidate("semantic-anchor", semantic_score=0.9, graph_score=0.0),
        _build_reliability_candidate(
            "p-1",
            semantic_score=0.0,
            graph_score=0.9,
            relation_hash="r-1",
            grounding_factor=0.3,
        ),
        _build_reliability_candidate(
            "p-2",
            semantic_score=0.0,
            graph_score=0.8,
            relation_hash="r-2",
            grounding_factor=0.3,
        ),
    ]

    semantic_weight, sparse_weight, graph_weight, estimate = retriever._calibrate_dual_pool_weights(
        candidates,
        semantic_weight=0.45,
        sparse_weight=0.15,
        graph_weight=0.40,
        scan_limit=10,
    )

    assert estimate.score == pytest.approx(0.0)
    assert estimate.grounded_relation_count == 0
    assert graph_weight == pytest.approx(0.15)
    assert semantic_weight == pytest.approx(0.6375)
    assert sparse_weight == pytest.approx(0.2125)


def test_graph_reliability_does_not_change_explicit_low_graph_weights() -> None:
    retriever = _build_graph_reliability_retriever()
    candidates = [
        _build_reliability_candidate(
            "p-1",
            semantic_score=0.0,
            graph_score=0.9,
            relation_hash="r-1",
            grounding_factor=0.3,
        )
    ]

    semantic_weight, sparse_weight, graph_weight, _estimate = retriever._calibrate_dual_pool_weights(
        candidates,
        semantic_weight=0.65,
        sparse_weight=0.20,
        graph_weight=0.15,
        scan_limit=10,
    )

    assert semantic_weight == pytest.approx(0.65)
    assert sparse_weight == pytest.approx(0.20)
    assert graph_weight == pytest.approx(0.15)
