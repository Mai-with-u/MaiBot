"""人物称呼、原因及认识状态归属 Individual，账号旧资料保留为来源。"""

from sqlalchemy import inspect

import json

from .models import MigrationExecutionContext


def migrate_v53_to_v54(context: MigrationExecutionContext) -> None:
    connection = context.connection
    columns = {column["name"] for column in inspect(connection).get_columns("individuals")}
    for name, definition in (
        ("name_reason", "VARCHAR"),
        ("is_known", "BOOLEAN NOT NULL DEFAULT 0"),
        ("profile_sources_json", "VARCHAR NOT NULL DEFAULT '[]'"),
    ):
        if name not in columns:
            connection.exec_driver_sql(f"ALTER TABLE individuals ADD COLUMN {name} {definition}")
    events = {column["name"] for column in inspect(connection).get_columns("individual_merge_events")}
    if "profiles_json" not in events:
        connection.exec_driver_sql(
            "ALTER TABLE individual_merge_events ADD COLUMN profiles_json VARCHAR NOT NULL DEFAULT '{}'"
        )
    individuals = (
        connection.exec_driver_sql(
            "SELECT individual_id, display_name FROM individuals WHERE profile_sources_json = '[]'"
        )
        .mappings()
        .all()
    )
    for individual in individuals:
        people = (
            connection.exec_driver_sql(
                "SELECT person_id, platform, user_id, person_name, name_reason, is_known, user_nickname "
                "FROM person_info WHERE individual_id=? ORDER BY id",
                (individual["individual_id"],),
            )
            .mappings()
            .all()
        )
        sources = [
            {
                "individual_id": individual["individual_id"],
                "person_id": person["person_id"],
                "platform": person["platform"],
                "user_id": person["user_id"],
                "display_name": person["person_name"] or person["user_nickname"] or person["user_id"],
                "name_reason": person["name_reason"],
                "is_known": bool(person["is_known"]),
            }
            for person in people
        ]
        # 已经关联的个体保留原显示名称；不同来源原因全部留档，不任意挑选一条。
        reasons = {
            source["name_reason"]
            for source in sources
            if source["display_name"] == individual["display_name"] and source["name_reason"]
        }
        connection.exec_driver_sql(
            "UPDATE individuals SET name_reason=?, is_known=?, profile_sources_json=? WHERE individual_id=?",
            (
                next(iter(reasons)) if len(reasons) == 1 else None,
                any(source["is_known"] for source in sources),
                json.dumps(sources, ensure_ascii=False),
                individual["individual_id"],
            ),
        )
