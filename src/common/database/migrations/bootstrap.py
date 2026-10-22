"""数据库迁移启动桥接层。"""

from typing import Optional

from sqlalchemy.engine import Engine

from src.common.logger import get_logger

from .builtin import (
    LATEST_SCHEMA_VERSION,
    build_default_migration_registry,
    build_default_schema_version_resolver,
)
from .exceptions import DatabaseMigrationExecutionError
from .manager import DatabaseMigrationManager
from .models import DatabaseMigrationState, MigrationPlan, ResolvedSchemaVersion, SchemaVersionSource
from .registry import MigrationRegistry
from .resolver import SchemaVersionResolver
from .version_store import SQLiteUserVersionStore

logger = get_logger("database_migration")


class DatabaseMigrationBootstrapper:
    """数据库迁移启动桥接器。

    该桥接器负责把数据库迁移基础设施接入现有启动流程，同时保持如下约束：
        1. 若数据库为空，则直接交给当前模型定义建出最新结构；
        2. 若数据库版本高于当前代码支持的最新版本，则立即终止启动；
        3. 若存在待执行迁移步骤，则在正常建表流程之前先执行迁移；
        4. 若数据库已是最新结构但尚未写入 ``user_version``，则在建表后补写版本号。
    """

    def __init__(
        self,
        manager: DatabaseMigrationManager,
        latest_schema_version: int = LATEST_SCHEMA_VERSION,
    ) -> None:
        """初始化数据库迁移启动桥接器。

        Args:
            manager: 数据库迁移编排器。
            latest_schema_version: 当前代码支持的最新 schema 版本号。
        """
        self.manager = manager
        self.latest_schema_version = latest_schema_version

    def prepare_database(self) -> DatabaseMigrationState:
        """为数据库初始化阶段准备迁移状态。

        Returns:
            DatabaseMigrationState: 迁移准备完成后的数据库状态。

        Raises:
            DatabaseMigrationExecutionError: 当数据库版本高于当前代码支持版本时抛出。
        """
        with self.manager.engine.connect() as connection:
            resolved_version = self.manager.resolver.resolve(connection)

        if resolved_version.version > self.latest_schema_version:
            raise DatabaseMigrationExecutionError(
                "当前数据库版本高于代码内注册的最新迁移版本，已拒绝继续启动。"
                f" 数据库版本={resolved_version.version}，代码支持版本={self.latest_schema_version}"
            )

        if resolved_version.source == SchemaVersionSource.EMPTY_DATABASE:
            logger.info(
                "检测到空数据库，将直接根据当前模型创建最新结构。"
                f" 目标版本={self.latest_schema_version}"
            )
            return self._build_noop_state(
                current_version=resolved_version.version,
                target_version=self.latest_schema_version,
                resolved_state=resolved_version,
            )

        migration_state = self.manager.describe_state(target_version=self.latest_schema_version)
        if not migration_state.requires_migration():
            logger.debug(
                f"数据库 schema 已是目标版本，无需迁移。当前版本={migration_state.resolved_version.version}"
            )
            return migration_state

        logger.info(
            "检测到数据库需要迁移，"
            f" 当前版本={migration_state.resolved_version.version}，目标版本={migration_state.target_version}"
        )
        self.manager.migrate(target_version=self.latest_schema_version)
        return self.manager.describe_state(target_version=self.latest_schema_version)

    def finalize_database(self, migration_state: DatabaseMigrationState) -> None:
        """在数据库初始化末尾补写最终 schema 版本号。

        该方法主要负责两类场景：
            1. 空库首次建表完成后，将 ``user_version`` 写入为最新版本；
            2. 已是最新结构但此前未写入 ``user_version`` 的数据库，补写版本号。

        Args:
            migration_state: 初始化前解析得到的迁移状态。
        """
        if migration_state.requires_migration():
            return
        if migration_state.target_version <= 0:
            return
        if migration_state.resolved_version.source == SchemaVersionSource.PRAGMA:
            return

        with self.manager.engine.begin() as connection:
            self.manager.version_store.write_version(connection, migration_state.target_version)

        logger.info(
            "数据库 schema 版本写入完成。"
            f" 来源={migration_state.resolved_version.source.value}，"
            f" 写入版本={migration_state.target_version}"
        )

    def _build_noop_state(
        self,
        current_version: int,
        target_version: int,
        resolved_state: ResolvedSchemaVersion,
    ) -> DatabaseMigrationState:
        """构建无迁移动作的数据库状态对象。

        Args:
            current_version: 当前数据库版本号。
            target_version: 当前初始化流程期望达到的目标版本号。
            resolved_state: 已解析的数据库版本状态。

        Returns:
            DatabaseMigrationState: 不包含迁移步骤的状态对象。
        """
        return DatabaseMigrationState(
            resolved_version=resolved_state,
            target_version=target_version,
            plan=MigrationPlan(current_version=current_version, target_version=target_version, steps=[]),
        )


def create_database_migration_bootstrapper(
    engine: Engine,
    registry: Optional[MigrationRegistry] = None,
    resolver: Optional[SchemaVersionResolver] = None,
    version_store: Optional[SQLiteUserVersionStore] = None,
    latest_schema_version: int = LATEST_SCHEMA_VERSION,
) -> DatabaseMigrationBootstrapper:
    """创建数据库迁移启动桥接器。

    Args:
        engine: 目标数据库引擎。
        registry: 迁移步骤注册表；未提供时使用默认注册表。
        resolver: 数据库版本解析器；未提供时使用默认解析器。
        version_store: 版本存储器；未提供时使用默认存储器。
        latest_schema_version: 当前代码支持的最新 schema 版本号。

    Returns:
        DatabaseMigrationBootstrapper: 配置完成的数据库迁移启动桥接器。
    """
    migration_registry = registry or build_default_migration_registry()
    migration_resolver = resolver or build_default_schema_version_resolver()
    migration_version_store = version_store or SQLiteUserVersionStore()
    migration_manager = DatabaseMigrationManager(
        engine=engine,
        registry=migration_registry,
        resolver=migration_resolver,
        version_store=migration_version_store,
    )
    return DatabaseMigrationBootstrapper(
        manager=migration_manager,
        latest_schema_version=latest_schema_version,
    )
