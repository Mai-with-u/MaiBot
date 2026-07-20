from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import sqlite3

from src.common.logger import get_logger

from .metadata_fact import FACT_SCHEMA_STATEMENTS
from .knowledge_types import (
    KnowledgeType,
    allowed_knowledge_type_values,
    resolve_stored_knowledge_type,
    validate_stored_knowledge_type,
)

logger = get_logger("A_Memorix.MetadataSchema")

SCHEMA_VERSION = 21
RUNTIME_AUTO_MIGRATION_MIN_SCHEMA_VERSION = 9


class MetadataSchemaMixin:
    """维护元数据数据库表结构、版本迁移与数据规范化。"""

    def _assert_schema_compatible(self, db_existed: bool) -> None:
        """运行时执行 post-1.0 自动迁移；legacy/vNext 仍要求离线迁移。"""
        cursor = self._conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
        has_version_table = cursor.fetchone() is not None
        if not has_version_table:
            if db_existed:
                raise RuntimeError(
                    "检测到旧版 metadata schema（缺少 schema_migrations）。"
                    " 请先执行 scripts/release_vnext_migrate.py migrate。"
                )
            return

        cursor.execute("SELECT MAX(version) FROM schema_migrations")
        row = cursor.fetchone()
        version = int(row[0]) if row and row[0] is not None else 0
        if version < SCHEMA_VERSION and version >= RUNTIME_AUTO_MIGRATION_MIN_SCHEMA_VERSION:
            self._run_runtime_auto_migration(current_version=version)
            cursor.execute("SELECT MAX(version) FROM schema_migrations")
            row = cursor.fetchone()
            version = int(row[0]) if row and row[0] is not None else 0
        if version != SCHEMA_VERSION:
            raise RuntimeError(
                f"metadata schema 版本不匹配: current={version}, expected={SCHEMA_VERSION}。"
                " 请执行 scripts/release_vnext_migrate.py migrate。"
            )

    def _run_runtime_auto_migration(self, *, current_version: int) -> None:
        """对 1.0 之后的已版本化库执行轻量自动迁移。"""
        logger.info(
            f"检测到 metadata schema 需要运行时自动迁移: current={current_version}, target={SCHEMA_VERSION}",
        )
        self._repair_historical_foreign_key_violations(current_version=current_version)
        self._migrate_schema()
        alias_result = self.rebuild_relation_hash_aliases()
        knowledge_type_result = self.normalize_paragraph_knowledge_types()
        fact_result = self.backfill_person_fact_claims()
        self.set_schema_version(SCHEMA_VERSION)
        logger.info(
            f"metadata schema 运行时自动迁移完成: {current_version} -> {SCHEMA_VERSION}, "
            f"alias_inserted={int(alias_result.get('inserted', 0) or 0)}, "
            f"knowledge_normalized={int(knowledge_type_result.get('normalized', 0) or 0)}, "
            f"fact_migrated={int(fact_result.get('migrated', 0) or 0)}",
        )

    def _collect_foreign_key_violations(self) -> List[sqlite3.Row]:
        """收集当前数据库中已经存在的外键违规记录。"""
        return list(self._conn.execute("PRAGMA foreign_key_check").fetchall())

    @staticmethod
    def _format_foreign_key_violation_summary(violations: List[sqlite3.Row]) -> str:
        """按子表、父表和外键编号汇总外键违规，避免日志输出大量行号。"""
        grouped: Dict[Tuple[str, str, int], int] = {}
        for row in violations:
            key = (str(row[0]), str(row[2]), int(row[3]))
            grouped[key] = grouped.get(key, 0) + 1
        return ", ".join(
            f"{child}->{parent}(fk#{foreign_key_id})={count}"
            for (child, parent, foreign_key_id), count in sorted(grouped.items())
        )

    def _backup_metadata_database_for_schema_repair(self, *, current_version: int) -> Path:
        """在自动清理历史孤立关联前创建一致性的 SQLite 备份。"""
        database_path = self.get_db_path()
        backup_path = database_path.with_name(
            f"{database_path.name}.pre-schema{current_version}-to-{SCHEMA_VERSION}-repair.bak"
        )
        if backup_path.exists():
            logger.warning(f"Schema 修复备份已存在，将继续使用: {backup_path}")
            return backup_path

        backup_connection = sqlite3.connect(str(backup_path))
        try:
            self._conn.backup(backup_connection)
            backup_connection.commit()
        except BaseException:
            backup_connection.close()
            backup_path.unlink(missing_ok=True)
            raise
        backup_connection.close()
        logger.warning(f"Schema 修复前数据库已备份: {backup_path}")
        return backup_path

    def _repair_historical_foreign_key_violations(self, *, current_version: int) -> Dict[str, int]:
        """定向清理缺少父记录、因而无法再被正常使用的历史关联数据。"""
        violations = self._collect_foreign_key_violations()
        if not violations:
            return {}

        backup_path = self._backup_metadata_database_for_schema_repair(
            current_version=current_version
        )
        table_rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        tables = {str(row[0]) for row in table_rows}

        # 关联表、缓存表和操作明细缺少父记录后已没有独立业务含义，可以安全清理。
        # 主体记忆表不进入该列表，未知异常会在事务末尾触发回滚。
        repairs: List[Tuple[str, str]] = []
        if {"paragraph_relations", "paragraphs", "relations"}.issubset(tables):
            repairs.append(
                (
                    "paragraph_relations",
                    """
                    DELETE FROM paragraph_relations
                    WHERE NOT EXISTS (
                        SELECT 1 FROM paragraphs p
                        WHERE p.hash = paragraph_relations.paragraph_hash
                    )
                       OR NOT EXISTS (
                        SELECT 1 FROM relations r
                        WHERE r.hash = paragraph_relations.relation_hash
                    )
                    """,
                )
            )
        if {"paragraph_entities", "paragraphs", "entities"}.issubset(tables):
            repairs.append(
                (
                    "paragraph_entities",
                    """
                    DELETE FROM paragraph_entities
                    WHERE NOT EXISTS (
                        SELECT 1 FROM paragraphs p
                        WHERE p.hash = paragraph_entities.paragraph_hash
                    )
                       OR NOT EXISTS (
                        SELECT 1 FROM entities e
                        WHERE e.hash = paragraph_entities.entity_hash
                    )
                    """,
                )
            )
        if {"episode_paragraphs", "episodes", "paragraphs"}.issubset(tables):
            repairs.append(
                (
                    "episode_paragraphs",
                    """
                    DELETE FROM episode_paragraphs
                    WHERE NOT EXISTS (
                        SELECT 1 FROM episodes e
                        WHERE e.episode_id = episode_paragraphs.episode_id
                    )
                       OR NOT EXISTS (
                        SELECT 1 FROM paragraphs p
                        WHERE p.hash = episode_paragraphs.paragraph_hash
                    )
                    """,
                )
            )
        if {"memory_feedback_action_logs", "memory_feedback_tasks"}.issubset(tables):
            repairs.append(
                (
                    "memory_feedback_action_logs",
                    """
                    DELETE FROM memory_feedback_action_logs
                    WHERE NOT EXISTS (
                        SELECT 1 FROM memory_feedback_tasks t
                        WHERE t.id = memory_feedback_action_logs.task_id
                    )
                    """,
                )
            )
        if {"paragraph_stale_relation_marks", "paragraphs", "relations"}.issubset(tables):
            repairs.append(
                (
                    "paragraph_stale_relation_marks",
                    """
                    DELETE FROM paragraph_stale_relation_marks
                    WHERE NOT EXISTS (
                        SELECT 1 FROM paragraphs p
                        WHERE p.hash = paragraph_stale_relation_marks.paragraph_hash
                    )
                       OR NOT EXISTS (
                        SELECT 1 FROM relations r
                        WHERE r.hash = paragraph_stale_relation_marks.relation_hash
                    )
                    """,
                )
            )
        stale_mark_columns = (
            {
                str(row[1])
                for row in self._conn.execute(
                    "PRAGMA table_info(paragraph_stale_relation_marks)"
                ).fetchall()
            }
            if "paragraph_stale_relation_marks" in tables
            else set()
        )
        if (
            {"paragraph_stale_relation_marks", "memory_feedback_tasks"}.issubset(tables)
            and "task_id" in stale_mark_columns
        ):
            repairs.append(
                (
                    "paragraph_stale_relation_marks.task_id",
                    """
                    UPDATE paragraph_stale_relation_marks
                    SET task_id = NULL
                    WHERE task_id IS NOT NULL
                      AND NOT EXISTS (
                        SELECT 1 FROM memory_feedback_tasks t
                        WHERE t.id = paragraph_stale_relation_marks.task_id
                    )
                    """,
                )
            )
        if {"external_memory_refs", "paragraphs"}.issubset(tables):
            repairs.append(
                (
                    "external_memory_refs",
                    """
                    DELETE FROM external_memory_refs
                    WHERE NOT EXISTS (
                        SELECT 1 FROM paragraphs p
                        WHERE p.hash = external_memory_refs.paragraph_hash
                    )
                    """,
                )
            )
        if {"delete_operation_items", "delete_operations"}.issubset(tables):
            repairs.append(
                (
                    "delete_operation_items",
                    """
                    DELETE FROM delete_operation_items
                    WHERE NOT EXISTS (
                        SELECT 1 FROM delete_operations o
                        WHERE o.operation_id = delete_operation_items.operation_id
                    )
                    """,
                )
            )
        if {"storage_cleanup_jobs", "delete_operations"}.issubset(tables):
            repairs.append(
                (
                    "storage_cleanup_jobs",
                    """
                    DELETE FROM storage_cleanup_jobs
                    WHERE NOT EXISTS (
                        SELECT 1 FROM delete_operations o
                        WHERE o.operation_id = storage_cleanup_jobs.operation_id
                    )
                    """,
                )
            )
        if {"fact_evidence", "fact_claims"}.issubset(tables):
            repairs.append(
                (
                    "fact_evidence",
                    """
                    DELETE FROM fact_evidence
                    WHERE NOT EXISTS (
                        SELECT 1 FROM fact_claims c
                        WHERE c.claim_id = fact_evidence.claim_id
                    )
                    """,
                )
            )
        if {"paragraph_ngrams", "paragraphs"}.issubset(tables):
            repairs.append(
                (
                    "paragraph_ngrams",
                    """
                    DELETE FROM paragraph_ngrams
                    WHERE NOT EXISTS (
                        SELECT 1 FROM paragraphs p
                        WHERE p.hash = paragraph_ngrams.paragraph_hash
                    )
                    """,
                )
            )

        repaired: Dict[str, int] = {}
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for label, sql in repairs:
                cursor = self._conn.execute(sql)
                if cursor.rowcount > 0:
                    repaired[label] = int(cursor.rowcount)

            remaining = self._collect_foreign_key_violations()
            if remaining:
                summary = self._format_foreign_key_violation_summary(remaining)
                raise RuntimeError(
                    f"历史数据库包含无法自动修复的外键异常: {len(remaining)} 项，"
                    f"详情={summary}，备份={backup_path}"
                )
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise

        repair_summary = ", ".join(f"{name}={count}" for name, count in repaired.items())
        logger.warning(
            f"历史数据库孤立关联已自动修复: violations={len(violations)}, "
            f"repaired={repair_summary or '0'}, backup={backup_path}"
        )
        return repaired

    def _ensure_memory_feedback_task_columns(self, cursor: sqlite3.Cursor) -> None:
        """补齐 memory_feedback_tasks 历史库缺失的 rollback_* 列。"""
        cursor.execute("PRAGMA table_info(memory_feedback_tasks)")
        feedback_task_columns = {row[1] for row in cursor.fetchall()}
        feedback_task_migrations = {
            "rollback_status": "ALTER TABLE memory_feedback_tasks ADD COLUMN rollback_status TEXT DEFAULT 'none'",
            "rollback_plan_json": "ALTER TABLE memory_feedback_tasks ADD COLUMN rollback_plan_json TEXT",
            "rollback_result_json": "ALTER TABLE memory_feedback_tasks ADD COLUMN rollback_result_json TEXT",
            "rollback_error": "ALTER TABLE memory_feedback_tasks ADD COLUMN rollback_error TEXT",
            "rollback_requested_by": "ALTER TABLE memory_feedback_tasks ADD COLUMN rollback_requested_by TEXT",
            "rollback_reason": "ALTER TABLE memory_feedback_tasks ADD COLUMN rollback_reason TEXT",
            "rollback_requested_at": "ALTER TABLE memory_feedback_tasks ADD COLUMN rollback_requested_at REAL",
            "rolled_back_at": "ALTER TABLE memory_feedback_tasks ADD COLUMN rolled_back_at REAL",
        }
        for col, sql in feedback_task_migrations.items():
            if col not in feedback_task_columns:
                try:
                    cursor.execute(sql)
                except sqlite3.OperationalError as e:
                    cursor.execute("PRAGMA table_info(memory_feedback_tasks)")
                    current_columns = {row[1] for row in cursor.fetchall()}
                    if col not in current_columns:
                        raise RuntimeError(f"Schema迁移失败 (memory_feedback_tasks.{col})") from e

    def _ensure_paragraph_stale_relation_mark_columns(self, cursor: sqlite3.Cursor) -> None:
        """补齐段落陈旧关系标记的来源追踪列。"""
        cursor.execute("PRAGMA table_info(paragraph_stale_relation_marks)")
        stale_mark_columns = {row[1] for row in cursor.fetchall()}
        stale_mark_migrations = {
            "source_type": "ALTER TABLE paragraph_stale_relation_marks ADD COLUMN source_type TEXT",
            "source_id": "ALTER TABLE paragraph_stale_relation_marks ADD COLUMN source_id TEXT",
            "source_operation_id": "ALTER TABLE paragraph_stale_relation_marks ADD COLUMN source_operation_id TEXT",
        }
        for col, sql in stale_mark_migrations.items():
            if col not in stale_mark_columns:
                try:
                    cursor.execute(sql)
                except sqlite3.OperationalError as e:
                    cursor.execute("PRAGMA table_info(paragraph_stale_relation_marks)")
                    current_columns = {row[1] for row in cursor.fetchall()}
                    if col not in current_columns:
                        raise RuntimeError(f"Schema迁移失败 (paragraph_stale_relation_marks.{col})") from e

    def _ensure_person_profile_snapshot_columns(self, cursor: sqlite3.Cursor) -> None:
        """补齐人物画像证据版本列。"""
        cursor.execute("PRAGMA table_info(person_profile_snapshots)")
        snapshot_columns = {row[1] for row in cursor.fetchall()}
        if "evidence_fingerprint" not in snapshot_columns:
            try:
                cursor.execute("ALTER TABLE person_profile_snapshots ADD COLUMN evidence_fingerprint TEXT")
            except sqlite3.OperationalError as exc:
                cursor.execute("PRAGMA table_info(person_profile_snapshots)")
                current_columns = {row[1] for row in cursor.fetchall()}
                if "evidence_fingerprint" not in current_columns:
                    raise RuntimeError(
                        "Schema迁移失败 (person_profile_snapshots.evidence_fingerprint)"
                    ) from exc
        if "fact_claim_ids_json" not in snapshot_columns:
            cursor.execute("ALTER TABLE person_profile_snapshots ADD COLUMN fact_claim_ids_json TEXT")

    @staticmethod
    def _ensure_columns(
        cursor: sqlite3.Cursor,
        table: str,
        migrations: Dict[str, str],
    ) -> None:
        """严格补齐正式版本字段，失败时直接暴露迁移错误。"""
        cursor.execute(f"PRAGMA table_info({table})")
        existing = {str(row[1]) for row in cursor.fetchall()}
        for column, sql in migrations.items():
            if column not in existing:
                cursor.execute(sql)
        cursor.execute(f"PRAGMA table_info({table})")
        migrated = {str(row[1]) for row in cursor.fetchall()}
        missing = sorted(set(migrations) - migrated)
        if missing:
            raise RuntimeError(f"Schema迁移失败 ({table}: {', '.join(missing)})")

    def _ensure_lifecycle_columns(self, cursor: sqlite3.Cursor) -> None:
        """建立关系级生命周期和段落显式过期字段。"""
        self._ensure_columns(
            cursor,
            "paragraphs",
            {
                "expires_at": "ALTER TABLE paragraphs ADD COLUMN expires_at REAL",
                "deletion_reason": "ALTER TABLE paragraphs ADD COLUMN deletion_reason TEXT",
            },
        )
        relation_columns = {
            "retention_strength": (
                "ALTER TABLE relations ADD COLUMN retention_strength REAL NOT NULL DEFAULT 1.0"
            ),
            "retention_anchor_at": "ALTER TABLE relations ADD COLUMN retention_anchor_at REAL",
            "next_lifecycle_at": "ALTER TABLE relations ADD COLUMN next_lifecycle_at REAL",
            "reinforcement_count": (
                "ALTER TABLE relations ADD COLUMN reinforcement_count INTEGER NOT NULL DEFAULT 0"
            ),
            "lifecycle_revision": (
                "ALTER TABLE relations ADD COLUMN lifecycle_revision INTEGER NOT NULL DEFAULT 0"
            ),
            "inactive_reason": "ALTER TABLE relations ADD COLUMN inactive_reason TEXT",
            "last_access_reinforced_at": (
                "ALTER TABLE relations ADD COLUMN last_access_reinforced_at REAL"
            ),
        }
        deleted_relation_columns = {
            key: sql.replace("ALTER TABLE relations", "ALTER TABLE deleted_relations")
            for key, sql in relation_columns.items()
        }
        self._ensure_columns(cursor, "relations", relation_columns)
        self._ensure_columns(cursor, "deleted_relations", deleted_relation_columns)

        migration_epoch = datetime.now().timestamp()
        cursor.execute(
            """
            UPDATE relations
            SET retention_strength = MIN(1.0, MAX(0.0, COALESCE(retention_strength, 1.0))),
                retention_anchor_at = COALESCE(retention_anchor_at, ?),
                next_lifecycle_at = CASE
                    WHEN is_pinned = 1 OR is_permanent = 1 THEN NULL
                    ELSE COALESCE(next_lifecycle_at, ?)
                END,
                reinforcement_count = COALESCE(reinforcement_count, 0),
                lifecycle_revision = COALESCE(lifecycle_revision, 0),
                inactive_reason = CASE
                    WHEN is_inactive = 1 THEN COALESCE(inactive_reason, 'schema_19_migrated_inactive')
                    ELSE inactive_reason
                END,
                is_pinned = CASE WHEN is_permanent = 1 THEN 1 ELSE is_pinned END
            """,
            (migration_epoch, migration_epoch),
        )
        cursor.execute(
            """
            UPDATE deleted_relations
            SET retention_strength = MIN(1.0, MAX(0.0, COALESCE(retention_strength, 1.0))),
                retention_anchor_at = COALESCE(retention_anchor_at, ?),
                reinforcement_count = COALESCE(reinforcement_count, 0),
                lifecycle_revision = COALESCE(lifecycle_revision, 0),
                inactive_reason = COALESCE(inactive_reason, 'deleted')
            """,
            (migration_epoch,),
        )
        cursor.execute(
            """
            UPDATE paragraphs
            SET deletion_reason = 'schema_19_migrated_soft_delete'
            WHERE is_deleted = 1 AND deletion_reason IS NULL
            """
        )
        self._ensure_lifecycle_not_null_constraints()
        cursor.execute("DROP INDEX IF EXISTS idx_relations_lifecycle_due")
        cursor.execute(
            """
            CREATE INDEX idx_relations_lifecycle_due
            ON relations(is_inactive, next_lifecycle_at, hash)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_relations_inactive_reason
            ON relations(is_inactive, inactive_reason, inactive_since)
            """
        )
        # SQLite 表重建会删除原表所属索引，迁移后必须恢复全新库的基础索引集合。
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_relations_vector ON relations(vector_index)",
            "CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(subject)",
            "CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(object)",
            (
                "CREATE INDEX IF NOT EXISTS idx_relations_inactive "
                "ON relations(is_inactive, inactive_since)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_relations_protected "
                "ON relations(is_pinned, protected_until)"
            ),
        ):
            cursor.execute(statement)
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_paragraphs_expiration
            ON paragraphs(is_deleted, expires_at)
            """
        )

    @staticmethod
    def _ensure_relation_graph_projection_tables(cursor: sqlite3.Cursor) -> None:
        """建立关系活跃态到持久化图的可重试投影队列。"""

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS relation_graph_projection_jobs (
                relation_hash TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                object TEXT NOT NULL,
                desired_active INTEGER NOT NULL CHECK(desired_active IN (0, 1)),
                desired_lifecycle_revision INTEGER NOT NULL,
                job_revision INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'running', 'failed')),
                attempt_count INTEGER NOT NULL DEFAULT 0,
                lease_token TEXT,
                lease_expires_at REAL,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_relation_graph_projection_ready
            ON relation_graph_projection_jobs(status, lease_expires_at, updated_at, relation_hash)
            """
        )
        cursor.execute("DROP TRIGGER IF EXISTS trg_relations_graph_projection_lifecycle")
        cursor.execute(
            """
            CREATE TRIGGER trg_relations_graph_projection_lifecycle
            AFTER UPDATE OF is_inactive ON relations
            WHEN COALESCE(OLD.is_inactive, 0) != COALESCE(NEW.is_inactive, 0)
            BEGIN
                INSERT INTO relation_graph_projection_jobs (
                    relation_hash, subject, object, desired_active,
                    desired_lifecycle_revision, job_revision, status,
                    attempt_count, lease_token, lease_expires_at, last_error,
                    created_at, updated_at
                ) VALUES (
                    NEW.hash, NEW.subject, NEW.object,
                    CASE WHEN COALESCE(NEW.is_inactive, 0) = 0 THEN 1 ELSE 0 END,
                    COALESCE(NEW.lifecycle_revision, 0), 1, 'pending',
                    0, NULL, NULL, NULL,
                    CAST(strftime('%s', 'now') AS REAL),
                    CAST(strftime('%s', 'now') AS REAL)
                )
                ON CONFLICT(relation_hash) DO UPDATE SET
                    subject = excluded.subject,
                    object = excluded.object,
                    desired_active = excluded.desired_active,
                    desired_lifecycle_revision = excluded.desired_lifecycle_revision,
                    job_revision = relation_graph_projection_jobs.job_revision + 1,
                    status = 'pending',
                    attempt_count = 0,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    last_error = NULL,
                    updated_at = excluded.updated_at;
            END
            """
        )

    @staticmethod
    def _seed_relation_graph_projection_jobs(cursor: sqlite3.Cursor) -> None:
        """迁移时为全部现存关系建立权威基线，不能信任升级前图快照。"""

        migration_epoch = datetime.now().timestamp()
        cursor.execute(
            """
            INSERT INTO relation_graph_projection_jobs (
                relation_hash, subject, object, desired_active,
                desired_lifecycle_revision, job_revision, status,
                attempt_count, lease_token, lease_expires_at, last_error,
                created_at, updated_at
            )
            SELECT hash, subject, object,
                   CASE WHEN COALESCE(is_inactive, 0) = 0 THEN 1 ELSE 0 END,
                   COALESCE(lifecycle_revision, 0), 1, 'pending',
                   0, NULL, NULL, NULL, ?, ?
            FROM relations
            WHERE 1 = 1
            ON CONFLICT(relation_hash) DO UPDATE SET
                subject = excluded.subject,
                object = excluded.object,
                desired_active = excluded.desired_active,
                desired_lifecycle_revision = excluded.desired_lifecycle_revision,
                job_revision = relation_graph_projection_jobs.job_revision + 1,
                status = 'pending',
                attempt_count = 0,
                lease_token = NULL,
                lease_expires_at = NULL,
                last_error = NULL,
                updated_at = excluded.updated_at
            """,
            (migration_epoch, migration_epoch),
        )
    def _ensure_lifecycle_not_null_constraints(self) -> None:
        """把历史 ALTER 列收敛为与全新数据库一致的 NOT NULL 契约。"""
        tables_to_rebuild: List[str] = []
        for table in ("relations", "deleted_relations"):
            columns = {
                str(row["name"]): row
                for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            anchor = columns.get("retention_anchor_at")
            if anchor is None:
                raise RuntimeError(f"Schema迁移失败 ({table}.retention_anchor_at 缺失)")
            if int(anchor["notnull"] or 0) != 1:
                tables_to_rebuild.append(table)
        if not tables_to_rebuild:
            return

        column_names = [
            "hash",
            "subject",
            "predicate",
            "object",
            "vector_index",
            "confidence",
            "vector_state",
            "vector_updated_at",
            "vector_error",
            "vector_retry_count",
            "created_at",
            "source_paragraph",
            "metadata",
            "is_permanent",
            "last_accessed",
            "access_count",
            "last_access_reinforced_at",
            "is_inactive",
            "inactive_since",
            "is_pinned",
            "protected_until",
            "last_reinforced",
            "retention_strength",
            "retention_anchor_at",
            "next_lifecycle_at",
            "reinforcement_count",
            "lifecycle_revision",
            "inactive_reason",
        ]
        self._conn.commit()
        foreign_keys_enabled = bool(self._conn.execute("PRAGMA foreign_keys").fetchone()[0])
        if foreign_keys_enabled:
            self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            for table in tables_to_rebuild:
                temporary_table = f"{table}_schema20_rebuild"
                schema_objects = self._conn.execute(
                    """
                    SELECT type, name, sql
                    FROM sqlite_master
                    WHERE tbl_name = ?
                      AND type IN ('index', 'trigger')
                      AND sql IS NOT NULL
                    ORDER BY CASE type WHEN 'index' THEN 0 ELSE 1 END, name
                    """,
                    (table,),
                ).fetchall()
                self._conn.execute(f"DROP TABLE IF EXISTS {temporary_table}")
                deleted_at_column = (
                    ",\n                        deleted_at REAL"
                    if table == "deleted_relations"
                    else ""
                )
                unique_constraint = (
                    ",\n                        UNIQUE(subject, predicate, object)"
                    if table == "relations"
                    else ""
                )
                self._conn.execute(
                    f"""
                    CREATE TABLE {temporary_table} (
                        hash TEXT PRIMARY KEY,
                        subject TEXT NOT NULL,
                        predicate TEXT NOT NULL,
                        object TEXT NOT NULL,
                        vector_index INTEGER,
                        confidence REAL DEFAULT 1.0,
                        vector_state TEXT DEFAULT 'none',
                        vector_updated_at REAL,
                        vector_error TEXT,
                        vector_retry_count INTEGER DEFAULT 0,
                        created_at REAL,
                        source_paragraph TEXT,
                        metadata TEXT,
                        is_permanent BOOLEAN DEFAULT 0,
                        last_accessed REAL,
                        access_count INTEGER DEFAULT 0,
                        last_access_reinforced_at REAL,
                        is_inactive BOOLEAN DEFAULT 0,
                        inactive_since REAL,
                        is_pinned BOOLEAN DEFAULT 0,
                        protected_until REAL,
                        last_reinforced REAL,
                        retention_strength REAL NOT NULL DEFAULT 1.0,
                        retention_anchor_at REAL NOT NULL,
                        next_lifecycle_at REAL,
                        reinforcement_count INTEGER NOT NULL DEFAULT 0,
                        lifecycle_revision INTEGER NOT NULL DEFAULT 0,
                        inactive_reason TEXT{deleted_at_column}{unique_constraint}
                    )
                    """
                )
                copied_columns = [
                    *column_names,
                    *(["deleted_at"] if table == "deleted_relations" else []),
                ]
                columns_sql = ", ".join(copied_columns)
                self._conn.execute(
                    f"INSERT INTO {temporary_table} ({columns_sql}) SELECT {columns_sql} FROM {table}"
                )
                self._conn.execute(f"DROP TABLE {table}")
                self._conn.execute(f"ALTER TABLE {temporary_table} RENAME TO {table}")
                for schema_object in schema_objects:
                    sql = str(schema_object["sql"] or "").strip()
                    if not sql:
                        raise RuntimeError(
                            f"Schema迁移失败 ({table}.{schema_object['name']} 定义为空)"
                        )
                    self._conn.execute(sql)

            violations = self._conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                summary = self._format_foreign_key_violation_summary(list(violations))
                raise RuntimeError(
                    f"Schema迁移后外键校验失败: {len(violations)} 项，详情={summary}"
                )
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        finally:
            if foreign_keys_enabled:
                self._conn.execute("PRAGMA foreign_keys = ON")

    def _ensure_external_memory_refs_foreign_key(self, cursor: sqlite3.Cursor) -> None:
        """重建外部引用表，使悬空映射不可能再次产生。"""
        cursor.execute("PRAGMA foreign_key_list(external_memory_refs)")
        has_paragraph_fk = any(
            str(row[2]) == "paragraphs" and str(row[3]) == "paragraph_hash" and str(row[6]).upper() == "CASCADE"
            for row in cursor.fetchall()
        )
        if has_paragraph_fk:
            return

        cursor.execute("SELECT COUNT(*) FROM external_memory_refs")
        before_count = int(cursor.fetchone()[0])
        cursor.execute("DROP TABLE IF EXISTS external_memory_refs_schema_19")
        cursor.execute(
            """
            CREATE TABLE external_memory_refs_schema_19 (
                external_id TEXT PRIMARY KEY,
                paragraph_hash TEXT NOT NULL,
                source_type TEXT,
                created_at REAL NOT NULL,
                metadata_json TEXT,
                FOREIGN KEY (paragraph_hash) REFERENCES paragraphs(hash) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO external_memory_refs_schema_19 (
                external_id, paragraph_hash, source_type, created_at, metadata_json
            )
            SELECT r.external_id, r.paragraph_hash, r.source_type, r.created_at, r.metadata_json
            FROM external_memory_refs r
            JOIN paragraphs p ON p.hash = r.paragraph_hash
            """
        )
        after_count = int(cursor.rowcount if cursor.rowcount >= 0 else 0)
        cursor.execute("DROP TABLE external_memory_refs")
        cursor.execute("ALTER TABLE external_memory_refs_schema_19 RENAME TO external_memory_refs")
        cursor.execute(
            """
            CREATE INDEX idx_external_memory_refs_paragraph
            ON external_memory_refs(paragraph_hash)
            """
        )
        if before_count != after_count:
            logger.warning(f"Schema 19 清理悬空 external memory ref: {before_count - after_count} 项")

    @staticmethod
    def _ensure_storage_cleanup_tables(cursor: sqlite3.Cursor) -> None:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS storage_cleanup_jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                action TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                expected_state_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL,
                lease_token TEXT,
                lease_until REAL,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL,
                UNIQUE(operation_id, resource_type, resource_id, action),
                FOREIGN KEY (operation_id) REFERENCES delete_operations(operation_id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_storage_cleanup_ready
            ON storage_cleanup_jobs(status, next_attempt_at, lease_until, created_at)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_storage_cleanup_operation
            ON storage_cleanup_jobs(operation_id, status, job_id)
            """
        )

    @staticmethod
    def _ensure_fact_tables(cursor: sqlite3.Cursor) -> None:
        for statement in FACT_SCHEMA_STATEMENTS:
            cursor.execute(statement)

    def _ensure_episode_columns(self, cursor: sqlite3.Cursor) -> None:
        """补齐 Episode 分组输入指纹列。"""
        cursor.execute("PRAGMA table_info(episodes)")
        episode_columns = {row[1] for row in cursor.fetchall()}
        if "input_fingerprint" not in episode_columns:
            try:
                cursor.execute("ALTER TABLE episodes ADD COLUMN input_fingerprint TEXT")
            except sqlite3.OperationalError as exc:
                cursor.execute("PRAGMA table_info(episodes)")
                current_columns = {row[1] for row in cursor.fetchall()}
                if "input_fingerprint" not in current_columns:
                    raise RuntimeError("Schema迁移失败 (episodes.input_fingerprint)") from exc

    def _ensure_episode_source_revision_columns(self, cursor: sqlite3.Cursor) -> None:
        """把 Episode 调度切换为来源级 revision/outbox。"""
        self._ensure_columns(
            cursor,
            "episode_rebuild_sources",
            {
                "desired_revision": (
                    "ALTER TABLE episode_rebuild_sources "
                    "ADD COLUMN desired_revision INTEGER NOT NULL DEFAULT 1"
                ),
                "built_revision": (
                    "ALTER TABLE episode_rebuild_sources "
                    "ADD COLUMN built_revision INTEGER NOT NULL DEFAULT 0"
                ),
                "claimed_revision": "ALTER TABLE episode_rebuild_sources ADD COLUMN claimed_revision INTEGER",
                "dirty_start": "ALTER TABLE episode_rebuild_sources ADD COLUMN dirty_start REAL",
                "dirty_end": "ALTER TABLE episode_rebuild_sources ADD COLUMN dirty_end REAL",
                "first_requested_at": (
                    "ALTER TABLE episode_rebuild_sources ADD COLUMN first_requested_at REAL"
                ),
                "ready_at": "ALTER TABLE episode_rebuild_sources ADD COLUMN ready_at REAL",
                "lease_token": "ALTER TABLE episode_rebuild_sources ADD COLUMN lease_token TEXT",
                "lease_until": "ALTER TABLE episode_rebuild_sources ADD COLUMN lease_until REAL",
                "next_attempt_at": (
                    "ALTER TABLE episode_rebuild_sources ADD COLUMN next_attempt_at REAL"
                ),
                "built_generation_hash": (
                    "ALTER TABLE episode_rebuild_sources ADD COLUMN built_generation_hash TEXT"
                ),
                "claimed_generation_hash": (
                    "ALTER TABLE episode_rebuild_sources ADD COLUMN claimed_generation_hash TEXT"
                ),
                "retry_revision": "ALTER TABLE episode_rebuild_sources ADD COLUMN retry_revision INTEGER",
                "retry_generation_hash": (
                    "ALTER TABLE episode_rebuild_sources ADD COLUMN retry_generation_hash TEXT"
                ),
            },
        )
        now = datetime.now().timestamp()
        cursor.execute(
            """
            UPDATE episode_rebuild_sources
            SET desired_revision = MAX(1, COALESCE(desired_revision, 1)),
                built_revision = CASE
                    WHEN status = 'done' THEN MAX(1, COALESCE(desired_revision, 1))
                    ELSE MIN(COALESCE(built_revision, 0), MAX(0, COALESCE(desired_revision, 1) - 1))
                END,
                status = CASE WHEN status = 'running' THEN 'pending' ELSE status END,
                first_requested_at = COALESCE(first_requested_at, requested_at, ?),
                ready_at = COALESCE(ready_at, requested_at, ?),
                next_attempt_at = COALESCE(next_attempt_at, requested_at, ?),
                lease_token = NULL,
                lease_until = NULL,
                claimed_revision = NULL,
                claimed_generation_hash = NULL,
                retry_revision = NULL,
                retry_generation_hash = NULL
            """,
            (now, now, now),
        )

        cursor.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'episode_pending_paragraphs'
            """
        )
        if cursor.fetchone() is not None:
            cursor.execute(
                """
                INSERT INTO episode_rebuild_sources (
                    source, status, retry_count, last_error, reason,
                    requested_at, updated_at, desired_revision, built_revision,
                    first_requested_at, ready_at, next_attempt_at
                )
                SELECT source, 'pending', 0, NULL, 'schema_19_pending_migration',
                       MIN(updated_at), ?, 1, 0, MIN(updated_at), MIN(updated_at), MIN(updated_at)
                FROM episode_pending_paragraphs
                WHERE source IS NOT NULL AND TRIM(source) != ''
                GROUP BY source
                ON CONFLICT(source) DO UPDATE SET
                    desired_revision = episode_rebuild_sources.desired_revision + 1,
                    status = 'pending',
                    reason = 'schema_19_pending_migration',
                    updated_at = excluded.updated_at,
                    ready_at = MIN(episode_rebuild_sources.ready_at, excluded.ready_at),
                    next_attempt_at = MIN(episode_rebuild_sources.next_attempt_at, excluded.next_attempt_at)
                """,
                (now,),
            )
            cursor.execute("DROP TABLE episode_pending_paragraphs")

        cursor.execute(
            """
            INSERT INTO episode_rebuild_sources (
                source, status, retry_count, last_error, reason,
                requested_at, updated_at, desired_revision, built_revision,
                first_requested_at, ready_at, next_attempt_at
            )
            SELECT source, 'pending', 0, NULL, 'schema_19_source_discovery',
                   ?, ?, 1, 0, ?, ?, ?
            FROM (
                SELECT DISTINCT source
                FROM paragraphs
                WHERE source IS NOT NULL AND TRIM(source) != ''
                  AND (is_deleted IS NULL OR is_deleted = 0)
                UNION
                SELECT DISTINCT source
                FROM episodes
                WHERE source IS NOT NULL AND TRIM(source) != ''
            ) discovered
            WHERE 1 = 1
            ON CONFLICT(source) DO NOTHING
            """,
            (now, now, now, now, now),
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_rebuild_claim
            ON episode_rebuild_sources(lease_until, next_attempt_at, ready_at, first_requested_at)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_rebuild_revision
            ON episode_rebuild_sources(desired_revision, built_revision)
            """
        )

    def _ensure_fuzzy_modify_plan_tables(self, cursor: sqlite3.Cursor) -> None:
        """补齐模糊修改计划表，用于预览、确认、执行和追溯。"""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_fuzzy_modify_plans (
                plan_id TEXT PRIMARY KEY,
                request_text TEXT NOT NULL,
                scope TEXT NOT NULL,
                target_person_id TEXT,
                target_chat_id TEXT,
                status TEXT NOT NULL,
                confidence REAL DEFAULT 0,
                plan_json TEXT NOT NULL,
                preview_json TEXT,
                execution_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                executed_at REAL,
                requested_by TEXT,
                reason TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_fuzzy_modify_plans_created
            ON memory_fuzzy_modify_plans(created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_fuzzy_modify_plans_status_updated
            ON memory_fuzzy_modify_plans(status, updated_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_fuzzy_modify_plans_target
            ON memory_fuzzy_modify_plans(target_person_id, target_chat_id)
        """)

    def _initialize_tables(self) -> None:
        """初始化数据库表结构"""
        cursor = self._conn.cursor()

        # 段落表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS paragraphs (
                hash TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                vector_index INTEGER,
                created_at REAL,
                updated_at REAL,
                metadata TEXT,
                source TEXT,
                word_count INTEGER,
                event_time REAL,
                event_time_start REAL,
                event_time_end REAL,
                time_granularity TEXT,
                time_confidence REAL DEFAULT 1.0,
                knowledge_type TEXT DEFAULT 'mixed',
                is_permanent BOOLEAN DEFAULT 0,
                last_accessed REAL,
                access_count INTEGER DEFAULT 0,
                is_deleted INTEGER DEFAULT 0,
                deleted_at REAL,
                expires_at REAL,
                deletion_reason TEXT
            )
        """)

        # 实体表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                hash TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                vector_index INTEGER,
                appearance_count INTEGER DEFAULT 1,
                created_at REAL,
                metadata TEXT,
                is_deleted INTEGER DEFAULT 0,
                deleted_at REAL
            )
        """)

        # 关系表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS relations (
                hash TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                vector_index INTEGER,
                confidence REAL DEFAULT 1.0,
                vector_state TEXT DEFAULT 'none',
                vector_updated_at REAL,
                vector_error TEXT,
                vector_retry_count INTEGER DEFAULT 0,
                created_at REAL,
                source_paragraph TEXT,
                metadata TEXT,
                is_permanent BOOLEAN DEFAULT 0,
                last_accessed REAL,
                access_count INTEGER DEFAULT 0,
                last_access_reinforced_at REAL,
                is_inactive BOOLEAN DEFAULT 0,
                inactive_since REAL,
                is_pinned BOOLEAN DEFAULT 0,
                protected_until REAL,
                last_reinforced REAL,
                retention_strength REAL NOT NULL DEFAULT 1.0,
                retention_anchor_at REAL NOT NULL,
                next_lifecycle_at REAL,
                reinforcement_count INTEGER NOT NULL DEFAULT 0,
                lifecycle_revision INTEGER NOT NULL DEFAULT 0,
                inactive_reason TEXT,
                UNIQUE(subject, predicate, object)
            )
        """)

        # 回收站关系表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deleted_relations (
                hash TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                vector_index INTEGER,
                confidence REAL DEFAULT 1.0,
                vector_state TEXT DEFAULT 'none',
                vector_updated_at REAL,
                vector_error TEXT,
                vector_retry_count INTEGER DEFAULT 0,
                created_at REAL,
                source_paragraph TEXT,
                metadata TEXT,
                is_permanent BOOLEAN DEFAULT 0,
                last_accessed REAL,
                access_count INTEGER DEFAULT 0,
                last_access_reinforced_at REAL,
                is_inactive BOOLEAN DEFAULT 0,
                inactive_since REAL,
                is_pinned BOOLEAN DEFAULT 0,
                protected_until REAL,
                last_reinforced REAL,
                retention_strength REAL NOT NULL DEFAULT 1.0,
                retention_anchor_at REAL NOT NULL,
                next_lifecycle_at REAL,
                reinforcement_count INTEGER NOT NULL DEFAULT 0,
                lifecycle_revision INTEGER NOT NULL DEFAULT 0,
                inactive_reason TEXT,
                deleted_at REAL
            )
        """)

        # 32位哈希别名映射（用于 vNext 唯一解析）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS relation_hash_aliases (
                alias32 TEXT PRIMARY KEY,
                hash TEXT NOT NULL
            )
        """)

        # Schema 版本
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at REAL NOT NULL
            )
        """)

        # 三元组与段落的关联表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS paragraph_relations (
                paragraph_hash TEXT NOT NULL,
                relation_hash TEXT NOT NULL,
                PRIMARY KEY (paragraph_hash, relation_hash),
                FOREIGN KEY (paragraph_hash) REFERENCES paragraphs(hash) ON DELETE CASCADE,
                FOREIGN KEY (relation_hash) REFERENCES relations(hash) ON DELETE CASCADE
            )
        """)

        # 实体与段落的关联表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS paragraph_entities (
                paragraph_hash TEXT NOT NULL,
                entity_hash TEXT NOT NULL,
                mention_count INTEGER DEFAULT 1,
                PRIMARY KEY (paragraph_hash, entity_hash),
                FOREIGN KEY (paragraph_hash) REFERENCES paragraphs(hash) ON DELETE CASCADE,
                FOREIGN KEY (entity_hash) REFERENCES entities(hash) ON DELETE CASCADE
            )
        """)

        # 创建索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paragraphs_vector
            ON paragraphs(vector_index)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_entities_vector
            ON entities(vector_index)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_relations_vector
            ON relations(vector_index)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_relations_subject
            ON relations(subject)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_relations_object
            ON relations(object)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_entities_name
            ON entities(name)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paragraphs_source
            ON paragraphs(source)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paragraphs_deleted
            ON paragraphs(is_deleted, deleted_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_entities_deleted
            ON entities(is_deleted, deleted_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_relations_inactive
            ON relations(is_inactive, inactive_since)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_relations_protected
            ON relations(is_pinned, protected_until)
        """)

        # 人物画像开关表（按 stream_id + user_id 维度）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS person_profile_switches (
                stream_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY (stream_id, user_id)
            )
        """)

        # 人物画像快照表（版本化）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS person_profile_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id TEXT NOT NULL,
                profile_version INTEGER NOT NULL,
                profile_text TEXT NOT NULL,
                aliases_json TEXT,
                relation_edges_json TEXT,
                vector_evidence_json TEXT,
                evidence_ids_json TEXT,
                evidence_fingerprint TEXT,
                fact_claim_ids_json TEXT,
                updated_at REAL NOT NULL,
                expires_at REAL,
                source_note TEXT,
                UNIQUE(person_id, profile_version)
            )
        """)
        self._ensure_person_profile_snapshot_columns(cursor)

        # 已开启范围内的活跃人物集合
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS person_profile_active_persons (
                stream_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                person_id TEXT NOT NULL,
                last_seen_at REAL NOT NULL,
                PRIMARY KEY (stream_id, user_id, person_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS person_profile_overrides (
                person_id TEXT PRIMARY KEY,
                override_text TEXT NOT NULL,
                updated_at REAL NOT NULL,
                updated_by TEXT,
                source TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_person_profile_switches_enabled
            ON person_profile_switches(enabled)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_person_profile_snapshots_person
            ON person_profile_snapshots(person_id, updated_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_person_profile_active_seen
            ON person_profile_active_persons(last_seen_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_person_profile_overrides_updated
            ON person_profile_overrides(updated_at DESC)
        """)

        # Episode 情景记忆表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                episode_id TEXT PRIMARY KEY,
                source TEXT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                event_time_start REAL,
                event_time_end REAL,
                time_granularity TEXT,
                time_confidence REAL DEFAULT 1.0,
                participants_json TEXT,
                keywords_json TEXT,
                evidence_ids_json TEXT,
                paragraph_count INTEGER DEFAULT 0,
                llm_confidence REAL DEFAULT 0.0,
                segmentation_model TEXT,
                segmentation_version TEXT,
                input_fingerprint TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        self._ensure_episode_columns(cursor)

        # Episode -> Paragraph 映射
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS episode_paragraphs (
                episode_id TEXT NOT NULL,
                paragraph_hash TEXT NOT NULL,
                position INTEGER DEFAULT 0,
                PRIMARY KEY (episode_id, paragraph_hash),
                FOREIGN KEY (episode_id) REFERENCES episodes(episode_id) ON DELETE CASCADE,
                FOREIGN KEY (paragraph_hash) REFERENCES paragraphs(hash) ON DELETE CASCADE
            )
        """)

        # Episode 来源级 revision/outbox（异步）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS episode_rebuild_sources (
                source TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                reason TEXT,
                requested_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                desired_revision INTEGER NOT NULL DEFAULT 1,
                built_revision INTEGER NOT NULL DEFAULT 0,
                claimed_revision INTEGER,
                dirty_start REAL,
                dirty_end REAL,
                first_requested_at REAL,
                ready_at REAL,
                lease_token TEXT,
                lease_until REAL,
                next_attempt_at REAL,
                built_generation_hash TEXT,
                claimed_generation_hash TEXT,
                retry_revision INTEGER,
                retry_generation_hash TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_source_time_end
            ON episodes(source, event_time_end DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_updated_at
            ON episodes(updated_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episode_paragraphs_paragraph
            ON episode_paragraphs(paragraph_hash)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episode_rebuild_status_updated
            ON episode_rebuild_sources(status, updated_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episode_rebuild_updated_at
            ON episode_rebuild_sources(updated_at DESC)
        """)
        self._ensure_episode_source_revision_columns(cursor)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS paragraph_vector_backfill (
                paragraph_hash TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paragraph_vector_backfill_status_updated
            ON paragraph_vector_backfill(status, updated_at)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_feedback_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_tool_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL,
                query_timestamp REAL NOT NULL,
                due_at REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                attempt_count INTEGER DEFAULT 0,
                query_snapshot_json TEXT,
                decision_json TEXT,
                last_error TEXT,
                rollback_status TEXT DEFAULT 'none',
                rollback_plan_json TEXT,
                rollback_result_json TEXT,
                rollback_error TEXT,
                rollback_requested_by TEXT,
                rollback_reason TEXT,
                rollback_requested_at REAL,
                rolled_back_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_feedback_tasks_status_due
            ON memory_feedback_tasks(status, due_at, updated_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_feedback_tasks_session_query
            ON memory_feedback_tasks(session_id, query_timestamp DESC)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_feedback_action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                query_tool_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                target_hash TEXT,
                before_json TEXT,
                after_json TEXT,
                reason TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY (task_id) REFERENCES memory_feedback_tasks(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_feedback_action_logs_task
            ON memory_feedback_action_logs(task_id, created_at ASC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_feedback_action_logs_query
            ON memory_feedback_action_logs(query_tool_id, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_feedback_action_logs_target
            ON memory_feedback_action_logs(target_hash)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS paragraph_stale_relation_marks (
                paragraph_hash TEXT NOT NULL,
                relation_hash TEXT NOT NULL,
                query_tool_id TEXT,
                task_id INTEGER,
                reason TEXT,
                source_type TEXT,
                source_id TEXT,
                source_operation_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (paragraph_hash, relation_hash),
                FOREIGN KEY (paragraph_hash) REFERENCES paragraphs(hash) ON DELETE CASCADE,
                FOREIGN KEY (relation_hash) REFERENCES relations(hash) ON DELETE CASCADE,
                FOREIGN KEY (task_id) REFERENCES memory_feedback_tasks(id) ON DELETE SET NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paragraph_stale_relation_marks_paragraph
            ON paragraph_stale_relation_marks(paragraph_hash, updated_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paragraph_stale_relation_marks_relation
            ON paragraph_stale_relation_marks(relation_hash, updated_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paragraph_stale_relation_marks_updated
            ON paragraph_stale_relation_marks(updated_at DESC)
        """)
        self._ensure_paragraph_stale_relation_mark_columns(cursor)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paragraph_stale_relation_marks_source
            ON paragraph_stale_relation_marks(source_type, source_id, updated_at DESC)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS person_profile_refresh_queue (
                person_id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                reason TEXT,
                source_query_tool_id TEXT,
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                requested_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_person_profile_refresh_queue_status_updated
            ON person_profile_refresh_queue(status, updated_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_person_profile_refresh_queue_requested
            ON person_profile_refresh_queue(requested_at DESC)
        """)
        self._ensure_memory_feedback_task_columns(cursor)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS external_memory_refs (
                external_id TEXT PRIMARY KEY,
                paragraph_hash TEXT NOT NULL,
                source_type TEXT,
                created_at REAL NOT NULL,
                metadata_json TEXT,
                FOREIGN KEY (paragraph_hash) REFERENCES paragraphs(hash) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_external_memory_refs_paragraph
            ON external_memory_refs(paragraph_hash)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_v5_operations (
                operation_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                target TEXT,
                reason TEXT,
                updated_by TEXT,
                created_at REAL NOT NULL,
                resolved_hashes_json TEXT,
                result_json TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_v5_operations_created
            ON memory_v5_operations(created_at DESC)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS delete_operations (
                operation_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                selector TEXT,
                reason TEXT,
                requested_by TEXT,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                restored_at REAL,
                summary_json TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_delete_operations_created
            ON delete_operations(created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_delete_operations_mode
            ON delete_operations(mode, created_at DESC)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS delete_operation_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_hash TEXT,
                item_key TEXT,
                payload_json TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY (operation_id) REFERENCES delete_operations(operation_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_delete_operation_items_operation
            ON delete_operation_items(operation_id, id ASC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_delete_operation_items_hash
            ON delete_operation_items(item_hash)
        """)
        self._ensure_storage_cleanup_tables(cursor)
        self._ensure_fact_tables(cursor)
        self._ensure_lifecycle_columns(cursor)
        self._ensure_relation_graph_projection_tables(cursor)
        self._ensure_external_memory_refs_foreign_key(cursor)
        self._ensure_fuzzy_modify_plan_tables(cursor)
        self._create_temporal_indexes_if_ready()
        self._create_performance_indexes()
        # 新版 schema 包含完整字段，直接写入版本信息
        cursor.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, datetime.now().timestamp()),
        )
        self._conn.commit()
        logger.debug("数据库表结构初始化完成")

    def _migrate_schema(self) -> None:
        """执行数据库schema迁移"""
        cursor = self._conn.cursor()
        self._ensure_person_profile_snapshot_columns(cursor)

        # vNext 关键表兜底：历史库可能缺失，需在迁移阶段主动补齐。
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS relation_hash_aliases (
                alias32 TEXT PRIMARY KEY,
                hash TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at REAL NOT NULL
            )
        """)

        # Episode MVP 表结构补齐
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                episode_id TEXT PRIMARY KEY,
                source TEXT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                event_time_start REAL,
                event_time_end REAL,
                time_granularity TEXT,
                time_confidence REAL DEFAULT 1.0,
                participants_json TEXT,
                keywords_json TEXT,
                evidence_ids_json TEXT,
                paragraph_count INTEGER DEFAULT 0,
                llm_confidence REAL DEFAULT 0.0,
                segmentation_model TEXT,
                segmentation_version TEXT,
                input_fingerprint TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        self._ensure_episode_columns(cursor)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS episode_paragraphs (
                episode_id TEXT NOT NULL,
                paragraph_hash TEXT NOT NULL,
                position INTEGER DEFAULT 0,
                PRIMARY KEY (episode_id, paragraph_hash),
                FOREIGN KEY (episode_id) REFERENCES episodes(episode_id) ON DELETE CASCADE,
                FOREIGN KEY (paragraph_hash) REFERENCES paragraphs(hash) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS episode_rebuild_sources (
                source TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                reason TEXT,
                requested_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                desired_revision INTEGER NOT NULL DEFAULT 1,
                built_revision INTEGER NOT NULL DEFAULT 0,
                claimed_revision INTEGER,
                dirty_start REAL,
                dirty_end REAL,
                first_requested_at REAL,
                ready_at REAL,
                lease_token TEXT,
                lease_until REAL,
                next_attempt_at REAL,
                built_generation_hash TEXT,
                claimed_generation_hash TEXT,
                retry_revision INTEGER,
                retry_generation_hash TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_source_time_end
            ON episodes(source, event_time_end DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_updated_at
            ON episodes(updated_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episode_paragraphs_paragraph
            ON episode_paragraphs(paragraph_hash)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episode_rebuild_status_updated
            ON episode_rebuild_sources(status, updated_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episode_rebuild_updated_at
            ON episode_rebuild_sources(updated_at DESC)
        """)
        self._ensure_episode_source_revision_columns(cursor)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS paragraph_vector_backfill (
                paragraph_hash TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paragraph_vector_backfill_status_updated
            ON paragraph_vector_backfill(status, updated_at)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_feedback_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_tool_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL,
                query_timestamp REAL NOT NULL,
                due_at REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                attempt_count INTEGER DEFAULT 0,
                query_snapshot_json TEXT,
                decision_json TEXT,
                last_error TEXT,
                rollback_status TEXT DEFAULT 'none',
                rollback_plan_json TEXT,
                rollback_result_json TEXT,
                rollback_error TEXT,
                rollback_requested_by TEXT,
                rollback_reason TEXT,
                rollback_requested_at REAL,
                rolled_back_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_feedback_tasks_status_due
            ON memory_feedback_tasks(status, due_at, updated_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_feedback_tasks_session_query
            ON memory_feedback_tasks(session_id, query_timestamp DESC)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_feedback_action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                query_tool_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                target_hash TEXT,
                before_json TEXT,
                after_json TEXT,
                reason TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY (task_id) REFERENCES memory_feedback_tasks(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_feedback_action_logs_task
            ON memory_feedback_action_logs(task_id, created_at ASC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_feedback_action_logs_query
            ON memory_feedback_action_logs(query_tool_id, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_feedback_action_logs_target
            ON memory_feedback_action_logs(target_hash)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS paragraph_stale_relation_marks (
                paragraph_hash TEXT NOT NULL,
                relation_hash TEXT NOT NULL,
                query_tool_id TEXT,
                task_id INTEGER,
                reason TEXT,
                source_type TEXT,
                source_id TEXT,
                source_operation_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (paragraph_hash, relation_hash),
                FOREIGN KEY (paragraph_hash) REFERENCES paragraphs(hash) ON DELETE CASCADE,
                FOREIGN KEY (relation_hash) REFERENCES relations(hash) ON DELETE CASCADE,
                FOREIGN KEY (task_id) REFERENCES memory_feedback_tasks(id) ON DELETE SET NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paragraph_stale_relation_marks_paragraph
            ON paragraph_stale_relation_marks(paragraph_hash, updated_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paragraph_stale_relation_marks_relation
            ON paragraph_stale_relation_marks(relation_hash, updated_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paragraph_stale_relation_marks_updated
            ON paragraph_stale_relation_marks(updated_at DESC)
        """)
        self._ensure_paragraph_stale_relation_mark_columns(cursor)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paragraph_stale_relation_marks_source
            ON paragraph_stale_relation_marks(source_type, source_id, updated_at DESC)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS person_profile_refresh_queue (
                person_id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                reason TEXT,
                source_query_tool_id TEXT,
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                requested_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_person_profile_refresh_queue_status_updated
            ON person_profile_refresh_queue(status, updated_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_person_profile_refresh_queue_requested
            ON person_profile_refresh_queue(requested_at DESC)
        """)
        self._ensure_memory_feedback_task_columns(cursor)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS external_memory_refs (
                external_id TEXT PRIMARY KEY,
                paragraph_hash TEXT NOT NULL,
                source_type TEXT,
                created_at REAL NOT NULL,
                metadata_json TEXT,
                FOREIGN KEY (paragraph_hash) REFERENCES paragraphs(hash) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_external_memory_refs_paragraph
            ON external_memory_refs(paragraph_hash)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_v5_operations (
                operation_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                target TEXT,
                reason TEXT,
                updated_by TEXT,
                created_at REAL NOT NULL,
                resolved_hashes_json TEXT,
                result_json TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_v5_operations_created
            ON memory_v5_operations(created_at DESC)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS delete_operations (
                operation_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                selector TEXT,
                reason TEXT,
                requested_by TEXT,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                restored_at REAL,
                summary_json TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_delete_operations_created
            ON delete_operations(created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_delete_operations_mode
            ON delete_operations(mode, created_at DESC)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS delete_operation_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_hash TEXT,
                item_key TEXT,
                payload_json TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY (operation_id) REFERENCES delete_operations(operation_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_delete_operation_items_operation
            ON delete_operation_items(operation_id, id ASC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_delete_operation_items_hash
            ON delete_operation_items(item_hash)
        """)
        self._ensure_storage_cleanup_tables(cursor)
        self._ensure_fact_tables(cursor)
        self._ensure_lifecycle_columns(cursor)
        self._ensure_relation_graph_projection_tables(cursor)
        self._ensure_external_memory_refs_foreign_key(cursor)
        self._ensure_fuzzy_modify_plan_tables(cursor)

        # 检查paragraphs表是否有knowledge_type列
        cursor.execute("PRAGMA table_info(paragraphs)")
        columns = [row[1] for row in cursor.fetchall()]

        if "knowledge_type" not in columns:
            logger.info("检测到旧版schema，正在迁移添加knowledge_type字段...")
            try:
                cursor.execute("""
                    ALTER TABLE paragraphs
                    ADD COLUMN knowledge_type TEXT DEFAULT 'mixed'
                """)
                self._conn.commit()
                logger.info("Schema迁移完成：已添加knowledge_type字段")
            except sqlite3.OperationalError as e:
                logger.warning(f"Schema迁移失败（可能已存在）: {e}")

        # 问题2: 时序字段迁移
        cursor.execute("PRAGMA table_info(paragraphs)")
        columns = [row[1] for row in cursor.fetchall()]
        temporal_columns = {
            "event_time": "ALTER TABLE paragraphs ADD COLUMN event_time REAL",
            "event_time_start": "ALTER TABLE paragraphs ADD COLUMN event_time_start REAL",
            "event_time_end": "ALTER TABLE paragraphs ADD COLUMN event_time_end REAL",
            "time_granularity": "ALTER TABLE paragraphs ADD COLUMN time_granularity TEXT",
            "time_confidence": "ALTER TABLE paragraphs ADD COLUMN time_confidence REAL DEFAULT 1.0",
        }
        for col, sql in temporal_columns.items():
            if col not in columns:
                try:
                    cursor.execute(sql)
                except sqlite3.OperationalError as e:
                    logger.warning(f"Schema迁移失败（{col}）: {e}")

        # 时序索引（仅在列存在时创建，兼容旧库迁移）
        self._create_temporal_indexes_if_ready()
        self._conn.commit()

        # 检查paragraphs表是否有is_permanent列
        cursor.execute("PRAGMA table_info(paragraphs)")
        columns = [row[1] for row in cursor.fetchall()]

        if "is_permanent" not in columns:
            logger.info("正在迁移: 添加记忆动态字段...")
            try:
                # 段落表
                cursor.execute("ALTER TABLE paragraphs ADD COLUMN is_permanent BOOLEAN DEFAULT 0")
                cursor.execute("ALTER TABLE paragraphs ADD COLUMN last_accessed REAL")
                cursor.execute("ALTER TABLE paragraphs ADD COLUMN access_count INTEGER DEFAULT 0")

                # 关系表
                cursor.execute("ALTER TABLE relations ADD COLUMN is_permanent BOOLEAN DEFAULT 0")
                cursor.execute("ALTER TABLE relations ADD COLUMN last_accessed REAL")
                cursor.execute("ALTER TABLE relations ADD COLUMN access_count INTEGER DEFAULT 0")

                self._conn.commit()
                logger.info("Schema迁移完成：已添加记忆动态字段")
            except sqlite3.OperationalError as e:
                logger.warning(f"Schema迁移失败: {e}")

        # 检查relations表是否有is_inactive列 (V5 Memory System)
        cursor.execute("PRAGMA table_info(relations)")
        columns = [row[1] for row in cursor.fetchall()]

        if "is_inactive" not in columns:
            logger.info("正在迁移: 添加V5记忆动态字段 (inactive, protected)...")
            try:
                # 关系表 V5 新增字段
                cursor.execute("ALTER TABLE relations ADD COLUMN is_inactive BOOLEAN DEFAULT 0")
                cursor.execute("ALTER TABLE relations ADD COLUMN inactive_since REAL")
                cursor.execute("ALTER TABLE relations ADD COLUMN is_pinned BOOLEAN DEFAULT 0")
                cursor.execute("ALTER TABLE relations ADD COLUMN protected_until REAL")
                cursor.execute("ALTER TABLE relations ADD COLUMN last_reinforced REAL")

                # 为回收站创建 deleted_relations 表
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS deleted_relations (
                        hash TEXT PRIMARY KEY,
                        subject TEXT NOT NULL,
                        predicate TEXT NOT NULL,
                        object TEXT NOT NULL,
                        vector_index INTEGER,
                        confidence REAL DEFAULT 1.0,
                        vector_state TEXT DEFAULT 'none',
                        vector_updated_at REAL,
                        vector_error TEXT,
                        vector_retry_count INTEGER DEFAULT 0,
                        created_at REAL,
                        source_paragraph TEXT,
                        metadata TEXT,
                        is_permanent BOOLEAN DEFAULT 0,
                        last_accessed REAL,
                        access_count INTEGER DEFAULT 0,
                        is_inactive BOOLEAN DEFAULT 0,
                        inactive_since REAL,
                        is_pinned BOOLEAN DEFAULT 0,
                        protected_until REAL,
                        last_reinforced REAL,
                        deleted_at REAL  -- 用于记录删除时间的额外列
                    )
                """)

                self._conn.commit()
                logger.info("Schema迁移完成：已添加V5记忆动态字段及回收站表")
            except sqlite3.OperationalError as e:
                logger.warning(f"Schema迁移失败 (V5): {e}")

        # 关系向量状态字段迁移
        cursor.execute("PRAGMA table_info(relations)")
        relation_columns = {row[1] for row in cursor.fetchall()}
        relation_vector_columns = {
            "vector_state": "ALTER TABLE relations ADD COLUMN vector_state TEXT DEFAULT 'none'",
            "vector_updated_at": "ALTER TABLE relations ADD COLUMN vector_updated_at REAL",
            "vector_error": "ALTER TABLE relations ADD COLUMN vector_error TEXT",
            "vector_retry_count": "ALTER TABLE relations ADD COLUMN vector_retry_count INTEGER DEFAULT 0",
        }
        for col, sql in relation_vector_columns.items():
            if col not in relation_columns:
                try:
                    cursor.execute(sql)
                except sqlite3.OperationalError as e:
                    logger.warning(f"Schema迁移失败 (relations.{col}): {e}")

        # 回收站同步字段迁移（用于 restore 保留向量状态）
        cursor.execute("PRAGMA table_info(deleted_relations)")
        deleted_relation_columns = {row[1] for row in cursor.fetchall()}
        deleted_relation_vector_columns = {
            "vector_state": "ALTER TABLE deleted_relations ADD COLUMN vector_state TEXT DEFAULT 'none'",
            "vector_updated_at": "ALTER TABLE deleted_relations ADD COLUMN vector_updated_at REAL",
            "vector_error": "ALTER TABLE deleted_relations ADD COLUMN vector_error TEXT",
            "vector_retry_count": "ALTER TABLE deleted_relations ADD COLUMN vector_retry_count INTEGER DEFAULT 0",
        }
        for col, sql in deleted_relation_vector_columns.items():
            if col not in deleted_relation_columns:
                try:
                    cursor.execute(sql)
                except sqlite3.OperationalError as e:
                    logger.warning(f"Schema迁移失败 (deleted_relations.{col}): {e}")

        # 检查 entities 表是否有 is_deleted 列 (Soft Delete System)
        cursor.execute("PRAGMA table_info(entities)")
        columns = [row[1] for row in cursor.fetchall()]

        if "is_deleted" not in columns:
            logger.info("正在迁移: 添加软删除字段 (Soft Delete)...")
            try:
                # 实体表
                cursor.execute("ALTER TABLE entities ADD COLUMN is_deleted INTEGER DEFAULT 0")
                cursor.execute("ALTER TABLE entities ADD COLUMN deleted_at REAL")

                # 段落表
                cursor.execute("ALTER TABLE paragraphs ADD COLUMN is_deleted INTEGER DEFAULT 0")
                cursor.execute("ALTER TABLE paragraphs ADD COLUMN deleted_at REAL")

                self._conn.commit()
                logger.info("Schema迁移完成：已添加软删除字段")
            except sqlite3.OperationalError as e:
                logger.warning(f"Schema迁移失败 (Soft Delete): {e}")

        # 数据修复: 检查是否存在 source/vector_index 列错位的情况
        # 症状: vector_index (本应是int) 变成了文件名字符串, source (本应是文件名) 变成了类型字符串
        try:
            cursor.execute("""
                SELECT count(*) FROM paragraphs
                WHERE typeof(vector_index) = 'text'
                AND source IN ('mixed', 'factual', 'narrative', 'structured', 'auto')
            """)
            count = cursor.fetchone()[0]
            if count > 0:
                logger.warning(f"检测到 {count} 条数据存在列错位（文件名误存入vector_index），正在自动修复...")
                cursor.execute("""
                    UPDATE paragraphs
                    SET
                        knowledge_type = source,
                        source = vector_index,
                        vector_index = NULL
                    WHERE typeof(vector_index) = 'text'
                    AND source IN ('mixed', 'factual', 'narrative', 'structured', 'auto')
                """)
                self._conn.commit()
                logger.info(f"自动修复完成: 已校正 {cursor.rowcount} 条数据")
        except Exception as e:
            logger.error(f"数据自动修复失败: {e}")

        # 升级前的图快照不能作为活跃态事实源。迁移完成时为全部关系建立
        # 最终状态任务，SDK 启动阶段会在开放检索前完成权威重放。
        self._seed_relation_graph_projection_jobs(cursor)
        self._create_performance_indexes()
        self._conn.commit()

    def _create_temporal_indexes_if_ready(self) -> None:
        """
        仅当时序列已存在时创建索引。

        旧库升级时，_initialize_tables 不能提前对不存在的列建索引；
        因此统一在迁移阶段按列存在性安全创建。
        """
        cursor = self._conn.cursor()
        cursor.execute("PRAGMA table_info(paragraphs)")
        columns = {row[1] for row in cursor.fetchall()}

        if "event_time" in columns:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_paragraphs_event_time ON paragraphs(event_time)")
        if "event_time_start" in columns:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_paragraphs_event_start ON paragraphs(event_time_start)")
        if "event_time_end" in columns:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_paragraphs_event_end ON paragraphs(event_time_end)")

    def _create_performance_indexes(self) -> None:
        """创建热点查询使用的补充索引。"""
        cursor = self._conn.cursor()
        cursor.execute("PRAGMA table_info(paragraphs)")
        paragraph_columns = {row[1] for row in cursor.fetchall()}
        cursor.execute("PRAGMA table_info(relations)")
        relation_columns = {row[1] for row in cursor.fetchall()}

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_paragraph_relations_relation
            ON paragraph_relations(relation_hash, paragraph_hash)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_paragraph_entities_entity
            ON paragraph_entities(entity_hash, paragraph_hash)
            """
        )
        if {"source", "is_deleted", "created_at", "hash"}.issubset(paragraph_columns):
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_paragraphs_source_live_created
                ON paragraphs(source, is_deleted, created_at, hash)
                """
            )
        if {"subject", "object", "is_inactive"}.issubset(relation_columns):
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_relations_subject_object_active
                ON relations(LOWER(TRIM(subject)), LOWER(TRIM(object)), is_inactive)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_relations_object_active
                ON relations(LOWER(TRIM(object)), is_inactive)
                """
            )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_paragraph_vector_backfill_status_retry_updated
            ON paragraph_vector_backfill(status, retry_count, updated_at)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_rebuild_status_retry_updated
            ON episode_rebuild_sources(status, retry_count, requested_at, updated_at)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_person_profile_refresh_status_retry_updated
            ON person_profile_refresh_queue(status, retry_count, requested_at, updated_at)
            """
        )

    def run_legacy_migration_for_vnext(self) -> Dict[str, Any]:
        """
        离线迁移入口：
        - 复用旧迁移逻辑补齐历史库字段
        - 重建 relation 32位别名
        - 归一化历史 knowledge_type
        - 写入 vNext schema 版本
        """
        self._migrate_schema()
        alias_result = self.rebuild_relation_hash_aliases()
        knowledge_type_result = self.normalize_paragraph_knowledge_types()
        fact_result = self.backfill_person_fact_claims()
        self.set_schema_version(SCHEMA_VERSION)
        return {
            "schema_version": SCHEMA_VERSION,
            "alias_result": alias_result,
            "knowledge_type_result": knowledge_type_result,
            "fact_result": fact_result,
        }

    def list_invalid_paragraph_knowledge_types(self) -> List[str]:
        """列出当前库中不合法的段落 knowledge_type。"""

        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT knowledge_type
            FROM paragraphs
            WHERE knowledge_type IS NULL
               OR TRIM(COALESCE(knowledge_type, '')) = ''
               OR LOWER(TRIM(knowledge_type)) NOT IN ({placeholders})
            ORDER BY knowledge_type
            """.format(placeholders=", ".join("?" for _ in allowed_knowledge_type_values())),
            tuple(allowed_knowledge_type_values()),
        )
        invalid: List[str] = []
        for row in cursor.fetchall():
            raw = row[0]
            invalid.append(str(raw) if raw is not None else "")
        return invalid

    def normalize_paragraph_knowledge_types(self) -> Dict[str, Any]:
        """将历史非法 knowledge_type 归一化为合法值。"""

        cursor = self._conn.cursor()
        cursor.execute("SELECT hash, content, knowledge_type FROM paragraphs")
        rows = cursor.fetchall()

        normalized_count = 0
        normalized_map: Dict[str, int] = {}
        invalid_before: List[str] = []
        invalid_seen = set()

        for row in rows:
            paragraph_hash = str(row["hash"])
            content = str(row["content"] or "")
            raw_value = row["knowledge_type"]
            try:
                validate_stored_knowledge_type(raw_value)
                continue
            except ValueError:
                raw_text = str(raw_value) if raw_value is not None else ""
                if raw_text not in invalid_seen:
                    invalid_seen.add(raw_text)
                    invalid_before.append(raw_text)

            normalized_type = resolve_stored_knowledge_type(
                raw_value,
                content=content,
                allow_legacy=True,
                unknown_fallback=KnowledgeType.MIXED,
            )
            cursor.execute(
                "UPDATE paragraphs SET knowledge_type = ? WHERE hash = ?",
                (normalized_type.value, paragraph_hash),
            )
            normalized_count += 1
            normalized_map[normalized_type.value] = normalized_map.get(normalized_type.value, 0) + 1

        self._conn.commit()
        return {
            "normalized": normalized_count,
            "invalid_before": sorted(invalid_before),
            "normalized_to": normalized_map,
        }
