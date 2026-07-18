"""A_Memorix 的 SDK 运行时导出。"""

from __future__ import annotations

from typing import Any

from .search_runtime_initializer import SearchRuntimeBundle, SearchRuntimeInitializer, build_search_runtime

__all__ = [
    "SearchRuntimeBundle",
    "SearchRuntimeInitializer",
    "build_search_runtime",
    "KernelSearchRequest",
    "SDKMemoryKernel",
]


def __getattr__(name: str) -> Any:
    if name in {"KernelSearchRequest", "SDKMemoryKernel"}:
        from .sdk_memory_kernel import KernelSearchRequest, SDKMemoryKernel

        return {
            "KernelSearchRequest": KernelSearchRequest,
            "SDKMemoryKernel": SDKMemoryKernel,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
