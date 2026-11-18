"""数据库迁移编排器。"""

from typing import Callable, Optional

from sqlalchemy.engine import Connection, Engine

from src.common.logger import get_logger
from src.common.runtime_context import report_phase

from .exceptions import DatabaseMigrationExecutionError
from .models import DatabaseMigrationState, MigrationExecutionContext, MigrationPlan
from .planner import MigrationPlanner
from .progress import BaseMigrationProgressReporter, create_rich_migration_progress_reporter
from .registry import MigrationRegistry
from .resolver import SchemaVersionResolver
from .version_store import SQLiteUserVersionStore

logger = get_logger("database_migration")


class DatabaseMigrationManager:
    """数据库迁移编排器。

    该类只负责基础设施层面的编排工作，包括：
        1. 解析当前数据库版本；
        2. 生成迁移计划；
        3. 顺序执行已注册迁移步骤；
        4. 在每一步成功后更新 ``user_version``。

    当前模块不内置任何业务迁移步骤，也不会自动接入项目启动流程。
    """

    def __init__(
        self,
        engine: Engine,
        registry: Optional[MigrationRegistry] = None,
        planner: Optional[MigrationPlanner] = None,
        resolver: Optional[SchemaVersionResolver] = None,
        version_store: Optional[SQLiteUserVersionStore] = None,
        progress_reporter_factory: Optional[Callable[[], BaseMigrationProgressReporter]] = None,
    ) -> None:
        """初始化数据库迁移编排器。

        Args:
            engine: 目标数据库引擎。
            registry: 迁移步骤注册表。
            planner: 迁移计划生成器。
            resolver: 数据库版本解析器。
            version_store: 版本存储器。
            progress_reporter_factory: 迁移进度上报器工厂。
        """
        self.engine = engine
        self.registry = registry or MigrationRegistry()
        self.planner = planner or MigrationPlanner()
        self.resolver = resolver or SchemaVersionResolver()
        self.version_store = version_store or SQLiteUserVersionStore()
        self.progress_reporter_factory = progress_reporter_factory or create_rich_migration_progress_reporter

    def describe_state(self, target_version: Optional[int] = None) -> DatabaseMigrationState:
        """描述当前数据库的迁移状态。

        Args:
            target_version: 目标数据库版本；未提供时使用注册表中声明的最新版本。

        Returns:
            DatabaseMigrationState: 当前数据库迁移状态。
        """
        with self.engine.connect() as connection:
            resolved_version = self.resolver.resolve(connection)

        effective_target_version = self._resolve_target_version(target_version)
        migration_plan = self.planner.plan(
            current_version=resolved_version.version,
            target_version=effective_target_version,
            registry=self.registry,
        )
        return DatabaseMigrationState(
            resolved_version=resolved_version,
            target_version=effective_target_version,
            plan=migration_plan,
        )

    def plan(self, target_version: Optional[int] = None) -> MigrationPlan:
        """生成当前数据库的迁移计划。

        Args:
            target_version: 目标数据库版本；未提供时使用注册表中声明的最新版本。

        Returns:
            MigrationPlan: 当前数据库对应的迁移计划。
        """
        return self.describe_state(target_version=target_version).plan

    def migrate(self, target_version: Optional[int] = None) -> MigrationPlan:
        """执行迁移计划。

        注意：
            若当前数据库是通过结构探测得出的版本，且计划为空，本方法不会自动把该
            版本写回 ``user_version``。这样做是为了避免在尚未明确接入策略前引入隐式
            副作用。

        Args:
            target_version: 目标数据库版本；未提供时使用注册表中声明的最新版本。

        Returns:
            MigrationPlan: 已执行的迁移计划。

        Raises:
            DatabaseMigrationExecutionError: 当迁移步骤执行失败时抛出。
        """
        migration_state = self.describe_state(target_version=target_version)
        migration_plan = migration_state.plan
        if migration_plan.is_empty():
            logger.info("数据库迁移计划为空，跳过执行。")
            return migration_plan

        current_version = migration_state.resolved_version.version
        total_steps = migration_plan.step_count()
        for step_index, step in enumerate(migration_plan.steps, start=1):
            logger.info(
                f"开始执行数据库迁移步骤: {step.name} ({step.version_from} -> {step.version_to})"
            )
            # 大数据量迁移可能持续很久，宿主据此显示进度，而不是把它当成启动卡住。
            report_phase("migrating", f"数据库迁移 {step_index}/{total_steps}：{step.name}")
            try:
                with self.progress_reporter_factory() as progress_reporter:
                    if step.transactional:
                        with self.engine.begin() as connection:
                            execution_context = self._build_execution_context(
                                connection=connection,
                                current_version=current_version,
                                migration_plan=migration_plan,
                                step_index=step_index,
                                step_name=step.name,
                                total_steps=total_steps,
                                progress_reporter=progress_reporter,
                            )
                            step.run(execution_context)
                            self.version_store.write_version(connection, step.version_to)
                    else:
                        with self.engine.connect() as connection:
                            execution_context = self._build_execution_context(
                                connection=connection,
                                current_version=current_version,
                                migration_plan=migration_plan,
                                step_index=step_index,
                                step_name=step.name,
                                total_steps=total_steps,
                                progress_reporter=progress_reporter,
                            )
                            step.run(execution_context)
                            self.version_store.write_version(connection, step.version_to)
                            connection.commit()
            except Exception as exc:
                logger.exception(
                    f"数据库迁移步骤执行失败，将输出完整 traceback: "
                    f"{step.name} ({step.version_from} -> {step.version_to})"
                )
                raise DatabaseMigrationExecutionError(
                    f"执行迁移步骤 {step.name} ({step.version_from} -> {step.version_to}) 失败。"
                ) from exc
            current_version = step.version_to
            logger.info(f"数据库迁移步骤执行完成: {step.name}，当前版本已更新为 {current_version}")

        report_phase("starting", "数据库迁移完成")
        return migration_plan

    def _resolve_target_version(self, target_version: Optional[int]) -> int:
        """解析最终目标版本号。

        Args:
            target_version: 调用方显式指定的目标版本。

        Returns:
            int: 最终用于规划和执行的目标版本号。
        """
        if target_version is not None:
            return target_version
        return self.registry.latest_version()

    def _build_execution_context(
        self,
        connection: Connection,
        current_version: int,
        migration_plan: MigrationPlan,
        step_index: int,
        step_name: str,
        total_steps: int,
        progress_reporter: BaseMigrationProgressReporter,
    ) -> MigrationExecutionContext:
        """构建单个迁移步骤的执行上下文。

        Args:
            connection: 当前迁移步骤使用的数据库连接。
            current_version: 当前数据库版本。
            migration_plan: 当前迁移计划。
            step_index: 当前步骤序号，从 ``1`` 开始。
            step_name: 当前步骤名称。
            total_steps: 计划总步骤数。
            progress_reporter: 当前步骤使用的进度上报器。

        Returns:
            MigrationExecutionContext: 当前步骤的执行上下文对象。
        """
        return MigrationExecutionContext(
            connection=connection,
            current_version=current_version,
            target_version=migration_plan.target_version,
            step_index=step_index,
            step_name=step_name,
            total_steps=total_steps,
            progress_reporter=progress_reporter,
        )
