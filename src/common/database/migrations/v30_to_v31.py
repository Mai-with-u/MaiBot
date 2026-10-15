"""v30 schema 升级到 v31：清理低信息行为场景。"""

from collections import defaultdict
from datetime import datetime
from typing import Any, Sequence

import json
import random

from sqlalchemy.engine import Connection

from src.common.logger import get_logger
from .behavior_generic_tags import is_behavior_generic_tag

from .models import MigrationExecutionContext
from .schema import SQLiteSchemaInspector

logger = get_logger("database_migration")
LOW_DOMAIN_SCENE_DELETE_RATES = {
    1: 1.0,
    2: 0.75,
    3: 0.5,
}


def migrate_v30_to_v31(context: MigrationExecutionContext) -> None:
    """清理泛 tag 和低信息行为场景簇。"""

    context.start_progress(
        total_tables=3,
        total_records=_count_behavior_cleanup_rows(context.connection),
        description="v30 -> v31 迁移进度",
        table_unit_name="表",
        record_unit_name="记录",
    )
    behavior_cleanup_stats = _cleanup_behavior_scene_generic_tags(context.connection)
    context.advance_progress(
        records=behavior_cleanup_stats["tag_rows_deleted"],
        completed_tables=1,
        item_name="behavior_scene_tag_clusters",
    )
    context.advance_progress(
        records=behavior_cleanup_stats["scene_clusters_updated"] + behavior_cleanup_stats["scene_clusters_deleted"],
        completed_tables=1,
        item_name="behavior_scene_clusters",
    )
    context.advance_progress(
        records=behavior_cleanup_stats["behavior_paths_deleted"]
        + behavior_cleanup_stats["offline_import_records_deleted"],
        completed_tables=1,
        item_name="behavior_experience_paths",
    )
    logger.info(
        "v30 -> v31 数据库迁移完成：行为场景清理 tag=%s, 场景更新=%s, 场景删除=%s, 路径删除=%s, 导入记录删除=%s",
        behavior_cleanup_stats["tag_rows_deleted"],
        behavior_cleanup_stats["scene_clusters_updated"],
        behavior_cleanup_stats["scene_clusters_deleted"],
        behavior_cleanup_stats["behavior_paths_deleted"],
        behavior_cleanup_stats["offline_import_records_deleted"],
    )


def _count_behavior_cleanup_rows(connection: Connection) -> int:
    schema_inspector = SQLiteSchemaInspector()
    total_rows = 0
    for table_name in ("behavior_scene_tag_clusters", "behavior_scene_clusters"):
        if not schema_inspector.table_exists(connection, table_name):
            continue
        row = connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table_name}").fetchone()
        total_rows += int(row[0] or 0) if row is not None else 0
    return total_rows


def _cleanup_behavior_scene_generic_tags(connection: Connection) -> dict[str, int]:
    stats = {
        "tag_rows_deleted": 0,
        "scene_clusters_updated": 0,
        "behavior_paths_deleted": 0,
        "offline_import_records_deleted": 0,
        "scene_clusters_deleted": 0,
    }
    schema_inspector = SQLiteSchemaInspector()
    if not schema_inspector.table_exists(connection, "behavior_scene_tag_clusters"):
        return stats
    if not schema_inspector.table_exists(connection, "behavior_scene_clusters"):
        return stats

    cleanup_plan = _build_behavior_cleanup_plan(connection)
    stats["tag_rows_deleted"] = _delete_by_ids(
        connection,
        "behavior_scene_tag_clusters",
        "id",
        cleanup_plan["generic_tag_row_ids"],
    )

    deleted_scene_ids = {int(item["scene_cluster_id"]) for item in cleanup_plan["scene_deletes"]}
    for update_plan in cleanup_plan["scene_updates"]:
        scene_cluster_id = int(update_plan["scene_cluster_id"])
        if scene_cluster_id in deleted_scene_ids:
            continue
        cursor = connection.exec_driver_sql(
            """
            UPDATE behavior_scene_clusters
            SET tag_distribution = ?,
                update_time = ?
            WHERE id = ?
            """,
            (update_plan["new_distribution"], datetime.now().isoformat(timespec="seconds"), scene_cluster_id),
        )
        stats["scene_clusters_updated"] += int(cursor.rowcount or 0)

    scene_cluster_ids = sorted(deleted_scene_ids)
    stats["behavior_paths_deleted"] = _delete_paths_by_scene_ids(connection, scene_cluster_ids)
    stats["offline_import_records_deleted"] = _delete_import_records_by_scene_ids(connection, scene_cluster_ids)
    stats["scene_clusters_deleted"] = _delete_by_ids(
        connection,
        "behavior_scene_clusters",
        "id",
        scene_cluster_ids,
    )
    return stats


def _build_behavior_cleanup_plan(connection: Connection) -> dict[str, list[dict[str, Any]] | list[int]]:
    tag_rows = connection.exec_driver_sql(
        """
        SELECT id, tag_kind, tag, cluster_key, source_count
        FROM behavior_scene_tag_clusters
        ORDER BY id
        """
    ).mappings().all()
    scene_rows = connection.exec_driver_sql(
        """
        SELECT id, session_id, tag_distribution, source_count
        FROM behavior_scene_clusters
        ORDER BY id
        """
    ).mappings().all()

    rows_by_key: dict[tuple[str, str], list[Any]] = defaultdict(list)
    generic_tag_rows: list[Any] = []
    for row in tag_rows:
        key = (str(row["tag_kind"] or ""), str(row["cluster_key"] or ""))
        if key[0] and key[1]:
            rows_by_key[key].append(row)
        if is_behavior_generic_tag(row["tag_kind"], row["tag"]):
            generic_tag_rows.append(row)

    generic_tag_row_ids = {int(row["id"]) for row in generic_tag_rows}
    remaining_keys: set[tuple[str, str]] = set()
    emptied_keys: set[tuple[str, str]] = set()
    for key, rows in rows_by_key.items():
        remaining_rows = [row for row in rows if int(row["id"]) not in generic_tag_row_ids]
        if remaining_rows:
            remaining_keys.add(key)
        elif any(int(row["id"]) in generic_tag_row_ids for row in rows):
            emptied_keys.add(key)

    scene_updates: list[dict[str, Any]] = []
    scene_deletes: list[dict[str, Any]] = []
    for row in scene_rows:
        raw_items = _load_json_list(row["tag_distribution"])
        kept_items: list[dict[str, Any]] = []
        scene_changed = not raw_items
        for item in raw_items:
            if not isinstance(item, dict):
                scene_changed = True
                continue
            split_ref = _split_tag_ref(item.get("tag"))
            if split_ref is None:
                scene_changed = True
                continue
            if split_ref in emptied_keys or split_ref not in remaining_keys:
                scene_changed = True
                continue
            kept_items.append(item)

        new_distribution = _normalize_distribution(kept_items)
        old_distribution = str(row["tag_distribution"] or "[]")
        normalized_items = [item for item in _load_json_list(new_distribution) if isinstance(item, dict)]
        if new_distribution == "[]" and scene_changed:
            scene_deletes.append({"scene_cluster_id": int(row["id"])})
        elif _low_signal_scene_delete_reason(normalized_items):
            scene_deletes.append({"scene_cluster_id": int(row["id"])})
        elif scene_changed and new_distribution != old_distribution:
            scene_updates.append(
                {
                    "scene_cluster_id": int(row["id"]),
                    "new_distribution": new_distribution,
                }
            )

    return {
        "generic_tag_row_ids": sorted(generic_tag_row_ids),
        "scene_updates": scene_updates,
        "scene_deletes": scene_deletes,
    }


def _delete_by_ids(connection: Connection, table_name: str, column_name: str, ids: Sequence[int]) -> int:
    deleted_count = 0
    for batch in _iter_batches(list(ids)):
        placeholders = ",".join("?" for _ in batch)
        cursor = connection.exec_driver_sql(
            f"DELETE FROM {table_name} WHERE {column_name} IN ({placeholders})",
            tuple(batch),
        )
        deleted_count += int(cursor.rowcount or 0)
    return deleted_count


def _delete_paths_by_scene_ids(connection: Connection, scene_cluster_ids: Sequence[int]) -> int:
    schema_inspector = SQLiteSchemaInspector()
    if not scene_cluster_ids or not schema_inspector.table_exists(connection, "behavior_experience_paths"):
        return 0
    deleted_count = 0
    for batch in _iter_batches(list(scene_cluster_ids)):
        placeholders = ",".join("?" for _ in batch)
        cursor = connection.exec_driver_sql(
            f"DELETE FROM behavior_experience_paths WHERE scene_cluster_id IN ({placeholders})",
            tuple(batch),
        )
        deleted_count += int(cursor.rowcount or 0)
    return deleted_count


def _delete_import_records_by_scene_ids(connection: Connection, scene_cluster_ids: Sequence[int]) -> int:
    schema_inspector = SQLiteSchemaInspector()
    if not scene_cluster_ids or not schema_inspector.table_exists(connection, "behavior_offline_import_records"):
        return 0

    table_schema = schema_inspector.get_table_schema(connection, "behavior_offline_import_records")
    columns = [
        column_name
        for column_name in ("target_scene_cluster_id", "source_scene_cluster_id")
        if table_schema.has_column(column_name)
    ]
    if not columns:
        return 0

    deleted_count = 0
    for batch in _iter_batches(list(scene_cluster_ids)):
        placeholders = ",".join("?" for _ in batch)
        where_clause = " OR ".join(f"{column_name} IN ({placeholders})" for column_name in columns)
        params: list[int] = []
        for _ in columns:
            params.extend(batch)
        cursor = connection.exec_driver_sql(
            f"DELETE FROM behavior_offline_import_records WHERE {where_clause}",
            tuple(params),
        )
        deleted_count += int(cursor.rowcount or 0)
    return deleted_count


def _load_json_list(raw_value: Any) -> list[Any]:
    if isinstance(raw_value, list):
        return raw_value
    if not isinstance(raw_value, str) or not raw_value.strip():
        return []
    try:
        parsed_value = json.loads(raw_value)
    except (TypeError, ValueError):
        return []
    return parsed_value if isinstance(parsed_value, list) else []


def _split_tag_ref(raw_value: Any) -> tuple[str, str] | None:
    tag_ref = str(raw_value or "").strip()
    if ":" not in tag_ref:
        return None
    tag_kind, cluster_key = tag_ref.split(":", 1)
    tag_kind = tag_kind.strip()
    cluster_key = cluster_key.strip()
    if not tag_kind or not cluster_key:
        return None
    return tag_kind, cluster_key


def _normalize_distribution(items: Sequence[dict[str, Any]]) -> str:
    weighted_items: list[tuple[str, float]] = []
    for item in items:
        tag_ref = str(item.get("tag") or "").strip()
        if not tag_ref:
            continue
        try:
            probability = float(item.get("probability") or 0.0)
        except (TypeError, ValueError):
            continue
        if probability <= 0:
            continue
        weighted_items.append((tag_ref, probability))

    total_probability = sum(probability for _, probability in weighted_items)
    if total_probability <= 0:
        return "[]"
    normalized_items = [
        {"tag": tag_ref, "probability": round(probability / total_probability, 6)}
        for tag_ref, probability in sorted(weighted_items)
    ]
    return json.dumps(normalized_items, ensure_ascii=False, sort_keys=True)


def _low_signal_scene_delete_reason(items: Sequence[dict[str, Any]]) -> str:
    domain_ref_count = 0
    for item in items:
        split_ref = _split_tag_ref(item.get("tag"))
        if split_ref is None:
            continue
        tag_kind, _ = split_ref
        if tag_kind == "domain":
            domain_ref_count += 1

    if domain_ref_count == 0:
        return "no_domain_tag"
    delete_rate = LOW_DOMAIN_SCENE_DELETE_RATES.get(domain_ref_count, 0.0)
    if delete_rate >= 1.0 or random.random() < delete_rate:
        return f"{domain_ref_count}_domain_random_delete"
    return ""


def _iter_batches(values: Sequence[int], *, batch_size: int = 500) -> Sequence[list[int]]:
    return [list(values[index : index + batch_size]) for index in range(0, len(values), batch_size)]
