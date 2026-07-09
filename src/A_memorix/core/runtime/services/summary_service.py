from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...utils import profile_policy
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
        source = (
            str(getattr(import_result, "source", "") or "").strip()
            or build_source("chat_summary", chat_id, [])
        )
        stored_ids: List[str] = []
        episode_pending_ids: List[str] = []
        if success:
            if not paragraph_hash:
                raise RuntimeError("聊天摘要导入成功但未返回 paragraph_hash，无法执行 Episode 增量入队")
            assert self.metadata_store is not None
            if profile_policy.should_auto_enqueue_episode(self._cfg, source_type="chat_summary"):
                self.metadata_store.enqueue_episode_pending(paragraph_hash, source=source)
                episode_pending_ids.append(paragraph_hash)
            stored_ids.append(paragraph_hash)
            self._persist()
        payload = {"success": success, "detail": detail}
        if stored_ids:
            payload["stored_ids"] = stored_ids
        if episode_pending_ids:
            payload["episode_pending_ids"] = episode_pending_ids
        return payload
