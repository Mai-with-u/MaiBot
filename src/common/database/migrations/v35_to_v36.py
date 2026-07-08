"""v35 schema 升级到 v36：新增消息平台与消息 ID 复合索引。"""

from src.common.logger import get_logger

from .models import MigrationExecutionContext

logger = get_logger("database_migration")

MAI_MESSAGES_PLATFORM_MESSAGE_ID_INDEX = "ix_mai_messages_platform_message_id"


def migrate_v35_to_v36(context: MigrationExecutionContext) -> None:
    """为 ``mai_messages`` 创建 ``platform + message_id`` 查询索引。"""

    context.start_progress(
        total_tables=1,
        total_records=1,
        description="v35 -> v36 迁移进度",
        table_unit_name="表",
        record_unit_name="索引",
    )

    create_mai_messages_platform_message_id_index(context)
    context.advance_progress(records=1, completed_tables=1, item_name=MAI_MESSAGES_PLATFORM_MESSAGE_ID_INDEX)

    logger.info("v35 -> v36 数据库迁移完成：消息平台与消息 ID 复合索引已就绪")


def create_mai_messages_platform_message_id_index(context: MigrationExecutionContext) -> None:
    """创建消息平台与消息 ID 复合索引，避免按平台索引回表扫描大量消息。"""

    context.connection.exec_driver_sql(
        f"CREATE INDEX IF NOT EXISTS {MAI_MESSAGES_PLATFORM_MESSAGE_ID_INDEX} "
        "ON mai_messages (platform, message_id)"
    )
