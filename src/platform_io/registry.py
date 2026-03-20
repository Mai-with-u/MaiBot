"""提供 Platform IO 的驱动注册与查询能力。"""

from typing import Dict, List, Optional

from src.platform_io.drivers.base import PlatformIODriver
from src.platform_io.types import DriverKind


class DriverRegistry:
    """集中保存已注册的 Platform IO 驱动，并提供基础查询接口。"""

    def __init__(self) -> None:
        """初始化一个空的驱动注册表。"""
        self._drivers: Dict[str, PlatformIODriver] = {}

    def register(self, driver: PlatformIODriver) -> None:
        """注册一个驱动实例。

        Args:
            driver: 要注册的驱动实例。

        Raises:
            ValueError: 当驱动 ID 已经存在时抛出。
        """
        if driver.driver_id in self._drivers:
            raise ValueError(f"驱动 {driver.driver_id} 已注册")
        self._drivers[driver.driver_id] = driver

    def unregister(self, driver_id: str) -> Optional[PlatformIODriver]:
        """按驱动 ID 注销一个驱动。

        Args:
            driver_id: 要移除的驱动 ID。

        Returns:
            Optional[PlatformIODriver]: 若驱动存在，则返回被移除的驱动实例。
        """
        return self._drivers.pop(driver_id, None)

    def get(self, driver_id: str) -> Optional[PlatformIODriver]:
        """按驱动 ID 获取驱动实例。

        Args:
            driver_id: 要查询的驱动 ID。

        Returns:
            Optional[PlatformIODriver]: 若存在匹配驱动，则返回该驱动实例。
        """
        return self._drivers.get(driver_id)

    def list(self, *, kind: Optional[DriverKind] = None, platform: Optional[str] = None) -> List[PlatformIODriver]:
        """列出已注册驱动，并支持可选过滤。

        Args:
            kind: 可选的驱动类型过滤条件。
            platform: 可选的平台名称过滤条件。

        Returns:
            List[PlatformIODriver]: 符合过滤条件的驱动列表。
        """
        drivers = list(self._drivers.values())
        if kind is not None:
            drivers = [driver for driver in drivers if driver.descriptor.kind == kind]
        if platform is not None:
            drivers = [driver for driver in drivers if driver.descriptor.platform == platform]
        return drivers

    def clear(self) -> None:
        """清空全部已注册驱动。"""
        self._drivers.clear()
