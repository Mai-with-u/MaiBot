from __future__ import annotations

from typing import Any, Dict, List

import asyncio
import time

from src.common.logger import get_logger

from ...utils.memory_lifecycle_policy import (
    RelationLifecycleEvent,
    RelationLifecyclePolicy,
)
from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")

MAX_PROJECTION_RECONCILE_ROUNDS = 3


class MemoryMaintenanceService(KernelServiceBase):
    async def _memory_maintenance_loop(self) -> None:
        while not self._background_stopping:
            interval_value = self._cfg("memory.base_decay_interval_hours", 1.0)
            interval_hours = max(1.0 / 60.0, float(1.0 if interval_value is None else interval_value))
            try:
                await asyncio.sleep(max(60.0, interval_hours * 3600.0))
                if self._background_stopping:
                    break
                if not bool(self._cfg("memory.enabled", True)):
                    continue
                await self._run_memory_maintenance_cycle(interval_hours=interval_hours)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"memory_maintenance cycle 异常，将在下一周期重试: {exc}")

    async def _run_memory_maintenance_cycle(self, *, interval_hours: float) -> None:
        del interval_hours

        await self._process_freeze_and_prune()
        await self._orphan_gc_phase()
        self._last_maintenance_at = time.time()
        self._persist()

    def _relation_lifecycle_policy(self) -> RelationLifecyclePolicy:
        freeze_threshold = float(self._cfg("memory.prune_threshold", 0.1))
        return RelationLifecyclePolicy(
            half_life_hours=float(self._cfg("memory.half_life_hours", 24.0)),
            freeze_threshold=freeze_threshold,
            revive_threshold=float(self._cfg("memory.revive_threshold", freeze_threshold * 1.5)),
            access_alpha=float(self._cfg("memory.access_reinforcement_alpha", 0.05)),
            access_cooldown_seconds=(
                float(self._cfg("memory.access_reinforcement_cooldown_minutes", 60.0)) * 60.0
            ),
            reinforce_alpha=float(self._cfg("memory.explicit_reinforcement_alpha", 0.5)),
            weaken_alpha=float(self._cfg("memory.weaken_alpha", 0.5)),
        )

    def _reconcile_relation_graph_projection_jobs(
        self,
        *,
        reset_leases: bool = False,
        batch_size: int | None = None,
    ) -> Dict[str, int]:
        """把 metadata 权威活跃态幂等投影到持久化图快照。"""

        with self._relation_graph_projection_lock:
            return self._reconcile_relation_graph_projection_jobs_locked(
                reset_leases=reset_leases,
                batch_size=batch_size,
            )

    @staticmethod
    def _projection_authority_key(item: Dict[str, Any]) -> tuple[str, int, str]:
        return (
            str(item.get("relation_hash", "") or ""),
            int(item.get("job_revision", 0) or 0),
            str(item.get("lease_token", "") or ""),
        )

    @classmethod
    def _projection_authority_signature(cls, item: Dict[str, Any]) -> tuple[Any, ...]:
        return (
            *cls._projection_authority_key(item),
            str(item.get("subject", "") or ""),
            str(item.get("object", "") or ""),
            bool(item.get("authoritative_active")),
        )

    def _claim_all_relation_graph_projection_jobs(self, *, batch_size: int) -> List[Dict[str, Any]]:
        assert self.metadata_store is not None
        target_count = self.metadata_store.count_claimable_relation_graph_projection_jobs()
        claimed: List[Dict[str, Any]] = []
        while len(claimed) < target_count:
            batch = self.metadata_store.claim_relation_graph_projection_jobs(
                limit=min(batch_size, target_count - len(claimed)),
                lease_seconds=300.0,
            )
            if not batch:
                break
            claimed.extend(batch)
        return claimed

    def _fail_unauthorized_projection_jobs(
        self,
        claimed: List[Dict[str, Any]],
        authorized: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        assert self.metadata_store is not None
        authorized_keys = {
            self._projection_authority_key(item)
            for item in authorized
        }
        unauthorized = [
            item
            for item in claimed
            if self._projection_authority_key(item) not in authorized_keys
        ]
        if unauthorized:
            self.metadata_store.fail_relation_graph_projection_jobs(
                unauthorized,
                error="关系图投影任务授权失效",
            )
        return unauthorized

    def _apply_authorized_graph_projection(self, authorized: List[Dict[str, Any]]) -> None:
        assert self.graph_store is not None
        active = [item for item in authorized if bool(item.get("authoritative_active"))]
        inactive = [item for item in authorized if not bool(item.get("authoritative_active"))]
        if active:
            self.graph_store.add_edges(
                [(str(item["subject"]), str(item["object"])) for item in active],
                relation_hashes=[str(item["relation_hash"]) for item in active],
            )
        if inactive:
            self.graph_store.prune_relation_hashes(
                [
                    (
                        str(item["subject"]),
                        str(item["object"]),
                        str(item["relation_hash"]),
                    )
                    for item in inactive
                ]
            )

    def _reconcile_relation_graph_projection_jobs_locked(
        self,
        *,
        reset_leases: bool,
        batch_size: int | None,
    ) -> Dict[str, int]:
        """在 SDK 实例写锁内聚合任务，只发布一次稳定整图快照。"""

        assert self.metadata_store is not None
        assert self.graph_store is not None
        if reset_leases:
            self.metadata_store.reset_relation_graph_projection_leases()
        limit = int(batch_size or self._cfg("memory.lifecycle_batch_size", 1000))
        if limit <= 0:
            raise ValueError("关系图投影 batch_size 必须大于0")

        claimed_count = 0
        completed_count = 0
        saved_batches = 0
        force_full_rebuild = bool(reset_leases)
        for reconcile_round in range(MAX_PROJECTION_RECONCILE_ROUNDS):
            claimed = self._claim_all_relation_graph_projection_jobs(batch_size=limit)
            if not claimed:
                if force_full_rebuild:
                    # 启动时即使队列为空，也不能把旧磁盘快照当作活跃态事实源。
                    self._graph_admin_service._rebuild_graph_from_metadata()
                    self.graph_store.save()
                    saved_batches += 1
                break
            claimed_count += len(claimed)
            initial_authorized = self.metadata_store.authorize_relation_graph_projection_jobs(
                claimed
            )
            unauthorized = self._fail_unauthorized_projection_jobs(
                claimed,
                initial_authorized,
            )
            authorized = self.metadata_store.authorize_relation_graph_projection_jobs(
                initial_authorized
            )
            unauthorized.extend(
                self._fail_unauthorized_projection_jobs(initial_authorized, authorized)
            )
            if not authorized:
                if force_full_rebuild:
                    self._graph_admin_service._rebuild_graph_from_metadata()
                    self.graph_store.save()
                    saved_batches += 1
                break
            try:
                if force_full_rebuild:
                    self._graph_admin_service._rebuild_graph_from_metadata()
                else:
                    self._apply_authorized_graph_projection(authorized)

                # 图已在内存中变更，发布快照前再次验证同一租约和任务版本。
                before_save = self.metadata_store.authorize_relation_graph_projection_jobs(
                    authorized
                )
                before_signatures = {
                    self._projection_authority_signature(item) for item in before_save
                }
                authorized_signatures = {
                    self._projection_authority_signature(item) for item in authorized
                }
                if before_signatures != authorized_signatures:
                    self.metadata_store.reenqueue_authoritative_relation_graph_projection_jobs(
                        authorized
                    )
                    force_full_rebuild = True
                    if reconcile_round + 1 >= MAX_PROJECTION_RECONCILE_ROUNDS:
                        # 当前轮已经改过内存图。超限返回前先恢复到此刻的权威态，
                        # durable intent 保留给下一周期处理磁盘发布。
                        self._graph_admin_service._rebuild_graph_from_metadata()
                        raise RuntimeError("关系图投影在保存前持续变化，已保留任务等待重试")
                    continue
                self.graph_store.save()
                saved_batches += 1
            except Exception as exc:
                self.metadata_store.fail_relation_graph_projection_jobs(
                    authorized,
                    error=str(exc),
                )
                raise
            completed = self.metadata_store.complete_relation_graph_projection_jobs(authorized)
            completed_count += completed
            if completed != len(authorized):
                # save 已成功但 CAS 失败时，必须先恢复 durable intent。下一轮会
                # 从 metadata 全量重建、保存并重放，不能留下“空队列+错图”。
                self.metadata_store.reenqueue_authoritative_relation_graph_projection_jobs(
                    authorized
                )
                force_full_rebuild = True
                if reconcile_round + 1 >= MAX_PROJECTION_RECONCILE_ROUNDS:
                    # 最后一轮的旧快照已经落盘，必须在抛错前补发一次权威
                    # 全图快照；重入任务仍保留，覆盖恢复保存期间的新变化。
                    self._graph_admin_service._rebuild_graph_from_metadata()
                    self.graph_store.save()
                    saved_batches += 1
                    raise RuntimeError("关系图投影 CAS 持续冲突，已保留任务等待重试")
                continue
            force_full_rebuild = False
            if unauthorized:
                break
        else:
            if self.metadata_store.count_claimable_relation_graph_projection_jobs() > 0:
                raise RuntimeError("关系图投影单轮任务持续增长，已保留剩余任务等待重试")
        return {
            "claimed": claimed_count,
            "completed": completed_count,
            "saved_batches": saved_batches,
        }

    def apply_relation_lifecycle_event(
        self,
        hashes: List[str],
        *,
        event: RelationLifecycleEvent,
        strength: float = 1.0,
        now: float | None = None,
    ) -> List[Dict[str, Any]]:
        """应用关系级事件，并把状态变化精确同步到结构图。"""
        assert self.metadata_store is not None
        assert self.graph_store is not None
        event_time = time.time() if now is None else float(now)
        transitions = self.metadata_store.apply_relation_lifecycle_event(
            hashes,
            event=event,
            policy=self._relation_lifecycle_policy(),
            now=event_time,
            strength=strength,
        )

        if any(bool(item.get("was_inactive")) != bool(item.get("is_inactive")) for item in transitions):
            self._reconcile_relation_graph_projection_jobs()
        return transitions

    async def _process_freeze_and_prune(self) -> None:
        assert self.metadata_store is not None
        assert self.graph_store is not None
        policy = self._relation_lifecycle_policy()
        freeze_duration = float(self._cfg("memory.freeze_duration_hours", 24.0)) * 3600.0
        if freeze_duration < 0.0:
            raise ValueError("memory.freeze_duration_hours 不能小于0")
        now = time.time()

        batch_size = int(self._cfg("memory.lifecycle_batch_size", 1000))
        if batch_size <= 0:
            raise ValueError("memory.lifecycle_batch_size 必须大于0")
        while True:
            evaluated = self.metadata_store.evaluate_due_relation_lifecycles(
                policy=policy,
                now=now,
                limit=batch_size,
            )
            current_frozen = list(evaluated.get("frozen", []))
            current_scheduled = list(evaluated.get("scheduled", []))
            if len(current_frozen) + len(current_scheduled) < batch_size:
                break

        self._reconcile_relation_graph_projection_jobs(batch_size=batch_size)

        cutoff = now - freeze_duration
        expired_rows = self.metadata_store.get_decay_prune_candidate_rows(
            cutoff_time=cutoff,
            now=now,
            policy=policy,
            limit=batch_size,
        )
        if not expired_rows:
            return
        deleted_hashes = [str(item["hash"]) for item in expired_rows]
        if deleted_hashes:
            expected_relation_states = {
                str(item["hash"]): {
                    key: item[key]
                    for key in (
                        "expected_lifecycle_revision",
                        "expected_retention_strength",
                        "expected_retention_anchor_at",
                        "expected_inactive_since",
                        "expected_inactive_reason",
                        "expected_is_inactive",
                        "expected_is_permanent",
                        "expected_is_pinned",
                        "expected_protected_until",
                    )
                }
                for item in expired_rows
            }
            result = await self._delete_admin_service._execute_delete_action(
                mode="relation",
                selector={
                    "hashes": deleted_hashes,
                    "expected_relation_states": expected_relation_states,
                },
                requested_by="memory_lifecycle",
                reason="lifecycle_decay_archive",
            )
            if not result.get("success", False):
                raise RuntimeError(str(result.get("error", "关系生命周期归档失败") or "关系生命周期归档失败"))

    async def _orphan_gc_phase(self) -> None:
        assert self.metadata_store is not None
        assert self.graph_store is not None
        orphan_cfg = self._cfg("memory.orphan", {}) or {}
        if not bool(orphan_cfg.get("enable_soft_delete", True)):
            return
        entity_retention = max(0.0, float(orphan_cfg.get("entity_retention_days", 7.0))) * 86400.0
        grace_hours = max(0.0, float(orphan_cfg.get("sweep_grace_hours", 24.0)))

        isolated = self.graph_store.get_isolated_nodes(include_inactive=True)
        if isolated:
            entity_hashes = self.metadata_store.get_entity_gc_candidates(isolated, retention_seconds=entity_retention)
            if entity_hashes:
                result = await self._delete_admin_service._execute_delete_action(
                    mode="entity",
                    selector={"hashes": entity_hashes},
                    requested_by="memory_maintenance",
                    reason="entity_orphan_expired",
                )
                if not result.get("success", False):
                    raise RuntimeError(str(result.get("error", "实体回收失败") or "实体回收失败"))

        paragraph_hashes = self.metadata_store.get_expired_paragraph_hashes(limit=1000)
        if paragraph_hashes:
            result = await self._delete_admin_service._execute_delete_action(
                mode="paragraph",
                selector={"hashes": paragraph_hashes},
                requested_by="memory_maintenance",
                reason="paragraph_explicit_expiration",
            )
            if not result.get("success", False):
                raise RuntimeError(str(result.get("error", "段落显式过期失败") or "段落显式过期失败"))

        await self._delete_admin_service._purge_deleted_memory(
            grace_hours=grace_hours,
            limit=1000,
        )
