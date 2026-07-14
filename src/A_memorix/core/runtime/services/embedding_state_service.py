from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import json
import time

from src.common.logger import get_logger

from ...storage import VectorStore
from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")


class MemoryEmbeddingStateService(KernelServiceBase):
    def _stored_vector_dimension(self, store: Optional[VectorStore] = None) -> Optional[int]:
        ready_manifest = (
            self._read_dual_vector_ready_manifest()
            if store is None and self._dual_vector_pools_config_enabled()
            else None
        )
        if ready_manifest is not None:
            try:
                manifest_dimension = int(ready_manifest.get("dimension") or 0)
            except Exception:
                manifest_dimension = 0
            if manifest_dimension > 0:
                return manifest_dimension
        vector_dir = Path(store.data_dir) if store is not None and store.data_dir is not None else self._vectors_root()
        meta_path = vector_dir / "vectors_metadata.json"
        if not meta_path.exists():
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
        except Exception as exc:
            logger.warning(f"读取向量元数据失败，将回退到 runtime self-check: {exc}")
            return None
        try:
            value = int(meta.get("dimension") or 0)
        except Exception:
            return None
        return value if value > 0 else None

    @staticmethod
    def _normalize_embedding_fingerprint(value: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(value, dict):
            return None
        hash_value = str(value.get("hash", "") or "").strip()
        if not hash_value:
            return None
        payload = dict(value)
        payload["hash"] = hash_value
        return payload

    def _current_embedding_status_dimension(self) -> int:
        manager = self.embedding_manager
        getter = getattr(manager, "get_requested_dimension", None)
        if callable(getter):
            try:
                requested_dimension = int(getter())
            except Exception:
                requested_dimension = 0
            if requested_dimension > 0:
                return requested_dimension
        try:
            default_dimension = int(getattr(manager, "default_dimension", 0) or 0)
        except Exception:
            default_dimension = 0
        if default_dimension > 0:
            return default_dimension
        return max(1, int(self._cfg("embedding.dimension", self.embedding_dimension) or self.embedding_dimension))

    def _current_embedding_fingerprint(self, *, dimension: Optional[int] = None) -> Optional[Dict[str, Any]]:
        manager = self.embedding_manager
        getter = getattr(manager, "get_embedding_fingerprint", None)
        if not callable(getter):
            return None
        try:
            effective_dimension = int(dimension or self._current_embedding_status_dimension())
            return self._normalize_embedding_fingerprint(getter(dimension=effective_dimension))
        except Exception as exc:
            logger.warning(f"生成 embedding 指纹失败: {exc}")
            return None

    def _stored_embedding_fingerprint(self, store: Optional[VectorStore] = None) -> Optional[Dict[str, Any]]:
        ready_manifest = (
            self._read_dual_vector_ready_manifest()
            if store is None and self._dual_vector_pools_config_enabled()
            else None
        )
        if ready_manifest is not None:
            manifest_fingerprint = self._normalize_embedding_fingerprint(ready_manifest.get("embedding_fingerprint"))
            if manifest_fingerprint is not None:
                return manifest_fingerprint

        vector_dir = Path(store.data_dir) if store is not None and store.data_dir is not None else self._vectors_root()
        meta_path = vector_dir / "vectors_metadata.json"
        if not meta_path.exists():
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
        except Exception as exc:
            logger.warning(f"读取向量指纹元数据失败: {exc}")
            return None
        if not isinstance(meta, dict):
            return None
        return self._normalize_embedding_fingerprint(meta.get("embedding_fingerprint"))

    def _stamp_missing_embedding_fingerprint_if_dimension_matches(self, store: Optional[VectorStore]) -> bool:
        if store is None:
            return False
        stored_dimension = self._stored_vector_dimension(store)
        current_dimension = self._current_embedding_status_dimension()
        if stored_dimension is None or int(stored_dimension) != int(current_dimension):
            return False
        current_fingerprint = self._current_embedding_fingerprint(dimension=current_dimension)
        if current_fingerprint is None:
            return False
        stored_fingerprint = self._stored_embedding_fingerprint(store)
        if stored_fingerprint is not None:
            return False
        store.save(embedding_fingerprint=current_fingerprint)
        logger.warning("旧向量库缺少 embedding 指纹且维度匹配，已写入当前模型指纹以复用旧向量")
        stamped_fingerprint = self._stored_embedding_fingerprint(store)
        return stamped_fingerprint is not None and str(stamped_fingerprint.get("hash", "") or "") == str(
            current_fingerprint.get("hash", "") or ""
        )

    @staticmethod
    def _embedding_fingerprint_status(
        current: Optional[Dict[str, Any]],
        stored: Optional[Dict[str, Any]],
        *,
        has_stored_vectors: bool,
    ) -> str:
        if not has_stored_vectors:
            return "none"
        if current is None:
            return "unknown"
        if stored is None:
            return "missing"
        return "matched" if str(current.get("hash", "")) == str(stored.get("hash", "")) else "mismatched"

    def _stored_vectors_compatible_with_current_embedding(self, store: Optional[VectorStore] = None) -> bool:
        current = self._current_embedding_fingerprint()
        stored = self._stored_embedding_fingerprint(store)
        if current is None:
            return False
        if stored is None:
            stamped = self._stamp_missing_embedding_fingerprint_if_dimension_matches(store or self.vector_store)
            if not stamped:
                return False
            stored = self._stored_embedding_fingerprint(store)
            if stored is None:
                return False
        return str(current.get("hash", "") or "") == str(stored.get("hash", "") or "")

    def _vector_mismatch_error(self, *, stored_dimension: int, detected_dimension: int) -> str:
        return (
            "检测到现有向量库与当前 embedding 输出维度不一致："
            f"stored={stored_dimension}, encoded={detected_dimension}。"
            " 当前版本不会兼容 hash 时代或其他维度的旧向量，请改回原 embedding 配置，"
            "或执行重嵌入/重建向量。"
        )

    def _embedding_fallback_enabled(self) -> bool:
        return bool(self._cfg("embedding.fallback.enabled", True))

    def _allow_metadata_only_write(self) -> bool:
        return bool(self._cfg("embedding.fallback.allow_metadata_only_write", True))

    def _embedding_probe_interval_seconds(self) -> float:
        return max(10.0, float(self._cfg("embedding.fallback.probe_interval_seconds", 180) or 180))

    def _paragraph_vector_backfill_enabled(self) -> bool:
        return bool(self._cfg("embedding.paragraph_vector_backfill.enabled", True))

    def _paragraph_vector_backfill_interval_seconds(self) -> float:
        return max(10.0, float(self._cfg("embedding.paragraph_vector_backfill.interval_seconds", 60) or 60))

    def _paragraph_vector_backfill_batch_size(self) -> int:
        return max(1, int(self._cfg("embedding.paragraph_vector_backfill.batch_size", 64) or 64))

    def _paragraph_vector_backfill_max_retry(self) -> int:
        return max(1, int(self._cfg("embedding.paragraph_vector_backfill.max_retry", 5) or 5))

    def _is_embedding_degraded(self) -> bool:
        return bool(self._embedding_degraded.get("active", False))

    def _embedding_degraded_snapshot(self) -> Dict[str, Any]:
        return {
            "active": bool(self._embedding_degraded.get("active", False)),
            "reason": str(self._embedding_degraded.get("reason", "") or ""),
            "since": self._embedding_degraded.get("since"),
            "last_check": self._embedding_degraded.get("last_check"),
        }

    def _set_embedding_degraded(self, *, active: bool, reason: str = "", checked_at: Optional[float] = None) -> None:
        now = float(checked_at or time.time())
        prev = self._embedding_degraded_snapshot()
        if active:
            since = prev.get("since") if bool(prev.get("active", False)) else now
            self._embedding_degraded = {
                "active": True,
                "reason": str(reason or "").strip(),
                "since": since,
                "last_check": now,
            }
        else:
            self._embedding_degraded = {
                "active": False,
                "reason": "",
                "since": None,
                "last_check": now,
            }
        if bool(prev.get("active", False)) != bool(active):
            if active:
                logger.warning(
                    "embedding 进入降级态，将启用 sparse-only 与 metadata-only 写入回退: "
                    f"reason={self._embedding_degraded.get('reason', '')}"
                )
            else:
                logger.info("embedding 已恢复，退出降级态")
        self._apply_runtime_sparse_mode()

    def _apply_runtime_sparse_mode(self) -> None:
        retriever = self.retriever
        if retriever is None:
            return
        setter = getattr(retriever, "set_runtime_sparse_only", None)
        if not callable(setter):
            return
        try:
            setter(self._is_embedding_degraded())
        except Exception as exc:
            logger.warning(f"设置 retriever sparse-only 运行时状态失败: {exc}")

    async def _refresh_runtime_self_check(self, *, sample_text: str = "A_Memorix runtime self check") -> Dict[str, Any]:
        from .. import sdk_memory_kernel as kernel_module

        report = await kernel_module.run_embedding_runtime_self_check(
            config=self._build_runtime_config(),
            vector_store=self.vector_store,
            embedding_manager=self.embedding_manager,
            sample_text=sample_text,
        )
        self._runtime_facade._runtime_self_check_report = dict(report)
        checked_at = float(report.get("checked_at") or time.time())
        self._embedding_degraded["last_check"] = checked_at
        return report

    def _mark_startup_self_check_deferred(self) -> None:
        """记录启动阶段跳过真实 embedding encode 自检，避免阻塞主启动流程。"""
        configured_dimension = max(
            1,
            int(self._cfg("embedding.dimension", self.embedding_dimension) or self.embedding_dimension),
        )
        requested_dimension = self._current_embedding_status_dimension()
        vector_store_dimension = int(getattr(self.vector_store, "dimension", 0) or 0)
        degraded = self._embedding_degraded_snapshot()
        is_degraded = bool(degraded.get("active", False))
        self._runtime_facade._runtime_self_check_report = {
            "ok": not is_degraded,
            "code": "startup_self_check_deferred_degraded" if is_degraded else "startup_self_check_deferred",
            "message": str(degraded.get("reason", "") or "").strip()
            or "启动阶段已跳过真实 embedding encode 自检，将由后台探测或手动 self_check 执行",
            "configured_dimension": configured_dimension,
            "requested_dimension": requested_dimension,
            "vector_store_dimension": vector_store_dimension,
            "detected_dimension": requested_dimension,
            "encoded_dimension": 0,
            "elapsed_ms": 0.0,
            "sample_text": "",
            "checked_at": None,
        }

    def _is_startup_self_check_deferred(self) -> bool:
        report = self._runtime_facade._runtime_self_check_report
        code = str(report.get("code", "") or "") if isinstance(report, dict) else ""
        return code in {"startup_self_check_deferred", "startup_self_check_deferred_degraded"}

    @staticmethod
    def _self_check_effective_dimension(report: Dict[str, Any]) -> int:
        for key in ("encoded_dimension", "detected_dimension", "requested_dimension"):
            try:
                value = int(report.get(key, 0) or 0)
            except Exception:
                value = 0
            if value > 0:
                return value
        return 0

    def _apply_self_check_dimension_result(self, report: Dict[str, Any]) -> str:
        detected_dimension = self._self_check_effective_dimension(report)
        if detected_dimension <= 0:
            return ""

        self.embedding_dimension = int(detected_dimension)
        vector_dimension = int(getattr(self.vector_store, "dimension", 0) or 0)
        if vector_dimension <= 0 or vector_dimension == detected_dimension:
            return ""

        stored_dimension = self._stored_vector_dimension() or vector_dimension
        message = self._vector_mismatch_error(
            stored_dimension=int(stored_dimension),
            detected_dimension=int(detected_dimension),
        )
        self._vector_persist_blocked_until_rebuild = True
        self._vector_rebuild_source_dimension = int(stored_dimension)
        return message

    def _enqueue_paragraph_vector_backfill(self, paragraph_hash: str, *, error: str = "") -> None:
        if self.metadata_store is None:
            return
        try:
            self.metadata_store.enqueue_paragraph_vector_backfill(
                paragraph_hash,
                error=str(error or ""),
            )
        except Exception as exc:
            logger.warning(f"登记 paragraph 向量回填任务失败: {exc}")

    async def _recover_embedding_once(self, *, sample_text: str = "A_Memorix runtime self check") -> Dict[str, Any]:
        report = await self._refresh_runtime_self_check(sample_text=sample_text)
        checked_at = float(report.get("checked_at") or time.time())
        ok = bool(report.get("ok", False))
        dimension_mismatch = self._apply_self_check_dimension_result(report)
        if dimension_mismatch:
            self._set_embedding_degraded(active=True, reason=dimension_mismatch, checked_at=checked_at)
            return {
                "success": False,
                "recovered": False,
                "report": report,
                "detail": "dimension_mismatch",
            }

        if ok:
            self._set_embedding_degraded(active=False, checked_at=checked_at)
            backfill_result: Dict[str, Any] = {}
            if self._paragraph_vector_backfill_enabled():
                backfill_result = await self._run_paragraph_backfill_once(
                    limit=self._paragraph_vector_backfill_batch_size(),
                    max_retry=self._paragraph_vector_backfill_max_retry(),
                    trigger="embedding_recovered",
                )
            return {
                "success": True,
                "recovered": True,
                "report": report,
                "backfill": backfill_result,
            }

        reason = str(report.get("message", "runtime self-check failed") or "runtime self-check failed")
        if self._embedding_fallback_enabled():
            self._set_embedding_degraded(active=True, reason=reason, checked_at=checked_at)
            return {
                "success": False,
                "recovered": False,
                "report": report,
                "detail": "still_degraded",
            }
        return {
            "success": False,
            "recovered": False,
            "report": report,
            "detail": "fallback_disabled",
        }
