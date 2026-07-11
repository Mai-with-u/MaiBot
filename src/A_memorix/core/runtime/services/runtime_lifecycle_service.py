from __future__ import annotations

from typing import List

from src.common.logger import get_logger

from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")


class MemoryRuntimeLifecycleService(KernelServiceBase):
    """按固定顺序创建和释放存储、检索及后台任务资源。"""

    async def initialize(self) -> None:
        """完成格式迁移、存储装载、检索组装和后台任务启动。

        重复调用不会重建存储，只会刷新运行时稀疏模式并补齐后台任务。
        ``_initialized`` 仅在所有同步依赖组装完成后置为真。
        """
        from .. import sdk_memory_kernel as kernel_module

        if self._initialized:
            self._apply_runtime_sparse_mode()
            await self._start_background_tasks()
            return

        self.data_dir.mkdir(parents=True, exist_ok=True)
        kernel_module.run_startup_format_migration(self.data_dir)
        self.embedding_manager = kernel_module.create_embedding_api_adapter(
            batch_size=int(self._cfg("embedding.batch_size", 32)),
            max_concurrent=int(self._cfg("embedding.max_concurrent", 5)),
            default_dimension=self.embedding_dimension,
            enable_cache=bool(self._cfg("embedding.enable_cache", False)),
            model_name=str(self._cfg("embedding.model_name", "auto") or "auto"),
            dimension_request_mode=str(self._cfg("embedding.dimension_request_mode", "explicit") or "explicit"),
            retry_config=self._cfg("embedding.retry", {}) or {},
        )
        stored_dimension = self._stored_vector_dimension()
        provisional_dimension = stored_dimension or self.embedding_dimension
        self.embedding_dimension = int(provisional_dimension)

        matrix_format = str(self._cfg("graph.sparse_matrix_format", "csr") or "csr").strip().lower()
        graph_format = (
            kernel_module.SparseMatrixFormat.CSC
            if matrix_format == "csc"
            else kernel_module.SparseMatrixFormat.CSR
        )

        self.vector_store = self._make_vector_store(self._vectors_root(), dimension=provisional_dimension)
        self.paragraph_vector_store = self._make_vector_store(
            self._paragraph_vector_dir(),
            dimension=provisional_dimension,
        )
        self.graph_vector_store = self._make_vector_store(
            self._graph_vector_dir(),
            dimension=provisional_dimension,
        )
        self.graph_store = kernel_module.GraphStore(matrix_format=graph_format, data_dir=self.data_dir / "graph")
        self.metadata_store = kernel_module.MetadataStore(data_dir=self.data_dir / "metadata")
        self.metadata_store.connect()

        skip_vector_load = False
        if self.graph_store.has_data():
            self.graph_store.load()

        sparse_cfg_raw = self._cfg("retrieval.sparse", {}) or {}
        try:
            sparse_cfg = kernel_module.SparseBM25Config(**sparse_cfg_raw)
        except Exception as exc:
            logger.warning(f"sparse 配置非法，回退默认: {exc}")
            sparse_cfg = kernel_module.SparseBM25Config()
        self.sparse_index = kernel_module.SparseBM25Index(metadata_store=self.metadata_store, config=sparse_cfg)
        if getattr(self.sparse_index.config, "enabled", False):
            warmup_summary = self.sparse_index.warmup()
            if warmup_summary.get("ok"):
                logger.info(
                    "[sdk] 稀疏索引预热完成: "
                    f"backend={warmup_summary.get('backend')}, "
                    f"docs={warmup_summary.get('doc_count')}, "
                    f"duration_ms={float(warmup_summary.get('duration_ms', 0.0)):.2f}"
                )
            else:
                logger.warning(
                    "[sdk] 稀疏索引预热失败，后续检索将按需重试: "
                    f"{warmup_summary.get('error', 'unknown')}"
                )

        if not skip_vector_load and self.vector_store.has_data():
            self.vector_store.load()
            self.vector_store.warmup_index(force_train=True)
        self._dual_vector_pools_ready = False
        if self._dual_vector_pools_config_enabled():
            self._cleanup_stale_dual_vector_build_dirs()
            if not self._reload_dual_vector_stores_from_disk():
                logger.warning("双池配置已开启，但 ready manifest 不可用，当前按单池检索与写入运行")

        self._refresh_relation_write_service()

        runtime_config = self._build_runtime_config()
        self._runtime_bundle = kernel_module.build_search_runtime(
            plugin_config=runtime_config,
            logger_obj=kernel_module.logger,
            owner_tag="sdk_kernel",
            log_prefix="[sdk]",
        )
        if not self._runtime_bundle.ready:
            raise RuntimeError(self._runtime_bundle.error or "检索运行时初始化失败")

        self.retriever = self._runtime_bundle.retriever
        self.threshold_filter = self._runtime_bundle.threshold_filter
        self.sparse_index = self._runtime_bundle.sparse_index or self.sparse_index
        self._apply_runtime_sparse_mode()

        self._refresh_runtime_dependents(preserve_managers=True)
        self.import_task_manager = kernel_module.ImportTaskManager(self._runtime_facade)
        self.retrieval_tuning_manager = kernel_module.RetrievalTuningManager(
            self._runtime_facade,
            import_write_blocked_provider=self.import_task_manager.is_write_blocked,
        )

        vector_pools_status = self._vector_pools_status()
        configured_pool_mode = str(vector_pools_status.get("configured_mode", "single"))
        effective_pool_mode = str(vector_pools_status.get("effective_mode", "single"))
        pool_mode_label = (
            effective_pool_mode
            if configured_pool_mode == effective_pool_mode
            else f"{effective_pool_mode}(configured={configured_pool_mode})"
        )
        logger.info(
            "[sdk] 向量存储初始化完成: "
            f"dim={self.embedding_dimension}, mode=SQ8, vector_pools={pool_mode_label}"
        )

        self._mark_startup_self_check_deferred()

        self._initialized = True
        await self._start_background_tasks()

    async def shutdown(self) -> None:
        """先等待后台工作和任务管理器退出，再持久化并释放底层存储。"""
        await self._stop_background_tasks()
        shutdown_errors: List[Exception] = []
        if self.import_task_manager is not None:
            try:
                await self.import_task_manager.shutdown()
            except Exception as exc:
                shutdown_errors.append(exc)
                logger.exception(f"关闭导入任务管理器失败: {exc}")
        if self.retrieval_tuning_manager is not None:
            try:
                await self.retrieval_tuning_manager.shutdown()
            except Exception as exc:
                shutdown_errors.append(exc)
                logger.exception(f"关闭调优任务管理器失败: {exc}")
        if shutdown_errors:
            raise RuntimeError("A_Memorix 任务管理器未能安全退出，底层存储保持打开") from shutdown_errors[0]
        self._close_runtime()

    def close(self) -> None:
        """同步释放非活动内核；活动内核必须使用 ``shutdown()``。"""
        if self._initialized:
            raise RuntimeError("A_Memorix 运行时仍处于活动状态，请先 await shutdown()")
        self._close_runtime()

    def _close_runtime(self) -> None:
        """持久化并释放资源，调用前必须确认没有任务仍会访问存储。"""
        try:
            self._persist()
        finally:
            if self.metadata_store is not None:
                self.metadata_store.close()
            self._initialized = False
            self._request_dedup_tasks.clear()
            self._runtime_facade._runtime_self_check_report = {}
            self._background_tasks.clear()
            self._active_person_timestamps.clear()
            self._embedding_degraded = {
                "active": False,
                "reason": "",
                "since": None,
                "last_check": None,
            }
