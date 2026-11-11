"""补齐已创建的聊天迁移账本签名列及目标记录索引。"""

from sqlalchemy import inspect

from src.common.database.chat_migration_models import copies

from .models import MigrationExecutionContext


def migrate_v50_to_v51(context: MigrationExecutionContext) -> None:
    columns = {column["name"] for column in inspect(context.connection).get_columns(copies.name)}
    if "signature" not in columns:
        context.connection.exec_driver_sql(
            "ALTER TABLE chat_migration_copies ADD COLUMN signature VARCHAR NOT NULL DEFAULT ''"
        )
    for index in copies.indexes:
        index.create(context.connection, checkfirst=True)
