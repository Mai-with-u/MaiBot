"""
双路检索器

同时检索关系和段落，实现知识图谱增强的检索。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import asyncio
import re
import time

import numpy as np

from src.common.logger import get_logger
from ..storage import VectorStore, GraphStore, MetadataStore
from ..embedding import EmbeddingAPIAdapter
from ..utils.matcher import AhoCorasick
from ..utils.metadata import coerce_metadata_dict
from ..utils.time_parser import format_timestamp
from .graph_relation_recall import GraphRelationRecallConfig, GraphRelationRecallService
from .pagerank import PersonalizedPageRank, PageRankConfig
from .posterior_graph import PosteriorGraphConfig, apply_posterior_graph_gate
from .sparse_bm25 import SparseBM25Config, SparseBM25Index

logger = get_logger("A_Memorix.DualPathRetriever")


class RetrievalStrategy(Enum):
    """检索策略"""

    PARA_ONLY = "paragraph_only"  # 仅段落检索
    REL_ONLY = "relation_only"   # 仅关系检索
    DUAL_PATH = "dual_path"      # 双路检索（推荐）


@dataclass
class RetrievalResult:
    """
    检索结果

    属性：
        hash_value: 哈希值
        content: 内容（段落或关系）
        score: 相似度分数
        result_type: 结果类型（paragraph/relation）
        source: 来源（paragraph_search/relation_search/fusion）
        metadata: 额外元数据
    """

    hash_value: str
    content: str
    score: float
    result_type: str  # 结果类型："paragraph" 或 "relation"
    source: str  # 来源阶段："paragraph_search"、"relation_search" 或 "fusion"
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "hash": self.hash_value,
            "content": self.content,
            "score": self.score,
            "type": self.result_type,
            "source": self.source,
            "metadata": self.metadata,
        }


@dataclass
class DualPathRetrieverConfig:
    """
    双路检索器配置

    属性：
        top_k_paragraphs: 段落检索数量
        top_k_relations: 关系检索数量
        top_k_final: 最终返回数量
        alpha: 段落和关系的融合权重（0-1）
            - 0: 仅使用关系分数
            - 1: 仅使用段落分数
            - 0.5: 平均融合
        enable_ppr: 是否启用PageRank重排序
        ppr_alpha: PageRank的alpha参数
        ppr_concurrency_limit: PPR计算的最大并发数
        enable_parallel: 是否并行检索
        retrieval_strategy: 检索策略
        debug: 是否启用调试模式（打印搜索结果原文）
    """
 
    top_k_paragraphs: int = 20
    top_k_relations: int = 10
    top_k_final: int = 10
    alpha: float = 0.5  # 融合权重
    enable_ppr: bool = True
    ppr_alpha: float = 0.85
    ppr_timeout_seconds: float = 1.5
    ppr_concurrency_limit: int = 4
    ppr_local_enabled: bool = True
    ppr_local_max_nodes: int = 256
    ppr_local_hops: int = 2
    ppr_local_min_graph_nodes: int = 128
    enable_parallel: bool = True
    retrieval_strategy: RetrievalStrategy = RetrievalStrategy.DUAL_PATH
    debug: bool = False
    sparse: SparseBM25Config = field(default_factory=SparseBM25Config)
    fusion: "FusionConfig" = field(default_factory=lambda: FusionConfig())
    relation_intent: "RelationIntentConfig" = field(default_factory=lambda: RelationIntentConfig())
    vector_pools: "VectorPoolsConfig" = field(default_factory=lambda: VectorPoolsConfig())
    graph_recall: GraphRelationRecallConfig = field(default_factory=GraphRelationRecallConfig)
    posterior_graph: PosteriorGraphConfig = field(default_factory=PosteriorGraphConfig)

    def __post_init__(self):
        """验证配置"""
        if isinstance(self.sparse, dict):
            self.sparse = SparseBM25Config(**self.sparse)
        if isinstance(self.fusion, dict):
            self.fusion = FusionConfig(**self.fusion)
        if isinstance(self.relation_intent, dict):
            self.relation_intent = RelationIntentConfig(**self.relation_intent)
        if isinstance(self.vector_pools, dict):
            self.vector_pools = VectorPoolsConfig(**self.vector_pools)
        if isinstance(self.graph_recall, dict):
            self.graph_recall = GraphRelationRecallConfig(**self.graph_recall)
        if isinstance(self.posterior_graph, dict):
            self.posterior_graph = PosteriorGraphConfig(**self.posterior_graph)

        if not 0 <= self.alpha <= 1:
            raise ValueError(f"alpha必须在[0, 1]之间: {self.alpha}")

        if self.top_k_paragraphs <= 0:
            raise ValueError(f"top_k_paragraphs必须大于0: {self.top_k_paragraphs}")

        if self.top_k_relations <= 0:
            raise ValueError(f"top_k_relations必须大于0: {self.top_k_relations}")

        if self.top_k_final <= 0:
            raise ValueError(f"top_k_final必须大于0: {self.top_k_final}")
        if self.ppr_timeout_seconds <= 0:
            raise ValueError(f"ppr_timeout_seconds必须大于0: {self.ppr_timeout_seconds}")
        self.ppr_local_enabled = bool(self.ppr_local_enabled)
        self.ppr_local_max_nodes = max(16, int(self.ppr_local_max_nodes))
        self.ppr_local_hops = max(1, int(self.ppr_local_hops))
        self.ppr_local_min_graph_nodes = max(0, int(self.ppr_local_min_graph_nodes))


@dataclass
class TemporalQueryOptions:
    """时序查询选项。"""

    time_from: Optional[float] = None
    time_to: Optional[float] = None
    person: Optional[str] = None
    source: Optional[str] = None
    allow_created_fallback: bool = True
    candidate_multiplier: int = 8
    max_scan: int = 1000


@dataclass
class RelationIntentConfig:
    """关系意图增强配置。"""

    enabled: bool = True
    alpha_override: float = 0.35
    relation_candidate_multiplier: int = 4
    preserve_top_relations: int = 3
    force_relation_sparse: bool = True
    pair_predicate_rerank_enabled: bool = True
    pair_predicate_limit: int = 3

    def __post_init__(self):
        self.alpha_override = min(1.0, max(0.0, float(self.alpha_override)))
        self.relation_candidate_multiplier = max(1, int(self.relation_candidate_multiplier))
        self.preserve_top_relations = max(0, int(self.preserve_top_relations))
        self.force_relation_sparse = bool(self.force_relation_sparse)
        self.pair_predicate_rerank_enabled = bool(self.pair_predicate_rerank_enabled)
        self.pair_predicate_limit = max(1, int(self.pair_predicate_limit))


@dataclass
class VectorPoolsConfig:
    """双向量池检索配置。"""

    mode: str = "dual"
    paragraph_top_k: int = 20
    graph_top_k: int = 40
    graph_expand_paragraph_k: int = 80
    relation_expand_per_hit: int = 5
    entity_expand_per_hit: int = 8
    relation_evidence_weight: float = 1.0
    entity_evidence_weight: float = 0.55
    semantic_weight: float = 0.65
    sparse_weight: float = 0.20
    graph_weight: float = 0.15
    relation_intent_graph_top_k: int = 80
    relation_intent_semantic_weight: float = 0.45
    relation_intent_sparse_weight: float = 0.15
    relation_intent_graph_weight: float = 0.40
    return_relation_items: bool = False
    relation_intent: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.mode = str(self.mode or "single").strip().lower()
        if self.mode not in {"single", "dual"}:
            self.mode = "single"
        self.paragraph_top_k = max(1, int(self.paragraph_top_k))
        self.graph_top_k = max(1, int(self.graph_top_k))
        self.graph_expand_paragraph_k = max(1, int(self.graph_expand_paragraph_k))
        self.relation_expand_per_hit = max(1, int(self.relation_expand_per_hit))
        self.entity_expand_per_hit = max(1, int(self.entity_expand_per_hit))
        self.relation_evidence_weight = max(0.0, float(self.relation_evidence_weight))
        self.entity_evidence_weight = max(0.0, float(self.entity_evidence_weight))
        self.semantic_weight = max(0.0, float(self.semantic_weight))
        self.sparse_weight = max(0.0, float(self.sparse_weight))
        self.graph_weight = max(0.0, float(self.graph_weight))

        relation_intent = self.relation_intent if isinstance(self.relation_intent, dict) else {}
        self.relation_intent_graph_top_k = max(
            1,
            int(relation_intent.get("graph_top_k", self.relation_intent_graph_top_k)),
        )
        self.relation_intent_semantic_weight = max(
            0.0,
            float(relation_intent.get("semantic_weight", self.relation_intent_semantic_weight)),
        )
        self.relation_intent_sparse_weight = max(
            0.0,
            float(relation_intent.get("sparse_weight", self.relation_intent_sparse_weight)),
        )
        self.relation_intent_graph_weight = max(
            0.0,
            float(relation_intent.get("graph_weight", self.relation_intent_graph_weight)),
        )
        self.return_relation_items = bool(
            relation_intent.get("return_relation_items", self.return_relation_items)
        )


@dataclass
class FusionConfig:
    """融合配置。"""

    method: str = "weighted_rrf"  # 融合方法：weighted_rrf 或 alpha_legacy
    rrf_k: int = 60
    vector_weight: float = 0.7
    bm25_weight: float = 0.3
    normalize_score: bool = True
    normalize_method: str = "minmax"

    def __post_init__(self):
        self.method = str(self.method or "weighted_rrf").strip().lower()
        self.normalize_method = str(self.normalize_method or "minmax").strip().lower()
        self.rrf_k = max(1, int(self.rrf_k))
        self.vector_weight = max(0.0, float(self.vector_weight))
        self.bm25_weight = max(0.0, float(self.bm25_weight))
        s = self.vector_weight + self.bm25_weight
        if s <= 0:
            self.vector_weight = 0.7
            self.bm25_weight = 0.3
        elif abs(s - 1.0) > 1e-8:
            self.vector_weight /= s
            self.bm25_weight /= s


class DualPathRetriever:
    """
    双路检索器

    功能：
    - 并行检索段落和关系
    - 结果融合与排序
    - PageRank重排序
    - 实体识别与加权

    参数：
        vector_store: 向量存储
        graph_store: 图存储
        metadata_store: 元数据存储
        embedding_manager: 嵌入管理器
        config: 检索配置
    """

    def __init__(
        self,
        vector_store: VectorStore,
        graph_store: GraphStore,
        metadata_store: MetadataStore,
        embedding_manager: EmbeddingAPIAdapter,
        sparse_index: Optional[SparseBM25Index] = None,
        config: Optional[DualPathRetrieverConfig] = None,
        paragraph_vector_store: Optional[VectorStore] = None,
        graph_vector_store: Optional[VectorStore] = None,
    ):
        """
        初始化双路检索器

        Args:
            vector_store: 向量存储
            graph_store: 图存储
            metadata_store: 元数据存储
            embedding_manager: 嵌入管理器
            config: 检索配置
        """
        self.vector_store = vector_store
        self.paragraph_vector_store = paragraph_vector_store or vector_store
        self.graph_vector_store = graph_vector_store or vector_store
        self.graph_store = graph_store
        self.metadata_store = metadata_store
        self.embedding_manager = embedding_manager
        self.config = config or DualPathRetrieverConfig()
        self.sparse_index = sparse_index

        # PageRank计算器
        ppr_config = PageRankConfig(alpha=self.config.ppr_alpha)
        self._ppr = PersonalizedPageRank(
            graph_store=graph_store,
            config=ppr_config,
        )
        self._ppr_semaphore = asyncio.Semaphore(self.config.ppr_concurrency_limit)
        self._graph_relation_recall = GraphRelationRecallService(
            graph_store=graph_store,
            metadata_store=metadata_store,
            config=self.config.graph_recall,
        )

        logger.debug(
            f"DualPathRetriever 初始化: "
            f"strategy={self.config.retrieval_strategy.value}, "
            f"top_k_para={self.config.top_k_paragraphs}, "
            f"top_k_rel={self.config.top_k_relations}"
        )

        # 缓存 Aho-Corasick 匹配器
        self._ac_matcher: Optional[AhoCorasick] = None
        self._ac_nodes_count = 0
        self._ac_node_map: Dict[str, str] = {}
        self._relation_intent_pattern = re.compile(
            r"(什么关系|有哪些关系|和.+关系|关联|关系网|subject|predicate|object|"
            r"relation|related|between.+and)",
            re.IGNORECASE,
        )
        self._runtime_sparse_only = False
        self._ppr_cache: Dict[Tuple[Any, ...], Tuple[float, Dict[str, float]]] = {}
        self._ppr_cache_ttl_seconds = 300.0
        self._ppr_cache_max_entries = 256

    def set_runtime_sparse_only(self, enabled: bool) -> None:
        """由运行时控制强制 sparse-only（不改用户配置文件）。"""
        self._runtime_sparse_only = bool(enabled)

    def _is_sparse_only_runtime(self) -> bool:
        mode = str(getattr(self.config.sparse, "mode", "auto") or "auto").strip().lower()
        return bool(self._runtime_sparse_only or mode == "fallback_only")

    async def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        strategy: Optional[RetrievalStrategy] = None,
        temporal: Optional[TemporalQueryOptions] = None,
    ) -> List[RetrievalResult]:
        """
        执行检索（异步方法）

        Args:
            query: 查询文本
            top_k: 返回结果数量（默认使用配置值）
            strategy: 检索策略（默认使用配置值）
            temporal: 时序查询选项（可选）

        Returns:
            检索结果列表
        """
        top_k = top_k or self.config.top_k_final
        strategy = strategy or self.config.retrieval_strategy
        relation_intent_ctx = self._build_relation_intent_context(query=query, top_k=top_k)

        logger.info(
            "执行检索: "
            f"query='{query[:50]}...', "
            f"strategy={strategy.value}, "
            f"relation_intent={relation_intent_ctx.get('enabled', False)}"
        )

        if temporal and not (query or "").strip():
            return self._retrieve_temporal_only(temporal, top_k)

        # 根据策略执行检索
        if strategy == RetrievalStrategy.PARA_ONLY:
            results = await self._retrieve_paragraphs_only(query, top_k, temporal=temporal)
        elif strategy == RetrievalStrategy.REL_ONLY:
            results = await self._retrieve_relations_only(query, top_k, temporal=temporal)
        else:  # 双路检索（DUAL_PATH）
            results = await self._retrieve_dual_path(
                query,
                top_k,
                temporal=temporal,
                relation_intent=relation_intent_ctx,
            )

        logger.info(f"检索完成: 返回 {len(results)} 条结果")

        # 调试模式：打印结果原文
        if self.config.debug:
            logger.info("[DEBUG] 检索结果内容原文:")
            for i, res in enumerate(results):
                logger.info(f"  {i+1}. [{res.result_type}] (Score: {res.score:.4f}) {res.content}")

        return results

    def _is_relation_intent_query(self, query: str) -> bool:
        q = str(query or "").strip()
        if not q:
            return False
        if "|" in q or "->" in q:
            return True
        return self._relation_intent_pattern.search(q) is not None

    def _build_relation_intent_context(self, query: str, top_k: int) -> Dict[str, Any]:
        cfg = self.config.relation_intent
        enabled = bool(cfg.enabled) and self._is_relation_intent_query(query)
        base_relation_k = max(1, int(self.config.top_k_relations))
        relation_top_k = max(base_relation_k, int(top_k))
        if enabled:
            relation_top_k = max(
                relation_top_k,
                relation_top_k * int(cfg.relation_candidate_multiplier),
            )
        return {
            "enabled": enabled,
            "alpha_override": float(cfg.alpha_override) if enabled else None,
            "relation_top_k": int(relation_top_k),
            "preserve_top_relations": int(cfg.preserve_top_relations) if enabled else 0,
            "force_relation_sparse": bool(cfg.force_relation_sparse) if enabled else False,
            "pair_predicate_rerank_enabled": bool(cfg.pair_predicate_rerank_enabled) if enabled else False,
            "pair_predicate_limit": int(cfg.pair_predicate_limit) if enabled else 0,
        }

    def _cap_temporal_scan_k(
        self,
        candidate_k: int,
        temporal: Optional[TemporalQueryOptions],
    ) -> int:
        """对 temporal 模式候选召回数应用 max_scan 上限。"""
        k = max(1, int(candidate_k))
        if temporal and temporal.max_scan and temporal.max_scan > 0:
            k = min(k, int(temporal.max_scan))
        return max(1, k)

    def _is_valid_embedding(self, emb: Optional[np.ndarray]) -> bool:
        if emb is None:
            return False
        arr = np.asarray(emb, dtype=np.float32)
        if arr.ndim == 0 or arr.size == 0:
            return False
        return bool(np.all(np.isfinite(arr)))

    def _get_embedding_dim(self, emb: Optional[np.ndarray]) -> Optional[int]:
        if emb is None:
            return None
        arr = np.asarray(emb)
        if arr.ndim == 1:
            return int(arr.shape[0]) if arr.size > 0 else None
        if arr.ndim == 2:
            if arr.shape[0] == 0:
                return None
            return int(arr.shape[1])
        return None

    def _is_embedding_dimension_compatible(self, emb: Optional[np.ndarray]) -> bool:
        got_dim = self._get_embedding_dim(emb)
        expected_dim = int(getattr(self.vector_store, "dimension", 0) or 0)
        if got_dim is None or expected_dim <= 0:
            return False
        return got_dim == expected_dim

    def _is_embedding_ready_for_vector_search(
        self,
        emb: Optional[np.ndarray],
        *,
        stage: str,
    ) -> bool:
        if not self._is_valid_embedding(emb):
            return False
        if self._is_embedding_dimension_compatible(emb):
            return True

        expected_dim = int(getattr(self.vector_store, "dimension", 0) or 0)
        got_dim = self._get_embedding_dim(emb)
        logger.warning(
            "metric.embedding_dim_mismatch_fallback_count=1 "
            f"stage={stage} expected_dim={expected_dim} got_dim={got_dim}"
        )
        return False

    def _should_use_sparse(
        self,
        embedding_ok: bool,
        vector_results: Optional[List[RetrievalResult]] = None,
    ) -> bool:
        if not self.config.sparse.enabled or self.sparse_index is None:
            return False

        mode = self.config.sparse.mode
        if mode == "hybrid":
            return True
        if mode == "fallback_only":
            return True
        # 自动模式（auto）
        if not embedding_ok:
            return True
        if not vector_results:
            return True
        best = max((float(r.score) for r in vector_results), default=0.0)
        return best < 0.45

    def _should_use_sparse_relations(
        self,
        embedding_ok: bool,
        relation_results: Optional[List[RetrievalResult]] = None,
        force_enable: bool = False,
    ) -> bool:
        if force_enable and self.config.sparse.enabled and self.sparse_index is not None:
            return True
        if not self.config.sparse.enable_relation_sparse_fallback:
            return False
        return self._should_use_sparse(embedding_ok, relation_results)

    def _normalize_scores_minmax(self, results: List[RetrievalResult]) -> None:
        if not results:
            return
        vals = [float(r.score) for r in results]
        lo = min(vals)
        hi = max(vals)
        if hi - lo < 1e-12:
            for r in results:
                r.score = 1.0
            return
        for r in results:
            r.score = (float(r.score) - lo) / (hi - lo)

    def _build_minmax_score_map(self, results: List[RetrievalResult]) -> Dict[str, float]:
        if not results:
            return {}
        vals = [float(r.score) for r in results]
        lo = min(vals)
        hi = max(vals)
        if hi - lo < 1e-12:
            return {r.hash_value: 1.0 for r in results}
        return {
            r.hash_value: (float(r.score) - lo) / (hi - lo)
            for r in results
        }

    @staticmethod
    def _clone_retrieval_result(item: RetrievalResult) -> RetrievalResult:
        return RetrievalResult(
            hash_value=item.hash_value,
            content=item.content,
            score=float(item.score),
            result_type=item.result_type,
            source=item.source,
            metadata=coerce_metadata_dict(item.metadata),
        )

    def _extract_graph_seed_entities(self, query: str, limit: int = 2) -> List[str]:
        entities = self._extract_entities(query)
        if not entities:
            return []
        ranked = sorted(
            entities.items(),
            key=lambda x: (-float(x[1]), -len(str(x[0])), str(x[0]).lower()),
        )
        return [str(name) for name, _ in ranked[: max(0, int(limit))]]

    def _search_relations_graph(
        self,
        query: str,
        temporal: Optional[TemporalQueryOptions] = None,
    ) -> List[RetrievalResult]:
        service = getattr(self, "_graph_relation_recall", None)
        if service is None or not bool(getattr(self.config.graph_recall, "enabled", True)):
            return []

        seed_entities = self._extract_graph_seed_entities(query, limit=2)
        if not seed_entities:
            return []

        payloads = service.recall(seed_entities=seed_entities)
        results: List[RetrievalResult] = []
        for payload in payloads:
            meta = payload.to_payload()
            results.append(
                RetrievalResult(
                    hash_value=str(meta["hash"]),
                    content=str(meta["content"]),
                    score=0.0,
                    result_type="relation",
                    source="graph_relation_recall",
                    metadata={
                        "subject": meta["subject"],
                        "predicate": meta["predicate"],
                        "object": meta["object"],
                        "confidence": float(meta["confidence"]),
                        "graph_seed_entities": list(meta["graph_seed_entities"]),
                        "graph_hops": int(meta["graph_hops"]),
                        "graph_candidate_type": str(meta["graph_candidate_type"]),
                        "supporting_paragraph_count": int(meta["supporting_paragraph_count"]),
                    },
                )
            )
        return self._apply_temporal_filter_to_relations(results, temporal)

    def _build_paragraph_results_from_ids(
        self,
        hash_values: List[str],
        scores: List[float],
        *,
        source: str,
        temporal: Optional[TemporalQueryOptions] = None,
    ) -> List[RetrievalResult]:
        """按向量/稀疏召回顺序批量回表构造段落结果。"""
        paragraph_map = self.metadata_store.get_paragraphs_by_hashes(hash_values)
        results: List[RetrievalResult] = []
        seen = set()
        for hash_value, score in zip(hash_values, scores, strict=False):
            if hash_value in seen:
                continue
            paragraph = paragraph_map.get(hash_value)
            if paragraph is None:
                continue
            if temporal and not self._is_temporal_match(paragraph, temporal):
                continue
            seen.add(hash_value)
            results.append(
                RetrievalResult(
                    hash_value=hash_value,
                    content=paragraph["content"],
                    score=float(score),
                    result_type="paragraph",
                    source=source,
                    metadata={
                        "word_count": paragraph.get("word_count", 0),
                        "time_meta": self._build_time_meta_from_paragraph(paragraph, temporal=temporal),
                    },
                )
            )
        if temporal:
            return self._sort_results_with_temporal(results, temporal)
        return results

    def _build_relation_results_from_ids(
        self,
        hash_values: List[str],
        scores: List[float],
        *,
        source: str,
        temporal: Optional[TemporalQueryOptions] = None,
        extra_metadata_by_hash: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[RetrievalResult]:
        """按向量/稀疏召回顺序批量回表构造关系结果。"""
        relation_map = self.metadata_store.get_relations_by_hashes(hash_values, include_inactive=False)
        supporting_time_meta: Dict[str, Optional[Dict[str, Any]]] = {}
        if temporal:
            supporting_time_meta = self._best_supporting_time_meta_batch(list(relation_map.keys()), temporal)

        results: List[RetrievalResult] = []
        seen = set()
        for hash_value, score in zip(hash_values, scores, strict=False):
            if hash_value in seen:
                continue
            relation = relation_map.get(hash_value)
            if relation is None:
                continue

            relation_time_meta = None
            if temporal:
                relation_time_meta = supporting_time_meta.get(hash_value)
                if relation_time_meta is None:
                    continue

            seen.add(hash_value)
            metadata = {
                "subject": relation["subject"],
                "predicate": relation["predicate"],
                "object": relation["object"],
                "confidence": relation.get("confidence", 1.0),
                "time_meta": relation_time_meta,
            }
            if extra_metadata_by_hash:
                metadata.update(extra_metadata_by_hash.get(hash_value, {}))
            results.append(
                RetrievalResult(
                    hash_value=hash_value,
                    content=f"{relation['subject']} {relation['predicate']} {relation['object']}",
                    score=float(score),
                    result_type="relation",
                    source=source,
                    metadata=metadata,
                )
            )
        if temporal:
            return self._sort_results_with_temporal(results, temporal)
        return results

    def _fuse_ranked_lists_weighted_rrf(
        self,
        vector_results: List[RetrievalResult],
        sparse_results: List[RetrievalResult],
    ) -> List[RetrievalResult]:
        """按 weighted RRF 融合两路段落召回。"""
        if not vector_results:
            out = sparse_results[:]
            if self.config.fusion.normalize_score:
                self._normalize_scores_minmax(out)
            return out
        if not sparse_results:
            out = vector_results[:]
            if self.config.fusion.normalize_score:
                self._normalize_scores_minmax(out)
            return out

        k = self.config.fusion.rrf_k
        w_vec = self.config.fusion.vector_weight
        w_sparse = self.config.fusion.bm25_weight
        merged: Dict[str, RetrievalResult] = {}
        score_map: Dict[str, float] = {}

        for rank, item in enumerate(vector_results, start=1):
            h = item.hash_value
            if h not in merged:
                merged[h] = item
                merged[h].source = "fusion_rrf"
            score_map[h] = score_map.get(h, 0.0) + w_vec * (1.0 / (k + rank))

        for rank, item in enumerate(sparse_results, start=1):
            h = item.hash_value
            if h not in merged:
                merged[h] = item
                merged[h].source = "fusion_rrf"
            score_map[h] = score_map.get(h, 0.0) + w_sparse * (1.0 / (k + rank))

        out = list(merged.values())
        for item in out:
            item.score = float(score_map.get(item.hash_value, 0.0))

        out.sort(key=lambda x: x.score, reverse=True)
        if self.config.fusion.normalize_score and self.config.fusion.normalize_method == "minmax":
            self._normalize_scores_minmax(out)
        return out

    def _search_paragraphs_sparse(
        self,
        query: str,
        top_k: int,
        temporal: Optional[TemporalQueryOptions] = None,
    ) -> List[RetrievalResult]:
        """BM25 段落召回。"""
        if not self.sparse_index or not self.config.sparse.enabled:
            return []

        candidate_k = max(top_k, self.config.sparse.candidate_k)
        candidate_k = self._cap_temporal_scan_k(candidate_k, temporal)
        sparse_rows = self.sparse_index.search(query=query, k=candidate_k)
        sparse_rows = self._filter_sparse_paragraph_rows(sparse_rows)
        hash_values = [str(row.get("hash", "") or "") for row in sparse_rows]
        scores = [float(row.get("score", 0.0)) for row in sparse_rows]
        bm25_scores = {
            str(row.get("hash", "") or ""): float(row.get("bm25_score", 0.0))
            for row in sparse_rows
        }
        results = self._build_paragraph_results_from_ids(
            hash_values,
            scores,
            source="sparse_bm25",
            temporal=temporal,
        )
        for item in results:
            item.metadata["bm25_score"] = bm25_scores.get(item.hash_value, 0.0)
        if self.config.fusion.normalize_score and self.config.fusion.normalize_method == "minmax":
            self._normalize_scores_minmax(results)
        return results

    def _filter_sparse_paragraph_rows(
        self,
        rows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        过滤 paragraph sparse tail。

        目标不是压缩强 lexical hit，而是避免只命中一个弱 token 的尾部结果
        在 weighted RRF 中拿到过高的 rank credit。
        """
        if len(rows) <= 2:
            return rows

        top_score = max(0.0, float(rows[0].get("score", 0.0) or 0.0))
        if top_score <= 0.0:
            return rows[:2]

        relative_floor = top_score * 0.2
        filtered_rows: List[Dict[str, Any]] = []
        removed_count = 0
        for index, row in enumerate(rows):
            if index < 2:
                filtered_rows.append(row)
                continue

            raw_score = float(row.get("score", 0.0) or 0.0)
            matched_token_count = int(row.get("matched_token_count", 0) or 0)
            matched_token_ratio = float(row.get("matched_token_ratio", 0.0) or 0.0)

            if (
                raw_score >= relative_floor
                or matched_token_count >= 3
                or (matched_token_count >= 2 and matched_token_ratio >= 0.12)
            ):
                filtered_rows.append(row)
                continue

            removed_count += 1

        if removed_count > 0:
            logger.debug(
                "sparse_paragraph_tail_pruned=1 "
                f"removed_count={removed_count} "
                f"kept_count={len(filtered_rows)}"
            )
        return filtered_rows

    def _search_relations_sparse(
        self,
        query: str,
        top_k: int,
        temporal: Optional[TemporalQueryOptions] = None,
    ) -> List[RetrievalResult]:
        """关系 BM25 召回。"""
        if not self.sparse_index or not self.config.sparse.enabled:
            return []
        if not self.config.sparse.enable_relation_sparse_fallback:
            return []

        candidate_k = max(top_k, self.config.sparse.relation_candidate_k)
        candidate_k = self._cap_temporal_scan_k(candidate_k, temporal)
        rows = self.sparse_index.search_relations(query=query, k=candidate_k)
        hash_values = [str(row.get("hash", "") or "") for row in rows]
        scores = [float(row.get("score", 0.0)) for row in rows]
        bm25_scores = {
            str(row.get("hash", "") or ""): float(row.get("bm25_score", 0.0))
            for row in rows
        }
        results = self._build_relation_results_from_ids(
            hash_values,
            scores,
            source="sparse_relation_bm25",
            temporal=temporal,
        )
        for item in results:
            item.metadata["bm25_score"] = bm25_scores.get(item.hash_value, 0.0)

        if self.config.fusion.normalize_score and self.config.fusion.normalize_method == "minmax":
            self._normalize_scores_minmax(results)
        return results

    def _merge_relation_results(
        self,
        vector_results: List[RetrievalResult],
        sparse_results: List[RetrievalResult],
    ) -> List[RetrievalResult]:
        """合并关系候选，按 hash 去重并保留更高分。"""
        merged: Dict[str, RetrievalResult] = {}
        for item in vector_results:
            merged[item.hash_value] = item
        for item in sparse_results:
            old = merged.get(item.hash_value)
            if old is None or float(item.score) > float(old.score):
                merged[item.hash_value] = item
            elif old is not None and old.source != item.source:
                old.source = "relation_fusion"
        out = list(merged.values())
        out.sort(key=lambda x: x.score, reverse=True)
        return out

    def _merge_relation_results_graph_enhanced(
        self,
        vector_results: List[RetrievalResult],
        sparse_results: List[RetrievalResult],
        graph_results: List[RetrievalResult],
    ) -> List[RetrievalResult]:
        """融合语义、图结构和证据评分的图感知关系结果。"""
        vector_norm = self._build_minmax_score_map(vector_results)
        sparse_norm = self._build_minmax_score_map(sparse_results)
        graph_score_map = {
            "direct_pair": 1.0,
            "one_hop_seed": 0.75,
            "two_hop_pair": 0.55,
        }

        merged: Dict[str, RetrievalResult] = {}
        source_sets: Dict[str, set[str]] = {}
        support_cache: Dict[str, int] = {}

        for group in (vector_results, sparse_results, graph_results):
            for item in group:
                existing = merged.get(item.hash_value)
                if existing is None:
                    existing = self._clone_retrieval_result(item)
                    merged[item.hash_value] = existing
                else:
                    for key, value in coerce_metadata_dict(item.metadata).items():
                        if key not in existing.metadata or existing.metadata.get(key) in (None, "", []):
                            existing.metadata[key] = value
                source_sets.setdefault(item.hash_value, set()).add(str(item.source or "").strip() or "relation_search")

        out = list(merged.values())
        missing_support_hashes = [
            item.hash_value
            for item in out
            if coerce_metadata_dict(item.metadata).get("supporting_paragraph_count") is None
        ]
        support_counts = {
            hash_value: len(paragraphs)
            for hash_value, paragraphs in self.metadata_store.get_paragraphs_by_relation_hashes(missing_support_hashes).items()
        }
        for item in out:
            meta = item.metadata if isinstance(item.metadata, dict) else {}
            semantic_norm = max(
                float(vector_norm.get(item.hash_value, 0.0)),
                float(sparse_norm.get(item.hash_value, 0.0)),
            )
            graph_candidate_type = str(meta.get("graph_candidate_type", "") or "")
            graph_score = float(graph_score_map.get(graph_candidate_type, 0.0))

            if item.hash_value not in support_cache:
                cached = meta.get("supporting_paragraph_count")
                if cached is None:
                    support_cache[item.hash_value] = int(support_counts.get(item.hash_value, 0))
                else:
                    support_cache[item.hash_value] = max(0, int(cached))
            supporting_paragraph_count = support_cache[item.hash_value]
            evidence_score = min(1.0, supporting_paragraph_count / 3.0)

            meta["supporting_paragraph_count"] = supporting_paragraph_count
            meta["graph_seed_entities"] = list(meta.get("graph_seed_entities") or [])
            if "graph_hops" in meta:
                meta["graph_hops"] = int(meta.get("graph_hops") or 0)
            item.score = 0.60 * semantic_norm + 0.30 * graph_score + 0.10 * evidence_score

            sources = source_sets.get(item.hash_value, set())
            if len(sources) > 1:
                item.source = "relation_fusion"
            elif sources:
                item.source = next(iter(sources))

        out.sort(key=lambda x: x.score, reverse=True)
        return out

    async def _retrieve_paragraphs_only(
        self,
        query: str,
        top_k: int,
        temporal: Optional[TemporalQueryOptions] = None,
    ) -> List[RetrievalResult]:
        """
        仅检索段落（异步方法）

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            检索结果列表
        """
        if self._is_sparse_only_runtime():
            sparse_results = self._search_paragraphs_sparse(query, top_k, temporal=temporal)
            return sparse_results[:top_k]

        query_emb = None
        embedding_ok = False
        vector_results: List[RetrievalResult] = []

        try:
            query_emb = await self.embedding_manager.encode(query)
            embedding_ok = self._is_embedding_ready_for_vector_search(
                query_emb,
                stage="paragraph_only",
            )
        except Exception as e:
            logger.warning(f"段落检索 embedding 生成失败，将尝试 sparse 回退: {e}")

        if embedding_ok:
            multiplier = max(1, temporal.candidate_multiplier) if temporal else 1
            candidate_k = self._cap_temporal_scan_k(top_k * 2 * multiplier, temporal)
            para_ids, para_scores = self.vector_store.search(
                query_emb,  # type: ignore[arg-type]
                k=candidate_k,
            )
            vector_results = self._build_paragraph_results_from_ids(
                list(para_ids),
                [float(score) for score in para_scores],
                source="paragraph_search",
                temporal=temporal,
            )

        sparse_results: List[RetrievalResult] = []
        if self._should_use_sparse(embedding_ok, vector_results):
            sparse_results = self._search_paragraphs_sparse(query, top_k, temporal=temporal)

        if self.config.fusion.method == "weighted_rrf" and (vector_results and sparse_results):
            results = self._fuse_ranked_lists_weighted_rrf(vector_results, sparse_results)
        elif vector_results and sparse_results:
            results = vector_results + sparse_results
            results.sort(key=lambda x: x.score, reverse=True)
        else:
            results = vector_results if vector_results else sparse_results

        return results[:top_k]

    async def _retrieve_relations_only(
        self,
        query: str,
        top_k: int,
        temporal: Optional[TemporalQueryOptions] = None,
    ) -> List[RetrievalResult]:
        """
        仅检索关系 (通过实体枢纽 Entity-Pivot)
        
        策略:
        1. 检索向量库中的 Top-K 实体 (Entity)
        2. 通过图结构/元数据扩展出与实体关联的关系 (Relation)
        3. 以实体相似度作为基础分返回关系

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            检索结果列表
        """
        if self._is_sparse_only_runtime():
            sparse_results = self._search_relations_sparse(query=query, top_k=top_k, temporal=temporal)
            graph_results = self._search_relations_graph(query=query, temporal=temporal)
            if graph_results:
                merged = self._merge_relation_results_graph_enhanced(
                    [],
                    sparse_results,
                    graph_results,
                )
                return merged[:top_k]
            return sparse_results[:top_k]

        query_emb = None
        embedding_ok = False
        vector_results: List[RetrievalResult] = []
        try:
            query_emb = await self.embedding_manager.encode(query)
            embedding_ok = self._is_embedding_ready_for_vector_search(
                query_emb,
                stage="relation_only",
            )
        except Exception as e:
            logger.warning(f"关系检索 embedding 生成失败，将尝试 sparse 回退: {e}")

        if embedding_ok:
            # 1. 检索向量 (混合了段落和实体，所以扩大检索范围以召回足够多实体)
            multiplier = max(1, temporal.candidate_multiplier) if temporal else 1
            candidate_k = self._cap_temporal_scan_k(top_k * 3 * multiplier, temporal)
            ids, scores = self.vector_store.search(
                query_emb,  # type: ignore[arg-type]
                k=candidate_k,
            )

            seen_relations = set()
            entity_map = self.metadata_store.get_entities_by_hashes(ids)
            relation_scores: Dict[str, float] = {}
            relation_pivots: Dict[str, str] = {}
            for hash_value, score in zip(ids, scores, strict=False):
                entity = entity_map.get(hash_value)
                if not entity:
                    continue
                entity_name = entity["name"]

                related_rels = []
                related_rels.extend(self.metadata_store.get_relations(subject=entity_name, include_inactive=False))
                related_rels.extend(self.metadata_store.get_relations(object=entity_name, include_inactive=False))

                for rel in related_rels:
                    if rel["hash"] in seen_relations:
                        continue
                    seen_relations.add(rel["hash"])
                    relation_scores[rel["hash"]] = float(score)
                    relation_pivots[rel["hash"]] = str(entity_name)

            vector_results = self._build_relation_results_from_ids(
                list(relation_scores.keys()),
                list(relation_scores.values()),
                source="relation_search (via entity)",
                temporal=temporal,
                extra_metadata_by_hash={
                    hash_value: {"pivot_entity": pivot}
                    for hash_value, pivot in relation_pivots.items()
                },
            )

        sparse_results: List[RetrievalResult] = []
        if self._should_use_sparse_relations(embedding_ok, vector_results):
            sparse_results = self._search_relations_sparse(query=query, top_k=top_k, temporal=temporal)

        graph_results = self._search_relations_graph(query=query, temporal=temporal)
        if graph_results:
            results = self._merge_relation_results_graph_enhanced(
                vector_results,
                sparse_results,
                graph_results,
            )
        elif vector_results and sparse_results:
            results = self._merge_relation_results(vector_results, sparse_results)
        else:
            results = vector_results if vector_results else sparse_results

        return results[:top_k]

    async def _retrieve_dual_path(
        self,
        query: str,
        top_k: int,
        temporal: Optional[TemporalQueryOptions] = None,
        relation_intent: Optional[Dict[str, Any]] = None,
    ) -> List[RetrievalResult]:
        """
        双路检索（段落+关系）（异步方法）

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            融合后的检索结果列表
        """
        if self.config.vector_pools.mode == "dual" and not self._is_sparse_only_runtime():
            return await self._retrieve_dual_vector_pools(
                query=query,
                top_k=top_k,
                temporal=temporal,
                relation_intent=relation_intent,
            )

        query_emb = None
        embedding_ok = False
        relation_intent = relation_intent or {}
        relation_top_k = max(
            1,
            int(relation_intent.get("relation_top_k", self.config.top_k_relations)),
        )
        force_relation_sparse = bool(relation_intent.get("force_relation_sparse", False))
        preserve_top_relations = max(
            0,
            int(relation_intent.get("preserve_top_relations", 0)),
        )
        pair_predicate_rerank_enabled = bool(
            relation_intent.get("pair_predicate_rerank_enabled", False)
        )
        pair_predicate_limit = max(
            1,
            int(
                relation_intent.get(
                    "pair_predicate_limit",
                    self.config.relation_intent.pair_predicate_limit,
                )
            ),
        )
        alpha_override = relation_intent.get("alpha_override")

        if self._is_sparse_only_runtime():
            para_results = self._search_paragraphs_sparse(
                query=query,
                top_k=max(top_k * 2, self.config.sparse.candidate_k),
                temporal=temporal,
            )
            sparse_rel_results = self._search_relations_sparse(
                query=query,
                top_k=max(
                    top_k,
                    self.config.sparse.relation_candidate_k,
                    relation_top_k,
                ),
                temporal=temporal,
            )
            graph_rel_results: List[RetrievalResult] = []
            if bool(relation_intent.get("enabled", False)):
                graph_rel_results = self._search_relations_graph(query=query, temporal=temporal)
            if graph_rel_results:
                rel_results = self._merge_relation_results_graph_enhanced(
                    [],
                    sparse_rel_results,
                    graph_rel_results,
                )
            else:
                rel_results = sparse_rel_results

            fused_results = self._fuse_results(
                para_results,
                rel_results,
                None,
                alpha_override=alpha_override,
                preserve_top_relations=preserve_top_relations,
            )
            if self.config.enable_ppr:
                fused_results = await self._rerank_with_ppr(
                    fused_results,
                    query,
                )
            if temporal:
                fused_results = self._sort_results_with_temporal(fused_results, temporal)
            fused_results = apply_posterior_graph_gate(
                self,
                query=query,
                base_results=fused_results,
                top_k=top_k,
                temporal=temporal,
                relation_intent=relation_intent,
            )
            fused_results = self._apply_relation_intent_pair_rerank(
                fused_results,
                enabled=bool(relation_intent.get("enabled", False)),
                pair_rerank_enabled=pair_predicate_rerank_enabled,
                pair_limit=pair_predicate_limit,
            )
            return fused_results[:top_k]

        try:
            query_emb = await self.embedding_manager.encode(query)
            embedding_ok = self._is_embedding_ready_for_vector_search(
                query_emb,
                stage="dual_path",
            )
        except Exception as e:
            logger.warning(f"双路检索 embedding 生成失败，将尝试 sparse 回退: {e}")

        para_results: List[RetrievalResult] = []
        rel_results: List[RetrievalResult] = []
        if embedding_ok:
            # 并行检索（使用 asyncio）
            if self.config.enable_parallel:
                para_results, rel_results = await self._parallel_retrieve(
                    query_emb,
                    temporal=temporal,
                    relation_top_k=relation_top_k,
                )  # type: ignore[arg-type]
            else:
                para_results, rel_results = self._sequential_retrieve(
                    query_emb,
                    temporal=temporal,
                    relation_top_k=relation_top_k,
                )  # type: ignore[arg-type]
        else:
            logger.warning("embedding 不可用，跳过向量段落/关系召回")

        sparse_para_results: List[RetrievalResult] = []
        if self._should_use_sparse(embedding_ok, para_results):
            sparse_para_results = self._search_paragraphs_sparse(
                query=query,
                top_k=max(top_k * 2, self.config.sparse.candidate_k),
                temporal=temporal,
            )
        sparse_rel_results: List[RetrievalResult] = []
        if self._should_use_sparse_relations(
            embedding_ok,
            rel_results,
            force_enable=force_relation_sparse,
        ):
            sparse_rel_results = self._search_relations_sparse(
                query=query,
                top_k=max(
                    top_k,
                    self.config.sparse.relation_candidate_k,
                    relation_top_k,
                ),
                temporal=temporal,
            )

        graph_rel_results: List[RetrievalResult] = []
        if bool(relation_intent.get("enabled", False)):
            graph_rel_results = self._search_relations_graph(query=query, temporal=temporal)

        if self.config.fusion.method == "weighted_rrf" and para_results and sparse_para_results:
            para_results = self._fuse_ranked_lists_weighted_rrf(para_results, sparse_para_results)
        elif para_results and sparse_para_results:
            para_results = para_results + sparse_para_results
            para_results.sort(key=lambda x: x.score, reverse=True)
        elif sparse_para_results and (not para_results or not embedding_ok):
            para_results = sparse_para_results

        if graph_rel_results:
            rel_results = self._merge_relation_results_graph_enhanced(
                rel_results,
                sparse_rel_results,
                graph_rel_results,
            )
        elif rel_results and sparse_rel_results:
            rel_results = self._merge_relation_results(rel_results, sparse_rel_results)
        elif sparse_rel_results and (not rel_results or not embedding_ok):
            rel_results = sparse_rel_results

        # 融合结果
        fused_results = self._fuse_results(
            para_results,
            rel_results,
            query_emb,
            alpha_override=alpha_override,
            preserve_top_relations=preserve_top_relations,
        )

        # PageRank重排序
        if self.config.enable_ppr:
            fused_results = await self._rerank_with_ppr(
                fused_results,
                query,
            )

        if temporal:
            fused_results = self._sort_results_with_temporal(fused_results, temporal)

        fused_results = apply_posterior_graph_gate(
            self,
            query=query,
            base_results=fused_results,
            top_k=top_k,
            temporal=temporal,
            relation_intent=relation_intent,
        )

        fused_results = self._apply_relation_intent_pair_rerank(
            fused_results,
            enabled=bool(relation_intent.get("enabled", False)),
            pair_rerank_enabled=pair_predicate_rerank_enabled,
            pair_limit=pair_predicate_limit,
        )

        return fused_results[:top_k]

    async def _parallel_retrieve(
        self,
        query_emb: np.ndarray,
        temporal: Optional[TemporalQueryOptions] = None,
        relation_top_k: Optional[int] = None,
    ) -> Tuple[List[RetrievalResult], List[RetrievalResult]]:
        """
        并行检索段落和关系（异步方法）

        Args:
            query_emb: 查询嵌入

        Returns:
            (段落结果, 关系结果)
        """
        try:
            return await asyncio.to_thread(
                self._collect_mixed_candidates,
                query_emb,
                temporal,
                relation_top_k,
            )
        except Exception as e:
            logger.error(f"并行检索失败: {e}")
            return [], []

    def _sequential_retrieve(
        self,
        query_emb: np.ndarray,
        temporal: Optional[TemporalQueryOptions] = None,
        relation_top_k: Optional[int] = None,
    ) -> Tuple[List[RetrievalResult], List[RetrievalResult]]:
        """
        顺序检索段落和关系

        Args:
            query_emb: 查询嵌入

        Returns:
            (段落结果, 关系结果)
        """
        return self._collect_mixed_candidates(
            query_emb,
            temporal,
            relation_top_k,
        )

    def _mixed_candidate_budget(
        self,
        para_top_k: int,
        rel_top_k: int,
        temporal: Optional[TemporalQueryOptions],
    ) -> int:
        multiplier = max(1, temporal.candidate_multiplier) if temporal else 1
        base = max(para_top_k + rel_top_k, max(para_top_k, rel_top_k) * 2)
        return max(base * 6 * multiplier, 48)

    def _merge_backfilled_results(
        self,
        *,
        primary_results: List[RetrievalResult],
        backfill_results: List[RetrievalResult],
        top_k: int,
    ) -> List[RetrievalResult]:
        merged: Dict[str, RetrievalResult] = {}
        for item in primary_results:
            merged[item.hash_value] = item
        for item in backfill_results:
            existing = merged.get(item.hash_value)
            if existing is None or float(item.score) > float(existing.score):
                merged[item.hash_value] = item

        results = list(merged.values())
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

    @staticmethod
    def _graph_vector_id(item_type: str, hash_value: str) -> str:
        return f"{str(item_type or '').strip()}:{str(hash_value or '').strip()}"

    @staticmethod
    def _parse_graph_vector_id(value: str) -> Tuple[str, str]:
        token = str(value or "").strip()
        if ":" not in token:
            return "", token
        item_type, hash_value = token.split(":", 1)
        return item_type.strip().lower(), hash_value.strip()

    def _dual_pool_weights(self, relation_intent: Dict[str, Any]) -> Tuple[float, float, float, int]:
        cfg = self.config.vector_pools
        if bool(relation_intent.get("enabled", False)):
            return (
                float(cfg.relation_intent_semantic_weight),
                float(cfg.relation_intent_sparse_weight),
                float(cfg.relation_intent_graph_weight),
                int(cfg.relation_intent_graph_top_k),
            )
        return (
            float(cfg.semantic_weight),
            float(cfg.sparse_weight),
            float(cfg.graph_weight),
            int(cfg.graph_top_k),
        )

    def _ensure_paragraph_candidate(
        self,
        candidates: Dict[str, RetrievalResult],
        paragraph: Dict[str, Any],
        *,
        temporal: Optional[TemporalQueryOptions] = None,
    ) -> RetrievalResult:
        paragraph_hash = str(paragraph.get("hash", "") or "").strip()
        item = candidates.get(paragraph_hash)
        if item is None:
            metadata = {
                "word_count": paragraph.get("word_count", 0),
                "score_breakdown": {},
                "evidence_items": [],
            }
            if temporal:
                metadata["time_meta"] = self._build_time_meta_from_paragraph(paragraph, temporal=temporal)
            item = RetrievalResult(
                hash_value=paragraph_hash,
                content=str(paragraph.get("content", "") or ""),
                score=0.0,
                result_type="paragraph",
                source="dual_vector_pool",
                metadata=metadata,
            )
            candidates[paragraph_hash] = item
        return item

    @staticmethod
    def _candidate_score_meta(candidate: RetrievalResult) -> Dict[str, Any]:
        metadata = candidate.metadata if isinstance(candidate.metadata, dict) else None
        if metadata is None:
            metadata = {}
            candidate.metadata = metadata
        score_meta = metadata.get("score_breakdown")
        if not isinstance(score_meta, dict):
            score_meta = {}
            metadata["score_breakdown"] = score_meta
        return score_meta

    @staticmethod
    def _candidate_evidence_items(candidate: RetrievalResult) -> List[Dict[str, Any]]:
        metadata = candidate.metadata if isinstance(candidate.metadata, dict) else None
        if metadata is None:
            metadata = {}
            candidate.metadata = metadata
        evidence_items = metadata.get("evidence_items")
        if not isinstance(evidence_items, list):
            evidence_items = []
            metadata["evidence_items"] = evidence_items
        return evidence_items

    @staticmethod
    def _mark_candidate_source(candidate: RetrievalResult, source: str) -> None:
        metadata = candidate.metadata if isinstance(candidate.metadata, dict) else {}
        sources = metadata.get("dual_pool_sources")
        if not isinstance(sources, list):
            sources = []
        if source not in sources:
            sources.append(source)
        metadata["dual_pool_sources"] = sources
        candidate.metadata = metadata

    def _add_candidate_score(
        self,
        candidates: Dict[str, RetrievalResult],
        paragraph: Dict[str, Any],
        *,
        score_key: str,
        score: float,
        source: str,
        temporal: Optional[TemporalQueryOptions],
    ) -> RetrievalResult:
        item = self._ensure_paragraph_candidate(candidates, paragraph, temporal=temporal)
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        if temporal:
            metadata["time_meta"] = self._build_time_meta_from_paragraph(paragraph, temporal=temporal)
        score_meta = self._candidate_score_meta(item)
        old_score = float(score_meta.get(score_key, 0.0) or 0.0)
        score_meta[score_key] = max(old_score, float(score))
        item.metadata = metadata
        self._mark_candidate_source(item, source)
        return item

    def _append_graph_evidence(
        self,
        candidate: RetrievalResult,
        *,
        evidence: Dict[str, Any],
        score: float,
    ) -> None:
        evidence_items = self._candidate_evidence_items(candidate)
        evidence_payload = dict(evidence)
        evidence_payload["score"] = float(score)
        evidence_key = (
            str(evidence_payload.get("type", "") or ""),
            str(evidence_payload.get("hash", "") or ""),
        )
        for old in evidence_items:
            old_key = (
                str(old.get("type", "") or ""),
                str(old.get("hash", "") or ""),
            )
            if old_key == evidence_key:
                if float(score) > float(old.get("score", 0.0) or 0.0):
                    old.update(evidence_payload)
                return
        evidence_items.append(evidence_payload)

    def _aggregate_graph_evidence_score(self, candidate: RetrievalResult) -> float:
        evidence_items = self._candidate_evidence_items(candidate)
        relation_scores = sorted(
            [
                float(item.get("score", 0.0) or 0.0)
                for item in evidence_items
                if str(item.get("type", "") or "") == "relation"
            ],
            reverse=True,
        )
        entity_scores = sorted(
            [
                float(item.get("score", 0.0) or 0.0)
                for item in evidence_items
                if str(item.get("type", "") or "") == "entity"
            ],
            reverse=True,
        )
        graph_score = 0.0
        if relation_scores:
            graph_score += relation_scores[0]
            graph_score += 0.35 * sum(relation_scores[1:])
        graph_score += 0.20 * sum(entity_scores)
        return min(1.0, graph_score)

    @staticmethod
    def _normalize_graph_scores_by_type(
        parsed_items: List[Tuple[str, str, float]],
    ) -> List[Tuple[str, str, float, float]]:
        scores_by_type: Dict[str, List[float]] = {}
        for item_type, _hash_value, raw_score in parsed_items:
            scores_by_type.setdefault(item_type, []).append(float(raw_score))

        bounds_by_type = {
            item_type: (min(scores), max(scores))
            for item_type, scores in scores_by_type.items()
            if scores
        }
        normalized_items: List[Tuple[str, str, float, float]] = []
        for item_type, hash_value, raw_score in parsed_items:
            min_score, max_score = bounds_by_type.get(item_type, (0.0, 0.0))
            if max_score > min_score:
                normalized_score = (float(raw_score) - min_score) / (max_score - min_score)
            else:
                normalized_score = max(0.0, min(1.0, float(raw_score)))
            normalized_items.append((item_type, hash_value, float(raw_score), float(normalized_score)))
        return normalized_items

    async def _collect_dual_graph_evidence(
        self,
        *,
        query_emb: np.ndarray,
        top_k: int,
        temporal: Optional[TemporalQueryOptions],
    ) -> Dict[str, Dict[str, Any]]:
        cfg = self.config.vector_pools
        graph_store = self.graph_vector_store
        graph_ids, graph_scores = await asyncio.to_thread(graph_store.search, query_emb, k=top_k)
        parsed_items: List[Tuple[str, str, float]] = []
        relation_hashes: List[str] = []
        entity_hashes: List[str] = []
        for raw_id, raw_score in zip(graph_ids, graph_scores, strict=False):
            item_type, hash_value = self._parse_graph_vector_id(raw_id)
            if item_type not in {"relation", "entity"} or not hash_value:
                continue
            score = float(raw_score)
            parsed_items.append((item_type, hash_value, score))
            if item_type == "relation":
                relation_hashes.append(hash_value)
            else:
                entity_hashes.append(hash_value)
        normalized_items = self._normalize_graph_scores_by_type(parsed_items)

        relation_rows = self.metadata_store.get_relations_by_hashes(
            relation_hashes,
            include_inactive=False,
        )
        entity_rows = self.metadata_store.get_entities_by_hashes(entity_hashes)
        relation_paragraphs = self.metadata_store.get_paragraphs_by_relation_hashes(relation_hashes)
        entity_paragraphs_getter = getattr(
            self.metadata_store,
            "get_paragraphs_by_entity_hashes",
            None,
        )
        if callable(entity_paragraphs_getter):
            entity_paragraphs = entity_paragraphs_getter(entity_hashes)
        else:
            entity_paragraphs = {}

        expanded_entries: List[Tuple[float, int, Dict[str, Any], Dict[str, Any]]] = []
        for item_index, (item_type, hash_value, raw_score, normalized_score) in enumerate(normalized_items):
            if item_type == "relation":
                relation = relation_rows.get(hash_value)
                if relation is None:
                    continue
                evidence_score = float(normalized_score) * float(cfg.relation_evidence_weight)
                paragraphs = relation_paragraphs.get(hash_value, [])[: cfg.relation_expand_per_hit]
                evidence = {
                    "type": "relation",
                    "hash": hash_value,
                    "subject": relation.get("subject", ""),
                    "predicate": relation.get("predicate", ""),
                    "object": relation.get("object", ""),
                    "source": "graph_vector_relation",
                    "raw_score": float(raw_score),
                    "normalized_score": float(normalized_score),
                }
            else:
                entity = entity_rows.get(hash_value)
                if entity is None:
                    continue
                evidence_score = float(normalized_score) * float(cfg.entity_evidence_weight)
                paragraphs = entity_paragraphs.get(hash_value, [])[: cfg.entity_expand_per_hit]
                evidence = {
                    "type": "entity",
                    "hash": hash_value,
                    "name": entity.get("name", ""),
                    "source": "graph_vector_entity",
                    "raw_score": float(raw_score),
                    "normalized_score": float(normalized_score),
                }

            for paragraph_index, paragraph in enumerate(paragraphs):
                paragraph_hash = str(paragraph.get("hash", "") or "").strip()
                if not paragraph_hash:
                    continue
                if temporal and not self._is_temporal_match(paragraph, temporal):
                    continue
                order = item_index * max(1, max(cfg.relation_expand_per_hit, cfg.entity_expand_per_hit)) + paragraph_index
                expanded_entries.append((float(evidence_score), order, paragraph, dict(evidence)))

        expanded_entries.sort(key=lambda item: (-item[0], item[1]))
        evidence_by_paragraph: Dict[str, Dict[str, Any]] = {}
        for evidence_score, _order, paragraph, evidence in expanded_entries[: cfg.graph_expand_paragraph_k]:
            paragraph_hash = str(paragraph.get("hash", "") or "").strip()
            if not paragraph_hash:
                continue
            payload = evidence_by_paragraph.setdefault(
                paragraph_hash,
                {
                    "paragraph": paragraph,
                    "evidence": [],
                },
            )
            payload["evidence"].append((evidence, float(evidence_score)))
        return evidence_by_paragraph

    async def _retrieve_dual_vector_pools(
        self,
        query: str,
        top_k: int,
        temporal: Optional[TemporalQueryOptions] = None,
        relation_intent: Optional[Dict[str, Any]] = None,
    ) -> List[RetrievalResult]:
        relation_intent = relation_intent or {}
        semantic_weight, sparse_weight, graph_weight, graph_top_k = self._dual_pool_weights(relation_intent)
        try:
            query_emb = await self.embedding_manager.encode(query)
            embedding_ok = self._is_embedding_ready_for_vector_search(
                query_emb,
                stage="dual_vector_pool",
            )
        except Exception as e:
            logger.warning(f"双向量池检索 embedding 生成失败，将尝试 sparse 回退: {e}")
            embedding_ok = False
            query_emb = None

        candidates: Dict[str, RetrievalResult] = {}
        if embedding_ok and query_emb is not None:
            paragraph_top_k = max(top_k, int(self.config.vector_pools.paragraph_top_k))
            para_ids, para_scores = await asyncio.to_thread(
                self.paragraph_vector_store.search,
                query_emb,
                k=paragraph_top_k,
            )
            paragraph_map = self.metadata_store.get_paragraphs_by_hashes(para_ids)
            for hash_value, score in zip(para_ids, para_scores, strict=False):
                paragraph = paragraph_map.get(hash_value)
                if paragraph is None:
                    continue
                if temporal and not self._is_temporal_match(paragraph, temporal):
                    continue
                self._add_candidate_score(
                    candidates,
                    paragraph,
                    score_key="semantic",
                    score=float(score),
                    source="paragraph_vector_pool",
                    temporal=temporal,
                )

            graph_evidence = await self._collect_dual_graph_evidence(
                query_emb=query_emb,
                top_k=graph_top_k,
                temporal=temporal,
            )
            for payload in graph_evidence.values():
                paragraph = payload.get("paragraph")
                if not isinstance(paragraph, dict):
                    continue
                candidate = self._ensure_paragraph_candidate(candidates, paragraph, temporal=temporal)
                self._mark_candidate_source(candidate, "graph_vector_pool")
                for evidence, evidence_score in payload.get("evidence", []):
                    self._append_graph_evidence(
                        candidate,
                        evidence=evidence,
                        score=float(evidence_score),
                    )
                score_meta = self._candidate_score_meta(candidate)
                score_meta["graph_evidence"] = self._aggregate_graph_evidence_score(candidate)
        else:
            logger.warning("embedding 不可用，跳过双向量池向量召回")

        sparse_para_results: List[RetrievalResult] = []
        if self._should_use_sparse(embedding_ok, list(candidates.values())):
            sparse_para_results = self._search_paragraphs_sparse(
                query=query,
                top_k=max(top_k * 2, self.config.sparse.candidate_k),
                temporal=temporal,
            )
        for sparse_item in sparse_para_results:
            paragraph = {
                "hash": sparse_item.hash_value,
                "content": sparse_item.content,
                "word_count": sparse_item.metadata.get("word_count", 0)
                if isinstance(sparse_item.metadata, dict)
                else 0,
            }
            candidate = self._add_candidate_score(
                candidates,
                paragraph,
                score_key="sparse",
                score=float(sparse_item.score),
                source="paragraph_sparse",
                temporal=temporal,
            )
            if isinstance(sparse_item.metadata, dict):
                candidate.metadata.update(
                    {
                        key: value
                        for key, value in sparse_item.metadata.items()
                        if key not in {"score_breakdown", "evidence_items"}
                    }
                )

        results = list(candidates.values())
        for item in results:
            score_meta = self._candidate_score_meta(item)
            semantic_score = float(score_meta.get("semantic", 0.0) or 0.0)
            sparse_score = float(score_meta.get("sparse", 0.0) or 0.0)
            graph_score = float(
                score_meta.get("graph_evidence", self._aggregate_graph_evidence_score(item))
                or 0.0
            )
            final_score = (
                semantic_weight * semantic_score
                + sparse_weight * sparse_score
                + graph_weight * graph_score
            )
            score_meta.update(
                {
                    "semantic": semantic_score,
                    "sparse": sparse_score,
                    "graph_evidence": graph_score,
                    "semantic_weight": semantic_weight,
                    "sparse_weight": sparse_weight,
                    "graph_weight": graph_weight,
                    "final": final_score,
                }
            )
            item.score = float(final_score)
            item.source = "dual_vector_pool"

        results.sort(key=lambda x: x.score, reverse=True)

        if self.config.enable_ppr:
            results = await self._rerank_with_ppr(results, query)

        if temporal:
            results = self._sort_results_with_temporal(results, temporal)

        results = apply_posterior_graph_gate(
            self,
            query=query,
            base_results=results,
            top_k=top_k,
            temporal=temporal,
            relation_intent=relation_intent,
        )

        return results[:top_k]

    def _collect_mixed_candidates(
        self,
        query_emb: np.ndarray,
        temporal: Optional[TemporalQueryOptions] = None,
        relation_top_k: Optional[int] = None,
    ) -> Tuple[List[RetrievalResult], List[RetrievalResult]]:
        para_top_k = self.config.top_k_paragraphs
        rel_top_k = relation_top_k if relation_top_k is not None else self.config.top_k_relations
        candidate_k = self._mixed_candidate_budget(para_top_k, rel_top_k, temporal)
        candidate_k = self._cap_temporal_scan_k(candidate_k, temporal)
        ids, scores = self.vector_store.search(query_emb, k=candidate_k)

        para_candidates: List[RetrievalResult] = []
        rel_candidates: List[RetrievalResult] = []
        paragraph_map = self.metadata_store.get_paragraphs_by_hashes(ids)
        relation_map = self.metadata_store.get_relations_by_hashes(ids, include_inactive=False)
        relation_time_meta = self._best_supporting_time_meta_batch(list(relation_map.keys()), temporal) if temporal else {}
        seen_para = set()
        seen_rel = set()

        for hash_value, score in zip(ids, scores, strict=False):
            paragraph = paragraph_map.get(hash_value)
            if paragraph is not None and hash_value not in seen_para:
                if temporal and not self._is_temporal_match(paragraph, temporal):
                    continue
                seen_para.add(hash_value)
                para_candidates.append(
                    RetrievalResult(
                        hash_value=hash_value,
                        content=paragraph["content"],
                        score=float(score),
                        result_type="paragraph",
                        source="paragraph_search",
                        metadata={
                            "word_count": paragraph.get("word_count", 0),
                            "time_meta": self._build_time_meta_from_paragraph(
                                paragraph,
                                temporal=temporal,
                            ),
                        },
                    )
                )
                continue

            relation = relation_map.get(hash_value)
            if relation is None or hash_value in seen_rel:
                continue
            item_time_meta = None
            if temporal:
                item_time_meta = relation_time_meta.get(hash_value)
                if item_time_meta is None:
                    continue

            seen_rel.add(hash_value)
            rel_candidates.append(
                RetrievalResult(
                    hash_value=hash_value,
                    content=f"{relation['subject']} {relation['predicate']} {relation['object']}",
                    score=float(score),
                    result_type="relation",
                    source="relation_search",
                    metadata={
                        "subject": relation["subject"],
                        "predicate": relation["predicate"],
                        "object": relation["object"],
                        "confidence": relation.get("confidence", 1.0),
                        "time_meta": item_time_meta,
                    },
                )
            )

        para_results = self._sort_results_with_temporal(para_candidates, temporal) if temporal else para_candidates
        rel_results = self._sort_results_with_temporal(rel_candidates, temporal) if temporal else rel_candidates

        # 双重方案里，向量主干优先解决“召回不够”，因此主检索走共享候选池，
        # 但再补一层按类型回填，避免 paragraph / relation 任一侧被饿死。
        para_backfill = self._search_paragraphs(query_emb, para_top_k, temporal)
        rel_backfill = self._search_relations(query_emb, rel_top_k, temporal)
        para_results = self._merge_backfilled_results(
            primary_results=para_results,
            backfill_results=para_backfill,
            top_k=para_top_k,
        )
        rel_results = self._merge_backfilled_results(
            primary_results=rel_results,
            backfill_results=rel_backfill,
            top_k=rel_top_k,
        )
        return para_results, rel_results

    def _search_paragraphs(
        self,
        query_emb: np.ndarray,
        top_k: int,
        temporal: Optional[TemporalQueryOptions] = None,
    ) -> List[RetrievalResult]:
        """
        搜索段落

        Args:
            query_emb: 查询嵌入
            top_k: 返回数量

        Returns:
            段落结果列表
        """
        multiplier = max(1, temporal.candidate_multiplier) if temporal else 1
        candidate_k = self._cap_temporal_scan_k(top_k * multiplier, temporal)
        para_ids, para_scores = self.vector_store.search(query_emb, k=candidate_k)

        return self._build_paragraph_results_from_ids(
            list(para_ids),
            [float(score) for score in para_scores],
            source="paragraph_search",
            temporal=temporal,
        )

    def _search_relations(
        self,
        query_emb: np.ndarray,
        top_k: int,
        temporal: Optional[TemporalQueryOptions] = None,
    ) -> List[RetrievalResult]:
        """
        搜索关系

        Args:
            query_emb: 查询嵌入
            top_k: 返回数量

        Returns:
            关系结果列表
        """
        multiplier = max(1, temporal.candidate_multiplier) if temporal else 1
        candidate_k = self._cap_temporal_scan_k(top_k * multiplier, temporal)
        rel_ids, rel_scores = self.vector_store.search(query_emb, k=candidate_k)

        return self._build_relation_results_from_ids(
            list(rel_ids),
            [float(score) for score in rel_scores],
            source="relation_search",
            temporal=temporal,
        )

    def _fuse_results(
        self,
        para_results: List[RetrievalResult],
        rel_results: List[RetrievalResult],
        query_emb: Optional[np.ndarray] = None,
        alpha_override: Optional[float] = None,
        preserve_top_relations: int = 0,
    ) -> List[RetrievalResult]:
        """
        融合段落和关系结果

        融合策略：
        1. 计算加权分数
        2. 去重（基于段落和关系的关联）
        3. 排序

        Args:
            para_results: 段落结果
            rel_results: 关系结果
            query_emb: 查询嵌入（兼容参数，当前未使用）

        Returns:
            融合后的结果列表
        """
        del query_emb  # 参数保留用于兼容
        alpha = float(alpha_override) if alpha_override is not None else self.config.alpha

        # 为段落结果计算加权分数
        for result in para_results:
            result.score = result.score * alpha
            result.source = "fusion"

        # 为关系结果计算加权分数
        for result in rel_results:
            result.score = result.score * (1 - alpha)
            result.source = "fusion"

        preserve_top_relations = max(0, int(preserve_top_relations))
        preserved_relation_hashes = set()
        if preserve_top_relations > 0 and rel_results:
            rel_ranked = sorted(rel_results, key=lambda x: x.score, reverse=True)
            preserved_relation_hashes = {
                item.hash_value for item in rel_ranked[:preserve_top_relations]
            }

        # 合并结果
        all_results = para_results + rel_results
        all_results.sort(key=lambda x: x.score, reverse=True)

        # 去重：如果段落有关联的关系，只保留分数更高的
        seen_paragraphs = set()
        seen_items = set()
        deduplicated_results = []
        relation_support_hashes = self.metadata_store.get_paragraph_hashes_by_relation_hashes(
            [item.hash_value for item in all_results if item.result_type == "relation"]
        )

        for result in all_results:
            if result.hash_value in seen_items:
                continue
            if result.result_type == "paragraph":
                hash_val = result.hash_value
                if hash_val not in seen_paragraphs:
                    seen_paragraphs.add(hash_val)
                    seen_items.add(hash_val)
                    deduplicated_results.append(result)
            else:  # 关系结果（relation）
                if result.hash_value in preserved_relation_hashes:
                    seen_items.add(result.hash_value)
                    deduplicated_results.append(result)
                    continue
                # 检查关系关联的段落是否已存在
                support_hashes = relation_support_hashes.get(result.hash_value, [])
                if support_hashes and any(paragraph_hash in seen_paragraphs for paragraph_hash in support_hashes):
                    continue
                else:
                    seen_items.add(result.hash_value)
                    deduplicated_results.append(result)

        # 按分数排序
        deduplicated_results.sort(key=lambda x: x.score, reverse=True)

        return deduplicated_results

    def _apply_relation_intent_pair_rerank(
        self,
        results: List[RetrievalResult],
        *,
        enabled: bool,
        pair_rerank_enabled: bool,
        pair_limit: int,
    ) -> List[RetrievalResult]:
        """仅在 relation-intent 下对关系项执行同主客体多谓词重排。"""
        if not enabled or not pair_rerank_enabled:
            return results
        return self._rerank_relation_items_by_pair(results, pair_limit=pair_limit)

    def _rerank_relation_items_by_pair(
        self,
        results: List[RetrievalResult],
        pair_limit: int,
    ) -> List[RetrievalResult]:
        """
        同主客体多谓词重排：
        1. 关系项按 (subject, object) 分组
        2. 组内按分数降序 + 原始位置升序
        3. 组间按组最高分降序 + 组最早位置升序
        4. 先拼接每组前 N 条，再拼接每组 overflow 条目
        5. 回填到原关系槽位，段落槽位不变
        """
        if len(results) <= 1:
            return results

        relation_positions: List[int] = []
        relation_items: List[Tuple[int, RetrievalResult]] = []
        for idx, item in enumerate(results):
            if item.result_type == "relation":
                relation_positions.append(idx)
                relation_items.append((idx, item))

        if len(relation_items) <= 1:
            return results

        pair_limit = max(1, int(pair_limit))

        grouped: Dict[Tuple[str, str], List[Tuple[int, RetrievalResult]]] = {}
        for original_idx, item in relation_items:
            metadata = item.metadata if isinstance(item.metadata, dict) else {}
            subject = str(metadata.get("subject", "")).strip().lower()
            obj = str(metadata.get("object", "")).strip().lower()
            if subject and obj:
                key = (subject, obj)
            else:
                key = ("__missing__", item.hash_value)
            grouped.setdefault(key, []).append((original_idx, item))

        for grouped_items in grouped.values():
            grouped_items.sort(key=lambda x: (-float(x[1].score), x[0]))

        ordered_groups = sorted(
            grouped.values(),
            key=lambda grouped_items: (
                -float(grouped_items[0][1].score),
                grouped_items[0][0],
            ),
        )

        prioritized: List[RetrievalResult] = []
        overflow: List[RetrievalResult] = []
        for grouped_items in ordered_groups:
            prioritized.extend([item for _, item in grouped_items[:pair_limit]])
            overflow.extend([item for _, item in grouped_items[pair_limit:]])

        reordered_relations = prioritized + overflow
        if len(reordered_relations) != len(relation_items):
            return results

        logger.debug(
            "relation_rerank_applied=1 "
            f"relation_pair_groups={len(ordered_groups)} "
            f"relation_pair_overflow_count={len(overflow)} "
            f"relation_pair_limit={pair_limit}"
        )

        rebuilt = list(results)
        for slot_idx, relation_item in zip(relation_positions, reordered_relations, strict=False):
            rebuilt[slot_idx] = relation_item
        return rebuilt

    async def _rerank_with_ppr(
        self,
        results: List[RetrievalResult],
        query: str,
    ) -> List[RetrievalResult]:
        """
        使用PageRank重排序结果 (异步 + 线程池)

        Args:
            results: 检索结果
            query: 查询文本

        Returns:
            重排序后的结果
        """
        # 从查询中提取实体
        entities = self._extract_entities(query)

        if not entities:
            logger.debug("未识别到实体，跳过PPR重排序")
            return results

        # 计算PPR分数 (带短缓存；未命中时放入线程池，避免阻塞主循环)
        ppr_timeout_s = max(0.1, float(getattr(self.config, "ppr_timeout_seconds", 1.5) or 1.5))
        try:
            ppr_scores = await self._get_cached_ppr_scores(entities, timeout_s=ppr_timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                "metric.ppr_timeout_skip_count=1 "
                f"timeout_s={ppr_timeout_s} "
                f"entities={len(entities)}"
            )
            return results
        except Exception as e:
            logger.warning(f"PPR 重排序失败，回退原排序: {e}")
            return results

        # 调整结果分数
        ppr_scores_by_name = {
            str(name).strip().lower(): float(score)
            for name, score in ppr_scores.items()
        }
        paragraph_entity_map = self.metadata_store.get_paragraph_entities_by_hashes(
            [result.hash_value for result in results if result.result_type == "paragraph"]
        )
        for result in results:
            if result.result_type == "paragraph":
                # 获取段落的实体
                para_entities = paragraph_entity_map.get(result.hash_value, [])

                # 计算实体的平均PPR分数
                if para_entities:
                    entity_scores = []
                    for ent in para_entities:
                        ent_name = str(ent.get("name", "")).strip().lower()
                        if ent_name in ppr_scores_by_name:
                            entity_scores.append(ppr_scores_by_name[ent_name])

                    if entity_scores:
                        # 只使用命中的高价值图实体做正向增益，避免把原本高分的正确段落
                        # 因为“实体多但非全部命中”而反向压低。
                        focus_scores = sorted(entity_scores, reverse=True)[:2]
                        ppr_signal = float(np.mean(focus_scores))
                        boost_weight = 0.12 if len(focus_scores) >= 2 else 0.06
                        boost = ppr_signal * boost_weight

                        metadata = result.metadata if isinstance(result.metadata, dict) else {}
                        metadata["ppr_signal"] = round(ppr_signal, 4)
                        metadata["ppr_focus_entity_count"] = len(focus_scores)
                        metadata["ppr_boost"] = round(boost, 4)
                        result.metadata = metadata

                        result.score = float(result.score) + float(boost)

        # 重新排序
        results.sort(key=lambda x: x.score, reverse=True)

        return results

    async def _get_cached_ppr_scores(
        self,
        entities: Dict[str, float],
        *,
        timeout_s: float,
    ) -> Dict[str, float]:
        """按实体集合和图规模缓存 PPR 结果，降低重复查询成本。"""
        now = time.monotonic()
        key = self._build_ppr_cache_key(entities)
        cached = self._ppr_cache.get(key)
        if cached is not None:
            expires_at, scores = cached
            if expires_at > now:
                return scores
            self._ppr_cache.pop(key, None)

        async with self._ppr_semaphore:
            now = time.monotonic()
            cached = self._ppr_cache.get(key)
            if cached is not None:
                expires_at, scores = cached
                if expires_at > now:
                    return scores
                self._ppr_cache.pop(key, None)

            scores = await asyncio.wait_for(
                asyncio.to_thread(self._compute_ppr_scores, entities),
                timeout=timeout_s,
            )
            self._store_ppr_cache_entry(key, scores)
            return scores

    def _compute_ppr_scores(self, entities: Dict[str, float]) -> Dict[str, float]:
        if self._should_use_local_ppr():
            scores = self._compute_local_ppr_scores(entities)
            if scores:
                return scores
        return self._ppr.compute(personalization=entities, normalize=True)

    def _should_use_local_ppr(self) -> bool:
        return bool(
            self.config.ppr_local_enabled
            and int(getattr(self.graph_store, "num_nodes", 0) or 0) >= self.config.ppr_local_min_graph_nodes
        )

    def _resolve_ppr_seed_nodes(self, entities: Dict[str, float]) -> Dict[str, float]:
        seeds: Dict[str, float] = {}
        for name, weight in entities.items():
            node_name = self.graph_store.find_node(str(name), ignore_case=True)
            if not node_name:
                continue
            weight_value = max(0.0, float(weight))
            if weight_value <= 0.0:
                continue
            seeds[node_name] = seeds.get(node_name, 0.0) + weight_value
        return seeds

    def _collect_local_ppr_nodes(self, seeds: Dict[str, float]) -> List[str]:
        max_nodes = int(self.config.ppr_local_max_nodes)
        max_hops = int(self.config.ppr_local_hops)
        ordered_nodes: List[str] = []
        seen: set[str] = set()
        queue: List[Tuple[str, int]] = [(node, 0) for node in seeds]

        while queue and len(seen) < max_nodes:
            node, depth = queue.pop(0)
            if node in seen:
                continue
            seen.add(node)
            ordered_nodes.append(node)
            if depth >= max_hops:
                continue
            neighbors = self.graph_store.get_neighbors(node)
            neighbors.extend(self.graph_store.get_in_neighbors(node))
            for neighbor in neighbors:
                if neighbor not in seen and len(seen) + len(queue) < max_nodes:
                    queue.append((neighbor, depth + 1))
        return ordered_nodes

    def _compute_local_ppr_scores(self, entities: Dict[str, float]) -> Dict[str, float]:
        seeds = self._resolve_ppr_seed_nodes(entities)
        if not seeds:
            return {}

        nodes = self._collect_local_ppr_nodes(seeds)
        if not nodes:
            return {}

        node_set = set(nodes)
        adjacency: Dict[str, List[str]] = {}
        for node in nodes:
            adjacency[node] = [
                neighbor
                for neighbor in self.graph_store.get_neighbors(node)
                if neighbor in node_set
            ]

        total_seed_weight = sum(seeds.get(node, 0.0) for node in nodes)
        if total_seed_weight <= 0.0:
            return {}

        personalization = {
            node: float(seeds.get(node, 0.0)) / total_seed_weight
            for node in nodes
        }
        scores = dict(personalization)
        alpha = float(self.config.ppr_alpha)
        tol = float(self._ppr.config.tol)
        max_iter = int(self._ppr.config.max_iter)
        min_iterations = int(self._ppr.config.min_iterations)

        for iteration in range(max_iter):
            next_scores = {
                node: (1.0 - alpha) * personalization.get(node, 0.0)
                for node in nodes
            }
            dangling_mass = 0.0
            for node, score in scores.items():
                neighbors = adjacency.get(node, [])
                if not neighbors:
                    dangling_mass += score
                    continue
                share = alpha * score / float(len(neighbors))
                for neighbor in neighbors:
                    next_scores[neighbor] = next_scores.get(neighbor, 0.0) + share
            if dangling_mass > 0.0:
                for node in nodes:
                    next_scores[node] = next_scores.get(node, 0.0) + alpha * dangling_mass * personalization.get(node, 0.0)

            diff = sum(abs(next_scores.get(node, 0.0) - scores.get(node, 0.0)) for node in nodes)
            scores = next_scores
            if iteration + 1 >= min_iterations and diff < tol:
                break

        total_score = sum(scores.values())
        if total_score > 0.0:
            scores = {node: score / total_score for node, score in scores.items()}
        return scores

    def _build_ppr_cache_key(self, entities: Dict[str, float]) -> Tuple[Any, ...]:
        entity_key = tuple(
            sorted(
                (
                    str(name).strip().lower(),
                    round(float(weight), 6),
                )
                for name, weight in entities.items()
                if str(name).strip()
            )
        )
        return (
            int(getattr(self.graph_store, "num_nodes", 0) or 0),
            int(getattr(self.graph_store, "num_edges", 0) or 0),
            round(float(self.config.ppr_alpha), 6),
            bool(self.config.ppr_local_enabled),
            int(self.config.ppr_local_max_nodes),
            int(self.config.ppr_local_hops),
            int(self.config.ppr_local_min_graph_nodes),
            entity_key,
        )

    def _store_ppr_cache_entry(self, key: Tuple[Any, ...], scores: Dict[str, float]) -> None:
        if len(self._ppr_cache) >= self._ppr_cache_max_entries:
            oldest_key = min(self._ppr_cache.items(), key=lambda item: item[1][0])[0]
            self._ppr_cache.pop(oldest_key, None)
        self._ppr_cache[key] = (
            time.monotonic() + self._ppr_cache_ttl_seconds,
            dict(scores),
        )

    def _retrieve_temporal_only(
        self,
        temporal: TemporalQueryOptions,
        top_k: int,
    ) -> List[RetrievalResult]:
        """无语义 query 时，直接走时序索引查询。"""
        limit = self._cap_temporal_scan_k(
            top_k * max(1, temporal.candidate_multiplier),
            temporal,
        )
        paragraphs = self.metadata_store.query_paragraphs_temporal(
            start_ts=temporal.time_from,
            end_ts=temporal.time_to,
            person=temporal.person,
            source=temporal.source,
            limit=limit,
            allow_created_fallback=temporal.allow_created_fallback,
        )
        results: List[RetrievalResult] = []
        for para in paragraphs:
            time_meta = self._build_time_meta_from_paragraph(para, temporal=temporal)
            results.append(
                RetrievalResult(
                    hash_value=para["hash"],
                    content=para["content"],
                    score=1.0,
                    result_type="paragraph",
                    source="temporal_scan",
                    metadata={
                        "word_count": para.get("word_count", 0),
                        "time_meta": time_meta,
                    },
                )
            )

        results = self._sort_results_with_temporal(results, temporal)
        return results[:top_k]

    def _extract_effective_time(
        self,
        paragraph: Dict[str, Any],
        temporal: Optional[TemporalQueryOptions] = None,
    ) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        """提取段落有效时间区间与命中依据。"""
        event_time = paragraph.get("event_time")
        event_start = paragraph.get("event_time_start")
        event_end = paragraph.get("event_time_end")

        if event_start is not None or event_end is not None:
            effective_start = event_start if event_start is not None else (
                event_time if event_time is not None else event_end
            )
            effective_end = event_end if event_end is not None else (
                event_time if event_time is not None else event_start
            )
            return effective_start, effective_end, "event_time_range"

        if event_time is not None:
            return event_time, event_time, "event_time"

        allow_fallback = True
        if temporal is not None:
            allow_fallback = temporal.allow_created_fallback

        created_at = paragraph.get("created_at")
        if allow_fallback and created_at is not None:
            return created_at, created_at, "created_at_fallback"

        return None, None, None

    def _build_time_meta_from_paragraph(
        self,
        paragraph: Dict[str, Any],
        temporal: Optional[TemporalQueryOptions] = None,
    ) -> Dict[str, Any]:
        """构建统一 time_meta 结构。"""
        effective_start, effective_end, match_basis = self._extract_effective_time(
            paragraph,
            temporal=temporal,
        )
        return {
            "event_time": paragraph.get("event_time"),
            "event_time_start": paragraph.get("event_time_start"),
            "event_time_end": paragraph.get("event_time_end"),
            "ingest_time": paragraph.get("created_at"),
            "time_granularity": paragraph.get("time_granularity"),
            "time_confidence": paragraph.get("time_confidence", 1.0),
            "effective_start": effective_start,
            "effective_end": effective_end,
            "effective_start_text": format_timestamp(effective_start),
            "effective_end_text": format_timestamp(effective_end),
            "match_basis": match_basis or "none",
        }

    def _matches_person_filter(self, paragraph_hash: str, person: Optional[str]) -> bool:
        if not person:
            return True
        target = person.strip().lower()
        if not target:
            return True
        para_entities = self.metadata_store.get_paragraph_entities(paragraph_hash)
        for ent in para_entities:
            name = str(ent.get("name", "")).strip().lower()
            if target in name:
                return True
        return False

    def _is_temporal_match(
        self,
        paragraph: Dict[str, Any],
        temporal: TemporalQueryOptions,
    ) -> bool:
        """判断段落是否命中时序筛选。"""
        if temporal.source and paragraph.get("source") != temporal.source:
            return False

        if not self._matches_person_filter(paragraph.get("hash", ""), temporal.person):
            return False

        effective_start, effective_end, _ = self._extract_effective_time(paragraph, temporal=temporal)
        if effective_start is None or effective_end is None:
            return False

        if temporal.time_from is not None and temporal.time_to is not None:
            return effective_end >= temporal.time_from and effective_start <= temporal.time_to
        if temporal.time_from is not None:
            return effective_end >= temporal.time_from
        if temporal.time_to is not None:
            return effective_start <= temporal.time_to
        return True

    def _apply_temporal_filter_to_paragraphs(
        self,
        results: List[RetrievalResult],
        temporal: Optional[TemporalQueryOptions],
    ) -> List[RetrievalResult]:
        if not temporal:
            return results

        filtered: List[RetrievalResult] = []
        for result in results:
            paragraph = self.metadata_store.get_paragraph(result.hash_value)
            if not paragraph:
                continue
            if not self._is_temporal_match(paragraph, temporal):
                continue
            result.metadata["time_meta"] = self._build_time_meta_from_paragraph(paragraph, temporal=temporal)
            filtered.append(result)

        return self._sort_results_with_temporal(filtered, temporal)

    def _best_supporting_time_meta(
        self,
        relation_hash: str,
        temporal: TemporalQueryOptions,
    ) -> Optional[Dict[str, Any]]:
        """获取关系在时序窗口内最优支撑段落的 time_meta。"""
        supports = self.metadata_store.get_paragraphs_by_relation(relation_hash)
        if not supports:
            return None

        best_meta: Optional[Dict[str, Any]] = None
        best_time = float("-inf")
        for para in supports:
            if not self._is_temporal_match(para, temporal):
                continue
            meta = self._build_time_meta_from_paragraph(para, temporal=temporal)
            eff = meta.get("effective_end")
            score = float(eff) if eff is not None else float("-inf")
            if score >= best_time:
                best_time = score
                best_meta = meta

        return best_meta

    def _best_supporting_time_meta_batch(
        self,
        relation_hashes: List[str],
        temporal: Optional[TemporalQueryOptions],
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """批量获取关系在时序窗口内的最优支撑段落 time_meta。"""
        if temporal is None:
            return {}

        grouped = self.metadata_store.get_paragraphs_by_relation_hashes(relation_hashes)
        out: Dict[str, Optional[Dict[str, Any]]] = {}
        for relation_hash, supports in grouped.items():
            best_meta: Optional[Dict[str, Any]] = None
            best_time = float("-inf")
            for para in supports:
                if not self._is_temporal_match(para, temporal):
                    continue
                meta = self._build_time_meta_from_paragraph(para, temporal=temporal)
                eff = meta.get("effective_end")
                score = float(eff) if eff is not None else float("-inf")
                if score >= best_time:
                    best_time = score
                    best_meta = meta
            out[relation_hash] = best_meta
        return out

    def _apply_temporal_filter_to_relations(
        self,
        results: List[RetrievalResult],
        temporal: Optional[TemporalQueryOptions],
    ) -> List[RetrievalResult]:
        if not temporal:
            return results

        missing_hashes = [
            result.hash_value
            for result in results
            if result.metadata.get("time_meta") is None
        ]
        batch_time_meta = self._best_supporting_time_meta_batch(missing_hashes, temporal)
        filtered: List[RetrievalResult] = []
        for result in results:
            meta = result.metadata.get("time_meta")
            if meta is None:
                meta = batch_time_meta.get(result.hash_value)
                if meta is None:
                    continue
                result.metadata["time_meta"] = meta
            filtered.append(result)

        return self._sort_results_with_temporal(filtered, temporal)

    def _sort_results_with_temporal(
        self,
        results: List[RetrievalResult],
        temporal: TemporalQueryOptions,
    ) -> List[RetrievalResult]:
        """语义优先，时间次排序（新到旧）。"""
        del temporal  # temporal 保留给未来扩展，目前只使用结果内 time_meta

        def _temporal_key(item: RetrievalResult) -> float:
            time_meta = item.metadata.get("time_meta", {})
            effective = time_meta.get("effective_end")
            if effective is None:
                effective = time_meta.get("effective_start")
            if effective is None:
                return float("-inf")
            return float(effective)

        results.sort(key=lambda x: (x.score, _temporal_key(x)), reverse=True)
        return results

    def _extract_entities(self, text: str) -> Dict[str, float]:
        """
        从文本中提取实体（简化版本）

        Args:
            text: 输入文本

        Returns:
            实体字典 {实体名: 权重}
        """
        # 获取所有实体
        all_entities = self.graph_store.get_nodes()
        if not all_entities:
            return {}

        # 检查是否需要更新 Aho-Corasick 匹配器
        if self._ac_matcher is None or self._ac_nodes_count != len(all_entities):
            self._ac_matcher = AhoCorasick()
            for entity in all_entities:
                self._ac_matcher.add_pattern(entity.lower())
            self._ac_matcher.build()
            self._ac_nodes_count = len(all_entities)
            self._ac_node_map = {node.lower(): node for node in all_entities}

        # 执行匹配
        text_lower = text.lower()
        stats = self._ac_matcher.find_all(text_lower)

        # 映射回原始名称并使用出现次数作为权重
        entities = {
            self._ac_node_map[low_name]: float(count)
            for low_name, count in stats.items()
            if low_name in self._ac_node_map
        }

        return entities

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取检索统计信息

        Returns:
            统计信息字典
        """
        vector_size = getattr(self.vector_store, "size", None)
        if vector_size is None:
            vector_size = getattr(self.vector_store, "num_vectors", 0)

        return {
            "config": {
                "top_k_paragraphs": self.config.top_k_paragraphs,
                "top_k_relations": self.config.top_k_relations,
                "top_k_final": self.config.top_k_final,
                "alpha": self.config.alpha,
                "enable_ppr": self.config.enable_ppr,
                "ppr_local_enabled": self.config.ppr_local_enabled,
                "ppr_local_max_nodes": self.config.ppr_local_max_nodes,
                "ppr_local_hops": self.config.ppr_local_hops,
                "ppr_local_min_graph_nodes": self.config.ppr_local_min_graph_nodes,
                "enable_parallel": self.config.enable_parallel,
                "strategy": self.config.retrieval_strategy.value,
                "sparse_mode": self.config.sparse.mode,
                "fusion_method": self.config.fusion.method,
                "relation_intent_enabled": self.config.relation_intent.enabled,
                "relation_intent_alpha_override": self.config.relation_intent.alpha_override,
                "relation_intent_candidate_multiplier": self.config.relation_intent.relation_candidate_multiplier,
                "relation_intent_preserve_top_relations": self.config.relation_intent.preserve_top_relations,
                "relation_intent_force_sparse": self.config.relation_intent.force_relation_sparse,
                "relation_intent_pair_rerank_enabled": self.config.relation_intent.pair_predicate_rerank_enabled,
                "relation_intent_pair_predicate_limit": self.config.relation_intent.pair_predicate_limit,
                "graph_recall_enabled": self.config.graph_recall.enabled,
                "graph_recall_candidate_k": self.config.graph_recall.candidate_k,
                "graph_recall_allow_two_hop_pair": self.config.graph_recall.allow_two_hop_pair,
                "graph_recall_max_paths": self.config.graph_recall.max_paths,
            },
            "vector_store": {
                "size": int(vector_size),
            },
            "graph_store": {
                "num_nodes": self.graph_store.num_nodes,
                "num_edges": self.graph_store.num_edges,
            },
            "metadata_store": self.metadata_store.get_statistics(),
            "sparse": self.sparse_index.stats() if self.sparse_index else None,
        }

    def __repr__(self) -> str:
        return (
            f"DualPathRetriever("
            f"strategy={self.config.retrieval_strategy.value}, "
            f"para_k={self.config.top_k_paragraphs}, "
            f"rel_k={self.config.top_k_relations})"
        )
