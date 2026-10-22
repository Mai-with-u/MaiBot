"""v44 到 v45：独立记录主程序实际接收消息的时间。"""

from .models import MigrationExecutionContext
from .schema import SQLiteSchemaInspector


def migrate_v44_to_v45(context: MigrationExecutionContext) -> None:
    """保留上游时间；历史消息缺少接收时刻，新增列保持 NULL。"""
    connection = context.connection
    schema = SQLiteSchemaInspector().get_table_schema(connection, "mai_messages")
    if not schema.has_column("received_at"):
        connection.exec_driver_sql("ALTER TABLE mai_messages ADD COLUMN received_at DATETIME")
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_mai_messages_received_at ON mai_messages (received_at)"
    )
