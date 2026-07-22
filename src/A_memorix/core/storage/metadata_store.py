"""
元数据存储模块

基于SQLite的元数据管理，存储段落、实体、关系等信息。
"""

from datetime import datetime
from pathlib import Path
from typing import Any, ContextManager, Dict, List, Optional, Sequence, Tuple, Union

import json
import sqlite3
import uuid

from src.common.logger import get_logger

from ..utils.hash import compute_hash, normalize_text
from ..utils.memory_lifecycle_policy import (
    RelationLifecycleEvent,
    RelationLifecyclePolicy,
    RelationLifecycleState,
    apply_lifecycle_event,
    evaluate_lifecycle,
    retention_at,
)
from ..utils.time_parser import normalize_time_meta
from .knowledge_types import validate_stored_knowledge_type
from .metadata_cleanup import MetadataCleanupMixin
from .metadata_episode import MetadataEpisodeMixin
from .metadata_fact import MetadataFactMixin
from .metadata_feedback import MetadataFeedbackMixin
from .metadata_fts import MetadataFTSMixin
from .metadata_profile import MetadataProfileMixin
from .metadata_schema import MetadataSchemaMixin, SCHEMA_VERSION
from .sqlite_connection import SQLiteConnectionManager
from .transaction import ConnectionTransaction

logger = get_logger("A_Memorix.MetadataStore")


class MetadataStore(
    MetadataSchemaMixin,
    MetadataFTSMixin,
    MetadataCleanupMixin,
    MetadataEpisodeMixin,
    MetadataFactMixin,
    MetadataFeedbackMixin,
    MetadataProfileMixin,
):
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
        self._connection_manager: Optional[SQLiteConnectionManager] = None
        self._connection_override: Optional[sqlite3.Connection] = None
        self._db_path: Optional[Path] = None

        logger.debug(f"元数据存储初始化: db={db_name}")

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
        self._db_path = db_path

        if self._connection_manager is not None:
            self._connection_manager.close_all()
        self._connection_override = None
        self._connection_manager = SQLiteConnectionManager(db_path)
        self._connection_manager.connection()

        logger.info(f"数据库已连接: {db_path}")

        # 每次建立新连接都按真实表结构判断，避免切换数据目录后沿用旧状态。
        cursor = self._conn.cursor()
        cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'paragraphs'")
        schema_existed = cursor.fetchone() is not None
        if not schema_existed:
            self._initialize_tables()
        if enforce_schema:
            self._assert_schema_compatible(db_existed=schema_existed)

        # 初始化 FTS schema（幂等）
        try:
            self.ensure_fts_schema()
        except Exception as e:
            logger.warning(f"初始化 FTS schema 失败，将跳过 BM25 检索: {e}")

    def close(self) -> None:
        """关闭数据库连接"""
        if self._connection_override is not None:
            self._connection_override.close()
            self._connection_override = None
        if self._connection_manager is not None:
            self._connection_manager.close_all()
            self._connection_manager = None
        logger.info("数据库连接已关闭")

    @property
    def _conn(self) -> Optional[sqlite3.Connection]:
        """返回当前线程使用的连接，兼容历史存储实现。"""
        if self._connection_override is not None:
            return self._connection_override
        if self._connection_manager is None:
            return None
        return self._connection_manager.connection()

    @_conn.setter
    def _conn(self, connection: Optional[sqlite3.Connection]) -> None:
        """保留测试和迁移工具直接注入连接的能力。"""
        self._connection_override = connection

    def transaction(self, *, immediate: bool = False) -> ContextManager[sqlite3.Connection]:
        """创建统一事务边界，异常时回滚，成功时提交。"""
        if self._connection_override is not None:
            return ConnectionTransaction(self._connection_override, immediate=immediate)
        if self._connection_manager is None:
            raise RuntimeError("MetadataStore 未连接数据库")
        return self._connection_manager.transaction(immediate=immediate)

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

        try:
            with self.transaction(immediate=True):
                cursor = self._conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO paragraphs
                    (
                        hash, content, vector_index, created_at, updated_at, metadata, source, word_count,
                        event_time, event_time_start, event_time_end, time_granularity, time_confidence,
                        knowledge_type
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        hash_value,
                        content,
                        vector_index,
                        now,
                        now,
                        self._encode_metadata(metadata),
                        source,
                        word_count,
                        normalized_time.get("event_time"),
                        normalized_time.get("event_time_start"),
                        normalized_time.get("event_time_end"),
                        normalized_time.get("time_granularity"),
                        normalized_time.get("time_confidence", 1.0),
                        resolved_knowledge_type.value,
                    ),
                )
                self._upsert_paragraph_ngram_if_ready(
                    hash_value,
                    content,
                    count_delta=1,
                )
                self.fts_upsert_tokenized_paragraph(hash_value)
                self.enqueue_episode_source_rebuild(
                    source=source,
                    reason="paragraph_added",
                )
            logger.debug(
                f"添加段落: hash={hash_value[:16]}..., words={word_count}, type={resolved_knowledge_type.value}"
            )
            return hash_value
        except sqlite3.IntegrityError:
            logger.debug(f"段落已存在: {hash_value[:16]}...")
            if metadata:
                self._merge_existing_paragraph_metadata(hash_value, metadata)
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

        with self.transaction(immediate=True):
            cursor = self._conn.cursor()
            cursor.execute("SELECT 1 FROM entities WHERE hash = ?", (hash_value,))
            existed = cursor.fetchone() is not None
            cursor.execute(
                """
                INSERT INTO entities
                (hash, name, vector_index, appearance_count, created_at, metadata, is_deleted, deleted_at)
                VALUES (?, ?, ?, 1, ?, ?, 0, NULL)
                ON CONFLICT(hash) DO UPDATE SET
                    appearance_count = entities.appearance_count + 1,
                    is_deleted = 0,
                    deleted_at = NULL
                """,
                (
                    hash_value,
                    name,
                    vector_index,
                    now,
                    self._encode_metadata(metadata),
                ),
            )
            if source_paragraph:
                self.link_paragraph_entity(source_paragraph, hash_value)
        if existed:
            logger.debug(f"实体已存在(复活/计数+1): {name}")
        else:
            logger.debug(f"添加实体: {name} ({hash_value[:8]})")
        return hash_value

    def add_entities_batch(
        self,
        names: List[str],
        vector_index: Optional[int] = None,
        source_paragraph: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """在单个受管事务中批量添加实体。"""
        with self.transaction(immediate=True):
            return self._add_entities_batch(
                names,
                vector_index=vector_index,
                source_paragraph=source_paragraph,
                metadata=metadata,
            )

    def _add_entities_batch(
        self,
        names: List[str],
        vector_index: Optional[int] = None,
        source_paragraph: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """批量添加实体，并保持出现次数、软删除复活和段落提及次数语义。"""
        if not names:
            return []

        ordered_hashes: List[str] = []
        grouped_entities: Dict[str, Tuple[str, int]] = {}
        for name in names:
            name_normalized = self._canonicalize_name(name)
            if not name_normalized:
                raise ValueError("Entity name cannot be empty")
            hash_value = compute_hash(name_normalized)
            ordered_hashes.append(hash_value)
            existing = grouped_entities.get(hash_value)
            if existing is None:
                grouped_entities[hash_value] = (name, 1)
            else:
                grouped_entities[hash_value] = (existing[0], existing[1] + 1)

        now = datetime.now().timestamp()
        encoded_metadata = self._encode_metadata(metadata)
        cursor = self._conn.cursor()
        cursor.executemany(
            """
            INSERT INTO entities
            (hash, name, vector_index, appearance_count, created_at, metadata, is_deleted, deleted_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
            ON CONFLICT(hash) DO UPDATE SET
                appearance_count = entities.appearance_count + excluded.appearance_count,
                is_deleted = 0,
                deleted_at = NULL
            """,
            [
                (hash_value, name, vector_index, count, now, encoded_metadata)
                for hash_value, (name, count) in grouped_entities.items()
            ],
        )

        if source_paragraph:
            cursor.executemany(
                """
                INSERT INTO paragraph_entities (paragraph_hash, entity_hash, mention_count)
                VALUES (?, ?, ?)
                ON CONFLICT(paragraph_hash, entity_hash) DO UPDATE SET
                    mention_count = paragraph_entities.mention_count + excluded.mention_count
                """,
                [
                    (source_paragraph, hash_value, count)
                    for hash_value, (_, count) in grouped_entities.items()
                ],
            )
            sources = self._get_sources_for_paragraph_hashes([source_paragraph], include_deleted=True)
            self._enqueue_episode_source_rebuilds(
                sources,
                reason="paragraph_entity_linked",
            )
        else:
            self._conn.commit()

        logger.debug(f"批量添加实体: items={len(names)}, unique={len(grouped_entities)}")
        return ordered_hashes

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
        hash_value = self.compute_relation_hash(subject, predicate, obj)

        now = datetime.now().timestamp()

        # 记录原始 display name 到 metadata (如果需要的话，或者直接存到 DB 字段)
        # 这里我们直接存入 subject, predicate, object 字段，
        # 注意：如果 DB 里已存在该关系 (hash 相同)，则不会更新这些字段，保留第一次的拼写。

        with self.transaction(immediate=True):
            cursor = self._conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO relations
                (hash, subject, predicate, object, vector_index, confidence, created_at, source_paragraph, metadata,
                 retention_strength, retention_anchor_at, next_lifecycle_at, reinforcement_count, inactive_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, ?, 0, NULL)
            """,
                (
                    hash_value,
                    subject,  # 原始拼写
                    predicate,
                    obj,
                    vector_index,
                    confidence,
                    now,
                    source_paragraph,  # 这里的 source_paragraph 仅作为 "首次发现地" 记录，也可留空
                    self._encode_metadata(metadata),
                    now,
                    now,
                ),
            )
            if cursor.rowcount > 0:
                logger.debug(f"添加关系: {subject} -{predicate}-> {obj}")
            else:
                logger.debug(f"关系已存在: {subject} -{predicate}-> {obj}")

            # 3. 建立来源关联 (幂等)
            # 无论关系是新创建的还是已存在的，只要提供了 source_paragraph，都要建立连接
            if source_paragraph:
                self.link_paragraph_relation(source_paragraph, hash_value)
        return hash_value

    def add_relations_batch(
        self,
        relations: List[Tuple[str, str, str]],
        vector_index: Optional[int] = None,
        confidence: float = 1.0,
        source_paragraph: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """在单个受管事务中批量添加关系。"""
        with self.transaction(immediate=True):
            return self._add_relations_batch(
                relations,
                vector_index=vector_index,
                confidence=confidence,
                source_paragraph=source_paragraph,
                metadata=metadata,
            )

    def _add_relations_batch(
        self,
        relations: List[Tuple[str, str, str]],
        vector_index: Optional[int] = None,
        confidence: float = 1.0,
        source_paragraph: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """批量添加关系和段落关联，保留首见关系元数据。"""
        if not relations:
            return []

        relation_rows: List[Tuple[str, str, str, str]] = []
        for subject, predicate, obj in relations:
            relation_hash = self.compute_relation_hash(subject, predicate, obj)
            relation_rows.append((relation_hash, subject, predicate, obj))

        now = datetime.now().timestamp()
        encoded_metadata = self._encode_metadata(metadata)
        cursor = self._conn.cursor()
        cursor.executemany(
            """
            INSERT OR IGNORE INTO relations
            (hash, subject, predicate, object, vector_index, confidence, created_at, source_paragraph, metadata,
             retention_strength, retention_anchor_at, next_lifecycle_at, reinforcement_count, inactive_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, ?, 0, NULL)
            """,
            [
                (
                    relation_hash,
                    subject,
                    predicate,
                    obj,
                    vector_index,
                    confidence,
                    now,
                    source_paragraph,
                    encoded_metadata,
                    now,
                    now,
                )
                for relation_hash, subject, predicate, obj in relation_rows
            ],
        )

        if source_paragraph:
            unique_relation_hashes = list(dict.fromkeys(row[0] for row in relation_rows))
            placeholders = ",".join(["?"] * len(unique_relation_hashes))
            cursor.execute(
                f"""
                SELECT relation_hash
                FROM paragraph_relations
                WHERE paragraph_hash = ?
                  AND relation_hash IN ({placeholders})
                """,
                (source_paragraph, *unique_relation_hashes),
            )
            existing_links = {str(row[0]) for row in cursor.fetchall()}
            cursor.executemany(
                """
                INSERT OR IGNORE INTO paragraph_relations (paragraph_hash, relation_hash)
                VALUES (?, ?)
                """,
                [(source_paragraph, relation_hash) for relation_hash, _, _, _ in relation_rows],
            )
            new_evidence_hashes = [
                relation_hash for relation_hash in unique_relation_hashes if relation_hash not in existing_links
            ]
            self._observe_relation_evidence(new_evidence_hashes, observed_at=now, cursor=cursor)
            sources = self._get_sources_for_paragraph_hashes([source_paragraph], include_deleted=True)
            self._enqueue_episode_source_rebuilds(
                sources,
                reason="paragraph_relation_linked",
            )
        else:
            self._conn.commit()

        logger.debug(f"批量添加关系: items={len(relations)}")
        return [relation_hash for relation_hash, _, _, _ in relation_rows]

    def compute_relation_hash(self, subject: str, predicate: str, obj: str) -> str:
        """
        计算 relation 的稳定 hash，不执行写入。
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
        return compute_hash(relation_key)

    def link_paragraph_relation(
        self,
        paragraph_hash: str,
        relation_hash: str,
    ) -> bool:
        """
        关联段落和关系 (幂等)
        """
        try:
            with self.transaction(immediate=True):
                cursor = self._conn.cursor()
                # 使用 INSERT OR IGNORE 避免重复报错
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO paragraph_relations
                    (paragraph_hash, relation_hash)
                    VALUES (?, ?)
                    """,
                    (paragraph_hash, relation_hash),
                )
                linked = cursor.rowcount > 0
                if linked:
                    self._observe_relation_evidence(
                        [relation_hash],
                        observed_at=datetime.now().timestamp(),
                        cursor=cursor,
                    )
                    self._enqueue_episode_source_rebuilds(
                        self._get_sources_for_paragraph_hashes([paragraph_hash], include_deleted=True),
                        reason="paragraph_relation_linked",
                    )
            return linked
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
        try:
            with self.transaction(immediate=True):
                cursor = self._conn.cursor()
                # 首先尝试插入
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO paragraph_entities
                    (paragraph_hash, entity_hash, mention_count)
                    VALUES (?, ?, ?)
                    """,
                    (paragraph_hash, entity_hash, mention_count),
                )

                if cursor.rowcount == 0:
                    # 如果已存在 (IGNORE生效)，则更新计数
                    cursor.execute(
                        """
                        UPDATE paragraph_entities
                        SET mention_count = mention_count + ?
                        WHERE paragraph_hash = ? AND entity_hash = ?
                        """,
                        (mention_count, paragraph_hash, entity_hash),
                    )

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
        cursor.execute(
            """
            SELECT * FROM paragraphs WHERE hash = ?
        """,
            (hash_value,),
        )
        row = cursor.fetchone()

        if row:
            return self._row_to_dict(row, "paragraph")
        return None

    def get_paragraphs_by_hashes(
        self,
        hash_values: Sequence[str],
    ) -> Dict[str, Dict[str, Any]]:
        """批量获取段落，按输入 hash 去重后返回 hash -> paragraph。"""
        normalized = self._normalize_hash_sequence(hash_values)
        if not normalized:
            return {}

        out: Dict[str, Dict[str, Any]] = {}
        cursor = self._conn.cursor()
        for batch in self._iter_sql_batches(normalized):
            placeholders = ",".join(["?"] * len(batch))
            cursor.execute(
                f"""
                SELECT * FROM paragraphs
                WHERE hash IN ({placeholders})
                """,
                tuple(batch),
            )
            for row in cursor.fetchall():
                payload = self._row_to_dict(row, "paragraph")
                out[str(payload.get("hash", "") or "")] = payload
        return out

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

        with self.transaction(immediate=True):
            cursor = self._conn.cursor()
            cursor.execute(
                f"UPDATE paragraphs SET {', '.join(updates)} WHERE hash = ?",
                tuple(params),
            )
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
        cursor.execute(
            """
            SELECT * FROM entities WHERE hash = ?
        """,
            (hash_value,),
        )
        row = cursor.fetchone()

        if row:
            return self._row_to_dict(row, "entity")
        return None

    def get_entities_by_hashes(
        self,
        hash_values: Sequence[str],
    ) -> Dict[str, Dict[str, Any]]:
        """批量获取实体，按输入 hash 去重后返回 hash -> entity。"""
        normalized = self._normalize_hash_sequence(hash_values)
        if not normalized:
            return {}

        out: Dict[str, Dict[str, Any]] = {}
        cursor = self._conn.cursor()
        for batch in self._iter_sql_batches(normalized):
            placeholders = ",".join(["?"] * len(batch))
            cursor.execute(
                f"""
                SELECT * FROM entities
                WHERE hash IN ({placeholders})
                """,
                tuple(batch),
            )
            for row in cursor.fetchall():
                payload = self._row_to_dict(row, "entity")
                out[str(payload.get("hash", "") or "")] = payload
        return out

    def get_relation(self, hash_value: str, include_inactive: bool = True) -> Optional[Dict[str, Any]]:
        """
        获取关系

        Args:
            hash_value: 关系哈希

        Returns:
            关系信息字典，不存在则返回None
        """
        cursor = self._conn.cursor()
        if include_inactive:
            cursor.execute(
                """
                SELECT * FROM relations WHERE hash = ?
                """,
                (hash_value,),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM relations
                WHERE hash = ?
                  AND (is_inactive IS NULL OR is_inactive = 0)
                """,
                (hash_value,),
            )
        row = cursor.fetchone()

        if row:
            return self._row_to_dict(row, "relation")
        return None

    def update_relation_metadata(
        self,
        relation_hash: str,
        patch: Dict[str, Any],
        *,
        merge: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """更新关系 metadata，并返回更新后的 metadata。"""
        hash_token = str(relation_hash or "").strip()
        if not hash_token:
            raise ValueError("relation_hash 不能为空")
        if not isinstance(patch, dict):
            raise TypeError("patch 必须是 dict")

        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT metadata
            FROM relations
            WHERE hash = ?
            LIMIT 1
            """,
            (hash_token,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        metadata = self._decode_metadata(row["metadata"])
        updated = self._deep_merge_dict(metadata, patch) if merge else dict(patch)
        cursor.execute(
            """
            UPDATE relations
            SET metadata = ?
            WHERE hash = ?
            """,
            (self._encode_metadata(updated), hash_token),
        )
        self._conn.commit()
        return updated

    def get_relations_by_hashes(
        self,
        hash_values: Sequence[str],
        include_inactive: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        """批量获取关系，按输入 hash 去重后返回 hash -> relation。"""
        normalized = self._normalize_hash_sequence(hash_values)
        if not normalized:
            return {}

        out: Dict[str, Dict[str, Any]] = {}
        cursor = self._conn.cursor()
        inactive_sql = "" if include_inactive else "AND (is_inactive IS NULL OR is_inactive = 0)"
        for batch in self._iter_sql_batches(normalized):
            placeholders = ",".join(["?"] * len(batch))
            cursor.execute(
                f"""
                SELECT * FROM relations
                WHERE hash IN ({placeholders})
                  {inactive_sql}
                """,
                tuple(batch),
            )
            for row in cursor.fetchall():
                payload = self._row_to_dict(row, "relation")
                out[str(payload.get("hash", "") or "")] = payload
        return out

    def get_paragraph_relations(self, paragraph_hash: str) -> List[Dict[str, Any]]:
        """
        获取段落的所有关系

        Args:
            paragraph_hash: 段落哈希

        Returns:
            关系列表
        """
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT r.* FROM relations r
            JOIN paragraph_relations pr ON r.hash = pr.relation_hash
            WHERE pr.paragraph_hash = ?
        """,
            (paragraph_hash,),
        )

        return [self._row_to_dict(row, "relation") for row in cursor.fetchall()]

    def get_paragraph_hashes_by_relation_hashes(
        self,
        relation_hashes: List[str],
    ) -> Dict[str, List[str]]:
        normalized: List[str] = []
        seen = set()
        for item in relation_hashes or []:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        if not normalized:
            return {}

        placeholders = ",".join(["?"] * len(normalized))
        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            SELECT pr.relation_hash, pr.paragraph_hash
            FROM paragraph_relations pr
            JOIN paragraphs p ON p.hash = pr.paragraph_hash
            WHERE pr.relation_hash IN ({placeholders})
              AND (p.is_deleted IS NULL OR p.is_deleted = 0)
            ORDER BY pr.relation_hash ASC, p.updated_at DESC, p.created_at DESC, pr.paragraph_hash ASC
            """,
            tuple(normalized),
        )
        grouped: Dict[str, List[str]] = {token: [] for token in normalized}
        for row in cursor.fetchall():
            relation_hash = str(row["relation_hash"] or "").strip()
            paragraph_hash = str(row["paragraph_hash"] or "").strip()
            if not relation_hash or not paragraph_hash:
                continue
            if paragraph_hash not in grouped.setdefault(relation_hash, []):
                grouped[relation_hash].append(paragraph_hash)
        return grouped

    def get_paragraph_entities(self, paragraph_hash: str) -> List[Dict[str, Any]]:
        """
        获取段落的所有实体

        Args:
            paragraph_hash: 段落哈希

        Returns:
            实体列表
        """
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT e.*, pe.mention_count
            FROM entities e
            JOIN paragraph_entities pe ON e.hash = pe.entity_hash
            WHERE pe.paragraph_hash = ?
        """,
            (paragraph_hash,),
        )

        return [self._row_to_dict(row, "entity") for row in cursor.fetchall()]

    def get_paragraph_entities_by_hashes(
        self,
        paragraph_hashes: Sequence[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """批量获取段落实体映射，返回 paragraph_hash -> entities。"""
        normalized = self._normalize_hash_sequence(paragraph_hashes)
        if not normalized:
            return {}

        grouped: Dict[str, List[Dict[str, Any]]] = {hash_value: [] for hash_value in normalized}
        cursor = self._conn.cursor()
        for batch in self._iter_sql_batches(normalized):
            placeholders = ",".join(["?"] * len(batch))
            cursor.execute(
                f"""
                SELECT pe.paragraph_hash, e.*, pe.mention_count
                FROM paragraph_entities pe
                JOIN entities e ON e.hash = pe.entity_hash
                WHERE pe.paragraph_hash IN ({placeholders})
                """,
                tuple(batch),
            )
            for row in cursor.fetchall():
                paragraph_hash = str(row["paragraph_hash"] or "").strip()
                if not paragraph_hash:
                    continue
                grouped.setdefault(paragraph_hash, []).append(self._row_to_dict(row, "entity"))
        return grouped

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
        cursor.execute(
            """
            SELECT p.*
            FROM paragraphs p
            JOIN paragraph_entities pe ON p.hash = pe.paragraph_hash
            WHERE pe.entity_hash = ?
        """,
            (entity_hash,),
        )

        return [self._row_to_dict(row, "paragraph") for row in cursor.fetchall()]

    def get_paragraph_hashes_by_entity_hashes(
        self,
        entity_hashes: Sequence[str],
    ) -> Dict[str, List[str]]:
        """批量获取实体支撑段落 hash，返回 entity_hash -> paragraph_hashes。"""
        normalized = self._normalize_hash_sequence(entity_hashes)
        if not normalized:
            return {}

        grouped: Dict[str, List[str]] = {hash_value: [] for hash_value in normalized}
        cursor = self._conn.cursor()
        for batch in self._iter_sql_batches(normalized):
            placeholders = ",".join(["?"] * len(batch))
            cursor.execute(
                f"""
                SELECT pe.entity_hash, pe.paragraph_hash
                FROM paragraph_entities pe
                JOIN paragraphs p ON p.hash = pe.paragraph_hash
                WHERE pe.entity_hash IN ({placeholders})
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                ORDER BY pe.entity_hash ASC, p.updated_at DESC, p.created_at DESC, pe.paragraph_hash ASC
                """,
                tuple(batch),
            )
            for row in cursor.fetchall():
                entity_hash = str(row["entity_hash"] or "").strip()
                paragraph_hash = str(row["paragraph_hash"] or "").strip()
                if not entity_hash or not paragraph_hash:
                    continue
                if paragraph_hash not in grouped.setdefault(entity_hash, []):
                    grouped[entity_hash].append(paragraph_hash)
        return grouped

    def get_paragraphs_by_entity_hashes(
        self,
        entity_hashes: Sequence[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """批量获取实体支撑段落，返回 entity_hash -> paragraphs。"""
        normalized = self._normalize_hash_sequence(entity_hashes)
        if not normalized:
            return {}

        grouped: Dict[str, List[Dict[str, Any]]] = {hash_value: [] for hash_value in normalized}
        cursor = self._conn.cursor()
        for batch in self._iter_sql_batches(normalized):
            placeholders = ",".join(["?"] * len(batch))
            cursor.execute(
                f"""
                SELECT pe.entity_hash, p.*
                FROM paragraph_entities pe
                JOIN paragraphs p ON p.hash = pe.paragraph_hash
                WHERE pe.entity_hash IN ({placeholders})
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                ORDER BY pe.entity_hash ASC, p.updated_at DESC, p.created_at DESC, pe.paragraph_hash ASC
                """,
                tuple(batch),
            )
            for row in cursor.fetchall():
                entity_hash = str(row["entity_hash"] or "").strip()
                if not entity_hash:
                    continue
                grouped.setdefault(entity_hash, []).append(self._row_to_dict(row, "paragraph"))
        return grouped

    def get_relations(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        object: Optional[str] = None,
        include_inactive: bool = True,
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
        if not include_inactive:
            conditions.append("(is_inactive IS NULL OR is_inactive = 0)")

        sql = "SELECT * FROM relations"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params))

        return [self._row_to_dict(row, "relation") for row in cursor.fetchall()]

    def get_all_triples(self) -> List[Tuple[str, str, str, str]]:
        """
        高效获取所有三元组 (subject, predicate, object, hash)
        直接返回元组，跳过字典转换和 metadata 解码，用于构建 V5 Map 缓存。
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
        cursor.execute(
            """
            SELECT p.*
            FROM paragraphs p
            JOIN paragraph_relations pr ON p.hash = pr.paragraph_hash
            WHERE pr.relation_hash = ?
        """,
            (relation_hash,),
        )

        return [self._row_to_dict(row, "paragraph") for row in cursor.fetchall()]

    def get_paragraphs_by_relation_hashes(
        self,
        relation_hashes: Sequence[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """批量获取关系支撑段落，返回 relation_hash -> paragraphs。"""
        normalized = self._normalize_hash_sequence(relation_hashes)
        if not normalized:
            return {}

        grouped: Dict[str, List[Dict[str, Any]]] = {hash_value: [] for hash_value in normalized}
        cursor = self._conn.cursor()
        for batch in self._iter_sql_batches(normalized):
            placeholders = ",".join(["?"] * len(batch))
            cursor.execute(
                f"""
                SELECT pr.relation_hash, p.*
                FROM paragraph_relations pr
                JOIN paragraphs p ON p.hash = pr.paragraph_hash
                WHERE pr.relation_hash IN ({placeholders})
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                ORDER BY pr.relation_hash ASC, p.updated_at DESC, p.created_at DESC, pr.paragraph_hash ASC
                """,
                tuple(batch),
            )
            for row in cursor.fetchall():
                relation_hash = str(row["relation_hash"] or "").strip()
                if not relation_hash:
                    continue
                grouped.setdefault(relation_hash, []).append(self._row_to_dict(row, "paragraph"))
        return grouped

    def get_paragraphs_by_source(self, source: str) -> List[Dict[str, Any]]:
        """
        按来源获取段落

        Args:
            source: 来源标识符

        Returns:
            段落列表
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM paragraphs WHERE source = ?", (source,))
        return [self._row_to_dict(row, "paragraph") for row in cursor.fetchall()]

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
              AND (is_deleted IS NULL OR is_deleted = 0)
            GROUP BY source
            ORDER BY last_updated DESC
        """)

        results = []
        for row in cursor.fetchall():
            results.append({"source": row[0], "count": row[1], "last_updated": row[2]})
        return results

    def search_paragraphs_by_content(self, content_query: str) -> List[Dict[str, Any]]:
        """按内容模糊搜索段落"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT * FROM paragraphs WHERE content LIKE ?
        """,
            (f"%{content_query}%",),
        )
        return [self._row_to_dict(row, "paragraph") for row in cursor.fetchall()]

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
        err_text = str(error).strip() if error is not None else None
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
        normalized_states = [str(s or "").strip().lower() for s in (states or []) if str(s or "").strip()]
        normalized_states = [s for s in normalized_states if s in {"none", "pending", "ready", "failed"}]
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
        cursor.execute(
            f"""
            UPDATE {table_map[item_type]}
            SET vector_index = ?
            WHERE hash = ?
        """,
            (vector_index, hash_value),
        )
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
        if item_type == "paragraph":
            cursor.execute(
                """
                UPDATE paragraphs
                SET is_permanent = ?,
                    expires_at = CASE WHEN ? = 1 THEN NULL ELSE expires_at END,
                    deletion_reason = CASE WHEN ? = 1 THEN NULL ELSE deletion_reason END
                WHERE hash = ?
                """,
                (
                    1 if is_permanent else 0,
                    1 if is_permanent else 0,
                    1 if is_permanent else 0,
                    hash_value,
                ),
            )
        else:
            permanent_value = 1 if is_permanent else 0
            cursor.execute(
                """
                UPDATE relations
                SET is_permanent = ?,
                    is_pinned = ?,
                    next_lifecycle_at = CASE WHEN ? = 1 THEN NULL ELSE retention_anchor_at END,
                    lifecycle_revision = COALESCE(lifecycle_revision, 0) + 1
                WHERE hash = ?
                  AND (
                    is_permanent IS NOT ?
                    OR is_pinned IS NOT ?
                    OR (? = 1 AND next_lifecycle_at IS NOT NULL)
                    OR (? = 0 AND next_lifecycle_at IS NOT retention_anchor_at)
                  )
                """,
                (
                    permanent_value,
                    permanent_value,
                    permanent_value,
                    hash_value,
                    permanent_value,
                    permanent_value,
                    permanent_value,
                    permanent_value,
                ),
            )
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
        cursor.execute(
            f"""
            UPDATE {table_map[item_type]}
            SET last_accessed = MAX(COALESCE(last_accessed, ?), ?),
                access_count = access_count + 1
            WHERE hash = ?
        """,
            (now, now, hash_value),
        )
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
        if value is None or value == "":
            return default
        try:
            return json.loads(value)
        except Exception:
            return default

    @staticmethod
    def _encode_metadata(value: Optional[Dict[str, Any]]) -> str:
        if value is None:
            return "{}"
        if not isinstance(value, dict):
            raise TypeError("metadata 必须是 dict")
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _decode_metadata(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if value is None:
            return {}
        if isinstance(value, bytes):
            if not value:
                return {}
            value = value.decode("utf-8")
        if value == "":
            return {}
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise TypeError("metadata 字段必须解码为 dict")
        return decoded

    @classmethod
    def _deep_merge_dict(cls, base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(base)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge_dict(merged[key], value)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _append_metadata_tokens(tokens: List[str], value: Any) -> None:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                MetadataStore._append_metadata_tokens(tokens, item)
            return

        token = str(value or "").strip()
        if token and token not in tokens:
            tokens.append(token)

    @classmethod
    def _merge_metadata_binding_ids(
        cls,
        merged: Dict[str, Any],
        base: Dict[str, Any],
        patch: Dict[str, Any],
        scalar_key: str,
        list_key: str,
    ) -> None:
        tokens: List[str] = []
        for metadata in (base, patch):
            cls._append_metadata_tokens(tokens, metadata.get(scalar_key))
            cls._append_metadata_tokens(tokens, metadata.get(list_key))

        if not tokens:
            return

        preferred_scalar = str(
            patch.get(scalar_key) or merged.get(scalar_key) or base.get(scalar_key) or tokens[0]
        ).strip()
        merged[scalar_key] = preferred_scalar
        merged[list_key] = tokens

    @classmethod
    def _merge_paragraph_metadata(cls, base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        merged = cls._deep_merge_dict(base, patch)
        for scalar_key, list_key in (
            ("chat_id", "chat_ids"),
            ("session_id", "session_ids"),
            ("stream_id", "stream_ids"),
        ):
            cls._merge_metadata_binding_ids(merged, base, patch, scalar_key, list_key)
        return merged

    def _merge_existing_paragraph_metadata(
        self,
        paragraph_hash: str,
        metadata_patch: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(metadata_patch, dict):
            raise TypeError("metadata_patch 必须是 dict")

        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT metadata
            FROM paragraphs
            WHERE hash = ?
            LIMIT 1
            """,
            (paragraph_hash,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        metadata = self._decode_metadata(row["metadata"])
        updated = self._merge_paragraph_metadata(metadata, metadata_patch)
        if updated == metadata:
            return metadata

        with self.transaction(immediate=True):
            cursor.execute(
                """
                UPDATE paragraphs
                SET metadata = ?, updated_at = ?
                WHERE hash = ?
                """,
                (self._encode_metadata(updated), datetime.now().timestamp(), paragraph_hash),
            )
            self._enqueue_episode_source_rebuilds(
                self._get_sources_for_paragraph_hashes([paragraph_hash], include_deleted=True),
                reason="paragraph_metadata_merged",
            )
        return updated

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

    def create_fuzzy_modify_plan(
        self,
        *,
        request_text: str,
        scope: str,
        plan: Dict[str, Any],
        preview: Optional[Dict[str, Any]] = None,
        target_person_id: str = "",
        target_chat_id: str = "",
        status: str = "awaiting_confirmation",
        confidence: float = 0.0,
        requested_by: str = "",
        reason: str = "",
        plan_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        op_id = str(plan_id or f"fuzzy_{uuid.uuid4().hex}").strip()
        now = datetime.now().timestamp()
        payload = {
            "plan_id": op_id,
            "request_text": str(request_text or "").strip(),
            "scope": str(scope or "").strip(),
            "target_person_id": str(target_person_id or "").strip(),
            "target_chat_id": str(target_chat_id or "").strip(),
            "status": str(status or "awaiting_confirmation").strip(),
            "confidence": float(confidence or 0.0),
            "plan": plan or {},
            "preview": preview or {},
            "execution": {},
            "created_at": now,
            "updated_at": now,
            "executed_at": None,
            "requested_by": str(requested_by or "").strip(),
            "reason": str(reason or "").strip(),
        }
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO memory_fuzzy_modify_plans (
                plan_id, request_text, scope, target_person_id, target_chat_id,
                status, confidence, plan_json, preview_json, execution_json,
                created_at, updated_at, executed_at, requested_by, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                payload["plan_id"],
                payload["request_text"],
                payload["scope"],
                payload["target_person_id"] or None,
                payload["target_chat_id"] or None,
                payload["status"],
                payload["confidence"],
                self._json_dumps(payload["plan"]),
                self._json_dumps(payload["preview"]),
                self._json_dumps(payload["execution"]),
                payload["created_at"],
                payload["updated_at"],
                payload["requested_by"] or None,
                payload["reason"] or None,
            ),
        )
        self._conn.commit()
        return self.get_fuzzy_modify_plan(op_id) or payload

    def update_fuzzy_modify_plan(
        self,
        plan_id: str,
        *,
        status: Optional[str] = None,
        plan: Optional[Dict[str, Any]] = None,
        preview: Optional[Dict[str, Any]] = None,
        execution: Optional[Dict[str, Any]] = None,
        confidence: Optional[float] = None,
        executed_at: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        token = str(plan_id or "").strip()
        if not token:
            return None

        assignments: List[str] = ["updated_at = ?"]
        params: List[Any] = [datetime.now().timestamp()]
        if status is not None:
            assignments.append("status = ?")
            params.append(str(status or "").strip())
        if plan is not None:
            assignments.append("plan_json = ?")
            params.append(self._json_dumps(plan))
        if preview is not None:
            assignments.append("preview_json = ?")
            params.append(self._json_dumps(preview))
        if execution is not None:
            assignments.append("execution_json = ?")
            params.append(self._json_dumps(execution))
        if confidence is not None:
            assignments.append("confidence = ?")
            params.append(float(confidence))
        if executed_at is not None:
            assignments.append("executed_at = ?")
            params.append(float(executed_at))
        if reason is not None:
            assignments.append("reason = ?")
            params.append(str(reason or "").strip() or None)
        params.append(token)

        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            UPDATE memory_fuzzy_modify_plans
            SET {", ".join(assignments)}
            WHERE plan_id = ?
            """,
            tuple(params),
        )
        self._conn.commit()
        if cursor.rowcount <= 0:
            return None
        return self.get_fuzzy_modify_plan(token)

    def claim_fuzzy_modify_plan(
        self,
        plan_id: str,
        *,
        stale_after_seconds: float = 300.0,
    ) -> Optional[Dict[str, Any]]:
        """原子领取待执行计划，并只回收超过租约的 executing 计划。"""
        token = str(plan_id or "").strip()
        if not token:
            return None
        now = datetime.now().timestamp()
        stale_before = now - max(1.0, float(stale_after_seconds))
        cursor = self._conn.cursor()
        cursor.execute(
            """
            UPDATE memory_fuzzy_modify_plans
            SET status = 'executing',
                updated_at = ?
            WHERE plan_id = ?
              AND (
                    status IN ('awaiting_confirmation', 'failed')
                    OR (status = 'executing' AND updated_at <= ?)
                  )
            """,
            (now, token, stale_before),
        )
        self._conn.commit()
        if int(cursor.rowcount or 0) <= 0:
            return None
        return self.get_fuzzy_modify_plan(token)

    def list_fuzzy_modify_plans(
        self,
        *,
        limit: int = 50,
        statuses: Optional[Sequence[str]] = None,
        scope: str = "",
    ) -> List[Dict[str, Any]]:
        normalized_statuses = [str(item or "").strip() for item in (statuses or []) if str(item or "").strip()]
        where: List[str] = []
        params: List[Any] = []
        if normalized_statuses:
            placeholders = ",".join(["?"] * len(normalized_statuses))
            where.append(f"status IN ({placeholders})")
            params.extend(normalized_statuses)
        scope_token = str(scope or "").strip()
        if scope_token:
            where.append("scope = ?")
            params.append(scope_token)
        params.append(max(1, int(limit or 50)))
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            SELECT *
            FROM memory_fuzzy_modify_plans
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [self._fuzzy_modify_plan_row_to_dict(row) for row in cursor.fetchall()]

    def get_fuzzy_modify_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        token = str(plan_id or "").strip()
        if not token:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM memory_fuzzy_modify_plans
            WHERE plan_id = ?
            LIMIT 1
            """,
            (token,),
        )
        row = cursor.fetchone()
        return self._fuzzy_modify_plan_row_to_dict(row) if row is not None else None

    def _fuzzy_modify_plan_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        payload = dict(row)
        payload["plan"] = self._json_loads(payload.pop("plan_json", None), {})
        payload["preview"] = self._json_loads(payload.pop("preview_json", None), {})
        payload["execution"] = self._json_loads(payload.pop("execution_json", None), {})
        payload["target_person_id"] = str(payload.get("target_person_id") or "")
        payload["target_chat_id"] = str(payload.get("target_chat_id") or "")
        payload["requested_by"] = str(payload.get("requested_by") or "")
        payload["reason"] = str(payload.get("reason") or "")
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

    def update_paragraph_metadata(
        self,
        paragraph_hash: str,
        patch: Dict[str, Any],
        *,
        merge: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """更新段落 metadata，并返回更新后的 metadata。"""
        hash_token = str(paragraph_hash or "").strip()
        if not hash_token:
            raise ValueError("paragraph_hash 不能为空")
        if not isinstance(patch, dict):
            raise TypeError("patch 必须是 dict")

        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT metadata, is_deleted
            FROM paragraphs
            WHERE hash = ?
            LIMIT 1
            """,
            (hash_token,),
        )
        row = cursor.fetchone()
        if row is None or bool(row["is_deleted"]):
            return None

        metadata = self._decode_metadata(row["metadata"])
        updated = self._merge_paragraph_metadata(metadata, patch) if merge else dict(patch)
        with self.transaction(immediate=True):
            cursor.execute(
                """
                UPDATE paragraphs
                SET metadata = ?, updated_at = ?
                WHERE hash = ?
                """,
                (self._encode_metadata(updated), datetime.now().timestamp(), hash_token),
            )
            self._enqueue_episode_source_rebuilds(
                self._get_sources_for_paragraph_hashes([hash_token], include_deleted=True),
                reason="paragraph_metadata_updated",
            )
        return updated

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

        cursor.execute("SELECT COUNT(*) FROM paragraph_stale_relation_marks")
        stats["stale_paragraph_mark_count"] = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM person_profile_refresh_queue WHERE status IN ('pending', 'running', 'failed')"
        )
        stats["person_profile_refresh_pending_count"] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM person_profile_refresh_queue WHERE status = 'failed'")
        stats["person_profile_refresh_failed_count"] = cursor.fetchone()[0]

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
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='deleted_relations'")
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
            "SELECT content FROM paragraphs WHERE hash=? AND is_deleted=1",
            (str(paragraph_hash),),
        )
        row = cursor.fetchone()
        cursor.execute(
            """
            UPDATE paragraphs
            SET is_deleted=0, deleted_at=NULL, expires_at=NULL, deletion_reason=NULL
            WHERE hash=? AND is_deleted=1
            """,
            (str(paragraph_hash),),
        )
        changed = cursor.rowcount > 0 and row is not None
        if changed:
            self._upsert_paragraph_ngram_if_ready(
                str(paragraph_hash),
                str(row["content"] or ""),
                count_delta=1,
            )
            self.fts_upsert_tokenized_paragraph(str(paragraph_hash))
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
        with self.transaction(immediate=True):
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
            if updated > 0:
                self._enqueue_episode_source_rebuilds(
                    touched_sources,
                    reason="paragraph_time_backfill",
                )
        return {"candidates": candidates, "updated": updated}

    def get_schema_version(self) -> int:
        cursor = self._conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
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

    def clear_all(self) -> None:
        """清空所有表数据"""
        cursor = self._conn.cursor()
        tables = [
            "relation_graph_projection_jobs",
            "paragraphs",
            "entities",
            "relations",
            "paragraph_relations",
            "paragraph_entities",
            "episodes",
            "episode_paragraphs",
            "episode_rebuild_sources",
            "paragraph_vector_backfill",
            "memory_feedback_tasks",
            "memory_feedback_action_logs",
            "paragraph_stale_relation_marks",
            "person_profile_refresh_queue",
            "fact_transitions",
            "fact_evidence",
            "fact_claims",
            "storage_cleanup_jobs",
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
        cursor.execute(
            """
            UPDATE relations
            SET last_accessed = MAX(COALESCE(last_accessed, ?), ?),
                access_count = access_count + ?
            WHERE hash = ?
        """,
            (now, now, access_count_delta, hash_value),
        )
        self._conn.commit()

    def claim_relation_graph_projection_jobs(
        self,
        *,
        limit: int = 1000,
        lease_seconds: float = 60.0,
        now: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """领取一批关系图投影任务，过期租约可以被正式重试。"""

        safe_limit = int(limit)
        safe_lease = float(lease_seconds)
        if safe_limit <= 0:
            raise ValueError("关系图投影任务 limit 必须大于0")
        if safe_lease <= 0.0:
            raise ValueError("关系图投影任务 lease_seconds 必须大于0")
        claimed_at = datetime.now().timestamp() if now is None else float(now)
        lease_token = uuid.uuid4().hex
        claimed: List[Dict[str, Any]] = []
        with self.transaction(immediate=True) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT relation_hash, subject, object, desired_active,
                       desired_lifecycle_revision, job_revision, status,
                       attempt_count, updated_at
                FROM relation_graph_projection_jobs
                WHERE status IN ('pending', 'failed')
                   OR (status = 'running' AND COALESCE(lease_expires_at, 0.0) <= ?)
                ORDER BY updated_at ASC, relation_hash ASC
                LIMIT ?
                """,
                (claimed_at, safe_limit),
            )
            for row in cursor.fetchall():
                cursor.execute(
                    """
                    UPDATE relation_graph_projection_jobs
                    SET status = 'running',
                        attempt_count = attempt_count + 1,
                        lease_token = ?,
                        lease_expires_at = ?,
                        last_error = NULL,
                        updated_at = ?
                    WHERE relation_hash = ?
                      AND job_revision = ?
                      AND (
                          status IN ('pending', 'failed')
                          OR (status = 'running' AND COALESCE(lease_expires_at, 0.0) <= ?)
                      )
                    """,
                    (
                        lease_token,
                        claimed_at + safe_lease,
                        claimed_at,
                        str(row["relation_hash"]),
                        int(row["job_revision"]),
                        claimed_at,
                    ),
                )
                if cursor.rowcount <= 0:
                    continue
                item = dict(row)
                item["lease_token"] = lease_token
                item["lease_expires_at"] = claimed_at + safe_lease
                item["attempt_count"] = int(row["attempt_count"] or 0) + 1
                item["status"] = "running"
                claimed.append(item)
        return claimed

    def count_claimable_relation_graph_projection_jobs(
        self,
        *,
        now: Optional[float] = None,
    ) -> int:
        """返回调用时刻可领取任务数，为单轮聚合建立有界水位。"""

        timestamp = datetime.now().timestamp() if now is None else float(now)
        row = self._conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM relation_graph_projection_jobs
            WHERE status IN ('pending', 'failed')
               OR (status = 'running' AND COALESCE(lease_expires_at, 0.0) <= ?)
            """,
            (timestamp,),
        ).fetchone()
        return int(row["count"] or 0) if row is not None else 0

    def reset_relation_graph_projection_leases(self, *, now: Optional[float] = None) -> int:
        """独占图运行时启动时释放上次进程遗留的执行租约。"""

        reset_at = datetime.now().timestamp() if now is None else float(now)
        with self.transaction(immediate=True) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE relation_graph_projection_jobs
                SET status = 'pending', lease_token = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE status = 'running'
                """,
                (reset_at,),
            )
            return max(0, int(cursor.rowcount))

    def authorize_relation_graph_projection_jobs(
        self,
        jobs: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """按任务版本、租约和当前关系活跃态授权图投影副作用。"""

        claims = {
            str(item.get("relation_hash", "") or "").strip(): item
            for item in jobs
            if str(item.get("relation_hash", "") or "").strip()
        }
        if not claims:
            return []
        authorized: List[Dict[str, Any]] = []
        relation_hashes = sorted(claims)
        for offset in range(0, len(relation_hashes), 900):
            chunk = relation_hashes[offset : offset + 900]
            placeholders = ",".join(["?"] * len(chunk))
            cursor = self._conn.cursor()
            cursor.execute(
                f"""
                SELECT j.relation_hash, j.subject, j.object,
                       j.job_revision, j.status, j.lease_token,
                       r.hash AS current_hash,
                       r.subject AS current_subject, r.object AS current_object,
                       r.is_inactive AS current_is_inactive,
                       r.lifecycle_revision AS current_lifecycle_revision
                FROM relation_graph_projection_jobs j
                LEFT JOIN relations r ON r.hash = j.relation_hash
                WHERE j.relation_hash IN ({placeholders})
                """,
                tuple(chunk),
            )
            for row in cursor.fetchall():
                relation_hash = str(row["relation_hash"])
                claim = claims[relation_hash]
                if (
                    str(row["status"] or "") != "running"
                    or str(row["lease_token"] or "") != str(claim.get("lease_token", "") or "")
                    or int(row["job_revision"] or 0) != int(claim.get("job_revision", 0) or 0)
                ):
                    continue
                current_exists = row["current_hash"] is not None
                authorized.append(
                    {
                        **dict(claim),
                        "subject": (
                            str(row["current_subject"] or "")
                            if current_exists
                            else str(row["subject"] or "")
                        ),
                        "object": (
                            str(row["current_object"] or "")
                            if current_exists
                            else str(row["object"] or "")
                        ),
                        "authoritative_active": current_exists and not bool(row["current_is_inactive"]),
                        "current_lifecycle_revision": (
                            int(row["current_lifecycle_revision"] or 0)
                            if current_exists
                            else None
                        ),
                    }
                )
        return authorized

    def complete_relation_graph_projection_jobs(
        self,
        jobs: Sequence[Dict[str, Any]],
        *,
        completed_at: Optional[float] = None,
    ) -> int:
        """图快照持久化后按租约、任务版本和当前权威态 CAS 完成。"""

        finished_at = datetime.now().timestamp() if completed_at is None else float(completed_at)
        completed = 0
        with self.transaction(immediate=True) as conn:
            cursor = conn.cursor()
            for item in jobs:
                relation_hash = str(item.get("relation_hash", "") or "").strip()
                if not relation_hash:
                    continue
                row = cursor.execute(
                    "SELECT is_inactive FROM relations WHERE hash = ?",
                    (relation_hash,),
                ).fetchone()
                current_active = row is not None and not bool(row["is_inactive"])
                applied_active = bool(item.get("authoritative_active"))
                if current_active != applied_active:
                    cursor.execute(
                        """
                        UPDATE relation_graph_projection_jobs
                        SET status = 'pending', lease_token = NULL,
                            lease_expires_at = NULL, updated_at = ?
                        WHERE relation_hash = ? AND job_revision = ?
                          AND status = 'running' AND lease_token = ?
                        """,
                        (
                            finished_at,
                            relation_hash,
                            int(item.get("job_revision", 0) or 0),
                            str(item.get("lease_token", "") or ""),
                        ),
                    )
                    continue
                cursor.execute(
                    """
                    DELETE FROM relation_graph_projection_jobs
                    WHERE relation_hash = ? AND job_revision = ?
                      AND status = 'running' AND lease_token = ?
                    """,
                    (
                        relation_hash,
                        int(item.get("job_revision", 0) or 0),
                        str(item.get("lease_token", "") or ""),
                    ),
                )
                completed += max(0, int(cursor.rowcount))
        return completed

    def fail_relation_graph_projection_jobs(
        self,
        jobs: Sequence[Dict[str, Any]],
        *,
        error: str,
        failed_at: Optional[float] = None,
    ) -> int:
        """记录当前租约执行失败；更高版本任务不会被旧失败覆盖。"""

        recorded_at = datetime.now().timestamp() if failed_at is None else float(failed_at)
        message = str(error or "关系图投影失败")[:500]
        failed = 0
        with self.transaction(immediate=True) as conn:
            cursor = conn.cursor()
            for item in jobs:
                cursor.execute(
                    """
                    UPDATE relation_graph_projection_jobs
                    SET status = 'failed', lease_token = NULL,
                        lease_expires_at = NULL, last_error = ?, updated_at = ?
                    WHERE relation_hash = ? AND job_revision = ?
                      AND status = 'running' AND lease_token = ?
                    """,
                    (
                        message,
                        recorded_at,
                        str(item.get("relation_hash", "") or ""),
                        int(item.get("job_revision", 0) or 0),
                        str(item.get("lease_token", "") or ""),
                    ),
                )
                failed += max(0, int(cursor.rowcount))
        return failed

    def reenqueue_authoritative_relation_graph_projection_jobs(
        self,
        jobs: Sequence[Dict[str, Any]],
        *,
        enqueued_at: Optional[float] = None,
    ) -> int:
        """CAS 未完成时按当前 metadata 状态重新建立 durable intent。"""

        fallback_by_hash = {
            str(item.get("relation_hash", "") or "").strip(): item
            for item in jobs
            if str(item.get("relation_hash", "") or "").strip()
        }
        if not fallback_by_hash:
            return 0
        timestamp = datetime.now().timestamp() if enqueued_at is None else float(enqueued_at)
        enqueued = 0
        with self.transaction(immediate=True) as conn:
            cursor = conn.cursor()
            for relation_hash, fallback in fallback_by_hash.items():
                relation = cursor.execute(
                    """
                    SELECT subject, object, is_inactive, lifecycle_revision
                    FROM relations
                    WHERE hash = ?
                    """,
                    (relation_hash,),
                ).fetchone()
                if relation is None:
                    subject = str(fallback.get("subject", "") or "").strip()
                    obj = str(fallback.get("object", "") or "").strip()
                    desired_active = 0
                    lifecycle_revision = int(
                        fallback.get("current_lifecycle_revision")
                        or fallback.get("desired_lifecycle_revision")
                        or 0
                    )
                else:
                    subject = str(relation["subject"] or "").strip()
                    obj = str(relation["object"] or "").strip()
                    desired_active = 0 if bool(relation["is_inactive"]) else 1
                    lifecycle_revision = int(relation["lifecycle_revision"] or 0)
                if not subject or not obj:
                    raise RuntimeError(f"关系图投影任务缺少端点: {relation_hash}")
                cursor.execute(
                    """
                    INSERT INTO relation_graph_projection_jobs (
                        relation_hash, subject, object, desired_active,
                        desired_lifecycle_revision, job_revision, status,
                        attempt_count, lease_token, lease_expires_at, last_error,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, 'pending', 0, NULL, NULL, NULL, ?, ?)
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
                    (
                        relation_hash,
                        subject,
                        obj,
                        desired_active,
                        lifecycle_revision,
                        timestamp,
                        timestamp,
                    ),
                )
                enqueued += 1
        return enqueued

    def _relation_lifecycle_state_from_row(self, row: sqlite3.Row) -> RelationLifecycleState:
        anchor_at = row["retention_anchor_at"]
        if anchor_at is None:
            raise RuntimeError(f"关系缺少 retention_anchor_at: {row['hash']}")
        return RelationLifecycleState(
            strength=float(row["retention_strength"]),
            anchor_at=float(anchor_at),
            is_inactive=bool(row["is_inactive"]),
            inactive_since=self._as_optional_float(row["inactive_since"]),
            inactive_reason=str(row["inactive_reason"] or "").strip() or None,
            last_access_reinforced_at=self._as_optional_float(row["last_access_reinforced_at"]),
        )

    def _observe_relation_evidence(
        self,
        hashes: List[str],
        *,
        observed_at: float,
        cursor: sqlite3.Cursor,
    ) -> None:
        """新证据建立关联时重置关系生命周期；调用方负责事务边界。"""
        normalized = list(dict.fromkeys(str(item or "").strip() for item in hashes if str(item or "").strip()))
        if not normalized:
            return
        for index in range(0, len(normalized), 500):
            chunk = normalized[index : index + 500]
            placeholders = ",".join(["?"] * len(chunk))
            cursor.execute(
                f"""
                UPDATE relations
                SET retention_strength = 1.0,
                    retention_anchor_at = MAX(
                        COALESCE(retention_anchor_at, ?),
                        COALESCE(inactive_since, ?),
                        COALESCE(last_accessed, ?),
                        COALESCE(last_reinforced, ?),
                        COALESCE(last_access_reinforced_at, ?),
                        ?
                    ),
                    next_lifecycle_at = MAX(
                        COALESCE(retention_anchor_at, ?),
                        COALESCE(inactive_since, ?),
                        COALESCE(last_accessed, ?),
                        COALESCE(last_reinforced, ?),
                        COALESCE(last_access_reinforced_at, ?),
                        ?
                    ),
                    reinforcement_count = COALESCE(reinforcement_count, 0) + 1,
                    lifecycle_revision = COALESCE(lifecycle_revision, 0) + 1,
                    last_reinforced = MAX(
                        COALESCE(retention_anchor_at, ?),
                        COALESCE(inactive_since, ?),
                        COALESCE(last_accessed, ?),
                        COALESCE(last_reinforced, ?),
                        COALESCE(last_access_reinforced_at, ?),
                        ?
                    ),
                    is_inactive = 0,
                    inactive_since = NULL,
                    inactive_reason = NULL
                WHERE hash IN ({placeholders})
                """,
                (*([float(observed_at)] * 18), *chunk),
            )

    def apply_relation_lifecycle_event(
        self,
        hashes: List[str],
        *,
        event: RelationLifecycleEvent,
        policy: RelationLifecyclePolicy,
        now: float,
        strength: float = 1.0,
    ) -> List[Dict[str, Any]]:
        """在单个元数据事务内应用关系生命周期事件。"""
        normalized = list(dict.fromkeys(str(item or "").strip() for item in hashes if str(item or "").strip()))
        if not normalized:
            return []

        event_value = RelationLifecycleEvent(event)
        placeholders = ",".join(["?"] * len(normalized))
        transitions: List[Dict[str, Any]] = []
        with self.transaction(immediate=True) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT hash, subject, object,
                       retention_strength, retention_anchor_at, next_lifecycle_at,
                       is_inactive, inactive_since, inactive_reason,
                       last_accessed, last_reinforced, last_access_reinforced_at
                FROM relations
                WHERE hash IN ({placeholders})
                ORDER BY hash ASC
                """,
                tuple(normalized),
            )
            rows = cursor.fetchall()
            for row in rows:
                previous = self._relation_lifecycle_state_from_row(row)
                related_times = [float(now), float(previous.anchor_at)]
                for field in (
                    "inactive_since",
                    "last_accessed",
                    "last_reinforced",
                    "last_access_reinforced_at",
                ):
                    if row[field] is not None:
                        related_times.append(float(row[field]))
                event_time = max(related_times)
                decision = apply_lifecycle_event(
                    previous,
                    event_value,
                    now=event_time,
                    policy=policy,
                    strength=float(strength),
                )
                access_increment = 1 if event_value is RelationLifecycleEvent.ACCESS else 0
                reinforced_increment = (
                    1
                    if event_value in {RelationLifecycleEvent.EVIDENCE, RelationLifecycleEvent.REINFORCE}
                    else 0
                )
                cursor.execute(
                    """
                    UPDATE relations
                    SET retention_strength = ?,
                        retention_anchor_at = ?,
                        next_lifecycle_at = ?,
                        is_inactive = ?,
                        inactive_since = ?,
                        inactive_reason = ?,
                        last_accessed = CASE
                            WHEN ? > 0 THEN MAX(COALESCE(last_accessed, ?), ?)
                            ELSE last_accessed
                        END,
                        access_count = COALESCE(access_count, 0) + ?,
                        last_access_reinforced_at = ?,
                        last_reinforced = CASE
                            WHEN ? > 0 THEN MAX(COALESCE(last_reinforced, ?), ?)
                            ELSE last_reinforced
                        END,
                        reinforcement_count = COALESCE(reinforcement_count, 0) + ?,
                        lifecycle_revision = COALESCE(lifecycle_revision, 0) + ?
                    WHERE hash = ?
                    """,
                    (
                        decision.strength,
                        decision.anchor_at,
                        decision.next_lifecycle_at,
                        1 if decision.is_inactive else 0,
                        decision.inactive_since,
                        decision.inactive_reason,
                        access_increment,
                        event_time,
                        event_time,
                        access_increment,
                        decision.last_access_reinforced_at,
                        reinforced_increment,
                        event_time,
                        event_time,
                        reinforced_increment,
                        1 if decision.changed else 0,
                        str(row["hash"]),
                    ),
                )
                transitions.append(
                    {
                        "hash": str(row["hash"]),
                        "subject": str(row["subject"] or ""),
                        "object": str(row["object"] or ""),
                        "was_inactive": previous.is_inactive,
                        "is_inactive": decision.is_inactive,
                        "inactive_reason": decision.inactive_reason,
                        "retention_score": retention_at(
                            RelationLifecycleState(
                                strength=decision.strength,
                                anchor_at=decision.anchor_at,
                                is_inactive=decision.is_inactive,
                                inactive_since=decision.inactive_since,
                                inactive_reason=decision.inactive_reason,
                                last_access_reinforced_at=decision.last_access_reinforced_at,
                            ),
                            now=event_time,
                            policy=policy,
                        ),
                        "retention_anchor_at": decision.anchor_at,
                        "next_lifecycle_at": decision.next_lifecycle_at,
                        "lifecycle_changed": decision.changed,
                    }
                )
        return transitions

    def evaluate_due_relation_lifecycles(
        self,
        *,
        policy: RelationLifecyclePolicy,
        now: float,
        limit: int = 1000,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """计算到期活跃关系并物化冻结状态或下一到期时间。"""
        frozen: List[Dict[str, Any]] = []
        scheduled: List[Dict[str, Any]] = []
        with self.transaction(immediate=True) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT hash, subject, object,
                       retention_strength, retention_anchor_at, next_lifecycle_at,
                       is_inactive, inactive_since, inactive_reason, last_access_reinforced_at
                FROM relations
                WHERE is_inactive = 0
                  AND is_permanent = 0
                  AND is_pinned = 0
                  AND COALESCE(protected_until, 0.0) <= ?
                  AND next_lifecycle_at <= ?
                ORDER BY next_lifecycle_at ASC, hash ASC
                LIMIT ?
                """,
                (float(now), float(now), max(1, int(limit))),
            )
            for row in cursor.fetchall():
                state = self._relation_lifecycle_state_from_row(row)
                decision = evaluate_lifecycle(state, now=float(now), policy=policy)
                cursor.execute(
                    """
                    UPDATE relations
                    SET next_lifecycle_at = ?,
                        is_inactive = ?,
                        inactive_since = ?,
                        inactive_reason = ?,
                        lifecycle_revision = COALESCE(lifecycle_revision, 0) + 1
                    WHERE hash = ?
                    """,
                    (
                        decision.next_lifecycle_at,
                        1 if decision.is_inactive else 0,
                        decision.inactive_since,
                        decision.inactive_reason,
                        str(row["hash"]),
                    ),
                )
                payload = {
                    "hash": str(row["hash"]),
                    "subject": str(row["subject"] or ""),
                    "object": str(row["object"] or ""),
                    "retention_score": retention_at(state, now=float(now), policy=policy),
                    "next_lifecycle_at": decision.next_lifecycle_at,
                }
                if decision.is_inactive:
                    frozen.append(payload)
                else:
                    scheduled.append(payload)
        return {"frozen": frozen, "scheduled": scheduled}

    def get_decay_prune_candidate_rows(
        self,
        *,
        cutoff_time: float,
        now: float,
        policy: RelationLifecyclePolicy,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """返回冻结期已过且当前仍低于阈值的自动衰减关系。"""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT hash, subject, object,
                   retention_strength, retention_anchor_at, next_lifecycle_at,
                   lifecycle_revision, is_inactive, inactive_since, inactive_reason,
                   is_permanent, is_pinned, protected_until, last_access_reinforced_at
            FROM relations
            WHERE is_inactive = 1
              AND inactive_reason = 'decay'
              AND inactive_since < ?
              AND is_permanent = 0
              AND is_pinned = 0
              AND COALESCE(protected_until, 0.0) <= ?
            ORDER BY inactive_since ASC, hash ASC
            LIMIT ?
            """,
            (float(cutoff_time), float(now), max(1, int(limit))),
        )
        candidates: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            state = self._relation_lifecycle_state_from_row(row)
            score = retention_at(state, now=float(now), policy=policy)
            if score > policy.freeze_threshold:
                continue
            candidates.append(
                {
                    "hash": str(row["hash"]),
                    "subject": str(row["subject"] or ""),
                    "object": str(row["object"] or ""),
                    "retention_score": score,
                    "expected_lifecycle_revision": int(row["lifecycle_revision"] or 0),
                    "expected_retention_strength": float(row["retention_strength"]),
                    "expected_retention_anchor_at": float(row["retention_anchor_at"]),
                    "expected_inactive_since": self._as_optional_float(row["inactive_since"]),
                    "expected_inactive_reason": str(row["inactive_reason"] or ""),
                    "expected_is_inactive": bool(row["is_inactive"]),
                    "expected_is_permanent": bool(row["is_permanent"]),
                    "expected_is_pinned": bool(row["is_pinned"]),
                    "expected_protected_until": self._as_optional_float(row["protected_until"]),
                }
            )
        return candidates

    # =========================================================================
    # V5 记忆系统方法
    # =========================================================================

    def get_relation_status_batch(self, hashes: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        批量获取关系状态 (V5)

        Args:
            hashes: 关系哈希列表

        Returns:
            Dict[hash, status_dict]
            status_dict 分开返回证据置信度、生命周期保留状态和保护状态。
        """
        if not hashes:
            return {}

        placeholders = ",".join(["?"] * len(hashes))
        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            SELECT hash, is_inactive, confidence, is_pinned, protected_until, last_reinforced, inactive_since,
                   retention_strength, retention_anchor_at, next_lifecycle_at, reinforcement_count,
                   lifecycle_revision, inactive_reason, last_access_reinforced_at
            FROM relations
            WHERE hash IN ({placeholders})
        """,
            hashes,
        )

        result = {}
        for row in cursor.fetchall():
            result[row["hash"]] = {
                "is_inactive": bool(row["is_inactive"]),
                "confidence": row["confidence"],
                "is_pinned": bool(row["is_pinned"]),
                "protected_until": row["protected_until"],
                "last_reinforced": row["last_reinforced"],
                "inactive_since": row["inactive_since"],
                "retention_strength": row["retention_strength"],
                "retention_anchor_at": row["retention_anchor_at"],
                "next_lifecycle_at": row["next_lifecycle_at"],
                "reinforcement_count": int(row["reinforcement_count"] or 0),
                "lifecycle_revision": int(row["lifecycle_revision"] or 0),
                "inactive_reason": str(row["inactive_reason"] or ""),
                "last_access_reinforced_at": row["last_access_reinforced_at"],
            }
        return result

    def mark_relations_active(self, hashes: List[str]) -> None:
        """批量恢复关系活跃状态，不改变证据置信度。"""
        if not hashes:
            return

        placeholders = ",".join(["?"] * len(hashes))
        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            UPDATE relations
            SET is_inactive = 0,
                inactive_since = NULL,
                inactive_reason = NULL,
                next_lifecycle_at = retention_anchor_at,
                lifecycle_revision = COALESCE(lifecycle_revision, 0) + 1
            WHERE hash IN ({placeholders})
              AND (is_inactive = 1 OR inactive_since IS NOT NULL OR inactive_reason IS NOT NULL)
            """,
            hashes,
        )

        self._conn.commit()

    def update_relations_protection(
        self,
        hashes: List[str],
        protected_until: Optional[float] = None,
        is_pinned: Optional[bool] = None,
        last_reinforced: Optional[float] = None,
    ) -> None:
        """
        批量更新关系保护状态
        """
        if not hashes:
            return

        updates: List[str] = []
        params: List[Any] = []
        changed_conditions: List[str] = []
        changed_params: List[Any] = []

        if protected_until is not None:
            updates.append("protected_until = ?")
            params.append(protected_until)
            changed_conditions.append("protected_until IS NOT ?")
            changed_params.append(protected_until)
        if is_pinned is not None:
            updates.append("is_pinned = ?")
            pinned_value = 1 if is_pinned else 0
            params.append(pinned_value)
            updates.append(
                "next_lifecycle_at = CASE "
                "WHEN ? = 1 THEN NULL "
                "ELSE COALESCE(next_lifecycle_at, retention_anchor_at) END"
            )
            params.append(pinned_value)
            changed_conditions.append("is_pinned IS NOT ?")
            changed_params.append(pinned_value)
            changed_conditions.append("(? = 1 AND next_lifecycle_at IS NOT NULL)")
            changed_params.append(pinned_value)
            changed_conditions.append("(? = 0 AND next_lifecycle_at IS NULL)")
            changed_params.append(pinned_value)
        if last_reinforced is not None:
            updates.append("last_reinforced = ?")
            params.append(last_reinforced)
            changed_conditions.append("last_reinforced IS NOT ?")
            changed_params.append(last_reinforced)

        if not updates:
            return

        updates.append("lifecycle_revision = COALESCE(lifecycle_revision, 0) + 1")
        sql_set = ", ".join(updates)
        placeholders = ",".join(["?"] * len(hashes))

        params.extend(hashes)
        params.extend(changed_params)

        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            UPDATE relations
            SET {sql_set}
            WHERE hash IN ({placeholders})
              AND ({" OR ".join(changed_conditions)})
        """,
            params,
        )
        self._conn.commit()

    def get_prune_candidates(self, cutoff_time: float, limit: int = 1000) -> List[str]:
        """
        获取待修剪候选 (已过冷冻保留期)

        Args:
            cutoff_time: 截止时间 (now - 冷冻时长)
            limit: 限制数量
        """
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT hash FROM relations
            WHERE is_inactive = 1
            AND inactive_since < ?
            LIMIT ?
        """,
            (cutoff_time, limit),
        )
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
            cursor.execute(
                f"""
                INSERT OR REPLACE INTO deleted_relations
                (hash, subject, predicate, object, vector_index, confidence, created_at,
                 vector_state, vector_updated_at, vector_error, vector_retry_count,
                 source_paragraph, metadata, is_permanent, last_accessed, access_count,
                 last_access_reinforced_at,
                 is_inactive, inactive_since, is_pinned, protected_until, last_reinforced,
                 retention_strength, retention_anchor_at, next_lifecycle_at, reinforcement_count, lifecycle_revision,
                 inactive_reason, deleted_at)
                SELECT
                 hash, subject, predicate, object, vector_index, confidence, created_at,
                 vector_state, vector_updated_at, vector_error, vector_retry_count,
                 source_paragraph, metadata, is_permanent, last_accessed, access_count,
                 last_access_reinforced_at,
                 is_inactive, inactive_since, is_pinned, protected_until, last_reinforced,
                 retention_strength, retention_anchor_at, next_lifecycle_at, reinforcement_count, lifecycle_revision,
                 inactive_reason, ?
                FROM relations
                WHERE hash IN ({placeholders})
            """,
                (now, *hashes),
            )

            # 2. 删除 (级联删除会自动处理 paragraph_relations 关联)
            cursor.execute(
                f"""
                DELETE FROM relations
                WHERE hash IN ({placeholders})
            """,
                hashes,
            )

            deleted_count = cursor.rowcount
            self._conn.commit()
            return deleted_count

        except Exception as e:
            logger.error(f"备份删除失败: {e}")
            self._conn.rollback()
            return 0

    @staticmethod
    def _restore_relation_metadata_in_transaction(
        cursor: sqlite3.Cursor,
        hash_value: str,
    ) -> bool:
        """在现有写事务中恢复墓碑，并为恢复结果分配新生命周期世代。"""

        row = cursor.execute(
            "SELECT * FROM deleted_relations WHERE hash = ?",
            (hash_value,),
        ).fetchone()
        if row is None:
            return False

        data = dict(row)
        data.pop("deleted_at", None)
        current_row = cursor.execute(
            "SELECT lifecycle_revision FROM relations WHERE hash = ?",
            (hash_value,),
        ).fetchone()
        current_revision = (
            int(current_row["lifecycle_revision"] or 0)
            if current_row is not None
            else -1
        )
        tombstone_revision = int(data.get("lifecycle_revision", 0) or 0)
        # 墓碑复活必须进入新世代，不能让删除前的 expected_revision 再次命中。
        data["lifecycle_revision"] = max(current_revision, tombstone_revision) + 1

        columns = list(data)
        placeholders = ",".join(["?"] * len(columns))
        update_columns = [column for column in columns if column != "hash"]
        update_sql = ", ".join(
            f"{column} = excluded.{column}" for column in update_columns
        )
        cursor.execute(
            f"""
            INSERT INTO relations ({",".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(hash) DO UPDATE SET {update_sql}
            """,
            [data[column] for column in columns],
        )
        cursor.execute("DELETE FROM deleted_relations WHERE hash = ?", (hash_value,))
        return True

    def restore_relation_metadata(self, hash_value: str) -> Optional[Dict[str, Any]]:
        """
        从回收站恢复关系元数据

        Returns:
            恢复后的关系数据 (字典)，失败返回 None
        """
        try:
            with self.transaction(immediate=True) as connection:
                restored = self._restore_relation_metadata_in_transaction(
                    connection.cursor(),
                    hash_value,
                )
            return self.get_relation(hash_value) if restored else None
        except Exception as e:
            logger.error(f"恢复关系失败: {hash_value} - {e}")
            return None

    def restore_relation(self, hash_value: str) -> Optional[Dict[str, Any]]:
        """兼容旧调用名：恢复关系。"""
        return self.restore_relation_metadata(hash_value)

    def restore_relation_status_from_snapshot(
        self,
        hash_value: str,
        snapshot: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        token = str(hash_value or "").strip()
        if not token or not isinstance(snapshot, dict):
            return None

        with self.transaction(immediate=True) as connection:
            cursor = connection.cursor()
            current = cursor.execute(
                "SELECT lifecycle_revision FROM relations WHERE hash = ?",
                (token,),
            ).fetchone()
            if current is None:
                restored = self._restore_relation_metadata_in_transaction(cursor, token)
                if not restored:
                    return None
                current = cursor.execute(
                    "SELECT lifecycle_revision FROM relations WHERE hash = ?",
                    (token,),
                ).fetchone()
            if current is None:
                raise RuntimeError(f"关系状态恢复后仍不存在: {token}")
            next_revision = max(
                int(current["lifecycle_revision"] or 0),
                int(snapshot.get("lifecycle_revision", 0) or 0),
            ) + 1

            cursor.execute(
                """
                UPDATE relations
                SET is_inactive = ?,
                    confidence = ?,
                    is_pinned = ?,
                    protected_until = ?,
                    last_reinforced = ?,
                    inactive_since = ?,
                    last_access_reinforced_at = ?,
                    retention_strength = ?,
                    retention_anchor_at = ?,
                    next_lifecycle_at = ?,
                    reinforcement_count = ?,
                    lifecycle_revision = ?,
                    inactive_reason = ?
                WHERE hash = ?
                """,
                (
                    1 if bool(snapshot.get("is_inactive")) else 0,
                    float(snapshot.get("confidence", 0.0) or 0.0),
                    1 if bool(snapshot.get("is_pinned")) else 0,
                    self._as_optional_float(snapshot.get("protected_until")),
                    self._as_optional_float(snapshot.get("last_reinforced")),
                    self._as_optional_float(snapshot.get("inactive_since")),
                    self._as_optional_float(snapshot.get("last_access_reinforced_at")),
                    float(snapshot["retention_strength"]),
                    float(snapshot["retention_anchor_at"]),
                    self._as_optional_float(snapshot.get("next_lifecycle_at")),
                    int(snapshot["reinforcement_count"]),
                    next_revision,
                    str(snapshot.get("inactive_reason", "") or "").strip() or None,
                    token,
                ),
            )
        return self.get_relation_status_batch([token]).get(token)

    def get_protected_relations_hashes(self) -> List[str]:
        """获取所有受保护关系的哈希 (Pinned 或 Protected Until > Now)"""
        now = datetime.now().timestamp()

        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT hash FROM relations
            WHERE is_pinned = 1 OR protected_until > ?
        """,
            (now,),
        )

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
                    d["metadata"] = self._decode_metadata(d["metadata"])
                except Exception:
                    d["metadata"] = {}
            data.append(d)
        return data

    def get_deleted_relation(self, hash_value: str) -> Optional[Dict[str, Any]]:
        """获取单条回收站记录"""
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM deleted_relations WHERE hash = ?", (hash_value,))
        row = cursor.fetchone()
        if not row:
            return None

        d = dict(row)
        if "metadata" in d and d["metadata"]:
            try:
                d["metadata"] = self._decode_metadata(d["metadata"])
            except Exception:
                d["metadata"] = {}
        return d

    def reinforce_relations(
        self,
        hashes: List[str],
        *,
        policy: RelationLifecyclePolicy,
        now: float,
        strength: float = 1.0,
    ) -> List[Dict[str, Any]]:
        """按关系生命周期策略执行显式强化。"""
        return self.apply_relation_lifecycle_event(
            hashes,
            event=RelationLifecycleEvent.REINFORCE,
            policy=policy,
            now=now,
            strength=strength,
        )

    def mark_relations_inactive(
        self,
        hashes: List[str],
        inactive_since: Optional[float] = None,
        *,
        reason: str = "correction",
    ) -> None:
        """标记纠错链路淘汰的关系为非活跃。"""
        if not hashes:
            return
        mark_time = inactive_since if inactive_since is not None else datetime.now().timestamp()

        cursor = self._conn.cursor()
        chunk_size = 500
        for i in range(0, len(hashes), chunk_size):
            chunk = hashes[i : i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            sql = f"""
                UPDATE relations
                SET is_inactive = 1,
                    inactive_since = CASE
                        WHEN is_inactive = 1 THEN inactive_since
                        ELSE MAX(
                            COALESCE(retention_anchor_at, ?),
                            COALESCE(last_accessed, ?),
                            COALESCE(last_reinforced, ?),
                            COALESCE(last_access_reinforced_at, ?),
                            ?
                        )
                    END,
                    inactive_reason = ?,
                    next_lifecycle_at = NULL,
                    lifecycle_revision = COALESCE(lifecycle_revision, 0) + 1
                WHERE hash IN ({placeholders})
                  AND (is_inactive = 0 OR inactive_reason IS NOT ?)
            """
            cursor.execute(
                sql,
                [*([float(mark_time)] * 5), str(reason), *chunk, str(reason)],
            )

        self._conn.commit()

    def protect_relations(self, hashes: List[str], is_pinned: bool = False, ttl_seconds: float = 0) -> None:
        """
        设置保护状态
        """
        if not hashes:
            return
        now = datetime.now().timestamp()
        protected_until = (now + ttl_seconds) if ttl_seconds > 0 else 0
        self.update_relations_protection(
            hashes,
            protected_until=protected_until,
            is_pinned=is_pinned,
        )

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

        # 解码 JSON metadata 字段
        if "metadata" in d and d["metadata"]:
            try:
                d["metadata"] = self._decode_metadata(d["metadata"])
            except Exception:
                d["metadata"] = {}

        return d

    @staticmethod
    def _normalize_hash_sequence(hash_values: Sequence[str]) -> List[str]:
        """规范化 hash 列表并保持首次出现顺序。"""
        normalized: List[str] = []
        seen = set()
        for item in hash_values or []:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        return normalized

    @staticmethod
    def _iter_sql_batches(items: Sequence[str], batch_size: int = 900) -> List[List[str]]:
        """按 SQLite 参数数量限制切分批量查询。"""
        safe_batch_size = max(1, int(batch_size))
        return [list(items[index : index + safe_batch_size]) for index in range(0, len(items), safe_batch_size)]

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
    # V5 软删除与垃圾回收
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
            batch = normalized_hashes[i : i + batch_size]
            placeholders = ",".join(["?"] * len(batch))

            # 使用 NOT EXISTS 子查询检查引用
            # 注意: paragraph_entities 中引用的 paragraph 如果被软删了，是否算引用？
            # 这里的语义: 只要有 rows 存在于 paragraph_entities 且该 row 对应的 paragraph 没被彻底物理删除，就算引用。
            # 更严格: ... OR (EXISTS ... AND entity_hash=... AND is_deleted=0)
            # 但 paragraph_entities 表没有 is_deleted 字段(它是关联表). 我们检查关联是否存在。
            # 如果 paragraph 本身 soft deleted, 它的引用应该失效吗？
            # 策略: 只有当 paragraph 也是 active 时，引用才有效。
            # 有效引用条件：JOIN paragraphs p ON pe.paragraph_hash = p.hash WHERE p.is_deleted = 0

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

    def mark_as_deleted(self, hashes: List[str], type_: str, *, reason: str = "") -> int:
        """
        标记为软删除 (Mark Phase)

        Args:
            hashes: Hash 列表
            type_: 'entity' | 'paragraph'
        """
        if not hashes:
            return 0

        table = "entities" if type_ == "entity" else "paragraphs"
        reason_token = str(reason or "").strip()
        if type_ == "paragraph" and not reason_token:
            raise ValueError("软删除段落必须提供明确的 reason")
        count = 0
        with self.transaction(immediate=True):
            now = datetime.now().timestamp()
            touched_sources: List[str] = []
            if type_ == "paragraph":
                touched_sources = self._get_sources_for_paragraph_hashes(hashes, include_deleted=True)

            batch_size = 900
            for i in range(0, len(hashes), batch_size):
                batch = hashes[i : i + batch_size]
                placeholders = ",".join(["?"] * len(batch))

                # 幂等更新: 只更那些 is_deleted=0 的
                cursor = self._conn.cursor()
                if type_ == "paragraph":
                    cursor.execute(
                        f"""
                        UPDATE paragraphs
                        SET is_deleted = 1, deleted_at = ?, deletion_reason = ?
                        WHERE is_deleted = 0 AND hash IN ({placeholders})
                        """,
                        [now, reason_token] + batch,
                    )
                else:
                    cursor.execute(
                        f"""
                        UPDATE {table}
                        SET is_deleted = 1, deleted_at = ?
                        WHERE is_deleted = 0 AND hash IN ({placeholders})
                        """,
                        [now] + batch,
                    )
                changed = cursor.rowcount
                count += changed
                if type_ == "paragraph" and changed > 0:
                    self._delete_paragraph_ngrams_if_ready(
                        batch,
                        count_delta=-changed,
                    )
                    for paragraph_hash in batch:
                        self.fts_delete_tokenized_paragraph(str(paragraph_hash))

            if type_ == "paragraph" and count > 0 and touched_sources:
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
        cursor.execute(
            f"""
            SELECT {cols} FROM {table}
            WHERE is_deleted = 1
            AND deleted_at < ?
        """,
            (cutoff,),
        )

        return [(row[0], row[1]) for row in cursor.fetchall()]

    def physically_delete_entities(self, hashes: List[str]) -> int:
        """物理删除实体 (批量)"""
        if not hashes:
            return 0

        count = 0
        with self.transaction(immediate=True):
            touched_sources: List[str] = []
            batch_size = 900
            for i in range(0, len(hashes), batch_size):
                batch = hashes[i : i + batch_size]
                placeholders = ",".join(["?"] * len(batch))
                cursor = self._conn.cursor()
                cursor.execute(
                    f"""
                    SELECT DISTINCT p.source
                    FROM paragraph_entities pe
                    JOIN paragraphs p ON p.hash = pe.paragraph_hash
                    WHERE pe.entity_hash IN ({placeholders})
                      AND p.source IS NOT NULL AND TRIM(p.source) != ''
                      AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                    """,
                    batch,
                )
                touched_sources.extend(str(row["source"] or "").strip() for row in cursor.fetchall())
                cursor.execute(f"DELETE FROM entities WHERE hash IN ({placeholders})", batch)
                count += cursor.rowcount

            if count > 0 and touched_sources:
                self._enqueue_episode_source_rebuilds(
                    touched_sources,
                    reason="entity_physically_deleted",
                )
        return count

    def physically_delete_paragraphs(self, hashes: List[str]) -> int:
        """物理删除段落 (批量)"""
        if not hashes:
            return 0
        count = 0
        with self.transaction(immediate=True):
            touched_sources = self._get_sources_for_paragraph_hashes(hashes, include_deleted=True)
            self.detach_fact_evidence_for_paragraphs(
                hashes,
                reason="paragraph_physically_deleted",
                conn=self._conn,
            )
            active_delete_count = 0
            batch_size = 900
            for i in range(0, len(hashes), batch_size):
                batch = hashes[i : i + batch_size]
                placeholders = ",".join(["?"] * len(batch))
                cursor = self._conn.cursor()
                cursor.execute(
                    f"""
                    SELECT hash
                    FROM paragraphs
                    WHERE (is_deleted IS NULL OR is_deleted = 0)
                      AND hash IN ({placeholders})
                    """,
                    batch,
                )
                active_delete_count += len(cursor.fetchall())
            self._delete_paragraph_ngrams_if_ready(
                hashes,
                count_delta=-active_delete_count,
            )
            for paragraph_hash in hashes:
                self.fts_delete_tokenized_paragraph(str(paragraph_hash))

            for i in range(0, len(hashes), batch_size):
                batch = hashes[i : i + batch_size]
                placeholders = ",".join(["?"] * len(batch))
                cursor = self._conn.cursor()
                cursor.execute(f"DELETE FROM paragraphs WHERE hash IN ({placeholders})", batch)
                count += cursor.rowcount
            if count > 0:
                self._refresh_paragraph_tokenized_fts_meta(self._conn)

            if count > 0 and touched_sources:
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
        with self.transaction(immediate=True):
            if entity_hashes:
                batch_size = 900
                for i in range(0, len(entity_hashes), batch_size):
                    batch = entity_hashes[i : i + batch_size]
                    placeholders = ",".join(["?"] * len(batch))
                    cursor = self._conn.cursor()
                    cursor.execute(
                        f"""
                        UPDATE entities
                        SET is_deleted = 0, deleted_at = NULL
                        WHERE is_deleted = 1 AND hash IN ({placeholders})
                        """,
                        batch,
                    )
                    count += cursor.rowcount

            if paragraph_hashes:
                touched_sources = self._get_sources_for_paragraph_hashes(paragraph_hashes, include_deleted=True)
                batch_size = 900
                for i in range(0, len(paragraph_hashes), batch_size):
                    batch = paragraph_hashes[i : i + batch_size]
                    placeholders = ",".join(["?"] * len(batch))
                    cursor = self._conn.cursor()
                    cursor.execute(
                        f"""
                        SELECT hash, content
                        FROM paragraphs
                        WHERE is_deleted = 1 AND hash IN ({placeholders})
                        """,
                        batch,
                    )
                    revive_rows = cursor.fetchall()
                    cursor.execute(
                        f"""
                        UPDATE paragraphs
                        SET is_deleted = 0, deleted_at = NULL,
                            expires_at = NULL, deletion_reason = NULL
                        WHERE is_deleted = 1 AND hash IN ({placeholders})
                        """,
                        batch,
                    )
                    changed = cursor.rowcount
                    count += changed
                    if changed > 0:
                        for row in revive_rows:
                            self._upsert_paragraph_ngram_if_ready(
                                str(row["hash"]),
                                str(row["content"] or ""),
                                count_delta=1,
                            )
                            self.fts_upsert_tokenized_paragraph(str(row["hash"]))
                if touched_sources:
                    self._enqueue_episode_source_rebuilds(
                        touched_sources,
                        reason="paragraph_revived",
                    )

        if count > 0:
            logger.info(f"自动复活: {count} 项 (Soft Delete Revived)")

        return count

    def revive_entities_by_names(self, names: List[str]) -> int:
        """
        根据名称复活实体 (Convenience wrapper)
        """
        if not names:
            return 0

        # 使用内部方法计算哈希
        hashes = [compute_hash(self._canonicalize_name(n)) for n in names]
        return self.revive_if_deleted(entity_hashes=hashes)

    def get_entity_status_batch(self, hashes: List[str]) -> Dict[str, Dict[str, Any]]:
        """批量获取实体状态 (WebUI用)"""
        if not hashes:
            return {}

        result = {}
        batch_size = 900
        for i in range(0, len(hashes), batch_size):
            batch = hashes[i : i + batch_size]
            placeholders = ",".join(["?"] * len(batch))

            cursor = self._conn.cursor()
            cursor.execute(
                f"""
                SELECT hash, is_deleted, deleted_at
                FROM entities
                WHERE hash IN ({placeholders})
            """,
                batch,
            )

            for row in cursor.fetchall():
                result[row[0]] = {"is_deleted": bool(row[1]), "deleted_at": row[2]}
        return result

    # =========================================================================
    # Person Profile (问题3) - Switches / Active Set / Snapshots
    # =========================================================================

    # =========================================================================
    # Episode 最小可用实现（MVP）
    # =========================================================================

    def get_deleted_entities(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取已软删除的实体 (回收站用)"""
        if not self.has_table("entities"):
            return []

        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT hash, name, deleted_at
            FROM entities
            WHERE is_deleted = 1
            ORDER BY deleted_at DESC
            LIMIT ?
        """,
            (limit,),
        )

        items = []
        for row in cursor.fetchall():
            items.append(
                {
                    "hash": row[0],
                    "name": row[1],
                    "type": "entity",  # 标记为实体
                    "deleted_at": row[2],
                }
            )
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
