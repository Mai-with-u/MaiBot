from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from src.A_memorix.core.retrieval import (
    DualPathRetriever,
    DualPathRetrieverConfig,
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
    assert entity_meta["evidence_items"][0]["type"] == "entity"
    assert entity_meta["evidence_items"][0]["hash"] == "e-1"
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
