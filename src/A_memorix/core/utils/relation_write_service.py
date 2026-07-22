"""
统一关系写入与关系向量化服务。

规则：
1. 元数据是主数据源，向量是从索引。
2. 关系先写 metadata，再写向量。
3. 向量失败不回滚 metadata，依赖状态机与回填任务修复。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

from src.common.logger import get_logger


logger = get_logger("A_Memorix.RelationWriteService")


@dataclass
class RelationWriteResult:
    hash_value: str
    vector_written: bool
    vector_already_exists: bool
    vector_state: str


@dataclass(frozen=True)
class _RelationVectorRecord:
    hash_value: str
    subject: str
    predicate: str
    obj: str


class RelationWriteService:
    """关系写入收口服务。"""

    ERROR_MAX_LEN = 500

    def __init__(
        self,
        metadata_store: Any,
        graph_store: Any,
        vector_store: Any,
        embedding_manager: Any,
        graph_vector_store: Any = None,
        use_typed_relation_ids: bool = False,
    ):
        self.metadata_store = metadata_store
        self.graph_store = graph_store
        self.vector_store = vector_store
        self.graph_vector_store = graph_vector_store or vector_store
        self.embedding_manager = embedding_manager
        self.use_typed_relation_ids = bool(use_typed_relation_ids)

    @staticmethod
    def build_relation_vector_text(subject: str, predicate: str, obj: str) -> str:
        s = str(subject or "").strip()
        p = str(predicate or "").strip()
        o = str(obj or "").strip()
        # 双表达：兼容关键词检索与自然语言问句
        return f"{s} {p} {o}\n{s}和{o}的关系是{p}"

    @staticmethod
    def relation_vector_id(hash_value: str) -> str:
        return f"relation:{str(hash_value or '').strip()}"

    def _embedding_write_batch_size(self) -> int:
        """按适配器批大小与并发数形成一轮受控写入，限制单批失败范围。"""
        batch_size = max(1, int(getattr(self.embedding_manager, "batch_size", 32)))
        max_concurrent = max(1, int(getattr(self.embedding_manager, "max_concurrent", 1)))
        return min(512, batch_size * max_concurrent)

    async def _ensure_relation_vectors(
        self,
        records: List[_RelationVectorRecord],
        *,
        max_error_len: int = ERROR_MAX_LEN,
        typed_id: bool = False,
    ) -> List[RelationWriteResult]:
        """批量确保关系向量存在，并统一更新关系向量状态。"""
        if not records:
            return []

        target_store = self.graph_vector_store if typed_id else self.vector_store
        vector_ids = {
            record.hash_value: (
                self.relation_vector_id(record.hash_value) if typed_id else record.hash_value
            )
            for record in records
        }
        unique_records = list({record.hash_value: record for record in records}.values())
        existing_hashes = {
            record.hash_value
            for record in unique_records
            if vector_ids[record.hash_value] in target_store
        }
        pending_records = [record for record in unique_records if record.hash_value not in existing_hashes]
        restored_hashes: Set[str] = set()
        if pending_records:
            target_store.restore([vector_ids[record.hash_value] for record in pending_records])
            restored_hashes = {
                record.hash_value
                for record in pending_records
                if vector_ids[record.hash_value] in target_store
            }
            pending_records = [
                record for record in pending_records if record.hash_value not in restored_hashes
            ]

        with self.metadata_store.transaction(immediate=True):
            for record in unique_records:
                state = (
                    "ready"
                    if record.hash_value in existing_hashes or record.hash_value in restored_hashes
                    else "pending"
                )
                self.metadata_store.set_relation_vector_state(record.hash_value, state)

        if not pending_records:
            return [
                RelationWriteResult(
                    hash_value=record.hash_value,
                    vector_written=record.hash_value in restored_hashes,
                    vector_already_exists=record.hash_value in existing_hashes,
                    vector_state="ready",
                )
                for record in records
            ]

        added_hashes: Set[str] = set(restored_hashes)
        failed_errors: Dict[str, str] = {}
        write_batch_size = self._embedding_write_batch_size()
        for offset in range(0, len(pending_records), write_batch_size):
            batch_records = pending_records[offset : offset + write_batch_size]
            try:
                embeddings = np.asarray(
                    await self.embedding_manager.encode_batch(
                        [
                            self.build_relation_vector_text(
                                record.subject,
                                record.predicate,
                                record.obj,
                            )
                            for record in batch_records
                        ]
                    ),
                    dtype=np.float32,
                )
                if embeddings.ndim == 1:
                    embeddings = embeddings.reshape(1, -1)
                if embeddings.shape[0] != len(batch_records):
                    raise ValueError(
                        "关系批量向量数量不匹配: "
                        f"{embeddings.shape[0]} vs {len(batch_records)}"
                    )

                # 编码期间其他任务可能已写入同一关系，写索引前再次排除已存在项。
                records_to_add: List[_RelationVectorRecord] = []
                vectors_to_add: List[np.ndarray] = []
                for record, embedding in zip(batch_records, embeddings, strict=True):
                    if vector_ids[record.hash_value] in target_store:
                        existing_hashes.add(record.hash_value)
                        continue
                    records_to_add.append(record)
                    vectors_to_add.append(embedding)

                if records_to_add:
                    target_store.add(
                        vectors=np.asarray(vectors_to_add, dtype=np.float32),
                        ids=[vector_ids[record.hash_value] for record in records_to_add],
                    )
                    missing_hashes = {
                        record.hash_value
                        for record in records_to_add
                        if vector_ids[record.hash_value] not in target_store
                    }
                    if missing_hashes:
                        raise RuntimeError(
                            f"关系批量向量写入不完整: missing={len(missing_hashes)}"
                        )
                    added_hashes.update(record.hash_value for record in records_to_add)
            except Exception as exc:
                err = str(exc)[:max_error_len]
                failed_errors.update({record.hash_value: err for record in batch_records})

        with self.metadata_store.transaction(immediate=True):
            for record in pending_records:
                error = failed_errors.get(record.hash_value)
                if error is None:
                    self.metadata_store.set_relation_vector_state(record.hash_value, "ready")
                else:
                    self.metadata_store.set_relation_vector_state(
                        record.hash_value,
                        "failed",
                        error=error,
                        bump_retry=True,
                    )

        success_count = len(pending_records) - len(failed_errors)
        if success_count:
            logger.info(
                "metric.relation_vector_write_success=1 "
                f"metric.relation_vector_write_success_count={success_count}"
            )
        if failed_errors:
            logger.warning(
                "metric.relation_vector_write_fail=1 "
                f"metric.relation_vector_write_fail_count={len(failed_errors)} "
                f"err={next(iter(failed_errors.values()))}"
            )
        return [
            RelationWriteResult(
                hash_value=record.hash_value,
                vector_written=record.hash_value in added_hashes,
                vector_already_exists=record.hash_value in existing_hashes,
                vector_state="failed" if record.hash_value in failed_errors else "ready",
            )
            for record in records
        ]

    async def upsert_relations_with_vectors(
        self,
        relations: List[Tuple[str, str, str]],
        confidence: float = 1.0,
        source_paragraph: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        write_vector: bool = True,
    ) -> List[RelationWriteResult]:
        """在一个元数据事务和一个图批次中写入多条关系。"""
        normalized_relations = [
            (str(subject).strip(), str(predicate).strip(), str(obj).strip())
            for subject, predicate, obj in relations
            if str(subject).strip() and str(predicate).strip() and str(obj).strip()
        ]
        if not normalized_relations:
            return []

        with self.metadata_store.transaction(immediate=True), self.graph_store.batch_update():
            relation_hashes = self.metadata_store.add_relations_batch(
                normalized_relations,
                confidence=confidence,
                source_paragraph=source_paragraph,
                metadata=metadata or {},
            )
            statuses = self.metadata_store.get_relation_status_batch(relation_hashes)
            active_edges = [
                ((subject, obj), relation_hash)
                for relation_hash, (subject, _, obj) in zip(
                    relation_hashes,
                    normalized_relations,
                    strict=True,
                )
                if not bool((statuses.get(relation_hash) or {}).get("is_inactive"))
            ]
            if active_edges:
                self.graph_store.add_edges(
                    [edge for edge, _ in active_edges],
                    relation_hashes=[relation_hash for _, relation_hash in active_edges],
                )

        records = [
            _RelationVectorRecord(
                hash_value=relation_hash,
                subject=subject,
                predicate=predicate,
                obj=obj,
            )
            for relation_hash, (subject, predicate, obj) in zip(
                relation_hashes,
                normalized_relations,
                strict=True,
            )
        ]

        if not write_vector:
            return [
                RelationWriteResult(
                    hash_value=record.hash_value,
                    vector_written=False,
                    vector_already_exists=False,
                    vector_state="none",
                )
                for record in records
            ]

        return await self._ensure_relation_vectors(
            records,
            typed_id=self.use_typed_relation_ids,
        )

    async def ensure_relation_vector(
        self,
        hash_value: str,
        subject: str,
        predicate: str,
        obj: str,
        *,
        max_error_len: int = ERROR_MAX_LEN,
        typed_id: bool = False,
        before_vector_write: Optional[Callable[[], None]] = None,
    ) -> RelationWriteResult:
        """
        为已有关系确保向量存在并更新状态。
        """
        vector_id = self.relation_vector_id(hash_value) if typed_id else str(hash_value or "").strip()
        target_store = self.graph_vector_store if typed_id else self.vector_store
        if vector_id in target_store:
            self.metadata_store.set_relation_vector_state(hash_value, "ready")
            return RelationWriteResult(
                hash_value=hash_value,
                vector_written=False,
                vector_already_exists=True,
                vector_state="ready",
            )

        self.metadata_store.set_relation_vector_state(hash_value, "pending")
        try:
            tombstoned = target_store.is_tombstoned(vector_id)
            embedding = None
            if not tombstoned:
                vector_text = self.build_relation_vector_text(subject, predicate, obj)
                embedding = await self.embedding_manager.encode(vector_text)
        except Exception as e:
            err = str(e)[:max_error_len]
            self.metadata_store.set_relation_vector_state(
                hash_value,
                "failed",
                error=err,
                bump_retry=True,
            )
            logger.warning(
                "metric.relation_vector_write_fail=1 "
                "metric.relation_vector_write_fail_count=1 "
                f"hash={hash_value[:16]} "
                f"err={err}"
            )
            return RelationWriteResult(
                hash_value=hash_value,
                vector_written=False,
                vector_already_exists=False,
                vector_state="failed",
            )

        # 授权回调必须在所有 await 结束后、真正变更向量池前同步执行。
        # 回调异常代表上游权威状态已经失效，必须原样抛出，不能伪装成 embedding 失败。
        if before_vector_write is not None:
            before_vector_write()
        try:
            if tombstoned:
                restored = target_store.restore([vector_id])
                if restored != 1 or vector_id not in target_store:
                    raise RuntimeError("关系向量恢复后成员校验失败")
                self.metadata_store.set_relation_vector_state(hash_value, "ready")
                logger.info(
                    "metric.relation_vector_restore_success=1 "
                    f"hash={hash_value[:16]}"
                )
                return RelationWriteResult(
                    hash_value=hash_value,
                    vector_written=True,
                    vector_already_exists=False,
                    vector_state="ready",
                )

            if embedding is None:
                raise RuntimeError("关系向量 embedding 未生成")
            added = int(
                target_store.add(
                    vectors=embedding.reshape(1, -1),
                    ids=[vector_id],
                )
                or 0
            )
            if vector_id not in target_store:
                raise RuntimeError("关系向量写入后成员校验失败")
            self.metadata_store.set_relation_vector_state(hash_value, "ready")
            logger.info(
                "metric.relation_vector_write_success=1 "
                "metric.relation_vector_write_success_count=1 "
                f"hash={hash_value[:16]}"
            )
            return RelationWriteResult(
                hash_value=hash_value,
                vector_written=added > 0,
                vector_already_exists=added == 0,
                vector_state="ready",
            )
        except Exception as e:
            err = str(e)[:max_error_len]
            self.metadata_store.set_relation_vector_state(
                hash_value,
                "failed",
                error=err,
                bump_retry=True,
            )
            logger.warning(
                "metric.relation_vector_write_fail=1 "
                "metric.relation_vector_write_fail_count=1 "
                f"hash={hash_value[:16]} "
                f"err={err}"
            )
            return RelationWriteResult(
                hash_value=hash_value,
                vector_written=False,
                vector_already_exists=False,
                vector_state="failed",
            )

    async def upsert_relation_with_vector(
        self,
        subject: str,
        predicate: str,
        obj: str,
        confidence: float = 1.0,
        source_paragraph: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        write_vector: bool = True,
    ) -> RelationWriteResult:
        """
        统一关系写入：
        1) 写 metadata relation
        2) 写 graph edge relation_hash
        3) 按需写 relation vector
        """
        with self.metadata_store.transaction(immediate=True), self.graph_store.batch_update():
            rel_hash = self.metadata_store.add_relation(
                subject=subject,
                predicate=predicate,
                obj=obj,
                confidence=confidence,
                source_paragraph=source_paragraph,
                metadata=metadata or {},
            )
            status = self.metadata_store.get_relation_status_batch([rel_hash]).get(rel_hash) or {}
            if not bool(status.get("is_inactive")):
                self.graph_store.add_edges([(subject, obj)], relation_hashes=[rel_hash])

        if not write_vector:
            return RelationWriteResult(
                hash_value=rel_hash,
                vector_written=False,
                vector_already_exists=False,
                vector_state="none",
            )

        return await self.ensure_relation_vector(
            hash_value=rel_hash,
            subject=subject,
            predicate=predicate,
            obj=obj,
            typed_id=self.use_typed_relation_ids,
        )
