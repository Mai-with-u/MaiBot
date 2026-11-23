"""数据库迁移计划生成器。"""

from typing import List

from .exceptions import (
    DatabaseMigrationPlanningError,
    MissingMigrationStepError,
    UnsupportedMigrationDirectionError,
)
from .models import MigrationPlan, MigrationStep
from .registry import MigrationRegistry


class MigrationPlanner:
    """数据库迁移计划生成器。"""

    def plan(
        self,
        current_version: int,
        target_version: int,
        registry: MigrationRegistry,
    ) -> MigrationPlan:
        """根据当前版本与目标版本生成迁移计划。

        Args:
            current_version: 当前数据库版本。
            target_version: 目标数据库版本。
            registry: 迁移步骤注册表。

        Returns:
            MigrationPlan: 按顺序执行的迁移计划。

        Raises:
            DatabaseMigrationPlanningError: 当版本号非法时抛出。
            MissingMigrationStepError: 当所需迁移步骤缺失时抛出。
            UnsupportedMigrationDirectionError: 当请求降级迁移时抛出。
        """
        self._validate_version(current_version, "current_version")
        self._validate_version(target_version, "target_version")

        if target_version < current_version:
            raise UnsupportedMigrationDirectionError(
                f"当前仅支持升级迁移，不支持从 {current_version} 降级到 {target_version}。"
            )
        if target_version == current_version:
            return MigrationPlan(current_version=current_version, target_version=target_version, steps=[])

        steps = self._build_steps(current_version, target_version, registry)
        return MigrationPlan(current_version=current_version, target_version=target_version, steps=steps)

    def plan_to_latest(self, current_version: int, registry: MigrationRegistry) -> MigrationPlan:
        """生成迁移到注册表最新版本的执行计划。

        Args:
            current_version: 当前数据库版本。
            registry: 迁移步骤注册表。

        Returns:
            MigrationPlan: 指向最新版本的迁移计划。
        """
        target_version = registry.latest_version()
        return self.plan(current_version=current_version, target_version=target_version, registry=registry)

    def _build_steps(
        self,
        current_version: int,
        target_version: int,
        registry: MigrationRegistry,
    ) -> List[MigrationStep]:
        """按顺序拼装迁移步骤链。

        Args:
            current_version: 当前数据库版本。
            target_version: 目标数据库版本。
            registry: 迁移步骤注册表。

        Returns:
            List[MigrationStep]: 按顺序执行的迁移步骤列表。

        Raises:
            MissingMigrationStepError: 当中间某一版本缺少迁移步骤时抛出。
        """
        planned_steps: List[MigrationStep] = []
        next_version = current_version

        while next_version < target_version:
            step = registry.get_step(next_version)
            if step is None or step.version_to > target_version:
                raise MissingMigrationStepError(
                    f"缺少从版本 {next_version} 升级到版本 {target_version} 的迁移步骤。"
                )
            planned_steps.append(step)
            next_version = step.version_to

        return planned_steps

    def _validate_version(self, version: int, field_name: str) -> None:
        """校验版本号是否合法。

        Args:
            version: 待校验的版本号。
            field_name: 当前版本号对应的字段名。

        Raises:
            DatabaseMigrationPlanningError: 当版本号非法时抛出。
        """
        if version < 0:
            raise DatabaseMigrationPlanningError(f"{field_name} 不能小于 0: {version}")
