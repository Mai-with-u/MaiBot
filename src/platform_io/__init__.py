"""导出 Platform IO 层的公开入口。

当前仍处于地基阶段，调用方应优先从这里导入共享类型和全局管理器，
而不是直接依赖更底层的私有子模块。
"""

from .manager import PlatformIOManager, get_platform_io_manager
from .route_key_factory import RouteKeyFactory
from .routing import RouteBindingConflictError, RouteTable
from .types import (
    DeliveryReceipt,
    DeliveryStatus,
    DriverDescriptor,
    DriverKind,
    InboundMessageEnvelope,
    RouteBinding,
    RouteKey,
    RouteMode,
)

__all__ = [
    "DeliveryReceipt",
    "DeliveryStatus",
    "DriverDescriptor",
    "DriverKind",
    "InboundMessageEnvelope",
    "PlatformIOManager",
    "RouteKeyFactory",
    "RouteBinding",
    "RouteBindingConflictError",
    "RouteKey",
    "RouteMode",
    "RouteTable",
    "get_platform_io_manager",
]
