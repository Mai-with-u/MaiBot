"""移除迁移记录指纹，保留按来源记录去重的账本。"""

from sqlalchemy import inspect

from .models import MigrationExecutionContext


def migrate_v51_to_v52(context: MigrationExecutionContext) -> None:
    connection = context.connection
    columns = {column["name"] for column in inspect(connection).get_columns("chat_migration_copies")}
    if "signature" not in columns:
        return
    # 旧来源键附带内容指纹，转换成稳定的来源记录键。多版本副本仍保留各自的账本行，
    # 避免丢失目标记录对应关系和实例统计去重信息。
    rows = connection.exec_driver_sql(
        "SELECT id, table_name, origin, target FROM chat_migration_copies ORDER BY id"
    ).all()
    occupied = {(row.table_name, row.origin, row.target) for row in rows}
    for row in rows:
        origin, separator, suffix = row.origin.rpartition(":")
        if not separator or len(suffix) != 64 or any(char not in "0123456789abcdef" for char in suffix):
            continue
        key = (row.table_name, origin, row.target)
        if key not in occupied:
            connection.exec_driver_sql("UPDATE chat_migration_copies SET origin=? WHERE id=?", (origin, row.id))
            occupied.add(key)
    connection.exec_driver_sql("ALTER TABLE chat_migration_copies DROP COLUMN signature")
