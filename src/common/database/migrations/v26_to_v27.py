from __future__ import annotations

from sqlalchemy.engine import Connection

from src.common.logger import get_logger

from .models import MigrationExecutionContext

logger = get_logger("database_migration")


def migrate_v26_to_v27(context: MigrationExecutionContext) -> None:
    """移除行为场景簇不再使用的 score 字段。"""

    context.start_progress(
        total_tables=1,
        total_records=0,
        description="v26 -> v27 迁移进度",
        table_unit_name="表",
        record_unit_name="记录",
    )
    if _has_table(context.connection, "behavior_scene_clusters") and _has_column(
        context.connection,
        "behavior_scene_clusters",
        "score",
    ):
        _rebuild_behavior_scene_clusters(context.connection)
    context.advance_progress(records=0, completed_tables=1, item_name="behavior_scene_clusters")
    logger.info("v26 -> v27 数据库迁移完成：已移除 behavior_scene_clusters.score")


def _rebuild_behavior_scene_clusters(connection: Connection) -> None:
    connection.exec_driver_sql("DROP TABLE IF EXISTS behavior_scene_clusters_v27")
    connection.exec_driver_sql(
        """
        CREATE TABLE behavior_scene_clusters_v27 (
            id INTEGER NOT NULL,
            session_id VARCHAR(255),
            tag_distribution TEXT NOT NULL DEFAULT '[]',
            source_count INTEGER NOT NULL DEFAULT 0,
            update_time DATETIME NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO behavior_scene_clusters_v27 (
            id,
            session_id,
            tag_distribution,
            source_count,
            update_time
        )
        SELECT
            id,
            session_id,
            COALESCE(tag_distribution, '[]'),
            COALESCE(source_count, 0),
            update_time
        FROM behavior_scene_clusters
        """
    )
    connection.exec_driver_sql("DROP TABLE behavior_scene_clusters")
    connection.exec_driver_sql("ALTER TABLE behavior_scene_clusters_v27 RENAME TO behavior_scene_clusters")
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_behavior_scene_clusters_session_id "
        "ON behavior_scene_clusters (session_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_behavior_scene_clusters_update_time "
        "ON behavior_scene_clusters (update_time)"
    )


def _has_table(connection: Connection, table_name: str) -> bool:
    row = connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _has_column(connection: Connection, table_name: str, column_name: str) -> bool:
    rows = connection.exec_driver_sql(f'PRAGMA table_info("{table_name}")').fetchall()
    return any(str(row[1]) == column_name for row in rows)
