"""v38 schema 升级到 v39：无损压缩回复效果完整记录。"""

from pathlib import Path

import gzip

from src.common.logger import get_logger
from src.common.reply_effect_record_codec import decode_record_payload, encode_record_payload

from .models import MigrationExecutionContext
from .schema import SQLiteSchemaInspector

logger = get_logger("database_migration")

_REPLY_EFFECT_BASE_DIR = Path(__file__).resolve().parents[4] / "logs" / "maisaka_reply_effect"


def migrate_v38_to_v39(context: MigrationExecutionContext) -> None:
    """把重复存放的大块明文 JSON 改为无损压缩，并回收数据库空页。"""

    connection = context.connection
    schema_inspector = SQLiteSchemaInspector()
    table_name = "maisaka_reply_effects"
    if not schema_inspector.table_exists(connection, table_name):
        return

    row_count_result = connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    row_count = int(row_count_result[0] or 0) if row_count_result is not None else 0
    mirror_files = list(_REPLY_EFFECT_BASE_DIR.glob("*/*.json"))
    context.start_progress(
        total_tables=2,
        total_records=row_count + len(mirror_files),
        description="v38 -> v39 无损压缩进度",
        table_unit_name="存储",
        record_unit_name="记录",
    )

    table_schema = schema_inspector.get_table_schema(connection, table_name)
    if not table_schema.has_column("record_blob"):
        connection.exec_driver_sql("ALTER TABLE maisaka_reply_effects ADD COLUMN record_blob BLOB")

    rows = connection.exec_driver_sql(
        "SELECT effect_id, record_json, record_blob FROM maisaka_reply_effects"
    ).mappings().all()
    compressed_rows = 0
    for row in rows:
        record_blob = row["record_blob"]
        if record_blob:
            decode_record_payload(str(row["record_json"] or "{}"), bytes(record_blob))
        else:
            record_payload = decode_record_payload(str(row["record_json"] or "{}"))
            encoded_payload = encode_record_payload(record_payload)
            # 写入前先解码校验，确保迁移不会让完整记录产生任何内容变化。
            if decode_record_payload("{}", encoded_payload) != record_payload:
                raise ValueError(f"回复效果记录压缩校验失败：{row['effect_id']}")
            connection.exec_driver_sql(
                "UPDATE maisaka_reply_effects SET record_json = '{}', record_blob = ? WHERE effect_id = ?",
                (encoded_payload, str(row["effect_id"])),
            )
            compressed_rows += 1
        context.advance_progress(records=1, item_name=str(row["effect_id"]))
    context.advance_progress(completed_tables=1, item_name=table_name)

    compressed_files = 0
    for file_path in mirror_files:
        raw_payload = file_path.read_bytes()
        compressed_payload = gzip.compress(raw_payload, compresslevel=9, mtime=0)
        if gzip.decompress(compressed_payload) != raw_payload:
            raise ValueError(f"回复效果镜像压缩校验失败：{file_path}")
        compressed_path = file_path.with_suffix(f"{file_path.suffix}.gz")
        temp_path = compressed_path.with_name(f".{compressed_path.name}.tmp")
        temp_path.write_bytes(compressed_payload)
        temp_path.replace(compressed_path)
        file_path.unlink()
        compressed_files += 1
        context.advance_progress(records=1, item_name=file_path.name)
    context.advance_progress(completed_tables=1, item_name="maisaka_reply_effect JSON 镜像")

    # ALTER/UPDATE 先提交，再通过 VACUUM 真正归还已释放的数据库文件空间。
    connection.commit()
    connection.exec_driver_sql("VACUUM")
    logger.info(
        f"v38 -> v39 数据库迁移完成：压缩数据库记录={compressed_rows}，"
        f"压缩 JSON 镜像={compressed_files}；记录字段内容保持完整"
    )
