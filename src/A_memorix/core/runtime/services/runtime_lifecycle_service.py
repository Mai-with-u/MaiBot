from __future__ import annotations

from typing import List

import time

from src.common.logger import get_logger

from ...storage import VectorStoreIntegrityError
from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")


class MemoryRuntimeLifecycleService(KernelServiceBase):
    """按固定顺序创建和释放存储、检索及后台任务资源。"""

    async def initialize(self) -> None:
        """持有数据目录独占写者锁后初始化完整运行时。"""

        async with self._runtime_initialization_lock:
            await self._initialize_serialized()

    async def _initialize_serialized(self) -> None:
        """串行执行初始化，禁止同一内核的两个协程共享半初始化资源。"""

        if self._initialized:
            if not self._runtime_writer_lock.held:
                raise RuntimeError("A_Memorix 活动运行时丢失数据目录写者锁")
            await self._initialize_with_writer_lock()
            return

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._runtime_writer_lock.acquire()
        try:
            await self._initialize_with_writer_lock()
        except BaseException:
            try:
                await self._discard_failed_initialization()
            finally:
                self._runtime_writer_lock.release()
            raise

    async def _discard_failed_initialization(self) -> None:
        """初始化失败时在写者锁内丢弃半成品，禁止无锁 close 再次落盘。"""

        try:
            await self._stop_background_tasks()
        except BaseException as exc:
            logger.warning(f"初始化失败后的后台任务清理异常: {exc}")
            for task in self._background_tasks.values():
                if task is not None and not task.done():
                    task.cancel()
            self._background_tasks.clear()

        for manager in (self.import_task_manager, self.retrieval_tuning_manager):
            if manager is None:
                continue
            try:
                await manager.shutdown()
            except BaseException as exc:
                logger.warning(f"初始化失败后的任务管理器清理异常: {exc}")

        if self.metadata_store is not None:
            try:
                self.metadata_store.close()
            except BaseException as exc:
                logger.warning(f"初始化失败后的元数据连接清理异常: {exc}")
        self._clear_runtime_references()
        self._initialized = False

    def _clear_runtime_references(self) -> None:
        """失效全部运行时引用，防止锁释放后的旧内核继续写存储。"""

        self.embedding_manager = None
        self.metadata_store = None
        self.graph_store = None
        self.vector_store = None
        self.paragraph_vector_store = None
        self.graph_vector_store = None
        self.relation_write_service = None
        self.sparse_index = None
        self.retriever = None
        self.threshold_filter = None
        self._runtime_bundle = None
        self.episode_retriever = None
        self.aggregate_query_service = None
        self.person_profile_service = None
        self.episode_segmentation_service = None
        self.episode_service = None
        self.summary_importer = None
        self.import_task_manager = None
        self.retrieval_tuning_manager = None
        self._legacy_vector_view = None

    async def _initialize_with_writer_lock(self) -> None:
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
        try:
            self.embedding_manager = kernel_module.create_embedding_api_adapter(
                batch_size=int(self._cfg("embedding.batch_size", 32)),
                max_concurrent=int(self._cfg("embedding.max_concurrent", 5)),
                default_dimension=self.embedding_dimension,
                enable_cache=bool(self._cfg("embedding.enable_cache", False)),
                model_name=str(self._cfg("embedding.model_name", "auto") or "auto"),
                dimension_request_mode=str(self._cfg("embedding.dimension_request_mode", "explicit") or "explicit"),
                retry_config=self._cfg("embedding.retry", {}) or {},
            )
        except Exception as exc:
            self.embedding_manager = None
            self._set_runtime_capability("embedding", False)
            self._set_embedding_degraded(active=True, reason=str(exc)[:500], checked_at=time.time())
            logger.exception(f"[sdk] Embedding 通道初始化失败，核心运行时继续启动: {exc}")
        else:
            self._set_runtime_capability("embedding", True)

        try:
            stored_dimension = self._stored_vector_dimension()
        except Exception as exc:
            stored_dimension = None
            logger.warning(f"读取历史向量维度失败，将使用配置维度并隔离向量加载: {exc}")
        provisional_dimension = int(self.embedding_dimension)
        if stored_dimension is not None and stored_dimension != provisional_dimension:
            logger.warning(
                "历史向量维度与当前配置不同，将按当前维度创建新世代: "
                f"stored={stored_dimension}, current={provisional_dimension}"
            )

        matrix_format = str(self._cfg("graph.sparse_matrix_format", "csr") or "csr").strip().lower()
        graph_format = (
            kernel_module.SparseMatrixFormat.CSC if matrix_format == "csc" else kernel_module.SparseMatrixFormat.CSR
        )

        self.metadata_store = kernel_module.MetadataStore(data_dir=self.data_dir / "metadata")
        self.metadata_store.connect()
        self._set_runtime_capability("metadata", True)

        try:
            self.graph_store = kernel_module.GraphStore(
                matrix_format=graph_format,
                data_dir=self.data_dir / "graph",
            )
            if self.graph_store.has_data():
                self.graph_store.load()
            projection_service = self._maintenance_service
            type(projection_service)._reconcile_relation_graph_projection_jobs(
                projection_service,
                reset_leases=True,
                batch_size=10_000,
            )
        except Exception as exc:
            self.graph_store = None
            self._set_runtime_capability("graph", False)
            logger.exception(f"[sdk] 图谱通道初始化失败，元数据与其他检索通道继续运行: {exc}")
        else:
            self._set_runtime_capability("graph", True)

        sparse_cfg_raw = self._cfg("retrieval.sparse", {}) or {}
        try:
            sparse_cfg = kernel_module.SparseBM25Config(**sparse_cfg_raw)
        except Exception as exc:
            logger.warning(f"sparse 配置非法，回退默认: {exc}")
            sparse_cfg = kernel_module.SparseBM25Config()
        try:
            self.sparse_index = kernel_module.SparseBM25Index(
                metadata_store=self.metadata_store,
                config=sparse_cfg,
            )
            sparse_enabled = bool(getattr(self.sparse_index.config, "enabled", False))
            if sparse_enabled:
                warmup_summary = self.sparse_index.warmup()
                if not warmup_summary.get("ok"):
                    raise RuntimeError(str(warmup_summary.get("error", "sparse_warmup_failed")))
                logger.info(
                    "[sdk] 稀疏索引预热完成: "
                    f"backend={warmup_summary.get('backend')}, "
                    f"docs={warmup_summary.get('doc_count')}, "
                    f"duration_ms={float(warmup_summary.get('duration_ms', 0.0)):.2f}"
                )
            self._set_runtime_capability("sparse", sparse_enabled)
        except Exception as exc:
            self.sparse_index = None
            self._set_runtime_capability("sparse", False)
            logger.exception(f"[sdk] 稀疏通道初始化失败，其他检索通道继续运行: {exc}")

        self._dual_vector_pools_ready = False
        try:
            self.vector_store = self._make_vector_store(
                self._vectors_root(),
                dimension=provisional_dimension,
            )
            self.paragraph_vector_store = self._make_vector_store(
                self._paragraph_vector_dir(),
                dimension=provisional_dimension,
            )
            self.graph_vector_store = self._make_vector_store(
                self._graph_vector_dir(),
                dimension=provisional_dimension,
            )
            self._cleanup_stale_dual_vector_build_dirs()
            self._resume_vector_recovery_if_needed()

            dual_loaded = False
            if self._dual_vector_pools_config_enabled():
                dual_loaded = self._reload_dual_vector_stores_from_disk()
            if dual_loaded and self._legacy_vector_view is None:
                self._set_vector_health(
                    state="healthy",
                    error_code="",
                    reason="",
                    trusted_coverage=1.0,
                    recovery_stage="idle",
                    operation_id="",
                    copy_progress={},
                )
            elif self._legacy_vector_view is None and self.vector_store.has_data():
                self.vector_store.load(
                    expected_embedding_fingerprint=self._current_embedding_fingerprint()
                )
                self.vector_store.warmup_index(force_train=True)
            self._set_runtime_capability("vector_read", True)
            self._set_runtime_capability("vector_write", True)
            if not dual_loaded and self._legacy_vector_view is None:
                self._set_vector_health(
                    state="healthy",
                    error_code="",
                    reason="",
                    trusted_coverage=1.0,
                    recovery_stage="idle",
                    operation_id="",
                    copy_progress={},
                )
        except VectorStoreIntegrityError as exc:
            if not self._recover_known_vector_failure(exc):
                self._disable_vector_channel(exc)
        except Exception as exc:
            self._disable_vector_channel(exc)

        self._refresh_relation_write_service()

        runtime_config = self._build_runtime_config()
        self._runtime_bundle = kernel_module.build_search_runtime(
            plugin_config=runtime_config,
            logger_obj=kernel_module.logger,
            owner_tag="sdk_kernel",
            log_prefix="[sdk]",
        )
        if self._runtime_bundle.ready:
            self.retriever = self._runtime_bundle.retriever
            self.threshold_filter = self._runtime_bundle.threshold_filter
            self.sparse_index = self._runtime_bundle.sparse_index or self.sparse_index
            self._apply_runtime_sparse_mode()
        else:
            self.retriever = None
            self.threshold_filter = None
            logger.warning(self._runtime_bundle.error or "检索通道不可用，元数据核心仍保持运行")

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
            f"[sdk] 向量存储初始化完成: dim={self.embedding_dimension}, mode=SQ8, vector_pools={pool_mode_label}"
        )

        self._mark_startup_self_check_deferred()

        self._initialized = True
        await self._start_background_tasks()

    async def shutdown(self) -> None:
        """先等待后台工作和任务管理器退出，再持久化并释放底层存储。"""

        async with self._runtime_initialization_lock:
            await self._shutdown_serialized()

    async def _shutdown_serialized(self) -> None:
        """与初始化互斥地关闭运行时，避免释放半初始化对象的写者锁。"""

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
        if self._runtime_initialization_lock.locked():
            raise RuntimeError("A_Memorix 正在初始化或异步关闭，不能同步 close()")
        if self._initialized:
            raise RuntimeError("A_Memorix 运行时仍处于活动状态，请先 await shutdown()")
        self._close_runtime()

    def _close_runtime(self) -> None:
        """持久化并释放资源，调用前必须确认没有任务仍会访问存储。"""
        has_writable_store = any(
            store is not None
            for store in (
                self.metadata_store,
                self.graph_store,
                self.vector_store,
                self.paragraph_vector_store,
                self.graph_vector_store,
            )
        )
        if has_writable_store and not self._runtime_writer_lock.held:
            raise RuntimeError("A_Memorix 存储仍可写，但数据目录写者锁未持有")
        metadata_store = self.metadata_store
        try:
            if has_writable_store:
                self._persist()
        finally:
            try:
                if metadata_store is not None:
                    metadata_store.close()
            finally:
                # persist 或 close 失败时也要先废弃可写引用，再释放跨进程锁。
                self._clear_runtime_references()
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
                self._runtime_capabilities = {
                    "metadata": False,
                    "sparse": False,
                    "graph": False,
                    "vector_read": False,
                    "vector_write": False,
                    "embedding": False,
                }
                self._vector_health = {
                    "state": "not_initialized",
                    "error_code": "",
                    "reason": "",
                    "trusted_coverage": 0.0,
                    "recovery_stage": "idle",
                    "operation_id": "",
                    "copy_progress": {},
                    "updated_at": None,
                }
                self._runtime_writer_lock.release()
