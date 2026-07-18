from __future__ import annotations

from typing import Any, Dict

_runtime_kernel: Any = None


def set_runtime_kernel(kernel: Any | None) -> None:
    global _runtime_kernel
    _runtime_kernel = kernel


def get_runtime_kernel() -> Any | None:
    return _runtime_kernel


def get_runtime_components() -> Dict[str, Any]:
    kernel = get_runtime_kernel()
    if kernel is None:
        return {}
    return {
        "vector_store": getattr(kernel, "vector_store", None),
        "paragraph_vector_store": getattr(kernel, "paragraph_vector_store", None),
        "graph_vector_store": getattr(kernel, "graph_vector_store", None),
        "graph_store": getattr(kernel, "graph_store", None),
        "metadata_store": getattr(kernel, "metadata_store", None),
        "embedding_manager": getattr(kernel, "embedding_manager", None),
        "sparse_index": getattr(kernel, "sparse_index", None),
        "vector_pools_ready": bool(getattr(kernel, "_dual_vector_pools_enabled", lambda: False)()),
    }
