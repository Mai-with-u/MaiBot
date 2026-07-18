from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..sdk_memory_kernel import SDKMemoryKernel


class KernelServiceBase:
    def __init__(self, kernel: SDKMemoryKernel) -> None:
        object.__setattr__(self, "_kernel", kernel)

    def __getattribute__(self, name: str) -> Any:
        if name not in {"_kernel", "__class__", "__dict__", "__setattr__", "__getattr__", "__getattribute__"}:
            kernel = object.__getattribute__(self, "_kernel")
            kernel_attrs = getattr(kernel, "__dict__", {})
            if name in kernel_attrs:
                return kernel_attrs[name]
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._kernel, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_kernel":
            object.__setattr__(self, name, value)
            return
        setattr(self._kernel, name, value)
