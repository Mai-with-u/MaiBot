"""v40 -> v41：持久化消息的正式账号与作用域归属。"""

from .models import MigrationExecutionContext
from .schema import SQLiteSchemaInspector


def migrate_v40_to_v41(context: MigrationExecutionContext) -> None:
    """新增归属列，只从真实关联的聊天流回填历史消息。"""
    connection = context.connection
    schema = SQLiteSchemaInspector().get_table_schema(connection, "mai_messages")
    for name in ("account_id", "scope"):
        if not schema.has_column(name):
            connection.exec_driver_sql(f"ALTER TABLE mai_messages ADD COLUMN {name} VARCHAR(255)")
    connection.exec_driver_sql(
        """
        UPDATE mai_messages SET
            account_id = (SELECT account_id FROM chat_sessions WHERE chat_sessions.session_id = mai_messages.session_id),
            scope = (SELECT scope FROM chat_sessions WHERE chat_sessions.session_id = mai_messages.session_id)
        WHERE account_id IS NULL AND EXISTS (
            SELECT 1 FROM chat_sessions WHERE chat_sessions.session_id = mai_messages.session_id
        )
        """
    )
