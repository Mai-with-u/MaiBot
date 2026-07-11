from datetime import datetime
from typing import Any, Dict, List

import sqlite3

from src.common.logger import get_logger

from .knowledge_types import (
    KnowledgeType,
    allowed_knowledge_type_values,
    resolve_stored_knowledge_type,
    validate_stored_knowledge_type,
)

logger = get_logger("A_Memorix.MetadataSchema")

SCHEMA_VERSION = 15
RUNTIME_AUTO_MIGRATION_MIN_SCHEMA_VERSION = 9


class MetadataSchemaMixin:
    """维护元数据数据库表结构、版本迁移与数据规范化。"""

    def _assert_schema_compatible(self, db_existed: bool) -> None:
        """运行时执行 post-1.0 自动迁移；legacy/vNext 仍要求离线迁移。"""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
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
        self._migrate_schema()
        alias_result = self.rebuild_relation_hash_aliases()
        knowledge_type_result = self.normalize_paragraph_knowledge_types()
        self.set_schema_version(SCHEMA_VERSION)
        logger.info(
            f"metadata schema 运行时自动迁移完成: {current_version} -> {SCHEMA_VERSION}, "
            f"alias_inserted={int(alias_result.get('inserted', 0) or 0)}, "
            f"knowledge_normalized={int(knowledge_type_result.get('normalized', 0) or 0)}",
        )

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
                        raise RuntimeError(
                            f"Schema迁移失败 (memory_feedback_tasks.{col})"
                        ) from e

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
                        raise RuntimeError(
                            f"Schema迁移失败 (paragraph_stale_relation_marks.{col})"
                        ) from e

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
                deleted_at REAL
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
                is_inactive BOOLEAN DEFAULT 0,
                inactive_since REAL,
                is_pinned BOOLEAN DEFAULT 0,
                protected_until REAL,
                last_reinforced REAL,
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
                is_inactive BOOLEAN DEFAULT 0,
                inactive_since REAL,
                is_pinned BOOLEAN DEFAULT 0,
                protected_until REAL,
                last_reinforced REAL,
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
                updated_at REAL NOT NULL,
                expires_at REAL,
                source_note TEXT,
                UNIQUE(person_id, profile_version)
            )
        """)

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
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)

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

        # Episode 生成队列（异步）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS episode_pending_paragraphs (
                paragraph_hash TEXT PRIMARY KEY,
                source TEXT,
                created_at REAL,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                updated_at REAL NOT NULL
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
                updated_at REAL NOT NULL
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
            CREATE INDEX IF NOT EXISTS idx_episode_pending_status_updated
            ON episode_pending_paragraphs(status, updated_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episode_pending_source_created
            ON episode_pending_paragraphs(source, created_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episode_rebuild_status_updated
            ON episode_rebuild_sources(status, updated_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episode_rebuild_updated_at
            ON episode_rebuild_sources(updated_at DESC)
        """)
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
                metadata_json TEXT
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
        self._ensure_fuzzy_modify_plan_tables(cursor)
        self._create_performance_indexes()
        # 新版 schema 包含完整字段，直接写入版本信息
        cursor.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)", (SCHEMA_VERSION, datetime.now().timestamp()))
        self._conn.commit()
        logger.debug("数据库表结构初始化完成")

    def _migrate_schema(self) -> None:
        """执行数据库schema迁移"""
        cursor = self._conn.cursor()

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
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
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
            CREATE TABLE IF NOT EXISTS episode_pending_paragraphs (
                paragraph_hash TEXT PRIMARY KEY,
                source TEXT,
                created_at REAL,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                updated_at REAL NOT NULL
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
                updated_at REAL NOT NULL
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
            CREATE INDEX IF NOT EXISTS idx_episode_pending_status_updated
            ON episode_pending_paragraphs(status, updated_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episode_pending_source_created
            ON episode_pending_paragraphs(source, created_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episode_rebuild_status_updated
            ON episode_rebuild_sources(status, updated_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episode_rebuild_updated_at
            ON episode_rebuild_sources(updated_at DESC)
        """)
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
                metadata_json TEXT
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
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_paragraphs_event_time ON paragraphs(event_time)"
            )
        if "event_time_start" in columns:
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_paragraphs_event_start ON paragraphs(event_time_start)"
            )
        if "event_time_end" in columns:
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_paragraphs_event_end ON paragraphs(event_time_end)"
            )

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
            CREATE INDEX IF NOT EXISTS idx_episode_pending_status_retry_updated
            ON episode_pending_paragraphs(status, retry_count, updated_at)
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
        self.set_schema_version(SCHEMA_VERSION)
        return {
            "schema_version": SCHEMA_VERSION,
            "alias_result": alias_result,
            "knowledge_type_result": knowledge_type_result,
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
