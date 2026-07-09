from __future__ import annotations

from typing import Any, Dict

from .base import KernelServiceBase


class MemorySourceAdminService(KernelServiceBase):
    async def memory_source_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store

        act = str(action or "").strip().lower()
        if act == "list":
            sources = self.metadata_store.get_all_sources()
            items = []
            for row in sources:
                source_name = str(row.get("source", "") or "").strip()
                items.append(
                    {
                        **row,
                        "episode_rebuild_blocked": self.metadata_store.is_episode_source_query_blocked(source_name),
                    }
                )
            return {"success": True, "items": items, "count": len(items)}

        if act == "delete":
            source = str(kwargs.get("source", "") or "").strip()
            result = await self._execute_delete_action(
                mode="source",
                selector={"sources": [source]},
                requested_by=str(kwargs.get("requested_by", "") or "memory_source_admin"),
                reason=str(kwargs.get("reason", "") or "source_delete"),
            )
            await self._invalidate_import_manifest_for_sources(result)
            return result

        if act == "batch_delete":
            result = await self._execute_delete_action(
                mode="source",
                selector={"sources": list(kwargs.get("sources") or [])},
                requested_by=str(kwargs.get("requested_by", "") or "memory_source_admin"),
                reason=str(kwargs.get("reason", "") or "source_batch_delete"),
            )
            await self._invalidate_import_manifest_for_sources(result)
            return result

        return {"success": False, "error": f"不支持的 source action: {act}"}
