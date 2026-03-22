"""Shared runtime initializer for Action/Tool/Command retrieval components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.common.logger import get_logger

from ..retrieval import (
    DualPathRetriever,
    DualPathRetrieverConfig,
    DynamicThresholdFilter,
    FusionConfig,
    GraphRelationRecallConfig,
    RelationIntentConfig,
    RetrievalStrategy,
    SparseBM25Config,
    ThresholdConfig,
    ThresholdMethod,
)

_logger = get_logger("A_Memorix.SearchRuntimeInitializer")

_REQUIRED_COMPONENT_KEYS = (
    "vector_store",
    "graph_store",
    "metadata_store",
    "embedding_manager",
)


def _get_config_value(config: Optional[dict], key: str, default: Any = None) -> Any:
    if not isinstance(config, dict):
        return default
    current: Any = config
    for part in key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _resolve_debug_enabled(plugin_config: Optional[dict]) -> bool:
    advanced = _get_config_value(plugin_config, "advanced", {})
    if isinstance(advanced, dict):
        return bool(advanced.get("debug", False))
    return bool(_get_config_value(plugin_config, "debug", False))


@dataclass
class SearchRuntimeBundle:
    """Resolved runtime components and initialized retriever/filter."""

    vector_store: Optional[Any] = None
    graph_store: Optional[Any] = None
    metadata_store: Optional[Any] = None
    embedding_manager: Optional[Any] = None
    sparse_index: Optional[Any] = None
    retriever: Optional[DualPathRetriever] = None
    threshold_filter: Optional[DynamicThresholdFilter] = None
    error: str = ""

    @property
    def ready(self) -> bool:
        return (
            self.retriever is not None
            and self.vector_store is not None
            and self.graph_store is not None
            and self.metadata_store is not None
            and self.embedding_manager is not None
        )


def _resolve_runtime_components(plugin_config: Optional[dict]) -> SearchRuntimeBundle:
    bundle = SearchRuntimeBundle(
        vector_store=_get_config_value(plugin_config, "vector_store"),
        graph_store=_get_config_value(plugin_config, "graph_store"),
        metadata_store=_get_config_value(plugin_config, "metadata_store"),
        embedding_manager=_get_config_value(plugin_config, "embedding_manager"),
        sparse_index=_get_config_value(plugin_config, "sparse_index"),
    )

    missing_required = any(
        getattr(bundle, key) is None for key in _REQUIRED_COMPONENT_KEYS
    )
    if not missing_required:
        return bundle

    try:
        from ...plugin import AMemorixPlugin

        instances = AMemorixPlugin.get_storage_instances()
    except Exception:
        instances = {}

    if not isinstance(instances, dict) or not instances:
        return bundle

    if bundle.vector_store is None:
        bundle.vector_store = instances.get("vector_store")
    if bundle.graph_store is None:
        bundle.graph_store = instances.get("graph_store")
    if bundle.metadata_store is None:
        bundle.metadata_store = instances.get("metadata_store")
    if bundle.embedding_manager is None:
        bundle.embedding_manager = instances.get("embedding_manager")
    if bundle.sparse_index is None:
        bundle.sparse_index = instances.get("sparse_index")
    return bundle


def build_search_runtime(
    plugin_config: Optional[dict],
    logger_obj: Optional[Any],
    owner_tag: str,
    *,
    log_prefix: str = "",
) -> SearchRuntimeBundle:
    """Build retriever + threshold filter with unified fallback/config parsing."""

    log = logger_obj or _logger
    owner = str(owner_tag or "runtime").strip().lower() or "runtime"
    prefix = str(log_prefix or "").strip()
    prefix_text = f"{prefix} " if prefix else ""

    runtime = _resolve_runtime_components(plugin_config)
    if any(getattr(runtime, key) is None for key in _REQUIRED_COMPONENT_KEYS):
        runtime.error = "存储组件未完全初始化"
        log.warning(f"{prefix_text}[{owner}] 存储组件未完全初始化，无法使用检索功能")
        return runtime

    sparse_cfg_raw = _safe_dict(_get_config_value(plugin_config, "retrieval.sparse", {}) or {})
    fusion_cfg_raw = _safe_dict(_get_config_value(plugin_config, "retrieval.fusion", {}) or {})
    relation_intent_cfg_raw = _safe_dict(
        _get_config_value(plugin_config, "retrieval.search.relation_intent", {}) or {}
    )
    graph_recall_cfg_raw = _safe_dict(
        _get_config_value(plugin_config, "retrieval.search.graph_recall", {}) or {}
    )

    try:
        sparse_cfg = SparseBM25Config(**sparse_cfg_raw)
    except Exception as e:
        log.warning(f"{prefix_text}[{owner}] sparse 配置非法，回退默认: {e}")
        sparse_cfg = SparseBM25Config()

    try:
        fusion_cfg = FusionConfig(**fusion_cfg_raw)
    except Exception as e:
        log.warning(f"{prefix_text}[{owner}] fusion 配置非法，回退默认: {e}")
        fusion_cfg = FusionConfig()

    try:
        relation_intent_cfg = RelationIntentConfig(**relation_intent_cfg_raw)
    except Exception as e:
        log.warning(f"{prefix_text}[{owner}] relation_intent 配置非法，回退默认: {e}")
        relation_intent_cfg = RelationIntentConfig()

    try:
        graph_recall_cfg = GraphRelationRecallConfig(**graph_recall_cfg_raw)
    except Exception as e:
        log.warning(f"{prefix_text}[{owner}] graph_recall 配置非法，回退默认: {e}")
        graph_recall_cfg = GraphRelationRecallConfig()

    try:
        config = DualPathRetrieverConfig(
            top_k_paragraphs=_get_config_value(plugin_config, "retrieval.top_k_paragraphs", 20),
            top_k_relations=_get_config_value(plugin_config, "retrieval.top_k_relations", 10),
            top_k_final=_get_config_value(plugin_config, "retrieval.top_k_final", 10),
            alpha=_get_config_value(plugin_config, "retrieval.alpha", 0.5),
            enable_ppr=_get_config_value(plugin_config, "retrieval.enable_ppr", True),
            ppr_alpha=_get_config_value(plugin_config, "retrieval.ppr_alpha", 0.85),
            ppr_timeout_seconds=_get_config_value(
                plugin_config, "retrieval.ppr_timeout_seconds", 1.5
            ),
            ppr_concurrency_limit=_get_config_value(
                plugin_config, "retrieval.ppr_concurrency_limit", 4
            ),
            enable_parallel=_get_config_value(plugin_config, "retrieval.enable_parallel", True),
            retrieval_strategy=RetrievalStrategy.DUAL_PATH,
            debug=_resolve_debug_enabled(plugin_config),
            sparse=sparse_cfg,
            fusion=fusion_cfg,
            relation_intent=relation_intent_cfg,
            graph_recall=graph_recall_cfg,
        )

        runtime.retriever = DualPathRetriever(
            vector_store=runtime.vector_store,
            graph_store=runtime.graph_store,
            metadata_store=runtime.metadata_store,
            embedding_manager=runtime.embedding_manager,
            sparse_index=runtime.sparse_index,
            config=config,
        )

        threshold_config = ThresholdConfig(
            method=ThresholdMethod.ADAPTIVE,
            min_threshold=_get_config_value(plugin_config, "threshold.min_threshold", 0.3),
            max_threshold=_get_config_value(plugin_config, "threshold.max_threshold", 0.95),
            percentile=_get_config_value(plugin_config, "threshold.percentile", 75.0),
            std_multiplier=_get_config_value(plugin_config, "threshold.std_multiplier", 1.5),
            min_results=_get_config_value(plugin_config, "threshold.min_results", 3),
            enable_auto_adjust=_get_config_value(plugin_config, "threshold.enable_auto_adjust", True),
        )
        runtime.threshold_filter = DynamicThresholdFilter(threshold_config)
        runtime.error = ""
        log.info(f"{prefix_text}[{owner}] 检索运行时初始化完成")
    except Exception as e:
        runtime.retriever = None
        runtime.threshold_filter = None
        runtime.error = str(e)
        log.error(f"{prefix_text}[{owner}] 检索运行时初始化失败: {e}")

    return runtime


class SearchRuntimeInitializer:
    """Compatibility wrapper around the function style initializer."""

    @staticmethod
    def build_search_runtime(
        plugin_config: Optional[dict],
        logger_obj: Optional[Any],
        owner_tag: str,
        *,
        log_prefix: str = "",
    ) -> SearchRuntimeBundle:
        return build_search_runtime(
            plugin_config=plugin_config,
            logger_obj=logger_obj,
            owner_tag=owner_tag,
            log_prefix=log_prefix,
        )
