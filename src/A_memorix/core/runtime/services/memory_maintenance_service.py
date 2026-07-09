from __future__ import annotations

from typing import List

import asyncio
import time

from src.common.logger import get_logger

from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")


class MemoryMaintenanceService(KernelServiceBase):
    async def _memory_maintenance_loop(self) -> None:
        try:
            while not self._background_stopping:
                interval_hours = max(1.0 / 60.0, float(self._cfg("memory.base_decay_interval_hours", 1.0) or 1.0))
                await asyncio.sleep(max(60.0, interval_hours * 3600.0))
                if self._background_stopping:
                    break
                if not bool(self._cfg("memory.enabled", True)):
                    continue
                await self._run_memory_maintenance_cycle(interval_hours=interval_hours)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"memory_maintenance loop 异常: {exc}")

    async def _run_memory_maintenance_cycle(self, *, interval_hours: float) -> None:
        assert self.graph_store is not None
        assert self.metadata_store is not None
        half_life = float(self._cfg("memory.half_life_hours", 24.0) or 24.0)
        if half_life > 0:
            factor = 0.5 ** (float(interval_hours) / half_life)
            self.graph_store.decay(factor)

        await self._process_freeze_and_prune()
        await self._orphan_gc_phase()
        self._last_maintenance_at = time.time()
        self._persist()

    async def _process_freeze_and_prune(self) -> None:
        assert self.metadata_store is not None
        assert self.graph_store is not None
        prune_threshold = max(0.0, float(self._cfg("memory.prune_threshold", 0.1) or 0.1))
        freeze_duration = max(0.0, float(self._cfg("memory.freeze_duration_hours", 24.0) or 24.0)) * 3600.0
        now = time.time()

        low_edges = self.graph_store.get_low_weight_edges(prune_threshold)
        hashes_to_freeze: List[str] = []
        edges_to_deactivate: List[tuple[str, str]] = []
        for src, tgt in low_edges:
            relation_hashes = list(self.graph_store.get_relation_hashes_for_edge(src, tgt))
            if not relation_hashes:
                continue
            statuses = self.metadata_store.get_relation_status_batch(relation_hashes)
            current_hashes: List[str] = []
            protected = False
            for hash_value, status in statuses.items():
                if bool(status.get("is_pinned")) or float(status.get("protected_until") or 0.0) > now:
                    protected = True
                    break
                current_hashes.append(hash_value)
            if protected or not current_hashes:
                continue
            hashes_to_freeze.extend(current_hashes)
            edges_to_deactivate.append((src, tgt))

        if hashes_to_freeze:
            self.metadata_store.mark_relations_inactive(hashes_to_freeze, inactive_since=now)
            self.graph_store.deactivate_edges(edges_to_deactivate)

        cutoff = now - freeze_duration
        expired_hashes = self.metadata_store.get_prune_candidates(cutoff)
        if not expired_hashes:
            return
        relation_info = self.metadata_store.get_relations_subject_object_map(expired_hashes)
        operations = [(src, tgt, hash_value) for hash_value, (src, tgt) in relation_info.items()]
        if operations:
            self.graph_store.prune_relation_hashes(operations)
        deleted_hashes = [hash_value for hash_value in expired_hashes if hash_value in relation_info]
        if deleted_hashes:
            self.metadata_store.backup_and_delete_relations(deleted_hashes)
            self._delete_vectors_by_type(relation_hashes=deleted_hashes)

    async def _orphan_gc_phase(self) -> None:
        assert self.metadata_store is not None
        assert self.graph_store is not None
        orphan_cfg = self._cfg("memory.orphan", {}) or {}
        if not bool(orphan_cfg.get("enable_soft_delete", True)):
            return
        entity_retention = max(0.0, float(orphan_cfg.get("entity_retention_days", 7.0) or 7.0)) * 86400.0
        paragraph_retention = max(0.0, float(orphan_cfg.get("paragraph_retention_days", 7.0) or 7.0)) * 86400.0
        grace_period = max(0.0, float(orphan_cfg.get("sweep_grace_hours", 24.0) or 24.0)) * 3600.0

        isolated = self.graph_store.get_isolated_nodes(include_inactive=True)
        if isolated:
            entity_hashes = self.metadata_store.get_entity_gc_candidates(isolated, retention_seconds=entity_retention)
            if entity_hashes:
                self.metadata_store.mark_as_deleted(entity_hashes, "entity")

        paragraph_hashes = self.metadata_store.get_paragraph_gc_candidates(retention_seconds=paragraph_retention)
        if paragraph_hashes:
            self.metadata_store.mark_as_deleted(paragraph_hashes, "paragraph")

        dead_paragraphs = self.metadata_store.sweep_deleted_items("paragraph", grace_period)
        if dead_paragraphs:
            hashes = [str(item[0] or "").strip() for item in dead_paragraphs if item and str(item[0] or "").strip()]
            if hashes:
                self.metadata_store.physically_delete_paragraphs(hashes)
                self._delete_vectors_by_type(paragraph_hashes=hashes)

        dead_entities = self.metadata_store.sweep_deleted_items("entity", grace_period)
        if dead_entities:
            entity_hashes = [str(item[0] or "").strip() for item in dead_entities if item and str(item[0] or "").strip()]
            entity_names = [str(item[1] or "").strip() for item in dead_entities if item and str(item[1] or "").strip()]
            if entity_names:
                self.graph_store.delete_nodes(entity_names)
            if entity_hashes:
                self.metadata_store.physically_delete_entities(entity_hashes)
                self._delete_vectors_by_type(entity_hashes=entity_hashes)
