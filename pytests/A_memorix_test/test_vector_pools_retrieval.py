"""双向量池检索功能测试。"""

from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from src.A_memorix.core.retrieval.dual_path import (
    DualPathRetriever,
    DualPathRetrieverConfig,
    RetrievalResult,
    VectorPoolsConfig,
)


# ── VectorPoolsConfig 配置测试 ──


class TestVectorPoolsConfig:
    def test_default_values(self):
        cfg = VectorPoolsConfig()
        assert cfg.mode == "dual"
        assert cfg.paragraph_top_k == 20
        assert cfg.graph_top_k == 40
        assert cfg.graph_expand_paragraph_k == 80
        assert cfg.relation_expand_per_hit == 5
        assert cfg.entity_expand_per_hit == 8
        assert cfg.relation_evidence_weight == 1.0
        assert cfg.entity_evidence_weight == 0.55
        assert cfg.semantic_weight == 0.65
        assert cfg.sparse_weight == 0.20
        assert cfg.graph_weight == 0.15
        assert cfg.relation_intent_graph_top_k == 80
        assert cfg.relation_intent_semantic_weight == 0.45
        assert cfg.relation_intent_sparse_weight == 0.15
        assert cfg.relation_intent_graph_weight == 0.40
        assert cfg.return_relation_items is False

    def test_mode_normalizes_to_single_or_dual(self):
        assert VectorPoolsConfig(mode="dual").mode == "dual"
        assert VectorPoolsConfig(mode="DUAL").mode == "dual"
        assert VectorPoolsConfig(mode="single").mode == "single"
        assert VectorPoolsConfig(mode="  dual  ").mode == "dual"

    def test_invalid_mode_clamped_to_single(self):
        assert VectorPoolsConfig(mode="invalid").mode == "single"
        assert VectorPoolsConfig(mode="").mode == "single"
        assert VectorPoolsConfig(mode="hybrid").mode == "single"

    def test_negative_values_clamped_to_zero(self):
        cfg = VectorPoolsConfig(
            semantic_weight=-1.0,
            sparse_weight=-0.5,
            graph_weight=-100,
            relation_evidence_weight=-1,
            entity_evidence_weight=-3.14,
        )
        assert cfg.semantic_weight == 0.0
        assert cfg.sparse_weight == 0.0
        assert cfg.graph_weight == 0.0
        assert cfg.relation_evidence_weight == 0.0
        assert cfg.entity_evidence_weight == 0.0

    def test_top_k_values_clamped_to_min_1(self):
        cfg = VectorPoolsConfig(
            paragraph_top_k=0,
            graph_top_k=-5,
            graph_expand_paragraph_k=0,
            relation_expand_per_hit=-1,
            entity_expand_per_hit=0,
        )
        assert cfg.paragraph_top_k == 1
        assert cfg.graph_top_k == 1
        assert cfg.graph_expand_paragraph_k == 1
        assert cfg.relation_expand_per_hit == 1
        assert cfg.entity_expand_per_hit == 1

    def test_relation_intent_overrides_with_nested_dict(self):
        cfg = VectorPoolsConfig(
            relation_intent={
                "graph_top_k": 200,
                "semantic_weight": 0.3,
                "sparse_weight": 0.1,
                "graph_weight": 0.6,
                "return_relation_items": True,
            }
        )
        assert cfg.relation_intent_graph_top_k == 200
        assert cfg.relation_intent_semantic_weight == 0.3
        assert cfg.relation_intent_sparse_weight == 0.1
        assert cfg.relation_intent_graph_weight == 0.6
        assert cfg.return_relation_items is True

    def test_relation_intent_negative_clamped(self):
        cfg = VectorPoolsConfig(
            relation_intent={
                "semantic_weight": -1.0,
                "sparse_weight": -9,
                "graph_weight": -0.1,
            }
        )
        assert cfg.relation_intent_semantic_weight == 0.0
        assert cfg.relation_intent_sparse_weight == 0.0
        assert cfg.relation_intent_graph_weight == 0.0

    def test_relation_intent_empty_dict_keeps_defaults(self):
        cfg = VectorPoolsConfig(relation_intent={})
        assert cfg.relation_intent_graph_top_k == 80
        assert cfg.relation_intent_semantic_weight == 0.45
        assert cfg.return_relation_items is False

    def test_relation_intent_not_dict_keeps_defaults(self):
        cfg = VectorPoolsConfig(relation_intent=None)  # type: ignore[arg-type]
        assert cfg.relation_intent_graph_top_k == 80


# ── DualPathRetrieverConfig 向量池集成测试 ──


class TestDualPathRetrieverConfigVectorPools:
    def test_default_config_includes_vector_pools(self):
        cfg = DualPathRetrieverConfig()
        assert isinstance(cfg.vector_pools, VectorPoolsConfig)
        assert cfg.vector_pools.mode == "dual"

    def test_dict_vector_pools_deserializes(self):
        cfg = DualPathRetrieverConfig(
            vector_pools={"mode": "dual", "paragraph_top_k": 30}  # type: ignore[arg-type]
        )
        assert isinstance(cfg.vector_pools, VectorPoolsConfig)
        assert cfg.vector_pools.mode == "dual"
        assert cfg.vector_pools.paragraph_top_k == 30


# ── _graph_vector_id / _parse_graph_vector_id ──


class TestGraphVectorIdRoundtrip:
    def test_relation_roundtrip(self):
        vid = DualPathRetriever._graph_vector_id("relation", "abc123")
        assert vid == "relation:abc123"
        item_type, hash_value = DualPathRetriever._parse_graph_vector_id(vid)
        assert item_type == "relation"
        assert hash_value == "abc123"

    def test_entity_roundtrip(self):
        vid = DualPathRetriever._graph_vector_id("entity", "ent-001")
        assert vid == "entity:ent-001"
        item_type, hash_value = DualPathRetriever._parse_graph_vector_id(vid)
        assert item_type == "entity"
        assert hash_value == "ent-001"

    def test_empty_type_handled(self):
        vid = DualPathRetriever._graph_vector_id("", "hash-1")
        assert vid == ":hash-1"
        item_type, hash_value = DualPathRetriever._parse_graph_vector_id(vid)
        assert item_type == ""
        assert hash_value == "hash-1"

    def test_hash_with_colon_parses_correctly(self):
        vid = DualPathRetriever._graph_vector_id("relation", "hash:with:colons")
        item_type, hash_value = DualPathRetriever._parse_graph_vector_id(vid)
        assert item_type == "relation"
        assert hash_value == "hash:with:colons"

    def test_no_colon_returns_empty_type(self):
        item_type, hash_value = DualPathRetriever._parse_graph_vector_id("plainhash")
        assert item_type == ""
        assert hash_value == "plainhash"

    def test_empty_string_parse(self):
        item_type, hash_value = DualPathRetriever._parse_graph_vector_id("")
        assert item_type == ""
        assert hash_value == ""

    def test_whitespace_in_id(self):
        vid = DualPathRetriever._graph_vector_id("  relation  ", "  hash  ")
        item_type, hash_value = DualPathRetriever._parse_graph_vector_id(vid)
        assert item_type == "relation"
        assert hash_value == "hash"


# ── _dual_pool_weights 权重选择测试 ──


def _make_retriever(vector_pools_cfg: Optional[VectorPoolsConfig] = None) -> DualPathRetriever:
    """构造一个配置了 vector_pools 的最小 DualPathRetriever。"""
    config = DualPathRetrieverConfig()
    if vector_pools_cfg is not None:
        config.vector_pools = vector_pools_cfg
    return DualPathRetriever(
        vector_store=MagicMock(),
        graph_store=MagicMock(),
        metadata_store=MagicMock(),
        embedding_manager=MagicMock(),
        config=config,
    )


class TestDualPoolWeights:
    def test_default_weights_without_relation_intent(self):
        retriever = _make_retriever()
        semantic, sparse, graph, gtopk = retriever._dual_pool_weights({})
        assert semantic == 0.65
        assert sparse == 0.20
        assert graph == 0.15
        assert gtopk == 40

    def test_relation_intent_weights(self):
        retriever = _make_retriever()
        semantic, sparse, graph, gtopk = retriever._dual_pool_weights({"enabled": True})
        assert semantic == 0.45
        assert sparse == 0.15
        assert graph == 0.40
        assert gtopk == 80

    def test_custom_config_weights(self):
        cfg = VectorPoolsConfig(
            semantic_weight=0.5,
            sparse_weight=0.3,
            graph_weight=0.2,
            graph_top_k=60,
        )
        retriever = _make_retriever(cfg)
        semantic, sparse, graph, gtopk = retriever._dual_pool_weights({})
        assert semantic == 0.5
        assert sparse == 0.3
        assert graph == 0.2
        assert gtopk == 60

    def test_custom_relation_intent_weights_override(self):
        cfg = VectorPoolsConfig(
            relation_intent_semantic_weight=0.6,
            relation_intent_sparse_weight=0.1,
            relation_intent_graph_weight=0.3,
            relation_intent_graph_top_k=100,
        )
        retriever = _make_retriever(cfg)
        semantic, sparse, graph, gtopk = retriever._dual_pool_weights({"enabled": True})
        assert semantic == 0.6
        assert sparse == 0.1
        assert graph == 0.3
        assert gtopk == 100


# ── _collect_dual_graph_evidence 图谱证据收集测试 ──


def _make_fake_meta():
    """构造一个 fake metadata_store，支持 get_entities_by_hashes / get_paragraphs_by_hashes 等。"""
    meta = MagicMock()
    meta.get_entities_by_hashes = MagicMock(return_value={})
    meta.get_paragraphs_by_hashes = MagicMock(return_value={})
    meta.get_relations_by_hashes = MagicMock(return_value={})
    meta.get_paragraphs_by_relation_hashes = MagicMock(return_value={})
    getter = MagicMock(return_value={})
    meta.get_paragraphs_by_entity_hashes = getter
    meta.get_entity_paragraphs = MagicMock(return_value={})
    meta.get_relation_paragraphs = MagicMock(return_value={})
    return meta


def _make_fake_graph_vector_store(
    ids_scores: Optional[List[Tuple[str, float]]] = None,
):
    """构造一个 fake graph_vector_store，search 返回指定的 (id, score) 列表。"""
    store = MagicMock()
    if ids_scores:
        ids = [item[0] for item in ids_scores]
        scores = [item[1] for item in ids_scores]
    else:
        ids, scores = [], []
    store.search = MagicMock(return_value=(ids, scores))
    return store


class TestCollectDualGraphEvidence:
    @pytest.mark.asyncio
    async def test_empty_graph_store_returns_empty(self):
        cfg = VectorPoolsConfig(mode="dual")
        retriever = _make_retriever(cfg)
        retriever.graph_vector_store = _make_fake_graph_vector_store([])
        retriever.metadata_store = _make_fake_meta()

        result = await retriever._collect_dual_graph_evidence(
            query_emb=None,
            top_k=40,
            temporal=None,
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_entity_vector_maps_to_entity_paragraphs(self):
        """图谱向量池中 entity:xxx ID 应展开为关联段落并以加权证据分计入。"""
        cfg = VectorPoolsConfig(entity_expand_per_hit=2)
        retriever = _make_retriever(cfg)
        # 图谱池返回 entity:e1 / entity:e2，解析后 hash 为 e1 / e2
        retriever.graph_vector_store = _make_fake_graph_vector_store([("entity:e1", 0.9), ("entity:e2", 0.5)])
        meta = _make_fake_meta()
        meta.get_relations_by_hashes = MagicMock(return_value={})
        meta.get_entities_by_hashes.return_value = {
            "e1": {"name": "小明", "hash": "e1"},
            "e2": {"name": "小红", "hash": "e2"},
        }
        meta.get_relation_paragraphs.return_value = {}
        meta.get_entity_paragraphs_getter = MagicMock(
            return_value={
                "e1": [{"hash": "p1", "content": "小明喜欢咖啡", "word_count": 4}],
                "e2": [{"hash": "p2", "content": "小红喜欢茶", "word_count": 4}],
            }
        )
        meta.get_paragraphs_by_entity_hashes = meta.get_entity_paragraphs_getter
        meta.get_entity_paragraphs.return_value = {}
        retriever.metadata_store = meta

        result = await retriever._collect_dual_graph_evidence(
            query_emb=None,
            top_k=40,
            temporal=None,
        )
        assert "p1" in result
        assert "p2" in result
        p1_evidence = result["p1"]["evidence"]
        assert len(p1_evidence) == 1
        assert p1_evidence[0][0]["type"] == "entity"
        assert p1_evidence[0][0]["name"] == "小明"
        assert p1_evidence[0][1] == pytest.approx(1.0 * 0.55)  # normalized_score * entity_evidence_weight

    @pytest.mark.asyncio
    async def test_relation_vector_maps_to_relation_paragraphs(self):
        """图谱向量池中 relation:xxx ID 应展开并加权。"""
        cfg = VectorPoolsConfig(relation_expand_per_hit=3)
        retriever = _make_retriever(cfg)
        retriever.graph_vector_store = _make_fake_graph_vector_store([("relation:r1", 0.8)])
        meta = _make_fake_meta()
        meta.get_relations_by_hashes = MagicMock(
            return_value={
                "r1": {
                    "hash": "r1",
                    "subject": "小明",
                    "predicate": "喜欢",
                    "object": "咖啡",
                }
            }
        )
        meta.get_entities_by_hashes.return_value = {}
        meta.get_paragraphs_by_relation_hashes.return_value = {
            "r1": [{"hash": "p3", "content": "小明最近喜欢喝咖啡", "word_count": 6}],
        }
        retriever.metadata_store = meta

        result = await retriever._collect_dual_graph_evidence(
            query_emb=None,
            top_k=40,
            temporal=None,
        )
        assert "p3" in result
        p3_evidence = result["p3"]["evidence"]
        assert len(p3_evidence) == 1
        assert p3_evidence[0][0]["type"] == "relation"
        assert p3_evidence[0][0]["subject"] == "小明"

    @pytest.mark.asyncio
    async def test_unknown_ids_skipped(self):
        retriever = _make_retriever()
        retriever.graph_vector_store = _make_fake_graph_vector_store(
            [("entity:ghost", 0.99), ("relation:phantom", 0.5)]
        )
        meta = _make_fake_meta()
        meta.get_entities_by_hashes.return_value = {}
        meta.get_relations_by_hashes = MagicMock(return_value={})
        retriever.metadata_store = meta

        result = await retriever._collect_dual_graph_evidence(
            query_emb=None,
            top_k=40,
            temporal=None,
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_respects_expand_limits(self):
        cfg = VectorPoolsConfig(
            relation_expand_per_hit=1,
            entity_expand_per_hit=1,
            graph_expand_paragraph_k=2,
        )
        retriever = _make_retriever(cfg)
        retriever.graph_vector_store = _make_fake_graph_vector_store(
            [("entity:e1", 1.0), ("entity:e2", 0.9), ("entity:e3", 0.8)]
        )
        meta = _make_fake_meta()
        meta.get_relations_by_hashes = MagicMock(return_value={})
        meta.get_entities_by_hashes.return_value = {
            "e1": {"name": "A", "hash": "e1"},
            "e2": {"name": "B", "hash": "e2"},
            "e3": {"name": "C", "hash": "e3"},
        }
        meta.get_relation_paragraphs.return_value = {}
        meta.get_entity_paragraphs_getter = MagicMock(
            return_value={
                "e1": [{"hash": f"p{i}", "content": f"c{i}", "word_count": 1} for i in range(5)],
                "e2": [{"hash": f"q{i}", "content": f"c{i}", "word_count": 1} for i in range(5)],
                "e3": [{"hash": f"r{i}", "content": f"c{i}", "word_count": 1} for i in range(5)],
            }
        )
        meta.get_paragraphs_by_entity_hashes = meta.get_entity_paragraphs_getter
        meta.get_entity_paragraphs.return_value = {}
        retriever.metadata_store = meta

        result = await retriever._collect_dual_graph_evidence(
            query_emb=None,
            top_k=40,
            temporal=None,
        )
        assert len(result) <= 2


# ── _ensure_paragraph_candidate 候选构建测试 ──


class TestEnsureParagraphCandidate:
    def test_creates_new_candidate_when_not_exists(self):
        retriever = _make_retriever()
        candidates: Dict[str, RetrievalResult] = {}
        paragraph = {"hash": "p-new", "content": "新段落", "word_count": 3}
        result = retriever._ensure_paragraph_candidate(candidates, paragraph)
        assert "p-new" in candidates
        assert result.hash_value == "p-new"
        assert result.result_type == "paragraph"
        assert result.source == "dual_vector_pool"

    def test_returns_existing_candidate(self):
        retriever = _make_retriever()
        existing = RetrievalResult(
            hash_value="p-exist",
            content="已有",
            score=0.5,
            result_type="paragraph",
            source="other",
            metadata={},
        )
        candidates: Dict[str, RetrievalResult] = {"p-exist": existing}
        paragraph = {"hash": "p-exist", "content": "已有", "word_count": 3}
        result = retriever._ensure_paragraph_candidate(candidates, paragraph)
        assert result is existing
        assert result.score == 0.5  # 不覆盖已有分数


# ── _add_candidate_score 分数累加测试 ──


class TestAddCandidateScore:
    def test_adds_score_to_breakdown(self):
        retriever = _make_retriever()
        candidates: Dict[str, RetrievalResult] = {}
        paragraph = {"hash": "p-score", "content": "测试", "word_count": 2}
        retriever._add_candidate_score(
            candidates,
            paragraph,
            score_key="semantic",
            score=0.85,
            source="paragraph_vector_pool",
            temporal=None,
        )
        candidate = candidates["p-score"]
        score_meta = retriever._candidate_score_meta(candidate)
        assert score_meta["semantic"] == pytest.approx(0.85)

    def test_max_semantic_score_kept(self):
        retriever = _make_retriever()
        candidates: Dict[str, RetrievalResult] = {}
        paragraph = {"hash": "p-max", "content": "测试", "word_count": 1}
        retriever._add_candidate_score(
            candidates,
            paragraph,
            score_key="semantic",
            score=0.3,
            source="src",
            temporal=None,
        )
        retriever._add_candidate_score(
            candidates,
            paragraph,
            score_key="semantic",
            score=0.9,
            source="src",
            temporal=None,
        )
        retriever._add_candidate_score(
            candidates,
            paragraph,
            score_key="semantic",
            score=0.6,
            source="src",
            temporal=None,
        )
        candidate = candidates["p-max"]
        score_meta = retriever._candidate_score_meta(candidate)
        assert score_meta["semantic"] == pytest.approx(0.9)

    def test_sparse_score_overwrites(self):
        retriever = _make_retriever()
        candidates: Dict[str, RetrievalResult] = {}
        paragraph = {"hash": "p-sparse", "content": "测试", "word_count": 1}
        retriever._add_candidate_score(
            candidates,
            paragraph,
            score_key="sparse",
            score=0.4,
            source="src1",
            temporal=None,
        )
        retriever._add_candidate_score(
            candidates,
            paragraph,
            score_key="sparse",
            score=0.7,
            source="src2",
            temporal=None,
        )
        candidate = candidates["p-sparse"]
        score_meta = retriever._candidate_score_meta(candidate)
        assert score_meta["sparse"] == pytest.approx(0.7)


# ── _candidate_evidence_items 证据项测试 ──


class TestCandidateEvidenceItems:
    def test_empty_for_new_candidate(self):
        retriever = _make_retriever()
        candidates: Dict[str, RetrievalResult] = {}
        paragraph = {"hash": "p-ev", "content": "测试", "word_count": 1}
        candidate = retriever._ensure_paragraph_candidate(candidates, paragraph)
        items = retriever._candidate_evidence_items(candidate)
        assert items == []

    def test_appends_evidence_via_helper(self):
        retriever = _make_retriever()
        candidates: Dict[str, RetrievalResult] = {}
        paragraph = {"hash": "p-append", "content": "测试", "word_count": 1}
        candidate = retriever._ensure_paragraph_candidate(candidates, paragraph)
        evidence = {"type": "entity", "name": "小明", "hash": "e1"}
        retriever._append_graph_evidence(candidate, evidence=evidence, score=0.88)
        items = retriever._candidate_evidence_items(candidate)
        assert len(items) == 1
        assert items[0]["type"] == "entity"
        assert items[0]["score"] == pytest.approx(0.88)
