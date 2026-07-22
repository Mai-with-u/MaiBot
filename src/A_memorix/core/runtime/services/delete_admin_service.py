from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import time
import uuid

from src.common.logger import get_logger

from ...utils.hash import compute_hash
from ...utils.metadata import coerce_metadata_dict
from ...utils.runtime_payloads import merge_tokens, optional_float, tokens
from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")


class _VectorCleanupRollbackError(RuntimeError):
    """向量清理 checkpoint 无法恢复，后续同池任务必须停止。"""


class MemoryDeleteAdminService(KernelServiceBase):
    async def memory_delete_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        act = str(action or "").strip().lower()
        mode = str(kwargs.get("mode", "") or "").strip().lower()
        selector = kwargs.get("selector")
        if selector is None:
            selector = {
                key: value
                for key, value in kwargs.items()
                if key
                not in {
                    "action",
                    "mode",
                    "dry_run",
                    "cascade",
                    "operation_id",
                    "reason",
                    "requested_by",
                }
            }
        reason = str(kwargs.get("reason", "") or "").strip()
        requested_by = str(kwargs.get("requested_by", "") or "").strip()

        if act == "preview":
            return await self._preview_delete_action(mode=mode, selector=selector)
        if act == "execute":
            result = await self._execute_delete_action(
                mode=mode,
                selector=selector,
                requested_by=requested_by,
                reason=reason,
            )
            await self._invalidate_import_manifest_for_sources(result)
            return result
        if act == "restore":
            return await self._restore_delete_action(
                mode=mode,
                selector=selector,
                operation_id=str(kwargs.get("operation_id", "") or "").strip(),
                requested_by=requested_by,
                reason=reason,
            )
        if act == "get_operation":
            operation = self.metadata_store.get_delete_operation(str(kwargs.get("operation_id", "") or "").strip())
            return {
                "success": operation is not None,
                "operation": operation,
                "error": "" if operation is not None else "operation 不存在",
            }
        if act == "list_operations":
            items = self.metadata_store.list_delete_operations(
                limit=max(1, int(kwargs.get("limit", 50) or 50)),
                mode=mode,
            )
            return {"success": True, "items": items, "count": len(items)}
        if act == "purge":
            return await self._purge_deleted_memory(
                grace_hours=optional_float(kwargs.get("grace_hours")),
                limit=max(1, int(kwargs.get("limit", 1000) or 1000)),
            )
        return {"success": False, "error": f"不支持的 delete action: {act}"}

    @staticmethod
    def _delete_cleanup_jobs(
        *,
        paragraph_hashes: Sequence[str],
        entity_hashes: Sequence[str],
        relation_hashes: Sequence[str],
    ) -> List[Dict[str, Any]]:
        jobs: List[Dict[str, Any]] = []
        resource_groups = (
            ("paragraph", tokens(paragraph_hashes)),
            ("entity", tokens(entity_hashes)),
            ("relation", tokens(relation_hashes)),
        )
        for resource_type, hashes in resource_groups:
            if not hashes:
                continue
            jobs.append(
                {
                    "resource_type": resource_type,
                    "resource_id": "batch",
                    "action": "vector_delete",
                    "payload": {f"{resource_type}_hashes": hashes},
                    "expected_state": {"operation_status": "pending_cleanup"},
                }
            )
        if any(hashes for _, hashes in resource_groups):
            jobs.append(
                {
                    "resource_type": "graph",
                    "resource_id": "structure",
                    "action": "graph_rebuild",
                    "payload": {},
                    "expected_state": {"operation_status": "pending_cleanup"},
                }
            )
        return jobs

    def _cleanup_vector_store(self, resource_type: str) -> Any:
        """返回 Outbox 资源唯一负责的向量池。"""
        if not self._dual_vector_pools_enabled():
            target_store = self.vector_store
        elif resource_type == "paragraph":
            target_store = self.paragraph_vector_store
        elif resource_type in {"entity", "relation"}:
            target_store = self.graph_vector_store
        else:
            raise ValueError(f"未知向量资源类型: {resource_type}")
        if target_store is None:
            raise RuntimeError(f"{resource_type} 向量存储未初始化")
        return target_store

    async def process_pending_storage_cleanup_jobs(
        self,
        *,
        operation_id: str = "",
        limit: int = 100,
    ) -> Dict[str, Any]:
        """串行执行清理 Outbox，禁止恢复状态转换与外部提交交错。"""
        async with self._storage_cleanup_lock:
            return await self._process_pending_storage_cleanup_jobs_serialized(
                operation_id=operation_id,
                limit=limit,
            )

    async def _process_pending_storage_cleanup_jobs_serialized(
        self,
        *,
        operation_id: str = "",
        limit: int = 100,
    ) -> Dict[str, Any]:
        """执行幂等清理 Outbox，并保留失败任务供后台重试。"""
        assert self.metadata_store
        worker_token = self.metadata_store.create_cleanup_worker_token()
        jobs = self.metadata_store.claim_storage_cleanup_jobs(
            worker_token=worker_token,
            operation_id=operation_id,
            limit=limit,
        )
        completed = 0
        cancelled = 0
        failed = 0
        deleted_vectors = 0
        pending_vector_batches: Dict[int, Dict[str, Any]] = {}

        def fail_job(job: Dict[str, Any], exc: Exception) -> None:
            nonlocal failed
            retry_count = max(1, int(job.get("attempt_count", 1) or 1))
            retry_delay = min(300.0, float(2 ** min(retry_count - 1, 8)))
            self.metadata_store.fail_storage_cleanup_job(
                job_id=int(job["job_id"]),
                worker_token=worker_token,
                error=str(exc),
                retry_delay_seconds=retry_delay,
            )
            failed += 1
            logger.warning(
                f"storage cleanup job 失败: job_id={job.get('job_id')}, "
                f"action={job.get('action')}, err={exc}"
            )

        def complete_job(job: Dict[str, Any], *, status: str = "completed") -> None:
            nonlocal cancelled, completed
            changed = self.metadata_store.complete_storage_cleanup_job(
                job_id=int(job["job_id"]),
                worker_token=worker_token,
                status=status,
            )
            if not changed:
                raise RuntimeError(f"storage cleanup job 租约已失效: job_id={job.get('job_id')}")
            if status == "cancelled":
                cancelled += 1
            else:
                completed += 1

        def authorize_job(job: Dict[str, Any]) -> Dict[str, Any]:
            return self.metadata_store.authorize_storage_cleanup_job(
                job_id=int(job["job_id"]),
                worker_token=worker_token,
            )

        def assert_authority(
            job: Dict[str, Any],
            expected_resource_ids: Sequence[str],
            *,
            phase: str,
        ) -> Dict[str, Any]:
            authority = authorize_job(job)
            if not authority["operation_matches"]:
                raise RuntimeError(f"向量任务的 operation 状态在{phase}前已变化")
            if authority["resource_ids"] != list(expected_resource_ids):
                raise RuntimeError(f"向量任务的资源权威状态在{phase}前已变化")
            return authority

        def rollback_vector_batch(batch: Dict[str, Any], exc: Exception) -> None:
            checkpoint_token = str(batch.get("checkpoint_token", "") or "")
            if not checkpoint_token:
                return
            try:
                batch["store"].rollback_cleanup_checkpoint(checkpoint_token)
            except Exception as rollback_exc:
                raise _VectorCleanupRollbackError(
                    f"向量批次失败且无法恢复正式提交基线: {exc}"
                ) from rollback_exc
            finally:
                batch["checkpoint_token"] = ""

        def flush_vector_batches() -> None:
            nonlocal deleted_vectors
            batches = list(pending_vector_batches.values())
            pending_vector_batches.clear()
            for batch_index, batch in enumerate(batches):
                entries = list(batch["entries"])
                try:
                    for entry in entries:
                        assert_authority(
                            entry["job"],
                            entry["resource_ids"],
                            phase="持久化",
                        )
                    # 基线已在批次入口提交；这里仅为本批外部变更再提交一次。
                    self._save_vector_store(batch["store"])
                    batch["store"].commit_cleanup_checkpoint(batch["checkpoint_token"])
                    batch["checkpoint_token"] = ""
                except Exception as exc:
                    try:
                        rollback_vector_batch(batch, exc)
                    except _VectorCleanupRollbackError as rollback_error:
                        for entry in entries:
                            fail_job(entry["job"], rollback_error)
                        for remaining_batch in batches[batch_index + 1 :]:
                            try:
                                rollback_vector_batch(remaining_batch, rollback_error)
                            finally:
                                for entry in remaining_batch["entries"]:
                                    fail_job(entry["job"], rollback_error)
                        raise
                    for entry in entries:
                        fail_job(entry["job"], exc)
                    continue
                deleted_vectors += int(batch.get("deleted_count", 0) or 0)
                for entry in entries:
                    try:
                        complete_job(entry["job"])
                    except Exception as exc:
                        fail_job(entry["job"], exc)

        for job in jobs:
            try:
                action = str(job.get("action", "") or "").strip()
                authority = authorize_job(job)
                if not authority["operation_matches"]:
                    complete_job(job, status="cancelled")
                    continue

                if action == "vector_delete":
                    resource_type = str(authority["resource_type"])
                    resource_ids = list(authority["resource_ids"])
                    if not resource_ids:
                        complete_job(job)
                        continue
                    target_store = self._cleanup_vector_store(resource_type)
                    batch = pending_vector_batches.get(id(target_store))
                    if batch is None:
                        # 先提交进入清理前的合法缓冲区，再以该提交作为精确回滚基线。
                        self._save_vector_store(target_store)
                        batch = {
                            "store": target_store,
                            "entries": [],
                            "deleted_count": 0,
                            "checkpoint_token": target_store.begin_cleanup_checkpoint(),
                        }
                        pending_vector_batches[id(target_store)] = batch
                    delete_kwargs = {
                        "paragraph_hashes": resource_ids if resource_type == "paragraph" else [],
                        "entity_hashes": resource_ids if resource_type == "entity" else [],
                        "relation_hashes": resource_ids if resource_type == "relation" else [],
                    }
                    try:
                        if self._dual_vector_pools_enabled():
                            vector_ids = list(resource_ids)
                            if resource_type in {"entity", "relation"}:
                                vector_ids = [
                                    self._graph_vector_id(resource_type, resource_id)
                                    for resource_id in resource_ids
                                ]
                            deleted_count = int(target_store.delete(vector_ids) or 0)
                        else:
                            deleted_count = int(self._delete_vectors_by_type(**delete_kwargs) or 0)
                    except Exception as exc:
                        pending_vector_batches.pop(id(target_store), None)
                        try:
                            rollback_vector_batch(batch, exc)
                        finally:
                            for entry in batch["entries"]:
                                fail_job(entry["job"], exc)
                            batch["entries"].clear()
                        raise
                    batch["deleted_count"] += deleted_count
                    batch["entries"].append(
                        {"job": job, "resource_ids": resource_ids}
                    )
                    continue
                elif action == "vector_upsert":
                    # 向量删除必须连续落盘，不能跨过 embedding await 留下未持久化墓碑。
                    flush_vector_batches()
                    resource_type = str(authority["resource_type"])
                    resource_ids = list(authority["resource_ids"])
                    if not resource_ids:
                        complete_job(job, status="cancelled")
                        continue
                    if resource_type == "relation" and not self.relation_vectors_enabled:
                        # 任务可能在关系向量启用时创建，却在重启后的禁用配置下执行。
                        # 此时不应留下永远无法消费的 pending 状态；禁用就是当前
                        # 权威投影策略，先把仍获授权的关系收敛为 none 再完成任务。
                        for relation_hash in resource_ids:
                            if not self.metadata_store.set_relation_vector_state(
                                relation_hash,
                                "none",
                            ):
                                raise RuntimeError(
                                    f"禁用关系向量时权威关系不存在: {relation_hash}"
                                )
                        complete_job(job)
                        continue
                    resource_id = resource_ids[0]
                    if resource_type == "paragraph":
                        item = self.metadata_store.get_paragraph(resource_id)
                    elif resource_type == "entity":
                        item = self.metadata_store.get_entity(resource_id)
                    elif resource_type == "relation":
                        item = self.metadata_store.get_relation(resource_id)
                    else:
                        raise ValueError(f"未知 vector_upsert 资源类型: {resource_type}")
                    if item is None:
                        raise RuntimeError(f"{resource_type} 权威资源不存在")
                    target_store = self._cleanup_vector_store(resource_type)
                    upsert_checkpoint = {"token": ""}

                    def before_vector_write(
                        current_job: Dict[str, Any] = job,
                        current_resource_ids: Sequence[str] = tuple(resource_ids),
                        current_store: Any = target_store,
                        checkpoint: Dict[str, str] = upsert_checkpoint,
                    ) -> None:
                        # embedding await 已结束；从此处到 save/commit 均为同步区间。
                        assert_authority(current_job, current_resource_ids, phase="写入")
                        self._save_vector_store(current_store)
                        checkpoint["token"] = current_store.begin_cleanup_checkpoint()

                    try:
                        if resource_type == "paragraph":
                            restored = await self._ensure_paragraph_vector(
                                item,
                                before_vector_write=before_vector_write,
                            )
                        elif resource_type == "entity":
                            restored = await self._ensure_entity_vector(
                                item,
                                before_vector_write=before_vector_write,
                            )
                        else:
                            restored = await self._ensure_relation_vector(
                                item,
                                before_vector_write=before_vector_write,
                            )
                        if not restored:
                            raise RuntimeError(f"{resource_type} 向量恢复失败")
                        assert_authority(job, resource_ids, phase="持久化")
                        checkpoint_token = upsert_checkpoint["token"]
                        if checkpoint_token:
                            self._save_vector_store(target_store)
                            target_store.commit_cleanup_checkpoint(checkpoint_token)
                            upsert_checkpoint["token"] = ""
                        else:
                            # 已存在向量也可能来自进入任务前的合法写缓冲区；
                            # Outbox 完成前仍需建立耐久提交，不能只相信内存成员状态。
                            self._save_vector_store(target_store)
                    except Exception as exc:
                        checkpoint_token = upsert_checkpoint["token"]
                        if checkpoint_token:
                            try:
                                target_store.rollback_cleanup_checkpoint(checkpoint_token)
                            except Exception as rollback_exc:
                                raise _VectorCleanupRollbackError(
                                    f"向量恢复失败且无法恢复正式提交基线: {exc}"
                                ) from rollback_exc
                            finally:
                                upsert_checkpoint["token"] = ""
                        if resource_type == "relation" and self.metadata_store.get_relation(resource_id) is not None:
                            self.metadata_store.set_relation_vector_state(resource_id, "pending")
                        raise
                    complete_job(job)
                    continue
                elif action in {"graph_rebuild", "graph_restore"}:
                    flush_vector_batches()
                    self._rebuild_graph_from_metadata()
                    if self.graph_store is None:
                        raise RuntimeError("图存储未初始化")
                    refreshed_authority = authorize_job(job)
                    if not refreshed_authority["operation_matches"]:
                        raise RuntimeError("图任务的 operation 状态在持久化前已变化")
                    self.graph_store.save()
                else:
                    raise ValueError(f"未知清理任务 action: {action}")

                complete_job(job)
            except _VectorCleanupRollbackError as exc:
                fail_job(job, exc)
                raise
            except Exception as exc:
                fail_job(job, exc)

        flush_vector_batches()

        operation_tokens = merge_tokens([operation_id], [job.get("operation_id") for job in jobs])
        reconciled_tokens = self.metadata_store.reconcile_settled_delete_operations(
            operation_tokens if operation_id else ()
        )
        operation_tokens = merge_tokens(operation_tokens, reconciled_tokens)
        operation_summaries: Dict[str, Dict[str, int]] = {}
        for token in operation_tokens:
            summary = self.metadata_store.summarize_storage_cleanup_jobs(token)
            operation_summaries[token] = summary

        return {
            "claimed": len(jobs),
            "completed": completed,
            "cancelled": cancelled,
            "failed": failed,
            "deleted_vectors": deleted_vectors,
            "operations": operation_summaries,
        }

    @staticmethod
    def _selector_dict(selector: Any) -> Dict[str, Any]:
        if isinstance(selector, dict):
            return dict(selector)
        if isinstance(selector, (list, tuple)):
            return {"items": list(selector)}
        token = str(selector or "").strip()
        return {"query": token} if token else {}

    @staticmethod
    def _filter_relation_deletion_preconditions(
        cursor: Any,
        relation_hashes: Sequence[str],
        expected_states: Any,
    ) -> tuple[List[str], List[str]]:
        """在删除事务内校验生命周期候选的乐观锁状态。"""
        if expected_states is None:
            return list(relation_hashes), []
        if not isinstance(expected_states, dict):
            raise ValueError("expected_relation_states 必须是以关系 hash 为键的对象")

        required_keys = {
            "expected_lifecycle_revision",
            "expected_retention_strength",
            "expected_retention_anchor_at",
            "expected_inactive_since",
            "expected_inactive_reason",
            "expected_is_inactive",
            "expected_is_permanent",
            "expected_is_pinned",
            "expected_protected_until",
        }
        eligible: List[str] = []
        stale: List[str] = []
        for offset in range(0, len(relation_hashes), 900):
            chunk = list(relation_hashes[offset : offset + 900])
            placeholders = ",".join(["?"] * len(chunk))
            cursor.execute(
                f"""
                SELECT hash, lifecycle_revision, retention_strength, retention_anchor_at,
                       inactive_since, inactive_reason, is_inactive, is_permanent,
                       is_pinned, protected_until
                FROM relations
                WHERE hash IN ({placeholders})
                """,
                tuple(chunk),
            )
            current_rows = {str(row["hash"]): row for row in cursor.fetchall()}
            for relation_hash in chunk:
                expected = expected_states.get(relation_hash)
                current = current_rows.get(relation_hash)
                if not isinstance(expected, dict) or not required_keys.issubset(expected) or current is None:
                    stale.append(relation_hash)
                    continue

                current_inactive_since = (
                    None if current["inactive_since"] is None else float(current["inactive_since"])
                )
                current_protected_until = (
                    None if current["protected_until"] is None else float(current["protected_until"])
                )
                matches = (
                    int(current["lifecycle_revision"] or 0)
                    == int(expected["expected_lifecycle_revision"])
                    and float(current["retention_strength"])
                    == float(expected["expected_retention_strength"])
                    and float(current["retention_anchor_at"])
                    == float(expected["expected_retention_anchor_at"])
                    and current_inactive_since == expected["expected_inactive_since"]
                    and str(current["inactive_reason"] or "")
                    == str(expected["expected_inactive_reason"] or "")
                    and bool(current["is_inactive"]) is bool(expected["expected_is_inactive"])
                    and bool(current["is_permanent"]) is bool(expected["expected_is_permanent"])
                    and bool(current["is_pinned"]) is bool(expected["expected_is_pinned"])
                    and current_protected_until == expected["expected_protected_until"]
                )
                (eligible if matches else stale).append(relation_hash)
        return eligible, stale

    def _resolve_paragraph_targets(self, selector: Any, *, include_deleted: bool = False) -> List[Dict[str, Any]]:
        assert self.metadata_store
        raw = self._selector_dict(selector)
        rows: List[Dict[str, Any]] = []
        hashes = merge_tokens(raw.get("hashes"), raw.get("items"), [raw.get("hash")])
        for hash_value in hashes:
            row = self.metadata_store.get_paragraph(hash_value)
            if row is None:
                continue
            if not include_deleted and bool(row.get("is_deleted", 0)):
                continue
            rows.append(row)
        if rows:
            return rows
        query = str(raw.get("query", "") or raw.get("content", "") or "").strip()
        if not query:
            return []
        if len(query) == 64 and all(ch in "0123456789abcdef" for ch in query.lower()):
            row = self.metadata_store.get_paragraph(query)
            if row is None:
                return []
            if not include_deleted and bool(row.get("is_deleted", 0)):
                return []
            return [row]
        matches = self.metadata_store.search_paragraphs_by_content(query)
        return [row for row in matches if include_deleted or not bool(row.get("is_deleted", 0))]

    def _resolve_entity_targets(self, selector: Any, *, include_deleted: bool = False) -> List[Dict[str, Any]]:
        assert self.metadata_store
        raw = self._selector_dict(selector)
        rows: List[Dict[str, Any]] = []
        hashes = merge_tokens(raw.get("hashes"), raw.get("items"), [raw.get("hash")])
        for hash_value in hashes:
            row = self.metadata_store.get_entity(hash_value)
            if row is None:
                continue
            if not include_deleted and bool(row.get("is_deleted", 0)):
                continue
            rows.append(row)
        names = merge_tokens(raw.get("names"), [raw.get("name")], [raw.get("query")])
        for name in names:
            if not name:
                continue
            matches = self.metadata_store.query(
                """
                SELECT *
                FROM entities
                WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
                   OR hash = ?
                ORDER BY appearance_count DESC, created_at ASC
                """,
                (name, compute_hash(str(name).strip().lower())),
            )
            for row in matches:
                if not include_deleted and bool(row.get("is_deleted", 0)):
                    continue
                rows.append(
                    self.metadata_store._row_to_dict(row, "entity")
                    if hasattr(self.metadata_store, "_row_to_dict")
                    else row
                )
        dedup: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            token = str(row.get("hash", "") or "").strip()
            if token and token not in dedup:
                dedup[token] = row
        return list(dedup.values())

    def _resolve_source_targets(self, selector: Any) -> List[str]:
        raw = self._selector_dict(selector)
        return merge_tokens(raw.get("sources"), [raw.get("source")], [raw.get("query")], raw.get("items"))

    def _resolve_relation_hashes(self, target: str) -> List[str]:
        """在删除服务内部解析活跃关系，避免依赖其他管理服务的私有方法。"""
        assert self.metadata_store
        token = str(target or "").strip()
        if not token:
            return []
        if len(token) == 64 and all(character in "0123456789abcdef" for character in token.lower()):
            return [token] if self.metadata_store.get_relation(token) is not None else []
        hashes = self.metadata_store.search_relation_hashes_by_text(token, limit=10)
        if hashes:
            return hashes
        return [
            str(row.get("hash", "") or "")
            for row in self.metadata_store.get_relations(subject=token)[:10]
            if str(row.get("hash", "") or "").strip()
        ]

    def _resolve_deleted_relation_hashes(self, target: str) -> List[str]:
        """在删除服务内部解析关系墓碑。"""
        assert self.metadata_store
        token = str(target or "").strip()
        if not token:
            return []
        if len(token) == 64 and all(character in "0123456789abcdef" for character in token.lower()):
            return [token] if self.metadata_store.get_deleted_relation(token) is not None else []
        return self.metadata_store.search_deleted_relation_hashes_by_text(token, limit=10)

    def _snapshot_relation_item(self, hash_value: str) -> Optional[Dict[str, Any]]:
        assert self.metadata_store
        relation = self.metadata_store.get_relation(hash_value)
        if relation is None:
            relation = self.metadata_store.get_deleted_relation(hash_value)
        if relation is None:
            return None
        paragraph_hashes = [
            str(row.get("paragraph_hash", "") or "").strip()
            for row in self.metadata_store.query(
                "SELECT paragraph_hash FROM paragraph_relations WHERE relation_hash = ? ORDER BY paragraph_hash ASC",
                (hash_value,),
            )
            if str(row.get("paragraph_hash", "") or "").strip()
        ]
        return {
            "item_type": "relation",
            "item_hash": hash_value,
            "item_key": hash_value,
            "payload": {
                "relation": relation,
                "paragraph_hashes": paragraph_hashes,
            },
        }

    def _snapshot_paragraph_item(self, hash_value: str) -> Optional[Dict[str, Any]]:
        assert self.metadata_store
        paragraph = self.metadata_store.get_paragraph(hash_value)
        if paragraph is None:
            return None
        entity_links = [
            {
                "paragraph_hash": hash_value,
                "entity_hash": str(row.get("entity_hash", "") or ""),
                "mention_count": int(row.get("mention_count", 1) or 1),
            }
            for row in self.metadata_store.query(
                """
                SELECT paragraph_hash, entity_hash, mention_count
                FROM paragraph_entities
                WHERE paragraph_hash = ?
                ORDER BY entity_hash ASC
                """,
                (hash_value,),
            )
        ]
        relation_hashes = [
            str(row.get("relation_hash", "") or "").strip()
            for row in self.metadata_store.query(
                """
                SELECT relation_hash
                FROM paragraph_relations
                WHERE paragraph_hash = ?
                ORDER BY relation_hash ASC
                """,
                (hash_value,),
            )
            if str(row.get("relation_hash", "") or "").strip()
        ]
        return {
            "item_type": "paragraph",
            "item_hash": hash_value,
            "item_key": hash_value,
            "payload": {
                "paragraph": paragraph,
                "entity_links": entity_links,
                "relation_hashes": relation_hashes,
                "external_refs": self.metadata_store.list_external_memory_refs_by_paragraphs([hash_value]),
                "fact_evidence_snapshot": self.metadata_store.snapshot_fact_evidence_for_paragraphs(
                    [hash_value]
                ),
            },
        }

    def _snapshot_entity_item(self, hash_value: str) -> Optional[Dict[str, Any]]:
        assert self.metadata_store
        entity = self.metadata_store.get_entity(hash_value)
        if entity is None:
            return None
        paragraph_links = [
            {
                "paragraph_hash": str(row.get("paragraph_hash", "") or ""),
                "entity_hash": hash_value,
                "mention_count": int(row.get("mention_count", 1) or 1),
            }
            for row in self.metadata_store.query(
                """
                SELECT paragraph_hash, mention_count
                FROM paragraph_entities
                WHERE entity_hash = ?
                ORDER BY paragraph_hash ASC
                """,
                (hash_value,),
            )
        ]
        return {
            "item_type": "entity",
            "item_hash": hash_value,
            "item_key": hash_value,
            "payload": {
                "entity": entity,
                "paragraph_links": paragraph_links,
            },
        }

    def _relation_has_remaining_paragraphs(self, relation_hash: str, removing_hashes: Sequence[str]) -> bool:
        assert self.metadata_store
        excluded = [str(item or "").strip() for item in removing_hashes if str(item or "").strip()]
        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        if excluded:
            placeholders = ",".join(["?"] * len(excluded))
            cursor.execute(
                f"""
                SELECT p.hash, p.metadata
                FROM paragraph_relations pr
                JOIN paragraphs p ON p.hash = pr.paragraph_hash
                WHERE pr.relation_hash = ?
                  AND pr.paragraph_hash NOT IN ({placeholders})
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                """,
                tuple([relation_hash] + excluded),
            )
        else:
            cursor.execute(
                """
                SELECT p.hash, p.metadata
                FROM paragraph_relations pr
                JOIN paragraphs p ON p.hash = pr.paragraph_hash
                WHERE pr.relation_hash = ?
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                """,
                (relation_hash,),
            )
        now = time.time()
        for row in cursor.fetchall():
            paragraph = self.metadata_store._row_to_dict(row, "paragraph")
            metadata = coerce_metadata_dict(paragraph.get("metadata"))
            memory_change = metadata.get("memory_change") if isinstance(metadata.get("memory_change"), dict) else {}
            valid_to = optional_float(memory_change.get("valid_to"))
            if valid_to is None or valid_to > now:
                return True
        return False

    def _build_delete_preview_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        item_type = str(item.get("item_type", "") or "").strip()
        item_hash = str(item.get("item_hash", "") or "").strip()
        item_key = str(item.get("item_key", "") or item_hash).strip()
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        preview = {
            "item_type": item_type,
            "item_hash": item_hash,
            "item_key": item_key,
        }
        if item_type == "entity":
            entity = payload.get("entity") if isinstance(payload.get("entity"), dict) else {}
            name = str(entity.get("name", "") or item_key).strip()
            preview["label"] = name
            preview["preview"] = name
        elif item_type == "relation":
            relation = payload.get("relation") if isinstance(payload.get("relation"), dict) else {}
            subject = str(relation.get("subject", "") or "").strip()
            predicate = str(relation.get("predicate", "") or "").strip()
            obj = str(relation.get("object", "") or "").strip()
            text = self._format_relation_text(subject, predicate, obj)
            preview["label"] = text or item_key
            preview["preview"] = text or item_key
        elif item_type == "paragraph":
            paragraph = payload.get("paragraph") if isinstance(payload.get("paragraph"), dict) else {}
            content = str(paragraph.get("content", "") or "").strip()
            source = str(paragraph.get("source", "") or "").strip()
            preview["label"] = source or item_key
            preview["preview"] = self._trim_text(content)
            preview["source"] = source
        return preview

    def _build_standard_delete_result(
        self,
        *,
        mode: str,
        operation_id: str = "",
        counts: Optional[Dict[str, Any]] = None,
        sources: Optional[Sequence[str]] = None,
        deleted_entity_count: int = 0,
        deleted_relation_count: int = 0,
        deleted_paragraph_count: int = 0,
        deleted_source_count: int = 0,
        deleted_vector_count: int = 0,
        requested_source_count: int = 0,
        matched_source_count: int = 0,
        error: str = "",
    ) -> Dict[str, Any]:
        normalized_counts = dict(counts or {})
        normalized_counts.setdefault("entities", int(normalized_counts.get("entities", 0) or 0))
        normalized_counts.setdefault("relations", int(normalized_counts.get("relations", 0) or 0))
        normalized_counts.setdefault("paragraphs", int(normalized_counts.get("paragraphs", 0) or 0))
        normalized_counts.setdefault("sources", int(normalized_counts.get("sources", 0) or 0))
        if requested_source_count:
            normalized_counts["requested_sources"] = int(requested_source_count or 0)
        if matched_source_count:
            normalized_counts["matched_sources"] = int(matched_source_count or 0)

        deleted_count = (
            int(deleted_entity_count or 0)
            + int(deleted_relation_count or 0)
            + int(deleted_paragraph_count or 0)
            + int(deleted_source_count or 0)
        )
        return {
            "success": bool(not error and deleted_count > 0),
            "mode": str(mode or "").strip().lower(),
            "operation_id": str(operation_id or "").strip(),
            "counts": normalized_counts,
            "sources": [str(item or "").strip() for item in (sources or []) if str(item or "").strip()],
            "deleted_count": deleted_count,
            "deleted_entity_count": int(deleted_entity_count or 0),
            "deleted_relation_count": int(deleted_relation_count or 0),
            "deleted_paragraph_count": int(deleted_paragraph_count or 0),
            "deleted_source_count": int(deleted_source_count or 0),
            "deleted_vector_count": int(deleted_vector_count or 0),
            "requested_source_count": int(requested_source_count or 0),
            "matched_source_count": int(matched_source_count or 0),
            "error": str(error or ""),
        }

    async def _build_delete_plan(self, *, mode: str, selector: Any) -> Dict[str, Any]:
        """解析删除选择器并生成不执行写操作的完整删除计划。

        计划同时收集快照、向量 ID、来源以及因段落或实体失去引用而需要级联处理的
        关系。预览和正式执行共用该结果，避免两条路径对删除范围作出不同判断。
        """
        assert self.metadata_store
        act_mode = str(mode or "").strip().lower()
        normalized_selector = self._selector_dict(selector)
        items: List[Dict[str, Any]] = []
        counts = {"relations": 0, "paragraphs": 0, "entities": 0, "sources": 0}
        vector_ids: List[str] = []
        sources: List[str] = []
        target_hashes: Dict[str, List[str]] = {
            "relations": [],
            "paragraphs": [],
            "entities": [],
            "sources": [],
            "matched_sources": [],
        }
        seen_items: set[tuple[str, str]] = set()
        relation_hashes: List[str] = []
        paragraph_hashes: List[str] = []
        entity_hashes: List[str] = []
        paragraph_relation_candidates: List[str] = []

        def append_item(snapshot: Optional[Dict[str, Any]]) -> None:
            if not isinstance(snapshot, dict):
                return
            item_type = str(snapshot.get("item_type", "") or "").strip()
            item_hash = str(snapshot.get("item_hash", "") or snapshot.get("item_key", "") or "").strip()
            if not item_type or not item_hash:
                return
            key = (item_type, item_hash)
            if key in seen_items:
                return
            seen_items.add(key)
            items.append(snapshot)

        def append_relation_hash(hash_value: str) -> None:
            token = str(hash_value or "").strip()
            if not token or token in relation_hashes:
                return
            row = self.metadata_store.get_relation(token)
            if row is None:
                return
            relation_hashes.append(token)
            append_item(self._snapshot_relation_item(token))
            vector_ids.append(token)

        def append_paragraph_row(row: Optional[Dict[str, Any]]) -> None:
            if not isinstance(row, dict):
                return
            paragraph_hash = str(row.get("hash", "") or "").strip()
            if not paragraph_hash or paragraph_hash in paragraph_hashes or bool(row.get("is_deleted", 0)):
                return
            paragraph_hashes.append(paragraph_hash)
            snapshot = self._snapshot_paragraph_item(paragraph_hash)
            append_item(snapshot)
            vector_ids.append(paragraph_hash)
            paragraph = (
                (snapshot or {}).get("payload", {}).get("paragraph")
                if isinstance((snapshot or {}).get("payload"), dict)
                else {}
            )
            source = str((paragraph or {}).get("source", "") or "").strip()
            if source:
                sources.append(source)
            paragraph_relation_candidates.extend(tokens(((snapshot or {}).get("payload") or {}).get("relation_hashes")))

        def append_entity_row(row: Optional[Dict[str, Any]]) -> None:
            if not isinstance(row, dict):
                return
            entity_hash = str(row.get("hash", "") or "").strip()
            if not entity_hash or entity_hash in entity_hashes or bool(row.get("is_deleted", 0)):
                return
            entity_hashes.append(entity_hash)
            append_item(self._snapshot_entity_item(entity_hash))
            vector_ids.append(entity_hash)

        if act_mode == "relation":
            direct_hashes = merge_tokens(
                normalized_selector.get("hashes"),
                normalized_selector.get("items"),
                [normalized_selector.get("hash")],
            )
            query_hashes = self._resolve_relation_hashes(str(normalized_selector.get("query", "") or ""))
            for hash_value in direct_hashes or query_hashes:
                append_relation_hash(hash_value)
            counts["relations"] = len(relation_hashes)
            target_hashes["relations"] = list(relation_hashes)

        elif act_mode in {"paragraph", "source"}:
            paragraph_rows: List[Dict[str, Any]] = []
            if act_mode == "source":
                source_tokens = self._resolve_source_targets(normalized_selector)
                target_hashes["sources"] = source_tokens
                counts["requested_sources"] = len(source_tokens)
                matched_source_tokens: List[str] = []
                for source in source_tokens:
                    source_rows = self.metadata_store.query(
                        """
                        SELECT *
                        FROM paragraphs
                        WHERE source = ?
                          AND (is_deleted IS NULL OR is_deleted = 0)
                        ORDER BY created_at ASC
                        """,
                        (source,),
                    )
                    if source_rows:
                        matched_source_tokens.append(source)
                        sources.append(source)
                        paragraph_rows.extend(source_rows)
                target_hashes["matched_sources"] = matched_source_tokens
                counts["sources"] = len(matched_source_tokens)
                counts["matched_sources"] = len(matched_source_tokens)
            else:
                paragraph_rows = self._resolve_paragraph_targets(normalized_selector, include_deleted=False)
            for row in paragraph_rows:
                append_paragraph_row(row)
            target_hashes["paragraphs"] = list(paragraph_hashes)
            counts["paragraphs"] = len(paragraph_hashes)

            for relation_hash in tokens(paragraph_relation_candidates):
                if not self._relation_has_remaining_paragraphs(relation_hash, paragraph_hashes):
                    append_relation_hash(relation_hash)
            target_hashes["relations"] = list(relation_hashes)
            counts["relations"] = len(relation_hashes)

        elif act_mode == "entity":
            entity_rows = self._resolve_entity_targets(normalized_selector, include_deleted=False)
            for row in entity_rows:
                append_entity_row(row)
            target_hashes["entities"] = list(entity_hashes)
            counts["entities"] = len(entity_hashes)
            entity_names = [
                str(row.get("name", "") or "").strip() for row in entity_rows if str(row.get("name", "") or "").strip()
            ]
            for entity_name in entity_names:
                for relation in self.metadata_store.get_relations(
                    subject=entity_name
                ) + self.metadata_store.get_relations(object=entity_name):
                    append_relation_hash(str(relation.get("hash", "") or "").strip())
            target_hashes["relations"] = list(relation_hashes)
            counts["relations"] = len(relation_hashes)
        elif act_mode == "mixed":
            source_tokens = merge_tokens(normalized_selector.get("sources"), [normalized_selector.get("source")])
            target_hashes["sources"] = list(source_tokens)
            counts["requested_sources"] = len(source_tokens)
            matched_source_tokens: List[str] = []

            for row in self._resolve_entity_targets(
                {"hashes": normalized_selector.get("entity_hashes")}, include_deleted=False
            ):
                append_entity_row(row)
            target_hashes["entities"] = list(entity_hashes)
            counts["entities"] = len(entity_hashes)

            for row in self._resolve_paragraph_targets(
                {"hashes": normalized_selector.get("paragraph_hashes")}, include_deleted=False
            ):
                append_paragraph_row(row)

            for source in source_tokens:
                source_rows = self.metadata_store.query(
                    """
                    SELECT *
                    FROM paragraphs
                    WHERE source = ?
                      AND (is_deleted IS NULL OR is_deleted = 0)
                    ORDER BY created_at ASC
                    """,
                    (source,),
                )
                if source_rows:
                    matched_source_tokens.append(source)
                    sources.append(source)
                    for row in source_rows:
                        append_paragraph_row(row)

            target_hashes["paragraphs"] = list(paragraph_hashes)
            counts["paragraphs"] = len(paragraph_hashes)
            target_hashes["matched_sources"] = matched_source_tokens
            counts["sources"] = len(matched_source_tokens)
            counts["matched_sources"] = len(matched_source_tokens)

            for hash_value in tokens(normalized_selector.get("relation_hashes")):
                append_relation_hash(hash_value)

            entity_names = [
                str(row.get("name", "") or "").strip()
                for row in self._resolve_entity_targets({"hashes": entity_hashes}, include_deleted=False)
                if str(row.get("name", "") or "").strip()
            ]
            for entity_name in entity_names:
                for relation in self.metadata_store.get_relations(
                    subject=entity_name
                ) + self.metadata_store.get_relations(object=entity_name):
                    append_relation_hash(str(relation.get("hash", "") or "").strip())

            for relation_hash in tokens(paragraph_relation_candidates):
                if not self._relation_has_remaining_paragraphs(relation_hash, paragraph_hashes):
                    append_relation_hash(relation_hash)

            target_hashes["relations"] = list(relation_hashes)
            counts["relations"] = len(relation_hashes)
        else:
            return {"success": False, "error": f"不支持的 delete mode: {act_mode}"}

        sources = tokens(sources)
        vector_ids = tokens(vector_ids)
        primary_count = (
            counts.get(f"{act_mode}s", 0) if act_mode not in {"source", "mixed"} else counts.get("matched_sources", 0)
        )
        success = (
            primary_count > 0
            or counts.get("paragraphs", 0) > 0
            or counts.get("relations", 0) > 0
            or counts.get("entities", 0) > 0
            if act_mode != "source"
            else (counts.get("matched_sources", 0) > 0 and counts.get("paragraphs", 0) > 0)
        )
        return {
            "success": success,
            "mode": act_mode,
            "selector": normalized_selector,
            "items": items,
            "counts": counts,
            "vector_ids": vector_ids,
            "sources": sources,
            "target_hashes": target_hashes,
            "requested_source_count": counts.get("requested_sources", 0) if act_mode == "source" else 0,
            "matched_source_count": counts.get("matched_sources", 0) if act_mode == "source" else 0,
            "error": "" if success else "未命中可删除内容",
        }

    async def _preview_delete_action(self, *, mode: str, selector: Any) -> Dict[str, Any]:
        plan = await self._build_delete_plan(mode=mode, selector=selector)
        if not plan.get("success", False):
            return {"success": False, "error": plan.get("error", "未命中可删除内容")}
        preview_items = [self._build_delete_preview_item(item) for item in plan.get("items", [])[:100]]
        return {
            "success": True,
            "mode": plan.get("mode"),
            "selector": plan.get("selector"),
            "counts": plan.get("counts", {}),
            "requested_source_count": int(plan.get("requested_source_count", 0) or 0),
            "matched_source_count": int(plan.get("matched_source_count", 0) or 0),
            "sources": plan.get("sources", []),
            "vector_ids": plan.get("vector_ids", []),
            "items": preview_items,
            "item_count": len(plan.get("items", [])),
            "dry_run": True,
        }

    async def _execute_delete_action(
        self,
        *,
        mode: str,
        selector: Any,
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        async with self._storage_cleanup_lock:
            return await self._execute_delete_action_serialized(
                mode=mode,
                selector=selector,
                requested_by=requested_by,
                reason=reason,
            )

    async def _execute_delete_action_serialized(
        self,
        *,
        mode: str,
        selector: Any,
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        """原子提交元数据删除和外部存储清理 Outbox。"""
        assert self.metadata_store
        plan: Dict[str, Any] = {}
        act_mode = str(mode or "").strip().lower()
        paragraph_hashes: List[str] = []
        entity_hashes: List[str] = []
        relation_hashes: List[str] = []
        requested_source_tokens: List[str] = []
        matched_source_tokens: List[str] = []
        affected_sources: List[str] = []
        stale_relation_hashes: List[str] = []
        operation_id = ""
        try:
            with self.metadata_store.transaction(immediate=True) as conn:
                # 删除计划、快照和写入必须共享同一个写事务。否则计划生成后新增的
                # 关系或证据可能逃过级联处理，恢复快照也无法代表实际提交状态。
                plan = await self._build_delete_plan(mode=mode, selector=selector)
                if not plan.get("success", False):
                    return {"success": False, "error": plan.get("error", "未命中可删除内容")}

                act_mode = str(plan.get("mode", "") or "").strip().lower()
                paragraph_hashes = tokens((plan.get("target_hashes") or {}).get("paragraphs"))
                entity_hashes = tokens((plan.get("target_hashes") or {}).get("entities"))
                relation_hashes = tokens((plan.get("target_hashes") or {}).get("relations"))
                requested_source_tokens = tokens((plan.get("target_hashes") or {}).get("sources"))
                matched_source_tokens = tokens((plan.get("target_hashes") or {}).get("matched_sources"))
                affected_sources = tokens(plan.get("sources"))
                cursor = conn.cursor()
                expected_relation_states = (plan.get("selector") or {}).get("expected_relation_states")
                if expected_relation_states is not None:
                    if act_mode != "relation":
                        raise ValueError("expected_relation_states 仅允许用于 relation 删除")
                    relation_hashes, stale_relation_hashes = self._filter_relation_deletion_preconditions(
                        cursor,
                        relation_hashes,
                        expected_relation_states,
                    )
                    stale_set = set(stale_relation_hashes)
                    plan["items"] = [
                        item
                        for item in plan.get("items", [])
                        if not (
                            str(item.get("item_type", "") or "") == "relation"
                            and str(item.get("item_hash", "") or "") in stale_set
                        )
                    ]
                    plan["vector_ids"] = [
                        item for item in tokens(plan.get("vector_ids")) if item not in stale_set
                    ]
                    plan["target_hashes"] = {
                        **dict(plan.get("target_hashes") or {}),
                        "relations": relation_hashes,
                    }
                    plan["counts"] = {
                        **dict(plan.get("counts") or {}),
                        "relations": len(relation_hashes),
                    }
                    if not relation_hashes:
                        return {
                            "success": True,
                            "mode": act_mode,
                            "operation_id": "",
                            "counts": plan["counts"],
                            "deleted_count": 0,
                            "deleted_relation_count": 0,
                            "stale_relation_hashes": stale_relation_hashes,
                            "skipped_due_to_concurrent_change": len(stale_relation_hashes),
                        }
                if entity_hashes:
                    placeholders = ",".join(["?"] * len(entity_hashes))
                    cursor.execute(
                        f"""
                        SELECT DISTINCT p.source
                        FROM paragraph_entities pe
                        JOIN paragraphs p ON p.hash = pe.paragraph_hash
                        WHERE pe.entity_hash IN ({placeholders})
                          AND p.source IS NOT NULL AND TRIM(p.source) != ''
                          AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                        """,
                        tuple(entity_hashes),
                    )
                    affected_sources = tokens(
                        [*affected_sources, *(str(row["source"] or "").strip() for row in cursor.fetchall())]
                    )
                if paragraph_hashes:
                    current_fact_snapshot = self.metadata_store.snapshot_fact_evidence_for_paragraphs(
                        paragraph_hashes,
                        conn=conn,
                    )
                    for item in plan.get("items", []):
                        if str(item.get("item_type", "") or "") != "paragraph":
                            continue
                        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                        paragraph_hash = str(item.get("item_hash", "") or "").strip()
                        payload["fact_evidence_snapshot"] = {
                            **current_fact_snapshot,
                            "paragraph_hashes": [paragraph_hash],
                            "evidence": [
                                evidence
                                for evidence in current_fact_snapshot.get("evidence", [])
                                if str(evidence.get("evidence_id", "") or "") == paragraph_hash
                            ],
                        }
                        item["payload"] = payload
                operation = self.metadata_store.create_delete_operation(
                    mode=act_mode,
                    selector=plan.get("selector"),
                    items=plan.get("items", []),
                    reason=reason,
                    requested_by=requested_by,
                    status="prepared",
                    summary={
                        "counts": plan.get("counts", {}),
                        "sources": affected_sources,
                        "vector_ids": plan.get("vector_ids", []),
                        "state": "prepared",
                    },
                )
                operation_id = str(operation.get("operation_id", "") or "")

                if paragraph_hashes:
                    changed = self.metadata_store.mark_as_deleted(
                        paragraph_hashes,
                        "paragraph",
                        reason=str(reason or "user_delete").strip(),
                    )
                    if changed != len(paragraph_hashes):
                        raise RuntimeError("段落删除计划在提交前已发生变化")
                    placeholders = ",".join(["?"] * len(paragraph_hashes))
                    cursor.execute(
                        f"DELETE FROM paragraph_entities WHERE paragraph_hash IN ({placeholders})",
                        tuple(paragraph_hashes),
                    )
                    cursor.execute(
                        f"DELETE FROM paragraph_relations WHERE paragraph_hash IN ({placeholders})",
                        tuple(paragraph_hashes),
                    )
                    self.metadata_store.detach_fact_evidence_for_paragraphs(
                        paragraph_hashes,
                        reason=str(reason or "user_delete").strip(),
                        conn=conn,
                    )
                    self.metadata_store.delete_external_memory_refs_by_paragraphs(paragraph_hashes)

                if entity_hashes:
                    changed = self.metadata_store.mark_as_deleted(
                        entity_hashes,
                        "entity",
                        reason=str(reason or "user_delete").strip(),
                    )
                    if changed != len(entity_hashes):
                        raise RuntimeError("实体删除计划在提交前已发生变化")
                    cursor.execute(
                        f"DELETE FROM paragraph_entities WHERE entity_hash IN ({','.join(['?'] * len(entity_hashes))})",
                        tuple(entity_hashes),
                    )

                if relation_hashes:
                    deleted_relations = self.metadata_store.backup_and_delete_relations(relation_hashes)
                    if deleted_relations != len(relation_hashes):
                        raise RuntimeError("关系删除计划在提交前已发生变化")

                if affected_sources:
                    self.metadata_store._enqueue_episode_source_rebuilds(
                        affected_sources,
                        reason="delete_admin_execute",
                    )

                cleanup_jobs = self._delete_cleanup_jobs(
                    paragraph_hashes=paragraph_hashes,
                    entity_hashes=entity_hashes,
                    relation_hashes=relation_hashes,
                )
                self.metadata_store.enqueue_storage_cleanup_jobs(
                    operation_id=operation_id,
                    jobs=cleanup_jobs,
                    conn=conn,
                )
                self.metadata_store.update_delete_operation_state(
                    operation_id,
                    status="pending_cleanup",
                    summary_patch={"state": "pending_cleanup", "cleanup_jobs": len(cleanup_jobs)},
                    conn=conn,
                )

            cleanup_result = await self._process_pending_storage_cleanup_jobs_serialized(
                operation_id=operation_id
            )
            result = self._build_standard_delete_result(
                mode=act_mode,
                operation_id=operation_id,
                counts=plan.get("counts", {}),
                sources=affected_sources,
                deleted_entity_count=len(entity_hashes),
                deleted_relation_count=len(relation_hashes),
                deleted_paragraph_count=len(paragraph_hashes),
                deleted_source_count=len(matched_source_tokens),
                deleted_vector_count=int(cleanup_result.get("deleted_vectors", 0) or 0),
                requested_source_count=len(requested_source_tokens),
                matched_source_count=len(matched_source_tokens),
                error=""
                if (entity_hashes or relation_hashes or paragraph_hashes or matched_source_tokens)
                else "未命中可删除内容",
            )
            result["cleanup"] = cleanup_result
            result["stale_relation_hashes"] = stale_relation_hashes
            result["skipped_due_to_concurrent_change"] = len(stale_relation_hashes)
            return result
        except Exception as exc:
            logger.warning(f"delete_admin execute 失败: {exc}")
            return self._build_standard_delete_result(mode=act_mode, operation_id=operation_id, error=str(exc))

    async def _invalidate_import_manifest_for_sources(self, result: Dict[str, Any]) -> None:
        if not isinstance(result, dict) or not result.get("success"):
            return
        manager = self.import_task_manager
        if manager is None:
            return
        sources = tokens(result.get("sources"))
        if not sources:
            return
        try:
            manifest_result = await manager.invalidate_manifest_for_sources(sources)
        except Exception as exc:
            logger.warning(f"删除来源后清理导入清单失败: sources={sources}, err={exc}")
            result["manifest_invalidation"] = {"success": False, "error": str(exc), "sources": sources}
            return
        result["manifest_invalidation"] = manifest_result

    async def _restore_delete_action(
        self,
        *,
        mode: str,
        selector: Any,
        operation_id: str = "",
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        assert self.metadata_store

        op_id = str(operation_id or "").strip()
        if op_id:
            operation = self.metadata_store.get_delete_operation(op_id)
            if operation is None:
                return {"success": False, "error": "operation 不存在"}
            return await self._restore_delete_operation(operation)

        act_mode = str(mode or "").strip().lower()
        if act_mode != "relation":
            return {"success": False, "error": "paragraph/entity/source 恢复必须提供 operation_id"}

        raw = self._selector_dict(selector)
        target = str(raw.get("query", "") or raw.get("target", "") or raw.get("hash", "") or "").strip()
        hashes = self._resolve_deleted_relation_hashes(target)
        if not hashes:
            return {"success": False, "error": "未命中可恢复关系"}
        return await self.restore_deleted_relations(
            hashes,
            requested_by=requested_by,
            reason=reason,
        )

    async def restore_deleted_relations(
        self,
        hashes: Sequence[str],
        *,
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        """通过可恢复 Outbox 恢复回收站关系及其外部投影。"""
        async with self._storage_cleanup_lock:
            return await self._restore_deleted_relations_serialized(
                hashes,
                requested_by=requested_by,
                reason=reason,
            )

    async def _restore_deleted_relations_serialized(
        self,
        hashes: Sequence[str],
        *,
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        assert self.metadata_store
        normalized = tokens(hashes)
        if not normalized:
            return {"success": False, "error": "未命中可恢复关系"}

        operation_id = f"restore_{uuid.uuid4().hex}"
        restored_hashes: List[str] = []
        failures: List[Dict[str, str]] = []
        restore_jobs: List[Dict[str, Any]] = []
        with self.metadata_store.transaction(immediate=True) as conn:
            relation_items: List[Dict[str, Any]] = []
            for hash_value in normalized:
                relation = self.metadata_store.get_deleted_relation(hash_value)
                if relation is None:
                    failures.append({"hash": hash_value, "error": "relation 不存在"})
                    continue

                snapshot_row = conn.execute(
                    """
                    SELECT payload_json
                    FROM delete_operation_items
                    WHERE item_type = 'relation' AND item_hash = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (hash_value,),
                ).fetchone()
                snapshot_payload = (
                    self.metadata_store._json_loads(snapshot_row["payload_json"], {})
                    if snapshot_row is not None
                    else {}
                )
                paragraph_hashes = merge_tokens(
                    snapshot_payload.get("paragraph_hashes"),
                    [relation.get("source_paragraph")],
                )
                relation_row = dict(relation)
                relation_row.pop("deleted_at", None)
                # SQLite 先记录投影待恢复，只有 Outbox 成功后向量状态才会回到 ready。
                relation_row.update(
                    {
                        "vector_state": "pending" if self.relation_vectors_enabled else "none",
                        "vector_error": None,
                        "vector_updated_at": None,
                    }
                )
                relation_items.append(
                    {
                        "item_type": "relation",
                        "item_hash": hash_value,
                        "item_key": hash_value,
                        "payload": {
                            "relation": relation_row,
                            "paragraph_hashes": paragraph_hashes,
                        },
                    }
                )

            if not relation_items:
                return {
                    "success": False,
                    "restored_hashes": [],
                    "restored_count": 0,
                    "failures": failures,
                    "error": "未命中可恢复关系",
                }

            restored_targets = [str(item["item_hash"]) for item in relation_items]
            superseded = self.metadata_store.supersede_delete_cleanup_resources(
                resource_type="relation",
                resource_ids=restored_targets,
                conn=conn,
            )
            if self.relation_vectors_enabled:
                restore_jobs.extend(
                    {
                        "resource_type": "relation",
                        "resource_id": str(item["item_hash"]),
                        "action": "vector_upsert",
                        "payload": {"item": item["payload"]["relation"]},
                        "expected_state": {"operation_status": "restore_pending"},
                    }
                    for item in relation_items
                )
            restore_jobs.append(
                {
                    "resource_type": "graph",
                    "resource_id": "restore",
                    "action": "graph_restore",
                    "payload": {},
                    "expected_state": {"operation_status": "restore_pending"},
                }
            )
            self.metadata_store.create_delete_operation(
                mode="relation_restore",
                selector={"hashes": normalized},
                items=relation_items,
                reason=str(reason or "relation_restore").strip(),
                requested_by=requested_by,
                status="restore_pending",
                summary={
                    "state": "restore_pending",
                    "restore_jobs": len(restore_jobs),
                    "superseded_delete_cleanup": superseded,
                },
                operation_id=operation_id,
            )
            for item in relation_items:
                hash_value = str(item["item_hash"])
                payload = item["payload"]
                relation_row = payload["relation"]
                self.metadata_store.restore_table_row_from_snapshot(
                    "relations",
                    relation_row,
                    conn=conn,
                )
                conn.execute("DELETE FROM deleted_relations WHERE hash = ?", (hash_value,))
                for paragraph_hash in tokens(payload.get("paragraph_hashes")):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO paragraph_relations (paragraph_hash, relation_hash)
                        SELECT ?, ?
                        WHERE EXISTS (
                            SELECT 1 FROM paragraphs
                            WHERE hash = ? AND (is_deleted IS NULL OR is_deleted = 0)
                        )
                        """,
                        (paragraph_hash, hash_value, paragraph_hash),
                    )
                restored_hashes.append(hash_value)
            self.metadata_store.enqueue_storage_cleanup_jobs(
                operation_id=operation_id,
                jobs=restore_jobs,
                conn=conn,
            )
            self.metadata_store.rebuild_relation_hash_aliases()

        cleanup = await self._process_pending_storage_cleanup_jobs_serialized(
            operation_id=operation_id
        )
        operation = self.metadata_store.get_delete_operation(operation_id)
        status = str((operation or {}).get("status", "") or "")
        return {
            "success": status == "restored",
            "operation_id": operation_id,
            "status": status,
            "restored_hashes": restored_hashes,
            "restored_count": len(restored_hashes),
            "failures": failures,
            "cleanup": cleanup,
        }

    async def _restore_delete_operation(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        assert self.metadata_store
        operation_id = str(operation.get("operation_id", "") or "").strip()
        if not operation_id:
            return {"success": False, "error": "operation_id 不能为空"}
        async with self._storage_cleanup_lock:
            return await self._restore_delete_operation_serialized(operation_id)

    async def _restore_delete_operation_serialized(self, operation_id: str) -> Dict[str, Any]:
        assert self.metadata_store
        # 调用方可能在等待锁期间持有旧快照，取得串行权后必须重新读取当前状态和恢复项。
        operation = self.metadata_store.get_delete_operation(operation_id)
        if operation is None:
            return {"success": False, "error": "operation 不存在"}
        operation_status = str(operation.get("status", "") or "").strip()
        if operation_status in {"restore_pending", "restored"}:
            cleanup = await self._process_pending_storage_cleanup_jobs_serialized(
                operation_id=operation_id
            )
            refreshed = self.metadata_store.get_delete_operation(operation_id) or operation
            return {
                "success": str(refreshed.get("status", "") or "") == "restored",
                "operation_id": operation_id,
                "cleanup": cleanup,
                "status": refreshed.get("status"),
            }
        if operation_status not in {"pending_cleanup", "completed"}:
            return {
                "success": False,
                "operation_id": operation_id,
                "status": operation_status,
                "error": f"operation 当前状态不允许恢复: {operation_status}",
            }

        items = operation.get("items") if isinstance(operation.get("items"), list) else []
        entity_payloads: Dict[str, Dict[str, Any]] = {}
        paragraph_payloads: Dict[str, Dict[str, Any]] = {}
        relation_payloads: Dict[str, Dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("item_type", "") or "").strip()
            item_hash = str(item.get("item_hash", "") or "").strip()
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            if item_type == "entity" and item_hash:
                entity_payloads[item_hash] = payload
            elif item_type == "paragraph" and item_hash:
                paragraph_payloads[item_hash] = payload
            elif item_type == "relation" and item_hash:
                relation_payloads[item_hash] = payload

        restored_entities: List[str] = []
        restored_paragraphs: List[str] = []
        sources = tokens(
            [
                str(((payload.get("paragraph") or {}).get("source", "") or "")).strip()
                for payload in paragraph_payloads.values()
            ]
        )
        restored_relation_hashes: List[str] = []
        restore_jobs: List[Dict[str, Any]] = []
        with self.metadata_store.transaction(immediate=True) as conn:
            self.metadata_store.cancel_delete_cleanup_jobs(operation_id, conn=conn)

            for hash_value, payload in entity_payloads.items():
                entity_row = payload.get("entity") if isinstance(payload.get("entity"), dict) else {}
                if not entity_row:
                    continue
                self.metadata_store.restore_table_row_from_snapshot("entities", entity_row, conn=conn)
                restored_entities.append(hash_value)
                restore_jobs.append(
                    {
                        "resource_type": "entity",
                        "resource_id": hash_value,
                        "action": "vector_upsert",
                        "payload": {"item": entity_row},
                        "expected_state": {"operation_status": "restore_pending"},
                    }
                )

            for hash_value, payload in paragraph_payloads.items():
                paragraph_row = payload.get("paragraph") if isinstance(payload.get("paragraph"), dict) else {}
                if not paragraph_row:
                    continue
                existing = conn.execute(
                    "SELECT is_deleted FROM paragraphs WHERE hash = ?",
                    (hash_value,),
                ).fetchone()
                was_inactive = existing is None or bool(existing["is_deleted"])
                self.metadata_store.restore_table_row_from_snapshot("paragraphs", paragraph_row, conn=conn)
                if was_inactive:
                    self.metadata_store._upsert_paragraph_ngram_if_ready(
                        hash_value,
                        str(paragraph_row.get("content", "") or ""),
                        count_delta=1,
                        conn=conn,
                    )
                    self.metadata_store.fts_upsert_tokenized_paragraph(hash_value, conn=conn)
                restored_paragraphs.append(hash_value)
                restore_jobs.append(
                    {
                        "resource_type": "paragraph",
                        "resource_id": hash_value,
                        "action": "vector_upsert",
                        "payload": {"item": paragraph_row},
                        "expected_state": {"operation_status": "restore_pending"},
                    }
                )

            for hash_value, payload in relation_payloads.items():
                relation_row = (
                    dict(payload["relation"])
                    if isinstance(payload.get("relation"), dict)
                    else {}
                )
                if not relation_row:
                    continue
                relation_row.update(
                    {
                        "vector_state": "pending" if self.relation_vectors_enabled else "none",
                        "vector_error": None,
                        "vector_updated_at": None,
                    }
                )
                self.metadata_store.restore_table_row_from_snapshot("relations", relation_row, conn=conn)
                conn.execute("DELETE FROM deleted_relations WHERE hash = ?", (hash_value,))
                restored_relation_hashes.append(hash_value)
                if self.relation_vectors_enabled:
                    restore_jobs.append(
                        {
                            "resource_type": "relation",
                            "resource_id": hash_value,
                            "action": "vector_upsert",
                            "payload": {"item": relation_row},
                            "expected_state": {"operation_status": "restore_pending"},
                        }
                    )

            for payload in entity_payloads.values():
                for link in payload.get("paragraph_links") or []:
                    paragraph_hash = str(link.get("paragraph_hash", "") or "").strip()
                    entity_hash = str(link.get("entity_hash", "") or "").strip()
                    if paragraph_hash and entity_hash:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO paragraph_entities (
                                paragraph_hash, entity_hash, mention_count
                            ) VALUES (?, ?, ?)
                            """,
                            (paragraph_hash, entity_hash, max(1, int(link.get("mention_count", 1) or 1))),
                        )
            for payload in paragraph_payloads.values():
                paragraph_hash = str((payload.get("paragraph") or {}).get("hash", "") or "").strip()
                for link in payload.get("entity_links") or []:
                    entity_hash = str(link.get("entity_hash", "") or "").strip()
                    if paragraph_hash and entity_hash:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO paragraph_entities (
                                paragraph_hash, entity_hash, mention_count
                            ) VALUES (?, ?, ?)
                            """,
                            (paragraph_hash, entity_hash, max(1, int(link.get("mention_count", 1) or 1))),
                        )
                for relation_hash in tokens(payload.get("relation_hashes")):
                    if paragraph_hash and relation_hash:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO paragraph_relations (paragraph_hash, relation_hash)
                            VALUES (?, ?)
                            """,
                            (paragraph_hash, relation_hash),
                        )
                fact_snapshot = payload.get("fact_evidence_snapshot")
                if isinstance(fact_snapshot, dict):
                    self.metadata_store.restore_fact_evidence_snapshot(
                        fact_snapshot,
                        reason="delete_operation_restored",
                        conn=conn,
                    )
                self.metadata_store.restore_external_memory_refs(list(payload.get("external_refs") or []))
            for hash_value, payload in relation_payloads.items():
                for paragraph_hash in tokens(payload.get("paragraph_hashes")):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO paragraph_relations (paragraph_hash, relation_hash)
                        VALUES (?, ?)
                        """,
                        (paragraph_hash, hash_value),
                    )

            if sources:
                self.metadata_store._enqueue_episode_source_rebuilds(sources, reason="delete_admin_restore")
            if restored_entities or restored_paragraphs or restored_relation_hashes:
                restore_jobs.append(
                    {
                        "resource_type": "graph",
                        "resource_id": "restore",
                        "action": "graph_restore",
                        "payload": {},
                        "expected_state": {"operation_status": "restore_pending"},
                    }
                )
            self.metadata_store.enqueue_storage_cleanup_jobs(
                operation_id=operation_id,
                jobs=restore_jobs,
                conn=conn,
            )
            self.metadata_store.update_delete_operation_state(
                operation_id,
                status="restore_pending",
                summary_patch={"state": "restore_pending", "restore_jobs": len(restore_jobs)},
                conn=conn,
            )
            self.metadata_store.rebuild_relation_hash_aliases()

        cleanup_result = await self._process_pending_storage_cleanup_jobs_serialized(
            operation_id=operation_id
        )
        refreshed_operation = self.metadata_store.get_delete_operation(operation_id) or operation
        summary = {
            "restored_entities": restored_entities,
            "restored_paragraphs": restored_paragraphs,
            "restored_relations": restored_relation_hashes,
            "sources": sources,
            "cleanup": cleanup_result,
        }
        return {
            "success": str(refreshed_operation.get("status", "") or "") == "restored",
            "operation_id": operation_id,
            **summary,
            "restored_relation_count": len(restored_relation_hashes),
            "relation_failures": [],
        }

    async def _purge_deleted_memory(self, *, grace_hours: Optional[float], limit: int) -> Dict[str, Any]:
        assert self.metadata_store
        orphan_cfg = self._cfg("memory.orphan", {}) or {}
        grace = (
            float(grace_hours)
            if grace_hours is not None
            else max(
                1.0,
                float(orphan_cfg.get("sweep_grace_hours", 24.0) or 24.0),
            )
        )
        cutoff = time.time() - grace * 3600.0
        relation_candidates = self.metadata_store.query(
            """
            SELECT d.hash
            FROM deleted_relations d
            WHERE d.deleted_at IS NOT NULL AND d.deleted_at < ?
            ORDER BY d.deleted_at ASC
            LIMIT ?
            """,
            (cutoff, limit),
        )
        dead_paragraphs = self.metadata_store.sweep_deleted_items("paragraph", grace * 3600.0)
        dead_entities = self.metadata_store.sweep_deleted_items("entity", grace * 3600.0)
        requested = {
            "relation": [str(item.get("hash", "") or "").strip() for item in relation_candidates],
            "paragraph": [str(item[0] or "").strip() for item in dead_paragraphs],
            "entity": [str(item[0] or "").strip() for item in dead_entities],
        }
        backed_up: Dict[str, List[str]] = {"relation": [], "paragraph": [], "entity": []}
        for item_type, hashes in requested.items():
            normalized = tokens(hashes)
            if not normalized:
                continue
            placeholders = ",".join(["?"] * len(normalized))
            rows = self.metadata_store.query(
                f"""
                SELECT DISTINCT item_hash
                FROM delete_operation_items
                WHERE item_type = ? AND item_hash IN ({placeholders})
                """,
                tuple([item_type] + normalized),
            )
            backed_up[item_type] = tokens(row.get("item_hash") for row in rows)

        paragraph_hashes = backed_up["paragraph"]
        entity_hashes = backed_up["entity"]
        deleted_relation_hashes = backed_up["relation"]
        with self.metadata_store.transaction(immediate=True):
            if paragraph_hashes:
                self.metadata_store.physically_delete_paragraphs(paragraph_hashes)
            if entity_hashes:
                self.metadata_store.physically_delete_entities(entity_hashes)
            if deleted_relation_hashes:
                placeholders = ",".join(["?"] * len(deleted_relation_hashes))
                self.metadata_store.get_connection().execute(
                    f"DELETE FROM deleted_relations WHERE hash IN ({placeholders})",
                    tuple(deleted_relation_hashes),
                )

        skipped_without_snapshot = {
            item_type: len(tokens(hashes)) - len(backed_up[item_type])
            for item_type, hashes in requested.items()
        }
        return {
            "success": True,
            "grace_hours": grace,
            "purged_deleted_relations": deleted_relation_hashes,
            "purged_paragraph_hashes": paragraph_hashes,
            "purged_entity_hashes": entity_hashes,
            "purged_counts": {
                "relations": len(deleted_relation_hashes),
                "paragraphs": len(paragraph_hashes),
                "entities": len(entity_hashes),
            },
            "skipped_without_snapshot": skipped_without_snapshot,
        }
