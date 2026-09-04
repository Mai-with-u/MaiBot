from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...utils.runtime_payloads import build_source
from .base import KernelServiceBase


class MemorySummaryService(KernelServiceBase):
    async def summarize_chat_stream(
        self,
        *,
        chat_id: str,
        context_length: Optional[int] = None,
        include_personality: Optional[bool] = None,
        time_end: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        await self.initialize()
        assert self.summary_importer
        import_result = await self.summary_importer.import_from_stream(
            stream_id=str(chat_id or "").strip(),
            context_length=context_length,
            include_personality=include_personality,
            time_end=time_end,
            metadata=metadata,
        )
        success = bool(getattr(import_result, "success", False))
        detail = str(getattr(import_result, "detail", "") or "")
        paragraph_hash = str(getattr(import_result, "paragraph_hash", "") or "").strip()
        skipped = bool(getattr(import_result, "skipped", False))
        source = str(getattr(import_result, "source", "") or "").strip() or build_source("chat_summary", chat_id, [])
        stored_ids: List[str] = []
        skipped_ids: List[str] = []
        if success:
            if paragraph_hash:
                stored_ids.append(paragraph_hash)
                self._persist()
            elif skipped:
                external_id = str((metadata or {}).get("external_id", "") or "").strip()
                if external_id:
                    skipped_ids.append(external_id)
            else:
                raise RuntimeError("聊天摘要导入成功但未返回 paragraph_hash")
        payload = {"success": success, "detail": detail, "episode_source": source}
        if stored_ids:
            payload["stored_ids"] = stored_ids
        if skipped_ids:
            payload["skipped_ids"] = skipped_ids
        return payload
