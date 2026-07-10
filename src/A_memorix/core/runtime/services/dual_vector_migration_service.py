from __future__ import annotations

from typing import Any, Dict, Optional

import time

from .base import KernelServiceBase


class MemoryDualVectorMigrationService(KernelServiceBase):
    def _should_start_dual_vector_auto_migration(self) -> bool:
        return (
            self._dual_vector_pools_config_enabled()
            and not self._dual_vector_pools_enabled()
            and not self._dual_vector_auto_migration_attempted
            and not self._background_stopping
        )

    def _normalize_dual_vector_auto_migration_progress(
        self,
        progress: Optional[Dict[str, Any]] = None,
        *,
        now: Optional[float] = None,
        explicit_processed: bool = False,
        completed: bool = False,
        success: bool = False,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = dict(progress or {})
        now_ts = float(now if now is not None else time.time())
        started_at = self._dual_vector_auto_migration_status.get("started_at")
        elapsed_seconds = 0.0
        if isinstance(started_at, (int, float)):
            elapsed_seconds = max(0.0, now_ts - float(started_at))

        def _coerce_non_negative_int(value: Any, default: int = 0) -> int:
            try:
                number = int(float(value))
            except (TypeError, ValueError):
                return default
            return max(0, number)

        total = _coerce_non_negative_int(payload.get("total"), 0)
        if total <= 0:
            counts = payload.get("counts")
            if isinstance(counts, dict):
                total = sum(
                    _coerce_non_negative_int(counts.get(key), 0)
                    for key in ("paragraphs", "entities", "relations")
                )

        processed_keys = (
            "paragraph_done",
            "paragraph_failed",
            "entity_done",
            "entity_failed",
            "relation_done",
            "relation_failed",
        )
        if explicit_processed:
            processed = _coerce_non_negative_int(payload.get("processed"), 0)
        elif any(key in payload for key in processed_keys):
            processed = sum(_coerce_non_negative_int(payload.get(key), 0) for key in processed_keys)
        else:
            processed = _coerce_non_negative_int(payload.get("processed"), 0)
        if total > 0:
            processed = min(processed, total)

        if completed and success:
            if total > 0:
                processed = total
            percent = 100.0
        elif total > 0:
            percent = min(99.5, max(0.0, (float(processed) / float(total)) * 100.0))
        else:
            percent = 0.0

        estimated_remaining_seconds: Optional[int] = None
        if not completed and total > 0 and 0 < processed < total and elapsed_seconds > 0.0:
            rate = float(processed) / elapsed_seconds
            if rate > 0.0:
                remaining = (float(total) - float(processed)) / rate
                estimated_remaining_seconds = max(0, int(remaining + 0.999))

        payload.update(
            {
                "total": int(total),
                "processed": int(processed),
                "percent": round(percent, 2),
                "elapsed_seconds": round(elapsed_seconds, 3),
                "estimated_remaining_seconds": estimated_remaining_seconds,
            }
        )
        return payload

    def _update_dual_vector_auto_migration_stage(self, stage: str, **progress: Any) -> None:
        if not bool(self._dual_vector_auto_migration_status.get("running", False)):
            return
        now_ts = time.time()
        explicit_processed = "processed" in progress
        payload = dict(self._dual_vector_auto_migration_status.get("progress") or {})
        payload.update(progress)
        payload = self._normalize_dual_vector_auto_migration_progress(
            payload,
            now=now_ts,
            explicit_processed=explicit_processed,
        )
        self._dual_vector_auto_migration_status.update(
            {
                "stage": str(stage or "unknown"),
                "progress": payload,
                "updated_at": now_ts,
            }
        )
