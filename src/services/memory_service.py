from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.common.logger import get_logger
from src.plugin_runtime.integration import get_plugin_runtime_manager


logger = get_logger("memory_service")

PLUGIN_ID = "A_Memorix"


@dataclass
class MemoryHit:
    content: str
    score: float = 0.0
    hit_type: str = ""
    source: str = ""
    hash_value: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    episode_id: str = ""
    title: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "score": self.score,
            "type": self.hit_type,
            "source": self.source,
            "hash": self.hash_value,
            "metadata": self.metadata,
            "episode_id": self.episode_id,
            "title": self.title,
        }


@dataclass
class MemorySearchResult:
    summary: str = ""
    hits: List[MemoryHit] = field(default_factory=list)
    filtered: bool = False
    success: bool = True
    error: str = ""

    def to_text(self, limit: int = 5) -> str:
        if not self.hits:
            return ""
        lines = []
        for index, item in enumerate(self.hits[: max(1, int(limit))], start=1):
            content = item.content.strip().replace("\n", " ")
            if len(content) > 160:
                content = content[:160] + "..."
            lines.append(f"{index}. {content}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "error": self.error,
            "summary": self.summary,
            "hits": [item.to_dict() for item in self.hits],
            "filtered": self.filtered,
        }


@dataclass
class MemoryWriteResult:
    success: bool
    stored_ids: List[str] = field(default_factory=list)
    skipped_ids: List[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "stored_ids": self.stored_ids,
            "skipped_ids": self.skipped_ids,
            "detail": self.detail,
        }


@dataclass
class PersonProfileResult:
    summary: str = ""
    traits: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"summary": self.summary, "traits": self.traits, "evidence": self.evidence}


class MemoryService:
    async def _invoke(self, component_name: str, args: Optional[Dict[str, Any]] = None, *, timeout_ms: int = 30000) -> Any:
        runtime = get_plugin_runtime_manager()
        if not runtime.is_running:
            raise RuntimeError("plugin_runtime 未启动")
        response = await runtime.invoke_plugin(
            method="plugin.invoke_tool",
            plugin_id=PLUGIN_ID,
            component_name=component_name,
            args=args or {},
            timeout_ms=max(1000, int(timeout_ms or 30000)),
        )
        # 兼容新旧运行时返回:
        # - 旧版: 直接返回工具结果(dict)
        # - 新版: 返回 Envelope，工具结果在 payload.result 中
        if isinstance(response, dict):
            return response
        payload = getattr(response, "payload", None)
        if isinstance(payload, dict):
            if isinstance(payload.get("result"), dict):
                return payload["result"]
            return payload
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, dict):
                inner_payload = dumped.get("payload")
                if isinstance(inner_payload, dict):
                    if isinstance(inner_payload.get("result"), dict):
                        return inner_payload["result"]
                    return inner_payload
        return response

    async def _invoke_admin(
        self,
        component_name: str,
        *,
        action: str,
        timeout_ms: int = 30000,
        **kwargs,
    ) -> Dict[str, Any]:
        payload = await self._invoke(component_name, {"action": action, **kwargs}, timeout_ms=timeout_ms)
        return payload if isinstance(payload, dict) else {"success": False, "error": "invalid_payload"}

    @staticmethod
    def _coerce_write_result(payload: Any) -> MemoryWriteResult:
        if not isinstance(payload, dict):
            return MemoryWriteResult(success=False, detail="invalid_payload")
        stored_ids = [str(item) for item in (payload.get("stored_ids") or []) if str(item).strip()]
        skipped_ids = [str(item) for item in (payload.get("skipped_ids") or []) if str(item).strip()]
        detail = str(payload.get("detail") or payload.get("reason") or "")
        if stored_ids or skipped_ids:
            success = True
        elif "success" in payload:
            success = bool(payload.get("success"))
        else:
            success = not bool(detail)
        return MemoryWriteResult(
            success=success,
            stored_ids=stored_ids,
            skipped_ids=skipped_ids,
            detail=detail,
        )

    @staticmethod
    def _coerce_search_result(payload: Any) -> MemorySearchResult:
        if not isinstance(payload, dict):
            return MemorySearchResult(success=False, error="invalid_payload")
        hits: List[MemoryHit] = []
        for item in payload.get("hits", []) or []:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata", {}) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            if "source_branches" in item and "source_branches" not in metadata:
                metadata["source_branches"] = item.get("source_branches") or []
            if "rank" in item and "rank" not in metadata:
                metadata["rank"] = item.get("rank")
            hits.append(
                MemoryHit(
                    content=str(item.get("content", "") or ""),
                    score=float(item.get("score", 0.0) or 0.0),
                    hit_type=str(item.get("type", "") or ""),
                    source=str(item.get("source", "") or ""),
                    hash_value=str(item.get("hash", "") or ""),
                    metadata=metadata,
                    episode_id=str(item.get("episode_id", "") or ""),
                    title=str(item.get("title", "") or ""),
                )
            )
        success_raw = payload.get("success")
        error = str(payload.get("error", "") or "")
        success = (not bool(error)) if success_raw is None else bool(success_raw)
        return MemorySearchResult(
            summary=str(payload.get("summary", "") or ""),
            hits=hits,
            filtered=bool(payload.get("filtered", False)),
            success=success,
            error=error,
        )

    @staticmethod
    def _coerce_profile_result(payload: Any) -> PersonProfileResult:
        if not isinstance(payload, dict):
            return PersonProfileResult()
        return PersonProfileResult(
            summary=str(payload.get("summary", "") or ""),
            traits=[str(item) for item in (payload.get("traits") or []) if str(item).strip()],
            evidence=[item for item in (payload.get("evidence") or []) if isinstance(item, dict)],
        )

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        mode: str = "search",
        chat_id: str = "",
        person_id: str = "",
        time_start: str | float | None = None,
        time_end: str | float | None = None,
        respect_filter: bool = True,
        user_id: str = "",
        group_id: str = "",
    ) -> MemorySearchResult:
        clean_query = str(query or "").strip()
        normalized_time_start = None if time_start in {None, ""} else time_start
        normalized_time_end = None if time_end in {None, ""} else time_end
        if not clean_query and normalized_time_start is None and normalized_time_end is None:
            return MemorySearchResult()
        try:
            payload = await self._invoke(
                "search_memory",
                {
                    "query": clean_query,
                    "limit": max(1, int(limit)),
                    "mode": mode,
                    "chat_id": chat_id,
                    "person_id": person_id,
                    "time_start": normalized_time_start,
                    "time_end": normalized_time_end,
                    "respect_filter": bool(respect_filter),
                    "user_id": str(user_id or "").strip(),
                    "group_id": str(group_id or "").strip(),
                },
            )
            return self._coerce_search_result(payload)
        except Exception as exc:
            logger.warning("长期记忆搜索失败: %s", exc)
            return MemorySearchResult(success=False, error=str(exc))

    async def ingest_summary(
        self,
        *,
        external_id: str,
        chat_id: str,
        text: str,
        participants: Optional[List[str]] = None,
        time_start: float | None = None,
        time_end: float | None = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        respect_filter: bool = True,
        user_id: str = "",
        group_id: str = "",
    ) -> MemoryWriteResult:
        try:
            payload = await self._invoke(
                "ingest_summary",
                {
                    "external_id": external_id,
                    "chat_id": chat_id,
                    "text": text,
                    "participants": participants or [],
                    "time_start": time_start,
                    "time_end": time_end,
                    "tags": tags or [],
                    "metadata": metadata or {},
                    "respect_filter": bool(respect_filter),
                    "user_id": str(user_id or "").strip(),
                    "group_id": str(group_id or "").strip(),
                },
            )
            return self._coerce_write_result(payload)
        except Exception as exc:
            logger.warning("长期记忆写入摘要失败: %s", exc)
            return MemoryWriteResult(success=False, detail=str(exc))

    async def ingest_text(
        self,
        *,
        external_id: str,
        source_type: str,
        text: str,
        chat_id: str = "",
        person_ids: Optional[List[str]] = None,
        participants: Optional[List[str]] = None,
        timestamp: float | None = None,
        time_start: float | None = None,
        time_end: float | None = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        entities: Optional[List[str]] = None,
        relations: Optional[List[Dict[str, Any]]] = None,
        respect_filter: bool = True,
        user_id: str = "",
        group_id: str = "",
    ) -> MemoryWriteResult:
        try:
            payload = await self._invoke(
                "ingest_text",
                {
                    "external_id": external_id,
                    "source_type": source_type,
                    "text": text,
                    "chat_id": chat_id,
                    "person_ids": person_ids or [],
                    "participants": participants or [],
                    "timestamp": timestamp,
                    "time_start": time_start,
                    "time_end": time_end,
                    "tags": tags or [],
                    "metadata": metadata or {},
                    "entities": entities or [],
                    "relations": relations or [],
                    "respect_filter": bool(respect_filter),
                    "user_id": str(user_id or "").strip(),
                    "group_id": str(group_id or "").strip(),
                },
            )
            return self._coerce_write_result(payload)
        except Exception as exc:
            logger.warning("长期记忆写入文本失败: %s", exc)
            return MemoryWriteResult(success=False, detail=str(exc))

    async def get_person_profile(self, person_id: str, *, chat_id: str = "", limit: int = 10) -> PersonProfileResult:
        clean_person_id = str(person_id or "").strip()
        if not clean_person_id:
            return PersonProfileResult()
        try:
            payload = await self._invoke(
                "get_person_profile",
                {"person_id": clean_person_id, "chat_id": chat_id, "limit": max(1, int(limit))},
            )
            return self._coerce_profile_result(payload)
        except Exception as exc:
            logger.warning("获取人物画像失败: %s", exc)
            return PersonProfileResult()

    async def maintain_memory(
        self,
        *,
        action: str,
        target: str = "",
        hours: float | None = None,
        reason: str = "",
        limit: int = 50,
    ) -> MemoryWriteResult:
        try:
            payload = await self._invoke(
                "maintain_memory",
                {"action": action, "target": target, "hours": hours, "reason": reason, "limit": limit},
            )
            if not isinstance(payload, dict):
                return MemoryWriteResult(success=False, detail="invalid_payload")
            return MemoryWriteResult(success=bool(payload.get("success")), detail=str(payload.get("detail", "") or ""))
        except Exception as exc:
            logger.warning("记忆维护失败: %s", exc)
            return MemoryWriteResult(success=False, detail=str(exc))

    async def memory_stats(self) -> Dict[str, Any]:
        try:
            payload = await self._invoke("memory_stats", {})
            return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            logger.warning("获取记忆统计失败: %s", exc)
            return {}

    async def graph_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_graph_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning("图谱管理调用失败: %s", exc)
            return {"success": False, "error": str(exc)}

    async def source_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_source_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning("来源管理调用失败: %s", exc)
            return {"success": False, "error": str(exc)}

    async def episode_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_episode_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning("Episode 管理调用失败: %s", exc)
            return {"success": False, "error": str(exc)}

    async def profile_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_profile_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning("画像管理调用失败: %s", exc)
            return {"success": False, "error": str(exc)}

    async def runtime_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_runtime_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning("运行时管理调用失败: %s", exc)
            return {"success": False, "error": str(exc)}

    async def import_admin(self, *, action: str, timeout_ms: int = 120000, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_import_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning("导入管理调用失败: %s", exc)
            return {"success": False, "error": str(exc)}

    async def tuning_admin(self, *, action: str, timeout_ms: int = 120000, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_tuning_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning("调优管理调用失败: %s", exc)
            return {"success": False, "error": str(exc)}

    async def v5_admin(self, *, action: str, timeout_ms: int = 30000, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_v5_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning("V5 记忆管理调用失败: %s", exc)
            return {"success": False, "error": str(exc)}

    async def delete_admin(self, *, action: str, timeout_ms: int = 120000, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_delete_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning("删除管理调用失败: %s", exc)
            return {"success": False, "error": str(exc)}

    async def get_recycle_bin(self, *, limit: int = 50) -> Dict[str, Any]:
        try:
            payload = await self._invoke("maintain_memory", {"action": "recycle_bin", "limit": max(1, int(limit or 50))})
            return payload if isinstance(payload, dict) else {"success": False, "error": "invalid_payload"}
        except Exception as exc:
            logger.warning("获取回收站失败: %s", exc)
            return {"success": False, "error": str(exc)}

    async def restore_memory(self, *, target: str) -> MemoryWriteResult:
        return await self.maintain_memory(action="restore", target=target)

    async def reinforce_memory(self, *, target: str) -> MemoryWriteResult:
        return await self.maintain_memory(action="reinforce", target=target)

    async def freeze_memory(self, *, target: str) -> MemoryWriteResult:
        return await self.maintain_memory(action="freeze", target=target)

    async def protect_memory(self, *, target: str, hours: float | None = None) -> MemoryWriteResult:
        return await self.maintain_memory(action="protect", target=target, hours=hours)


memory_service = MemoryService()
