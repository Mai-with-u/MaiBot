"""将群聊窗口接入当前 A_Memorix 内核的向量空间和记忆可见性规则。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from src.A_memorix.core.retrieval import RetrievalResult, RetrievalScope, TopicGraphConfig, TopicGraphRetriever
from src.A_memorix.core.utils.async_work import cancellation_safe_to_thread

if TYPE_CHECKING:
    from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel


class RecentChatMemorySearch:
    def __init__(self) -> None:
        self.retriever: Optional[TopicGraphRetriever] = None

    async def search(
        self,
        kernel: SDKMemoryKernel,
        *,
        messages: Sequence[str],
        chat_id: str,
        group_id: str,
        shared_chat_ids: Sequence[str],
        window_size: int,
        limit: int,
    ) -> Dict[str, Any]:
        if not chat_id or not group_id:
            raise ValueError("群聊召回需要真实会话 ID 和群 ID")
        if kernel._is_chat_filtered(respect_filter=True, stream_id=chat_id, group_id=group_id):
            return {"hits": [], "filtered": True}
        if not messages or limit <= 0:
            return {"hits": []}

        channels = kernel._runtime_capability_status()["available_channels"]
        if not {"embedding", "vector_read", "graph", "metadata"}.issubset(channels):
            return {"success": False, "hits": [], "error": "topic_graph_unavailable"}

        hit_service = kernel._get_search_hit_service()
        scope = await cancellation_safe_to_thread(hit_service._resolve_retrieval_scope, chat_id, shared_chat_ids)
        if scope is None or not scope.paragraph_ids:
            return {"hits": []}

        dual = kernel._dual_vector_pools_enabled()
        paragraphs = kernel.paragraph_vector_store if dual else kernel.vector_store
        graph_vectors = kernel.graph_vector_store if dual else kernel.vector_store
        retriever = self.retriever
        if (
            retriever is None
            or retriever.config.max_recent_messages != window_size
            or retriever.paragraph_vector_store is not paragraphs
            or retriever.graph_vector_store is not graph_vectors
            or retriever.graph_store is not kernel.graph_store
            or retriever.metadata_store is not kernel.metadata_store
            or retriever.embedding_manager is not kernel.embedding_manager
            or retriever.sparse_index is not kernel.sparse_index
        ):
            retriever = self.retriever = TopicGraphRetriever(
                paragraph_vector_store=paragraphs,
                graph_vector_store=graph_vectors,
                graph_store=kernel.graph_store,
                metadata_store=kernel.metadata_store,
                embedding_manager=kernel.embedding_manager,
                sparse_index=kernel.sparse_index,
                config=TopicGraphConfig(max_recent_messages=window_size),
                graph_projection_lock=kernel._relation_graph_projection_lock,
            )
        results = await retriever.retrieve_from_messages(messages, top_k=limit, scope=scope)
        hits = await cancellation_safe_to_thread(self._visible_hits, kernel, results, scope, chat_id, group_id)
        return {"hits": hits}

    @staticmethod
    def _visible_hits(
        kernel: SDKMemoryKernel,
        results: Sequence[RetrievalResult],
        scope: RetrievalScope,
        chat_id: str,
        group_id: str,
    ) -> List[Dict[str, Any]]:
        # 按当前有效段落回表，避免注入已撤销内容或全图库的话题摘要。
        candidates = [item for item in results if item.hash_value in scope.paragraph_ids]
        rows = kernel.metadata_store.get_paragraphs_by_hashes([item.hash_value for item in candidates])
        hits = []
        for item in candidates:
            row = rows.get(item.hash_value)
            if row is not None:
                hits.append(
                    {
                        "hash": item.hash_value,
                        "type": "paragraph",
                        "score": item.score,
                        "content": row["content"],
                        "source": row["source"],
                        "metadata": row["metadata"],
                    }
                )
        hits = kernel._filter_user_visible_hits(hits)
        return kernel._filter_hits_by_retrieval_type_scope(hits, current_stream_id=chat_id, current_group_id=group_id)
