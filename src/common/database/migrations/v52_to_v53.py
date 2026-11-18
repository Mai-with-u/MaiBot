"""为已有平台用户建立独立 Individual，保留原人物及消息归属。"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import inspect

from .models import MigrationExecutionContext


def migrate_v52_to_v53(context: MigrationExecutionContext) -> None:
    connection = context.connection
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS individuals (individual_id VARCHAR PRIMARY KEY NOT NULL, "
        "display_name VARCHAR NOT NULL, created_at DATETIME NOT NULL)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS individual_merge_events (id INTEGER PRIMARY KEY NOT NULL, "
        "target_individual_id VARCHAR NOT NULL, members_json VARCHAR NOT NULL, created_at DATETIME NOT NULL)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_individual_merge_events_target_individual_id "
        "ON individual_merge_events(target_individual_id)"
    )
    columns = {column["name"] for column in inspect(connection).get_columns("person_info")}
    if "individual_id" not in columns:
        # SQLite 给已有表添加外键列时要求默认 NULL；回填后用触发器约束非空。
        connection.exec_driver_sql(
            "ALTER TABLE person_info ADD COLUMN individual_id VARCHAR REFERENCES individuals(individual_id)"
        )
    rows = (
        connection.exec_driver_sql(
            "SELECT id, person_name, user_nickname, user_id FROM person_info WHERE individual_id IS NULL"
        )
        .mappings()
        .all()
    )
    for row in rows:
        individual_id = uuid4().hex
        connection.exec_driver_sql(
            "INSERT INTO individuals VALUES (?, ?, ?)",
            (
                individual_id,
                row["person_name"] or row["user_nickname"] or row["user_id"],
                datetime.now().isoformat(sep=" "),
            ),
        )
        connection.exec_driver_sql("UPDATE person_info SET individual_id=? WHERE id=?", (individual_id, row["id"]))
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_person_info_individual_id ON person_info(individual_id)")
    for operation in ("INSERT", "UPDATE"):
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS person_individual_required_{operation.lower()} "
            f"BEFORE {operation} ON person_info WHEN NEW.individual_id IS NULL BEGIN "
            "SELECT RAISE(ABORT, 'person_info.individual_id is required'); END"
        )
