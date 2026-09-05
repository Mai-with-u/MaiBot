from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from src.A_memorix.host_service import a_memorix_host_service
from src.A_memorix.runtime_registry import get_runtime_kernel
from src.common.logger import get_logger


logger = get_logger("memory_service")


class MemoryMetadataUnavailableError(RuntimeError):
    """runtime kernel 尚未提供 metadata_store。"""

    def __init__(self, message: str = "长期记忆 metadata 数据库尚未就绪") -> None:
        super().__init__(message)


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

    def to_text(self, limit: int = 5, *, truncate_content: bool = True, max_content_chars: int = 160) -> str:
        if not self.hits:
            return ""
        lines = []
        for index, item in enumerate(self.hits[: max(1, int(limit))], start=1):
            content = item.content.strip().replace("\n", " ")
            if truncate_content and len(content) > max_content_chars:
                content = content[:max_content_chars] + "..."
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
    async def _invoke(
        self,
        component_name: str,
        args: Optional[Dict[str, Any]] = None,
        *,
        timeout_ms: Optional[int] = None,
    ) -> Any:
        if timeout_ms is None:
            response = await a_memorix_host_service.invoke(component_name, args or {})
        else:
            response = await a_memorix_host_service.invoke(component_name, args or {}, timeout_ms=timeout_ms)
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
        timeout_ms: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        if timeout_ms is None:
            payload = await self._invoke(component_name, {"action": action, **kwargs})
        else:
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
            logger.warning(f"长期记忆搜索失败: {exc}")
            return MemorySearchResult(success=False, error=str(exc))

    async def enqueue_feedback_task(
        self,
        *,
        query_tool_id: str,
        session_id: str,
        query_timestamp: Any = None,
        structured_content: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            payload = await self._invoke(
                "enqueue_feedback_task",
                {
                    "query_tool_id": str(query_tool_id or "").strip(),
                    "session_id": str(session_id or "").strip(),
                    "query_timestamp": query_timestamp,
                    "structured_content": structured_content if isinstance(structured_content, dict) else {},
                },
                timeout_ms=10000,
            )
        except Exception as exc:
            logger.warning(f"反馈纠错任务入队失败: {exc}")
            return {"success": False, "queued": False, "reason": str(exc)}
        return (
            payload if isinstance(payload, dict) else {"success": False, "queued": False, "reason": "invalid_payload"}
        )

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
            logger.warning(f"长期记忆写入摘要失败: {exc}")
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
            logger.warning(f"长期记忆写入文本失败: {exc}")
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
            logger.warning(f"获取人物画像失败: {exc}")
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
            logger.warning(f"记忆维护失败: {exc}")
            return MemoryWriteResult(success=False, detail=str(exc))

    async def memory_stats(self) -> Dict[str, Any]:
        try:
            payload = await self._invoke("memory_stats", {})
            return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            logger.warning(f"获取记忆统计失败: {exc}")
            return {}

    async def graph_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_graph_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning(f"图谱管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def source_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_source_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning(f"来源管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def episode_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_episode_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning(f"Episode 管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def profile_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_profile_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning(f"画像管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def feedback_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_feedback_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning(f"反馈纠错管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def fact_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_fact_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning(f"事实账本管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def runtime_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_runtime_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning(f"运行时管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def import_admin(self, *, action: str, timeout_ms: int = 120000, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_import_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning(f"导入管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def tuning_admin(self, *, action: str, timeout_ms: int = 120000, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_tuning_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning(f"调优管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def v5_admin(self, *, action: str, timeout_ms: Optional[int] = None, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_v5_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning(f"V5 记忆管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def delete_admin(self, *, action: str, timeout_ms: int = 120000, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_delete_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning(f"删除管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def preview_paragraph_sources(self, paragraph_hashes: Sequence[str]) -> Dict[str, str]:
        """按段落 hash 预览存储来源，失败时返回空映射。"""

        hashes = list(paragraph_hashes)
        if not hashes:
            return {}
        try:
            payload = await self.delete_admin(
                action="preview",
                mode="paragraph",
                selector={"hashes": hashes},
                timeout_ms=10000,
            )
        except Exception:
            return {}
        if not isinstance(payload, dict) or not bool(payload.get("success", False)):
            return {}

        sources: Dict[str, str] = {}
        for item in payload.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("item_type", "") or "").strip() != "paragraph":
                continue
            paragraph_hash = str(item.get("item_hash", "") or "").strip()
            source = str(item.get("source", "") or "").strip()
            if paragraph_hash and source:
                sources[paragraph_hash] = source
        return sources

    async def memory_correction_admin(self, *, action: str, timeout_ms: int = 120000, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_correction_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning(f"记忆修正管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def fuzzy_modify_admin(self, *, action: str, timeout_ms: int = 120000, **kwargs) -> Dict[str, Any]:
        return await self.memory_correction_admin(action=action, timeout_ms=timeout_ms, **kwargs)

    async def get_recycle_bin(self, *, limit: int = 50) -> Dict[str, Any]:
        try:
            payload = await self._invoke(
                "maintain_memory", {"action": "recycle_bin", "limit": max(1, int(limit or 50))}
            )
            return payload if isinstance(payload, dict) else {"success": False, "error": "invalid_payload"}
        except Exception as exc:
            logger.warning(f"获取回收站失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def restore_memory(self, *, target: str) -> MemoryWriteResult:
        return await self.maintain_memory(action="restore", target=target)

    async def reinforce_memory(self, *, target: str) -> MemoryWriteResult:
        return await self.maintain_memory(action="reinforce", target=target)

    async def freeze_memory(self, *, target: str) -> MemoryWriteResult:
        return await self.maintain_memory(action="freeze", target=target)

    async def protect_memory(self, *, target: str, hours: float | None = None) -> MemoryWriteResult:
        return await self.maintain_memory(action="protect", target=target, hours=hours)

    def get_runtime_metadata_store(self) -> Any:
        """读取 runtime_registry 中当前 kernel 的 metadata_store，供 WebUI 权威查询使用。"""

        kernel = get_runtime_kernel()
        return getattr(kernel, "metadata_store", None) if kernel is not None else None

    def query_memory_rows(self, sql: str, params: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        """执行 WebUI 时间线等宽松查询；store 缺失或 SQL 失败时返回空列表。"""

        metadata_store = self.get_runtime_metadata_store()
        if metadata_store is None or not hasattr(metadata_store, "query"):
            return []
        try:
            return list(metadata_store.query(sql, params))
        except Exception:
            return []

    def query_memory_records(self, sql: str, params: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        """执行 WebUI 权威记忆查询；查询错误需要完整暴露，不能伪装成空结果。"""

        metadata_store = self.get_runtime_metadata_store()
        if metadata_store is None:
            raise MemoryMetadataUnavailableError()
        return [dict(row) for row in metadata_store.query(sql, params)]

    async def get_paragraphs_by_source(self, source: str) -> List[Any]:
        """通过宿主 `_ensure_kernel` 路径读取指定来源段落，不改行形状。"""

        runtime_manager = a_memorix_host_service
        ensure_kernel = getattr(runtime_manager, "_ensure_kernel", None)
        if not callable(ensure_kernel):
            return []
        kernel = await ensure_kernel()
        metadata_store = getattr(kernel, "metadata_store", None)
        if metadata_store is None:
            return []
        paragraphs = metadata_store.get_paragraphs_by_source(source)
        if not paragraphs:
            return []
        return paragraphs

    @staticmethod
    def _memory_placeholders(values: Sequence[str]) -> str:
        return ",".join("?" for _ in values)

    @staticmethod
    def _append_limit(sql: str, limit: Optional[int]) -> str:
        if limit is None:
            return sql
        return f"{sql}\n        LIMIT ?"

    def search_paragraph_records(
        self,
        *,
        include_inactive: bool,
        keyword: str,
        pattern: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        return self.query_memory_records(
            """
            SELECT hash, content, source, knowledge_type, word_count, vector_index,
                   created_at, updated_at, metadata, is_deleted
            FROM paragraphs
            WHERE (? = 1 OR COALESCE(is_deleted, 0) = 0)
              AND (? = '' OR LOWER(COALESCE(content, '')) LIKE ? ESCAPE '\\'
                   OR LOWER(COALESCE(hash, '')) LIKE ? ESCAPE '\\'
                   OR LOWER(COALESCE(source, '')) LIKE ? ESCAPE '\\')
            ORDER BY COALESCE(updated_at, created_at, 0) DESC
            LIMIT ?
            """,
            (int(include_inactive), keyword, pattern, pattern, pattern, limit),
        )

    def search_entity_records(
        self,
        *,
        include_inactive: bool,
        keyword: str,
        pattern: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        return self.query_memory_records(
            """
            SELECT e.hash, e.name, e.appearance_count, e.vector_index, e.created_at, e.metadata, e.is_deleted,
                   (
                       SELECT COUNT(DISTINCT pe.paragraph_hash)
                       FROM paragraph_entities pe
                       JOIN paragraphs p ON p.hash = pe.paragraph_hash
                       WHERE pe.entity_hash = e.hash
                         AND COALESCE(p.is_deleted, 0) = 0
                   ) AS active_evidence_count
            FROM entities e
            WHERE (? = 1 OR COALESCE(e.is_deleted, 0) = 0)
              AND (? = '' OR LOWER(COALESCE(e.name, '')) LIKE ? ESCAPE '\\'
                   OR LOWER(COALESCE(e.hash, '')) LIKE ? ESCAPE '\\')
            ORDER BY e.appearance_count DESC, e.created_at DESC
            LIMIT ?
            """,
            (int(include_inactive), keyword, pattern, pattern, limit),
        )

    def search_relation_records(
        self,
        *,
        include_inactive: bool,
        keyword: str,
        pattern: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        return self.query_memory_records(
            """
            SELECT hash, subject, predicate, object, confidence, source_paragraph,
                   vector_state, created_at, last_reinforced, is_inactive,
                   is_pinned, protected_until, metadata
            FROM relations
            WHERE (? = 1 OR COALESCE(is_inactive, 0) = 0)
              AND (? = '' OR LOWER(COALESCE(subject, '')) LIKE ? ESCAPE '\\'
                   OR LOWER(COALESCE(predicate, '')) LIKE ? ESCAPE '\\'
                   OR LOWER(COALESCE(object, '')) LIKE ? ESCAPE '\\'
                   OR LOWER(COALESCE(hash, '')) LIKE ? ESCAPE '\\')
            ORDER BY COALESCE(last_reinforced, created_at, 0) DESC
            LIMIT ?
            """,
            (int(include_inactive), keyword, pattern, pattern, pattern, pattern, limit),
        )

    def search_fact_records(
        self,
        *,
        include_inactive: bool,
        keyword: str,
        pattern: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        return self.query_memory_records(
            """
            SELECT *
            FROM fact_claims
            WHERE (? = 1 OR LOWER(COALESCE(status, 'active')) = 'active')
              AND (? = '' OR LOWER(COALESCE(fact_key, '')) LIKE ? ESCAPE '\\'
                   OR LOWER(COALESCE(value_text, '')) LIKE ? ESCAPE '\\'
                   OR LOWER(COALESCE(scope_id, '')) LIKE ? ESCAPE '\\'
                   OR LOWER(COALESCE(claim_id, '')) LIKE ? ESCAPE '\\')
            ORDER BY COALESCE(updated_at, last_confirmed_at, created_at, 0) DESC
            LIMIT ?
            """,
            (int(include_inactive), keyword, pattern, pattern, pattern, pattern, limit),
        )

    def get_paragraph_record(self, token: str) -> List[Dict[str, Any]]:
        return self.query_memory_records("SELECT * FROM paragraphs WHERE hash = ? LIMIT 1", (token,))

    def get_entity_record(self, token: str) -> List[Dict[str, Any]]:
        return self.query_memory_records(
            """
            SELECT e.*,
                   (
                       SELECT COUNT(DISTINCT pe.paragraph_hash)
                       FROM paragraph_entities pe
                       JOIN paragraphs p ON p.hash = pe.paragraph_hash
                       WHERE pe.entity_hash = e.hash
                         AND COALESCE(p.is_deleted, 0) = 0
                   ) AS active_evidence_count
            FROM entities e
            WHERE e.hash = ? OR LOWER(TRIM(e.name)) = LOWER(TRIM(?))
            LIMIT 1
            """,
            (token, token),
        )

    def get_relation_record(self, token: str) -> List[Dict[str, Any]]:
        return self.query_memory_records("SELECT * FROM relations WHERE hash = ? LIMIT 1", (token,))

    def get_fact_record(self, token: str) -> List[Dict[str, Any]]:
        return self.query_memory_records("SELECT * FROM fact_claims WHERE claim_id = ? LIMIT 1", (token,))

    def list_entity_paragraph_hashes(self, entity_hash: str, limit: int) -> List[Dict[str, Any]]:
        return self.query_memory_records(
            """
            SELECT DISTINCT p.hash
            FROM paragraph_entities pe
            JOIN paragraphs p ON p.hash = pe.paragraph_hash
            WHERE pe.entity_hash = ? AND COALESCE(p.is_deleted, 0) = 0
            ORDER BY COALESCE(p.updated_at, p.created_at, 0) DESC
            LIMIT ?
            """,
            (entity_hash, limit),
        )

    def list_relation_paragraph_hashes(self, relation_hash: str, limit: int) -> List[Dict[str, Any]]:
        return self.query_memory_records(
            """
            SELECT DISTINCT p.hash
            FROM paragraph_relations pr
            JOIN paragraphs p ON p.hash = pr.paragraph_hash
            WHERE pr.relation_hash = ? AND COALESCE(p.is_deleted, 0) = 0
            ORDER BY COALESCE(p.updated_at, p.created_at, 0) DESC
            LIMIT ?
            """,
            (relation_hash, limit),
        )

    def list_fact_paragraph_evidence_ids(self, claim_id: str, limit: int) -> List[Dict[str, Any]]:
        return self.query_memory_records(
            """
        SELECT evidence_id
        FROM fact_evidence
        WHERE claim_id = ? AND evidence_type = 'paragraph'
        ORDER BY observed_at DESC
        LIMIT ?
        """,
            (claim_id, limit),
        )

    def list_active_paragraph_hashes(self, hashes: Sequence[str]) -> List[Dict[str, Any]]:
        placeholders = self._memory_placeholders(hashes)
        return self.query_memory_records(
            f"SELECT hash FROM paragraphs WHERE hash IN ({placeholders}) AND COALESCE(is_deleted, 0) = 0",
            tuple(hashes),
        )

    def list_paragraph_records_by_hashes(self, hashes: Sequence[str], limit: int) -> List[Dict[str, Any]]:
        placeholders = self._memory_placeholders(hashes)
        return self.query_memory_records(
            f"SELECT * FROM paragraphs WHERE hash IN ({placeholders}) ORDER BY COALESCE(updated_at, created_at, 0) DESC LIMIT ?",
            (*hashes, limit),
        )

    def list_entity_records_by_paragraph_hashes(self, hashes: Sequence[str], limit: int) -> List[Dict[str, Any]]:
        placeholders = self._memory_placeholders(hashes)
        return self.query_memory_records(
            f"""
            SELECT DISTINCT e.*,
                   (
                       SELECT COUNT(DISTINCT pe2.paragraph_hash)
                       FROM paragraph_entities pe2
                       JOIN paragraphs p2 ON p2.hash = pe2.paragraph_hash
                       WHERE pe2.entity_hash = e.hash
                         AND COALESCE(p2.is_deleted, 0) = 0
                   ) AS active_evidence_count
            FROM paragraph_entities pe
            JOIN entities e ON e.hash = pe.entity_hash
            WHERE pe.paragraph_hash IN ({placeholders})
            ORDER BY e.appearance_count DESC
            LIMIT ?
            """,
            (*hashes, limit),
        )

    def list_relation_records_by_paragraph_hashes(self, hashes: Sequence[str], limit: int) -> List[Dict[str, Any]]:
        placeholders = self._memory_placeholders(hashes)
        return self.query_memory_records(
            f"""
            SELECT DISTINCT r.*
            FROM paragraph_relations pr
            JOIN relations r ON r.hash = pr.relation_hash
            WHERE pr.paragraph_hash IN ({placeholders})
            ORDER BY COALESCE(r.last_reinforced, r.created_at, 0) DESC
            LIMIT ?
            """,
            (*hashes, limit),
        )

    def list_fact_records_by_paragraph_hashes(self, hashes: Sequence[str], limit: int) -> List[Dict[str, Any]]:
        placeholders = self._memory_placeholders(hashes)
        return self.query_memory_records(
            f"""
            SELECT DISTINCT fc.*
            FROM fact_evidence fe
            JOIN fact_claims fc ON fc.claim_id = fe.claim_id
            WHERE fe.evidence_id IN ({placeholders})
            ORDER BY COALESCE(fc.updated_at, fc.last_confirmed_at, fc.created_at, 0) DESC
            LIMIT ?
            """,
            (*hashes, limit),
        )

    def list_episode_records_by_paragraph_hashes(self, hashes: Sequence[str], limit: int) -> List[Dict[str, Any]]:
        placeholders = self._memory_placeholders(hashes)
        return self.query_memory_records(
            f"""
            SELECT DISTINCT e.episode_id, e.title, e.summary, e.source, e.paragraph_count,
                            e.event_time_start, e.event_time_end, e.updated_at
            FROM episode_paragraphs ep
            JOIN episodes e ON e.episode_id = ep.episode_id
            WHERE ep.paragraph_hash IN ({placeholders})
            ORDER BY e.updated_at DESC
            LIMIT ?
            """,
            (*hashes, limit),
        )

    def list_fact_evidence_records(self, claim_id: str, limit: int) -> List[Dict[str, Any]]:
        return self.query_memory_records(
            """
            SELECT evidence_type, evidence_id, stance, weight, observed_at, metadata_json
            FROM fact_evidence WHERE claim_id = ? ORDER BY observed_at DESC LIMIT ?
            """,
            (claim_id, limit),
        )

    def list_fact_transition_records(self, claim_id: str, limit: int) -> List[Dict[str, Any]]:
        return self.query_memory_records(
            """
            SELECT transition_id, old_claim_id, new_claim_id, transition_type, reason,
                   evidence_type, evidence_id, created_at
            FROM fact_transitions
            WHERE old_claim_id = ? OR new_claim_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (claim_id, claim_id, limit),
        )

    def list_profile_snapshot_records(
        self,
        *,
        paragraph_hashes: Sequence[str],
        fact_ids: Sequence[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        profile_match_clauses: List[str] = []
        profile_params: List[Any] = []
        if paragraph_hashes:
            placeholders = self._memory_placeholders(paragraph_hashes)
            profile_match_clauses.append(
                f"""
            EXISTS (
                SELECT 1 FROM json_each(COALESCE(s.evidence_ids_json, '[]')) evidence
                WHERE CAST(evidence.value AS TEXT) IN ({placeholders})
            )
            """
            )
            profile_params.extend(paragraph_hashes)
        if fact_ids:
            placeholders = self._memory_placeholders(fact_ids)
            profile_match_clauses.append(
                f"""
            EXISTS (
                SELECT 1 FROM json_each(COALESCE(s.fact_claim_ids_json, '[]')) claim
                WHERE CAST(claim.value AS TEXT) IN ({placeholders})
            )
            """
            )
            profile_params.extend(fact_ids)
        if not profile_match_clauses:
            return []
        return self.query_memory_records(
            f"""
            SELECT s.person_id, s.profile_version, s.profile_text,
                   s.updated_at, s.source_note
            FROM person_profile_snapshots s
            JOIN (
                SELECT person_id, MAX(profile_version) AS max_version
                FROM person_profile_snapshots GROUP BY person_id
            ) latest ON latest.person_id = s.person_id AND latest.max_version = s.profile_version
            WHERE {" OR ".join(profile_match_clauses)}
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (*profile_params, limit),
        )

    def list_relation_graph_projection_jobs(self, relation_ids: Sequence[str]) -> List[Dict[str, Any]]:
        placeholders = self._memory_placeholders(relation_ids)
        return self.query_memory_records(
            f"""
            SELECT relation_hash, desired_active, status, attempt_count, last_error, updated_at
            FROM relation_graph_projection_jobs
            WHERE relation_hash IN ({placeholders})
            ORDER BY updated_at DESC
            """,
            tuple(relation_ids),
        )

    def query_delete_operation_id_for_item(self, token: str) -> List[Dict[str, Any]]:
        return self.query_memory_rows(
            """
            SELECT operation_id
            FROM delete_operation_items
            WHERE item_hash = ?
               OR item_key = ?
               OR payload_json LIKE ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (token, token, f"%{token}%"),
        )

    def list_timeline_paragraph_rows(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = self._append_limit(
            """
        SELECT hash, content, created_at, updated_at, metadata, source, is_deleted, deleted_at
        FROM paragraphs
        ORDER BY COALESCE(updated_at, created_at, 0) DESC
        """,
            limit,
        )
        return self.query_memory_rows(sql, (limit,) if limit is not None else ())

    def list_timeline_episode_rows(self, sources: Sequence[str], limit: Optional[int] = None) -> List[Dict[str, Any]]:
        placeholders = self._memory_placeholders(sources)
        sql = self._append_limit(
            f"""
        SELECT episode_id, source, title, summary, paragraph_count, created_at, updated_at, event_time_start, event_time_end
        FROM episodes
        WHERE source IN ({placeholders})
        ORDER BY COALESCE(updated_at, created_at, event_time_start, 0) DESC
        """,
            limit,
        )
        return self.query_memory_rows(sql, (*sources, *((limit,) if limit is not None else ())))

    def list_timeline_feedback_task_rows(
        self,
        session_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        sql = self._append_limit(
            """
        SELECT *
        FROM memory_feedback_tasks
        WHERE session_id = ?
        ORDER BY COALESCE(updated_at, query_timestamp, created_at, 0) DESC
        """,
            limit,
        )
        return self.query_memory_rows(sql, (session_id, *((limit,) if limit is not None else ())))

    def get_paragraph_source_row(self, paragraph_hash: str) -> List[Dict[str, Any]]:
        return self.query_memory_rows(
            "SELECT hash, metadata, source FROM paragraphs WHERE hash = ? LIMIT 1",
            (paragraph_hash,),
        )

    def list_timeline_delete_operation_rows(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = self._append_limit(
            """
        SELECT operation_id, mode, selector, reason, requested_by, status, created_at, restored_at, summary_json
        FROM delete_operations
        ORDER BY COALESCE(restored_at, created_at, 0) DESC
        """,
            limit,
        )
        return self.query_memory_rows(sql, (limit,) if limit is not None else ())

    def list_timeline_delete_operation_item_rows(self, operation_ids: Sequence[str]) -> List[Dict[str, Any]]:
        placeholders = ",".join("?" for _ in operation_ids)
        return self.query_memory_rows(
            f"""
            SELECT operation_id, item_type, item_hash, item_key, payload_json, created_at
            FROM delete_operation_items
            WHERE operation_id IN ({placeholders})
            ORDER BY operation_id ASC, id ASC
            """,
            tuple(operation_ids),
        )

    def list_timeline_profile_snapshot_rows(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = self._append_limit(
            """
        SELECT DISTINCT pps.person_id, pps.profile_version, pps.updated_at, pps.source_note
        FROM person_profile_snapshots pps
        JOIN paragraph_entities pe ON pe.entity_hash = pps.person_id OR pe.entity_hash IN (
            SELECT hash FROM entities WHERE name = pps.person_id
        )
        JOIN paragraphs p ON p.hash = pe.paragraph_hash
        ORDER BY pps.updated_at DESC
        """,
            limit,
        )
        return self.query_memory_rows(sql, (limit,) if limit is not None else ())

    def list_timeline_profile_paragraph_rows(self, person_ids: Sequence[str]) -> List[Dict[str, Any]]:
        placeholders = ",".join("?" for _ in person_ids)
        return self.query_memory_rows(
            f"""
            SELECT pe.entity_hash, e.name AS entity_name, p.hash, p.metadata, p.source
            FROM paragraph_entities pe
            LEFT JOIN entities e ON e.hash = pe.entity_hash
            JOIN paragraphs p ON p.hash = pe.paragraph_hash
            WHERE pe.entity_hash IN ({placeholders}) OR e.name IN ({placeholders})
            """,
            (*person_ids, *person_ids),
        )

    def list_timeline_profile_override_rows(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = self._append_limit(
            """
        SELECT person_id, updated_at, updated_by, source
        FROM person_profile_overrides
        ORDER BY updated_at DESC
        """,
            limit,
        )
        return self.query_memory_rows(sql, (limit,) if limit is not None else ())

    def list_timeline_maintenance_relation_rows(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = self._append_limit(
            """
        SELECT r.hash, r.subject, r.predicate, r.object, r.source_paragraph, r.last_reinforced,
               r.inactive_since, r.protected_until, r.metadata, p.source, p.metadata AS paragraph_metadata
        FROM relations r
        LEFT JOIN paragraphs p ON p.hash = r.source_paragraph
        ORDER BY COALESCE(r.last_reinforced, r.inactive_since, r.protected_until, r.created_at, 0) DESC
        """,
            limit,
        )
        return self.query_memory_rows(sql, (limit,) if limit is not None else ())

    def get_graph_paragraph_row(self, paragraph_hash: str) -> List[Dict[str, Any]]:
        return self.query_memory_rows(
            """
        SELECT hash, content, source, created_at, updated_at, metadata, is_deleted, deleted_at
        FROM paragraphs
        WHERE hash = ?
        LIMIT 1
        """,
            (paragraph_hash,),
        )

    def list_graph_paragraph_entity_rows(self, paragraph_hash: str) -> List[Dict[str, Any]]:
        return self.query_memory_rows(
            """
            SELECT e.hash, e.name, pe.mention_count
            FROM paragraph_entities pe
            LEFT JOIN entities e ON e.hash = pe.entity_hash
            WHERE pe.paragraph_hash = ?
            ORDER BY COALESCE(pe.mention_count, 1) DESC, e.name ASC
            """,
            (paragraph_hash,),
        )

    def list_graph_paragraph_relation_rows(self, paragraph_hash: str) -> List[Dict[str, Any]]:
        return self.query_memory_rows(
            """
            SELECT r.hash, r.subject, r.predicate, r.object, r.confidence
            FROM paragraph_relations pr
            JOIN relations r ON r.hash = pr.relation_hash
            WHERE pr.paragraph_hash = ?
              AND (r.is_inactive IS NULL OR r.is_inactive = 0)
            ORDER BY r.confidence DESC, r.created_at DESC
            """,
            (paragraph_hash,),
        )


memory_service = MemoryService()
