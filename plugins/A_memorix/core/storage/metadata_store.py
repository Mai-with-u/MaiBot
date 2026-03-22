"""
元数据存储模块

基于SQLite的元数据管理，存储段落、实体、关系等信息。
"""

import sqlite3
import pickle
import json
import uuid
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union, List, Dict, Any, Tuple

from src.common.logger import get_logger
from ..utils.hash import compute_hash, normalize_text
from ..utils.time_parser import normalize_time_meta
from .knowledge_types import (
    KnowledgeType,
    allowed_knowledge_type_values,
    resolve_stored_knowledge_type,
    validate_stored_knowledge_type,
)

logger = get_logger("A_Memorix.MetadataStore")


SCHEMA_VERSION = 8


class MetadataStore:
    """
    元数据存储类

    功能：
    - SQLite数据库管理
    - 段落/实体/关系元数据存储
    - 增删改查操作
    - 事务支持
    - 索引优化

    参数：
        data_dir: 数据目录
        db_name: 数据库文件名（默认metadata.db）
    """

    def __init__(
        self,
        data_dir: Optional[Union[str, Path]] = None,
        db_name: str = "metadata.db",
    ):
        """
        初始化元数据存储

        Args:
            data_dir: 数据目录
            db_name: 数据库文件名
        """
        self.data_dir = Path(data_dir) if data_dir else None
        self.db_name = db_name
        self._conn: Optional[sqlite3.Connection] = None
        self._is_initialized = False
        self._db_path: Optional[Path] = None

        logger.info(f"MetadataStore 初始化: db={db_name}")

    def connect(
        self,
        data_dir: Optional[Union[str, Path]] = None,
        *,
        enforce_schema: bool = True,
    ) -> None:
        """
        连接到数据库

        Args:
            data_dir: 数据目录（默认使用初始化时的目录）
        """
        if data_dir is None:
            data_dir = self.data_dir

        if data_dir is None:
            raise ValueError("未指定数据目录")

        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)

        db_path = data_dir / self.db_name
        db_existed = db_path.exists()
        self._db_path = db_path

        # 连接数据库
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row  # 使用字典式访问

        # 优化性能
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA cache_size=-64000")  # 64MB缓存
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.execute("PRAGMA foreign_keys = ON") # 开启外键约束支持级联删除

        logger.info(f"连接到数据库: {db_path}")

        # 初始化或校验 schema
        if not self._is_initialized:
            if not db_existed:
                self._initialize_tables()
            if enforce_schema:
                self._assert_schema_compatible(db_existed=db_existed)
            self._is_initialized = True

        # 初始化 FTS schema（幂等）
        try:
            self.ensure_fts_schema()
        except Exception as e:
            logger.warning(f"初始化 FTS schema 失败，将跳过 BM25 检索: {e}")

    def _assert_schema_compatible(self, db_existed: bool) -> None:
        """vNext 运行时只做 schema 版本校验，不做隐式迁移。"""
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
        if version != SCHEMA_VERSION:
            raise RuntimeError(
                f"metadata schema 版本不匹配: current={version}, expected={SCHEMA_VERSION}。"
                " 请执行 scripts/release_vnext_migrate.py migrate。"
            )

    def close(self) -> None:
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("数据库连接已关闭")

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

    def _resolve_conn(self, conn: Optional[sqlite3.Connection] = None) -> sqlite3.Connection:
        """解析可用连接。"""
        resolved = conn or self._conn
        if resolved is None:
            raise RuntimeError("MetadataStore 未连接数据库")
        return resolved

    def get_db_path(self) -> Path:
        """获取 SQLite 数据库文件路径。"""
        if self._db_path is not None:
            return self._db_path
        if self.data_dir is None:
            raise RuntimeError("MetadataStore 未配置 data_dir")
        return Path(self.data_dir) / self.db_name

    def ensure_fts_schema(self, conn: Optional[sqlite3.Connection] = None) -> bool:
        """
        确保 FTS5 schema 存在（幂等）。

        采用 external-content 方式，不在 FTS 表重复存储正文。
        """
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS paragraphs_fts
                USING fts5(
                    content,
                    content='paragraphs',
                    content_rowid='rowid',
                    tokenize='unicode61'
                )
            """)

            # insert trigger
            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS paragraphs_ai
                AFTER INSERT ON paragraphs
                BEGIN
                    INSERT INTO paragraphs_fts(rowid, content)
                    VALUES (new.rowid, new.content);
                END
            """)

            # delete trigger
            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS paragraphs_ad
                AFTER DELETE ON paragraphs
                BEGIN
                    INSERT INTO paragraphs_fts(paragraphs_fts, rowid, content)
                    VALUES ('delete', old.rowid, old.content);
                END
            """)

            # update trigger
            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS paragraphs_au
                AFTER UPDATE OF content ON paragraphs
                BEGIN
                    INSERT INTO paragraphs_fts(paragraphs_fts, rowid, content)
                    VALUES ('delete', old.rowid, old.content);
                    INSERT INTO paragraphs_fts(rowid, content)
                    VALUES (new.rowid, new.content);
                END
            """)
            c.commit()
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS5 schema 创建失败（可能不支持 FTS5）: {e}")
            c.rollback()
            return False

    def ensure_fts_backfilled(self, conn: Optional[sqlite3.Connection] = None) -> bool:
        """
        确保 FTS 索引已回填。

        当历史数据存在但 FTS 表为空/不一致时执行 rebuild。
        """
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("SELECT COUNT(1) AS n FROM paragraphs")
            para_count = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(1) AS n FROM paragraphs_fts")
            fts_count = int(cur.fetchone()[0])

            if para_count > 0 and fts_count != para_count:
                cur.execute("INSERT INTO paragraphs_fts(paragraphs_fts) VALUES ('rebuild')")
                c.commit()
                logger.info(f"FTS 回填完成: paragraphs={para_count}, fts={para_count}")
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS 回填失败: {e}")
            c.rollback()
            return False

    def ensure_relations_fts_schema(self, conn: Optional[sqlite3.Connection] = None) -> bool:
        """
        确保关系 FTS5 schema 存在（幂等）。

        注意：relations 表没有 content 列，因此使用独立 FTS 表并通过触发器同步。
        """
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS relations_fts
                USING fts5(
                    relation_hash UNINDEXED,
                    content,
                    tokenize='unicode61'
                )
            """)

            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS relations_ai
                AFTER INSERT ON relations
                BEGIN
                    INSERT INTO relations_fts(relation_hash, content)
                    VALUES (
                        new.hash,
                        COALESCE(new.subject, '') || ' ' || COALESCE(new.predicate, '') || ' ' || COALESCE(new.object, '')
                    );
                END
            """)

            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS relations_ad
                AFTER DELETE ON relations
                BEGIN
                    DELETE FROM relations_fts WHERE relation_hash = old.hash;
                END
            """)

            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS relations_au
                AFTER UPDATE OF subject, predicate, object ON relations
                BEGIN
                    DELETE FROM relations_fts WHERE relation_hash = new.hash;
                    INSERT INTO relations_fts(relation_hash, content)
                    VALUES (
                        new.hash,
                        COALESCE(new.subject, '') || ' ' || COALESCE(new.predicate, '') || ' ' || COALESCE(new.object, '')
                    );
                END
            """)
            c.commit()
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"relations FTS5 schema 创建失败（可能不支持 FTS5）: {e}")
            c.rollback()
            return False

    def ensure_relations_fts_backfilled(self, conn: Optional[sqlite3.Connection] = None) -> bool:
        """确保关系 FTS 索引已回填。"""
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("SELECT COUNT(1) AS n FROM relations")
            rel_count = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(1) AS n FROM relations_fts")
            fts_count = int(cur.fetchone()[0])

            if rel_count != fts_count:
                cur.execute("DELETE FROM relations_fts")
                cur.execute("""
                    INSERT INTO relations_fts(relation_hash, content)
                    SELECT
                        r.hash,
                        COALESCE(r.subject, '') || ' ' || COALESCE(r.predicate, '') || ' ' || COALESCE(r.object, '')
                    FROM relations r
                """)
                c.commit()
                logger.info(f"relations FTS 回填完成: relations={rel_count}, fts={rel_count}")
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"relations FTS 回填失败: {e}")
            c.rollback()
            return False

    def ensure_paragraph_ngram_schema(self, conn: Optional[sqlite3.Connection] = None) -> bool:
        """确保段落 ngram 倒排表存在。"""
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS paragraph_ngrams (
                    term TEXT NOT NULL,
                    paragraph_hash TEXT NOT NULL,
                    PRIMARY KEY (term, paragraph_hash),
                    FOREIGN KEY (paragraph_hash) REFERENCES paragraphs(hash) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_paragraph_ngrams_hash
                ON paragraph_ngrams(paragraph_hash)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS paragraph_ngram_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            c.commit()
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"paragraph ngram schema 创建失败: {e}")
            c.rollback()
            return False

    @staticmethod
    def _char_ngrams(text: str, n: int) -> List[str]:
        compact = "".join(str(text or "").lower().split())
        if not compact:
            return []
        if len(compact) < n:
            return [compact]
        return [compact[i : i + n] for i in range(0, len(compact) - n + 1)]

    def ensure_paragraph_ngram_backfilled(
        self,
        n: int = 2,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        确保段落 ngram 倒排索引已回填。

        仅在 n 变化或文档数量变化时重建，避免每次加载都全量重建。
        """
        c = self._resolve_conn(conn)
        cur = c.cursor()
        n = max(1, int(n))
        try:
            cur.execute("SELECT value FROM paragraph_ngram_meta WHERE key='ngram_n'")
            row = cur.fetchone()
            current_n = int(row[0]) if row and row[0] is not None else None

            cur.execute("SELECT COUNT(1) FROM paragraphs WHERE is_deleted IS NULL OR is_deleted = 0")
            para_count = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(DISTINCT paragraph_hash) FROM paragraph_ngrams")
            indexed_docs = int(cur.fetchone()[0])

            need_rebuild = (current_n != n) or (para_count != indexed_docs)
            if not need_rebuild:
                return True

            cur.execute("DELETE FROM paragraph_ngrams")
            cur.execute("""
                SELECT hash, content
                FROM paragraphs
                WHERE is_deleted IS NULL OR is_deleted = 0
            """)
            rows = cur.fetchall()

            batch: List[Tuple[str, str]] = []
            batch_size = 2000
            for row in rows:
                p_hash = str(row["hash"])
                terms = list(dict.fromkeys(self._char_ngrams(str(row["content"] or ""), n)))
                for term in terms:
                    batch.append((term, p_hash))
                if len(batch) >= batch_size:
                    cur.executemany(
                        "INSERT OR IGNORE INTO paragraph_ngrams(term, paragraph_hash) VALUES (?, ?)",
                        batch,
                    )
                    batch.clear()
            if batch:
                cur.executemany(
                    "INSERT OR IGNORE INTO paragraph_ngrams(term, paragraph_hash) VALUES (?, ?)",
                    batch,
                )

            cur.execute("""
                INSERT INTO paragraph_ngram_meta(key, value) VALUES('ngram_n', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (str(n),))
            cur.execute("""
                INSERT INTO paragraph_ngram_meta(key, value) VALUES('paragraph_count', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (str(para_count),))
            c.commit()
            logger.info(f"paragraph ngram 回填完成: n={n}, paragraphs={para_count}")
            return True
        except Exception as e:
            logger.warning(f"paragraph ngram 回填失败: {e}")
            c.rollback()
            return False

    def fts_upsert_paragraph(
        self,
        paragraph_hash: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        将段落写入（或覆盖）到 FTS 索引。
        """
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute(
                "SELECT rowid, content FROM paragraphs WHERE hash = ?",
                (paragraph_hash,),
            )
            row = cur.fetchone()
            if not row:
                return False
            rowid = int(row[0])
            content = str(row[1] or "")
            cur.execute(
                "INSERT OR REPLACE INTO paragraphs_fts(rowid, content) VALUES (?, ?)",
                (rowid, content),
            )
            c.commit()
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS upsert 失败: {e}")
            c.rollback()
            return False

    def fts_delete_paragraph(
        self,
        paragraph_hash: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        从 FTS 索引删除段落。
        """
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute(
                "SELECT rowid, content FROM paragraphs WHERE hash = ?",
                (paragraph_hash,),
            )
            row = cur.fetchone()
            if not row:
                return False
            rowid = int(row[0])
            content = str(row[1] or "")
            cur.execute(
                "INSERT INTO paragraphs_fts(paragraphs_fts, rowid, content) VALUES ('delete', ?, ?)",
                (rowid, content),
            )
            c.commit()
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS delete 失败: {e}")
            c.rollback()
            return False

    def fts_search_bm25(
        self,
        match_query: str,
        limit: int = 20,
        max_doc_len: int = 2000,
        conn: Optional[sqlite3.Connection] = None,
    ) -> List[Dict[str, Any]]:
        """
        使用 FTS5 + bm25 执行全文检索。
        """
        if not match_query.strip():
            return []

        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute(
                """
                SELECT p.hash, p.content, bm25(paragraphs_fts) AS bm25_score
                FROM paragraphs_fts
                JOIN paragraphs p ON p.rowid = paragraphs_fts.rowid
                WHERE paragraphs_fts MATCH ?
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                ORDER BY bm25_score ASC
                LIMIT ?
                """,
                (match_query, max(1, int(limit))),
            )
            rows = cur.fetchall()
            results: List[Dict[str, Any]] = []
            for row in rows:
                content = str(row["content"] or "")
                if max_doc_len > 0:
                    content = content[:max_doc_len]
                results.append(
                    {
                        "hash": row["hash"],
                        "content": content,
                        "bm25_score": float(row["bm25_score"]),
                    }
                )
            return results
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS 查询失败: {e}")
            return []

    def fts_search_relations_bm25(
        self,
        match_query: str,
        limit: int = 20,
        max_doc_len: int = 512,
        conn: Optional[sqlite3.Connection] = None,
    ) -> List[Dict[str, Any]]:
        """使用 FTS5 + bm25 执行关系全文检索。"""
        if not match_query.strip():
            return []

        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute(
                """
                SELECT
                    r.hash,
                    r.subject,
                    r.predicate,
                    r.object,
                    bm25(relations_fts) AS bm25_score
                FROM relations_fts
                JOIN relations r ON r.hash = relations_fts.relation_hash
                WHERE relations_fts MATCH ?
                ORDER BY bm25_score ASC
                LIMIT ?
                """,
                (match_query, max(1, int(limit))),
            )
            rows = cur.fetchall()
            out: List[Dict[str, Any]] = []
            for row in rows:
                content = f"{row['subject']} {row['predicate']} {row['object']}"
                if max_doc_len > 0:
                    content = content[:max_doc_len]
                out.append(
                    {
                        "hash": row["hash"],
                        "subject": row["subject"],
                        "predicate": row["predicate"],
                        "object": row["object"],
                        "content": content,
                        "bm25_score": float(row["bm25_score"]),
                    }
                )
            return out
        except sqlite3.OperationalError as e:
            logger.warning(f"relations FTS 查询失败: {e}")
            return []

    def ngram_search_paragraphs(
        self,
        tokens: List[str],
        limit: int = 20,
        max_doc_len: int = 2000,
        conn: Optional[sqlite3.Connection] = None,
    ) -> List[Dict[str, Any]]:
        """按 ngram 倒排索引检索段落，避免 LIKE 全表扫描。"""
        uniq = [t for t in dict.fromkeys([str(x).strip().lower() for x in tokens]) if t]
        if not uniq:
            return []

        c = self._resolve_conn(conn)
        cur = c.cursor()
        placeholders = ",".join(["?"] * len(uniq))
        try:
            cur.execute(
                f"""
                SELECT
                    p.hash,
                    p.content,
                    COUNT(*) AS hit_terms
                FROM paragraph_ngrams ng
                JOIN paragraphs p ON p.hash = ng.paragraph_hash
                WHERE ng.term IN ({placeholders})
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                GROUP BY p.hash, p.content
                ORDER BY hit_terms DESC
                LIMIT ?
                """,
                tuple(uniq + [max(1, int(limit))]),
            )
            rows = cur.fetchall()
            out: List[Dict[str, Any]] = []
            token_count = max(1, len(uniq))
            for row in rows:
                hit_terms = int(row["hit_terms"])
                score = float(hit_terms / token_count)
                content = str(row["content"] or "")
                if max_doc_len > 0:
                    content = content[:max_doc_len]
                out.append(
                    {
                        "hash": row["hash"],
                        "content": content,
                        "bm25_score": -score,
                        "fallback_score": score,
                    }
                )
            return out
        except sqlite3.OperationalError as e:
            logger.warning(f"ngram 倒排查询失败: {e}")
            return []

    def fts_doc_count(self, conn: Optional[sqlite3.Connection] = None) -> int:
        """获取 FTS 文档数量。"""
        c = self._resolve_conn(conn)
        cur = c.cursor()
        try:
            cur.execute("SELECT COUNT(1) FROM paragraphs_fts")
            return int(cur.fetchone()[0])
        except sqlite3.OperationalError:
            return 0

    def shrink_memory(self, conn: Optional[sqlite3.Connection] = None) -> None:
        """请求 SQLite 收缩当前连接缓存。"""
        c = self._resolve_conn(conn)
        try:
            c.execute("PRAGMA shrink_memory")
        except sqlite3.OperationalError:
            pass

    @staticmethod
    def _normalize_episode_source(source: Any) -> str:
        return str(source or "").strip()

    def _dedupe_episode_sources(self, sources: List[Any]) -> List[str]:
        normalized: List[str] = []
        seen = set()
        for item in sources or []:
            token = self._normalize_episode_source(item)
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        return normalized

    def _get_sources_for_paragraph_hashes(
        self,
        hashes: List[str],
        *,
        include_deleted: bool = True,
    ) -> List[str]:
        normalized_hashes = [
            str(item or "").strip()
            for item in (hashes or [])
            if str(item or "").strip()
        ]
        if not normalized_hashes:
            return []

        placeholders = ",".join(["?"] * len(normalized_hashes))
        conditions = ["hash IN ({})".format(placeholders), "TRIM(COALESCE(source, '')) != ''"]
        if not include_deleted:
            conditions.append("(is_deleted IS NULL OR is_deleted = 0)")

        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            SELECT DISTINCT TRIM(source) AS source
            FROM paragraphs
            WHERE {' AND '.join(conditions)}
            """,
            tuple(normalized_hashes),
        )
        return self._dedupe_episode_sources([row["source"] for row in cursor.fetchall()])

    def _enqueue_episode_source_rebuilds(self, sources: List[Any], reason: str = "") -> int:
        normalized_sources = self._dedupe_episode_sources(sources)
        if not normalized_sources:
            return 0

        now = datetime.now().timestamp()
        reason_text = str(reason or "").strip()[:200] or None
        cursor = self._conn.cursor()
        cursor.executemany(
            """
            INSERT INTO episode_rebuild_sources (
                source, status, retry_count, last_error, reason, requested_at, updated_at
            ) VALUES (?, 'pending', 0, NULL, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                status = 'pending',
                last_error = NULL,
                reason = excluded.reason,
                requested_at = excluded.requested_at,
                updated_at = excluded.updated_at
            """,
            [
                (source, reason_text, now, now)
                for source in normalized_sources
            ],
        )
        self._conn.commit()
        return len(normalized_sources)

    def add_paragraph(
        self,
        content: str,
        vector_index: Optional[int] = None,
        source: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        knowledge_type: str = "mixed",
        time_meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        添加段落

        Args:
            content: 段落内容
            vector_index: 向量索引
            source: 来源
            metadata: 额外元数据
            knowledge_type: 知识类型 (narrative/factual/quote/structured/mixed)
            time_meta: 时间元信息 (event_time/event_time_start/event_time_end/...)

        Returns:
            段落哈希值
        """
        content_normalized = normalize_text(content)
        hash_value = compute_hash(content_normalized)
        resolved_knowledge_type = validate_stored_knowledge_type(knowledge_type)

        now = datetime.now().timestamp()
        word_count = len(content_normalized.split())
        normalized_time = normalize_time_meta(time_meta)

        cursor = self._conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO paragraphs
                (
                    hash, content, vector_index, created_at, updated_at, metadata, source, word_count,
                    event_time, event_time_start, event_time_end, time_granularity, time_confidence,
                    knowledge_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                hash_value,
                content,
                vector_index,
                now,
                now,
                pickle.dumps(metadata or {}),
                source,
                word_count,
                normalized_time.get("event_time"),
                normalized_time.get("event_time_start"),
                normalized_time.get("event_time_end"),
                normalized_time.get("time_granularity"),
                normalized_time.get("time_confidence", 1.0),
                resolved_knowledge_type.value,
            ))
            self._conn.commit()
            try:
                self.enqueue_episode_source_rebuild(
                    source=source,
                    reason="paragraph_added",
                )
            except Exception as e:
                logger.warning(f"Episode source 重建入队失败: hash={hash_value[:16]}..., err={e}")
            logger.debug(
                f"添加段落: hash={hash_value[:16]}..., words={word_count}, type={resolved_knowledge_type.value}"
            )
            return hash_value
        except sqlite3.IntegrityError:
            logger.debug(f"段落已存在: {hash_value[:16]}...")
            # 尝试复活
            self.revive_if_deleted(paragraph_hashes=[hash_value])
            return hash_value

    def _canonicalize_name(self, name: str) -> str:
        """
        规范化名称 (统一小写并去除首尾空格)
        
        Args:
            name: 原始名称
            
        Returns:
            规范化后的名称
        """
        if not name:
            return ""
        return name.strip().lower()

    def add_entity(
        self,
        name: str,
        vector_index: Optional[int] = None,
        source_paragraph: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        添加实体
        
        Args:
            name: 实体名称
            vector_index: 向量索引
            source_paragraph: 来源段落哈希 (如果提供，将建立关联)
            metadata: 额外元数据
            
        Returns:
            实体哈希值
        """
        # 1. 规范化名称
        name_normalized = self._canonicalize_name(name)
        if not name_normalized:
            raise ValueError("Entity name cannot be empty")
            
        hash_value = compute_hash(name_normalized)
        now = datetime.now().timestamp()

        cursor = self._conn.cursor()
        
        # 2. 插入实体 (INSERT OR IGNORE)
        # 注意：这里我们保留原有的 name 字段存储，可以是 display name，
        # 但 hash 必须由 canonical name 生成。
        # 如果实体已存在，我们其实不一定要更新 name (保留第一次的 display name 往往更好)
        # 或者我们也可以选择不作为唯一键冲突，而是逻辑判断。
        # 考虑到 entities.hash 是主键，entities.name 是 UNIQUE。
        # 如果 name 大小写不同但 hash 相同 (冲突)，或者 name 不同但 canonical name 相同?
        # 由于 hash 是由 canonical name 算出来的，所以 hash 相同意味着 canonical name 相同。
        # 如果 db 中已存在的 name 是 "Apple"，新来的 name 是 "apple"，它们 canonical name 都是 "apple"，hash 一样。
        # 此时 INSERT OR IGNORE 会忽略。
        
        try:
            cursor.execute("""
                INSERT INTO entities
                (hash, name, vector_index, appearance_count, created_at, metadata)
                VALUES (?, ?, ?, 1, ?, ?)
            """, (
                hash_value,
                name,
                vector_index,
                now,
                pickle.dumps(metadata or {}),
            ))
            
            logger.debug(f"添加实体: {name} ({hash_value[:8]})")
            self._conn.commit()
            
            # 3. 建立来源关联
            if source_paragraph:
                self.link_paragraph_entity(source_paragraph, hash_value)
                
            return hash_value
            
        except sqlite3.IntegrityError:
            # 实体已存在
            # 1. 尝试复活 (自动复活)
            self.revive_if_deleted(entity_hashes=[hash_value])
            
            # 2. 更新计数
            cursor.execute("""
                UPDATE entities
                SET appearance_count = appearance_count + 1
                WHERE hash = ?
            """, (hash_value,))
            self._conn.commit()
            
            logger.debug(f"实体已存在(复活/计数+1): {name}")
            
            # 3. 建立来源关联
            if source_paragraph:
                self.link_paragraph_entity(source_paragraph, hash_value)
                
            return hash_value

    def add_relation(
        self,
        subject: str,
        predicate: str,
        obj: str,
        vector_index: Optional[int] = None,
        confidence: float = 1.0,
        source_paragraph: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        添加关系
        
        Args:
            subject: 主语
            predicate: 谓语
            obj: 宾语
            vector_index: 向量索引
            confidence: 置信度
            source_paragraph: 来源段落哈希
            metadata: 额外元数据
            
        Returns:
            关系哈希值
        """
        # 1. 规范化输入
        s_canon = self._canonicalize_name(subject)
        p_canon = self._canonicalize_name(predicate)
        o_canon = self._canonicalize_name(obj)
        
        if not all([s_canon, p_canon, o_canon]):
             raise ValueError("Relation components cannot be empty")

        # 2. 计算组合哈希
        # 公式: md5(s|p|o)
        relation_key = f"{s_canon}|{p_canon}|{o_canon}"
        hash_value = compute_hash(relation_key)

        now = datetime.now().timestamp()
        
        # 记录原始 display name 到 metadata (如果需要的话，或者直接存到 DB 字段)
        # 这里我们直接存入 subject, predicate, object 字段，
        # 注意：如果 DB 里已存在该关系 (hash 相同)，则不会更新这些字段，保留第一次的拼写。
        
        cursor = self._conn.cursor()
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO relations
                (hash, subject, predicate, object, vector_index, confidence, created_at, source_paragraph, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                hash_value,
                subject,  # 原始拼写
                predicate,
                obj,
                vector_index,
                confidence,
                now,
                source_paragraph, # 这里的 source_paragraph 仅作为 "首次发现地" 记录，也可留空
                pickle.dumps(metadata or {}),
            ))
            self._conn.commit()
            
            if cursor.rowcount > 0:
                logger.debug(f"添加关系: {subject} -{predicate}-> {obj}")
            else:
                logger.debug(f"关系已存在: {subject} -{predicate}-> {obj}")

            # 3. 建立来源关联 (幂等)
            # 无论关系是新创建的还是已存在的，只要提供了 source_paragraph，都要建立连接
            if source_paragraph:
                self.link_paragraph_relation(source_paragraph, hash_value)
                
            return hash_value
            
        except sqlite3.IntegrityError as e:
            logger.warning(f"添加关系异常: {e}")
            return hash_value

    def link_paragraph_relation(
        self,
        paragraph_hash: str,
        relation_hash: str,
    ) -> bool:
        """
        关联段落和关系 (幂等)
        """
        cursor = self._conn.cursor()
        try:
            # 使用 INSERT OR IGNORE 避免重复报错
            cursor.execute("""
                INSERT OR IGNORE INTO paragraph_relations
                (paragraph_hash, relation_hash)
                VALUES (?, ?)
            """, (paragraph_hash, relation_hash))
            self._conn.commit()
            self._enqueue_episode_source_rebuilds(
                self._get_sources_for_paragraph_hashes([paragraph_hash], include_deleted=True),
                reason="paragraph_relation_linked",
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def link_paragraph_entity(
        self,
        paragraph_hash: str,
        entity_hash: str,
        mention_count: int = 1,
    ) -> bool:
        """
        关联段落和实体 (幂等)
        """
        cursor = self._conn.cursor()
        try:
            # 首先尝试插入
            cursor.execute("""
                INSERT OR IGNORE INTO paragraph_entities
                (paragraph_hash, entity_hash, mention_count)
                VALUES (?, ?, ?)
            """, (paragraph_hash, entity_hash, mention_count))
            
            if cursor.rowcount == 0:
                # 如果已存在 (IGNORE生效)，则更新计数
                cursor.execute("""
                    UPDATE paragraph_entities
                    SET mention_count = mention_count + ?
                    WHERE paragraph_hash = ? AND entity_hash = ?
                """, (mention_count, paragraph_hash, entity_hash))
            
            self._conn.commit()
            self._enqueue_episode_source_rebuilds(
                self._get_sources_for_paragraph_hashes([paragraph_hash], include_deleted=True),
                reason="paragraph_entity_linked",
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def get_paragraph(self, hash_value: str) -> Optional[Dict[str, Any]]:
        """
        获取段落

        Args:
            hash_value: 段落哈希

        Returns:
            段落信息字典，不存在则返回None
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT * FROM paragraphs WHERE hash = ?
        """, (hash_value,))
        row = cursor.fetchone()

        if row:
            return self._row_to_dict(row, "paragraph")
        return None

    def update_paragraph_time_meta(
        self,
        paragraph_hash: str,
        time_meta: Dict[str, Any],
    ) -> bool:
        """
        更新段落时间元信息。
        """
        normalized = normalize_time_meta(time_meta)
        if not normalized:
            return False
        source_to_rebuild = self._get_sources_for_paragraph_hashes(
            [paragraph_hash],
            include_deleted=True,
        )

        updates: List[str] = []
        params: List[Any] = []
        for key in [
            "event_time",
            "event_time_start",
            "event_time_end",
            "time_granularity",
            "time_confidence",
        ]:
            if key in normalized:
                updates.append(f"{key} = ?")
                params.append(normalized[key])

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(datetime.now().timestamp())
        params.append(paragraph_hash)

        cursor = self._conn.cursor()
        cursor.execute(
            f"UPDATE paragraphs SET {', '.join(updates)} WHERE hash = ?",
            tuple(params),
        )
        self._conn.commit()
        changed = cursor.rowcount > 0
        if changed:
            self._enqueue_episode_source_rebuilds(
                source_to_rebuild,
                reason="paragraph_time_updated",
            )
        return changed

    def query_paragraphs_temporal(
        self,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 100,
        allow_created_fallback: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        查询时序命中的段落（区间相交语义）。
        """
        if limit <= 0:
            return []

        effective_start = "COALESCE(p.event_time_start, p.event_time, p.event_time_end"
        effective_end = "COALESCE(p.event_time_end, p.event_time, p.event_time_start"
        if allow_created_fallback:
            effective_start += ", p.created_at)"
            effective_end += ", p.created_at)"
        else:
            effective_start += ")"
            effective_end += ")"

        conditions = ["(p.is_deleted IS NULL OR p.is_deleted = 0)"]
        params: List[Any] = []

        if source:
            conditions.append("p.source = ?")
            params.append(source)

        if person:
            conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM paragraph_entities pe
                    JOIN entities e ON e.hash = pe.entity_hash
                    WHERE pe.paragraph_hash = p.hash
                      AND LOWER(e.name) LIKE ?
                )
                """
            )
            params.append(f"%{str(person).strip().lower()}%")

        if start_ts is not None and end_ts is not None:
            conditions.append(f"({effective_end} >= ? AND {effective_start} <= ?)")
            params.extend([start_ts, end_ts])
        elif start_ts is not None:
            conditions.append(f"({effective_end} >= ?)")
            params.append(start_ts)
        elif end_ts is not None:
            conditions.append(f"({effective_start} <= ?)")
            params.append(end_ts)

        where_sql = " AND ".join(conditions)
        sql = f"""
            SELECT p.*
            FROM paragraphs p
            WHERE {where_sql}
            ORDER BY {effective_end} DESC, p.updated_at DESC
            LIMIT ?
        """
        params.append(limit)

        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params))
        return [self._row_to_dict(row, "paragraph") for row in cursor.fetchall()]

    def get_entity(self, hash_value: str) -> Optional[Dict[str, Any]]:
        """
        获取实体

        Args:
            hash_value: 实体哈希

        Returns:
            实体信息字典，不存在则返回None
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT * FROM entities WHERE hash = ?
        """, (hash_value,))
        row = cursor.fetchone()

        if row:
            return self._row_to_dict(row, "entity")
        return None

    def get_relation(self, hash_value: str) -> Optional[Dict[str, Any]]:
        """
        获取关系

        Args:
            hash_value: 关系哈希

        Returns:
            关系信息字典，不存在则返回None
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT * FROM relations WHERE hash = ?
        """, (hash_value,))
        row = cursor.fetchone()

        if row:
            return self._row_to_dict(row, "relation")
        return None

    def get_paragraph_relations(self, paragraph_hash: str) -> List[Dict[str, Any]]:
        """
        获取段落的所有关系

        Args:
            paragraph_hash: 段落哈希

        Returns:
            关系列表
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT r.* FROM relations r
            JOIN paragraph_relations pr ON r.hash = pr.relation_hash
            WHERE pr.paragraph_hash = ?
        """, (paragraph_hash,))

        return [self._row_to_dict(row, "relation") for row in cursor.fetchall()]

    def get_paragraph_entities(self, paragraph_hash: str) -> List[Dict[str, Any]]:
        """
        获取段落的所有实体

        Args:
            paragraph_hash: 段落哈希

        Returns:
            实体列表
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT e.*, pe.mention_count
            FROM entities e
            JOIN paragraph_entities pe ON e.hash = pe.entity_hash
            WHERE pe.paragraph_hash = ?
        """, (paragraph_hash,))

        return [self._row_to_dict(row, "entity") for row in cursor.fetchall()]

    def get_paragraphs_by_entity(self, entity_name: str) -> List[Dict[str, Any]]:
        """
        获取包含指定实体的所有段落 (自动处理规范化)
        
        Args:
            entity_name: 实体名称 (支持任意大小写)
            
        Returns:
            段落列表
        """
        # 1. 计算规范化 Hash
        name_canon = self._canonicalize_name(entity_name)
        if not name_canon:
            return []
            
        entity_hash = compute_hash(name_canon)
        
        cursor = self._conn.cursor()
        # 2. 直接使用 Hash 查询中间表，完全避开 Name 匹配
        cursor.execute("""
            SELECT p.*
            FROM paragraphs p
            JOIN paragraph_entities pe ON p.hash = pe.paragraph_hash
            WHERE pe.entity_hash = ?
        """, (entity_hash,))

        return [self._row_to_dict(row, "paragraph") for row in cursor.fetchall()]

    def get_relations(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        object: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        查询关系（大小写不敏感）
        
        Args:
            subject: 主语（可选）
            predicate: 谓语（可选）
            object: 宾语（可选）
            
        Returns:
            关系列表
        """
        # 构建查询条件
        conditions = []
        params = []
        
        if subject:
            conditions.append("LOWER(subject) = ?")
            params.append(self._canonicalize_name(subject))
        if predicate:
            conditions.append("LOWER(predicate) = ?")
            params.append(self._canonicalize_name(predicate))
        if object:
            conditions.append("LOWER(object) = ?")
            params.append(self._canonicalize_name(object))
            
        sql = "SELECT * FROM relations"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
            
        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params))
        
        return [self._row_to_dict(row, "relation") for row in cursor.fetchall()]

    def get_all_triples(self) -> List[Tuple[str, str, str, str]]:
        """
        高效获取所有三元组 (subject, predicate, object, hash)
        直接返回元组，跳过字典转换和pickle反序列化，用于构建 V5 Map 缓存。
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT subject, predicate, object, hash FROM relations")
        return list(cursor.fetchall())

    def get_paragraphs_by_relation(self, relation_hash: str) -> List[Dict[str, Any]]:
        """
        获取支持指定关系的所有段落

        Args:
            relation_hash: 关系哈希

        Returns:
            段落列表
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT p.*
            FROM paragraphs p
            JOIN paragraph_relations pr ON p.hash = pr.paragraph_hash
            WHERE pr.relation_hash = ?
        """, (relation_hash,))

        return [self._row_to_dict(row, "paragraph") for row in cursor.fetchall()]

    def get_paragraphs_by_source(self, source: str) -> List[Dict[str, Any]]:
        """
        按来源获取段落

        Args:
            source: 来源标识符

        Returns:
            段落列表
        """
        return self.query("SELECT * FROM paragraphs WHERE source = ?", (source,))

    def get_all_sources(self) -> List[Dict[str, Any]]:
        """
        获取所有来源文件统计信息
        
        Returns:
            来源列表 [{'source': 'name', 'count': int, 'last_updated': timestamp}]
        """
        cursor = self._conn.cursor()
        # 排除 source 为 NULL 或空的记录
        cursor.execute("""
            SELECT source, COUNT(*) as count, MAX(created_at) as last_updated 
            FROM paragraphs 
            WHERE source IS NOT NULL AND source != ''
            GROUP BY source
            ORDER BY last_updated DESC
        """)
        
        results = []
        for row in cursor.fetchall():
            results.append({
                "source": row[0],
                "count": row[1],
                "last_updated": row[2]
            })
        return results


    def search_paragraphs_by_content(self, content_query: str) -> List[Dict[str, Any]]:
        """按内容模糊搜索段落"""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT * FROM paragraphs WHERE content LIKE ?
        """, (f"%{content_query}%",))
        return [self._row_to_dict(row, "paragraph") for row in cursor.fetchall()]

    def delete_paragraph(self, hash_value: str) -> bool:
        """
        删除段落（级联删除相关关联）

        Args:
            hash_value: 段落哈希

        Returns:
            是否成功删除
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            DELETE FROM paragraphs WHERE hash = ?
        """, (hash_value,))
        self._conn.commit()

        deleted = cursor.rowcount > 0
        if deleted:
            logger.info(f"删除段落: {hash_value[:16]}...")

        return deleted

    def delete_entity(self, hash_or_name: str) -> bool:
        """
        删除实体（级联删除相关关联）
        支持通过哈希值或名称删除
        
        注意：会同时删除所有引用该实体（作为主语或宾语）的关系
        """
        cursor = self._conn.cursor()
        
        # 1. 解析实体信息 (获取 Name 和 Hash)
        entity_name = None
        entity_hash = None
        
        # 尝试作为 Hash 查询
        cursor.execute("SELECT name, hash FROM entities WHERE hash = ?", (hash_or_name,))
        row = cursor.fetchone()
        if row:
            entity_name = row[0]
            entity_hash = row[1]
        else:
            # 尝试作为 Name 查询 (原始匹配)
            cursor.execute("SELECT name, hash FROM entities WHERE name = ?", (hash_or_name,))
            row = cursor.fetchone()
            if row:
                entity_name = row[0]
                entity_hash = row[1]
            else:
                # 最后的最后：尝试规范化名称 (Canonical) 查询，解决大小写或 WebUI 手动输入导致的不匹配
                name_canon = self._canonicalize_name(hash_or_name)
                canon_hash = compute_hash(name_canon)
                cursor.execute("SELECT name, hash FROM entities WHERE hash = ?", (canon_hash,))
                row = cursor.fetchone()
                if row:
                    entity_name = row[0]
                    entity_hash = row[1]
                
        if not entity_name or not entity_hash:
            logger.debug(f"删除实体请求跳过：未在元数据记录中找到 {hash_or_name}")
            return False

        logger.info(f"开始删除实体: {entity_name} (Hash: {entity_hash[:8]}...)")

        try:
            # 2. 查找相关关系 (Subject 或 Object 为该实体)
            cursor.execute("""
                SELECT hash FROM relations 
                WHERE subject = ? OR object = ?
            """, (entity_name, entity_name))
            
            relation_hashes = [r[0] for r in cursor.fetchall()]
            
            if relation_hashes:
                logger.info(f"发现 {len(relation_hashes)} 个相关关系，准备级联删除")
                
                # 3. 删除这些关系与段落的关联
                # SQLite 不支持直接 DELETE ... WHERE ... IN (...) 的列表参数，需要拼接占位符
                placeholders = ','.join(['?'] * len(relation_hashes))
                
                cursor.execute(f"""
                    DELETE FROM paragraph_relations 
                    WHERE relation_hash IN ({placeholders})
                """, relation_hashes)
                
                # 4. 删除关系本体
                cursor.execute(f"""
                    DELETE FROM relations 
                    WHERE hash IN ({placeholders})
                """, relation_hashes)
                
                logger.info("相关关系已级联删除")
            
            # 5. 删除实体与段落的关联
            cursor.execute("DELETE FROM paragraph_entities WHERE entity_hash = ?", (entity_hash,))
            
            # 6. 删除实体本体
            cursor.execute("DELETE FROM entities WHERE hash = ?", (entity_hash,))
            
            self._conn.commit()
            logger.info("实体删除完成")
            return True
            
        except Exception as e:
            logger.error(f"删除实体时发生错误: {e}")
            self._conn.rollback()
            return False

    def delete_relation(self, hash_value: str) -> bool:
        """
        删除关系（级联删除相关关联）

        Args:
            hash_value: 关系哈希

        Returns:
            是否成功删除
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            DELETE FROM relations WHERE hash = ?
        """, (hash_value,))
        self._conn.commit()

        deleted = cursor.rowcount > 0
        if deleted:
            logger.info(f"删除关系: {hash_value[:16]}...")

        return deleted

    def set_relation_vector_state(
        self,
        hash_value: str,
        state: str,
        error: Optional[str] = None,
        bump_retry: bool = False,
    ) -> bool:
        """
        更新关系向量状态。
        """
        state_norm = str(state or "").strip().lower()
        if state_norm not in {"none", "pending", "ready", "failed"}:
            raise ValueError(f"无效 vector_state: {state}")

        now = datetime.now().timestamp()
        err_text = (str(error).strip() if error is not None else None)
        if err_text:
            err_text = err_text[:500]
        clear_error = state_norm in {"none", "pending", "ready"}

        cursor = self._conn.cursor()
        if bump_retry:
            cursor.execute(
                """
                UPDATE relations
                SET vector_state = ?,
                    vector_updated_at = ?,
                    vector_error = ?,
                    vector_retry_count = COALESCE(vector_retry_count, 0) + 1
                WHERE hash = ?
                """,
                (state_norm, now, None if clear_error else err_text, hash_value),
            )
        else:
            cursor.execute(
                """
                UPDATE relations
                SET vector_state = ?,
                    vector_updated_at = ?,
                    vector_error = ?
                WHERE hash = ?
                """,
                (state_norm, now, None if clear_error else err_text, hash_value),
            )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_relations_by_vector_state(
        self,
        states: List[str],
        limit: int = 200,
        max_retry: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        根据向量状态列出关系，用于回填任务。
        """
        normalized_states = [
            str(s or "").strip().lower()
            for s in (states or [])
            if str(s or "").strip()
        ]
        normalized_states = [
            s for s in normalized_states
            if s in {"none", "pending", "ready", "failed"}
        ]
        if not normalized_states:
            return []

        placeholders = ",".join(["?"] * len(normalized_states))
        params: List[Any] = list(normalized_states)
        sql = f"""
            SELECT hash, subject, predicate, object, confidence, source_paragraph,
                   vector_state, vector_updated_at, vector_error, vector_retry_count, created_at
            FROM relations
            WHERE vector_state IN ({placeholders})
        """
        if max_retry is not None:
            sql += " AND COALESCE(vector_retry_count, 0) < ?"
            params.append(int(max_retry))
        sql += " ORDER BY COALESCE(vector_updated_at, created_at, 0) ASC LIMIT ?"
        params.append(max(1, int(limit)))

        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params))
        return [self._row_to_dict(row, "relation") for row in cursor.fetchall()]

    def count_relations_by_vector_state(self) -> Dict[str, int]:
        """
        统计关系向量状态分布。
        """
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT COALESCE(vector_state, 'none') AS state, COUNT(*) AS cnt
            FROM relations
            GROUP BY COALESCE(vector_state, 'none')
            """
        )
        result: Dict[str, int] = {"none": 0, "pending": 0, "ready": 0, "failed": 0}
        total = 0
        for row in cursor.fetchall():
            state = str(row["state"] or "none").lower()
            count = int(row["cnt"] or 0)
            if state not in result:
                result[state] = 0
            result[state] += count
            total += count
        result["total"] = total
        return result

    def update_vector_index(
        self,
        item_type: str,
        hash_value: str,
        vector_index: int,
    ) -> bool:
        """
        更新向量索引

        Args:
            item_type: 类型（paragraph/entity/relation）
            hash_value: 哈希值
            vector_index: 向量索引

        Returns:
            是否成功更新
        """
        valid_types = ["paragraph", "entity", "relation"]
        if item_type not in valid_types:
            raise ValueError(f"无效的类型: {item_type}")

        table_map = {
            "paragraph": "paragraphs",
            "entity": "entities",
            "relation": "relations",
        }

        cursor = self._conn.cursor()
        cursor.execute(f"""
            UPDATE {table_map[item_type]}
            SET vector_index = ?
            WHERE hash = ?
        """, (vector_index, hash_value))
        self._conn.commit()

        return cursor.rowcount > 0

    def set_permanence(self, hash_value: str, item_type: str, is_permanent: bool) -> bool:
        """设置永久记忆标记"""
        table_map = {
            "paragraph": "paragraphs",
            "relation": "relations",
        }
        if item_type not in table_map:
            raise ValueError(f"类型 {item_type} 不支持设置永久性")
            
        cursor = self._conn.cursor()
        cursor.execute(f"""
            UPDATE {table_map[item_type]}
            SET is_permanent = ?
            WHERE hash = ?
        """, (1 if is_permanent else 0, hash_value))
        self._conn.commit()
        
        if cursor.rowcount > 0:
            logger.debug(f"设置永久记忆: {item_type}/{hash_value[:8]} -> {is_permanent}")
            return True
        return False

    def record_access(self, hash_value: str, item_type: str) -> bool:
        """记录访问（更新时间和次数）"""
        table_map = {
            "paragraph": "paragraphs",
            "relation": "relations",
        }
        if item_type not in table_map:
            return False
            
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(f"""
            UPDATE {table_map[item_type]}
            SET last_accessed = ?, access_count = access_count + 1
            WHERE hash = ?
        """, (now, hash_value))
        self._conn.commit()
        return cursor.rowcount > 0

    def query(
        self,
        sql: str,
        params: Optional[Tuple] = None,
    ) -> List[Dict[str, Any]]:
        """
        执行自定义查询

        Args:
            sql: SQL语句
            params: 参数

        Returns:
            查询结果列表
        """
        cursor = self._conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)

        return [dict(row) for row in cursor.fetchall()]

    def get_external_memory_ref(self, external_id: str) -> Optional[Dict[str, Any]]:
        """按 external_id 查询外部记忆映射。"""
        token = str(external_id or "").strip()
        if not token:
            return None

        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT external_id, paragraph_hash, source_type, created_at, metadata_json
            FROM external_memory_refs
            WHERE external_id = ?
            LIMIT 1
            """,
            (token,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        payload = dict(row)
        raw_metadata = payload.get("metadata_json")
        if raw_metadata:
            try:
                payload["metadata"] = json.loads(raw_metadata)
            except Exception:
                payload["metadata"] = {}
        else:
            payload["metadata"] = {}
        return payload

    def upsert_external_memory_ref(
        self,
        *,
        external_id: str,
        paragraph_hash: str,
        source_type: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """注册 external_id 到段落哈希的幂等映射。"""
        external_token = str(external_id or "").strip()
        paragraph_token = str(paragraph_hash or "").strip()
        if not external_token:
            raise ValueError("external_id 不能为空")
        if not paragraph_token:
            raise ValueError("paragraph_hash 不能为空")

        now = datetime.now().timestamp()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO external_memory_refs (
                external_id, paragraph_hash, source_type, created_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(external_id) DO UPDATE SET
                paragraph_hash = excluded.paragraph_hash,
                source_type = excluded.source_type,
                metadata_json = excluded.metadata_json
            """,
            (
                external_token,
                paragraph_token,
                str(source_type or "").strip() or None,
                now,
                metadata_json,
            ),
        )
        self._conn.commit()
        return self.get_external_memory_ref(external_token) or {
            "external_id": external_token,
            "paragraph_hash": paragraph_token,
            "source_type": str(source_type or "").strip(),
            "created_at": now,
            "metadata": metadata or {},
        }

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _json_loads(value: Any, default: Any) -> Any:
        if value in {None, ""}:
            return default
        try:
            return json.loads(value)
        except Exception:
            return default

    def list_external_memory_refs_by_paragraphs(self, paragraph_hashes: List[str]) -> List[Dict[str, Any]]:
        hashes = [str(item or "").strip() for item in (paragraph_hashes or []) if str(item or "").strip()]
        if not hashes:
            return []
        placeholders = ",".join(["?"] * len(hashes))
        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            SELECT external_id, paragraph_hash, source_type, created_at, metadata_json
            FROM external_memory_refs
            WHERE paragraph_hash IN ({placeholders})
            ORDER BY created_at ASC, external_id ASC
            """,
            tuple(hashes),
        )
        items: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            payload = dict(row)
            payload["metadata"] = self._json_loads(payload.get("metadata_json"), {})
            items.append(payload)
        return items

    def delete_external_memory_refs_by_paragraphs(self, paragraph_hashes: List[str]) -> List[Dict[str, Any]]:
        items = self.list_external_memory_refs_by_paragraphs(paragraph_hashes)
        hashes = [str(item or "").strip() for item in (paragraph_hashes or []) if str(item or "").strip()]
        if not hashes:
            return items
        placeholders = ",".join(["?"] * len(hashes))
        cursor = self._conn.cursor()
        cursor.execute(
            f"DELETE FROM external_memory_refs WHERE paragraph_hash IN ({placeholders})",
            tuple(hashes),
        )
        self._conn.commit()
        return items

    def restore_external_memory_refs(self, refs: List[Dict[str, Any]]) -> int:
        count = 0
        for item in refs or []:
            external_id = str(item.get("external_id", "") or "").strip()
            paragraph_hash = str(item.get("paragraph_hash", "") or "").strip()
            if not external_id or not paragraph_hash:
                continue
            created_at = float(item.get("created_at") or datetime.now().timestamp())
            metadata_json = self._json_dumps(item.get("metadata") or {})
            cursor = self._conn.cursor()
            cursor.execute(
                """
                INSERT INTO external_memory_refs (
                    external_id, paragraph_hash, source_type, created_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(external_id) DO UPDATE SET
                    paragraph_hash = excluded.paragraph_hash,
                    source_type = excluded.source_type,
                    created_at = excluded.created_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    external_id,
                    paragraph_hash,
                    str(item.get("source_type", "") or "").strip() or None,
                    created_at,
                    metadata_json,
                ),
            )
            count += max(0, int(cursor.rowcount or 0))
        self._conn.commit()
        return count

    def record_v5_operation(
        self,
        *,
        action: str,
        target: str,
        resolved_hashes: List[str],
        reason: str = "",
        updated_by: str = "",
        result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        operation_id = f"v5_{uuid.uuid4().hex}"
        created_at = datetime.now().timestamp()
        payload = {
            "operation_id": operation_id,
            "action": str(action or "").strip(),
            "target": str(target or "").strip(),
            "reason": str(reason or "").strip(),
            "updated_by": str(updated_by or "").strip(),
            "created_at": created_at,
            "resolved_hashes": [str(item or "").strip() for item in (resolved_hashes or []) if str(item or "").strip()],
            "result": result or {},
        }
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO memory_v5_operations (
                operation_id, action, target, reason, updated_by, created_at, resolved_hashes_json, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                payload["action"],
                payload["target"] or None,
                payload["reason"] or None,
                payload["updated_by"] or None,
                created_at,
                self._json_dumps(payload["resolved_hashes"]),
                self._json_dumps(payload["result"]),
            ),
        )
        self._conn.commit()
        return payload

    def create_delete_operation(
        self,
        *,
        mode: str,
        selector: Any,
        items: List[Dict[str, Any]],
        reason: str = "",
        requested_by: str = "",
        status: str = "executed",
        summary: Optional[Dict[str, Any]] = None,
        operation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        op_id = str(operation_id or f"del_{uuid.uuid4().hex}").strip()
        created_at = datetime.now().timestamp()
        normalized_items: List[Dict[str, Any]] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("item_type", "") or "").strip()
            if not item_type:
                continue
            normalized_items.append(
                {
                    "item_type": item_type,
                    "item_hash": str(item.get("item_hash", "") or "").strip() or None,
                    "item_key": str(item.get("item_key", "") or item.get("item_hash", "") or "").strip() or None,
                    "payload": item.get("payload") if isinstance(item.get("payload"), dict) else {},
                }
            )

        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO delete_operations (
                operation_id, mode, selector, reason, requested_by, status, created_at, restored_at, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                op_id,
                str(mode or "").strip(),
                self._json_dumps(selector if selector is not None else {}),
                str(reason or "").strip() or None,
                str(requested_by or "").strip() or None,
                str(status or "executed").strip(),
                created_at,
                self._json_dumps(summary or {}),
            ),
        )
        if normalized_items:
            cursor.executemany(
                """
                INSERT INTO delete_operation_items (
                    operation_id, item_type, item_hash, item_key, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        op_id,
                        item["item_type"],
                        item["item_hash"],
                        item["item_key"],
                        self._json_dumps(item["payload"]),
                        created_at,
                    )
                    for item in normalized_items
                ],
            )
        self._conn.commit()
        return self.get_delete_operation(op_id) or {
            "operation_id": op_id,
            "mode": str(mode or "").strip(),
            "selector": selector,
            "reason": str(reason or "").strip(),
            "requested_by": str(requested_by or "").strip(),
            "status": str(status or "executed").strip(),
            "created_at": created_at,
            "summary": summary or {},
            "items": normalized_items,
        }

    def mark_delete_operation_restored(
        self,
        operation_id: str,
        *,
        summary: Optional[Dict[str, Any]] = None,
    ) -> bool:
        token = str(operation_id or "").strip()
        if not token:
            return False
        cursor = self._conn.cursor()
        cursor.execute(
            """
            UPDATE delete_operations
            SET status = ?, restored_at = ?, summary_json = ?
            WHERE operation_id = ?
            """,
            (
                "restored",
                datetime.now().timestamp(),
                self._json_dumps(summary or {}),
                token,
            ),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_delete_operations(self, *, limit: int = 50, mode: str = "") -> List[Dict[str, Any]]:
        cursor = self._conn.cursor()
        params: List[Any] = []
        where = ""
        mode_token = str(mode or "").strip().lower()
        if mode_token:
            where = "WHERE LOWER(mode) = ?"
            params.append(mode_token)
        params.append(max(1, int(limit or 50)))
        cursor.execute(
            f"""
            SELECT operation_id, mode, selector, reason, requested_by, status, created_at, restored_at, summary_json
            FROM delete_operations
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            tuple(params),
        )
        items: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            payload = dict(row)
            payload["selector"] = self._json_loads(payload.get("selector"), {})
            payload["summary"] = self._json_loads(payload.get("summary_json"), {})
            items.append(payload)
        return items

    def get_delete_operation(self, operation_id: str) -> Optional[Dict[str, Any]]:
        token = str(operation_id or "").strip()
        if not token:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT operation_id, mode, selector, reason, requested_by, status, created_at, restored_at, summary_json
            FROM delete_operations
            WHERE operation_id = ?
            LIMIT 1
            """,
            (token,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        payload = dict(row)
        payload["selector"] = self._json_loads(payload.get("selector"), {})
        payload["summary"] = self._json_loads(payload.get("summary_json"), {})

        cursor.execute(
            """
            SELECT item_type, item_hash, item_key, payload_json, created_at
            FROM delete_operation_items
            WHERE operation_id = ?
            ORDER BY id ASC
            """,
            (token,),
        )
        payload["items"] = [
            {
                "item_type": str(item["item_type"] or ""),
                "item_hash": str(item["item_hash"] or ""),
                "item_key": str(item["item_key"] or ""),
                "payload": self._json_loads(item["payload_json"], {}),
                "created_at": item["created_at"],
            }
            for item in cursor.fetchall()
        ]
        return payload

    def purge_deleted_relations(self, *, cutoff_time: float, limit: int = 1000) -> List[str]:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT hash
            FROM deleted_relations
            WHERE deleted_at IS NOT NULL AND deleted_at < ?
            ORDER BY deleted_at ASC
            LIMIT ?
            """,
            (float(cutoff_time), max(1, int(limit or 1000))),
        )
        hashes = [str(row[0] or "").strip() for row in cursor.fetchall() if str(row[0] or "").strip()]
        if not hashes:
            return []
        placeholders = ",".join(["?"] * len(hashes))
        cursor.execute(f"DELETE FROM deleted_relations WHERE hash IN ({placeholders})", tuple(hashes))
        self._conn.commit()
        return hashes

    def get_statistics(self) -> Dict[str, int]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        cursor = self._conn.cursor()

        stats = {}

        # 段落数量
        cursor.execute("SELECT COUNT(*) FROM paragraphs")
        stats["paragraph_count"] = cursor.fetchone()[0]

        # 实体数量
        cursor.execute("SELECT COUNT(*) FROM entities")
        stats["entity_count"] = cursor.fetchone()[0]

        # 关系数量
        cursor.execute("SELECT COUNT(*) FROM relations")
        stats["relation_count"] = cursor.fetchone()[0]

        # 总词数
        cursor.execute("SELECT SUM(word_count) FROM paragraphs")
        result = cursor.fetchone()[0]
        stats["total_words"] = result if result else 0

        return stats

    def count_paragraphs(self, include_deleted: bool = False, only_deleted: bool = False) -> int:
        """
        获取段落数量
        """
        cursor = self._conn.cursor()
        if only_deleted:
            cursor.execute("SELECT COUNT(*) FROM paragraphs WHERE is_deleted = 1")
            return cursor.fetchone()[0]
        if include_deleted:
            cursor.execute("SELECT COUNT(*) FROM paragraphs")
            return cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM paragraphs WHERE is_deleted = 0")
        return cursor.fetchone()[0]

    def count_relations(self, include_deleted: bool = False, only_deleted: bool = False) -> int:
        """
        获取关系数量
        """
        cursor = self._conn.cursor()
        if only_deleted:
            cursor.execute("SELECT COUNT(*) FROM deleted_relations")
            return cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM relations")
        active_count = cursor.fetchone()[0]
        if not include_deleted:
            return active_count
        cursor.execute("SELECT COUNT(*) FROM deleted_relations")
        deleted_count = cursor.fetchone()[0]
        return int(active_count) + int(deleted_count)

    def count_entities(self) -> int:
        """
        获取实体数量

        Returns:
            实体数量
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM entities")
        return cursor.fetchone()[0]

    def get_knowledge_type_distribution(self) -> Dict[str, int]:
        """获取段落知识类型分布。"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT knowledge_type, COUNT(*) as count
            FROM paragraphs
            WHERE is_deleted = 0
            GROUP BY knowledge_type
            """
        )
        result: Dict[str, int] = {}
        for row in cursor.fetchall():
            type_name = row[0] if row[0] else "未分类"
            result[str(type_name)] = int(row[1] or 0)
        return result

    def get_memory_status_summary(self, now_ts: Optional[float] = None) -> Dict[str, int]:
        """聚合 memory status 统计。"""
        now_ts = float(now_ts) if now_ts is not None else datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM relations WHERE is_inactive = 0")
        active_count = int(cursor.fetchone()[0] or 0)
        cursor.execute("SELECT COUNT(*) FROM relations WHERE is_inactive = 1")
        inactive_count = int(cursor.fetchone()[0] or 0)
        cursor.execute("SELECT COUNT(*) FROM deleted_relations")
        deleted_count = int(cursor.fetchone()[0] or 0)
        cursor.execute("SELECT COUNT(*) FROM relations WHERE is_pinned = 1")
        pinned_count = int(cursor.fetchone()[0] or 0)
        cursor.execute("SELECT COUNT(*) FROM relations WHERE protected_until > ?", (now_ts,))
        ttl_count = int(cursor.fetchone()[0] or 0)
        return {
            "active_count": active_count,
            "inactive_count": inactive_count,
            "deleted_count": deleted_count,
            "pinned_count": pinned_count,
            "temp_protected_count": ttl_count,
        }

    def get_relations_subject_object_map(self, hashes: List[str]) -> Dict[str, Tuple[str, str]]:
        """批量获取关系 hash 对应的 (subject, object)。"""
        if not hashes:
            return {}
        cursor = self._conn.cursor()
        placeholders = ",".join(["?"] * len(hashes))
        cursor.execute(
            f"SELECT hash, subject, object FROM relations WHERE hash IN ({placeholders})",
            hashes,
        )
        return {str(row[0]): (str(row[1]), str(row[2])) for row in cursor.fetchall()}

    def get_connection(self) -> sqlite3.Connection:
        """公开连接访问（用于离线脚本），替代外部访问私有字段。"""
        return self._resolve_conn()

    def get_relation_db_snapshot(self) -> Tuple[int, float, str]:
        """返回关系快照：(relation_count, max_created_at, max_hash)。"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT
                COUNT(*) AS relation_count,
                COALESCE(MAX(created_at), 0) AS max_created_at,
                COALESCE(MAX(hash), '') AS max_hash
            FROM relations
            """
        )
        row = cursor.fetchone()
        if not row:
            return (0, 0.0, "")
        return (
            int(row[0] or 0),
            float(row[1] or 0.0),
            str(row[2] or ""),
        )

    def is_entity_still_referenced(self, entity_hash: str, entity_name: str = "") -> bool:
        """
        判断实体是否仍被引用：
        1) 被 paragraph_entities 引用
        2) 在 relations.subject/object 中出现
        """
        token_hash = str(entity_hash or "").strip()
        if token_hash:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT 1 FROM paragraph_entities WHERE entity_hash = ? LIMIT 1",
                (token_hash,),
            )
            if cursor.fetchone() is not None:
                return True

        canon_name = self._canonicalize_name(entity_name)
        if canon_name:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT 1
                FROM relations
                WHERE LOWER(TRIM(subject)) = ? OR LOWER(TRIM(object)) = ?
                LIMIT 1
                """,
                (canon_name, canon_name),
            )
            if cursor.fetchone() is not None:
                return True
        return False

    def search_relations_by_subject_or_object(
        self,
        query: str,
        *,
        limit: int = 5,
        include_deleted: bool = False,
    ) -> List[Dict[str, Any]]:
        """按 subject/object 模糊查询关系。"""
        q = str(query or "").strip()
        if not q:
            return []
        max_limit = int(max(1, limit))
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM relations
            WHERE subject LIKE ? OR object LIKE ?
            LIMIT ?
            """,
            (f"%{q}%", f"%{q}%", max_limit),
        )
        rows = [self._row_to_dict(row, "relation") for row in cursor.fetchall()]
        if rows or not include_deleted:
            return rows

        cursor.execute(
            """
            SELECT *
            FROM deleted_relations
            WHERE subject LIKE ? OR object LIKE ?
            LIMIT ?
            """,
            (f"%{q}%", f"%{q}%", max_limit),
        )
        return [self._row_to_dict(row, "relation") for row in cursor.fetchall()]

    def list_hashes(self, table: str) -> List[str]:
        """安全枚举指定表的 hash 列。"""
        allowed = {"paragraphs", "entities", "relations", "deleted_relations"}
        token = str(table or "").strip().lower()
        if token not in allowed:
            raise ValueError(f"unsupported table for list_hashes: {table}")
        cursor = self._conn.cursor()
        cursor.execute(f"SELECT hash FROM {token}")
        return [str(row[0]) for row in cursor.fetchall()]

    def get_orphan_deleted_relation_hashes(self, limit: int = 200) -> List[str]:
        """获取 deleted_relations 中已不在 relations 的孤儿 hash。"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT d.hash
            FROM deleted_relations d
            LEFT JOIN relations r ON r.hash = d.hash
            WHERE r.hash IS NULL
            LIMIT ?
            """,
            (int(max(1, limit)),),
        )
        return [str(row[0]) for row in cursor.fetchall()]

    def resolve_relation_hash_alias(
        self,
        value: str,
        *,
        include_deleted: bool = False,
    ) -> List[str]:
        """
        解析关系哈希输入：
        - 64位：直接校验存在性
        - 32位：通过 relation_hash_aliases 唯一映射
        """
        token = str(value or "").strip().lower()
        if not token:
            return []
        if len(token) == 64 and all(ch in "0123456789abcdef" for ch in token):
            cursor = self._conn.cursor()
            cursor.execute("SELECT 1 FROM relations WHERE hash = ? LIMIT 1", (token,))
            if cursor.fetchone():
                return [token]
            if include_deleted:
                cursor.execute("SELECT 1 FROM deleted_relations WHERE hash = ? LIMIT 1", (token,))
                if cursor.fetchone():
                    return [token]
            return []

        if len(token) != 32 or not all(ch in "0123456789abcdef" for ch in token):
            return []

        cursor = self._conn.cursor()
        cursor.execute("SELECT hash FROM relation_hash_aliases WHERE alias32 = ?", (token,))
        row = cursor.fetchone()
        if not row:
            return []
        resolved = str(row[0])
        return [resolved]

    def rebuild_relation_hash_aliases(self) -> Dict[str, Any]:
        """重建 32 位 relation hash 别名映射。"""
        cursor = self._conn.cursor()
        # 历史库兜底：缺表时先创建，避免迁移过程直接中断。
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS relation_hash_aliases (
                alias32 TEXT PRIMARY KEY,
                hash TEXT NOT NULL
            )
        """)
        cursor.execute("DELETE FROM relation_hash_aliases")

        cursor.execute("SELECT hash FROM relations")
        hashes = [str(r[0]) for r in cursor.fetchall()]
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='deleted_relations'"
        )
        has_deleted_relations = cursor.fetchone() is not None
        if has_deleted_relations:
            cursor.execute("SELECT hash FROM deleted_relations")
            hashes.extend(str(r[0]) for r in cursor.fetchall())

        alias_map: Dict[str, str] = {}
        conflicts: Dict[str, set[str]] = {}
        for h in hashes:
            if len(h) != 64:
                continue
            alias = h[:32]
            old = alias_map.get(alias)
            if old is None:
                alias_map[alias] = h
            elif old != h:
                conflicts.setdefault(alias, set()).update({old, h})

        for alias, full_hash in alias_map.items():
            if alias in conflicts:
                continue
            cursor.execute(
                "INSERT INTO relation_hash_aliases(alias32, hash) VALUES (?, ?)",
                (alias, full_hash),
            )
        self._conn.commit()
        return {
            "inserted": len(alias_map) - len(conflicts),
            "conflict_count": len(conflicts),
            "conflicts": sorted(conflicts.keys()),
        }

    def search_relation_hashes_by_text(self, query: str, limit: int = 5) -> List[str]:
        """按 relation 内容模糊查询 hash。"""
        q = str(query or "").strip()
        if not q:
            return []
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT hash FROM relations WHERE subject LIKE ? OR object LIKE ? LIMIT ?",
            (f"%{q}%", f"%{q}%", int(max(1, limit))),
        )
        return [str(row[0]) for row in cursor.fetchall()]

    def search_deleted_relation_hashes_by_text(self, query: str, limit: int = 5) -> List[str]:
        """按 deleted_relations 内容模糊查询 hash。"""
        q = str(query or "").strip()
        if not q:
            return []
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT hash FROM deleted_relations WHERE subject LIKE ? OR object LIKE ? LIMIT ?",
            (f"%{q}%", f"%{q}%", int(max(1, limit))),
        )
        return [str(row[0]) for row in cursor.fetchall()]

    def restore_entity_by_hash(self, entity_hash: str) -> bool:
        """恢复软删除实体。"""
        cursor = self._conn.cursor()
        cursor.execute(
            "UPDATE entities SET is_deleted=0, deleted_at=NULL WHERE hash=?",
            (str(entity_hash),),
        )
        changed = cursor.rowcount > 0
        if changed:
            self._conn.commit()
        return changed

    def restore_paragraph_by_hash(self, paragraph_hash: str) -> bool:
        """恢复软删除段落。"""
        cursor = self._conn.cursor()
        cursor.execute(
            "UPDATE paragraphs SET is_deleted=0, deleted_at=NULL WHERE hash=?",
            (str(paragraph_hash),),
        )
        changed = cursor.rowcount > 0
        if changed:
            self._conn.commit()
        return changed

    def backfill_temporal_metadata_from_created_at(
        self,
        *,
        limit: int = 100000,
        dry_run: bool = False,
        no_created_fallback: bool = False,
    ) -> Dict[str, int]:
        """回填段落 event_time 字段（created_at 兜底）。"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT hash, created_at, source
            FROM paragraphs
            WHERE (event_time IS NULL AND event_time_start IS NULL AND event_time_end IS NULL)
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(max(1, limit)),),
        )
        rows = cursor.fetchall()
        candidates = len(rows)
        if dry_run:
            return {"candidates": candidates, "updated": 0}
        if no_created_fallback:
            return {"candidates": candidates, "updated": 0}

        updated = 0
        touched_sources: List[str] = []
        for row in rows:
            created_at = row["created_at"]
            if created_at is None:
                continue
            cursor.execute(
                """
                UPDATE paragraphs
                SET event_time = ?, time_granularity = ?, time_confidence = ?, updated_at = ?
                WHERE hash = ?
                """,
                (float(created_at), "day", 0.2, float(created_at), row["hash"]),
            )
            if cursor.rowcount > 0:
                updated += 1
                touched_sources.append(row["source"])
        self._conn.commit()
        if updated > 0:
            self._enqueue_episode_source_rebuilds(
                touched_sources,
                reason="paragraph_time_backfill",
            )
        return {"candidates": candidates, "updated": updated}

    def get_schema_version(self) -> int:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        if cursor.fetchone() is None:
            return 0
        cursor.execute("SELECT MAX(version) FROM schema_migrations")
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def set_schema_version(self, version: int = SCHEMA_VERSION) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        cursor.execute(
            "INSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (int(version), datetime.now().timestamp()),
        )
        self._conn.commit()

    def delete_paragraph_atomic(self, paragraph_hash: str) -> Dict[str, Any]:
        """
        两阶段删除段落：DB 事务内计算 + 提交后执行清理

        Args:
            paragraph_hash: 段落哈希

        Returns:
            cleanup_plan: 包含需要后续从 Vector/GraphStore 中移除的 ID 列表
        """
        cleanup_plan = {
            "paragraph_hash": paragraph_hash,
            "vector_id_to_remove": None,
            "edges_to_remove": [],  # (src, tgt) 元组列表 (fallback)
            "relation_prune_ops": [],  # (subject, object, relation_hash) 精准裁剪
            "episode_sources_to_rebuild": [],
        }

        cursor = self._conn.cursor()
        try:
            # === Phase 1: DB Transaction (可回滚) ===
            # 使用 IMMEDIATE 模式，一旦开启事务立即锁定 DB (防止其他写操作插队导致幻读)
            cursor.execute("BEGIN IMMEDIATE")

            # 1. [快照] 获取候选关系
            cursor.execute("SELECT relation_hash FROM paragraph_relations WHERE paragraph_hash = ?", (paragraph_hash,))
            candidate_relations = [row[0] for row in cursor.fetchall()]

            # 2. [快照] 确认该段落存在并记录 ID 用于向量删除
            cursor.execute("SELECT hash, source FROM paragraphs WHERE hash = ?", (paragraph_hash,))
            paragraph_row = cursor.fetchone()
            if paragraph_row:
                cleanup_plan["vector_id_to_remove"] = paragraph_hash
                cleanup_plan["episode_sources_to_rebuild"] = self._dedupe_episode_sources(
                    [paragraph_row["source"]]
                )

            # 3. [主删除] 删除段落 (触发 CASCADE 删 paragraph_relations)
            cursor.execute("DELETE FROM paragraphs WHERE hash = ?", (paragraph_hash,))

            # 4. [计算孤儿]
            orphaned_hashes = []
            for rel_hash in candidate_relations:
                count = cursor.execute(
                    "SELECT count(*) FROM paragraph_relations WHERE relation_hash = ?",
                    (rel_hash,)
                ).fetchone()[0]

                if count == 0:
                    # 是孤儿：记录边信息以便后续删 Graph
                    cursor.execute("SELECT subject, object FROM relations WHERE hash = ?", (rel_hash,))
                    rel_info = cursor.fetchone()
                    if rel_info:
                        s_val, o_val = rel_info[0], rel_info[1]
                        cleanup_plan["relation_prune_ops"].append((s_val, o_val, rel_hash))

                        # 仅当 (subject, object) 不再有任何关系时，才计划删整条边（兼容旧实现）。
                        sibling_count = cursor.execute(
                            """
                            SELECT count(*) FROM relations
                            WHERE LOWER(TRIM(subject)) = LOWER(TRIM(?))
                              AND LOWER(TRIM(object)) = LOWER(TRIM(?))
                              AND hash != ?
                            """,
                            (s_val, o_val, rel_hash)
                        ).fetchone()[0]
                        if sibling_count == 0:
                            cleanup_plan["edges_to_remove"].append((s_val, o_val))

                    orphaned_hashes.append(rel_hash)

            # 5. [DB清理] 删除孤儿关系记录
            if orphaned_hashes:
                placeholders = ','.join(['?'] * len(orphaned_hashes))
                cursor.execute(f"DELETE FROM relations WHERE hash IN ({placeholders})", orphaned_hashes)

            self._conn.commit()
            if cleanup_plan["episode_sources_to_rebuild"]:
                self._enqueue_episode_source_rebuilds(
                    cleanup_plan["episode_sources_to_rebuild"],
                    reason="paragraph_deleted",
                )
            if cleanup_plan["vector_id_to_remove"]:
                logger.debug(f"原子删除段落成功: {paragraph_hash}, 计划清理 {len(orphaned_hashes)} 个孤儿关系")
            return cleanup_plan

        except Exception as e:
            self._conn.rollback()
            logger.error(f"DB Transaction failed: {e}")
            raise e


    def clear_all(self) -> None:
        """清空所有表数据"""
        cursor = self._conn.cursor()
        tables = [
            "paragraphs", "entities", "relations",
            "paragraph_relations", "paragraph_entities",
            "episodes", "episode_paragraphs",
            "episode_rebuild_sources", "episode_pending_paragraphs",
        ]
        for table in tables:
            cursor.execute(f"DELETE FROM {table}")
        self._conn.commit()
        logger.info("元数据存储所有表已清空")



    def update_relation_timestamp(self, hash_value: str, access_count_delta: int = 1) -> None:
        """更新关系的访问时间和计数"""
        now = datetime.now().timestamp()
        
        # 同时更新 last_accessed (旧) 和 last_reinforced (V5)
        
        cursor = self._conn.cursor()
        cursor.execute("""
            UPDATE relations
            SET last_accessed = ?,
                access_count = access_count + ?
            WHERE hash = ?
        """, (now, access_count_delta, hash_value))
        self._conn.commit()

    # =========================================================================
    # V5 Memory System Methods
    # =========================================================================

    def get_relation_status_batch(self, hashes: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        批量获取关系状态 (V5)
        
        Args:
            hashes: 关系哈希列表
            
        Returns:
            Dict[hash, status_dict]
            status_dict 包含: is_inactive, weight(confidence), is_pinned, protected_until, last_reinforced, inactive_since
        """
        if not hashes:
            return {}
            
        placeholders = ",".join(["?"] * len(hashes))
        cursor = self._conn.cursor()
        cursor.execute(f"""
            SELECT hash, is_inactive, confidence, is_pinned, protected_until, last_reinforced, inactive_since
            FROM relations
            WHERE hash IN ({placeholders})
        """, hashes)
        
        result = {}
        for row in cursor.fetchall():
            result[row["hash"]] = {
                "is_inactive": bool(row["is_inactive"]),
                "weight": row["confidence"],
                "is_pinned": bool(row["is_pinned"]),
                "protected_until": row["protected_until"],
                "last_reinforced": row["last_reinforced"],
                "inactive_since": row["inactive_since"]
            }
        return result

    def mark_relations_active(self, hashes: List[str], boost_weight: Optional[float] = None) -> None:
        """
        批量标记关系为活跃 (Active/Revive)
        
        Args:
            hashes: 关系哈希列表
            boost_weight: 如果提供，将设置 confidence = max(confidence, boost_weight)
        """
        if not hashes:
            return
            
        placeholders = ",".join(["?"] * len(hashes))
        cursor = self._conn.cursor()
        
        if boost_weight is not None:
            cursor.execute(f"""
                UPDATE relations
                SET is_inactive = 0,
                    inactive_since = NULL,
                    confidence = MAX(confidence, ?)
                WHERE hash IN ({placeholders})
            """, (boost_weight, *hashes))
        else:
             cursor.execute(f"""
                UPDATE relations
                SET is_inactive = 0,
                    inactive_since = NULL
                WHERE hash IN ({placeholders})
            """, hashes)
            
        self._conn.commit()

    def update_relations_protection(
        self, 
        hashes: List[str], 
        protected_until: Optional[float] = None, 
        is_pinned: Optional[bool] = None,
        last_reinforced: Optional[float] = None
    ) -> None:
        """
        批量更新关系保护状态
        """
        if not hashes:
            return
            
        updates = []
        params = []
        
        if protected_until is not None:
            updates.append("protected_until = ?")
            params.append(protected_until)
        if is_pinned is not None:
            updates.append("is_pinned = ?")
            params.append(1 if is_pinned else 0)
        if last_reinforced is not None:
            updates.append("last_reinforced = ?")
            params.append(last_reinforced)
            
        if not updates:
            return

        sql_set = ", ".join(updates)
        placeholders = ",".join(["?"] * len(hashes))
        
        params.extend(hashes)
        
        cursor = self._conn.cursor()
        cursor.execute(f"""
            UPDATE relations
            SET {sql_set}
            WHERE hash IN ({placeholders})
        """, params)
        self._conn.commit()

    def get_prune_candidates(self, cutoff_time: float, limit: int = 1000) -> List[str]:
        """
        获取待修剪候选 (已过冷冻保留期)
        
        Args:
            cutoff_time: 截止时间 (now - 冷冻时长)
            limit: 限制数量
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT hash FROM relations
            WHERE is_inactive = 1 
            AND inactive_since < ?
            LIMIT ?
        """, (cutoff_time, limit))
        return [row[0] for row in cursor.fetchall()]

    def backup_and_delete_relations(self, hashes: List[str]) -> int:
        """
        备份并删除关系 (Prune)
        
        Returns:
            删除的数量
        """
        if not hashes:
            return 0
            
        placeholders = ",".join(["?"] * len(hashes))
        now = datetime.now().timestamp()
        
        cursor = self._conn.cursor()
        try:
            # 1. 备份
            cursor.execute(f"""
                INSERT OR REPLACE INTO deleted_relations 
                (hash, subject, predicate, object, vector_index, confidence, created_at, 
                 vector_state, vector_updated_at, vector_error, vector_retry_count,
                 source_paragraph, metadata, is_permanent, last_accessed, access_count,
                 is_inactive, inactive_since, is_pinned, protected_until, last_reinforced, deleted_at)
                SELECT 
                 hash, subject, predicate, object, vector_index, confidence, created_at, 
                 vector_state, vector_updated_at, vector_error, vector_retry_count,
                 source_paragraph, metadata, is_permanent, last_accessed, access_count,
                 is_inactive, inactive_since, is_pinned, protected_until, last_reinforced, ?
                FROM relations
                WHERE hash IN ({placeholders})
            """, (now, *hashes))
            
            # 2. 删除 (级联删除会自动处理 paragraph_relations 关联)
            cursor.execute(f"""
                DELETE FROM relations
                WHERE hash IN ({placeholders})
            """, hashes)
            
            deleted_count = cursor.rowcount
            self._conn.commit()
            return deleted_count
            
        except Exception as e:
            logger.error(f"备份删除失败: {e}")
            self._conn.rollback()
            return 0

    def restore_relation_metadata(self, hash_value: str) -> Optional[Dict[str, Any]]:
        """
        从回收站恢复关系元数据
        
        Returns:
            恢复后的关系数据 (字典)，失败返回 None
        """
        cursor = self._conn.cursor()
        try:
            # 1. 查询备份数据
            cursor.execute("SELECT * FROM deleted_relations WHERE hash = ?", (hash_value,))
            row = cursor.fetchone()
            if not row:
                return None
                
            data = dict(row)
            # 移除 deleted_at 字段
            if "deleted_at" in data:
                del data["deleted_at"]
                
            # 2. 插入回 relations 表
            # 动态构建 SQL 以适应字段变化
            columns = list(data.keys())
            placeholders = ",".join(["?"] * len(columns))
            cols_str = ",".join(columns)
            values = list(data.values())
            
            cursor.execute(f"""
                INSERT OR REPLACE INTO relations ({cols_str})
                VALUES ({placeholders})
            """, values)
            
            # 3. 从备份表删除
            cursor.execute("DELETE FROM deleted_relations WHERE hash = ?", (hash_value,))
            
            self._conn.commit()
            return self._row_to_dict(row, "relation") # 使用助手函数将原始行转换为字典
            
        except Exception as e:
            logger.error(f"恢复关系失败: {hash_value} - {e}")
            self._conn.rollback()
            return None

    def restore_relation(self, hash_value: str) -> Optional[Dict[str, Any]]:
        """兼容旧调用名：恢复关系。"""
        return self.restore_relation_metadata(hash_value)
            
    def get_protected_relations_hashes(self) -> List[str]:
        """获取所有受保护关系的哈希 (Pinned 或 Protected Until > Now)"""
        now = datetime.now().timestamp()
        
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT hash FROM relations
            WHERE is_pinned = 1 OR protected_until > ?
        """, (now,))
        
        return [row[0] for row in cursor.fetchall()]


    
    def get_deleted_relations(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取回收站中的关系记录"""
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM deleted_relations ORDER BY deleted_at DESC LIMIT ?", (limit,))
        data = []
        for row in cursor.fetchall():
             d = dict(row)
             # 是否需要解码元数据？是的，与普通行相同
             if "metadata" in d and d["metadata"]:
                 try:
                     d["metadata"] = pickle.loads(d["metadata"])
                 except Exception:
                     d["metadata"] = {}
             data.append(d)
        return data

    def get_deleted_relation(self, hash_value: str) -> Optional[Dict[str, Any]]:
        """获取单条回收站记录"""
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM deleted_relations WHERE hash = ?", (hash_value,))
        row = cursor.fetchone()
        if not row: return None
        
        d = dict(row)
        if "metadata" in d and d["metadata"]:
             try:
                 d["metadata"] = pickle.loads(d["metadata"])
             except Exception:
                 d["metadata"] = {}
        return d

    def reinforce_relations(self, hashes: List[str]) -> None:
        """强化关系 (更新 last_reinforced, is_inactive=0)"""
        if not hashes: return
        now = datetime.now().timestamp()
        
        cursor = self._conn.cursor()
        # Batch update? chunking
        chunk_size = 500
        for i in range(0, len(hashes), chunk_size):
            chunk = hashes[i:i+chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            sql = f"""
                UPDATE relations 
                SET last_reinforced = ?, is_inactive = 0, inactive_since = NULL
                WHERE hash IN ({placeholders})
            """
            cursor.execute(sql, [now] + chunk)
            
        self._conn.commit()

    def mark_relations_inactive(self, hashes: List[str], inactive_since: Optional[float] = None) -> None:
        """标记关系为非活跃 (Freeze)。兼容显式 inactive_since 或默认当前时间。"""
        if not hashes:
            return
        mark_time = inactive_since if inactive_since is not None else datetime.now().timestamp()
        
        cursor = self._conn.cursor()
        chunk_size = 500
        for i in range(0, len(hashes), chunk_size):
            chunk = hashes[i:i+chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            sql = f"""
                UPDATE relations 
                SET is_inactive = 1, inactive_since = ?
                WHERE hash IN ({placeholders})
            """
            cursor.execute(sql, [mark_time] + chunk)
            
        self._conn.commit()

    def protect_relations(
        self, 
        hashes: List[str], 
        is_pinned: bool = False, 
        ttl_seconds: float = 0
    ) -> None:
        """
        设置保护状态
        """
        if not hashes: return
        now = datetime.now().timestamp()
        protected_until = (now + ttl_seconds) if ttl_seconds > 0 else 0
        
        cursor = self._conn.cursor()
        chunk_size = 500
        for i in range(0, len(hashes), chunk_size):
            chunk = hashes[i:i+chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            
            # 由于 is_pinned 和 protected_until 是分开的，如果请求固定（pin），我们会同时更新这两项，
            # 但通常用户要么切换固定状态，要么设置 TTL。
            # 如果 is_pinned=True，TTL 通常就不重要了。
            # 但目前的逻辑是正交处理它们的。
            
            # 如果用户取消固定 (is_pinned=False)，我们是否应该尊重已设置的 TTL？
            # 当前的 API 会同时设置这两项。
            
            sql = f"""
                UPDATE relations 
                SET is_pinned = ?, protected_until = ?
                WHERE hash IN ({placeholders})
            """
            cursor.execute(sql, [is_pinned, protected_until] + chunk)
            
        self._conn.commit()

    def vacuum(self) -> None:
        """优化数据库"""
        cursor = self._conn.cursor()
        cursor.execute("VACUUM")
        self._conn.commit()
        logger.info("数据库优化完成")

    def _row_to_dict(self, row: sqlite3.Row, row_type: str) -> Dict[str, Any]:
        """
        将数据库行转换为字典

        Args:
            row: 数据库行
            row_type: 行类型

        Returns:
            字典
        """
        d = dict(row)

        # 解码pickle字段
        if "metadata" in d and d["metadata"]:
            try:
                d["metadata"] = pickle.loads(d["metadata"])
            except Exception:
                d["metadata"] = {}

        return d

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._conn is not None

    def __enter__(self):
        """上下文管理器入口"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.close()

    # =========================================================================
    # V5 Soft Delete & Garbage Collection
    # =========================================================================

    def get_entity_gc_candidates(self, isolated_hashes: List[str], retention_seconds: float) -> List[str]:
        """
        获取实体 GC 候选列表 (Soft Delete Candidates)
        条件:
        1. 在 isolated_hashes 列表中 (由 GraphStore 提供；通常是实体名称)
        2. is_deleted = 0 (未被标记)
        3. created_at < now - retention (过了新手保护期)
        4. 不被任何 active paragraph 引用 (paragraph_entities check)
        
        Args:
            isolated_hashes: 孤儿实体名称列表（兼容传入 hash）
            retention_seconds: 保留时间 (秒)
        """
        if not isolated_hashes:
            return []

        # GraphStore.get_isolated_nodes 返回节点名，这里做 canonicalize -> entity hash 映射。
        # 同时兼容历史调用直接传 hash。
        normalized_hashes: List[str] = []
        for item in isolated_hashes:
            if not item:
                continue
            v = str(item).strip()
            if len(v) == 64 and all(c in "0123456789abcdefABCDEF" for c in v):
                normalized_hashes.append(v.lower())
            else:
                canon = self._canonicalize_name(v)
                if canon:
                    normalized_hashes.append(compute_hash(canon))

        normalized_hashes = list(dict.fromkeys(normalized_hashes))
        if not normalized_hashes:
            return []
            
        now = datetime.now().timestamp()
        cutoff = now - retention_seconds
        
        candidates = []
        batch_size = 900
        
        # 分批处理 IN 查询
        for i in range(0, len(normalized_hashes), batch_size):
            batch = normalized_hashes[i:i+batch_size]
            placeholders = ",".join(["?"] * len(batch))
            
            # 使用 NOT EXISTS 子查询检查引用
            # 注意: paragraph_entities 中引用的 paragraph 如果被软删了，是否算引用？
            # 这里的语义: 只要有 rows 存在于 paragraph_entities 且该 row 对应的 paragraph 没被彻底物理删除，就算引用。
            # 更严格: ... OR (EXISTS ... AND entity_hash=... AND is_deleted=0)
            # 但 paragraph_entities 表没有 is_deleted 字段(它是关联表). 我们检查关联是否存在。
            # 如果 paragraph 本身 soft deleted, 它的引用应该失效吗？
            # 策略: 只有当 paragraph 也是 active 时，引用才有效。
            # JOIN paragraphs p ON pe.paragraph_hash = p.hash WHERE p.is_deleted = 0
            
            query = f"""
                SELECT e.hash FROM entities e
                WHERE e.hash IN ({placeholders})
                AND e.is_deleted = 0
                AND (e.created_at IS NULL OR e.created_at < ?)
                AND NOT EXISTS (
                    SELECT 1 FROM paragraph_entities pe
                    JOIN paragraphs p ON pe.paragraph_hash = p.hash
                    WHERE pe.entity_hash = e.hash
                    AND p.is_deleted = 0
                )
            """
            
            cursor = self._conn.cursor()
            cursor.execute(query, [*batch, cutoff])
            candidates.extend([row[0] for row in cursor.fetchall()])
            
        return candidates

    def get_paragraph_gc_candidates(self, retention_seconds: float) -> List[str]:
        """
        获取段落 GC 候选列表
        条件:
        1. is_deleted = 0
        2. created_at < cutoff
        3. 没有 Relations (paragraph_relations empty)
        4. 没有 Entities 引用 (paragraph_entities empty) 
           OR 引用的 Entities 全是软删状态? (太复杂，简单点: 无引用)
           
        Refined Strategy: 
        段落孤儿判定 = 
          (Left Join paragraph_relations -> NULL) AND 
          (Left Join paragraph_entities -> NULL)
        """
        now = datetime.now().timestamp()
        cutoff = now - retention_seconds
        
        query = """
            SELECT p.hash FROM paragraphs p
            LEFT JOIN paragraph_relations pr ON p.hash = pr.paragraph_hash
            LEFT JOIN paragraph_entities pe ON p.hash = pe.paragraph_hash
            WHERE p.is_deleted = 0
            AND (p.created_at IS NULL OR p.created_at < ?)
            AND pr.relation_hash IS NULL
            AND pe.entity_hash IS NULL
        """
        
        cursor = self._conn.cursor()
        cursor.execute(query, (cutoff,))
        return [row[0] for row in cursor.fetchall()]

    def mark_as_deleted(self, hashes: List[str], type_: str) -> int:
        """
        标记为软删除 (Mark Phase)
        
        Args:
            hashes: Hash 列表
            type_: 'entity' | 'paragraph'
        """
        if not hashes:
            return 0
            
        table = "entities" if type_ == "entity" else "paragraphs"
        now = datetime.now().timestamp()
        touched_sources: List[str] = []
        if type_ == "paragraph":
            touched_sources = self._get_sources_for_paragraph_hashes(hashes, include_deleted=True)
        
        count = 0
        batch_size = 900
        for i in range(0, len(hashes), batch_size):
            batch = hashes[i:i+batch_size]
            placeholders = ",".join(["?"] * len(batch))
            
            # 幂等更新: 只更那些 is_deleted=0 的
            cursor = self._conn.cursor()
            cursor.execute(f"""
                UPDATE {table}
                SET is_deleted = 1, deleted_at = ?
                WHERE is_deleted = 0 AND hash IN ({placeholders})
            """, [now] + batch)
            count += cursor.rowcount
            
        self._conn.commit()
        if type_ == "paragraph" and count > 0:
            self._enqueue_episode_source_rebuilds(
                touched_sources,
                reason="paragraph_soft_deleted",
            )
        if count > 0:
            logger.info(f"软删除标记 ({table}): {count} 项")
        return count

    def sweep_deleted_items(self, type_: str, grace_period_seconds: float) -> List[Tuple[str, str]]:
        """
        扫描可物理清理的项目 (Sweep Phase - Selection)
        
        Args:
            type_: 'entity' | 'paragraph'
            grace_period_seconds: 宽限期
            
        Returns:
            List[(hash, name)]: 待删除项列表 (paragraph name为空)
        """
        table = "entities" if type_ == "entity" else "paragraphs"
        now = datetime.now().timestamp()
        cutoff = now - grace_period_seconds
        
        cols = "hash, name" if type_ == "entity" else "hash, '' as name"
        
        cursor = self._conn.cursor()
        cursor.execute(f"""
            SELECT {cols} FROM {table}
            WHERE is_deleted = 1
            AND deleted_at < ?
        """, (cutoff,))
        
        return [(row[0], row[1]) for row in cursor.fetchall()]

    def physically_delete_entities(self, hashes: List[str]) -> int:
        """物理删除实体 (批量)"""
        if not hashes: return 0
        
        count = 0
        batch_size = 900
        for i in range(0, len(hashes), batch_size):
            batch = hashes[i:i+batch_size]
            placeholders = ",".join(["?"] * len(batch))
            
            cursor = self._conn.cursor()
            cursor.execute(f"DELETE FROM entities WHERE hash IN ({placeholders})", batch)
            count += cursor.rowcount
            
        self._conn.commit()
        return count

    def physically_delete_paragraphs(self, hashes: List[str]) -> int:
        """物理删除段落 (批量)"""
        if not hashes: return 0
        touched_sources = self._get_sources_for_paragraph_hashes(hashes, include_deleted=True)
        
        count = 0
        batch_size = 900
        for i in range(0, len(hashes), batch_size):
            batch = hashes[i:i+batch_size]
            placeholders = ",".join(["?"] * len(batch))
            
            cursor = self._conn.cursor()
            cursor.execute(f"DELETE FROM paragraphs WHERE hash IN ({placeholders})", batch)
            count += cursor.rowcount
            
        self._conn.commit()
        if count > 0:
            self._enqueue_episode_source_rebuilds(
                touched_sources,
                reason="paragraph_physically_deleted",
            )
        return count

    def revive_if_deleted(self, entity_hashes: List[str] = None, paragraph_hashes: List[str] = None) -> int:
        """
        复活已软删的项目 (Auto Revival)
        当数据被再次访问、引用或导入时调用。
        """
        count = 0
        
        if entity_hashes:
            batch_size = 900
            for i in range(0, len(entity_hashes), batch_size):
                batch = entity_hashes[i:i+batch_size]
                placeholders = ",".join(["?"] * len(batch))
                
                cursor = self._conn.cursor()
                cursor.execute(f"""
                    UPDATE entities
                    SET is_deleted = 0, deleted_at = NULL
                    WHERE is_deleted = 1 AND hash IN ({placeholders})
                """, batch)
                count += cursor.rowcount
                
        if paragraph_hashes:
            touched_sources = self._get_sources_for_paragraph_hashes(paragraph_hashes, include_deleted=True)
            batch_size = 900
            for i in range(0, len(paragraph_hashes), batch_size):
                batch = paragraph_hashes[i:i+batch_size]
                placeholders = ",".join(["?"] * len(batch))
                
                cursor = self._conn.cursor()
                cursor.execute(f"""
                    UPDATE paragraphs
                    SET is_deleted = 0, deleted_at = NULL
                    WHERE is_deleted = 1 AND hash IN ({placeholders})
                """, batch)
                count += cursor.rowcount
        else:
            touched_sources = []
        
        if count > 0:
            self._conn.commit()
            if touched_sources:
                self._enqueue_episode_source_rebuilds(
                    touched_sources,
                    reason="paragraph_revived",
                )
            logger.info(f"自动复活: {count} 项 (Soft Delete Revived)")
            
        return count

    def revive_entities_by_names(self, names: List[str]) -> int:
        """
        根据名称复活实体 (Convenience wrapper)
        """
        if not names: return 0
        
        # 使用内部方法计算哈希
        hashes = [compute_hash(self._canonicalize_name(n)) for n in names]
        return self.revive_if_deleted(entity_hashes=hashes)

    def get_entity_status_batch(self, hashes: List[str]) -> Dict[str, Dict[str, Any]]:
        """批量获取实体状态 (WebUI用)"""
        if not hashes: return {}
        
        result = {}
        batch_size = 900
        for i in range(0, len(hashes), batch_size):
            batch = hashes[i:i+batch_size]
            placeholders = ",".join(["?"] * len(batch))
            
            cursor = self._conn.cursor()
            cursor.execute(f"""
                SELECT hash, is_deleted, deleted_at 
                FROM entities 
                WHERE hash IN ({placeholders})
            """, batch)
            
            for row in cursor.fetchall():
                result[row[0]] = {
                    "is_deleted": bool(row[1]),
                    "deleted_at": row[2]
                }
        return result

    # =========================================================================
    # Person Profile (问题3) - Switches / Active Set / Snapshots
    # =========================================================================

    def set_person_profile_switch(
        self,
        stream_id: str,
        user_id: str,
        enabled: bool,
        updated_at: Optional[float] = None,
    ) -> None:
        """设置人物画像自动注入开关（按 stream_id + user_id）。"""
        if not stream_id or not user_id:
            raise ValueError("stream_id 和 user_id 不能为空")

        ts = float(updated_at) if updated_at is not None else datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO person_profile_switches (stream_id, user_id, enabled, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stream_id, user_id) DO UPDATE SET
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (str(stream_id), str(user_id), 1 if enabled else 0, ts),
        )
        self._conn.commit()

    def get_person_profile_switch(self, stream_id: str, user_id: str, default: bool = False) -> bool:
        """读取人物画像自动注入开关。"""
        if not stream_id or not user_id:
            return bool(default)

        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT enabled FROM person_profile_switches WHERE stream_id = ? AND user_id = ?",
            (str(stream_id), str(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            return bool(default)
        return bool(row[0])

    def get_enabled_person_profile_switches(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """获取已开启人物画像注入开关的会话范围。"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT stream_id, user_id, enabled, updated_at
            FROM person_profile_switches
            WHERE enabled = 1
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (int(max(1, limit)),),
        )
        return [
            {
                "stream_id": row[0],
                "user_id": row[1],
                "enabled": bool(row[2]),
                "updated_at": row[3],
            }
            for row in cursor.fetchall()
        ]

    def mark_person_profile_active(
        self,
        stream_id: str,
        user_id: str,
        person_id: str,
        seen_at: Optional[float] = None,
    ) -> None:
        """记录活跃人物（用于定时按需刷新）。"""
        if not stream_id or not user_id or not person_id:
            return
        ts = float(seen_at) if seen_at is not None else datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO person_profile_active_persons (stream_id, user_id, person_id, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stream_id, user_id, person_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at
            """,
            (str(stream_id), str(user_id), str(person_id), ts),
        )
        self._conn.commit()

    def get_active_person_ids_for_enabled_switches(
        self,
        active_after: Optional[float] = None,
        limit: int = 200,
    ) -> List[str]:
        """获取“已开启开关范围内”的活跃人物集合。"""
        cursor = self._conn.cursor()
        sql = """
            SELECT a.person_id, MAX(a.last_seen_at) AS last_seen
            FROM person_profile_active_persons a
            JOIN person_profile_switches s
              ON a.stream_id = s.stream_id AND a.user_id = s.user_id
            WHERE s.enabled = 1
        """
        params: List[Any] = []
        if active_after is not None:
            sql += " AND a.last_seen_at >= ?"
            params.append(float(active_after))
        sql += """
            GROUP BY a.person_id
            ORDER BY last_seen DESC
            LIMIT ?
        """
        params.append(int(max(1, limit)))
        cursor.execute(sql, tuple(params))
        return [str(row[0]) for row in cursor.fetchall() if row and row[0]]

    def get_latest_person_profile_snapshot(self, person_id: str) -> Optional[Dict[str, Any]]:
        """获取人物最新画像快照。"""
        if not person_id:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT
                snapshot_id, person_id, profile_version, profile_text,
                aliases_json, relation_edges_json, vector_evidence_json, evidence_ids_json,
                updated_at, expires_at, source_note
            FROM person_profile_snapshots
            WHERE person_id = ?
            ORDER BY profile_version DESC
            LIMIT 1
            """,
            (str(person_id),),
        )
        row = cursor.fetchone()
        if not row:
            return None

        def _load_list(raw: Any) -> List[Any]:
            if not raw:
                return []
            try:
                data = json.loads(raw)
                return data if isinstance(data, list) else []
            except Exception:
                return []

        return {
            "snapshot_id": row[0],
            "person_id": row[1],
            "profile_version": int(row[2]),
            "profile_text": row[3] or "",
            "aliases": _load_list(row[4]),
            "relation_edges": _load_list(row[5]),
            "vector_evidence": _load_list(row[6]),
            "evidence_ids": _load_list(row[7]),
            "updated_at": row[8],
            "expires_at": row[9],
            "source_note": row[10] or "",
        }

    def upsert_person_profile_snapshot(
        self,
        person_id: str,
        profile_text: str,
        aliases: Optional[List[str]] = None,
        relation_edges: Optional[List[Dict[str, Any]]] = None,
        vector_evidence: Optional[List[Dict[str, Any]]] = None,
        evidence_ids: Optional[List[str]] = None,
        expires_at: Optional[float] = None,
        source_note: str = "",
        updated_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """写入人物画像快照（按 person_id 自动递增版本）。"""
        if not person_id:
            raise ValueError("person_id 不能为空")

        aliases = aliases or []
        relation_edges = relation_edges or []
        vector_evidence = vector_evidence or []
        evidence_ids = evidence_ids or []
        ts = float(updated_at) if updated_at is not None else datetime.now().timestamp()

        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT profile_version
            FROM person_profile_snapshots
            WHERE person_id = ?
            ORDER BY profile_version DESC
            LIMIT 1
            """,
            (str(person_id),),
        )
        row = cursor.fetchone()
        next_version = int(row[0]) + 1 if row else 1

        cursor.execute(
            """
            INSERT INTO person_profile_snapshots (
                person_id, profile_version, profile_text,
                aliases_json, relation_edges_json, vector_evidence_json, evidence_ids_json,
                updated_at, expires_at, source_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(person_id),
                next_version,
                str(profile_text or ""),
                json.dumps(aliases, ensure_ascii=False),
                json.dumps(relation_edges, ensure_ascii=False),
                json.dumps(vector_evidence, ensure_ascii=False),
                json.dumps(evidence_ids, ensure_ascii=False),
                ts,
                float(expires_at) if expires_at is not None else None,
                str(source_note or ""),
            ),
        )
        self._conn.commit()
        latest = self.get_latest_person_profile_snapshot(person_id)
        return latest or {
            "person_id": person_id,
            "profile_version": next_version,
            "profile_text": str(profile_text or ""),
            "aliases": aliases,
            "relation_edges": relation_edges,
            "vector_evidence": vector_evidence,
            "evidence_ids": evidence_ids,
            "updated_at": ts,
            "expires_at": expires_at,
            "source_note": source_note,
        }

    def get_person_profile_override(self, person_id: str) -> Optional[Dict[str, Any]]:
        """获取人物画像手工覆盖内容。"""
        if not person_id:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT person_id, override_text, updated_at, updated_by, source
            FROM person_profile_overrides
            WHERE person_id = ?
            LIMIT 1
            """,
            (str(person_id),),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "person_id": str(row[0]),
            "override_text": str(row[1] or ""),
            "updated_at": row[2],
            "updated_by": str(row[3] or ""),
            "source": str(row[4] or ""),
        }

    def set_person_profile_override(
        self,
        person_id: str,
        override_text: str,
        updated_by: str = "",
        source: str = "webui",
        updated_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """写入人物画像手工覆盖；空文本等价于清除覆盖。"""
        if not person_id:
            raise ValueError("person_id 不能为空")

        text = str(override_text or "").strip()
        if not text:
            self.delete_person_profile_override(person_id)
            return {
                "person_id": str(person_id),
                "override_text": "",
                "updated_at": None,
                "updated_by": str(updated_by or ""),
                "source": str(source or ""),
            }

        ts = float(updated_at) if updated_at is not None else datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO person_profile_overrides (
                person_id, override_text, updated_at, updated_by, source
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(person_id) DO UPDATE SET
                override_text = excluded.override_text,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by,
                source = excluded.source
            """,
            (
                str(person_id),
                text,
                ts,
                str(updated_by or ""),
                str(source or ""),
            ),
        )
        self._conn.commit()
        return self.get_person_profile_override(person_id) or {
            "person_id": str(person_id),
            "override_text": text,
            "updated_at": ts,
            "updated_by": str(updated_by or ""),
            "source": str(source or ""),
        }

    def delete_person_profile_override(self, person_id: str) -> bool:
        """删除人物画像手工覆盖。"""
        if not person_id:
            return False
        cursor = self._conn.cursor()
        cursor.execute(
            "DELETE FROM person_profile_overrides WHERE person_id = ?",
            (str(person_id),),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    # =========================================================================
    # Episode MVP
    # =========================================================================

    def enqueue_episode_source_rebuild(self, source: str, reason: str = "") -> bool:
        """将 source 入队到 episode 重建队列。"""
        return bool(self._enqueue_episode_source_rebuilds([source], reason=reason))

    def fetch_episode_source_rebuild_batch(
        self,
        limit: int = 20,
        max_retry: int = 3,
    ) -> List[Dict[str, Any]]:
        """获取待处理的 source 重建任务。"""
        safe_limit = max(1, int(limit))
        safe_retry = max(0, int(max_retry))
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT source, status, retry_count, last_error, reason, requested_at, updated_at
            FROM episode_rebuild_sources
            WHERE status = 'pending'
               OR (status = 'failed' AND retry_count < ?)
            ORDER BY requested_at ASC, updated_at ASC
            LIMIT ?
            """,
            (safe_retry, safe_limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_episode_source_running(
        self,
        source: str,
        *,
        requested_at: Optional[float] = None,
    ) -> bool:
        """将 source 标记为 running。"""
        token = self._normalize_episode_source(source)
        if not token:
            return False

        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        params: List[Any] = [now, token]
        sql = """
            UPDATE episode_rebuild_sources
            SET status = 'running',
                updated_at = ?
            WHERE source = ?
              AND status IN ('pending', 'failed')
        """
        if requested_at is not None:
            sql += " AND requested_at = ?"
            params.append(float(requested_at))
        cursor.execute(sql, tuple(params))
        self._conn.commit()
        return cursor.rowcount > 0

    def mark_episode_source_done(
        self,
        source: str,
        *,
        requested_at: Optional[float] = None,
    ) -> bool:
        """将 source 标记为 done；若运行期间发生新写入，则保持 pending。"""
        token = self._normalize_episode_source(source)
        if not token:
            return False

        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        if requested_at is None:
            cursor.execute(
                """
                UPDATE episode_rebuild_sources
                SET status = 'done',
                    last_error = NULL,
                    updated_at = ?
                WHERE source = ?
                """,
                (now, token),
            )
        else:
            req_ts = float(requested_at)
            cursor.execute(
                """
                UPDATE episode_rebuild_sources
                SET status = CASE
                        WHEN requested_at > ? THEN 'pending'
                        ELSE 'done'
                    END,
                    last_error = NULL,
                    updated_at = ?
                WHERE source = ?
                """,
                (req_ts, now, token),
            )
        self._conn.commit()
        return cursor.rowcount > 0

    def mark_episode_source_failed(
        self,
        source: str,
        error: str = "",
        *,
        requested_at: Optional[float] = None,
    ) -> bool:
        """标记 source 失败；若运行期间发生新写入，则重新回到 pending。"""
        token = self._normalize_episode_source(source)
        if not token:
            return False

        err_text = str(error or "").strip()[:500]
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        if requested_at is None:
            cursor.execute(
                """
                UPDATE episode_rebuild_sources
                SET status = 'failed',
                    retry_count = COALESCE(retry_count, 0) + 1,
                    last_error = ?,
                    updated_at = ?
                WHERE source = ?
                """,
                (err_text, now, token),
            )
        else:
            req_ts = float(requested_at)
            cursor.execute(
                """
                UPDATE episode_rebuild_sources
                SET status = CASE
                        WHEN requested_at > ? THEN 'pending'
                        ELSE 'failed'
                    END,
                    retry_count = CASE
                        WHEN requested_at > ? THEN COALESCE(retry_count, 0)
                        ELSE COALESCE(retry_count, 0) + 1
                    END,
                    last_error = CASE
                        WHEN requested_at > ? THEN NULL
                        ELSE ?
                    END,
                    updated_at = ?
                WHERE source = ?
                """,
                (req_ts, req_ts, req_ts, err_text, now, token),
            )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_episode_source_rebuilds(
        self,
        *,
        statuses: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """列出 source 重建状态。"""
        safe_limit = max(1, int(limit))
        params: List[Any] = []
        conditions: List[str] = []
        normalized_statuses = [
            str(item or "").strip().lower()
            for item in (statuses or [])
            if str(item or "").strip().lower() in {"pending", "running", "done", "failed"}
        ]
        if normalized_statuses:
            placeholders = ",".join(["?"] * len(normalized_statuses))
            conditions.append(f"status IN ({placeholders})")
            params.extend(normalized_statuses)

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(safe_limit)
        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            SELECT source, status, retry_count, last_error, reason, requested_at, updated_at
            FROM episode_rebuild_sources
            {where_sql}
            ORDER BY updated_at DESC, source ASC
            LIMIT ?
            """,
            tuple(params),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_episode_source_rebuild_summary(self, failed_limit: int = 20) -> Dict[str, Any]:
        """汇总 source 重建队列状态。"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT status, COUNT(*) AS cnt
            FROM episode_rebuild_sources
            GROUP BY status
            """
        )
        counts = {"pending": 0, "running": 0, "done": 0, "failed": 0, "total": 0}
        for row in cursor.fetchall():
            status = str(row["status"] or "").strip().lower()
            cnt = int(row["cnt"] or 0)
            counts[status] = counts.get(status, 0) + cnt
            counts["total"] += cnt

        running = self.list_episode_source_rebuilds(statuses=["running"], limit=20)
        failed = self.list_episode_source_rebuilds(
            statuses=["failed"],
            limit=max(1, int(failed_limit)),
        )
        return {
            "counts": counts,
            "running": running,
            "failed": failed,
        }

    def get_live_paragraphs_by_source(self, source: str) -> List[Dict[str, Any]]:
        """获取指定 source 下所有 live paragraphs。"""
        token = self._normalize_episode_source(source)
        if not token:
            return []
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM paragraphs
            WHERE TRIM(COALESCE(source, '')) = ?
              AND (is_deleted IS NULL OR is_deleted = 0)
            ORDER BY created_at ASC, hash ASC
            """,
            (token,),
        )
        return [self._row_to_dict(row, "paragraph") for row in cursor.fetchall()]

    def list_episode_sources_for_rebuild(self) -> List[str]:
        """列出全量重建涉及的 source（live paragraphs + stale episodes）。"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT source
            FROM (
                SELECT TRIM(source) AS source
                FROM paragraphs
                WHERE TRIM(COALESCE(source, '')) != ''
                  AND (is_deleted IS NULL OR is_deleted = 0)
                UNION
                SELECT TRIM(source) AS source
                FROM episodes
                WHERE TRIM(COALESCE(source, '')) != ''
            )
            WHERE TRIM(COALESCE(source, '')) != ''
            ORDER BY source ASC
            """
        )
        return self._dedupe_episode_sources([row["source"] for row in cursor.fetchall()])

    def is_episode_source_query_blocked(self, source: str) -> bool:
        """判断 source 是否处于重建中或失败状态。"""
        token = self._normalize_episode_source(source)
        if not token:
            return False
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT 1
            FROM episode_rebuild_sources
            WHERE source = ?
              AND status IN ('pending', 'running', 'failed')
            LIMIT 1
            """,
            (token,),
        )
        return cursor.fetchone() is not None

    def replace_episodes_for_source(
        self,
        source: str,
        episodes_payloads: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """按 source 全量替换 episode 结果。"""
        token = self._normalize_episode_source(source)
        if not token:
            return {"source": "", "episode_count": 0}

        payloads = [dict(item) for item in (episodes_payloads or []) if isinstance(item, dict)]
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()

        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                """
                SELECT episode_id, created_at
                FROM episodes
                WHERE TRIM(COALESCE(source, '')) = ?
                """,
                (token,),
            )
            existing_created_at = {
                str(row["episode_id"]): self._as_optional_float(row["created_at"])
                for row in cursor.fetchall()
            }

            cursor.execute(
                "DELETE FROM episodes WHERE TRIM(COALESCE(source, '')) = ?",
                (token,),
            )

            inserted_count = 0
            for raw_payload in payloads:
                title = str(raw_payload.get("title", "") or "").strip()
                summary = str(raw_payload.get("summary", "") or "").strip()
                evidence_ids = [
                    str(item).strip()
                    for item in (raw_payload.get("evidence_ids") or [])
                    if str(item).strip()
                ]
                evidence_ids = list(dict.fromkeys(evidence_ids))
                if not title or not summary or not evidence_ids:
                    continue

                episode_id = str(raw_payload.get("episode_id", "") or "").strip()
                if not episode_id:
                    seed = json.dumps(
                        {
                            "source": token,
                            "title": title,
                            "summary": summary,
                            "event_time_start": raw_payload.get("event_time_start"),
                            "event_time_end": raw_payload.get("event_time_end"),
                            "evidence_ids": evidence_ids,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    episode_id = compute_hash(seed)

                participants = [
                    str(item).strip()
                    for item in (raw_payload.get("participants") or [])
                    if str(item).strip()
                ][:16]
                keywords = [
                    str(item).strip()
                    for item in (raw_payload.get("keywords") or [])
                    if str(item).strip()
                ][:20]
                paragraph_count = raw_payload.get("paragraph_count", len(evidence_ids))
                try:
                    paragraph_count = max(0, int(paragraph_count))
                except Exception:
                    paragraph_count = len(evidence_ids)
                if paragraph_count <= 0:
                    paragraph_count = len(evidence_ids)
                if paragraph_count <= 0:
                    continue

                time_confidence = raw_payload.get("time_confidence", 1.0)
                llm_confidence = raw_payload.get("llm_confidence", 0.0)
                try:
                    time_confidence = float(time_confidence)
                except Exception:
                    time_confidence = 1.0
                try:
                    llm_confidence = float(llm_confidence)
                except Exception:
                    llm_confidence = 0.0

                created_at = existing_created_at.get(episode_id)
                created_ts = created_at if created_at is not None else now
                updated_ts = self._as_optional_float(raw_payload.get("updated_at")) or now

                cursor.execute(
                    """
                    INSERT INTO episodes (
                        episode_id, source, title, summary,
                        event_time_start, event_time_end, time_granularity, time_confidence,
                        participants_json, keywords_json, evidence_ids_json,
                        paragraph_count, llm_confidence, segmentation_model, segmentation_version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        episode_id,
                        token,
                        title[:120],
                        summary[:2000],
                        self._as_optional_float(raw_payload.get("event_time_start")),
                        self._as_optional_float(raw_payload.get("event_time_end")),
                        str(raw_payload.get("time_granularity", "") or "").strip() or None,
                        time_confidence,
                        json.dumps(participants, ensure_ascii=False),
                        json.dumps(keywords, ensure_ascii=False),
                        json.dumps(evidence_ids, ensure_ascii=False),
                        paragraph_count,
                        llm_confidence,
                        str(raw_payload.get("segmentation_model", "") or "").strip() or None,
                        str(raw_payload.get("segmentation_version", "") or "").strip() or None,
                        created_ts,
                        updated_ts,
                    ),
                )
                cursor.executemany(
                    """
                    INSERT OR IGNORE INTO episode_paragraphs (episode_id, paragraph_hash, position)
                    VALUES (?, ?, ?)
                    """,
                    [(episode_id, hash_value, idx) for idx, hash_value in enumerate(evidence_ids)],
                )
                inserted_count += 1

            self._conn.commit()
            return {"source": token, "episode_count": inserted_count}
        except Exception:
            self._conn.rollback()
            raise

    def enqueue_episode_pending(
        self,
        paragraph_hash: str,
        source: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> None:
        """将段落入队到 episode 异步生成队列。"""
        token = str(paragraph_hash or "").strip()
        if not token:
            return
        now = datetime.now().timestamp()
        created_ts = float(created_at) if created_at is not None else now
        src = str(source or "").strip() or None

        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO episode_pending_paragraphs (
                paragraph_hash, source, created_at, status, retry_count, last_error, updated_at
            ) VALUES (?, ?, ?, 'pending', 0, NULL, ?)
            ON CONFLICT(paragraph_hash) DO UPDATE SET
                source = excluded.source,
                created_at = COALESCE(episode_pending_paragraphs.created_at, excluded.created_at),
                status = CASE
                    WHEN episode_pending_paragraphs.status = 'done' THEN 'done'
                    ELSE 'pending'
                END,
                last_error = CASE
                    WHEN episode_pending_paragraphs.status = 'done' THEN episode_pending_paragraphs.last_error
                    ELSE NULL
                END,
                updated_at = excluded.updated_at
            """,
            (token, src, created_ts, now),
        )
        self._conn.commit()

    def fetch_episode_pending_batch(self, limit: int = 20, max_retry: int = 3) -> List[Dict[str, Any]]:
        """获取待处理 episode 队列批次。"""
        safe_limit = max(1, int(limit))
        safe_retry = max(0, int(max_retry))
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT paragraph_hash, source, created_at, status, retry_count, last_error, updated_at
            FROM episode_pending_paragraphs
            WHERE status = 'pending'
               OR (status = 'failed' AND retry_count < ?)
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (safe_retry, safe_limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_episode_pending_running(self, hashes: List[str]) -> None:
        """批量标记队列项为 running。"""
        if not hashes:
            return
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        chunk_size = 500
        uniq = list(dict.fromkeys([str(h).strip() for h in hashes if str(h).strip()]))
        for i in range(0, len(uniq), chunk_size):
            chunk = uniq[i:i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cursor.execute(
                f"""
                UPDATE episode_pending_paragraphs
                SET status = 'running', updated_at = ?
                WHERE paragraph_hash IN ({placeholders})
                  AND status IN ('pending', 'failed')
                """,
                [now] + chunk,
            )
        self._conn.commit()

    def mark_episode_pending_done(self, hashes: List[str]) -> None:
        """批量标记队列项为 done。"""
        if not hashes:
            return
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        chunk_size = 500
        uniq = list(dict.fromkeys([str(h).strip() for h in hashes if str(h).strip()]))
        for i in range(0, len(uniq), chunk_size):
            chunk = uniq[i:i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cursor.execute(
                f"""
                UPDATE episode_pending_paragraphs
                SET status = 'done',
                    last_error = NULL,
                    updated_at = ?
                WHERE paragraph_hash IN ({placeholders})
                """,
                [now] + chunk,
            )
        self._conn.commit()

    def mark_episode_pending_failed(self, hash_value: str, error: str = "") -> None:
        """标记单条队列项失败并累加重试次数。"""
        token = str(hash_value or "").strip()
        if not token:
            return
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            UPDATE episode_pending_paragraphs
            SET status = 'failed',
                retry_count = COALESCE(retry_count, 0) + 1,
                last_error = ?,
                updated_at = ?
            WHERE paragraph_hash = ?
            """,
            (str(error or ""), now, token),
        )
        self._conn.commit()

    def get_episode_pending_status_counts(self, source: str) -> Dict[str, int]:
        """统计某个 source 当前 pending 队列中的状态分布。"""
        token = self._normalize_episode_source(source)
        if not token:
            return {"pending": 0, "running": 0, "failed": 0, "done": 0}

        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM episode_pending_paragraphs
            WHERE TRIM(COALESCE(source, '')) = ?
            GROUP BY status
            """,
            (token,),
        )
        counts = {"pending": 0, "running": 0, "failed": 0, "done": 0}
        for row in cursor.fetchall():
            status = str(row["status"] or "").strip().lower()
            if status in counts:
                counts[status] = int(row["count"] or 0)
        return counts

    def _episode_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)

        def _load_list(raw: Any) -> List[Any]:
            if not raw:
                return []
            try:
                val = json.loads(raw)
                return val if isinstance(val, list) else []
            except Exception:
                return []

        data["participants"] = _load_list(data.pop("participants_json", None))
        data["keywords"] = _load_list(data.pop("keywords_json", None))
        data["evidence_ids"] = _load_list(data.pop("evidence_ids_json", None))
        return data

    @staticmethod
    def _as_optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    def upsert_episode(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """写入或更新 episode。"""
        if not isinstance(payload, dict):
            raise ValueError("payload 必须是字典")

        title = str(payload.get("title", "") or "").strip()
        summary = str(payload.get("summary", "") or "").strip()
        if not title:
            raise ValueError("episode.title 不能为空")
        if not summary:
            raise ValueError("episode.summary 不能为空")

        source = str(payload.get("source", "") or "").strip() or None
        participants_raw = payload.get("participants", []) or []
        keywords_raw = payload.get("keywords", []) or []
        evidence_ids_raw = payload.get("evidence_ids", []) or []
        participants = [str(x).strip() for x in participants_raw if str(x).strip()]
        keywords = [str(x).strip() for x in keywords_raw if str(x).strip()]
        evidence_ids = [str(x).strip() for x in evidence_ids_raw if str(x).strip()]

        now = datetime.now().timestamp()
        created_at = self._as_optional_float(payload.get("created_at"))
        updated_at = self._as_optional_float(payload.get("updated_at"))
        created_ts = created_at if created_at is not None else now
        updated_ts = updated_at if updated_at is not None else now

        episode_id = str(payload.get("episode_id", "") or "").strip()
        if not episode_id:
            seed = json.dumps(
                {
                    "source": source,
                    "title": title,
                    "summary": summary,
                    "event_time_start": payload.get("event_time_start"),
                    "event_time_end": payload.get("event_time_end"),
                    "evidence_ids": evidence_ids,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            episode_id = compute_hash(seed)

        paragraph_count = payload.get("paragraph_count")
        if paragraph_count is None:
            paragraph_count = len(evidence_ids)
        try:
            paragraph_count = int(paragraph_count)
        except Exception:
            paragraph_count = len(evidence_ids)

        time_conf = payload.get("time_confidence", 1.0)
        llm_conf = payload.get("llm_confidence", 0.0)
        try:
            time_conf = float(time_conf)
        except Exception:
            time_conf = 1.0
        try:
            llm_conf = float(llm_conf)
        except Exception:
            llm_conf = 0.0

        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT created_at FROM episodes WHERE episode_id = ? LIMIT 1",
            (episode_id,),
        )
        existed = cursor.fetchone()
        if existed and existed[0] is not None:
            created_ts = float(existed[0])

        cursor.execute(
            """
            INSERT INTO episodes (
                episode_id, source, title, summary,
                event_time_start, event_time_end, time_granularity, time_confidence,
                participants_json, keywords_json, evidence_ids_json,
                paragraph_count, llm_confidence, segmentation_model, segmentation_version,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(episode_id) DO UPDATE SET
                source = excluded.source,
                title = excluded.title,
                summary = excluded.summary,
                event_time_start = excluded.event_time_start,
                event_time_end = excluded.event_time_end,
                time_granularity = excluded.time_granularity,
                time_confidence = excluded.time_confidence,
                participants_json = excluded.participants_json,
                keywords_json = excluded.keywords_json,
                evidence_ids_json = excluded.evidence_ids_json,
                paragraph_count = excluded.paragraph_count,
                llm_confidence = excluded.llm_confidence,
                segmentation_model = excluded.segmentation_model,
                segmentation_version = excluded.segmentation_version,
                updated_at = excluded.updated_at
            """,
            (
                episode_id,
                source,
                title,
                summary,
                self._as_optional_float(payload.get("event_time_start")),
                self._as_optional_float(payload.get("event_time_end")),
                str(payload.get("time_granularity", "") or "").strip() or None,
                time_conf,
                json.dumps(participants, ensure_ascii=False),
                json.dumps(keywords, ensure_ascii=False),
                json.dumps(evidence_ids, ensure_ascii=False),
                max(0, paragraph_count),
                llm_conf,
                str(payload.get("segmentation_model", "") or "").strip() or None,
                str(payload.get("segmentation_version", "") or "").strip() or None,
                created_ts,
                updated_ts,
            ),
        )
        self._conn.commit()
        return self.get_episode_by_id(episode_id) or {"episode_id": episode_id}

    def bind_episode_paragraphs(self, episode_id: str, paragraph_hashes_ordered: List[str]) -> int:
        """重建 episode 与段落映射。"""
        token = str(episode_id or "").strip()
        if not token:
            raise ValueError("episode_id 不能为空")

        normalized: List[str] = []
        seen = set()
        for item in paragraph_hashes_ordered or []:
            h = str(item or "").strip()
            if not h or h in seen:
                continue
            seen.add(h)
            normalized.append(h)

        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM episode_paragraphs WHERE episode_id = ?", (token,))

        if normalized:
            cursor.executemany(
                """
                INSERT OR IGNORE INTO episode_paragraphs (episode_id, paragraph_hash, position)
                VALUES (?, ?, ?)
                """,
                [(token, h, idx) for idx, h in enumerate(normalized)],
            )

        now = datetime.now().timestamp()
        cursor.execute(
            """
            UPDATE episodes
            SET paragraph_count = ?, updated_at = ?
            WHERE episode_id = ?
            """,
            (len(normalized), now, token),
        )
        self._conn.commit()
        return len(normalized)

    def _build_episode_query_components(
        self,
        *,
        time_from: Optional[float] = None,
        time_to: Optional[float] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Tuple[str, str, str, List[str], List[Any]]:
        source_expr = "TRIM(COALESCE(e.source, ''))"
        effective_start = "COALESCE(e.event_time_start, e.event_time_end, e.updated_at)"
        effective_end = "COALESCE(e.event_time_end, e.event_time_start, e.updated_at)"
        conditions: List[str] = []
        params: List[Any] = []

        conditions.append(f"{source_expr} != ''")
        conditions.append("COALESCE(e.paragraph_count, 0) > 0")
        conditions.append(
            """
            NOT EXISTS (
                SELECT 1
                FROM episode_rebuild_sources ers
                WHERE ers.source = TRIM(COALESCE(e.source, ''))
                  AND ers.status IN ('pending', 'running')
            )
            """
        )

        if source:
            token = self._normalize_episode_source(source)
            if not token:
                return source_expr, effective_start, effective_end, ["1 = 0"], []
            conditions.append(f"{source_expr} = ?")
            params.append(token)

        p = str(person or "").strip().lower()
        if p:
            like_person = f"%{p}%"
            conditions.append(
                """
                (
                    LOWER(COALESCE(e.participants_json, '')) LIKE ?
                    OR EXISTS (
                        SELECT 1
                        FROM episode_paragraphs ep_person
                        JOIN paragraph_entities pe ON pe.paragraph_hash = ep_person.paragraph_hash
                        JOIN entities en ON en.hash = pe.entity_hash
                        WHERE ep_person.episode_id = e.episode_id
                          AND LOWER(en.name) LIKE ?
                    )
                )
                """
            )
            params.extend([like_person, like_person])

        if time_from is not None and time_to is not None:
            conditions.append(f"({effective_end} >= ? AND {effective_start} <= ?)")
            params.extend([float(time_from), float(time_to)])
        elif time_from is not None:
            conditions.append(f"({effective_end} >= ?)")
            params.append(float(time_from))
        elif time_to is not None:
            conditions.append(f"({effective_start} <= ?)")
            params.append(float(time_to))

        return source_expr, effective_start, effective_end, conditions, params

    @staticmethod
    def _tokenize_episode_query(query: str) -> Tuple[str, List[str]]:
        """将 episode 查询归一化为短语和 token。"""
        normalized = normalize_text(str(query or "")).strip().lower()
        if not normalized:
            return "", []

        token_pattern = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}")
        tokens: List[str] = []
        seen = set()
        for token in token_pattern.findall(normalized):
            if token in seen:
                continue
            seen.add(token)
            tokens.append(token)

        if not tokens and len(normalized) >= 2:
            tokens = [normalized]
        return normalized, tokens

    def get_episode_rows_by_paragraph_hashes(
        self,
        paragraph_hashes: List[str],
        *,
        time_from: Optional[float] = None,
        time_to: Optional[float] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        normalized: List[str] = []
        seen = set()
        for item in paragraph_hashes or []:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        if not normalized:
            return []

        _, _, _, conditions, params = self._build_episode_query_components(
            time_from=time_from,
            time_to=time_to,
            person=person,
            source=source,
        )
        placeholders = ",".join(["?"] * len(normalized))
        conditions.append(f"ep.paragraph_hash IN ({placeholders})")
        conditions.append("(p.is_deleted IS NULL OR p.is_deleted = 0)")
        where_sql = "WHERE " + " AND ".join(conditions)

        sql = f"""
            SELECT e.*, ep.paragraph_hash AS matched_paragraph_hash
            FROM episodes e
            JOIN episode_paragraphs ep ON ep.episode_id = e.episode_id
            JOIN paragraphs p ON p.hash = ep.paragraph_hash
            {where_sql}
            ORDER BY e.updated_at DESC
        """
        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params + normalized))

        grouped: Dict[str, Dict[str, Any]] = {}
        for row in cursor.fetchall():
            episode_id = str(row["episode_id"] or "").strip()
            if not episode_id:
                continue
            payload = grouped.get(episode_id)
            if payload is None:
                payload = self._episode_row_to_dict(row)
                payload["matched_paragraph_hashes"] = []
                grouped[episode_id] = payload
            matched_hash = str(row["matched_paragraph_hash"] or "").strip()
            if matched_hash and matched_hash not in payload["matched_paragraph_hashes"]:
                payload["matched_paragraph_hashes"].append(matched_hash)

        out = list(grouped.values())
        for item in out:
            item["matched_paragraph_count"] = len(item.get("matched_paragraph_hashes", []))
        return out

    def get_episode_rows_by_relation_hashes(
        self,
        relation_hashes: List[str],
        *,
        time_from: Optional[float] = None,
        time_to: Optional[float] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        normalized: List[str] = []
        seen = set()
        for item in relation_hashes or []:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        if not normalized:
            return []

        _, _, _, conditions, params = self._build_episode_query_components(
            time_from=time_from,
            time_to=time_to,
            person=person,
            source=source,
        )
        placeholders = ",".join(["?"] * len(normalized))
        conditions.append(f"pr.relation_hash IN ({placeholders})")
        conditions.append("(p.is_deleted IS NULL OR p.is_deleted = 0)")
        where_sql = "WHERE " + " AND ".join(conditions)

        sql = f"""
            SELECT
                e.*,
                p.hash AS matched_paragraph_hash,
                pr.relation_hash AS matched_relation_hash
            FROM episodes e
            JOIN episode_paragraphs ep ON ep.episode_id = e.episode_id
            JOIN paragraphs p ON p.hash = ep.paragraph_hash
            JOIN paragraph_relations pr ON pr.paragraph_hash = p.hash
            {where_sql}
            ORDER BY e.updated_at DESC
        """
        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params + normalized))

        grouped: Dict[str, Dict[str, Any]] = {}
        for row in cursor.fetchall():
            episode_id = str(row["episode_id"] or "").strip()
            if not episode_id:
                continue
            payload = grouped.get(episode_id)
            if payload is None:
                payload = self._episode_row_to_dict(row)
                payload["matched_paragraph_hashes"] = []
                payload["matched_relation_hashes"] = []
                grouped[episode_id] = payload
            matched_paragraph = str(row["matched_paragraph_hash"] or "").strip()
            matched_relation = str(row["matched_relation_hash"] or "").strip()
            if matched_paragraph and matched_paragraph not in payload["matched_paragraph_hashes"]:
                payload["matched_paragraph_hashes"].append(matched_paragraph)
            if matched_relation and matched_relation not in payload["matched_relation_hashes"]:
                payload["matched_relation_hashes"].append(matched_relation)

        out = list(grouped.values())
        for item in out:
            item["matched_paragraph_count"] = len(item.get("matched_paragraph_hashes", []))
            item["matched_relation_count"] = len(item.get("matched_relation_hashes", []))
        return out

    def query_episodes(
        self,
        query: str = "",
        time_from: Optional[float] = None,
        time_to: Optional[float] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """查询 episode 列表。"""
        safe_limit = max(1, int(limit))
        _, effective_start, effective_end, conditions, params = self._build_episode_query_components(
            time_from=time_from,
            time_to=time_to,
            person=person,
            source=source,
        )

        q, tokens = self._tokenize_episode_query(query)
        select_score_sql = "0.0 AS lexical_score"
        order_sql = f"{effective_end} DESC, e.updated_at DESC"
        select_params: List[Any] = []
        query_params: List[Any] = []
        if q:
            field_exprs = {
                "title": "LOWER(COALESCE(e.title, ''))",
                "summary": "LOWER(COALESCE(e.summary, ''))",
                "keywords": "LOWER(COALESCE(e.keywords_json, ''))",
                "participants": "LOWER(COALESCE(e.participants_json, ''))",
            }

            score_parts: List[str] = []
            phrase_like = f"%{q}%"
            score_parts.extend(
                [
                    f"CASE WHEN {field_exprs['title']} LIKE ? THEN 6.0 ELSE 0.0 END",
                    f"CASE WHEN {field_exprs['keywords']} LIKE ? THEN 4.5 ELSE 0.0 END",
                    f"CASE WHEN {field_exprs['summary']} LIKE ? THEN 3.0 ELSE 0.0 END",
                    f"CASE WHEN {field_exprs['participants']} LIKE ? THEN 2.0 ELSE 0.0 END",
                ]
            )
            select_params.extend([phrase_like, phrase_like, phrase_like, phrase_like])

            token_predicates: List[str] = []
            for token in tokens:
                like = f"%{token}%"
                token_any = (
                    f"({field_exprs['title']} LIKE ? OR "
                    f"{field_exprs['summary']} LIKE ? OR "
                    f"{field_exprs['keywords']} LIKE ? OR "
                    f"{field_exprs['participants']} LIKE ?)"
                )
                token_predicates.append(token_any)
                query_params.extend([like, like, like, like])

                score_parts.append(
                    "("
                    f"CASE WHEN {field_exprs['title']} LIKE ? THEN 3.0 ELSE 0.0 END + "
                    f"CASE WHEN {field_exprs['keywords']} LIKE ? THEN 2.5 ELSE 0.0 END + "
                    f"CASE WHEN {field_exprs['summary']} LIKE ? THEN 2.0 ELSE 0.0 END + "
                    f"CASE WHEN {field_exprs['participants']} LIKE ? THEN 1.5 ELSE 0.0 END + "
                    f"CASE WHEN {token_any.replace('?', '?')} THEN 2.0 ELSE 0.0 END"
                    ")"
                )
                select_params.extend([like, like, like, like, like, like, like, like])

            if token_predicates:
                conditions.append("(" + " OR ".join(token_predicates) + ")")

            select_score_sql = f"({' + '.join(score_parts)}) AS lexical_score"
            order_sql = f"lexical_score DESC, {effective_end} DESC, e.updated_at DESC"

        where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT e.*, {select_score_sql}
            FROM episodes e
            {where_sql}
            ORDER BY {order_sql}
            LIMIT ?
        """
        final_params = list(select_params) + list(params) + list(query_params) + [safe_limit]

        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(final_params))
        return [self._episode_row_to_dict(row) for row in cursor.fetchall()]

    def get_episode_by_id(self, episode_id: str) -> Optional[Dict[str, Any]]:
        """获取单条 episode。"""
        token = str(episode_id or "").strip()
        if not token:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM episodes WHERE episode_id = ? LIMIT 1",
            (token,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self._episode_row_to_dict(row)

    def get_episode_paragraphs(self, episode_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取 episode 关联段落（按 position 排序）。"""
        token = str(episode_id or "").strip()
        if not token:
            return []
        safe_limit = max(1, int(limit))
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT p.*, ep.position
            FROM episode_paragraphs ep
            JOIN paragraphs p ON p.hash = ep.paragraph_hash
            WHERE ep.episode_id = ?
              AND (p.is_deleted IS NULL OR p.is_deleted = 0)
            ORDER BY ep.position ASC
            LIMIT ?
            """,
            (token, safe_limit),
        )
        items = []
        for row in cursor.fetchall():
            payload = self._row_to_dict(row, "paragraph")
            payload["position"] = row["position"]
            items.append(payload)
        return items

    def has_table(self, table_name: str) -> bool:
        """检查数据库是否存在指定表。"""
        if not self._conn:
            return False
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
            (table_name,),
        )
        return cursor.fetchone() is not None

    def get_deleted_entities(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取已软删除的实体 (回收站用)"""
        if not self.has_table("entities"): return []
        
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT hash, name, deleted_at 
            FROM entities 
            WHERE is_deleted = 1 
            ORDER BY deleted_at DESC 
            LIMIT ?
        """, (limit,))
        
        items = []
        for row in cursor.fetchall():
            items.append({
                "hash": row[0],
                "name": row[1],
                "type": "entity", # 标记为实体
                "deleted_at": row[2]
            })
        return items

    def __repr__(self) -> str:
        stats = self.get_statistics() if self.is_connected else {}
        return (
            f"MetadataStore(paragraphs={stats.get('paragraph_count', 0)}, "
            f"entities={stats.get('entity_count', 0)}, "
            f"relations={stats.get('relation_count', 0)})"
        )

    def has_data(self) -> bool:
        """检查磁盘上是否存在现有数据"""
        if self.data_dir is None:
            return False
        return (self.data_dir / self.db_name).exists()
