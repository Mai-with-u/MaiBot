from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from src.A_memorix.core.retrieval import RetrievalResult
from src.A_memorix.core.utils.search_execution_service import (
    SearchExecutionRequest,
    SearchExecutionResult,
    SearchExecutionService,
)

from ..models import KernelSearchRequest, _NormalizedSearchTimeWindow
from .base import KernelServiceBase


class MemorySearchService(KernelServiceBase):
    """统一 SDK 检索入口，并在返回前收敛聊天范围和可见性规则。"""

    async def search_memory(self, request: KernelSearchRequest) -> Dict[str, Any]:
        """执行 search、time、hybrid、episode 或 aggregate 检索。

        共享聊天流只扩大候选召回范围，最终结果仍会经过人物、删除状态、聊天范围
        和检索类型过滤。普通检索交给 ``SearchExecutionService``，Episode 与聚合
        模式保留各自的执行器，最终统一转换为 SDK 命中结构。
        """
        if self._is_chat_filtered(
            respect_filter=request.respect_filter,
            stream_id=request.chat_id,
            group_id=request.group_id,
            user_id=request.user_id,
        ):
            return {"summary": "", "hits": [], "filtered": True}

        await self.initialize()
        assert self.retriever is not None
        assert self.episode_retriever is not None
        assert self.aggregate_query_service is not None

        mode = str(request.mode or "search").strip().lower() or "search"
        query = str(request.query or "").strip()
        limit = max(1, int(request.limit or 5))
        shared_chat_ids = tuple(str(item or "").strip() for item in request.shared_chat_ids if str(item or "").strip())
        scoped_limit = self._scoped_search_limit(limit, chat_id=request.chat_id, shared_chat_ids=shared_chat_ids)
        supported_modes = {"search", "time", "hybrid", "episode", "aggregate"}
        if mode not in supported_modes:
            return {
                "summary": "",
                "hits": [],
                "error": (
                    f"不支持的检索模式: {mode}（仅支持 search/time/hybrid/episode/aggregate，"
                    "semantic 已移除）"
                ),
            }
        try:
            time_window = self._normalize_search_time_window(request.time_start, request.time_end)
        except ValueError as exc:
            return {"summary": "", "hits": [], "error": str(exc)}

        if mode == "episode":
            rows = await self._episode_query_for_chat_scope(
                query=query,
                top_k=scoped_limit,
                time_from=time_window.numeric_start,
                time_to=time_window.numeric_end,
                person=request.person_id or None,
                chat_id=request.chat_id,
                shared_chat_ids=shared_chat_ids,
            )
            hits = self._filter_episode_hits([self._episode_hit(row) for row in rows])
            hits = self._filter_hits_by_chat_scope(hits, request.chat_id, shared_chat_ids)
            if request.respect_filter:
                hits = self._filter_hits_by_retrieval_type_scope(
                    hits,
                    current_stream_id=request.chat_id,
                    current_group_id=request.group_id,
                    current_user_id=request.user_id,
                )
            hits = hits[:limit]
            return {"summary": self._summary(hits), "hits": hits}

        if mode == "aggregate":
            payload = await self.aggregate_query_service.execute(
                query=query,
                top_k=scoped_limit,
                mix=True,
                mix_top_k=scoped_limit,
                time_from=time_window.query_start,
                time_to=time_window.query_end,
                search_runner=lambda: self._aggregate_search(query, scoped_limit, request),
                time_runner=lambda: self._aggregate_time(query, scoped_limit, request, time_window),
                episode_runner=lambda: self._aggregate_episode(query, scoped_limit, request, time_window),
            )
            hits = [dict(item) for item in payload.get("mixed_results", []) if isinstance(item, dict)]
            for item in hits:
                item.setdefault("metadata", {})
            filtered = self._filter_hits(hits, request.person_id)
            filtered = self._filter_user_visible_hits(filtered)
            filtered = self._filter_hits_by_chat_scope(filtered, request.chat_id, shared_chat_ids)
            if request.respect_filter:
                filtered = self._filter_hits_by_retrieval_type_scope(
                    filtered,
                    current_stream_id=request.chat_id,
                    current_group_id=request.group_id,
                    current_user_id=request.user_id,
                )
            filtered = filtered[:limit]
            return {"summary": self._summary(filtered), "hits": filtered}

        query_type = mode
        runtime_config = self._build_runtime_config()
        result = await self._search_execution_for_chat_scope(
            caller="sdk_memory_kernel",
            query_type=query_type,
            query=query,
            top_k=scoped_limit,
            request=request,
            time_from=time_window.query_start,
            time_to=time_window.query_end,
            plugin_config=runtime_config,
            enforce_chat_filter=bool(request.respect_filter),
        )
        if not result.success:
            return {"summary": "", "hits": [], "error": result.error}
        if result.chat_filtered:
            return {"summary": "", "hits": [], "filtered": True}

        hits = [self._retrieval_result_hit(item) for item in result.results]
        filtered = self._filter_hits(hits, request.person_id)
        filtered = self._filter_user_visible_hits(filtered)
        filtered = self._filter_hits_by_chat_scope(filtered, request.chat_id, shared_chat_ids)
        if request.respect_filter:
            filtered = self._filter_hits_by_retrieval_type_scope(
                filtered,
                current_stream_id=request.chat_id,
                current_group_id=request.group_id,
                current_user_id=request.user_id,
            )
        filtered = filtered[:limit]
        return {"summary": self._summary(filtered), "hits": filtered}

    async def _aggregate_search(self, query: str, limit: int, request: KernelSearchRequest) -> Dict[str, Any]:
        shared_chat_ids = tuple(str(item or "").strip() for item in request.shared_chat_ids if str(item or "").strip())
        result = await self._search_execution_for_chat_scope(
            caller="sdk_memory_kernel.aggregate",
            query_type="search",
            query=query,
            top_k=limit,
            request=request,
            plugin_config=self._build_runtime_config(),
            enforce_chat_filter=False,
        )
        hits = [self._retrieval_result_hit(item) for item in result.results] if result.success else []
        hits = self._filter_hits_by_chat_scope(hits, request.chat_id, shared_chat_ids)
        return {"success": result.success, "results": hits, "count": len(hits), "query_type": "search", "error": result.error}

    async def _aggregate_time(
        self,
        query: str,
        limit: int,
        request: KernelSearchRequest,
        time_window: _NormalizedSearchTimeWindow,
    ) -> Dict[str, Any]:
        shared_chat_ids = tuple(str(item or "").strip() for item in request.shared_chat_ids if str(item or "").strip())
        result = await self._search_execution_for_chat_scope(
            caller="sdk_memory_kernel.aggregate",
            query_type="time",
            query=query,
            top_k=limit,
            request=request,
            time_from=time_window.query_start,
            time_to=time_window.query_end,
            plugin_config=self._build_runtime_config(),
            enforce_chat_filter=False,
        )
        hits = [self._retrieval_result_hit(item) for item in result.results] if result.success else []
        hits = self._filter_hits_by_chat_scope(hits, request.chat_id, shared_chat_ids)
        return {"success": result.success, "results": hits, "count": len(hits), "query_type": "time", "error": result.error}

    async def _aggregate_episode(
        self,
        query: str,
        limit: int,
        request: KernelSearchRequest,
        time_window: _NormalizedSearchTimeWindow,
    ) -> Dict[str, Any]:
        assert self.episode_retriever
        shared_chat_ids = tuple(str(item or "").strip() for item in request.shared_chat_ids if str(item or "").strip())
        rows = await self._episode_query_for_chat_scope(
            query=query,
            top_k=limit,
            time_from=time_window.numeric_start,
            time_to=time_window.numeric_end,
            person=request.person_id or None,
            chat_id=request.chat_id,
            shared_chat_ids=shared_chat_ids,
        )
        hits = self._filter_episode_hits([self._episode_hit(row) for row in rows])
        hits = self._filter_hits_by_chat_scope(hits, request.chat_id, shared_chat_ids)
        return {"success": True, "results": hits, "count": len(hits), "query_type": "episode"}

    async def _search_execution_once(
        self,
        *,
        caller: str,
        query_type: str,
        query: str,
        top_k: int,
        request: KernelSearchRequest,
        plugin_config: dict,
        source: Optional[str],
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
        enforce_chat_filter: bool,
    ) -> SearchExecutionResult:
        return await SearchExecutionService.execute(
            retriever=self.retriever,
            threshold_filter=self.threshold_filter,
            plugin_config=plugin_config,
            request=SearchExecutionRequest(
                caller=caller,
                stream_id=str(request.chat_id or "") or None,
                group_id=str(request.group_id or "") or None,
                user_id=str(request.user_id or "") or None,
                query_type=query_type,
                query=query,
                top_k=top_k,
                time_from=time_from,
                time_to=time_to,
                person=str(request.person_id or "") or None,
                source=source,
                use_threshold=True,
                enable_ppr=bool(self._cfg("retrieval.enable_ppr", True)),
            ),
            enforce_chat_filter=enforce_chat_filter,
            reinforce_access=True,
        )

    async def _search_execution_for_chat_scope(
        self,
        *,
        caller: str,
        query_type: str,
        query: str,
        top_k: int,
        request: KernelSearchRequest,
        plugin_config: dict,
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
        enforce_chat_filter: bool,
    ) -> SearchExecutionResult:
        allowed_chat_ids = self._resolve_allowed_chat_ids(request.chat_id, request.shared_chat_ids)
        if len(allowed_chat_ids) <= 1:
            search_source = self._chat_source_for_search_scope(request.chat_id, request.shared_chat_ids)
            return await self._search_execution_once(
                caller=caller,
                query_type=query_type,
                query=query,
                top_k=top_k,
                request=request,
                plugin_config=plugin_config,
                source=search_source,
                time_from=time_from,
                time_to=time_to,
                enforce_chat_filter=enforce_chat_filter,
            )

        scoped_results: List[RetrievalResult] = []
        errors: List[str] = []
        chat_filtered = False
        for chat_id in sorted(allowed_chat_ids):
            result = await self._search_execution_once(
                caller=caller,
                query_type=query_type,
                query=query,
                top_k=top_k,
                request=request,
                plugin_config=plugin_config,
                source=self._chat_source(chat_id),
                time_from=time_from,
                time_to=time_to,
                enforce_chat_filter=False,
            )
            if result.chat_filtered:
                chat_filtered = True
            if not result.success:
                if result.error:
                    errors.append(result.error)
                continue
            scoped_results.extend(result.results)

        merged_results = self._dedupe_ranked_items(scoped_results, limit=top_k)
        return SearchExecutionResult(
            success=bool(merged_results) or not errors,
            error="; ".join(dict.fromkeys(errors)),
            query_type=query_type,
            query=query,
            top_k=top_k,
            time_from=time_from,
            time_to=time_to,
            person=str(request.person_id or "") or None,
            source=None,
            results=merged_results,
            chat_filtered=chat_filtered and not merged_results,
        )

    async def _episode_query_for_chat_scope(
        self,
        *,
        query: str,
        top_k: int,
        time_from: Optional[float],
        time_to: Optional[float],
        person: Optional[str],
        chat_id: str,
        shared_chat_ids: Sequence[str] = (),
    ) -> List[Any]:
        assert self.episode_retriever is not None
        allowed_chat_ids = self._resolve_allowed_chat_ids(chat_id, shared_chat_ids)
        if len(allowed_chat_ids) <= 1:
            return await self.episode_retriever.query(
                query=query,
                top_k=top_k,
                time_from=time_from,
                time_to=time_to,
                person=person,
                source=self._chat_source_for_search_scope(chat_id, shared_chat_ids),
            )

        rows: List[Any] = []
        for allowed_chat_id in sorted(allowed_chat_ids):
            rows.extend(
                await self.episode_retriever.query(
                    query=query,
                    top_k=top_k,
                    time_from=time_from,
                    time_to=time_to,
                    person=person,
                    source=self._chat_source(allowed_chat_id),
                )
            )
        return self._dedupe_ranked_items(rows, limit=top_k)

    def _filter_hits_by_chat_scope(
        self,
        hits: List[Dict[str, Any]],
        chat_id: str,
        shared_chat_ids: Sequence[str] = (),
    ) -> List[Dict[str, Any]]:
        service = self._kernel._get_search_hit_service()
        return type(service)._filter_hits_by_chat_scope(service, hits, chat_id, shared_chat_ids)
