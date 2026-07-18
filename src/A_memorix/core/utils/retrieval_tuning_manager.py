"""
WebUI 检索调优管理器（Retrieval tuning manager）。
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import random
import re
import time
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.common.logger import get_logger

from ...paths import artifacts_root
from ..runtime.search_runtime_initializer import build_search_runtime
from .model_routing import (
    ResolvedLLMModel,
    generate_with_resolved_model,
    get_text_generation_model_tasks,
    pick_text_generation_task,
    resolve_text_generation_model_selector,
)
from .search_execution_service import SearchExecutionRequest, SearchExecutionService

try:
    from src.services import llm_service as llm_api
except Exception:  # pragma: no cover
    llm_api = None

logger = get_logger("A_Memorix.RetrievalTuningManager")


OBJECTIVES = {"precision_priority", "balanced", "recall_priority"}
INTENSITIES = {"quick": 8, "standard": 20, "deep": 32}
CATEGORIES = {"query_nl", "query_kw", "spo_relation", "spo_search"}
MIN_VALIDATION_SCORE_DELTA = 0.02
MAX_PRECISION_DROP = 0.01
MAX_RECALL_DROP = 0.01
MAX_EMPTY_RATE_INCREASE = 0.05
MAX_LATENCY_INCREASE_MS = 100.0
MAX_LATENCY_INCREASE_RATIO = 0.50
NON_TUNED_RETRIEVAL_INFLUENCERS = [
    "embedding.*",
    "retrieval.sparse.tokenizer_mode",
    "retrieval.relation_vectorization.*",
    "retrieval.search.relation_intent.*",
    "retrieval.search.graph_recall.*",
    "retrieval.search.posterior_graph.*",
    "retrieval.aggregate.*",
    "filter.*",
    "episode.*",
    "person_profile.*",
    "integration.memory_query_default_limit",
    "integration.heuristic_memory_*",
]
_RUNTIME_CONFIG_INSTANCE_KEYS = {
    "vector_store",
    "graph_store",
    "metadata_store",
    "embedding_manager",
    "sparse_index",
    "relation_write_service",
    "plugin_instance",
}


def _now() -> float:
    return time.time()


def _clamp_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(min_value, min(max_value, parsed))


def _clamp_float(value: Any, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    return max(min_value, min(max_value, parsed))


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _nested_get(data: Dict[str, Any], key: str, default: Any = None) -> Any:
    cur: Any = data
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _nested_set(data: Dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    cur = data
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _safe_json_loads(text: str) -> Optional[Any]:
    raw = str(text or "").strip()
    if not raw:
        return None
    if "```" in raw:
        raw = raw.replace("```json", "```")
        for seg in raw.split("```"):
            seg = seg.strip()
            if seg.startswith("{") or seg.startswith("["):
                raw = seg
                break
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("检索调优响应不是完整 JSON，继续提取对象片段")
    s = raw.find("{")
    e = raw.rfind("}")
    if s >= 0 and e > s:
        try:
            return json.loads(raw[s : e + 1])
        except json.JSONDecodeError:
            return None
    return None


@dataclass
class RetrievalQueryCase:
    case_id: str
    category: str
    query: str
    expected_hashes: List[str] = field(default_factory=list)
    expected_spo: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "query": self.query,
            "expected_hashes": list(self.expected_hashes),
            "expected_spo": dict(self.expected_spo),
            "metadata": dict(self.metadata),
        }


@dataclass
class RetrievalTuningRoundRecord:
    round_index: int
    candidate_profile: Dict[str, Any]
    metrics: Dict[str, Any]
    score: float
    latency_ms: float
    failure_summary: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_index": self.round_index,
            "candidate_profile": copy.deepcopy(self.candidate_profile),
            "metrics": copy.deepcopy(self.metrics),
            "score": float(self.score),
            "latency_ms": float(self.latency_ms),
            "failure_summary": copy.deepcopy(self.failure_summary),
            "created_at": float(self.created_at),
        }


@dataclass
class RetrievalTuningTaskRecord:
    task_id: str
    status: str
    progress: float
    objective: str
    intensity: str
    rounds_total: int
    rounds_done: int = 0
    best_profile: Dict[str, Any] = field(default_factory=dict)
    best_metrics: Dict[str, Any] = field(default_factory=dict)
    best_score: float = -1.0
    baseline_profile: Dict[str, Any] = field(default_factory=dict)
    baseline_metrics: Dict[str, Any] = field(default_factory=dict)
    validation_summary: Dict[str, Any] = field(default_factory=dict)
    recommended: bool = False
    error: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    query_set_stats: Dict[str, Any] = field(default_factory=dict)
    artifact_paths: Dict[str, str] = field(default_factory=dict)
    rounds: List[RetrievalTuningRoundRecord] = field(default_factory=list)
    cancel_requested: bool = False
    created_at: float = field(default_factory=_now)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    updated_at: float = field(default_factory=_now)
    apply_log: List[Dict[str, Any]] = field(default_factory=list)

    def to_summary(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "progress": self.progress,
            "objective": self.objective,
            "intensity": self.intensity,
            "rounds_total": self.rounds_total,
            "rounds_done": self.rounds_done,
            "best_score": self.best_score,
            "recommended": bool(self.recommended),
            "validation_summary": copy.deepcopy(self.validation_summary),
            "error": self.error,
            "query_set_stats": dict(self.query_set_stats),
            "artifact_paths": dict(self.artifact_paths),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "updated_at": self.updated_at,
        }

    def to_detail(self, include_rounds: bool = False) -> Dict[str, Any]:
        payload = self.to_summary()
        payload.update(
            {
                "params": copy.deepcopy(self.params),
                "best_profile": copy.deepcopy(self.best_profile),
                "best_metrics": copy.deepcopy(self.best_metrics),
                "baseline_profile": copy.deepcopy(self.baseline_profile),
                "baseline_metrics": copy.deepcopy(self.baseline_metrics),
                "recommended": bool(self.recommended),
                "validation_summary": copy.deepcopy(self.validation_summary),
                "apply_log": copy.deepcopy(self.apply_log),
            }
        )
        if include_rounds:
            payload["rounds"] = [x.to_dict() for x in self.rounds]
        return payload


class RetrievalTuningManager:
    def __init__(
        self,
        plugin: Any,
        *,
        import_write_blocked_provider: Optional[Callable[[], bool]] = None,
    ):
        self.plugin = plugin
        self._import_write_blocked_provider = import_write_blocked_provider

        self._lock = asyncio.Lock()
        self._tasks: Dict[str, RetrievalTuningTaskRecord] = {}
        self._task_order: deque[str] = deque()
        self._queue: deque[str] = deque()
        self._active_task_id: Optional[str] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._stopping = False

        self._rollback_snapshot: Optional[Dict[str, Any]] = None

        self._artifacts_root = artifacts_root() / "retrieval_tuning"
        self._artifacts_root.mkdir(parents=True, exist_ok=True)

    def _cfg(self, key: str, default: Any = None) -> Any:
        getter = getattr(self.plugin, "get_config", None)
        if callable(getter):
            return getter(key, default)
        return default

    def _is_enabled(self) -> bool:
        return bool(self._cfg("web.tuning.enabled", True))

    def _queue_limit(self) -> int:
        return _clamp_int(self._cfg("web.tuning.max_queue_size", 8), 8, 1, 100)

    def _poll_interval_s(self) -> float:
        ms = _clamp_int(self._cfg("web.tuning.poll_interval_ms", 1200), 1200, 200, 60000)
        return max(0.2, ms / 1000.0)

    def _llm_retry_cfg(self) -> Dict[str, Any]:
        return {
            "max_attempts": _clamp_int(self._cfg("web.tuning.llm_retry.max_attempts", 3), 3, 1, 10),
            "min_wait_seconds": _clamp_float(self._cfg("web.tuning.llm_retry.min_wait_seconds", 2), 2.0, 0.1, 60.0),
            "max_wait_seconds": _clamp_float(self._cfg("web.tuning.llm_retry.max_wait_seconds", 20), 20.0, 0.2, 120.0),
            "backoff_multiplier": _clamp_float(self._cfg("web.tuning.llm_retry.backoff_multiplier", 2), 2.0, 1.0, 10.0),
        }

    def _eval_query_timeout_s(self) -> float:
        return _clamp_float(
            self._cfg("web.tuning.eval_query_timeout_seconds", 10.0),
            10.0,
            0.01,
            120.0,
        )

    def get_runtime_settings(self) -> Dict[str, Any]:
        intensity = str(self._cfg("web.tuning.default_intensity", "standard") or "standard")
        if intensity not in INTENSITIES:
            intensity = "standard"
        objective = str(self._cfg("web.tuning.default_objective", "precision_priority") or "precision_priority")
        if objective not in OBJECTIVES:
            objective = "precision_priority"
        return {
            "enabled": self._is_enabled(),
            "poll_interval_ms": _clamp_int(self._cfg("web.tuning.poll_interval_ms", 1200), 1200, 200, 60000),
            "max_queue_size": self._queue_limit(),
            "default_objective": objective,
            "default_intensity": intensity,
            "default_rounds": INTENSITIES[intensity],
            "default_top_k_eval": _clamp_int(self._cfg("web.tuning.default_top_k_eval", 20), 20, 5, 100),
            "default_sample_size": _clamp_int(self._cfg("web.tuning.default_sample_size", 24), 24, 4, 200),
            "eval_query_timeout_seconds": self._eval_query_timeout_s(),
            "llm_retry": self._llm_retry_cfg(),
        }

    def _ensure_ready(self) -> None:
        required = ("metadata_store", "vector_store", "graph_store", "embedding_manager")
        missing = [x for x in required if getattr(self.plugin, x, None) is None]
        if missing:
            raise ValueError(f"调优依赖未初始化: {', '.join(missing)}")
        checker = getattr(self.plugin, "is_runtime_ready", None)
        if callable(checker) and not checker():
            raise ValueError("插件运行时未就绪")
        provider = self._import_write_blocked_provider
        if provider is not None and bool(provider()):
            raise ValueError("导入任务运行中，当前禁止启动检索调优")

    def get_profile_snapshot(self) -> Dict[str, Any]:
        cfg = getattr(self.plugin, "config", {}) or {}
        profile = {
            "retrieval": {
                "top_k_paragraphs": _nested_get(cfg, "retrieval.top_k_paragraphs", 20),
                "top_k_relations": _nested_get(cfg, "retrieval.top_k_relations", 10),
                "top_k_final": _nested_get(cfg, "retrieval.top_k_final", 10),
                "alpha": _nested_get(cfg, "retrieval.alpha", 0.5),
                "enable_ppr": _nested_get(cfg, "retrieval.enable_ppr", True),
                "ppr_alpha": _nested_get(cfg, "retrieval.ppr_alpha", 0.85),
                "ppr_timeout_seconds": _nested_get(cfg, "retrieval.ppr_timeout_seconds", 1.5),
                "search": {
                    "smart_fallback": {"enabled": _nested_get(cfg, "retrieval.search.smart_fallback.enabled", True)}
                },
                "sparse": {
                    "enabled": _nested_get(cfg, "retrieval.sparse.enabled", True),
                    "mode": _nested_get(cfg, "retrieval.sparse.mode", "auto"),
                    "candidate_k": _nested_get(cfg, "retrieval.sparse.candidate_k", 80),
                    "relation_candidate_k": _nested_get(cfg, "retrieval.sparse.relation_candidate_k", 60),
                },
                "fusion": {
                    "method": _nested_get(cfg, "retrieval.fusion.method", "weighted_rrf"),
                    "rrf_k": _nested_get(cfg, "retrieval.fusion.rrf_k", 60),
                    "vector_weight": _nested_get(cfg, "retrieval.fusion.vector_weight", 0.7),
                    "bm25_weight": _nested_get(cfg, "retrieval.fusion.bm25_weight", 0.3),
                },
                "vector_pools": {
                    "mode": _nested_get(cfg, "retrieval.vector_pools.mode", "single"),
                    "paragraph_top_k": _nested_get(cfg, "retrieval.vector_pools.paragraph_top_k", 20),
                    "graph_top_k": _nested_get(cfg, "retrieval.vector_pools.graph_top_k", 40),
                    "graph_expand_paragraph_k": _nested_get(cfg, "retrieval.vector_pools.graph_expand_paragraph_k", 80),
                    "relation_expand_per_hit": _nested_get(cfg, "retrieval.vector_pools.relation_expand_per_hit", 5),
                    "entity_expand_per_hit": _nested_get(cfg, "retrieval.vector_pools.entity_expand_per_hit", 8),
                    "relation_evidence_weight": _nested_get(
                        cfg, "retrieval.vector_pools.relation_evidence_weight", 1.0
                    ),
                    "entity_evidence_weight": _nested_get(cfg, "retrieval.vector_pools.entity_evidence_weight", 0.55),
                    "semantic_weight": _nested_get(cfg, "retrieval.vector_pools.semantic_weight", 0.65),
                    "sparse_weight": _nested_get(cfg, "retrieval.vector_pools.sparse_weight", 0.2),
                    "graph_weight": _nested_get(cfg, "retrieval.vector_pools.graph_weight", 0.15),
                    "relation_intent": {
                        "graph_top_k": _nested_get(cfg, "retrieval.vector_pools.relation_intent.graph_top_k", 80),
                        "semantic_weight": _nested_get(
                            cfg, "retrieval.vector_pools.relation_intent.semantic_weight", 0.45
                        ),
                        "sparse_weight": _nested_get(cfg, "retrieval.vector_pools.relation_intent.sparse_weight", 0.15),
                        "graph_weight": _nested_get(cfg, "retrieval.vector_pools.relation_intent.graph_weight", 0.4),
                    },
                },
            },
            "threshold": {
                "min_threshold": _nested_get(cfg, "threshold.min_threshold", 0.3),
                "max_threshold": _nested_get(cfg, "threshold.max_threshold", 0.95),
                "percentile": _nested_get(cfg, "threshold.percentile", 75.0),
                "min_results": _nested_get(cfg, "threshold.min_results", 3),
                "enable_auto_adjust": _nested_get(cfg, "threshold.enable_auto_adjust", True),
            },
        }
        return self._normalize_profile(profile, fallback=profile)

    def _normalize_profile(
        self, profile: Optional[Dict[str, Any]], *, fallback: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        raw = copy.deepcopy(profile or {})
        base = copy.deepcopy(fallback or self.get_profile_snapshot())

        def pick(path: str, default: Any) -> Any:
            if _nested_get(raw, path, None) is not None:
                return _nested_get(raw, path, default)
            if path in raw:
                return raw.get(path, default)
            return _nested_get(base, path, default)

        fusion_method = str(pick("retrieval.fusion.method", "weighted_rrf") or "weighted_rrf").strip().lower()
        if fusion_method not in {"weighted_rrf", "alpha_legacy"}:
            fusion_method = "weighted_rrf"

        sparse_mode = str(pick("retrieval.sparse.mode", "auto") or "auto").strip().lower()
        if sparse_mode not in {"auto", "hybrid", "fallback_only"}:
            sparse_mode = "auto"

        vector_pool_mode = str(pick("retrieval.vector_pools.mode", "single") or "single").strip().lower()
        if vector_pool_mode not in {"single", "dual"}:
            vector_pool_mode = "single"

        vec_w = _clamp_float(pick("retrieval.fusion.vector_weight", 0.7), 0.7, 0.0, 1.0)
        bm_w = _clamp_float(pick("retrieval.fusion.bm25_weight", 0.3), 0.3, 0.0, 1.0)
        s = vec_w + bm_w
        if s <= 1e-9:
            vec_w, bm_w = 0.7, 0.3
        else:
            vec_w, bm_w = vec_w / s, bm_w / s

        semantic_w = _clamp_float(pick("retrieval.vector_pools.semantic_weight", 0.65), 0.65, 0.0, 1.0)
        sparse_w = _clamp_float(pick("retrieval.vector_pools.sparse_weight", 0.2), 0.2, 0.0, 1.0)
        graph_w = _clamp_float(pick("retrieval.vector_pools.graph_weight", 0.15), 0.15, 0.0, 1.0)
        weight_sum = semantic_w + sparse_w + graph_w
        if weight_sum <= 1e-9:
            semantic_w, sparse_w, graph_w = 0.65, 0.2, 0.15
        else:
            semantic_w, sparse_w, graph_w = semantic_w / weight_sum, sparse_w / weight_sum, graph_w / weight_sum

        ri_semantic_w = _clamp_float(
            pick("retrieval.vector_pools.relation_intent.semantic_weight", 0.45),
            0.45,
            0.0,
            1.0,
        )
        ri_sparse_w = _clamp_float(
            pick("retrieval.vector_pools.relation_intent.sparse_weight", 0.15),
            0.15,
            0.0,
            1.0,
        )
        ri_graph_w = _clamp_float(
            pick("retrieval.vector_pools.relation_intent.graph_weight", 0.4),
            0.4,
            0.0,
            1.0,
        )
        ri_sum = ri_semantic_w + ri_sparse_w + ri_graph_w
        if ri_sum <= 1e-9:
            ri_semantic_w, ri_sparse_w, ri_graph_w = 0.45, 0.15, 0.4
        else:
            ri_semantic_w, ri_sparse_w, ri_graph_w = (
                ri_semantic_w / ri_sum,
                ri_sparse_w / ri_sum,
                ri_graph_w / ri_sum,
            )

        min_threshold = _clamp_float(pick("threshold.min_threshold", 0.3), 0.3, 0.0, 1.0)
        max_threshold = _clamp_float(pick("threshold.max_threshold", 0.95), 0.95, 0.0, 1.0)
        if min_threshold >= max_threshold:
            min_threshold, max_threshold = 0.3, 0.95

        return {
            "retrieval": {
                "top_k_paragraphs": _clamp_int(pick("retrieval.top_k_paragraphs", 20), 20, 10, 1200),
                "top_k_relations": _clamp_int(pick("retrieval.top_k_relations", 10), 10, 4, 512),
                "top_k_final": _clamp_int(pick("retrieval.top_k_final", 10), 10, 4, 512),
                "alpha": _clamp_float(pick("retrieval.alpha", 0.5), 0.5, 0.0, 1.0),
                "enable_ppr": _coerce_bool(pick("retrieval.enable_ppr", True), True),
                "ppr_alpha": _clamp_float(pick("retrieval.ppr_alpha", 0.85), 0.85, 0.1, 0.99),
                "ppr_timeout_seconds": _clamp_float(pick("retrieval.ppr_timeout_seconds", 1.5), 1.5, 0.1, 10.0),
                "search": {
                    "smart_fallback": {
                        "enabled": _coerce_bool(pick("retrieval.search.smart_fallback.enabled", True), True)
                    }
                },
                "sparse": {
                    "enabled": _coerce_bool(pick("retrieval.sparse.enabled", True), True),
                    "mode": sparse_mode,
                    "candidate_k": _clamp_int(pick("retrieval.sparse.candidate_k", 80), 80, 20, 2000),
                    "relation_candidate_k": _clamp_int(pick("retrieval.sparse.relation_candidate_k", 60), 60, 20, 2000),
                },
                "fusion": {
                    "method": fusion_method,
                    "rrf_k": _clamp_int(pick("retrieval.fusion.rrf_k", 60), 60, 1, 500),
                    "vector_weight": float(vec_w),
                    "bm25_weight": float(bm_w),
                },
                "vector_pools": {
                    "mode": vector_pool_mode,
                    "paragraph_top_k": _clamp_int(pick("retrieval.vector_pools.paragraph_top_k", 20), 20, 4, 512),
                    "graph_top_k": _clamp_int(pick("retrieval.vector_pools.graph_top_k", 40), 40, 4, 1000),
                    "graph_expand_paragraph_k": _clamp_int(
                        pick("retrieval.vector_pools.graph_expand_paragraph_k", 80),
                        80,
                        4,
                        2000,
                    ),
                    "relation_expand_per_hit": _clamp_int(
                        pick("retrieval.vector_pools.relation_expand_per_hit", 5),
                        5,
                        1,
                        50,
                    ),
                    "entity_expand_per_hit": _clamp_int(
                        pick("retrieval.vector_pools.entity_expand_per_hit", 8),
                        8,
                        1,
                        80,
                    ),
                    "relation_evidence_weight": _clamp_float(
                        pick("retrieval.vector_pools.relation_evidence_weight", 1.0),
                        1.0,
                        0.0,
                        3.0,
                    ),
                    "entity_evidence_weight": _clamp_float(
                        pick("retrieval.vector_pools.entity_evidence_weight", 0.55),
                        0.55,
                        0.0,
                        3.0,
                    ),
                    "semantic_weight": float(semantic_w),
                    "sparse_weight": float(sparse_w),
                    "graph_weight": float(graph_w),
                    "relation_intent": {
                        "graph_top_k": _clamp_int(
                            pick("retrieval.vector_pools.relation_intent.graph_top_k", 80),
                            80,
                            4,
                            1000,
                        ),
                        "semantic_weight": float(ri_semantic_w),
                        "sparse_weight": float(ri_sparse_w),
                        "graph_weight": float(ri_graph_w),
                    },
                },
            },
            "threshold": {
                "min_threshold": float(min_threshold),
                "max_threshold": float(max_threshold),
                "percentile": _clamp_float(pick("threshold.percentile", 75.0), 75.0, 1.0, 99.0),
                "min_results": _clamp_int(pick("threshold.min_results", 3), 3, 1, 100),
                "enable_auto_adjust": _coerce_bool(pick("threshold.enable_auto_adjust", True), True),
            },
        }

    def get_persistable_profile(self, profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._normalize_profile(profile or self.get_profile_snapshot())

    async def _apply_profile_to_runtime(self, normalized: Dict[str, Any], *, validate: bool = True) -> Dict[str, Any]:
        applier = getattr(self.plugin, "apply_retrieval_tuning_profile", None)
        if callable(applier):
            result = applier(normalized, validate=validate)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, dict):
                raise RuntimeError("运行时热重建返回值非法")
            if not bool(result.get("success", True)):
                raise RuntimeError(str(result.get("error") or "运行时热重建失败"))
            return {
                "runtime_rebuilt": bool(result.get("runtime_rebuilt", False)),
                "validation_passed": bool(result.get("validation_passed", True)),
                "apply_error": str(result.get("error", "") or ""),
            }

        if not isinstance(getattr(self.plugin, "config", None), dict):
            raise RuntimeError("插件 config 不可写")
        for key, value in normalized.items():
            _nested_set(self.plugin.config, key, value)
        plugin_cfg = getattr(self.plugin, "_plugin_config", None)
        if isinstance(plugin_cfg, dict):
            for key, value in normalized.items():
                _nested_set(plugin_cfg, key, value)
        return {"runtime_rebuilt": False, "validation_passed": True, "apply_error": ""}

    async def apply_profile(
        self, profile: Dict[str, Any], *, reason: str = "manual", validate: bool = True
    ) -> Dict[str, Any]:
        normalized = self._normalize_profile(profile)
        current = self.get_profile_snapshot()
        self._rollback_snapshot = current
        apply_result = await self._apply_profile_to_runtime(normalized, validate=validate)
        return {
            "applied": normalized,
            "rollback_snapshot": current,
            "reason": reason,
            "applied_at": _now(),
            "persisted": False,
            **apply_result,
        }

    async def rollback_profile(self) -> Dict[str, Any]:
        if not self._rollback_snapshot:
            raise ValueError("暂无可回滚的参数快照")
        target = self._normalize_profile(self._rollback_snapshot, fallback=self._rollback_snapshot)
        apply_result = await self._apply_profile_to_runtime(target, validate=True)
        return {"rolled_back_to": target, "rolled_back_at": _now(), **apply_result}

    def export_toml_snippet(self, profile: Optional[Dict[str, Any]] = None) -> str:
        p = self._normalize_profile(profile or self.get_profile_snapshot())
        r = p["retrieval"]
        t = p["threshold"]
        lines = [
            "[retrieval]",
            f"top_k_paragraphs = {int(r['top_k_paragraphs'])}",
            f"top_k_relations = {int(r['top_k_relations'])}",
            f"top_k_final = {int(r['top_k_final'])}",
            f"alpha = {float(r['alpha']):.4f}",
            f"enable_ppr = {str(bool(r['enable_ppr'])).lower()}",
            f"ppr_alpha = {float(r['ppr_alpha']):.4f}",
            f"ppr_timeout_seconds = {float(r['ppr_timeout_seconds']):.4f}",
            "",
            "[retrieval.search.smart_fallback]",
            f"enabled = {str(bool(r['search']['smart_fallback']['enabled'])).lower()}",
            "",
            "[retrieval.sparse]",
            f"enabled = {str(bool(r['sparse']['enabled'])).lower()}",
            f'mode = "{r["sparse"]["mode"]}"',
            f"candidate_k = {int(r['sparse']['candidate_k'])}",
            f"relation_candidate_k = {int(r['sparse']['relation_candidate_k'])}",
            "",
            "[retrieval.fusion]",
            f'method = "{r["fusion"]["method"]}"',
            f"rrf_k = {int(r['fusion']['rrf_k'])}",
            f"vector_weight = {float(r['fusion']['vector_weight']):.4f}",
            f"bm25_weight = {float(r['fusion']['bm25_weight']):.4f}",
            "",
            "[retrieval.vector_pools]",
            f'mode = "{r["vector_pools"]["mode"]}"',
            f"paragraph_top_k = {int(r['vector_pools']['paragraph_top_k'])}",
            f"graph_top_k = {int(r['vector_pools']['graph_top_k'])}",
            f"graph_expand_paragraph_k = {int(r['vector_pools']['graph_expand_paragraph_k'])}",
            f"relation_expand_per_hit = {int(r['vector_pools']['relation_expand_per_hit'])}",
            f"entity_expand_per_hit = {int(r['vector_pools']['entity_expand_per_hit'])}",
            f"relation_evidence_weight = {float(r['vector_pools']['relation_evidence_weight']):.4f}",
            f"entity_evidence_weight = {float(r['vector_pools']['entity_evidence_weight']):.4f}",
            f"semantic_weight = {float(r['vector_pools']['semantic_weight']):.4f}",
            f"sparse_weight = {float(r['vector_pools']['sparse_weight']):.4f}",
            f"graph_weight = {float(r['vector_pools']['graph_weight']):.4f}",
            "",
            "[retrieval.vector_pools.relation_intent]",
            f"graph_top_k = {int(r['vector_pools']['relation_intent']['graph_top_k'])}",
            f"semantic_weight = {float(r['vector_pools']['relation_intent']['semantic_weight']):.4f}",
            f"sparse_weight = {float(r['vector_pools']['relation_intent']['sparse_weight']):.4f}",
            f"graph_weight = {float(r['vector_pools']['relation_intent']['graph_weight']):.4f}",
            "",
            "[threshold]",
            f"min_threshold = {float(t['min_threshold']):.4f}",
            f"max_threshold = {float(t['max_threshold']):.4f}",
            f"percentile = {float(t['percentile']):.4f}",
            f"min_results = {int(t['min_results'])}",
            f"enable_auto_adjust = {str(bool(t['enable_auto_adjust'])).lower()}",
        ]
        return "\n".join(lines).strip() + "\n"

    def _pending_task_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status in {"queued", "running", "cancel_requested"})

    def _sample_triples_for_query_set(
        self,
        *,
        triples: List[Tuple[Any, Any, Any, Any]],
        sample_size: int,
        seed: int,
    ) -> Tuple[List[Tuple[str, str, str, str]], Dict[str, Any]]:
        normalized: List[Tuple[str, str, str, str]] = []
        for row in triples:
            try:
                subject, predicate, obj, rel_hash = row
            except Exception:
                continue
            relation_hash = str(rel_hash or "").strip()
            if not relation_hash:
                continue
            normalized.append((str(subject or ""), str(predicate or ""), str(obj or ""), relation_hash))

        if not normalized:
            return [], {"error": "no_relations"}

        target = min(max(4, int(sample_size)), len(normalized))
        predicate_counter = Counter([str(x[1] or "").strip() or "__empty__" for x in normalized])
        entity_counter = Counter()
        for subj, _, obj, _ in normalized:
            entity_counter.update([str(subj or "").strip().lower() or "__empty__"])
            entity_counter.update([str(obj or "").strip().lower() or "__empty__"])

        if target >= len(normalized):
            return list(normalized), {
                "strategy": "all",
                "sample_size": int(target),
                "total_triples": int(len(normalized)),
                "predicate_total": int(len(predicate_counter)),
                "predicate_sampled": int(len(predicate_counter)),
            }

        rng = random.Random(f"{seed}:triple_sample")
        by_predicate: Dict[str, List[int]] = {}
        for idx, (_, predicate, _, _) in enumerate(normalized):
            key = str(predicate or "").strip() or "__empty__"
            by_predicate.setdefault(key, []).append(idx)
        for pool in by_predicate.values():
            rng.shuffle(pool)

        predicate_order = sorted(by_predicate.keys())
        rng.shuffle(predicate_order)

        selected: List[int] = []
        selected_set = set()

        # 第一轮按谓词轮询，避免高频谓词主导查询集。
        while len(selected) < target:
            progressed = False
            for key in predicate_order:
                pool = by_predicate.get(key, [])
                if not pool:
                    continue
                idx = int(pool.pop())
                if idx in selected_set:
                    continue
                selected.append(idx)
                selected_set.add(idx)
                progressed = True
                if len(selected) >= target:
                    break
            if not progressed:
                break

        if len(selected) < target:
            remain = [idx for idx in range(len(normalized)) if idx not in selected_set]
            rng.shuffle(remain)

            # 第二轮优先低频实体和谓词，提高样本多样性。
            def _remain_score(idx: int) -> Tuple[int, int]:
                subj, predicate, obj, _ = normalized[idx]
                subject_freq = int(entity_counter.get(str(subj or "").strip().lower() or "__empty__", 0))
                object_freq = int(entity_counter.get(str(obj or "").strip().lower() or "__empty__", 0))
                pred_freq = int(predicate_counter.get(str(predicate or "").strip() or "__empty__", 0))
                return (subject_freq + object_freq, pred_freq)

            remain = sorted(remain, key=_remain_score)
            need = target - len(selected)
            for idx in remain[:need]:
                selected.append(idx)
                selected_set.add(idx)

        selected = selected[:target]
        sampled = [normalized[idx] for idx in selected]
        sampled_predicates = {str(x[1] or "").strip() or "__empty__" for x in sampled}

        return sampled, {
            "strategy": "predicate_round_robin_entity_diversity",
            "sample_size": int(target),
            "total_triples": int(len(normalized)),
            "predicate_total": int(len(predicate_counter)),
            "predicate_sampled": int(len(sampled_predicates)),
        }

    def _select_round_eval_cases(
        self,
        *,
        cases: List[RetrievalQueryCase],
        intensity: str,
        round_index: int,
        seed: int,
    ) -> List[RetrievalQueryCase]:
        if not cases:
            return []
        mode = str(intensity or "standard").strip().lower()
        if mode not in INTENSITIES:
            mode = "standard"
        if mode == "deep":
            return list(cases)

        if mode == "quick":
            ratio = 0.45
            min_total = 16
        else:
            ratio = 0.70
            min_total = 24

        total = len(cases)
        target = max(min_total, int(total * ratio))
        if target >= total:
            return list(cases)

        rng = random.Random(f"{seed}:{round_index}:subset")
        by_cat: Dict[str, List[RetrievalQueryCase]] = {}
        for item in cases:
            by_cat.setdefault(str(item.category), []).append(item)

        selected: List[RetrievalQueryCase] = []
        selected_ids = set()
        cat_names = sorted([x for x in by_cat.keys() if x in CATEGORIES])
        if not cat_names:
            cat_names = sorted(by_cat.keys())
        per_cat = max(1, target // max(1, len(cat_names)))

        for cat in cat_names:
            pool = by_cat.get(cat, [])
            if not pool:
                continue
            picked = list(pool) if len(pool) <= per_cat else rng.sample(pool, per_cat)
            for item in picked:
                if item.case_id in selected_ids:
                    continue
                selected.append(item)
                selected_ids.add(item.case_id)

        if len(selected) < target:
            remain = [x for x in cases if x.case_id not in selected_ids]
            if len(remain) > (target - len(selected)):
                remain = rng.sample(remain, target - len(selected))
            for item in remain:
                selected.append(item)
                selected_ids.add(item.case_id)

        return selected[:target]

    def _split_query_cases(
        self,
        *,
        cases: List[RetrievalQueryCase],
        seed: int,
    ) -> Tuple[List[RetrievalQueryCase], List[RetrievalQueryCase], Dict[str, Any]]:
        if len(cases) <= 1:
            return (
                list(cases),
                list(cases),
                {"strategy": "shared_small_set", "train": len(cases), "holdout": len(cases)},
            )

        rng = random.Random(f"{seed}:holdout_split")
        by_cat: Dict[str, List[RetrievalQueryCase]] = {}
        for item in cases:
            by_cat.setdefault(str(item.category), []).append(item)

        holdout_target = max(4, int(round(len(cases) * 0.25)))
        holdout_target = min(holdout_target, max(1, len(cases) - 1))
        holdout_ids = set()

        for cat in sorted(by_cat):
            pool = list(by_cat[cat])
            rng.shuffle(pool)
            per_cat = 1 if len(pool) > 1 else 0
            for item in pool[:per_cat]:
                if len(holdout_ids) < holdout_target:
                    holdout_ids.add(item.case_id)

        if len(holdout_ids) < holdout_target:
            remain = [item for item in cases if item.case_id not in holdout_ids]
            rng.shuffle(remain)
            for item in remain[: holdout_target - len(holdout_ids)]:
                holdout_ids.add(item.case_id)

        train = [item for item in cases if item.case_id not in holdout_ids]
        holdout = [item for item in cases if item.case_id in holdout_ids]
        if not train or not holdout:
            return list(cases), list(cases), {"strategy": "shared_fallback", "train": len(cases), "holdout": len(cases)}

        return (
            train,
            holdout,
            {
                "strategy": "category_balanced_holdout",
                "train": len(train),
                "holdout": len(holdout),
                "holdout_ratio": round(len(holdout) / max(1, len(cases)), 4),
            },
        )

    async def _ensure_worker(self) -> None:
        async with self._lock:
            if self._worker_task and not self._worker_task.done():
                return
            self._stopping = False
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def shutdown(self) -> None:
        self._stopping = True
        worker = self._worker_task
        if worker is None or worker.done():
            return
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Retrieval tuning worker shutdown failed: {e}")

    async def create_task(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self._is_enabled():
            raise ValueError("检索调优中心已禁用")
        self._ensure_ready()

        data = payload or {}
        objective = str(data.get("objective") or self._cfg("web.tuning.default_objective", "precision_priority"))
        if objective not in OBJECTIVES:
            raise ValueError(f"objective 非法: {objective}")

        intensity = str(data.get("intensity") or self._cfg("web.tuning.default_intensity", "standard"))
        if intensity not in INTENSITIES:
            raise ValueError(f"intensity 非法: {intensity}")

        rounds_total = _clamp_int(data.get("rounds", INTENSITIES[intensity]), INTENSITIES[intensity], 1, 200)
        sample_size = _clamp_int(data.get("sample_size", self._cfg("web.tuning.default_sample_size", 24)), 24, 4, 500)
        top_k_eval = _clamp_int(data.get("top_k_eval", self._cfg("web.tuning.default_top_k_eval", 20)), 20, 5, 100)
        eval_query_timeout_seconds = _clamp_float(
            data.get("eval_query_timeout_seconds", self._eval_query_timeout_s()),
            self._eval_query_timeout_s(),
            0.01,
            120.0,
        )
        llm_enabled = _coerce_bool(data.get("llm_enabled", True), True)
        seed = data.get("seed")
        try:
            seed = int(seed)
        except Exception:
            seed = int(time.time()) % 1000003

        async with self._lock:
            if self._pending_task_count() >= self._queue_limit():
                raise ValueError("调优任务队列已满，请稍后重试")
            task = RetrievalTuningTaskRecord(
                task_id=uuid.uuid4().hex,
                status="queued",
                progress=0.0,
                objective=objective,
                intensity=intensity,
                rounds_total=rounds_total,
                params={
                    "sample_size": sample_size,
                    "top_k_eval": top_k_eval,
                    "eval_query_timeout_seconds": float(eval_query_timeout_seconds),
                    "llm_enabled": llm_enabled,
                    "seed": seed,
                },
            )
            self._tasks[task.task_id] = task
            self._task_order.appendleft(task.task_id)
            self._queue.append(task.task_id)
            task.updated_at = _now()

        await self._ensure_worker()
        return task.to_summary()

    async def list_tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = _clamp_int(limit, 50, 1, 500)
        async with self._lock:
            items: List[Dict[str, Any]] = []
            for task_id in list(self._task_order)[:limit]:
                task = self._tasks.get(task_id)
                if task:
                    items.append(task.to_summary())
            return items

    async def get_task(self, task_id: str, include_rounds: bool = False) -> Optional[Dict[str, Any]]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            return task.to_detail(include_rounds=include_rounds)

    async def get_rounds(self, task_id: str, offset: int = 0, limit: int = 50) -> Optional[Dict[str, Any]]:
        offset = max(0, int(offset))
        limit = _clamp_int(limit, 50, 1, 500)
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            total = len(task.rounds)
            sliced = task.rounds[offset : offset + limit]
            return {
                "total": total,
                "offset": offset,
                "limit": limit,
                "items": [item.to_dict() for item in sliced],
            }

    async def cancel_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.status in {"completed", "failed", "cancelled"}:
                return task.to_summary()
            if task.status == "queued":
                task.status = "cancelled"
                task.cancel_requested = True
                task.finished_at = _now()
                task.updated_at = task.finished_at
                self._queue = deque([x for x in self._queue if x != task_id])
                return task.to_summary()
            task.status = "cancel_requested"
            task.cancel_requested = True
            task.updated_at = _now()
            return task.to_summary()

    async def apply_best(self, task_id: str, *, validate: bool = True) -> Dict[str, Any]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError("任务不存在")
            if task.status != "completed":
                raise ValueError("任务未完成，无法应用最优参数")
            if not task.best_profile:
                raise ValueError("任务没有可应用的最优参数")
            if validate and task.validation_summary and not task.recommended:
                raise ValueError("任务未通过 holdout/online_like 验证，无法应用最优参数")
            best = copy.deepcopy(task.best_profile)
        applied = await self.apply_profile(best, reason=f"task:{task_id}:apply_best", validate=validate)
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.apply_log.append(
                    {
                        "applied_at": _now(),
                        "reason": "apply_best",
                        "profile": best,
                        "validate": bool(validate),
                    }
                )
                task.updated_at = _now()
        return applied

    async def get_report(self, task_id: str, fmt: str = "md") -> Optional[Dict[str, Any]]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            artifacts = dict(task.artifact_paths)
        fmt = str(fmt or "md").strip().lower()
        if fmt not in {"md", "json"}:
            fmt = "md"
        path_key = "report_md" if fmt == "md" else "report_json"
        path = artifacts.get(path_key)
        if not path:
            return {"format": fmt, "content": "", "path": ""}
        p = Path(path)
        if not p.exists():
            return {"format": fmt, "content": "", "path": str(p)}
        try:
            content = p.read_text(encoding="utf-8")
        except Exception:
            content = ""
        return {"format": fmt, "content": content, "path": str(p)}

    async def _worker_loop(self) -> None:
        while not self._stopping:
            task_id: Optional[str] = None
            async with self._lock:
                while self._queue:
                    candidate = self._queue.popleft()
                    task = self._tasks.get(candidate)
                    if task is None:
                        continue
                    if task.status != "queued":
                        continue
                    task_id = candidate
                    self._active_task_id = candidate
                    break

            if not task_id:
                await asyncio.sleep(self._poll_interval_s())
                continue

            try:
                await self._run_task(task_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Retrieval tuning task crashed: task_id={task_id}, err={e}")
                async with self._lock:
                    task = self._tasks.get(task_id)
                    if task is not None:
                        task.status = "failed"
                        task.error = str(e)
                        task.finished_at = _now()
                        task.updated_at = task.finished_at
            finally:
                async with self._lock:
                    if self._active_task_id == task_id:
                        self._active_task_id = None

    async def _run_task(self, task_id: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = "running"
            task.started_at = _now()
            task.updated_at = task.started_at

        artifacts_dir = self._artifacts_root / task_id
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        query_set_path = artifacts_dir / "query_set.json"
        rounds_path = artifacts_dir / "round_metrics.jsonl"
        best_profile_path = artifacts_dir / "best_profile.json"
        report_json_path = artifacts_dir / "report.json"
        report_md_path = artifacts_dir / "report.md"

        try:
            params = dict(task.params)
            cases, stats = await self._build_query_set(
                sample_size=int(params["sample_size"]),
                seed=int(params["seed"]),
                llm_enabled=bool(params.get("llm_enabled", True)),
            )
            if not cases:
                raise ValueError("当前知识库样本不足，无法构建调优测试集")
            train_cases, holdout_cases, split_stats = self._split_query_cases(
                cases=cases,
                seed=int(params["seed"]),
            )
            stats["split"] = split_stats

            query_set_path.write_text(
                json.dumps(
                    {
                        "task_id": task_id,
                        "created_at": _now(),
                        "stats": stats,
                        "items": [c.to_dict() for c in cases],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            baseline_profile = self.get_profile_snapshot()
            top_k_eval = int(params["top_k_eval"])
            baseline_eval = await self._evaluate_profile(
                profile=baseline_profile,
                cases=train_cases,
                objective=task.objective,
                top_k_eval=top_k_eval,
                query_timeout_s=float(params.get("eval_query_timeout_seconds") or self._eval_query_timeout_s()),
                evaluation_mode="stable",
            )
            baseline_round = RetrievalTuningRoundRecord(
                round_index=0,
                candidate_profile=baseline_profile,
                metrics=baseline_eval["metrics"],
                score=float(baseline_eval["score"]),
                latency_ms=float(baseline_eval["avg_elapsed_ms"]),
                failure_summary=baseline_eval["failure_summary"],
            )
            rounds_path.write_text(json.dumps(baseline_round.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")

            async with self._lock:
                task = self._tasks.get(task_id)
                if task is None:
                    return
                task.query_set_stats = stats
                task.baseline_profile = copy.deepcopy(baseline_profile)
                task.baseline_metrics = copy.deepcopy(baseline_eval["metrics"])
                task.rounds.append(baseline_round)
                task.best_profile = copy.deepcopy(baseline_profile)
                task.best_metrics = copy.deepcopy(baseline_eval["metrics"])
                task.best_score = float(baseline_eval["score"])
                task.progress = 0.0
                task.updated_at = _now()

            best_profile = copy.deepcopy(baseline_profile)
            best_metrics = copy.deepcopy(baseline_eval["metrics"])
            best_failure_summary = copy.deepcopy(baseline_eval["failure_summary"])
            best_score = float(baseline_eval["score"])
            llm_suggestions: List[Dict[str, Any]] = []
            task_cancelled = False

            for round_idx in range(1, int(task.rounds_total) + 1):
                async with self._lock:
                    task = self._tasks.get(task_id)
                    if task is None:
                        return
                    if task.cancel_requested or task.status == "cancel_requested":
                        task.status = "cancelled"
                        task.finished_at = _now()
                        task.updated_at = task.finished_at
                        task_cancelled = True
                        break

                if round_idx == 1 or (round_idx % 5 == 0 and not llm_suggestions):
                    llm_suggestions = await self._suggest_profiles_with_llm(
                        base_profile=best_profile,
                        failure_summary=best_failure_summary,
                        objective=task.objective,
                        max_count=3,
                        enabled=bool(params.get("llm_enabled", True)),
                    )

                candidate_profile = self._generate_candidate_profile(
                    task_id=task_id,
                    round_index=round_idx,
                    objective=task.objective,
                    baseline_profile=baseline_profile,
                    best_profile=best_profile,
                    llm_suggestions=llm_suggestions,
                )
                eval_cases = self._select_round_eval_cases(
                    cases=train_cases,
                    intensity=task.intensity,
                    round_index=round_idx,
                    seed=int(params.get("seed", 0)),
                )
                eval_result = await self._evaluate_profile(
                    profile=candidate_profile,
                    cases=eval_cases,
                    objective=task.objective,
                    top_k_eval=top_k_eval,
                    query_timeout_s=float(params.get("eval_query_timeout_seconds") or self._eval_query_timeout_s()),
                    evaluation_mode="stable",
                )
                round_record = RetrievalTuningRoundRecord(
                    round_index=round_idx,
                    candidate_profile=candidate_profile,
                    metrics=eval_result["metrics"],
                    score=float(eval_result["score"]),
                    latency_ms=float(eval_result["avg_elapsed_ms"]),
                    failure_summary=eval_result["failure_summary"],
                )
                with rounds_path.open("a", encoding="utf-8") as fp:
                    fp.write(json.dumps(round_record.to_dict(), ensure_ascii=False) + "\n")

                if float(eval_result["score"]) > float(best_score):
                    best_score = float(eval_result["score"])
                    best_profile = copy.deepcopy(candidate_profile)
                    best_metrics = copy.deepcopy(eval_result["metrics"])
                    best_failure_summary = copy.deepcopy(eval_result["failure_summary"])

                async with self._lock:
                    task = self._tasks.get(task_id)
                    if task is None:
                        return
                    task.rounds_done = round_idx
                    task.rounds.append(round_record)
                    task.best_profile = copy.deepcopy(best_profile)
                    task.best_metrics = copy.deepcopy(best_metrics)
                    task.best_score = float(best_score)
                    task.progress = min(1.0, float(round_idx) / float(task.rounds_total))
                    task.updated_at = _now()

            if best_profile and (not task_cancelled):
                # 候选轮可能基于子样本评估，收官时用训练集复核，再用 holdout 验证泛化。
                best_full = await self._evaluate_profile(
                    profile=best_profile,
                    cases=train_cases,
                    objective=task.objective,
                    top_k_eval=top_k_eval,
                    query_timeout_s=float(params.get("eval_query_timeout_seconds") or self._eval_query_timeout_s()),
                    evaluation_mode="stable",
                )
                best_profile = copy.deepcopy(best_profile)
                best_metrics = copy.deepcopy(best_full["metrics"])
                best_failure_summary = copy.deepcopy(best_full["failure_summary"])
                best_score = float(best_full["score"])
                if best_score < float(baseline_eval["score"]):
                    best_profile = copy.deepcopy(baseline_profile)
                    best_metrics = copy.deepcopy(baseline_eval["metrics"])
                    best_failure_summary = copy.deepcopy(baseline_eval["failure_summary"])
                    best_score = float(baseline_eval["score"])

                validation_summary = await self._validate_best_profile(
                    baseline_profile=baseline_profile,
                    candidate_profile=best_profile,
                    holdout_cases=holdout_cases,
                    objective=task.objective,
                    top_k_eval=top_k_eval,
                    query_timeout_s=float(params.get("eval_query_timeout_seconds") or self._eval_query_timeout_s()),
                )
                recommended = bool(validation_summary.get("recommended", False))
                online_like = (
                    validation_summary.get("online_like")
                    if isinstance(validation_summary.get("online_like"), dict)
                    else {}
                )
                online_baseline = online_like.get("baseline") if isinstance(online_like.get("baseline"), dict) else {}
                online_best = online_like.get("best") if isinstance(online_like.get("best"), dict) else {}
                if recommended:
                    best_metrics = copy.deepcopy(online_best.get("metrics") or best_metrics)
                    best_score = float(online_best.get("score", best_score) or best_score)
                else:
                    best_profile = copy.deepcopy(baseline_profile)
                    best_metrics = copy.deepcopy(online_baseline.get("metrics") or baseline_eval["metrics"])
                    best_score = float(online_baseline.get("score", baseline_eval["score"]) or baseline_eval["score"])
                baseline_metrics_for_report = copy.deepcopy(online_baseline.get("metrics") or baseline_eval["metrics"])

                async with self._lock:
                    task = self._tasks.get(task_id)
                    if task is not None:
                        task.best_profile = copy.deepcopy(best_profile)
                        task.best_metrics = copy.deepcopy(best_metrics)
                        task.best_score = float(best_score)
                        task.baseline_metrics = baseline_metrics_for_report
                        task.validation_summary = copy.deepcopy(validation_summary)
                        task.recommended = recommended
                        task.updated_at = _now()

            async with self._lock:
                task = self._tasks.get(task_id)
                if task is None:
                    return
                if task.status not in {"cancelled", "failed"}:
                    task.status = "completed"
                    task.progress = 1.0
                    task.finished_at = _now()
                    task.updated_at = task.finished_at
                final_task = copy.deepcopy(task)

            if final_task.status == "completed":
                best_profile_path.write_text(
                    json.dumps(final_task.best_profile, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                report_payload = self._build_report_payload(final_task)
                report_json_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                report_md_path.write_text(self._build_report_markdown(final_task, report_payload), encoding="utf-8")

            async with self._lock:
                task = self._tasks.get(task_id)
                if task is not None:
                    task.artifact_paths = {
                        "query_set": str(query_set_path),
                        "round_metrics_jsonl": str(rounds_path),
                        "best_profile": str(best_profile_path),
                        "report_json": str(report_json_path),
                        "report_md": str(report_md_path),
                    }
                    task.updated_at = _now()
        except Exception as e:
            logger.error(f"Retrieval tuning task failed: task_id={task_id}, err={e}")
            async with self._lock:
                task = self._tasks.get(task_id)
                if task is not None:
                    task.status = "failed"
                    task.error = str(e)
                    task.finished_at = _now()
                    task.updated_at = task.finished_at

    async def _build_query_set(
        self, *, sample_size: int, seed: int, llm_enabled: bool
    ) -> Tuple[List[RetrievalQueryCase], Dict[str, Any]]:
        store = getattr(self.plugin, "metadata_store", None)
        if store is None:
            return [], {"error": "metadata_store_unavailable"}

        triples = list(store.get_all_triples() or [])
        if not triples:
            return [], {"error": "no_relations"}

        sampled, sample_info = self._sample_triples_for_query_set(
            triples=triples,
            sample_size=sample_size,
            seed=seed,
        )
        if not sampled:
            return [], {"error": "no_relations"}

        anchors: List[Dict[str, Any]] = []
        for idx, row in enumerate(sampled):
            subject, predicate, obj, relation_hash = row
            paragraphs = store.get_paragraphs_by_relation(relation_hash)
            para_hash = ""
            para_content = ""
            if paragraphs:
                para_hash = str(paragraphs[0].get("hash") or "").strip()
                para_content = str(paragraphs[0].get("content") or "")
            anchors.append(
                {
                    "anchor_id": f"a{idx + 1:04d}",
                    "subject": str(subject or ""),
                    "predicate": str(predicate or ""),
                    "object": str(obj or ""),
                    "relation_hash": relation_hash,
                    "paragraph_hash": para_hash,
                    "paragraph_excerpt": para_content[:300],
                }
            )

        if not anchors:
            return [], {"error": "no_anchors"}

        predicate_groups: Dict[str, List[Dict[str, Any]]] = {}
        for anchor in anchors:
            predicate_groups.setdefault(str(anchor.get("predicate") or ""), []).append(anchor)

        nl_queries = await self._generate_nl_queries_with_llm(anchors, enabled=llm_enabled)
        cases: List[RetrievalQueryCase] = []

        seq = 0
        for anchor in anchors:
            seq += 1
            subject = anchor["subject"]
            predicate = anchor["predicate"]
            obj = anchor["object"]
            rel_hash = anchor["relation_hash"]
            para_hash = anchor["paragraph_hash"]
            expected = [rel_hash]
            if para_hash:
                expected.append(para_hash)
            aid = anchor["anchor_id"]

            common_meta = {
                "anchor_id": aid,
                "relation_hash": rel_hash,
                "paragraph_hash": para_hash,
                "subject": subject,
                "predicate": predicate,
                "object": obj,
            }
            cases.append(
                RetrievalQueryCase(
                    case_id=f"spo_relation_{seq:04d}",
                    category="spo_relation",
                    query=f"{subject}|{predicate}|{obj}",
                    expected_hashes=[rel_hash],
                    expected_spo={"subject": subject, "predicate": predicate, "object": obj},
                    metadata=dict(common_meta),
                )
            )
            cases.append(
                RetrievalQueryCase(
                    case_id=f"spo_search_{seq:04d}",
                    category="spo_search",
                    query=self._build_spo_search_query(
                        anchor=anchor,
                        seq=seq,
                        predicate_groups=predicate_groups,
                    ),
                    expected_hashes=list(expected),
                    metadata=dict(common_meta),
                )
            )
            cases.append(
                RetrievalQueryCase(
                    case_id=f"query_kw_{seq:04d}",
                    category="query_kw",
                    query=self._build_keyword_query(
                        anchor=anchor,
                        seq=seq,
                        predicate_groups=predicate_groups,
                    ),
                    expected_hashes=list(expected),
                    metadata=dict(common_meta),
                )
            )
            nl_query = nl_queries.get(aid) or self._build_nl_template(
                anchor=anchor,
                seq=seq,
                predicate_groups=predicate_groups,
            )
            cases.append(
                RetrievalQueryCase(
                    case_id=f"query_nl_{seq:04d}",
                    category="query_nl",
                    query=nl_query,
                    expected_hashes=list(expected),
                    metadata=dict(common_meta),
                )
            )

        counts = Counter([c.category for c in cases])
        stats = {
            "anchors": len(anchors),
            "case_total": len(cases),
            "category_counts": {k: int(v) for k, v in counts.items()},
            "seed": int(seed),
            "sample_size": int(sample_info.get("sample_size", len(anchors))),
            "sampling": dict(sample_info),
            "llm_nl_enabled": bool(llm_enabled),
            "llm_nl_generated": int(len(nl_queries)),
        }
        return cases, stats

    def _pick_contrast_anchor(
        self,
        *,
        anchor: Dict[str, Any],
        predicate_groups: Dict[str, List[Dict[str, Any]]],
        seq: int,
    ) -> Optional[Dict[str, Any]]:
        predicate = str(anchor.get("predicate") or "")
        pool = predicate_groups.get(predicate, [])
        if not pool:
            return None
        candidates = [
            x for x in pool if x is not anchor and str(x.get("object") or "") != str(anchor.get("object") or "")
        ]
        if not candidates:
            return None
        return candidates[seq % len(candidates)]

    def _build_spo_search_query(
        self,
        *,
        anchor: Dict[str, Any],
        seq: int,
        predicate_groups: Dict[str, List[Dict[str, Any]]],
    ) -> str:
        subject = str(anchor.get("subject") or "")
        predicate = str(anchor.get("predicate") or "")
        obj = str(anchor.get("object") or "")
        contrast = self._pick_contrast_anchor(anchor=anchor, predicate_groups=predicate_groups, seq=seq)
        contrast_obj = str(contrast.get("object") or "").strip() if contrast else ""

        variants = [
            f"{subject} {predicate} {obj}",
            f"{subject} {obj} relation {predicate}",
            f"{predicate} {subject} {obj} evidence",
            f"{subject} {predicate} {obj} not {contrast_obj}".strip(),
        ]
        return variants[seq % len(variants)].strip()

    def _build_keyword_query(
        self,
        *,
        anchor: Dict[str, Any],
        seq: int,
        predicate_groups: Dict[str, List[Dict[str, Any]]],
    ) -> str:
        subject = str(anchor.get("subject") or "")
        predicate = str(anchor.get("predicate") or "")
        obj = str(anchor.get("object") or "")
        excerpt = str(anchor.get("paragraph_excerpt") or "")
        tokens = re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}", excerpt)
        extras: List[str] = []
        seen = set()
        for token in tokens:
            key = token.lower()
            if key in seen:
                continue
            if key in {subject.lower(), predicate.lower(), obj.lower()}:
                continue
            seen.add(key)
            extras.append(token)
            if len(extras) >= 2:
                break
        contrast = self._pick_contrast_anchor(anchor=anchor, predicate_groups=predicate_groups, seq=seq)
        contrast_obj = str(contrast.get("object") or "").strip() if contrast else ""

        variants = [
            [subject, obj] + extras[:2],
            [predicate, obj] + extras[:2],
            [subject, predicate] + extras[:2],
            [subject, obj, predicate, contrast_obj] + extras[:1],
        ]
        parts = variants[seq % len(variants)]
        return " ".join([x for x in parts if x]).strip()

    def _build_nl_template(
        self,
        *,
        anchor: Dict[str, Any],
        seq: int,
        predicate_groups: Dict[str, List[Dict[str, Any]]],
    ) -> str:
        subject = str(anchor.get("subject") or "")
        predicate = str(anchor.get("predicate") or "")
        obj = str(anchor.get("object") or "")
        contrast = self._pick_contrast_anchor(anchor=anchor, predicate_groups=predicate_groups, seq=seq)
        contrast_obj = str(contrast.get("object") or "").strip() if contrast else ""
        templates = [
            f"请问 {subject} 与 {obj} 的关系是什么，是否是“{predicate}”？",
            f"在当前知识库中，哪条信息说明 {subject} 对应的是 {obj}，关系词接近“{predicate}”？",
            f"我想确认：{subject} 和 {obj} 之间是不是“{predicate}”这层关系，而不是 {contrast_obj}？",
            f"帮我查一下关于 {subject} 与 {obj} 的证据，重点看 {predicate} 相关描述。",
        ]
        return templates[seq % len(templates)]

    async def _select_llm_model(self) -> Optional[ResolvedLLMModel]:
        if llm_api is None:
            return None
        try:
            models = get_text_generation_model_tasks(llm_api) or {}
        except Exception:
            return None
        if not models:
            return None

        cfg_model = str(self._cfg("advanced.extraction_model", "auto") or "auto").strip()
        if cfg_model.lower() != "auto":
            task_name, task_config, selected_model_name = resolve_text_generation_model_selector(models, cfg_model)
            if task_name and task_config:
                return ResolvedLLMModel(
                    task_name=task_name,
                    task_config=task_config,
                    selected_model_name=selected_model_name,
                )
            logger.warning(f"advanced.extraction_model={cfg_model!r} 不可用于文本生成，已回退自动选择")
        task_name, task_config = pick_text_generation_task(
            models,
            preferred=("memory", "utils", "planner", "tool_use", "replyer"),
        )
        if task_name and task_config:
            return ResolvedLLMModel(task_name=task_name, task_config=task_config)
        return None

    async def _llm_call_text(self, prompt: str, *, request_type: str) -> str:
        if llm_api is None:
            raise RuntimeError("llm_api unavailable")
        resolved_model = await self._select_llm_model()
        if resolved_model is None:
            raise RuntimeError("no_llm_model")

        retry = self._llm_retry_cfg()
        max_attempts = int(retry["max_attempts"])
        min_wait = float(retry["min_wait_seconds"])
        max_wait = float(retry["max_wait_seconds"])
        backoff = float(retry["backoff_multiplier"])

        last_error: Optional[Exception] = None
        for idx in range(max_attempts):
            try:
                result = await generate_with_resolved_model(
                    resolved_model,
                    request_type=request_type,
                    prompt=prompt,
                    temperature=getattr(resolved_model.task_config, "temperature", None),
                    max_tokens=getattr(resolved_model.task_config, "max_tokens", None),
                )
                success = bool(result.success)
                response = str(result.completion.response or "")
                if not success:
                    raise RuntimeError("llm_generation_failed")
                text = str(response or "").strip()
                if text:
                    return text
                raise RuntimeError("empty_llm_response")
            except Exception as e:
                last_error = e
                if idx >= max_attempts - 1:
                    break
                delay = min(max_wait, min_wait * (backoff**idx))
                await asyncio.sleep(max(0.05, delay))
        raise RuntimeError(f"LLM call failed: {last_error}")

    async def _generate_nl_queries_with_llm(self, anchors: List[Dict[str, Any]], *, enabled: bool) -> Dict[str, str]:
        if not enabled or llm_api is None or not anchors:
            return {}
        payload = [
            {
                "anchor_id": x["anchor_id"],
                "subject": x["subject"],
                "predicate": x["predicate"],
                "object": x["object"],
                "paragraph_excerpt": x["paragraph_excerpt"][:180],
            }
            for x in anchors[:60]
        ]
        prompt = (
            "你是检索评估问题生成器。"
            "请基于给定 SPO 与简短上下文，为每条样本生成 1 条自然语言检索问题，返回 JSON："
            '{"items":[{"anchor_id":"...","query":"..."}]}。\n'
            f"样本：\n{json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            raw = await self._llm_call_text(prompt, request_type="A_Memorix.RetrievalTuning.NLCaseGen")
            obj = _safe_json_loads(raw)
            if not isinstance(obj, dict):
                return {}
            items = obj.get("items")
            if not isinstance(items, list):
                return {}
            out: Dict[str, str] = {}
            for row in items:
                if not isinstance(row, dict):
                    continue
                anchor_id = str(row.get("anchor_id") or "").strip()
                query = str(row.get("query") or "").strip()
                if anchor_id and query:
                    out[anchor_id] = query
            return out
        except Exception:
            return {}

    async def _suggest_profiles_with_llm(
        self,
        *,
        base_profile: Dict[str, Any],
        failure_summary: Dict[str, Any],
        objective: str,
        max_count: int,
        enabled: bool,
    ) -> List[Dict[str, Any]]:
        if not enabled or llm_api is None or max_count <= 0:
            return []
        prompt = (
            "你是检索调参专家。"
            "请基于基础参数与失败摘要，给出最多 "
            f'{int(max_count)} 组候选参数，返回 JSON: {{"profiles": [ ... ]}}。\n'
            "字段仅可包含：retrieval.top_k_paragraphs, retrieval.top_k_relations, retrieval.top_k_final, "
            "retrieval.alpha, retrieval.enable_ppr, retrieval.ppr_alpha, retrieval.ppr_timeout_seconds, "
            "retrieval.search.smart_fallback.enabled, "
            "retrieval.sparse.enabled, retrieval.sparse.mode, retrieval.sparse.candidate_k, retrieval.sparse.relation_candidate_k, "
            "retrieval.fusion.method, retrieval.fusion.rrf_k, retrieval.fusion.vector_weight, retrieval.fusion.bm25_weight, "
            "retrieval.vector_pools.paragraph_top_k, retrieval.vector_pools.graph_top_k, "
            "retrieval.vector_pools.graph_expand_paragraph_k, retrieval.vector_pools.relation_expand_per_hit, "
            "retrieval.vector_pools.entity_expand_per_hit, retrieval.vector_pools.relation_evidence_weight, "
            "retrieval.vector_pools.entity_evidence_weight, retrieval.vector_pools.semantic_weight, "
            "retrieval.vector_pools.sparse_weight, retrieval.vector_pools.graph_weight, "
            "retrieval.vector_pools.relation_intent.graph_top_k, "
            "retrieval.vector_pools.relation_intent.semantic_weight, "
            "retrieval.vector_pools.relation_intent.sparse_weight, "
            "retrieval.vector_pools.relation_intent.graph_weight, "
            "threshold.min_threshold, threshold.max_threshold, threshold.percentile, "
            "threshold.min_results, threshold.enable_auto_adjust。\n"
            f"objective={objective}\n"
            f"base={json.dumps(base_profile, ensure_ascii=False)}\n"
            f"failure_summary={json.dumps(failure_summary, ensure_ascii=False)}"
        )
        try:
            raw = await self._llm_call_text(prompt, request_type="A_Memorix.RetrievalTuning.ProfileSuggest")
            obj = _safe_json_loads(raw)
            if not isinstance(obj, dict):
                return []
            profiles = obj.get("profiles")
            if not isinstance(profiles, list):
                return []
            out = []
            for item in profiles[:max_count]:
                if isinstance(item, dict):
                    out.append(self._normalize_profile(item, fallback=base_profile))
            return out
        except Exception:
            return []

    def _generate_candidate_profile(
        self,
        *,
        task_id: str,
        round_index: int,
        objective: str,
        baseline_profile: Dict[str, Any],
        best_profile: Dict[str, Any],
        llm_suggestions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if llm_suggestions:
            return self._normalize_profile(llm_suggestions.pop(0), fallback=best_profile)

        rng = random.Random(f"{task_id}:{round_index}")
        base = baseline_profile if round_index % 4 == 1 else best_profile
        candidate = copy.deepcopy(base)

        if objective == "precision_priority":
            para_choices = [40, 80, 120, 180, 240, 320]
            rel_choices = [4, 8, 12, 16, 24]
            final_choices = [4, 8, 12, 16, 20, 32, 48, 64]
            alpha_choices = [0.0, 0.35, 0.50, 0.62, 0.72, 0.82, 0.90]
            pct_choices = [55, 60, 65, 72, 80]
            min_results_choices = [1, 2]
        elif objective == "recall_priority":
            para_choices = [120, 220, 300, 420, 560, 720]
            rel_choices = [8, 12, 16, 24, 32]
            final_choices = [8, 16, 32, 48, 64, 96, 128]
            alpha_choices = [0.20, 0.35, 0.45, 0.55, 0.65, 0.75]
            pct_choices = [40, 48, 55, 62]
            min_results_choices = [1, 2, 3]
        else:
            para_choices = [80, 160, 240, 320, 420, 520]
            rel_choices = [6, 10, 14, 18, 24, 30]
            final_choices = [6, 12, 20, 32, 48, 64, 80]
            alpha_choices = [0.25, 0.45, 0.55, 0.65, 0.75, 0.85]
            pct_choices = [48, 55, 62, 70]
            min_results_choices = [1, 2, 3]

        _nested_set(candidate, "retrieval.top_k_paragraphs", rng.choice(para_choices))
        _nested_set(candidate, "retrieval.top_k_relations", rng.choice(rel_choices))
        _nested_set(candidate, "retrieval.top_k_final", rng.choice(final_choices))
        _nested_set(candidate, "retrieval.alpha", rng.choice(alpha_choices))
        _nested_set(candidate, "retrieval.enable_ppr", bool(rng.choice([True, True, False])))
        _nested_set(candidate, "retrieval.ppr_alpha", rng.choice([0.72, 0.80, 0.85, 0.90, 0.94]))
        _nested_set(candidate, "retrieval.ppr_timeout_seconds", rng.choice([0.8, 1.2, 1.5, 2.0, 3.0]))
        _nested_set(candidate, "retrieval.search.smart_fallback.enabled", bool(rng.choice([True, True, False])))
        _nested_set(candidate, "retrieval.sparse.enabled", bool(rng.choice([True, True, False])))
        _nested_set(candidate, "retrieval.sparse.mode", rng.choice(["auto", "hybrid", "fallback_only"]))
        _nested_set(candidate, "retrieval.sparse.candidate_k", rng.choice([60, 80, 120, 160, 220, 320]))
        _nested_set(candidate, "retrieval.sparse.relation_candidate_k", rng.choice([40, 60, 90, 120, 180, 260]))
        _nested_set(candidate, "retrieval.fusion.method", rng.choice(["weighted_rrf", "weighted_rrf", "alpha_legacy"]))
        _nested_set(candidate, "retrieval.fusion.rrf_k", rng.choice([30, 45, 60, 75, 90]))
        vec_w = float(rng.choice([0.55, 0.65, 0.72, 0.80, 0.88]))
        _nested_set(candidate, "retrieval.fusion.vector_weight", vec_w)
        _nested_set(candidate, "retrieval.fusion.bm25_weight", 1.0 - vec_w)
        semantic_w = float(rng.choice([0.50, 0.60, 0.65, 0.72, 0.80]))
        graph_w = float(rng.choice([0.10, 0.15, 0.22, 0.30]))
        sparse_pool_w = max(0.0, 1.0 - semantic_w - graph_w)
        _nested_set(candidate, "retrieval.vector_pools.paragraph_top_k", rng.choice([12, 20, 32, 48, 64]))
        _nested_set(candidate, "retrieval.vector_pools.graph_top_k", rng.choice([24, 40, 64, 80, 120]))
        _nested_set(candidate, "retrieval.vector_pools.graph_expand_paragraph_k", rng.choice([40, 80, 120, 160]))
        _nested_set(candidate, "retrieval.vector_pools.relation_expand_per_hit", rng.choice([3, 5, 8, 12]))
        _nested_set(candidate, "retrieval.vector_pools.entity_expand_per_hit", rng.choice([4, 8, 12, 16]))
        _nested_set(candidate, "retrieval.vector_pools.relation_evidence_weight", rng.choice([0.75, 1.0, 1.25]))
        _nested_set(candidate, "retrieval.vector_pools.entity_evidence_weight", rng.choice([0.35, 0.55, 0.75]))
        _nested_set(candidate, "retrieval.vector_pools.semantic_weight", semantic_w)
        _nested_set(candidate, "retrieval.vector_pools.sparse_weight", sparse_pool_w)
        _nested_set(candidate, "retrieval.vector_pools.graph_weight", graph_w)
        ri_semantic_w = float(rng.choice([0.35, 0.45, 0.55, 0.65]))
        ri_graph_w = float(rng.choice([0.25, 0.35, 0.40, 0.50]))
        ri_sparse_w = max(0.0, 1.0 - ri_semantic_w - ri_graph_w)
        _nested_set(candidate, "retrieval.vector_pools.relation_intent.graph_top_k", rng.choice([40, 80, 120, 160]))
        _nested_set(candidate, "retrieval.vector_pools.relation_intent.semantic_weight", ri_semantic_w)
        _nested_set(candidate, "retrieval.vector_pools.relation_intent.sparse_weight", ri_sparse_w)
        _nested_set(candidate, "retrieval.vector_pools.relation_intent.graph_weight", ri_graph_w)
        _nested_set(candidate, "threshold.min_threshold", rng.choice([0.20, 0.25, 0.30, 0.35, 0.42]))
        _nested_set(candidate, "threshold.max_threshold", rng.choice([0.88, 0.92, 0.95, 0.98]))
        _nested_set(candidate, "threshold.percentile", rng.choice(pct_choices))
        _nested_set(candidate, "threshold.min_results", rng.choice(min_results_choices))
        _nested_set(candidate, "threshold.enable_auto_adjust", bool(rng.choice([True, True, False])))

        return self._normalize_profile(candidate, fallback=base)

    async def _validate_best_profile(
        self,
        *,
        baseline_profile: Dict[str, Any],
        candidate_profile: Dict[str, Any],
        holdout_cases: List[RetrievalQueryCase],
        objective: str,
        top_k_eval: int,
        query_timeout_s: float,
    ) -> Dict[str, Any]:
        cases = holdout_cases or []
        if not cases:
            return {"recommended": False, "reason": "holdout_empty"}

        stable_baseline = await self._evaluate_profile(
            profile=baseline_profile,
            cases=cases,
            objective=objective,
            top_k_eval=top_k_eval,
            query_timeout_s=query_timeout_s,
            evaluation_mode="stable",
        )
        stable_best = await self._evaluate_profile(
            profile=candidate_profile,
            cases=cases,
            objective=objective,
            top_k_eval=top_k_eval,
            query_timeout_s=query_timeout_s,
            evaluation_mode="stable",
        )
        online_baseline = await self._evaluate_profile(
            profile=baseline_profile,
            cases=cases,
            objective=objective,
            top_k_eval=top_k_eval,
            query_timeout_s=query_timeout_s,
            evaluation_mode="online_like",
        )
        online_best = await self._evaluate_profile(
            profile=candidate_profile,
            cases=cases,
            objective=objective,
            top_k_eval=top_k_eval,
            query_timeout_s=query_timeout_s,
            evaluation_mode="online_like",
        )

        base_metrics = online_baseline.get("metrics") or {}
        best_metrics = online_best.get("metrics") or {}
        score_delta = float(online_best.get("score", 0.0) or 0.0) - float(online_baseline.get("score", 0.0) or 0.0)
        p1_delta = float(best_metrics.get("precision_at_1", 0.0) or 0.0) - float(
            base_metrics.get("precision_at_1", 0.0) or 0.0
        )
        recall_delta = float(best_metrics.get("recall_at_k", 0.0) or 0.0) - float(
            base_metrics.get("recall_at_k", 0.0) or 0.0
        )
        empty_delta = float(best_metrics.get("empty_rate", 1.0) or 1.0) - float(
            base_metrics.get("empty_rate", 1.0) or 1.0
        )
        latency_delta = float(best_metrics.get("avg_elapsed_ms", 0.0) or 0.0) - float(
            base_metrics.get("avg_elapsed_ms", 0.0) or 0.0
        )
        latency_limit = max(
            MAX_LATENCY_INCREASE_MS,
            float(base_metrics.get("avg_elapsed_ms", 0.0) or 0.0) * MAX_LATENCY_INCREASE_RATIO,
        )
        checks = {
            "score_delta_ok": score_delta >= MIN_VALIDATION_SCORE_DELTA,
            "precision_drop_ok": p1_delta >= -MAX_PRECISION_DROP,
            "recall_drop_ok": recall_delta >= -MAX_RECALL_DROP,
            "empty_rate_ok": empty_delta <= MAX_EMPTY_RATE_INCREASE,
            "latency_ok": latency_delta <= latency_limit,
        }
        recommended = all(bool(value) for value in checks.values())
        return {
            "recommended": recommended,
            "reason": "" if recommended else "holdout_online_like_validation_failed",
            "checks": checks,
            "thresholds": {
                "min_score_delta": MIN_VALIDATION_SCORE_DELTA,
                "max_precision_drop": MAX_PRECISION_DROP,
                "max_recall_drop": MAX_RECALL_DROP,
                "max_empty_rate_increase": MAX_EMPTY_RATE_INCREASE,
                "max_latency_increase_ms": MAX_LATENCY_INCREASE_MS,
                "max_latency_increase_ratio": MAX_LATENCY_INCREASE_RATIO,
            },
            "deltas": {
                "score": round(score_delta, 6),
                "precision_at_1": round(p1_delta, 6),
                "recall_at_k": round(recall_delta, 6),
                "empty_rate": round(empty_delta, 6),
                "avg_elapsed_ms": round(latency_delta, 3),
            },
            "stable": {"baseline": stable_baseline, "best": stable_best},
            "online_like": {"baseline": online_baseline, "best": online_best},
            "holdout_case_count": len(cases),
        }

    def _build_runtime_config(
        self, normalized_profile: Dict[str, Any], *, evaluation_mode: str = "stable"
    ) -> Dict[str, Any]:
        raw_base = getattr(self.plugin, "config", {}) or {}
        if isinstance(raw_base, dict):
            base = {key: value for key, value in raw_base.items() if key not in _RUNTIME_CONFIG_INSTANCE_KEYS}
        else:
            base = {}
        merged = _deep_merge(base, normalized_profile)
        if str(evaluation_mode or "stable").strip().lower() == "stable":
            # stable 模式优先稳定性，避免并发访问共享 SQLite/Faiss 导致长时阻塞。
            _nested_set(merged, "retrieval.enable_parallel", False)
            # stable 模式关闭 PPR，保留可重复评估口径。
            _nested_set(merged, "retrieval.enable_ppr", False)
        merged["vector_store"] = getattr(self.plugin, "vector_store", None)
        merged["paragraph_vector_store"] = getattr(self.plugin, "paragraph_vector_store", None)
        merged["graph_vector_store"] = getattr(self.plugin, "graph_vector_store", None)
        merged["graph_store"] = getattr(self.plugin, "graph_store", None)
        merged["metadata_store"] = getattr(self.plugin, "metadata_store", None)
        merged["embedding_manager"] = getattr(self.plugin, "embedding_manager", None)
        merged["sparse_index"] = getattr(self.plugin, "sparse_index", None)
        checker = getattr(self.plugin, "_dual_vector_pools_enabled", None)
        vector_pools_ready = bool(checker()) if callable(checker) else False
        runtime_cfg = merged.get("runtime")
        merged["runtime"] = dict(runtime_cfg) if isinstance(runtime_cfg, dict) else {}
        merged["runtime"]["vector_pools_ready"] = vector_pools_ready
        merged["plugin_instance"] = self.plugin
        return merged

    async def _evaluate_profile(
        self,
        *,
        profile: Dict[str, Any],
        cases: List[RetrievalQueryCase],
        objective: str,
        top_k_eval: int,
        query_timeout_s: float,
        evaluation_mode: str = "stable",
    ) -> Dict[str, Any]:
        normalized = self._normalize_profile(profile)
        mode = str(evaluation_mode or "stable").strip().lower()
        if mode not in {"stable", "online_like"}:
            mode = "stable"
        eval_top_k = _clamp_int(top_k_eval, 20, 1, 1000)
        # 评估时让 top_k_final 参与有效召回深度，避免该参数对评分无影响。
        request_top_k = min(
            int(eval_top_k),
            _clamp_int(_nested_get(normalized, "retrieval.top_k_final", eval_top_k), eval_top_k, 1, 512),
        )
        eval_timeout_s = _clamp_float(
            query_timeout_s,
            self._eval_query_timeout_s(),
            0.01,
            120.0,
        )
        runtime_cfg = self._build_runtime_config(normalized, evaluation_mode=mode)
        runtime = build_search_runtime(
            plugin_config=runtime_cfg,
            logger_obj=logger,
            owner_tag="retrieval_tuning",
            log_prefix="[RetrievalTuning]",
        )
        if not runtime.ready:
            metrics = {
                "total_text_cases": 0,
                "precision_at_1": 0.0,
                "precision_at_3": 0.0,
                "mrr": 0.0,
                "recall_at_k": 0.0,
                "spo_relation_hit_rate": 0.0,
                "empty_rate": 1.0,
                "avg_elapsed_ms": 0.0,
                "category": {},
                "evaluation_mode": mode,
                "error": runtime.error or "runtime_not_ready",
            }
            return {
                "metrics": metrics,
                "score": -1.0,
                "avg_elapsed_ms": 0.0,
                "failure_summary": {"reason": metrics["error"]},
            }

        text_total = 0
        hit1 = 0
        hit3 = 0
        hitk = 0
        mrr_sum = 0.0
        empty_count = 0
        timeout_count = 0
        elapsed_total = 0.0
        text_failed: List[str] = []

        spo_total = 0
        spo_hit = 0
        spo_failed: List[str] = []

        category_stats: Dict[str, Dict[str, Any]] = {}
        failed_predicates = Counter()

        for case in cases:
            cat = str(case.category)
            if cat not in CATEGORIES:
                continue
            if cat not in category_stats:
                category_stats[cat] = {
                    "total": 0,
                    "hit": 0,
                    "hit_at_1": 0,
                    "hit_at_3": 0,
                    "empty": 0,
                }
            category_stats[cat]["total"] += 1

            if cat == "spo_relation":
                spo_total += 1
                spo = case.expected_spo or {}
                rows = runtime.metadata_store.get_relations(
                    subject=str(spo.get("subject") or ""),
                    predicate=str(spo.get("predicate") or ""),
                    object=str(spo.get("object") or ""),
                )
                expected_hash = str(case.expected_hashes[0]) if case.expected_hashes else ""
                ok = False
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if expected_hash and str(row.get("hash") or "") == expected_hash:
                        ok = True
                        break
                    if not expected_hash:
                        ok = True
                        break
                if ok:
                    spo_hit += 1
                    category_stats[cat]["hit"] += 1
                    category_stats[cat]["hit_at_1"] += 1
                    category_stats[cat]["hit_at_3"] += 1
                else:
                    spo_failed.append(case.case_id)
                    failed_predicates.update([str(spo.get("predicate") or "").strip() or "__empty__"])
                continue

            text_total += 1
            req = SearchExecutionRequest(
                caller="retrieval_tuning",
                query_type="search",
                query=str(case.query or "").strip(),
                top_k=int(request_top_k),
                use_threshold=True,
                enable_ppr=False if mode == "stable" else bool(_nested_get(normalized, "retrieval.enable_ppr", True)),
            )
            try:
                execution = await asyncio.wait_for(
                    SearchExecutionService.execute(
                        retriever=runtime.retriever,
                        threshold_filter=runtime.threshold_filter,
                        plugin_config=runtime_cfg,
                        request=req,
                        enforce_chat_filter=False,
                        reinforce_access=False,
                    ),
                    timeout=float(eval_timeout_s),
                )
            except asyncio.TimeoutError:
                timeout_count += 1
                empty_count += 1
                category_stats[cat]["empty"] += 1
                text_failed.append(case.case_id)
                failed_predicates.update([str(case.metadata.get("predicate") or "__unknown__")])
                continue

            if execution is None:
                empty_count += 1
                category_stats[cat]["empty"] += 1
                text_failed.append(case.case_id)
                failed_predicates.update([str(case.metadata.get("predicate") or "__unknown__")])
                continue

            elapsed_total += float(getattr(execution, "elapsed_ms", 0.0) or 0.0)

            if not bool(getattr(execution, "success", False)):
                empty_count += 1
                category_stats[cat]["empty"] += 1
                text_failed.append(case.case_id)
                failed_predicates.update([str(case.metadata.get("predicate") or "__unknown__")])
                continue

            hashes = [str(getattr(x, "hash_value", "") or "") for x in (getattr(execution, "results", None) or [])]
            if not hashes:
                empty_count += 1
                category_stats[cat]["empty"] += 1

            expected_set = set(case.expected_hashes or [])
            rank = 0
            for idx, hv in enumerate(hashes, start=1):
                if hv and hv in expected_set:
                    rank = idx
                    break

            if rank > 0:
                category_stats[cat]["hit"] += 1
                hitk += 1
                if rank <= 1:
                    hit1 += 1
                    category_stats[cat]["hit_at_1"] += 1
                if rank <= 3:
                    hit3 += 1
                    category_stats[cat]["hit_at_3"] += 1
                mrr_sum += 1.0 / float(rank)
            else:
                text_failed.append(case.case_id)
                failed_predicates.update([str(case.metadata.get("predicate") or "__unknown__")])

        p1 = (hit1 / text_total) if text_total else 0.0
        p3 = (hit3 / text_total) if text_total else 0.0
        recall = (hitk / text_total) if text_total else 0.0
        mrr = (mrr_sum / text_total) if text_total else 0.0
        spo_rate = (spo_hit / spo_total) if spo_total else 0.0
        empty_rate = (empty_count / text_total) if text_total else 1.0
        avg_elapsed = (elapsed_total / text_total) if text_total else 0.0

        metrics = {
            "total_text_cases": int(text_total),
            "precision_at_1": float(round(p1, 6)),
            "precision_at_3": float(round(p3, 6)),
            "mrr": float(round(mrr, 6)),
            "recall_at_k": float(round(recall, 6)),
            "spo_relation_hit_rate": float(round(spo_rate, 6)),
            "empty_rate": float(round(empty_rate, 6)),
            "timeout_count": int(timeout_count),
            "avg_elapsed_ms": float(round(avg_elapsed, 3)),
            "category": category_stats,
            "evaluation_mode": mode,
        }
        metrics["category_floor_penalty"] = float(round(self._category_floor_penalty(metrics, objective=objective), 6))

        score = self._score_metrics(metrics, objective=objective)
        failure_summary = {
            "text_failed_count": len(text_failed),
            "spo_failed_count": len(spo_failed),
            "failed_case_ids": text_failed[:50] + spo_failed[:50],
            "failed_by_category": {k: int(v["total"] - v["hit"]) for k, v in category_stats.items()},
            "top_failed_predicates": [
                {"predicate": key, "count": int(cnt)} for key, cnt in failed_predicates.most_common(5) if key
            ],
            "query_timeout_seconds": float(eval_timeout_s),
            "timeout_count": int(timeout_count),
            "effective_top_k": int(request_top_k),
            "evaluation_mode": mode,
            "ppr_forced_disabled": mode == "stable",
        }
        return {
            "metrics": metrics,
            "score": float(round(score, 6)),
            "avg_elapsed_ms": float(avg_elapsed),
            "failure_summary": failure_summary,
        }

    def _score_metrics(self, metrics: Dict[str, Any], *, objective: str) -> float:
        p1 = float(metrics.get("precision_at_1", 0.0) or 0.0)
        p3 = float(metrics.get("precision_at_3", 0.0) or 0.0)
        mrr = float(metrics.get("mrr", 0.0) or 0.0)
        recall = float(metrics.get("recall_at_k", 0.0) or 0.0)
        spo = float(metrics.get("spo_relation_hit_rate", 0.0) or 0.0)
        empty_rate = float(metrics.get("empty_rate", 1.0) or 1.0)
        category_penalty = metrics.get("category_floor_penalty", None)
        if category_penalty is None:
            category_penalty = self._category_floor_penalty(metrics, objective=objective)
        category_penalty = float(max(0.0, category_penalty))

        if objective == "recall_priority":
            raw = 0.15 * p1 + 0.15 * p3 + 0.15 * mrr + 0.40 * recall + 0.15 * spo
            penalty = 0.05 * empty_rate
        elif objective == "balanced":
            raw = 0.25 * p1 + 0.20 * p3 + 0.15 * mrr + 0.25 * recall + 0.15 * spo
            penalty = 0.10 * empty_rate
        else:
            raw = 0.40 * p1 + 0.20 * p3 + 0.15 * mrr + 0.15 * recall + 0.10 * spo
            penalty = 0.15 * empty_rate
        return float(raw - penalty - category_penalty)

    def _category_floor_penalty(self, metrics: Dict[str, Any], *, objective: str) -> float:
        category = metrics.get("category")
        if not isinstance(category, dict) or not category:
            return 0.0

        if objective == "recall_priority":
            floors = {"query_nl": 0.60, "query_kw": 0.48, "spo_search": 0.52, "spo_relation": 0.88}
            scale = 0.12
        elif objective == "balanced":
            floors = {"query_nl": 0.65, "query_kw": 0.52, "spo_search": 0.55, "spo_relation": 0.90}
            scale = 0.18
        else:
            floors = {"query_nl": 0.70, "query_kw": 0.55, "spo_search": 0.58, "spo_relation": 0.92}
            scale = 0.25

        weights = {"query_nl": 1.0, "query_kw": 1.1, "spo_search": 1.0, "spo_relation": 1.2}
        weighted_shortfall = 0.0
        weight_total = 0.0

        for cat, floor in floors.items():
            row = category.get(cat)
            if not isinstance(row, dict):
                continue
            total = int(row.get("total", 0) or 0)
            if total <= 0:
                continue
            hit = float(row.get("hit", 0.0) or 0.0)
            hit_rate = max(0.0, min(1.0, hit / float(max(1, total))))
            shortfall = max(0.0, float(floor) - hit_rate)
            w = float(weights.get(cat, 1.0))
            weighted_shortfall += w * shortfall
            weight_total += w

        if weight_total <= 1e-9:
            return 0.0
        return float(scale * (weighted_shortfall / weight_total))

    def _profile_diff(self, before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
        diff: Dict[str, Any] = {}

        def walk(prefix: str, left: Any, right: Any) -> None:
            if isinstance(left, dict) and isinstance(right, dict):
                keys = sorted(set(left.keys()) | set(right.keys()))
                for key in keys:
                    walk(f"{prefix}.{key}" if prefix else str(key), left.get(key), right.get(key))
                return
            if left != right:
                diff[prefix] = {"before": left, "after": right}

        walk("", before or {}, after or {})
        return diff

    def _build_report_payload(self, task: RetrievalTuningTaskRecord) -> Dict[str, Any]:
        baseline = task.baseline_metrics or {}
        best = task.best_metrics or {}

        def delta(name: str) -> float:
            return float(best.get(name, 0.0) or 0.0) - float(baseline.get(name, 0.0) or 0.0)

        return {
            "task_id": task.task_id,
            "objective": task.objective,
            "intensity": task.intensity,
            "status": task.status,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "rounds_total": task.rounds_total,
            "rounds_done": task.rounds_done,
            "best_score": task.best_score,
            "baseline_score": self._score_metrics(baseline, objective=task.objective),
            "recommended": bool(task.recommended),
            "validation_summary": task.validation_summary,
            "query_set_stats": task.query_set_stats,
            "baseline_metrics": baseline,
            "best_metrics": best,
            "deltas": {
                "precision_at_1": delta("precision_at_1"),
                "precision_at_3": delta("precision_at_3"),
                "mrr": delta("mrr"),
                "recall_at_k": delta("recall_at_k"),
                "spo_relation_hit_rate": delta("spo_relation_hit_rate"),
                "empty_rate": delta("empty_rate"),
                "timeout_count": delta("timeout_count"),
                "avg_elapsed_ms": delta("avg_elapsed_ms"),
            },
            "best_profile": task.best_profile,
            "baseline_profile": task.baseline_profile,
            "profile_diff": self._profile_diff(task.baseline_profile, task.best_profile),
            "non_tuned_retrieval_influencers": list(NON_TUNED_RETRIEVAL_INFLUENCERS),
            "apply_log": task.apply_log,
        }

    def _build_report_markdown(self, task: RetrievalTuningTaskRecord, payload: Dict[str, Any]) -> str:
        baseline = payload.get("baseline_metrics", {}) or {}
        best = payload.get("best_metrics", {}) or {}
        d = payload.get("deltas", {}) or {}
        validation = payload.get("validation_summary", {}) or {}
        lines = [
            f"# 检索调优报告（{task.task_id}）",
            "",
            "## 1. 任务信息",
            f"- 状态: {task.status}",
            f"- 目标函数: {task.objective}",
            f"- 强度: {task.intensity}",
            f"- 轮次: baseline + {task.rounds_total}",
            f"- 建议应用: {'是' if payload.get('recommended') else '否'}",
            f"- 创建时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(task.created_at))}",
            f"- 开始时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(task.started_at)) if task.started_at else '-'}",
            f"- 完成时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(task.finished_at)) if task.finished_at else '-'}",
            "",
            "## 2. 基线 vs 最优",
            f"- baseline score: {payload.get('baseline_score', 0.0):.6f}",
            f"- best score: {task.best_score:.6f}",
            f"- P@1: {baseline.get('precision_at_1', 0.0):.4f} -> {best.get('precision_at_1', 0.0):.4f} (Δ {d.get('precision_at_1', 0.0):+.4f})",
            f"- P@3: {baseline.get('precision_at_3', 0.0):.4f} -> {best.get('precision_at_3', 0.0):.4f} (Δ {d.get('precision_at_3', 0.0):+.4f})",
            f"- MRR: {baseline.get('mrr', 0.0):.4f} -> {best.get('mrr', 0.0):.4f} (Δ {d.get('mrr', 0.0):+.4f})",
            f"- Recall@K: {baseline.get('recall_at_k', 0.0):.4f} -> {best.get('recall_at_k', 0.0):.4f} (Δ {d.get('recall_at_k', 0.0):+.4f})",
            f"- SPO relation hit: {baseline.get('spo_relation_hit_rate', 0.0):.4f} -> {best.get('spo_relation_hit_rate', 0.0):.4f} (Δ {d.get('spo_relation_hit_rate', 0.0):+.4f})",
            f"- 空结果率: {baseline.get('empty_rate', 0.0):.4f} -> {best.get('empty_rate', 0.0):.4f} (Δ {d.get('empty_rate', 0.0):+.4f})",
            f"- 超时数: {int(baseline.get('timeout_count', 0) or 0)} -> {int(best.get('timeout_count', 0) or 0)} (Δ {int(d.get('timeout_count', 0) or 0):+d})",
            f"- 平均耗时(ms): {baseline.get('avg_elapsed_ms', 0.0):.2f} -> {best.get('avg_elapsed_ms', 0.0):.2f} (Δ {d.get('avg_elapsed_ms', 0.0):+.2f})",
            "",
            "## 3. 验证摘要",
            f"- holdout 样本数: {int(validation.get('holdout_case_count', 0) or 0)}",
            f"- 验证结果: {'通过' if validation.get('recommended') else '未通过'}",
            f"- 原因: {validation.get('reason', '') or '-'}",
            f"- 指标变化: {json.dumps(validation.get('deltas', {}), ensure_ascii=False)}",
            f"- 检查项: {json.dumps(validation.get('checks', {}), ensure_ascii=False)}",
            "",
            "## 4. 最优参数",
            "```json",
            json.dumps(task.best_profile, ensure_ascii=False, indent=2),
            "```",
            "",
            "## 5. 参数变化",
            "```json",
            json.dumps(payload.get("profile_diff", {}), ensure_ascii=False, indent=2),
            "```",
            "",
            "## 6. 测试集规模",
            f"- {json.dumps(task.query_set_stats, ensure_ascii=False)}",
            "",
            "## 7. 说明",
            "- 本报告仅对当前已存储图谱与向量状态有效。",
            "- 参数应用策略：默认只热应用到运行时，不自动写入 config.toml。",
            f"- 未参与自动调优但影响召回的参数: {', '.join(NON_TUNED_RETRIEVAL_INFLUENCERS)}",
        ]
        return "\n".join(lines).strip() + "\n"
