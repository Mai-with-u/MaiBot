from sqlalchemy import inspect

from .models import MigrationExecutionContext


def migrate_v55_to_v56(context: MigrationExecutionContext) -> None:
    """已有表情默认允许自动替换。"""
    columns = inspect(context.connection).get_columns("images")
    if not any(column["name"] == "is_locked" for column in columns):
        context.connection.exec_driver_sql("ALTER TABLE images ADD COLUMN is_locked BOOLEAN NOT NULL DEFAULT 0")
