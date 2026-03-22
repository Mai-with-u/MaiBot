"""
统一检索执行服务。

用于收敛 Action/Tool 在 search/time 上的核心执行流程，避免重复实现。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.common.logger import get_logger

from ..retrieval import TemporalQueryOptions
from .search_postprocess import (
    apply_safe_content_dedup,
    maybe_apply_smart_path_fallback,
)
from .time_parser import parse_query_time_range

logger = get_logger("A_Memorix.SearchExecutionService")


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


def _sanitize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


@dataclass
class SearchExecutionRequest:
    caller: str
    stream_id: Optional[str] = None
    group_id: Optional[str] = None
    user_id: Optional[str] = None
    query_type: str = "search"  # search|time|hybrid
    query: str = ""
    top_k: Optional[int] = None
    time_from: Optional[str] = None
    time_to: Optional[str] = None
    person: Optional[str] = None
    source: Optional[str] = None
    use_threshold: bool = True
    enable_ppr: bool = True


@dataclass
class SearchExecutionResult:
    success: bool
    error: str = ""
    query_type: str = "search"
    query: str = ""
    top_k: int = 10
    time_from: Optional[str] = None
    time_to: Optional[str] = None
    person: Optional[str] = None
    source: Optional[str] = None
    temporal: Optional[TemporalQueryOptions] = None
    results: List[Any] = field(default_factory=list)
    elapsed_ms: float = 0.0
    chat_filtered: bool = False
    dedup_hit: bool = False

    @property
    def count(self) -> int:
        return len(self.results)


class SearchExecutionService:
    """统一检索执行服务。"""

    @staticmethod
    def _resolve_plugin_instance(plugin_config: Optional[dict]) -> Optional[Any]:
        if isinstance(plugin_config, dict):
            plugin_instance = plugin_config.get("plugin_instance")
            if plugin_instance is not None:
                return plugin_instance

        try:
            from ...plugin import AMemorixPlugin

            return getattr(AMemorixPlugin, "get_global_instance", lambda: None)()
        except Exception:
            return None

    @staticmethod
    def _normalize_query_type(raw_query_type: str) -> str:
        return _sanitize_text(raw_query_type).lower() or "search"

    @staticmethod
    def _resolve_runtime_component(
        plugin_config: Optional[dict],
        plugin_instance: Optional[Any],
        key: str,
    ) -> Optional[Any]:
        if isinstance(plugin_config, dict):
            value = plugin_config.get(key)
            if value is not None:
                return value
        if plugin_instance is not None:
            value = getattr(plugin_instance, key, None)
            if value is not None:
                return value
        return None

    @staticmethod
    def _resolve_top_k(
        plugin_config: Optional[dict],
        query_type: str,
        top_k_raw: Optional[Any],
    ) -> Tuple[bool, int, str]:
        temporal_default_top_k = int(
            _get_config_value(plugin_config, "retrieval.temporal.default_top_k", 10)
        )
        default_top_k = temporal_default_top_k if query_type in {"time", "hybrid"} else 10
        if top_k_raw is None:
            return True, max(1, min(50, default_top_k)), ""
        try:
            top_k = int(top_k_raw)
        except (TypeError, ValueError):
            return False, 0, "top_k 参数必须为整数"
        return True, max(1, min(50, top_k)), ""

    @staticmethod
    def _build_temporal(
        plugin_config: Optional[dict],
        query_type: str,
        time_from_raw: Optional[str],
        time_to_raw: Optional[str],
        person: Optional[str],
        source: Optional[str],
    ) -> Tuple[bool, Optional[TemporalQueryOptions], str]:
        if query_type not in {"time", "hybrid"}:
            return True, None, ""

        temporal_enabled = bool(_get_config_value(plugin_config, "retrieval.temporal.enabled", True))
        if not temporal_enabled:
            return False, None, "时序检索已禁用（retrieval.temporal.enabled=false）"

        if not time_from_raw and not time_to_raw:
            return False, None, "time/hybrid 模式至少需要 time_from 或 time_to"

        try:
            ts_from, ts_to = parse_query_time_range(
                str(time_from_raw) if time_from_raw is not None else None,
                str(time_to_raw) if time_to_raw is not None else None,
            )
        except ValueError as e:
            return False, None, f"时间参数错误: {e}"

        temporal = TemporalQueryOptions(
            time_from=ts_from,
            time_to=ts_to,
            person=_sanitize_text(person) or None,
            source=_sanitize_text(source) or None,
            allow_created_fallback=bool(
                _get_config_value(plugin_config, "retrieval.temporal.allow_created_fallback", True)
            ),
            candidate_multiplier=int(
                _get_config_value(plugin_config, "retrieval.temporal.candidate_multiplier", 8)
            ),
            max_scan=int(_get_config_value(plugin_config, "retrieval.temporal.max_scan", 1000)),
        )
        return True, temporal, ""

    @staticmethod
    def _build_request_key(
        request: SearchExecutionRequest,
        query_type: str,
        top_k: int,
        temporal: Optional[TemporalQueryOptions],
    ) -> str:
        payload = {
            "stream_id": _sanitize_text(request.stream_id),
            "query_type": query_type,
            "query": _sanitize_text(request.query),
            "time_from": _sanitize_text(request.time_from),
            "time_to": _sanitize_text(request.time_to),
            "time_from_ts": temporal.time_from if temporal else None,
            "time_to_ts": temporal.time_to if temporal else None,
            "person": _sanitize_text(request.person),
            "source": _sanitize_text(request.source),
            "top_k": int(top_k),
            "use_threshold": bool(request.use_threshold),
            "enable_ppr": bool(request.enable_ppr),
        }
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(payload_json.encode("utf-8")).hexdigest()

    @staticmethod
    async def execute(
        *,
        retriever: Any,
        threshold_filter: Optional[Any],
        plugin_config: Optional[dict],
        request: SearchExecutionRequest,
        enforce_chat_filter: bool = True,
        reinforce_access: bool = True,
    ) -> SearchExecutionResult:
        if retriever is None:
            return SearchExecutionResult(success=False, error="知识检索器未初始化")

        query_type = SearchExecutionService._normalize_query_type(request.query_type)
        query = _sanitize_text(request.query)
        if query_type not in {"search", "time", "hybrid"}:
            return SearchExecutionResult(
                success=False,
                error=f"query_type 无效: {query_type}（仅支持 search/time/hybrid）",
            )

        if query_type in {"search", "hybrid"} and not query:
            return SearchExecutionResult(
                success=False,
                error="search/hybrid 模式必须提供 query",
            )

        top_k_ok, top_k, top_k_error = SearchExecutionService._resolve_top_k(
            plugin_config, query_type, request.top_k
        )
        if not top_k_ok:
            return SearchExecutionResult(success=False, error=top_k_error)

        temporal_ok, temporal, temporal_error = SearchExecutionService._build_temporal(
            plugin_config=plugin_config,
            query_type=query_type,
            time_from_raw=request.time_from,
            time_to_raw=request.time_to,
            person=request.person,
            source=request.source,
        )
        if not temporal_ok:
            return SearchExecutionResult(success=False, error=temporal_error)

        plugin_instance = SearchExecutionService._resolve_plugin_instance(plugin_config)
        if (
            enforce_chat_filter
            and plugin_instance is not None
            and hasattr(plugin_instance, "is_chat_enabled")
        ):
            if not plugin_instance.is_chat_enabled(
                stream_id=request.stream_id,
                group_id=request.group_id,
                user_id=request.user_id,
            ):
                logger.info(
                    "检索请求被聊天过滤拦截: "
                    f"caller={request.caller}, "
                    f"stream_id={request.stream_id}"
                )
                return SearchExecutionResult(
                    success=True,
                    query_type=query_type,
                    query=query,
                    top_k=top_k,
                    time_from=request.time_from,
                    time_to=request.time_to,
                    person=request.person,
                    source=request.source,
                    temporal=temporal,
                    results=[],
                    elapsed_ms=0.0,
                    chat_filtered=True,
                    dedup_hit=False,
                )

        request_key = SearchExecutionService._build_request_key(
            request=request,
            query_type=query_type,
            top_k=top_k,
            temporal=temporal,
        )

        async def _executor() -> Dict[str, Any]:
            original_ppr = bool(getattr(retriever.config, "enable_ppr", True))
            setattr(retriever.config, "enable_ppr", bool(request.enable_ppr))
            started_at = time.time()
            try:
                retrieved = await retriever.retrieve(
                    query=query,
                    top_k=top_k,
                    temporal=temporal,
                )

                should_apply_threshold = bool(request.use_threshold) and threshold_filter is not None
                if (
                    query_type == "time"
                    and not query
                    and bool(
                        _get_config_value(
                            plugin_config,
                            "retrieval.time.skip_threshold_when_query_empty",
                            True,
                        )
                    )
                ):
                    should_apply_threshold = False

                if should_apply_threshold:
                    retrieved = threshold_filter.filter(retrieved)

                if (
                    reinforce_access
                    and plugin_instance is not None
                    and hasattr(plugin_instance, "reinforce_access")
                ):
                    relation_hashes = [
                        item.hash_value
                        for item in retrieved
                        if getattr(item, "result_type", "") == "relation"
                    ]
                    if relation_hashes:
                        await plugin_instance.reinforce_access(relation_hashes)

                if query_type == "search":
                    graph_store = SearchExecutionService._resolve_runtime_component(
                        plugin_config, plugin_instance, "graph_store"
                    )
                    metadata_store = SearchExecutionService._resolve_runtime_component(
                        plugin_config, plugin_instance, "metadata_store"
                    )
                    fallback_enabled = bool(
                        _get_config_value(
                            plugin_config,
                            "retrieval.search.smart_fallback.enabled",
                            True,
                        )
                    )
                    fallback_threshold = float(
                        _get_config_value(
                            plugin_config,
                            "retrieval.search.smart_fallback.threshold",
                            0.6,
                        )
                    )
                    retrieved, fallback_triggered, fallback_added = maybe_apply_smart_path_fallback(
                        query=query,
                        results=list(retrieved),
                        graph_store=graph_store,
                        metadata_store=metadata_store,
                        enabled=fallback_enabled,
                        threshold=fallback_threshold,
                    )
                    if fallback_triggered:
                        logger.info(
                            "metric.smart_fallback_triggered_count=1 "
                            f"caller={request.caller} "
                            f"added={fallback_added}"
                        )

                dedup_enabled = bool(
                    _get_config_value(
                        plugin_config,
                        "retrieval.search.safe_content_dedup.enabled",
                        True,
                    )
                )
                if dedup_enabled:
                    retrieved, removed_count = apply_safe_content_dedup(list(retrieved))
                    if removed_count > 0:
                        logger.info(
                            f"metric.safe_dedup_removed_count={removed_count} "
                            f"caller={request.caller}"
                        )

                elapsed_ms = (time.time() - started_at) * 1000.0
                return {"results": retrieved, "elapsed_ms": elapsed_ms}
            finally:
                setattr(retriever.config, "enable_ppr", original_ppr)

        dedup_hit = False
        try:
            # 调优评估需要逐轮真实执行，且应避免额外 dedup 锁竞争。
            bypass_request_dedup = str(request.caller or "").strip().lower() == "retrieval_tuning"
            if (
                not bypass_request_dedup
                and
                plugin_instance is not None
                and hasattr(plugin_instance, "execute_request_with_dedup")
            ):
                dedup_hit, payload = await plugin_instance.execute_request_with_dedup(
                    request_key,
                    _executor,
                )
            else:
                payload = await _executor()
        except Exception as e:
            return SearchExecutionResult(success=False, error=f"知识检索失败: {e}")

        if dedup_hit:
            logger.info(f"metric.search_execution_dedup_hit_count=1 caller={request.caller}")

        return SearchExecutionResult(
            success=True,
            query_type=query_type,
            query=query,
            top_k=top_k,
            time_from=request.time_from,
            time_to=request.time_to,
            person=request.person,
            source=request.source,
            temporal=temporal,
            results=payload.get("results", []),
            elapsed_ms=float(payload.get("elapsed_ms", 0.0)),
            chat_filtered=False,
            dedup_hit=bool(dedup_hit),
        )

    @staticmethod
    def to_serializable_results(results: List[Any]) -> List[Dict[str, Any]]:
        serialized: List[Dict[str, Any]] = []
        for item in results:
            metadata = dict(getattr(item, "metadata", {}) or {})
            if "time_meta" not in metadata:
                metadata["time_meta"] = {}
            serialized.append(
                {
                    "hash": getattr(item, "hash_value", ""),
                    "type": getattr(item, "result_type", ""),
                    "score": float(getattr(item, "score", 0.0)),
                    "content": getattr(item, "content", ""),
                    "metadata": metadata,
                }
            )
        return serialized
