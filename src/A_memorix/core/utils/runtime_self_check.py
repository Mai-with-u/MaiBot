"""A_Memorix 运行时自检辅助工具。"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import numpy as np

from src.common.logger import get_logger

logger = get_logger("A_Memorix.RuntimeSelfCheck")

_DEFAULT_SAMPLE_TEXT = "A_Memorix runtime self check"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _get_config_value(config: Any, key: str, default: Any = None) -> Any:
    getter = getattr(config, "get_config", None)
    if callable(getter):
        return getter(key, default)

    current: Any = config
    for part in key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _build_report(
    *,
    ok: bool,
    code: str,
    message: str,
    configured_dimension: int,
    requested_dimension: int,
    vector_store_dimension: int,
    detected_dimension: int,
    encoded_dimension: int,
    elapsed_ms: float,
    sample_text: str,
    vector_pools: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "ok": bool(ok),
        "code": str(code or "").strip(),
        "message": str(message or "").strip(),
        "configured_dimension": int(configured_dimension),
        "requested_dimension": int(requested_dimension),
        "vector_store_dimension": int(vector_store_dimension),
        "detected_dimension": int(detected_dimension),
        "encoded_dimension": int(encoded_dimension),
        "elapsed_ms": float(elapsed_ms),
        "sample_text": str(sample_text or ""),
        "vector_pools": vector_pools or {},
        "checked_at": time.time(),
    }


def _store_snapshot(store: Optional[Any]) -> Dict[str, Any]:
    if store is None:
        return {
            "available": False,
            "dimension": 0,
            "num_vectors": 0,
            "has_data": False,
        }
    num_vectors = _safe_int(getattr(store, "num_vectors", getattr(store, "size", 0)), 0)
    has_data = False
    checker = getattr(store, "has_data", None)
    if callable(checker):
        try:
            has_data = bool(checker())
        except Exception:
            has_data = False
    else:
        has_data = num_vectors > 0
    return {
        "available": True,
        "dimension": _safe_int(getattr(store, "dimension", 0), 0),
        "num_vectors": num_vectors,
        "has_data": bool(has_data),
    }


def _build_vector_pools_snapshot(
    *,
    config: Any,
    vector_store: Optional[Any],
    paragraph_vector_store: Optional[Any],
    graph_vector_store: Optional[Any],
) -> Dict[str, Any]:
    configured = str(_get_config_value(config, "retrieval.vector_pools.mode", "dual") or "dual").strip().lower()
    runtime_ready = bool(_get_config_value(config, "runtime.vector_pools_ready", False))
    effective = "dual" if configured == "dual" and runtime_ready else "single"
    return {
        "configured_mode": configured if configured in {"single", "dual"} else "single",
        "effective_mode": effective,
        "ready": runtime_ready,
        "single_pool": _store_snapshot(vector_store),
        "paragraph_pool": _store_snapshot(paragraph_vector_store),
        "graph_pool": _store_snapshot(graph_vector_store),
    }


def _normalize_encoded_vector(encoded: Any) -> np.ndarray:
    if encoded is None:
        raise ValueError("embedding encode returned None")

    if isinstance(encoded, np.ndarray):
        array = encoded
    else:
        array = np.asarray(encoded, dtype=np.float32)

    if array.ndim == 2:
        if array.shape[0] != 1:
            raise ValueError(f"embedding encode returned batched output: shape={tuple(array.shape)}")
        array = array[0]

    if array.ndim != 1:
        raise ValueError(f"embedding encode returned invalid ndim={array.ndim}")
    if array.size <= 0:
        raise ValueError("embedding encode returned empty vector")
    if not np.all(np.isfinite(array)):
        raise ValueError("embedding encode returned non-finite values")
    return array.astype(np.float32, copy=False)


def _get_requested_dimension(embedding_manager: Optional[Any], fallback: int = 0) -> int:
    if embedding_manager is None:
        return int(fallback)
    getter = getattr(embedding_manager, "get_requested_dimension", None)
    if callable(getter):
        return _safe_int(getter(), fallback)
    getter = getattr(embedding_manager, "get_embedding_dimension", None)
    if callable(getter):
        return _safe_int(getter(), fallback)
    return int(fallback)


async def run_embedding_runtime_self_check(
    *,
    config: Any,
    vector_store: Optional[Any],
    embedding_manager: Optional[Any],
    paragraph_vector_store: Optional[Any] = None,
    graph_vector_store: Optional[Any] = None,
    sample_text: str = _DEFAULT_SAMPLE_TEXT,
) -> Dict[str, Any]:
    """探测真实 embedding 链路，并与运行时存储维度比较。"""
    configured_dimension = _safe_int(_get_config_value(config, "embedding.dimension", 0), 0)
    vector_store_dimension = _safe_int(getattr(vector_store, "dimension", 0), 0)
    requested_dimension = _get_requested_dimension(embedding_manager, configured_dimension)
    vector_pools = _build_vector_pools_snapshot(
        config=config,
        vector_store=vector_store,
        paragraph_vector_store=paragraph_vector_store,
        graph_vector_store=graph_vector_store,
    )

    if vector_store is None or embedding_manager is None:
        return _build_report(
            ok=False,
            code="runtime_components_missing",
            message="vector_store 或 embedding_manager 未初始化",
            configured_dimension=configured_dimension,
            requested_dimension=requested_dimension,
            vector_store_dimension=vector_store_dimension,
            detected_dimension=0,
            encoded_dimension=0,
            elapsed_ms=0.0,
            sample_text=sample_text,
            vector_pools=vector_pools,
        )

    start = time.perf_counter()
    detected_dimension = 0
    encoded_dimension = 0
    try:
        detected_dimension = _safe_int(await embedding_manager._detect_dimension(), 0)
        requested_dimension = _get_requested_dimension(embedding_manager, detected_dimension or configured_dimension)
        encoded = await embedding_manager.encode(sample_text)
        encoded_array = _normalize_encoded_vector(encoded)
        encoded_dimension = int(encoded_array.shape[0])
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.warning(f"embedding runtime self-check failed: {exc}")
        return _build_report(
            ok=False,
            code="embedding_probe_failed",
            message=f"embedding probe failed: {exc}",
            configured_dimension=configured_dimension,
            requested_dimension=requested_dimension,
            vector_store_dimension=vector_store_dimension,
            detected_dimension=detected_dimension,
            encoded_dimension=encoded_dimension,
            elapsed_ms=elapsed_ms,
            sample_text=sample_text,
            vector_pools=vector_pools,
        )

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    expected_dimension = vector_store_dimension or configured_dimension or detected_dimension
    if expected_dimension <= 0:
        return _build_report(
            ok=False,
            code="invalid_expected_dimension",
            message="无法确定期望 embedding 维度",
            configured_dimension=configured_dimension,
            requested_dimension=requested_dimension,
            vector_store_dimension=vector_store_dimension,
            detected_dimension=detected_dimension,
            encoded_dimension=encoded_dimension,
            elapsed_ms=elapsed_ms,
            sample_text=sample_text,
            vector_pools=vector_pools,
        )

    if encoded_dimension != expected_dimension:
        msg = f"embedding 真实输出维度与当前向量存储不一致: expected={expected_dimension}, encoded={encoded_dimension}"
        logger.error(msg)
        return _build_report(
            ok=False,
            code="embedding_dimension_mismatch",
            message=msg,
            configured_dimension=configured_dimension,
            requested_dimension=requested_dimension,
            vector_store_dimension=vector_store_dimension,
            detected_dimension=detected_dimension,
            encoded_dimension=encoded_dimension,
            elapsed_ms=elapsed_ms,
            sample_text=sample_text,
            vector_pools=vector_pools,
        )

    return _build_report(
        ok=True,
        code="ok",
        message="embedding runtime self-check passed",
        configured_dimension=configured_dimension,
        requested_dimension=requested_dimension,
        vector_store_dimension=vector_store_dimension,
        detected_dimension=detected_dimension,
        encoded_dimension=encoded_dimension,
        elapsed_ms=elapsed_ms,
        sample_text=sample_text,
        vector_pools=vector_pools,
    )


async def ensure_runtime_self_check(
    plugin_or_config: Any,
    *,
    force: bool = False,
    sample_text: str = _DEFAULT_SAMPLE_TEXT,
) -> Dict[str, Any]:
    """执行运行时自检，或复用已有的自检报告缓存。"""
    if plugin_or_config is None:
        return _build_report(
            ok=False,
            code="missing_plugin_or_config",
            message="plugin/config unavailable",
            configured_dimension=0,
            requested_dimension=0,
            vector_store_dimension=0,
            detected_dimension=0,
            encoded_dimension=0,
            elapsed_ms=0.0,
            sample_text=sample_text,
            vector_pools={},
        )

    cache = getattr(plugin_or_config, "_runtime_self_check_report", None)
    if isinstance(cache, dict) and cache and not force:
        return cache

    report = await run_embedding_runtime_self_check(
        config=getattr(plugin_or_config, "config", plugin_or_config),
        vector_store=getattr(plugin_or_config, "vector_store", None)
        if not isinstance(plugin_or_config, dict)
        else plugin_or_config.get("vector_store"),
        embedding_manager=getattr(plugin_or_config, "embedding_manager", None)
        if not isinstance(plugin_or_config, dict)
        else plugin_or_config.get("embedding_manager"),
        paragraph_vector_store=getattr(plugin_or_config, "paragraph_vector_store", None)
        if not isinstance(plugin_or_config, dict)
        else plugin_or_config.get("paragraph_vector_store"),
        graph_vector_store=getattr(plugin_or_config, "graph_vector_store", None)
        if not isinstance(plugin_or_config, dict)
        else plugin_or_config.get("graph_vector_store"),
        sample_text=sample_text,
    )
    if not isinstance(plugin_or_config, dict):
        plugin_or_config._runtime_self_check_report = report
    return report
