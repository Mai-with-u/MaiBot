from __future__ import annotations

from typing import Any, Dict, Iterable

from ...utils.runtime_payloads import optional_float, tokens
from .base import KernelServiceBase


class MemoryEpisodeAdminService(KernelServiceBase):
    async def rebuild_episodes_for_sources(self, sources: Iterable[str]) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store is not None
        source_tokens = tokens(sources)
        for source in source_tokens:
            self.metadata_store.enqueue_episode_source_rebuild(
                source,
                reason="episode_admin_rebuild",
                debounce_seconds=0.0,
            )
        result = await self.process_episode_source_rebuild_batch(
            sources=source_tokens,
            limit=max(1, len(source_tokens)),
            max_wait_seconds=0.0,
        )
        result["sources"] = source_tokens
        return result

    async def memory_episode_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store

        act = str(action or "").strip().lower()
        if act in {"query", "list"}:
            items = self.metadata_store.query_episodes(
                query=str(kwargs.get("query", "") or "").strip(),
                time_from=optional_float(kwargs.get("time_start", kwargs.get("time_from"))),
                time_to=optional_float(kwargs.get("time_end", kwargs.get("time_to"))),
                person=str(kwargs.get("person_id", "") or kwargs.get("person", "") or "").strip() or None,
                source=str(kwargs.get("source", "") or "").strip() or None,
                limit=max(1, int(kwargs.get("limit", 20) or 20)),
            )
            return {"success": True, "items": items, "count": len(items)}

        if act == "get":
            episode_id = str(kwargs.get("episode_id", "") or "").strip()
            if not episode_id:
                return {"success": False, "error": "episode_id 不能为空"}
            episode = self.metadata_store.get_episode_by_id(episode_id)
            if episode is None:
                return {"success": False, "error": "episode 不存在"}
            episode["paragraphs"] = self.metadata_store.get_episode_paragraphs(
                episode_id,
                limit=max(1, int(kwargs.get("paragraph_limit", 100) or 100)),
            )
            return {"success": True, "episode": episode}

        if act == "status":
            summary = self.metadata_store.get_episode_source_rebuild_summary(
                failed_limit=max(1, int(kwargs.get("limit", 20) or 20))
            )
            return {"success": True, **summary}

        if act == "rebuild":
            sources = tokens(kwargs.get("sources"))
            if not sources:
                source = str(kwargs.get("source", "") or "").strip()
                if source:
                    sources = [source]
            if not sources and bool(kwargs.get("all", False)):
                sources = self.metadata_store.list_episode_sources_for_rebuild()
                if not sources:
                    sources = [
                        str(row.get("source", "") or "").strip() for row in self.metadata_store.get_all_sources()
                    ]
            if not sources:
                return {"success": False, "error": "未提供可重建的 source"}
            result = await self.rebuild_episodes_for_sources(sources)
            completed = int(result.get("rebuilt", 0) or 0)
            success = (
                len(result.get("failures", [])) == 0
                and int(result.get("unfinished", 0) or 0) == 0
                and completed == len(sources)
            )
            return {"success": success, **result}

        if act == "process_sources":
            result = await self.process_episode_source_rebuild_batch(
                limit=max(1, int(kwargs.get("limit", 20) or 20)),
                max_retry=int(kwargs.get("max_retry", 3)),
                max_wait_seconds=0.0,
            )
            success = int(result.get("failed", 0) or 0) == 0 and int(result.get("unfinished", 0) or 0) == 0
            return {"success": success, **result}

        return {"success": False, "error": f"不支持的 episode action: {act}"}
