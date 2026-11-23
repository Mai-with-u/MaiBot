"""数据库迁移步骤注册表。"""

from typing import Dict, List, Optional

from .exceptions import DatabaseMigrationConfigurationError
from .models import MigrationStep


class MigrationRegistry:
    """数据库迁移步骤注册表。"""

    def __init__(self, steps: Optional[List[MigrationStep]] = None) -> None:
        """初始化迁移步骤注册表。

        Args:
            steps: 初始化时要注册的迁移步骤列表。
        """
        self._steps_by_from_version: Dict[int, MigrationStep] = {}
        if steps:
            self.register_many(steps)

    def register(self, step: MigrationStep) -> None:
        """注册单个迁移步骤。

        未发布的版本可以合并为一步，已注册的升级区间不能重叠。

        Args:
            step: 待注册的迁移步骤定义。

        Raises:
            DatabaseMigrationConfigurationError: 当步骤定义冲突或版本跨度不合法时抛出。
        """
        if step.version_from in self._steps_by_from_version:
            existing_step = self._steps_by_from_version[step.version_from]
            raise DatabaseMigrationConfigurationError(
                f"版本 {step.version_from} 已存在迁移步骤: {existing_step.name}"
            )
        for registered_step in self._steps_by_from_version.values():
            if registered_step.version_to == step.version_to:
                raise DatabaseMigrationConfigurationError(
                    f"目标版本 {step.version_to} 已由迁移步骤 {registered_step.name} 占用。"
                )
            if step.version_from < registered_step.version_to and registered_step.version_from < step.version_to:
                raise DatabaseMigrationConfigurationError(f"迁移区间与 {registered_step.name} 重叠。")
        self._steps_by_from_version[step.version_from] = step

    def register_many(self, steps: List[MigrationStep]) -> None:
        """批量注册多个迁移步骤。

        Args:
            steps: 待注册的迁移步骤列表。
        """
        for step in steps:
            self.register(step)

    def get_step(self, version_from: int) -> Optional[MigrationStep]:
        """获取指定起始版本的迁移步骤。

        Args:
            version_from: 迁移步骤的起始版本号。

        Returns:
            Optional[MigrationStep]: 若存在对应步骤则返回，否则返回 ``None``。
        """
        return self._steps_by_from_version.get(version_from)

    def has_step(self, version_from: int) -> bool:
        """判断指定起始版本是否已注册迁移步骤。

        Args:
            version_from: 待检查的起始版本号。

        Returns:
            bool: 若已注册对应步骤则返回 ``True``。
        """
        return version_from in self._steps_by_from_version

    def latest_version(self) -> int:
        """返回当前注册表支持到的最新 schema 版本。

        Returns:
            int: 若注册表为空则返回 ``0``，否则返回最大目标版本号。
        """
        if not self._steps_by_from_version:
            return 0
        return max(step.version_to for step in self._steps_by_from_version.values())

    def list_steps(self) -> List[MigrationStep]:
        """按起始版本顺序返回全部迁移步骤。

        Returns:
            List[MigrationStep]: 已注册迁移步骤列表。
        """
        ordered_versions = sorted(self._steps_by_from_version)
        return [self._steps_by_from_version[version] for version in ordered_versions]
