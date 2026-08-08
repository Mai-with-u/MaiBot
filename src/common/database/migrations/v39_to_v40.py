"""v39 schema 升级到 v40：新增 Bot 平台账号表。"""

from src.common.logger import get_logger

from .models import MigrationExecutionContext

logger = get_logger("database_migration")


def migrate_v39_to_v40(context: MigrationExecutionContext) -> None:
    """创建适配器上报的 Bot 平台账号表，不回填配置或历史会话。"""

    context.start_progress(
        total_tables=1,
        total_records=1,
        description="v39 -> v40 迁移进度",
        table_unit_name="表",
        record_unit_name="表",
    )
    connection = context.connection
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS bot_platform_accounts (
            id INTEGER NOT NULL,
            platform VARCHAR(100) NOT NULL,
            account_id VARCHAR(255) NOT NULL,
            disabled BOOLEAN NOT NULL DEFAULT 0,
            first_seen_at DATETIME NOT NULL,
            last_seen_at DATETIME NOT NULL,
            disabled_at DATETIME,
            last_source VARCHAR(32) NOT NULL DEFAULT '',
            last_adapter_id VARCHAR(255),
            last_plugin_id VARCHAR(255),
            last_gateway_name VARCHAR(255),
            PRIMARY KEY (id),
            CONSTRAINT uq_bot_platform_accounts_platform_account UNIQUE (platform, account_id)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_bot_platform_accounts_platform ON bot_platform_accounts (platform)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_bot_platform_accounts_account_id ON bot_platform_accounts (account_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_bot_platform_accounts_disabled ON bot_platform_accounts (disabled)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_bot_platform_accounts_last_seen_at ON bot_platform_accounts (last_seen_at)"
    )
    context.advance_progress(records=1, completed_tables=1, item_name="bot_platform_accounts")
    logger.info("v39 -> v40 数据库迁移完成：Bot 平台账号表已创建")
