from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence

import base64
import uuid

from .base import KernelServiceBase

if TYPE_CHECKING:
    from src.A_memorix.core.image.runtime import ImageMemoryRuntime


class MemoryImageService(KernelServiceBase):
    """图片记忆运行时的参数边界与操作分发。"""

    def _runtime(self) -> ImageMemoryRuntime:
        runtime = self.image_memory_runtime
        if runtime is None:
            raise RuntimeError("图片记忆未启用")
        return runtime

    @staticmethod
    def _decode_image(kwargs: Dict[str, Any]) -> Optional[bytes]:
        payload = kwargs.get("image_bytes")
        if payload is not None:
            if not isinstance(payload, (bytes, bytearray, memoryview)):
                raise TypeError("image_bytes 必须是字节数据")
            return bytes(payload)
        encoded = str(kwargs.get("image_base64") or "").strip()
        if not encoded:
            return None
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValueError("image_base64 不是合法的 Base64") from exc

    async def execute(self, *, action: str, **kwargs: Any) -> Dict[str, Any]:
        runtime = self._runtime()
        operation = str(action or "").strip().lower()
        if operation == "status":
            return {"success": True, **runtime.status()}
        if operation == "jobs":
            return runtime.metadata_store.list_image_jobs(
                limit=max(1, min(200, int(kwargs.get("limit", 50)))),
                offset=max(0, int(kwargs.get("offset", 0))),
                status=str(kwargs.get("status") or ""),
            )
        if operation == "retry_failed_jobs":
            counts = runtime.metadata_store.retry_failed_image_jobs()
            return {"success": True, "counts": counts, "count": sum(counts.values())}
        if operation == "ingest":
            image_bytes = self._decode_image(kwargs)
            if image_bytes is None:
                raise ValueError("图片写入缺少 image_bytes 或 image_base64")
            return await runtime.ingest(
                image_bytes=image_bytes,
                source_kind=str(kwargs.get("source_kind") or "chat"),
                external_ref=str(kwargs.get("external_ref") or ""),
                scope_type=str(kwargs.get("scope_type") or "chat"),
                chat_id=str(kwargs.get("chat_id") or ""),
                message_id=str(kwargs.get("message_id") or ""),
                component_path=str(kwargs.get("component_path") or ""),
                occurred_at=kwargs.get("occurred_at"),
                user_statement=str(kwargs.get("user_statement") or ""),
                installation_id=str(kwargs.get("installation_id") or ""),
            )
        if operation == "search":
            image_bytes = self._decode_image(kwargs)
            current_occurrence_id = str(kwargs.get("current_occurrence_id") or "")
            current_external_ref = str(kwargs.get("current_external_ref") or "")
            if not current_occurrence_id and current_external_ref:
                occurrence = runtime.metadata_store.get_image_occurrence_by_ref(current_external_ref)
                current_occurrence_id = str((occurrence or {}).get("occurrence_id") or "")
            chat_ids_raw = kwargs.get("chat_ids")
            chat_ids: Optional[Sequence[str]] = None
            if chat_ids_raw is not None:
                if not isinstance(chat_ids_raw, (list, tuple)):
                    raise TypeError("chat_ids 必须是字符串列表")
                chat_ids = [str(item) for item in chat_ids_raw if str(item).strip()]
            return await runtime.search(
                image_bytes=image_bytes,
                content_hash=str(kwargs.get("content_hash") or ""),
                current_occurrence_id=current_occurrence_id,
                chat_ids=chat_ids,
                candidate_limit=int(kwargs.get("candidate_limit") or runtime._cfg("candidate_limit", 8)),
                similarity_threshold=float(
                    kwargs.get("similarity_threshold")
                    if kwargs.get("similarity_threshold") is not None
                    else runtime._cfg("similarity_threshold", 0.72)
                ),
                session_id=str(kwargs.get("session_id") or ""),
            )
        if operation == "get":
            return runtime.get(
                asset_id=str(kwargs.get("asset_id") or ""),
                chat_ids=kwargs.get("chat_ids"),
            )
        if operation == "list":
            return runtime.list_assets(
                limit=max(1, min(500, int(kwargs.get("limit") or 50))),
                offset=max(0, int(kwargs.get("offset") or 0)),
            )
        if operation == "delete_occurrence":
            return await runtime.delete_occurrence(str(kwargs.get("occurrence_id") or ""))
        if operation == "observe":
            return runtime.add_observation(
                occurrence_id=str(kwargs.get("occurrence_id") or ""),
                text=str(kwargs.get("text") or ""),
                source_kind=str(kwargs.get("source_kind") or "manual"),
                confirm_status=str(kwargs.get("confirm_status") or "confirmed"),
                evidence=kwargs.get("evidence") if isinstance(kwargs.get("evidence"), dict) else {},
                supersedes_id=str(kwargs.get("supersedes_id") or ""),
            )
        if operation == "link":
            return runtime.link(
                occurrence_id=str(kwargs.get("occurrence_id") or ""),
                target_type=str(kwargs.get("target_type") or ""),
                target_id=str(kwargs.get("target_id") or ""),
                link_kind=str(kwargs.get("link_kind") or "evidence"),
                evidence=kwargs.get("evidence") if isinstance(kwargs.get("evidence"), dict) else {},
            )
        if operation == "unlink":
            return runtime.unlink(str(kwargs.get("link_id") or ""))
        if operation == "describe":
            targets = await runtime.apply_model_description(
                content_hash=str(kwargs.get("content_hash") or ""),
                text=str(kwargs.get("text") or ""),
            )
            return {
                "success": True,
                "updated_occurrences": len(targets),
                "compensation_targets": targets,
            }
        if operation == "process_jobs":
            return await runtime.process_jobs_once()
        if operation == "claim_description_compensations":
            lease_token = uuid.uuid4().hex
            items = runtime.metadata_store.claim_image_description_compensations(
                lease_token=lease_token,
                lease_seconds=float(runtime._cfg("job_lease_seconds", 120.0)),
                max_attempts=1 + int(runtime._cfg("job_max_retries", 5)),
                limit=max(1, min(100, int(kwargs.get("limit") or runtime._cfg("job_batch_size", 4)))),
            )
            return {"success": True, "lease_token": lease_token, "items": items}
        if operation == "complete_description_compensation":
            updated = runtime.metadata_store.complete_image_description_compensation(
                occurrence_id=str(kwargs.get("occurrence_id") or ""),
                description_hash=str(kwargs.get("description_hash") or ""),
                lease_token=str(kwargs.get("lease_token") or ""),
                success=bool(kwargs.get("success")),
                error=str(kwargs.get("error") or ""),
            )
            return {"success": updated}
        raise ValueError(f"不支持的图片记忆操作: {operation}")
