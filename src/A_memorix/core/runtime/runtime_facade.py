from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Coroutine, Dict, Optional, Sequence

from ..utils.memory_lifecycle_policy import RelationLifecycleEvent

if TYPE_CHECKING:
    from ..storage import GraphStore, MetadataStore, VectorStore
    from ..utils.relation_write_service import RelationWriteService
    from .sdk_memory_kernel import SDKMemoryKernel


class KernelRuntimeFacade:
    """向导入、调优等协作组件暴露受控的内核运行时能力。"""

    def __init__(self, kernel: SDKMemoryKernel) -> None:
        self._kernel = kernel
        self.config = kernel.config
        self._plugin_config = kernel.config
        self._runtime_self_check_report: Dict[str, Any] = {}

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._kernel._cfg(key, default)

    def is_runtime_ready(self) -> bool:
        return self._kernel.is_runtime_ready()

    def is_chat_enabled(self, stream_id: str, group_id: str | None = None, user_id: str | None = None) -> bool:
        return self._kernel.is_chat_enabled(stream_id=stream_id, group_id=group_id, user_id=user_id)

    async def reinforce_access(self, relation_hashes: Sequence[str]) -> None:
        if self._kernel.metadata_store is None or self._kernel.graph_store is None:
            return
        hashes = list(
            dict.fromkeys(str(item or "").strip() for item in relation_hashes if str(item or "").strip())
        )
        if not hashes:
            return
        service = self._kernel._maintenance_service
        type(service).apply_relation_lifecycle_event(
            service,
            hashes,
            event=RelationLifecycleEvent.ACCESS,
        )

    async def execute_request_with_dedup(
        self,
        request_key: str,
        executor: Callable[[], Coroutine[Any, Any, Dict[str, Any]]],
    ) -> tuple[bool, Dict[str, Any]]:
        return await self._kernel.execute_request_with_dedup(request_key, executor)

    async def apply_retrieval_tuning_profile(
        self,
        profile: Dict[str, Any],
        *,
        validate: bool = True,
    ) -> Dict[str, Any]:
        return await self._kernel.apply_retrieval_tuning_profile(profile, validate=validate)

    @property
    def vector_store(self) -> Optional[VectorStore]:
        return self._kernel.vector_store

    @property
    def paragraph_vector_store(self) -> Optional[VectorStore]:
        return self._kernel.paragraph_vector_store

    @property
    def graph_vector_store(self) -> Optional[VectorStore]:
        return self._kernel.graph_vector_store

    @property
    def graph_store(self) -> Optional[GraphStore]:
        return self._kernel.graph_store

    @property
    def metadata_store(self) -> Optional[MetadataStore]:
        return self._kernel.metadata_store

    @property
    def embedding_manager(self):
        return self._kernel.embedding_manager

    @property
    def sparse_index(self):
        return self._kernel.sparse_index

    @property
    def relation_write_service(self) -> Optional[RelationWriteService]:
        return self._kernel.relation_write_service

    def is_embedding_degraded(self) -> bool:
        return self._kernel._is_embedding_degraded()

    def _dual_vector_pools_enabled(self) -> bool:
        return self._kernel._dual_vector_pools_enabled()

    def allow_metadata_only_write(self) -> bool:
        return self._kernel._allow_metadata_only_write()

    async def ingest_text(self, **kwargs: Any) -> Dict[str, Any]:
        """让派生写入统一复用内核的 external ID 幂等入口。"""

        return await self._kernel.ingest_text(**kwargs)

    async def write_paragraph_vector_or_enqueue(
        self,
        *,
        paragraph_hash: str,
        content: str,
        context: str = "",
    ) -> Dict[str, Any]:
        return await self._kernel._write_paragraph_vector_or_enqueue(
            paragraph_hash=paragraph_hash,
            content=content,
            context=context,
        )

    def enqueue_paragraph_vector_backfill(
        self,
        paragraph_hash: str,
        *,
        error: str = "",
    ) -> None:
        self._kernel._enqueue_paragraph_vector_backfill(paragraph_hash, error=error)
