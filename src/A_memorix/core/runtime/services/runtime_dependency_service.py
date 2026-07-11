from __future__ import annotations

from .base import KernelServiceBase


class MemoryRuntimeDependencyService(KernelServiceBase):
    def _refresh_relation_write_service(self) -> None:
        from .. import sdk_memory_kernel as kernel_module

        if (
            self.metadata_store is None
            or self.graph_store is None
            or self.vector_store is None
            or self.embedding_manager is None
        ):
            self.relation_write_service = None
            return
        self.relation_write_service = kernel_module.RelationWriteService(
            metadata_store=self.metadata_store,
            graph_store=self.graph_store,
            vector_store=self.vector_store,
            graph_vector_store=self._graph_vector_store(),
            embedding_manager=self.embedding_manager,
            use_typed_relation_ids=self._dual_vector_pools_enabled(),
        )

    def _refresh_runtime_dependents(self, *, preserve_managers: bool = True) -> None:
        from .. import sdk_memory_kernel as kernel_module

        if (
            self.metadata_store is None
            or self.graph_store is None
            or self.vector_store is None
            or self.embedding_manager is None
            or self.retriever is None
        ):
            return

        runtime_config = self._build_runtime_config()
        self.episode_retriever = kernel_module.EpisodeRetrievalService(
            metadata_store=self.metadata_store,
            retriever=self.retriever,
        )
        self.aggregate_query_service = kernel_module.AggregateQueryService(plugin_config=runtime_config)
        self.person_profile_service = kernel_module.PersonProfileService(
            metadata_store=self.metadata_store,
            graph_store=self.graph_store,
            vector_store=self.vector_store,
            paragraph_vector_store=self.paragraph_vector_store or self.vector_store,
            graph_vector_store=self.graph_vector_store or self.vector_store,
            embedding_manager=self.embedding_manager,
            sparse_index=self.sparse_index,
            plugin_config=runtime_config,
            retriever=self.retriever,
        )
        self.episode_segmentation_service = kernel_module.EpisodeSegmentationService(plugin_config=runtime_config)
        self.episode_service = kernel_module.EpisodeService(
            metadata_store=self.metadata_store,
            plugin_config=runtime_config,
            segmentation_service=self.episode_segmentation_service,
        )
        self.summary_importer = kernel_module.SummaryImporter(
            vector_store=self.vector_store,
            graph_store=self.graph_store,
            metadata_store=self.metadata_store,
            embedding_manager=self.embedding_manager,
            plugin_config=runtime_config,
        )
        if not preserve_managers:
            self.import_task_manager = kernel_module.ImportTaskManager(self._runtime_facade)
            self.retrieval_tuning_manager = kernel_module.RetrievalTuningManager(
                self._runtime_facade,
                import_write_blocked_provider=self.import_task_manager.is_write_blocked,
            )

    def _persist(self, *, force_vectors: bool = False) -> None:
        from .. import sdk_memory_kernel as kernel_module

        rebuild_required = (
            False if force_vectors else bool(self._vector_rebuild_status().get("vector_rebuild_required", False))
        )
        if self.vector_store is not None and not self._dual_vector_pools_enabled():
            if rebuild_required:
                kernel_module.logger.debug("检测到向量库需要重建，跳过向量库持久化以保留重建提示")
            else:
                self._save_vector_store(self.vector_store)
        if self._dual_vector_pools_enabled() and not rebuild_required:
            if self.paragraph_vector_store is not None:
                self._save_vector_store(self.paragraph_vector_store)
            if self.graph_vector_store is not None:
                self._save_vector_store(self.graph_vector_store)
        if self.graph_store is not None:
            self.graph_store.save()
        if self.sparse_index is not None and getattr(self.sparse_index.config, "enabled", False):
            self.sparse_index.ensure_loaded()
