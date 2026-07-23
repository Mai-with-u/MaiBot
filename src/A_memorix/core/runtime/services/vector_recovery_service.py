from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import json
import os
import shutil
import time
import uuid

import numpy as np

from src.common.logger import get_logger

from ...storage import ReadOnlyVectorStoreView, VectorStoreIntegrityError
from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")


class MemoryVectorRecoveryService(KernelServiceBase):
    """在写者锁内隔离已知损坏，并维护只读旧向量复制进度。"""

    _STAGES = (
        "prepared",
        "quarantined",
        "metadata_reset",
        "new_generation_ready",
        "completed",
    )
    _KNOWN_RECOVERABLE_CODES = {
        "v1_id_set_mismatch",
        "v1_metadata_invalid",
        "v1_metadata_id_collision",
        "v1_tombstone_invalid",
        "v1_tombstone_orphaned",
        "v1_dimension_mismatch",
        "v1_fingerprint_missing",
        "v1_fingerprint_mismatch",
        "v2_dimension_mismatch",
        "v2_fingerprint_missing",
        "v2_fingerprint_mismatch",
        "v2_commit_invalid",
        "v2_commit_mismatch",
        "dual_pool_missing",
        "vector_pair_missing",
        "vector_pair_truncated",
        "vector_pair_count_mismatch",
    }
    _QUARANTINE_RETENTION_SECONDS = 7 * 24 * 60 * 60

    def _vector_recovery_journal_path(self) -> Path:
        return self.data_dir / "vector_recovery.json"

    def _vector_quarantine_root(self) -> Path:
        return self.data_dir / "vector_quarantine"

    def _read_vector_recovery_journal(self) -> Optional[Dict[str, Any]]:
        path = self._vector_recovery_journal_path()
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("向量恢复日志必须是 JSON 对象")
        return payload

    def _write_vector_recovery_journal(self, payload: Dict[str, Any]) -> None:
        path = self._vector_recovery_journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        candidate = dict(payload)
        candidate["updated_at"] = time.time()
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)

    @classmethod
    def _stage_at_least(cls, current: str, target: str) -> bool:
        try:
            return cls._STAGES.index(current) >= cls._STAGES.index(target)
        except ValueError:
            return False

    def _set_vector_health(self, **patch: Any) -> None:
        self._vector_health.update(patch)
        self._vector_health["updated_at"] = time.time()

    def _vector_health_snapshot(self) -> Dict[str, Any]:
        snapshot = dict(self._vector_health)
        snapshot["copy_progress"] = dict(snapshot.get("copy_progress") or {})
        return snapshot

    def _disable_vector_channel(self, exc: BaseException) -> None:
        """未知向量异常只关闭能力，不移动、删除或覆盖原文件。"""
        error_code = exc.error_code if isinstance(exc, VectorStoreIntegrityError) else "vector_unclassified_error"
        self.vector_store = None
        self.paragraph_vector_store = None
        self.graph_vector_store = None
        self._legacy_vector_view = None
        self._dual_vector_pools_ready = False
        self._set_runtime_capability("vector_read", False)
        self._set_runtime_capability("vector_write", False)
        self._set_vector_health(
            state="unavailable",
            error_code=error_code,
            reason=str(exc),
            recovery_stage="not_started",
            operation_id="",
            trusted_coverage=0.0,
        )
        logger.exception(f"向量通道发生未分类异常，已保持原文件并降级运行: {exc}")

    def _open_trusted_view(self, quarantine_path: Path) -> Optional[ReadOnlyVectorStoreView]:
        try:
            return ReadOnlyVectorStoreView.open_trusted_v1(
                data_dir=quarantine_path,
                dimension=self.embedding_dimension,
                expected_embedding_fingerprint=self._current_embedding_fingerprint(),
            )
        except VectorStoreIntegrityError as exc:
            logger.warning(
                "旧向量不满足只读复用条件，将保持 sparse/graph 检索: "
                f"code={exc.error_code}, error={exc}"
            )
            return None

    def _prepare_empty_dual_generation(self) -> None:
        root = self._vectors_root()
        root.mkdir(parents=True, exist_ok=True)
        self.vector_store = self._make_vector_store(root)
        self.paragraph_vector_store = self._make_vector_store(self._paragraph_vector_dir())
        self.graph_vector_store = self._make_vector_store(self._graph_vector_dir())
        self._save_vector_store(self.paragraph_vector_store)
        self._save_vector_store(self.graph_vector_store)
        empty_stats = {
            "paragraphs": {"done": 0, "failed": 0},
            "entities": {"done": 0, "failed": 0},
            "relations": {"done": 0, "failed": 0},
        }
        empty_migration = {
            "paragraphs": {"copied": 0, "encoded": 0, "missing": 0},
            "entities": {"copied": 0, "encoded": 0, "missing": 0},
            "relations": {"copied": 0, "encoded": 0, "missing": 0},
        }
        self._write_dual_vector_ready_manifest(
            stats=empty_stats,
            migration_stats=empty_migration,
            generation_reason="integrity_recovery",
        )
        self._dual_vector_pools_ready = True

    def _resume_known_vector_recovery(
        self,
        journal: Dict[str, Any],
        *,
        error: Optional[VectorStoreIntegrityError] = None,
    ) -> Dict[str, Any]:
        stage = str(journal.get("stage", "") or "")
        if stage not in self._STAGES:
            raise RuntimeError(f"向量恢复日志阶段无效: {stage or 'missing'}")
        operation_id = str(journal.get("operation_id", "") or "")
        if not operation_id:
            raise RuntimeError("向量恢复日志缺少 operation_id")

        source_path = Path(str(journal.get("source_path", self._vectors_root())))
        quarantine_path = Path(
            str(journal.get("quarantine_path", self._vector_quarantine_root() / operation_id))
        )
        if not self._stage_at_least(stage, "quarantined"):
            quarantine_path.parent.mkdir(parents=True, exist_ok=True)
            if source_path.exists() and not quarantine_path.exists():
                os.replace(source_path, quarantine_path)
            elif source_path.exists() and quarantine_path.exists():
                raise RuntimeError("向量恢复源目录和隔离目录同时存在，拒绝覆盖")
            elif not quarantine_path.exists():
                raise RuntimeError("向量恢复源目录与隔离目录均不存在")
            journal["stage"] = "quarantined"
            self._write_vector_recovery_journal(journal)
            stage = "quarantined"

        if not self._stage_at_least(stage, "metadata_reset"):
            if self.metadata_store is None:
                raise RuntimeError("重置向量派生状态时元数据库不可用")
            journal["metadata_reset"] = self.metadata_store.reset_vector_projection_state()
            journal["stage"] = "metadata_reset"
            self._write_vector_recovery_journal(journal)
            stage = "metadata_reset"

        if not self._stage_at_least(stage, "new_generation_ready"):
            self._prepare_empty_dual_generation()
            journal["stage"] = "new_generation_ready"
            self._write_vector_recovery_journal(journal)
            stage = "new_generation_ready"

        legacy_view = self._open_trusted_view(quarantine_path)
        self._legacy_vector_view = legacy_view
        report = legacy_view.report.to_dict() if legacy_view is not None else {}
        if not self._stage_at_least(stage, "completed"):
            journal["trusted_view"] = report
            journal["copy"] = {
                "state": "pending" if legacy_view is not None else "skipped",
                "cursor": "",
                "processed": 0,
                "copied": 0,
                "total": int(report.get("trusted_count", 0) or 0),
            }
            journal["stage"] = "completed"
            journal["completed_at"] = time.time()
            self._write_vector_recovery_journal(journal)

        self._set_runtime_capability("vector_write", True)
        self._set_runtime_capability("vector_read", True)
        self._set_vector_health(
            state="recovering" if legacy_view is not None else "degraded",
            error_code=(error.error_code if error is not None else str(journal.get("error_code", ""))),
            reason=(str(error) if error is not None else str(journal.get("reason", ""))),
            trusted_coverage=float(report.get("coverage", 0.0) or 0.0),
            recovery_stage="completed",
            operation_id=operation_id,
            copy_progress=dict(journal.get("copy") or {}),
        )
        return journal

    def _recover_known_vector_failure(self, error: VectorStoreIntegrityError) -> bool:
        if error.error_code not in self._KNOWN_RECOVERABLE_CODES:
            return False
        journal = self._read_vector_recovery_journal()
        if journal is None or str(journal.get("stage", "")) == "completed":
            operation_id = f"{int(time.time())}-{uuid.uuid4().hex[:12]}"
            journal = {
                "version": 1,
                "operation_id": operation_id,
                "stage": "prepared",
                "source_path": str(self._vectors_root()),
                "quarantine_path": str(self._vector_quarantine_root() / operation_id),
                "created_at": time.time(),
                **error.to_dict(),
            }
            self._write_vector_recovery_journal(journal)
        self._resume_known_vector_recovery(journal, error=error)
        logger.warning(
            "已隔离损坏向量并切换到空 V2 双池: "
            f"operation_id={journal.get('operation_id')}, code={error.error_code}"
        )
        return True

    def _resume_vector_recovery_if_needed(self) -> None:
        journal = self._read_vector_recovery_journal()
        if journal is None:
            return
        stage = str(journal.get("stage", "") or "")
        if stage != "completed":
            self._resume_known_vector_recovery(journal)
            return
        quarantine_path = Path(str(journal.get("quarantine_path", "") or ""))
        copy_state = str((journal.get("copy") or {}).get("state", "") or "")
        if copy_state not in {"completed", "skipped"} and quarantine_path.exists():
            self._legacy_vector_view = self._open_trusted_view(quarantine_path)
        report = dict(journal.get("trusted_view") or {})
        self._set_vector_health(
            state="recovering" if self._legacy_vector_view is not None else "degraded",
            error_code=str(journal.get("error_code", "") or ""),
            reason=str(journal.get("reason", "") or ""),
            trusted_coverage=float(report.get("coverage", 0.0) or 0.0),
            recovery_stage="completed",
            operation_id=str(journal.get("operation_id", "") or ""),
            copy_progress=dict(journal.get("copy") or {}),
        )

    def _legacy_copy_targets(self) -> List[Tuple[str, str, str]]:
        if self.metadata_store is None or self._legacy_vector_view is None:
            return []
        rows: List[Tuple[str, str, str]] = []
        for item_type, table in (("paragraph", "paragraphs"), ("entity", "entities"), ("relation", "relations")):
            active_filter = self._active_row_filter_sql(table)
            for row in self.metadata_store.query(
                f"SELECT hash FROM {table} WHERE {active_filter} ORDER BY hash ASC"
            ):
                hash_value = str(row.get("hash", "") or "").strip()
                if hash_value and hash_value in self._legacy_vector_view:
                    rows.append((item_type, hash_value, f"{item_type}:{hash_value}"))

        owner_counts: Dict[str, int] = {}
        for _item_type, source_id, _target_id in rows:
            owner_counts[source_id] = owner_counts.get(source_id, 0) + 1
        return sorted(
            (
                (item_type, source_id, target_id)
                for item_type, source_id, target_id in rows
                if owner_counts[source_id] == 1
            ),
            key=lambda item: (item[0], item[1]),
        )

    def _copy_legacy_vectors_once(self, *, batch_size: int = 256) -> Dict[str, Any]:
        journal = self._read_vector_recovery_journal()
        if journal is None or self._legacy_vector_view is None:
            return {"success": True, "processed": 0, "copied": 0, "done": True}
        if self.paragraph_vector_store is None or self.graph_vector_store is None:
            return {"success": False, "processed": 0, "copied": 0, "done": False}

        targets = self._legacy_copy_targets()
        copy_state = dict(journal.get("copy") or {})
        cursor = str(copy_state.get("cursor", "") or "")
        remaining = [item for item in targets if f"{item[0]}:{item[1]}" > cursor]
        batch = remaining[: max(1, int(batch_size))]
        if not batch:
            copy_state["state"] = "completed"
            copy_state["finished_at"] = time.time()
            copy_state["total"] = len(targets)
            journal["copy"] = copy_state
            self._write_vector_recovery_journal(journal)
            self._legacy_vector_view = None
            self._set_vector_health(
                state="recovered",
                copy_progress=copy_state,
                trusted_coverage=float((journal.get("trusted_view") or {}).get("coverage", 0.0) or 0.0),
            )
            return {"success": True, "processed": 0, "copied": 0, "done": True}

        source_ids = [source_id for _item_type, source_id, _target_id in batch]
        source_vectors = self._legacy_vector_view.get_vectors(source_ids)
        copied = 0
        copied_relations: List[str] = []
        for item_type, source_id, target_id in batch:
            vector = source_vectors.get(source_id)
            if vector is None:
                continue
            target_store = self.paragraph_vector_store if item_type == "paragraph" else self.graph_vector_store
            effective_target_id = source_id if item_type == "paragraph" else target_id
            if effective_target_id not in target_store:
                target_store.add(
                    vectors=np.asarray(vector, dtype=np.float32).reshape(1, -1),
                    ids=[effective_target_id],
                )
            copied += 1
            if item_type == "relation":
                copied_relations.append(source_id)

        self._save_vector_store(self.paragraph_vector_store)
        self._save_vector_store(self.graph_vector_store)
        self._refresh_dual_vector_ready_manifest_from_stores()
        for relation_hash in copied_relations:
            self.metadata_store.set_relation_vector_state(relation_hash, "ready")
        copy_state.update(
            {
                "state": "completed" if len(batch) == len(remaining) else "running",
                "cursor": f"{batch[-1][0]}:{batch[-1][1]}",
                "processed": int(copy_state.get("processed", 0) or 0) + len(batch),
                "copied": int(copy_state.get("copied", 0) or 0) + copied,
                "total": len(targets),
            }
        )
        if copy_state["state"] == "completed":
            copy_state["finished_at"] = time.time()
        journal["copy"] = copy_state
        self._write_vector_recovery_journal(journal)
        completed = copy_state["state"] == "completed"
        if completed:
            self._legacy_vector_view = None
        self._set_vector_health(
            state="recovered" if completed else "recovering",
            copy_progress=copy_state,
        )
        return {
            "success": True,
            "processed": len(batch),
            "copied": copied,
            "done": completed,
        }

    def _cleanup_vector_quarantine(self) -> None:
        root = self._vector_quarantine_root()
        if not root.exists():
            return
        journal = self._read_vector_recovery_journal() or {}
        active_operation = str(journal.get("operation_id", "") or "")
        active_copy_state = str((journal.get("copy") or {}).get("state", "") or "")
        directories = sorted(
            (path for path in root.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        now = time.time()
        for index, path in enumerate(directories):
            if index == 0:
                continue
            if path.name == active_operation and active_copy_state not in {"completed", "skipped"}:
                continue
            if now - path.stat().st_mtime < self._QUARANTINE_RETENTION_SECONDS:
                continue
            shutil.rmtree(path)
