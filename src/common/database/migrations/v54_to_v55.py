"""移除旧人物记忆点和名称设定原因；合并审计保留历史原貌。"""

from sqlalchemy import inspect

import json

from .models import MigrationExecutionContext


def migrate_v54_to_v55(context: MigrationExecutionContext) -> None:
    connection = context.connection
    for table, retired in (("person_info", ("memory_points", "name_reason")), ("individuals", ("name_reason",))):
        columns = {column["name"] for column in inspect(connection).get_columns(table)}
        for column in retired:
            if column in columns:
                connection.exec_driver_sql(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
    rows = connection.exec_driver_sql("SELECT individual_id, profile_sources_json FROM individuals").mappings().all()
    for row in rows:
        sources = json.loads(row["profile_sources_json"])
        for source in sources:
            source.pop("name_reason", None)
        connection.exec_driver_sql(
            "UPDATE individuals SET profile_sources_json=? WHERE individual_id=?",
            (json.dumps(sources, ensure_ascii=False), row["individual_id"]),
        )
