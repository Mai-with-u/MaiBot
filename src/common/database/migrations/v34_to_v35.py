"""v34 schema 升级到 v35：新增麦麦观察事件账本。"""

from src.common.logger import get_logger

from .models import MigrationExecutionContext

logger = get_logger("database_migration")


def migrate_v34_to_v35(context: MigrationExecutionContext) -> None:
    """创建 ``maisaka_monitor_events`` 表，用于支持麦麦观察离线补漏。"""

    context.start_progress(
        total_tables=1,
        total_records=1,
        description="v34 -> v35 迁移进度",
        table_unit_name="表",
        record_unit_name="表",
    )

    create_maisaka_monitor_events_table(context)
    context.advance_progress(records=1, completed_tables=1, item_name="maisaka_monitor_events")

    logger.info("v34 -> v35 数据库迁移完成：麦麦观察事件账本已就绪")


def create_maisaka_monitor_events_table(context: MigrationExecutionContext) -> None:
    """创建麦麦观察事件账本表及查询索引。"""

    connection = context.connection
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS maisaka_monitor_events (
            event_id INTEGER NOT NULL,
            event_type VARCHAR(100) NOT NULL,
            session_id VARCHAR(255) NOT NULL,
            timestamp FLOAT NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            payload_json TEXT NOT NULL,
            created_at DATETIME,
            PRIMARY KEY (event_id)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_maisaka_monitor_events_event_type "
        "ON maisaka_monitor_events (event_type)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_maisaka_monitor_events_session_id "
        "ON maisaka_monitor_events (session_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_maisaka_monitor_events_session_event "
        "ON maisaka_monitor_events (session_id, event_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_maisaka_monitor_events_type_event "
        "ON maisaka_monitor_events (event_type, event_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_maisaka_monitor_events_timestamp "
        "ON maisaka_monitor_events (timestamp)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_maisaka_monitor_events_created_at "
        "ON maisaka_monitor_events (created_at)"
    )
