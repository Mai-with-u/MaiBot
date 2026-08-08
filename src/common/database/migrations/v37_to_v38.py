"""v37 schema 升级到 v38：拆分回复请求指纹与稳定 Prompt 版本指纹。"""

from typing import Any, Dict

import json

from src.common.logger import get_logger
from src.common.reply_effect_fingerprint import extract_generation_fingerprints

from .models import MigrationExecutionContext
from .schema import SQLiteSchemaInspector

logger = get_logger("database_migration")


def migrate_v37_to_v38(context: MigrationExecutionContext) -> None:
    """新增完整请求指纹列，并把原 Prompt 指纹迁移为稳定版本指纹。"""

    connection = context.connection
    schema_inspector = SQLiteSchemaInspector()
    table_name = "maisaka_reply_effects"
    if not schema_inspector.table_exists(connection, table_name):
        return

    row_count_result = connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    row_count = int(row_count_result[0] or 0) if row_count_result is not None else 0
    context.start_progress(
        total_tables=1,
        total_records=row_count,
        description="v37 -> v38 迁移进度",
        table_unit_name="表",
        record_unit_name="记录",
    )

    table_schema = schema_inspector.get_table_schema(connection, table_name)
    if not table_schema.has_column("request_fingerprint"):
        connection.exec_driver_sql(
            "ALTER TABLE maisaka_reply_effects "
            "ADD COLUMN request_fingerprint VARCHAR(64) NOT NULL DEFAULT ''"
        )

    rows = connection.exec_driver_sql(
        "SELECT effect_id, request_fingerprint, prompt_fingerprint, record_json FROM maisaka_reply_effects"
    ).mappings().all()
    migrated_count = 0
    for row in rows:
        record_payload = _load_record_payload(str(row["record_json"] or "{}"))
        reply_payload = record_payload.get("reply")
        reply_payload = reply_payload if isinstance(reply_payload, dict) else {}
        metadata = reply_payload.get("reply_metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        _, calculated_request_fingerprint, prompt_version_fingerprint = extract_generation_fingerprints(metadata)
        existing_request_fingerprint = str(row["request_fingerprint"] or "")
        if existing_request_fingerprint:
            request_fingerprint = existing_request_fingerprint
            prompt_version_fingerprint = str(row["prompt_fingerprint"] or "")
        else:
            request_fingerprint = str(row["prompt_fingerprint"] or "") or calculated_request_fingerprint

        reply_payload["request_fingerprint"] = request_fingerprint
        reply_payload["prompt_fingerprint"] = prompt_version_fingerprint
        record_payload["reply"] = reply_payload
        connection.exec_driver_sql(
            """
            UPDATE maisaka_reply_effects
            SET request_fingerprint = ?, prompt_fingerprint = ?, record_json = ?
            WHERE effect_id = ?
            """,
            (
                request_fingerprint,
                prompt_version_fingerprint,
                json.dumps(record_payload, ensure_ascii=False, default=str),
                str(row["effect_id"]),
            ),
        )
        migrated_count += 1
        context.advance_progress(records=1, item_name=str(row["effect_id"]))

    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_reply_effect_request_fingerprint "
        "ON maisaka_reply_effects (request_fingerprint)"
    )
    context.advance_progress(completed_tables=1, item_name=table_name)
    logger.info(
        f"v37 -> v38 数据库迁移完成：已拆分回复请求指纹与 Prompt 版本指纹，迁移记录={migrated_count}"
    )


def _load_record_payload(raw_payload: str) -> Dict[str, Any]:
    """读取迁移记录 JSON，损坏数据直接暴露并中止迁移。"""

    payload = json.loads(raw_payload)
    if not isinstance(payload, dict):
        raise ValueError("回复效果 record_json 必须是 JSON 对象")
    return payload
