from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import time

from src.common.logger import get_logger

from ...storage import VectorStore
from ...utils.relation_write_service import RelationWriteService
from ...utils.runtime_payloads import optional_int
from ..search_runtime_initializer import build_search_runtime
from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")


class MemoryVectorRuntimeService(KernelServiceBase):
    def _vector_rebuild_status(self) -> Dict[str, Any]:
        if self.vector_store is not None and not self._vector_rebuild_lock.locked():
            self._stamp_missing_embedding_fingerprint_if_dimension_matches(self.vector_store)
        stored_dimension = self._stored_vector_dimension()
        if self._vector_persist_blocked_until_rebuild and self._vector_rebuild_source_dimension is not None:
            stored_dimension = int(self._vector_rebuild_source_dimension)
        current_dimension = self._current_embedding_status_dimension()
        dimension_rebuild_required = stored_dimension is not None and stored_dimension != current_dimension
        current_fingerprint = self._current_embedding_fingerprint()
        stored_fingerprint = self._stored_embedding_fingerprint()
        fingerprint_status = self._embedding_fingerprint_status(
            current_fingerprint,
            stored_fingerprint,
            has_stored_vectors=stored_dimension is not None,
        )
        fingerprint_rebuild_required = fingerprint_status in {"missing", "mismatched"}
        rebuild_required = dimension_rebuild_required or fingerprint_rebuild_required
        if dimension_rebuild_required:
            message = self._vector_mismatch_error(
                stored_dimension=int(stored_dimension or 0),
                detected_dimension=current_dimension,
            )
        elif fingerprint_status == "mismatched":
            message = "检测到 embedding 模型指纹与现有向量库不一致，请重建向量。"
        elif fingerprint_status == "missing":
            message = "现有向量库缺少 embedding 模型指纹，无法确认模型一致性，建议重建向量。"
        elif fingerprint_status == "unknown":
            message = "当前 embedding 模型指纹不可用，无法确认向量库模型一致性。"
        else:
            message = ""
        return {
            "stored_vector_dimension": int(stored_dimension or 0),
            "embedding_dimension": current_dimension,
            "vector_rebuild_required": bool(rebuild_required),
            "message": message,
            "embedding_fingerprint": current_fingerprint or {},
            "stored_embedding_fingerprint": stored_fingerprint or {},
            "embedding_fingerprint_status": fingerprint_status,
        }

    def _paragraph_vector_backfill_counts(self) -> Dict[str, int]:
        if self.metadata_store is None:
            return {"pending": 0, "running": 0, "failed": 0, "done": 0}
        try:
            return self.metadata_store.get_paragraph_vector_backfill_status_counts()
        except Exception as exc:
            logger.warning(f"读取 paragraph 回填状态失败: {exc}")
            return {"pending": 0, "running": 0, "failed": 0, "done": 0}

    async def _run_paragraph_backfill_once(
        self,
        *,
        limit: Optional[int] = None,
        max_retry: Optional[int] = None,
        trigger: str = "manual",
    ) -> Dict[str, Any]:
        target_store = self._paragraph_store()
        if self.metadata_store is None or target_store is None or self.embedding_manager is None:
            return {"success": False, "processed": 0, "done": 0, "failed": 0, "trigger": trigger}
        if self._is_embedding_degraded():
            return {
                "success": False,
                "processed": 0,
                "done": 0,
                "failed": 0,
                "trigger": trigger,
                "detail": "embedding_degraded",
            }

        safe_limit = max(1, int(limit or self._paragraph_vector_backfill_batch_size()))
        safe_retry = max(1, int(max_retry or self._paragraph_vector_backfill_max_retry()))
        rows = self.metadata_store.fetch_paragraph_vector_backfill_batch(limit=safe_limit, max_retry=safe_retry)
        if not rows:
            return {"success": True, "processed": 0, "done": 0, "failed": 0, "trigger": trigger}

        pending_hashes = [
            str(row.get("paragraph_hash", "") or "").strip()
            for row in rows
            if str(row.get("paragraph_hash", "") or "").strip()
        ]
        if pending_hashes:
            self.metadata_store.mark_paragraph_vector_backfill_running(pending_hashes)

        done_hashes: List[str] = []
        encode_items: List[tuple[str, str]] = []
        paragraph_map = self.metadata_store.get_paragraphs_by_hashes(pending_hashes)
        for paragraph_hash in pending_hashes:
            if paragraph_hash in target_store:
                done_hashes.append(paragraph_hash)
                continue
            paragraph = paragraph_map.get(paragraph_hash)
            if paragraph is None:
                done_hashes.append(paragraph_hash)
                continue
            content = str(paragraph.get("content", "") or "").strip()
            if not content:
                done_hashes.append(paragraph_hash)
                continue
            encode_items.append((paragraph_hash, content))

        done_count, failed_count, last_error, encoded_done_hashes, failed_hashes = await self._encode_and_add_rebuild_vectors(
            items=encode_items,
            batch_size=safe_limit,
            vector_store=target_store,
        )
        del done_count
        done_hashes.extend(encoded_done_hashes)
        for paragraph_hash in failed_hashes:
            self.metadata_store.mark_paragraph_vector_backfill_failed(paragraph_hash, last_error)
        if failed_hashes and self._embedding_fallback_enabled():
            self._set_embedding_degraded(active=True, reason=last_error[:500], checked_at=time.time())

        if done_hashes:
            self.metadata_store.mark_paragraph_vector_backfill_done(done_hashes)
            self._persist()

        return {
            "success": failed_count == 0,
            "processed": len(done_hashes) + failed_count,
            "done": len(done_hashes),
            "failed": failed_count,
            "trigger": trigger,
        }

    def _count_vector_rebuild_targets(self) -> Dict[str, int]:
        if self.metadata_store is None:
            return {"paragraphs": 0, "entities": 0, "relations": 0}
        paragraph_where = self._active_row_filter_sql("paragraphs")
        entity_where = self._active_row_filter_sql("entities")
        relation_where = self._active_row_filter_sql("relations")
        rows = self.metadata_store.query(
            f"""
            SELECT
                (SELECT COUNT(*) FROM paragraphs WHERE {paragraph_where}) AS paragraphs,
                (SELECT COUNT(*) FROM entities WHERE {entity_where}) AS entities,
                (SELECT COUNT(*) FROM relations WHERE {relation_where}) AS relations
            """
        )
        row = rows[0] if rows else {}
        return {
            "paragraphs": int(row.get("paragraphs", 0) or 0),
            "entities": int(row.get("entities", 0) or 0),
            "relations": int(row.get("relations", 0) or 0),
        }

    def _table_has_column(self, table: str, column: str) -> bool:
        if self.metadata_store is None:
            return False
        token = str(table or "").strip()
        col = str(column or "").strip()
        if token not in {"paragraphs", "entities", "relations"} or not col:
            return False
        rows = self.metadata_store.query(f"PRAGMA table_info({token})")
        return any(str(row.get("name", "") or "") == col for row in rows)

    def _active_row_filter_sql(self, table: str) -> str:
        if str(table or "").strip() == "relations" and self._table_has_column("relations", "is_inactive"):
            return "is_inactive IS NULL OR is_inactive = 0"
        return "is_deleted IS NULL OR is_deleted = 0" if self._table_has_column(table, "is_deleted") else "1 = 1"

    async def _backfill_missing_dual_vector_pool_entries(self, *, batch_size: int) -> Dict[str, Any]:
        if (
            self.metadata_store is None
            or self.vector_store is None
            or self.paragraph_vector_store is None
            or self.graph_vector_store is None
            or not self._dual_vector_pools_enabled()
        ):
            return {"success": False, "error": "dual_pool_not_ready"}

        safe_batch_size = max(1, int(batch_size or self._cfg("embedding.batch_size", 32) or 32))
        stats = {
            "paragraphs": {"done": 0, "failed": 0},
            "entities": {"done": 0, "failed": 0},
            "relations": {"done": 0, "failed": 0},
        }
        migration_stats = {
            "paragraphs": {"copied": 0, "encoded": 0, "missing": 0},
            "entities": {"copied": 0, "encoded": 0, "missing": 0},
            "relations": {"copied": 0, "encoded": 0, "missing": 0},
        }
        errors: List[str] = []
        source_store = self.vector_store
        if source_store is not None and not self._stored_vectors_compatible_with_current_embedding(source_store):
            source_store = None
        if source_store is not None and source_store.has_data():
            try:
                source_store.load()
                source_store.warmup_index(force_train=False)
            except Exception as exc:
                logger.warning(f"加载旧单池向量用于双池增量补齐失败，将回退 embedding 重建: {exc}")

        paragraph_where = self._active_row_filter_sql("paragraphs")
        paragraph_rows = self.metadata_store.query(
            f"""
            SELECT hash, content
            FROM paragraphs
            WHERE {paragraph_where}
            ORDER BY created_at ASC
            """
        )
        paragraph_items = [
            (str(row.get("hash", "") or ""), str(row.get("content", "") or "").strip())
            for row in paragraph_rows
            if str(row.get("hash", "") or "").strip()
            and str(row.get("content", "") or "").strip()
            and str(row.get("hash", "") or "").strip() not in self.paragraph_vector_store
        ]
        done, failed, error, _done_ids, _failed_ids, copy_stats = await self._copy_or_encode_dual_rebuild_vectors(
            items=paragraph_items,
            batch_size=safe_batch_size,
            target_store=self.paragraph_vector_store,
            source_store=source_store,
        )
        stats["paragraphs"] = {"done": done, "failed": failed}
        migration_stats["paragraphs"] = copy_stats
        if error:
            errors.append(f"paragraph_pool_backfill:{error}")

        entity_where = self._active_row_filter_sql("entities")
        entity_rows = self.metadata_store.query(
            f"""
            SELECT hash, name
            FROM entities
            WHERE {entity_where}
            ORDER BY created_at ASC
            """
        )
        entity_items = []
        for row in entity_rows:
            hash_value = str(row.get("hash", "") or "").strip()
            name = str(row.get("name", "") or "").strip()
            if not hash_value or not name:
                continue
            if self._graph_vector_id("entity", hash_value) in self.graph_vector_store:
                continue
            entity_items.append((hash_value, name))
        done, failed, error, _done_ids, _failed_ids, copy_stats = await self._copy_or_encode_dual_rebuild_vectors(
            items=entity_items,
            batch_size=safe_batch_size,
            target_store=self.graph_vector_store,
            target_id_prefix="entity",
            source_store=source_store,
        )
        stats["entities"] = {"done": done, "failed": failed}
        migration_stats["entities"] = copy_stats
        if error:
            errors.append(f"entity_graph_pool_backfill:{error}")

        if self.relation_vectors_enabled:
            relation_where = self._active_row_filter_sql("relations")
            relation_rows = self.metadata_store.query(
                f"""
                SELECT hash, subject, predicate, object
                FROM relations
                WHERE {relation_where}
                ORDER BY created_at ASC
                """
            )
            relation_items = []
            for row in relation_rows:
                hash_value = str(row.get("hash", "") or "").strip()
                if not hash_value:
                    continue
                if self._graph_vector_id("relation", hash_value) in self.graph_vector_store:
                    continue
                relation_items.append(
                    (
                        hash_value,
                        RelationWriteService.build_relation_vector_text(
                            str(row.get("subject", "") or ""),
                            str(row.get("predicate", "") or ""),
                            str(row.get("object", "") or ""),
                        ),
                    )
                )
            done, failed, error, done_ids, failed_ids, copy_stats = await self._copy_or_encode_dual_rebuild_vectors(
                items=relation_items,
                batch_size=safe_batch_size,
                target_store=self.graph_vector_store,
                target_id_prefix="relation",
                source_store=source_store,
            )
            stats["relations"] = {"done": done, "failed": failed}
            migration_stats["relations"] = copy_stats
            if error:
                errors.append(f"relation_graph_pool_backfill:{error}")

            if done_ids or failed_ids:
                conn = self.metadata_store.get_connection()
                cursor = conn.cursor()
                now_ts = time.time()
                for start in range(0, len(done_ids), 500):
                    batch_ids = done_ids[start : start + 500]
                    if not batch_ids:
                        continue
                    placeholders = ",".join("?" for _ in batch_ids)
                    cursor.execute(
                        f"""
                        UPDATE relations
                        SET vector_state = 'ready',
                            vector_updated_at = ?,
                            vector_error = NULL
                        WHERE hash IN ({placeholders})
                        """,
                        (now_ts, *batch_ids),
                    )
                for start in range(0, len(failed_ids), 500):
                    batch_ids = failed_ids[start : start + 500]
                    if not batch_ids:
                        continue
                    placeholders = ",".join("?" for _ in batch_ids)
                    cursor.execute(
                        f"""
                        UPDATE relations
                        SET vector_state = 'failed',
                            vector_updated_at = ?,
                            vector_error = ?,
                            vector_retry_count = COALESCE(vector_retry_count, 0) + 1
                        WHERE hash IN ({placeholders})
                        """,
                        (now_ts, (error or "dual_pool_backfill_failed")[:500], *batch_ids),
                    )
                conn.commit()

        failed_total = sum(int(item["failed"]) for item in stats.values())
        if failed_total:
            self._set_embedding_degraded(
                active=True,
                reason="; ".join(errors)[:500] or "dual_pool_backfill_failed",
                checked_at=time.time(),
            )
        if self.paragraph_vector_store is not None:
            self._save_vector_store(self.paragraph_vector_store)
        if self.graph_vector_store is not None:
            self._save_vector_store(self.graph_vector_store)
        self._refresh_dual_vector_ready_manifest_from_stores()
        return {
            "success": failed_total == 0,
            "stats": stats,
            "migration": migration_stats,
            "failed": int(failed_total),
            "errors": errors[:5],
        }

    async def _encode_and_add_rebuild_vectors(
        self,
        *,
        items: Sequence[tuple[str, str]],
        batch_size: int,
        vector_store: Optional[VectorStore] = None,
    ) -> tuple[int, int, str, List[str], List[str]]:
        target_store = vector_store or self.vector_store
        if target_store is None or self.embedding_manager is None:
            failed_ids = [item_id for item_id, _ in items]
            return 0, len(items), "vector_runtime_components_missing", [], failed_ids

        done = 0
        failed = 0
        last_error = ""
        done_ids: List[str] = []
        failed_ids: List[str] = []
        safe_batch_size = max(1, int(batch_size))
        for start in range(0, len(items), safe_batch_size):
            batch = list(items[start : start + safe_batch_size])
            ids = [item_id for item_id, _ in batch]
            texts = [text for _, text in batch]
            try:
                encoder = getattr(self.embedding_manager, "encode_batch", None)
                if callable(encoder):
                    embeddings = await encoder(texts, batch_size=safe_batch_size)
                else:
                    embeddings = await self.embedding_manager.encode(texts)
                embedding_array = np.asarray(embeddings, dtype=np.float32)
                if embedding_array.ndim == 1:
                    embedding_array = embedding_array.reshape(1, -1)
                if embedding_array.shape[0] != len(ids):
                    raise ValueError(f"embedding 返回数量异常: expected={len(ids)}, got={embedding_array.shape[0]}")
                target_store.add(vectors=embedding_array, ids=ids)
                done += len(ids)
                done_ids.extend(ids)
            except Exception as exc:
                last_error = str(exc)[:500]
                failed += len(ids)
                failed_ids.extend(ids)
                logger.warning(f"重建向量批次失败: start={start}, count={len(ids)}, error={last_error}")
        return done, failed, last_error, done_ids, failed_ids

    def _copy_rebuild_vectors_from_store(
        self,
        *,
        source_store: Optional[VectorStore],
        target_store: Optional[VectorStore],
        id_pairs: Sequence[tuple[str, str]],
        batch_size: int = 1024,
    ) -> tuple[int, List[str], List[tuple[str, str]]]:
        if source_store is None or target_store is None or not id_pairs:
            return 0, [], list(id_pairs)

        pair_by_source = {source_id: target_id for source_id, target_id in id_pairs}
        source_ids = list(pair_by_source.keys())
        iterator = getattr(source_store, "iter_vectors_by_ids", None)
        getter = getattr(source_store, "get_vectors", None)
        if not callable(iterator) and not callable(getter):
            return 0, [], list(id_pairs)

        try:
            if callable(iterator):
                vector_batches = iterator(source_ids, batch_size=max(1, int(batch_size or 1024)))
            else:
                vector_batches = [getter(source_ids)]
        except Exception as exc:
            logger.warning(f"读取旧向量失败，将回退 embedding 重建: {exc}")
            return 0, [], list(id_pairs)

        copied_source_ids: List[str] = []
        copied_set: set[str] = set()
        try:
            for source_vectors in vector_batches:
                if not isinstance(source_vectors, dict) or not source_vectors:
                    continue
                target_ids: List[str] = []
                vectors: List[np.ndarray] = []
                for source_id, vector in source_vectors.items():
                    target_id = pair_by_source.get(source_id)
                    if target_id is None or source_id in copied_set:
                        continue
                    target_ids.append(target_id)
                    vectors.append(np.asarray(vector, dtype=np.float32))
                    copied_source_ids.append(source_id)
                    copied_set.add(source_id)
                if not target_ids:
                    continue
                vector_array = np.asarray(vectors, dtype=np.float32)
                if vector_array.ndim == 1:
                    vector_array = vector_array.reshape(1, -1)
                added = int(target_store.add(vectors=vector_array, ids=target_ids) or 0)
                if added < len(target_ids):
                    logger.debug(f"复制旧向量到新池时存在已写入项: requested={len(target_ids)} added={added}")
        except Exception as exc:
            logger.warning(f"复制旧向量到新池失败，将回退 embedding 重建: {exc}")
            return 0, [], list(id_pairs)

        missing_pairs = [(source_id, target_id) for source_id, target_id in id_pairs if source_id not in copied_set]
        return len(copied_source_ids), copied_source_ids, missing_pairs

    async def _copy_or_encode_dual_rebuild_vectors(
        self,
        *,
        items: Sequence[tuple[str, str]],
        batch_size: int,
        target_store: Optional[VectorStore],
        target_id_prefix: str = "",
        source_store: Optional[VectorStore] = None,
    ) -> tuple[int, int, str, List[str], List[str], Dict[str, int]]:
        id_pairs = [
            (
                str(item_id or "").strip(),
                f"{target_id_prefix}:{str(item_id or '').strip()}" if target_id_prefix else str(item_id or "").strip(),
            )
            for item_id, _text in items
            if str(item_id or "").strip()
        ]
        copied, copied_source_ids, missing_pairs = self._copy_rebuild_vectors_from_store(
            source_store=source_store,
            target_store=target_store,
            id_pairs=id_pairs,
            batch_size=batch_size,
        )
        missing_source_ids = {source_id for source_id, _target_id in missing_pairs}
        text_by_id = {str(item_id or "").strip(): text for item_id, text in items}
        encode_items = [
            (
                target_id,
                text_by_id.get(source_id, ""),
            )
            for source_id, target_id in missing_pairs
            if str(text_by_id.get(source_id, "") or "").strip()
        ]
        done, failed, error, encoded_done_ids, encoded_failed_ids = await self._encode_and_add_rebuild_vectors(
            items=encode_items,
            batch_size=batch_size,
            vector_store=target_store,
        )

        def _source_id(target_id: str) -> str:
            if target_id_prefix and target_id.startswith(f"{target_id_prefix}:"):
                return target_id.split(":", 1)[1]
            return target_id

        done_source_ids = copied_source_ids + [_source_id(item_id) for item_id in encoded_done_ids]
        failed_source_ids = [_source_id(item_id) for item_id in encoded_failed_ids]
        skipped_missing = len(missing_source_ids) - len(encode_items)
        failed += max(0, skipped_missing)
        return (
            copied + done,
            failed,
            error,
            done_source_ids,
            failed_source_ids,
            {
                "copied": copied,
                "encoded": done,
                "missing": len(missing_pairs),
            },
        )

    async def _rebuild_all_vectors(
        self,
        *,
        batch_size: Optional[int] = None,
        include_relations: Optional[bool] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        if self._vector_rebuild_lock.locked():
            return {
                "success": False,
                "error": "vector_rebuild_running",
                "detail": "已有向量重建任务正在运行",
            }
        async with self._vector_rebuild_lock:
            return await self._rebuild_all_vectors_locked(
                batch_size=batch_size,
                include_relations=include_relations,
                dry_run=dry_run,
            )

    async def _rebuild_all_vectors_locked(
        self,
        *,
        batch_size: Optional[int] = None,
        include_relations: Optional[bool] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """在持有向量重建锁时重建全部有效对象的向量。

        单池模式会原地清理并重建；双池模式先写入独立构建目录，只有编码无失败且
        向量数量校验通过后才切换目录和 ready manifest。重建期间 embedding 保持
        降级状态，完成后重新组装检索运行时并通过自检决定是否解除持久化阻断。
        ``dry_run`` 只统计目标数量，不修改运行时和磁盘状态。
        """
        if self.metadata_store is None or self.vector_store is None or self.embedding_manager is None:
            return {"success": False, "error": "runtime_components_missing"}

        target_counts = self._count_vector_rebuild_targets()
        relation_enabled = bool(self.relation_vectors_enabled if include_relations is None else include_relations)
        if not relation_enabled:
            target_counts["relations"] = 0
        total = target_counts["paragraphs"] + target_counts["entities"] + target_counts["relations"]
        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "counts": target_counts,
                "total": int(total),
                **self._vector_rebuild_status(),
            }

        started = time.time()
        safe_batch_size = max(1, int(batch_size or self._cfg("embedding.batch_size", 32) or 32))
        detected_dimension = await self._detect_current_embedding_dimension_for_rebuild()
        if detected_dimension > 0:
            self.embedding_dimension = int(detected_dimension)
        self._set_embedding_degraded(
            active=True,
            reason="正在重建全部向量，检索临时降级",
            checked_at=started,
        )

        dual_mode = self._dual_vector_pools_config_enabled()
        legacy_source_store = self.vector_store if dual_mode else None
        self._update_dual_vector_auto_migration_stage(
            "prepare_rebuild",
            dual_mode=dual_mode,
            total=int(total),
            counts=dict(target_counts),
            legacy_source_available=legacy_source_store is not None,
        )
        if legacy_source_store is not None and not self._stored_vectors_compatible_with_current_embedding(
            legacy_source_store
        ):
            legacy_source_store = None
            self._update_dual_vector_auto_migration_stage("legacy_source_incompatible")
        dual_build_root: Optional[Path] = None
        build_paragraph_vector_store: Optional[VectorStore] = None
        build_graph_vector_store: Optional[VectorStore] = None
        if dual_mode and legacy_source_store is not None and legacy_source_store.has_data():
            try:
                self._update_dual_vector_auto_migration_stage("legacy_source_load")
                legacy_source_store.load()
                self._update_dual_vector_auto_migration_stage("legacy_source_warmup")
                legacy_source_store.warmup_index(force_train=False)
                self._update_dual_vector_auto_migration_stage("legacy_source_ready")
            except Exception as exc:
                logger.warning(f"加载旧单池向量用于双池迁移失败，将回退 embedding 重建: {exc}")
        if not dual_mode:
            self._dual_vector_pools_ready = False
            self._remove_dual_vector_ready_manifest()
            self.vector_store = self._make_vector_store(self._vectors_root())
            self.vector_store.clear()
            self.paragraph_vector_store = self._make_vector_store(self._paragraph_vector_dir())
            self.graph_vector_store = self._make_vector_store(self._graph_vector_dir())
            self._refresh_relation_write_service()
        else:
            dual_build_root, paragraph_data_dir, graph_data_dir = self._prepare_dual_vector_build_dirs()
            build_paragraph_vector_store = self._make_vector_store(paragraph_data_dir)
            build_graph_vector_store = self._make_vector_store(graph_data_dir)
        stats = {
            "paragraphs": {"done": 0, "failed": 0},
            "entities": {"done": 0, "failed": 0},
            "relations": {"done": 0, "failed": 0},
        }
        migration_stats = {
            "paragraphs": {"copied": 0, "encoded": 0, "missing": 0},
            "entities": {"copied": 0, "encoded": 0, "missing": 0},
            "relations": {"copied": 0, "encoded": 0, "missing": 0},
        }
        errors: List[str] = []
        paragraph_where = self._active_row_filter_sql("paragraphs")
        entity_where = self._active_row_filter_sql("entities")
        relation_where = self._active_row_filter_sql("relations")

        paragraph_rows = self.metadata_store.query(
            f"""
            SELECT hash, content
            FROM paragraphs
            WHERE {paragraph_where}
            ORDER BY created_at ASC
            """
        )
        paragraph_items = [
            (str(row.get("hash", "") or ""), str(row.get("content", "") or "").strip())
            for row in paragraph_rows
            if str(row.get("hash", "") or "").strip() and str(row.get("content", "") or "").strip()
        ]
        self._update_dual_vector_auto_migration_stage("paragraphs_start", paragraph_items=len(paragraph_items))
        if dual_mode:
            done, failed, error, _done_ids, _failed_ids, copy_stats = await self._copy_or_encode_dual_rebuild_vectors(
                items=paragraph_items,
                batch_size=safe_batch_size,
                target_store=build_paragraph_vector_store,
                source_store=legacy_source_store,
            )
            migration_stats["paragraphs"] = copy_stats
            if error:
                errors.append(f"paragraph_pool:{error}")
        else:
            done, failed, error, _done_ids, _failed_ids = await self._encode_and_add_rebuild_vectors(
                items=paragraph_items,
                batch_size=safe_batch_size,
            )
            if error:
                errors.append(error)
        stats["paragraphs"] = {"done": done, "failed": failed}
        self._update_dual_vector_auto_migration_stage(
            "paragraphs_done",
            paragraph_done=done,
            paragraph_failed=failed,
            paragraph_migration=dict(migration_stats.get("paragraphs") or {}),
        )

        entity_rows = self.metadata_store.query(
            f"""
            SELECT hash, name
            FROM entities
            WHERE {entity_where}
            ORDER BY created_at ASC
            """
        )
        entity_items = [
            (str(row.get("hash", "") or ""), str(row.get("name", "") or "").strip())
            for row in entity_rows
            if str(row.get("hash", "") or "").strip() and str(row.get("name", "") or "").strip()
        ]
        self._update_dual_vector_auto_migration_stage("entities_start", entity_items=len(entity_items))
        if dual_mode:
            done, failed, error, _done_ids, _failed_ids, copy_stats = await self._copy_or_encode_dual_rebuild_vectors(
                items=entity_items,
                batch_size=safe_batch_size,
                target_store=build_graph_vector_store,
                target_id_prefix="entity",
                source_store=legacy_source_store,
            )
            migration_stats["entities"] = copy_stats
            if error:
                errors.append(f"entity_graph_pool:{error}")
        else:
            done, failed, error, _done_ids, _failed_ids = await self._encode_and_add_rebuild_vectors(
                items=entity_items,
                batch_size=safe_batch_size,
            )
            if error:
                errors.append(error)
        stats["entities"] = {"done": done, "failed": failed}
        self._update_dual_vector_auto_migration_stage(
            "entities_done",
            entity_done=done,
            entity_failed=failed,
            entity_migration=dict(migration_stats.get("entities") or {}),
        )

        if relation_enabled:
            relation_rows = self.metadata_store.query(
                f"""
                SELECT hash, subject, predicate, object
                FROM relations
                WHERE {relation_where}
                ORDER BY created_at ASC
                """
            )
            relation_items = [
                (
                    str(row.get("hash", "") or ""),
                    RelationWriteService.build_relation_vector_text(
                        str(row.get("subject", "") or ""),
                        str(row.get("predicate", "") or ""),
                        str(row.get("object", "") or ""),
                    ),
                )
                for row in relation_rows
                if str(row.get("hash", "") or "").strip()
            ]
            self._update_dual_vector_auto_migration_stage("relations_start", relation_items=len(relation_items))
            if dual_mode:
                done, failed, error, done_ids, failed_ids, copy_stats = await self._copy_or_encode_dual_rebuild_vectors(
                    items=relation_items,
                    batch_size=safe_batch_size,
                    target_store=build_graph_vector_store,
                    target_id_prefix="relation",
                    source_store=legacy_source_store,
                )
                migration_stats["relations"] = copy_stats
                if error:
                    errors.append(f"relation_graph_pool:{error}")
            else:
                done, failed, error, done_ids, failed_ids = await self._encode_and_add_rebuild_vectors(
                    items=relation_items,
                    batch_size=safe_batch_size,
                )
                if error:
                    errors.append(error)
            stats["relations"] = {"done": done, "failed": failed}
            self._update_dual_vector_auto_migration_stage(
                "relations_done",
                relation_done=done,
                relation_failed=failed,
                relation_migration=dict(migration_stats.get("relations") or {}),
            )

            conn = self.metadata_store.get_connection()
            cursor = conn.cursor()
            now_ts = time.time()
            for start in range(0, len(done_ids), 500):
                batch_ids = done_ids[start : start + 500]
                if not batch_ids:
                    continue
                placeholders = ",".join("?" for _ in batch_ids)
                cursor.execute(
                    f"""
                    UPDATE relations
                    SET vector_state = 'ready',
                        vector_updated_at = ?,
                        vector_error = NULL
                    WHERE hash IN ({placeholders})
                    """,
                    (now_ts, *batch_ids),
                )
            for start in range(0, len(failed_ids), 500):
                batch_ids = failed_ids[start : start + 500]
                if not batch_ids:
                    continue
                placeholders = ",".join("?" for _ in batch_ids)
                cursor.execute(
                    f"""
                    UPDATE relations
                    SET vector_state = 'failed',
                        vector_updated_at = ?,
                        vector_error = ?
                    WHERE hash IN ({placeholders})
                    """,
                    (now_ts, error[:500], *batch_ids),
                )
            conn.commit()

        done_total = sum(int(item["done"]) for item in stats.values())
        failed_total = sum(int(item["failed"]) for item in stats.values())
        activation_ok = True
        if dual_mode:
            expected_paragraph_vectors = int(stats["paragraphs"]["done"])
            expected_graph_vectors = int(stats["entities"]["done"]) + int(stats["relations"]["done"])
            actual_paragraph_vectors = (
                int(build_paragraph_vector_store.num_vectors) if build_paragraph_vector_store else 0
            )
            actual_graph_vectors = int(build_graph_vector_store.num_vectors) if build_graph_vector_store else 0
            self._update_dual_vector_auto_migration_stage(
                "activation_check",
                stats=dict(stats),
                migration=dict(migration_stats),
                actual_paragraph_vectors=actual_paragraph_vectors,
                expected_paragraph_vectors=expected_paragraph_vectors,
                actual_graph_vectors=actual_graph_vectors,
                expected_graph_vectors=expected_graph_vectors,
            )
            if (
                failed_total == 0
                and actual_paragraph_vectors == expected_paragraph_vectors
                and actual_graph_vectors == expected_graph_vectors
            ):
                try:
                    if build_paragraph_vector_store is not None:
                        self._update_dual_vector_auto_migration_stage("paragraph_pool_warmup")
                        build_paragraph_vector_store.warmup_index(force_train=True)
                        self._update_dual_vector_auto_migration_stage("paragraph_pool_save")
                        self._save_vector_store(build_paragraph_vector_store)
                    if build_graph_vector_store is not None:
                        self._update_dual_vector_auto_migration_stage("graph_pool_warmup")
                        build_graph_vector_store.warmup_index(force_train=True)
                        self._update_dual_vector_auto_migration_stage("graph_pool_save")
                        self._save_vector_store(build_graph_vector_store)
                    self._update_dual_vector_auto_migration_stage("activate_dirs")
                    self._activate_dual_vector_build_dirs(dual_build_root)
                    self._update_dual_vector_auto_migration_stage("write_manifest")
                    self._write_dual_vector_ready_manifest(stats=stats, migration_stats=migration_stats)
                    self._cleanup_stale_dual_vector_build_dirs()
                    self._update_dual_vector_auto_migration_stage("reload_dual_stores")
                    activation_ok = self._reload_dual_vector_stores_from_disk()
                    if not activation_ok:
                        errors.append("dual_pool_activation:ready_manifest_unusable")
                    else:
                        self._update_dual_vector_auto_migration_stage("dual_backfill")
                        backfill_result = await self._backfill_missing_dual_vector_pool_entries(
                            batch_size=safe_batch_size,
                        )
                        self._update_dual_vector_auto_migration_stage("dual_backfill_done", backfill=backfill_result)
                        if not bool(backfill_result.get("success", False)):
                            for item in backfill_result.get("errors", []) or []:
                                errors.append(str(item))
                        self._update_dual_vector_auto_migration_stage("clear_legacy_single_pool")
                        self._clear_legacy_single_vector_files_after_dual_ready()
                except Exception as exc:
                    activation_ok = False
                    self._dual_vector_pools_ready = False
                    errors.append(f"dual_pool_activation:{str(exc)[:500]}")
                    logger.warning(f"双池临时构建目录切换失败，保留原有向量池: {exc}")
                    self._drop_dual_build_root(dual_build_root)
                    self._reload_dual_vector_stores_from_disk()
            else:
                activation_ok = False
                if failed_total == 0:
                    errors.append(
                        "dual_pool_activation:vector_count_mismatch "
                        f"paragraph={actual_paragraph_vectors}/{expected_paragraph_vectors}, "
                        f"graph={actual_graph_vectors}/{expected_graph_vectors}"
                    )
                self._drop_dual_build_root(dual_build_root)
                self._reload_dual_vector_stores_from_disk()
            self._refresh_relation_write_service()
        else:
            self._update_dual_vector_auto_migration_stage("single_pool_warmup")
            self.vector_store.warmup_index(force_train=True)
            self.paragraph_vector_store = self._make_vector_store(self._paragraph_vector_dir())
            self.graph_vector_store = self._make_vector_store(self._graph_vector_dir())
            self._refresh_relation_write_service()
        self._update_dual_vector_auto_migration_stage("runtime_rebuild")
        self._runtime_bundle = build_search_runtime(
            plugin_config=self._build_runtime_config(),
            logger_obj=logger,
            owner_tag="sdk_kernel",
            log_prefix="[sdk]",
        )
        if self._runtime_bundle.ready:
            self.retriever = self._runtime_bundle.retriever
            self.threshold_filter = self._runtime_bundle.threshold_filter
            self.sparse_index = self._runtime_bundle.sparse_index or self.sparse_index
            self._refresh_runtime_dependents(preserve_managers=True)
            self._apply_runtime_sparse_mode()

        self._update_dual_vector_auto_migration_stage("self_check")
        report = await self._refresh_runtime_self_check(sample_text="A_Memorix vector rebuild self check")
        if bool(report.get("ok", False)) and not errors:
            self._set_embedding_degraded(active=False, checked_at=float(report.get("checked_at") or time.time()))
        else:
            self._set_embedding_degraded(
                active=True,
                reason=str(report.get("message") or "; ".join(errors) or "vector_rebuild_incomplete")[:500],
                checked_at=float(report.get("checked_at") or time.time()),
            )

        elapsed_ms = (time.time() - started) * 1000.0
        rebuild_success = failed_total == 0 and bool(report.get("ok", False)) and (not dual_mode or activation_ok)
        if rebuild_success:
            self._vector_persist_blocked_until_rebuild = False
            self._vector_rebuild_source_dimension = None
        self._update_dual_vector_auto_migration_stage("persist", rebuild_success=rebuild_success, errors=list(errors[:5]))
        self._persist(force_vectors=rebuild_success)
        return {
            "success": rebuild_success,
            "dry_run": False,
            "counts": target_counts,
            "stats": stats,
            "migration": migration_stats,
            "total": int(total),
            "done": int(done_total),
            "failed": int(failed_total),
            "errors": errors[:5],
            "elapsed_ms": elapsed_ms,
            "self_check": report,
            **self._vector_rebuild_status(),
        }

    async def _detect_current_embedding_dimension_for_rebuild(self) -> int:
        if self.embedding_manager is None:
            raise RuntimeError("embedding_manager_missing")
        detector = getattr(self.embedding_manager, "_detect_dimension", None)
        if not callable(detector):
            return max(1, int(self._cfg("embedding.dimension", self.embedding_dimension) or self.embedding_dimension))
        detected_dimension = int(await detector())
        if detected_dimension <= 0:
            raise ValueError(f"embedding 维度检测结果非法: {detected_dimension}")
        return detected_dimension

    @staticmethod
    def _vector_store_snapshot(store: Optional[VectorStore]) -> Dict[str, Any]:
        if store is None:
            return {
                "available": False,
                "dimension": 0,
                "num_vectors": 0,
                "has_data": False,
            }
        has_data = False
        try:
            has_data = bool(store.has_data())
        except Exception:
            has_data = False
        return {
            "available": True,
            "dimension": int(getattr(store, "dimension", 0) or 0),
            "num_vectors": int(getattr(store, "num_vectors", 0) or 0),
            "has_data": has_data,
        }

    def _vector_pools_status(self) -> Dict[str, Any]:
        configured_mode = self._vector_pool_mode()
        ready = self._dual_vector_pools_enabled()
        return {
            "configured_mode": configured_mode,
            "effective_mode": "dual" if configured_mode == "dual" and ready else "single",
            "ready": ready,
            "single_pool": self._vector_store_snapshot(self.vector_store),
            "paragraph_pool": self._vector_store_snapshot(self.paragraph_vector_store),
            "graph_pool": self._vector_store_snapshot(self.graph_vector_store),
            "ready_manifest": str(self._dual_vector_ready_manifest_path()),
            "auto_migration": dict(self._dual_vector_auto_migration_status),
        }

    async def memory_runtime_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        act = str(action or "").strip().lower()

        if act == "save":
            self._persist()
            return {"success": True, "saved": True, "data_dir": str(self.data_dir)}

        if act == "get_config":
            degraded = self._embedding_degraded_snapshot()
            backfill_counts = self._paragraph_vector_backfill_counts()
            rebuild_status = self._vector_rebuild_status()
            vector_pools_status = self._vector_pools_status()
            return {
                "success": True,
                "config": self.config,
                "data_dir": str(self.data_dir),
                "embedding_dimension": int(rebuild_status["embedding_dimension"]),
                "stored_vector_dimension": int(rebuild_status["stored_vector_dimension"]),
                "vector_rebuild_required": bool(rebuild_status["vector_rebuild_required"]),
                "vector_rebuild_message": str(rebuild_status["message"]),
                "embedding_fingerprint": rebuild_status.get("embedding_fingerprint") or {},
                "stored_embedding_fingerprint": rebuild_status.get("stored_embedding_fingerprint") or {},
                "embedding_fingerprint_status": str(rebuild_status.get("embedding_fingerprint_status") or "unknown"),
                "auto_save": bool(self._cfg("advanced.enable_auto_save", True)),
                "relation_vectors_enabled": bool(self.relation_vectors_enabled),
                "vector_pools": vector_pools_status,
                "vector_pools_ready": bool(vector_pools_status.get("ready", False)),
                "vector_pools_effective_mode": str(vector_pools_status.get("effective_mode", "single")),
                "runtime_ready": self.is_runtime_ready(),
                "embedding_degraded": bool(degraded.get("active", False)),
                "embedding_degraded_reason": str(degraded.get("reason", "") or ""),
                "embedding_degraded_since": degraded.get("since"),
                "embedding_last_check": degraded.get("last_check"),
                "paragraph_vector_backfill_pending": int(backfill_counts.get("pending", 0) or 0),
                "paragraph_vector_backfill_running": int(backfill_counts.get("running", 0) or 0),
                "paragraph_vector_backfill_failed": int(backfill_counts.get("failed", 0) or 0),
                "paragraph_vector_backfill_done": int(backfill_counts.get("done", 0) or 0),
            }

        if act in {"self_check", "refresh_self_check"}:
            report = await self._refresh_runtime_self_check(
                sample_text=str(kwargs.get("sample_text", "") or "A_Memorix runtime self check")
            )
            checked_at = float(report.get("checked_at") or time.time())
            dimension_mismatch = self._apply_self_check_dimension_result(report)
            if dimension_mismatch:
                self._set_embedding_degraded(active=True, reason=dimension_mismatch, checked_at=checked_at)
            elif bool(report.get("ok", False)):
                self._set_embedding_degraded(active=False, checked_at=checked_at)
            elif self._embedding_fallback_enabled():
                self._set_embedding_degraded(
                    active=True,
                    reason=str(report.get("message", "runtime self-check failed") or "runtime self-check failed"),
                    checked_at=checked_at,
                )
            return {"success": bool(report.get("ok", False)), "report": report}

        if act == "set_auto_save":
            enabled = bool(kwargs.get("enabled", False))
            self._set_cfg("advanced.enable_auto_save", enabled)
            return {"success": True, "auto_save": enabled}

        if act == "recover_embedding":
            result = await self._recover_embedding_once(
                sample_text=str(kwargs.get("sample_text", "") or "A_Memorix runtime self check")
            )
            result["embedding_degraded"] = self._is_embedding_degraded()
            result["embedding_state"] = self._embedding_degraded_snapshot()
            result["backfill_counts"] = self._paragraph_vector_backfill_counts()
            return result

        if act == "rebuild_all_vectors":
            include_relations = kwargs.get("include_relations")
            result = await self._rebuild_all_vectors(
                batch_size=optional_int(kwargs.get("batch_size")),
                include_relations=include_relations if isinstance(include_relations, bool) else None,
                dry_run=bool(kwargs.get("dry_run", False)),
            )
            result["embedding_degraded"] = self._is_embedding_degraded()
            result["backfill_counts"] = self._paragraph_vector_backfill_counts()
            return result

        if act == "paragraph_backfill_once":
            result = await self._run_paragraph_backfill_once(
                limit=optional_int(kwargs.get("limit")),
                max_retry=optional_int(kwargs.get("max_retry")),
                trigger="manual",
            )
            result["embedding_degraded"] = self._is_embedding_degraded()
            result["backfill_counts"] = self._paragraph_vector_backfill_counts()
            return result

        return {"success": False, "error": f"不支持的 runtime action: {act}"}

