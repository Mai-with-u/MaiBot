from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Coroutine, Dict, Iterable, List, Optional, Sequence

import asyncio
import time  # noqa: F401

from src.chat.message_receive.chat_manager import chat_manager  # noqa: F401
from src.common.logger import get_logger
from src.services.llm_service import LLMServiceClient

from ...paths import default_data_dir, resolve_repo_path
from ..embedding import create_embedding_api_adapter  # noqa: F401
from ..retrieval import SparseBM25Config, SparseBM25Index  # noqa: F401
from ..storage import GraphStore, MetadataStore, SparseMatrixFormat, VectorStore  # noqa: F401
from ..storage.format_migration import run_startup_format_migration  # noqa: F401
from ..utils.aggregate_query_service import AggregateQueryService
from ..utils.episode_retrieval_service import EpisodeRetrievalService
from ..utils.episode_segmentation_service import EpisodeSegmentationService
from ..utils.episode_service import EpisodeService
from ..utils.feedback_policy import (
    feedback_cfg_auto_apply_threshold,
    feedback_cfg_batch_size,
    feedback_cfg_check_interval_seconds,
    feedback_cfg_enabled,
    feedback_cfg_episode_query_block_enabled,
    feedback_cfg_episode_rebuild_enabled,
    feedback_cfg_max_messages,
    feedback_cfg_paragraph_hard_filter_enabled,
    feedback_cfg_paragraph_mark_enabled,
    feedback_cfg_prefilter_enabled,
    feedback_cfg_profile_force_refresh_on_read,
    feedback_cfg_profile_refresh_enabled,
    feedback_cfg_reconcile_batch_size,
    feedback_cfg_reconcile_interval_seconds,
    feedback_cfg_window_hours,
    feedback_cfg_window_label,
    feedback_contains_signal,
    feedback_noise,
    feedback_signal_tokens,
    fuzzy_modify_cfg_allow_global_scope,
    fuzzy_modify_cfg_auto_execute_enabled,
    fuzzy_modify_cfg_candidate_limit,
    fuzzy_modify_cfg_confirm_threshold,
    fuzzy_modify_cfg_enabled,
    fuzzy_modify_cfg_max_targets,
)
from ..utils.profile_policy import (
    person_profile_refresh_debounce_seconds,
    person_profile_refresh_max_retry,
    person_profile_refresh_queue_batch_size,
    person_profile_refresh_queue_interval_seconds,
    person_profile_refresh_retry_backoff_seconds,
    should_auto_enqueue_episode,
)
from ..utils.profile_evidence import profile_evidence_type_from_source, profile_relation_content
from ..utils.person_profile_service import PersonProfileService
from ..utils.relation_write_service import RelationWriteService
from ..utils.retrieval_tuning_manager import RetrievalTuningManager
from ..utils.runtime_payloads import (
    argument_tokens,
    build_source,
    coerce_datetime,
    merge_argument_tokens,
    merge_tokens,
    resolve_knowledge_type,
    safe_json_loads,
    time_meta,
    tokens,
)
from ..utils.runtime_self_check import run_embedding_runtime_self_check  # noqa: F401
from ..utils.search_execution_service import SearchExecutionRequest, SearchExecutionResult, SearchExecutionService  # noqa: F401
from ..utils.summary_importer import SummaryImporter
from ..utils.web_import_manager import ImportTaskManager
from .kernel_compat import KernelCompatibilityMixin
from .models import KernelSearchRequest, _NormalizedSearchTimeWindow
from .runtime_facade import KernelRuntimeFacade
from .runtime_writer_lock import RuntimeWriterLock
from .search_runtime_initializer import SearchRuntimeBundle, build_search_runtime  # noqa: F401

logger = get_logger("A_Memorix.SDKMemoryKernel")

DUAL_VECTOR_AUTO_MIGRATION_INITIAL_DELAY_SECONDS = 5.0
DUAL_VECTOR_AUTO_MIGRATION_LOCK_RETRY_DELAYS_SECONDS = (2.0, 5.0, 10.0)


class SDKMemoryKernel(KernelCompatibilityMixin):
    """A_Memorix 对宿主暴露的统一运行时入口。

    内核持有存储、检索和后台任务的共享状态，具体业务由各运行时服务完成。
    活动内核必须通过 ``initialize()`` 和 ``shutdown()`` 管理完整生命周期。
    """

    def __init__(self, *, plugin_root: Path, config: Optional[Dict[str, Any]] = None) -> None:
        self.plugin_root = Path(plugin_root).resolve()
        self.config = config or {}
        storage_cfg = self._cfg("storage", {}) or {}
        data_dir = str(storage_cfg.get("data_dir", "./data") or "./data")
        self.data_dir = resolve_repo_path(data_dir, fallback=default_data_dir())
        self.embedding_dimension = max(1, int(self._cfg("embedding.dimension", 1024)))
        self.relation_vectors_enabled = bool(self._cfg("retrieval.relation_vectorization.enabled", False))

        self.embedding_manager = None
        self.vector_store: Optional[VectorStore] = None
        self.paragraph_vector_store: Optional[VectorStore] = None
        self.graph_vector_store: Optional[VectorStore] = None
        self.graph_store: Optional[GraphStore] = None
        self.metadata_store: Optional[MetadataStore] = None
        self.relation_write_service: Optional[RelationWriteService] = None
        self.sparse_index: Optional[SparseBM25Index] = None
        self.retriever = None
        self.threshold_filter = None
        self.episode_retriever: Optional[EpisodeRetrievalService] = None
        self.aggregate_query_service: Optional[AggregateQueryService] = None
        self.person_profile_service: Optional[PersonProfileService] = None
        self.episode_segmentation_service: Optional[EpisodeSegmentationService] = None
        self.episode_service: Optional[EpisodeService] = None
        self.summary_importer: Optional[SummaryImporter] = None
        self.import_task_manager: Optional[ImportTaskManager] = None
        self.retrieval_tuning_manager: Optional[RetrievalTuningManager] = None
        self._runtime_bundle: Optional[SearchRuntimeBundle] = None
        self._runtime_facade = KernelRuntimeFacade(self)
        self._initialized = False
        self._runtime_initialization_lock = asyncio.Lock()
        self._last_maintenance_at: Optional[float] = None
        self._request_dedup_tasks: Dict[str, asyncio.Task] = {}
        self._vector_rebuild_lock = asyncio.Lock()
        # 关系图是整图快照，单个 SDK 内的投影发布必须覆盖领取到 CAS 的完整临界区。
        self._relation_graph_projection_lock = RLock()
        # 跨进程只允许一个活动 SDK 写同一数据目录。OS 在进程退出时自动释放锁。
        self._runtime_writer_lock = RuntimeWriterLock(
            self.data_dir / ".a_memorix_runtime_writer.lock"
        )
        # 删除、恢复和清理 Outbox 会写同一组向量及图文件，必须在单进程写者内线性提交。
        self._storage_cleanup_lock = asyncio.Lock()
        self._vector_persist_blocked_until_rebuild = False
        self._vector_rebuild_source_dimension: Optional[int] = None
        self._dual_vector_pools_ready = False
        self._dual_vector_auto_migration_attempted = False
        self._dual_vector_auto_migration_status: Dict[str, Any] = {
            "running": False,
            "attempted": False,
            "success": False,
            "stage": "idle",
            "progress": {
                "total": 0,
                "processed": 0,
                "percent": 0.0,
                "elapsed_seconds": 0.0,
                "estimated_remaining_seconds": None,
            },
            "last_error": "",
            "started_at": None,
            "finished_at": None,
            "updated_at": None,
        }
        self._background_tasks: Dict[str, asyncio.Task] = {}
        self._background_lock = asyncio.Lock()
        self._background_stopping = False
        self._active_person_timestamps: Dict[str, float] = {}
        self._embedding_degraded: Dict[str, Any] = {
            "active": False,
            "reason": "",
            "since": None,
            "last_check": None,
        }
        self._current_effective_filter_cache: Dict[str, Any] = {"checked_at": 0.0, "needed": False}
        self._feedback_classifier: Optional[LLMServiceClient] = None
        self._fuzzy_modify_planner: Optional[LLMServiceClient] = None

        from .services import (
            MemoryBackgroundTaskService,
            MemoryChatFilterService,
            MemoryCorrectionAdminService,
            MemoryDeleteAdminService,
            MemoryDualVectorMigrationService,
            MemoryDualVectorStateService,
            MemoryEmbeddingStateService,
            MemoryEpisodeAdminService,
            MemoryFeedbackCorrectionService,
            MemoryGraphAdminService,
            MemoryImportTuningAdminService,
            MemoryIngestService,
            MemoryMaintenanceService,
            MemorySearchService,
            MemoryProfileAdminService,
            MemoryRequestDedupService,
            MemoryRuntimeConfigService,
            MemoryRuntimeDependencyService,
            MemoryRuntimeLifecycleService,
            MemorySearchHitProcessingService,
            MemorySourceAdminService,
            MemoryStatsService,
            MemorySummaryService,
            MemoryV5AdminService,
            MemoryVectorDeleteService,
            MemoryVectorRuntimeService,
        )

        self._graph_admin_service = MemoryGraphAdminService(self)
        self._background_task_service = MemoryBackgroundTaskService(self)
        self._chat_filter_service = MemoryChatFilterService(self)
        self._delete_admin_service = MemoryDeleteAdminService(self)
        self._dual_vector_migration_service = MemoryDualVectorMigrationService(self)
        self._dual_vector_state_service = MemoryDualVectorStateService(self)
        self._embedding_state_service = MemoryEmbeddingStateService(self)
        self._correction_admin_service = MemoryCorrectionAdminService(self)
        self._runtime_config_service = MemoryRuntimeConfigService(self)
        self._runtime_dependency_service = MemoryRuntimeDependencyService(self)
        self._runtime_lifecycle_service = MemoryRuntimeLifecycleService(self)
        self._search_service = MemorySearchService(self)
        self._search_hit_service = MemorySearchHitProcessingService(self)
        self._source_admin_service = MemorySourceAdminService(self)
        self._feedback_service = MemoryFeedbackCorrectionService(self)
        self._vector_runtime_service = MemoryVectorRuntimeService(self)
        self._ingest_service = MemoryIngestService(self)
        self._maintenance_service = MemoryMaintenanceService(self)
        self._episode_admin_service = MemoryEpisodeAdminService(self)
        self._profile_admin_service = MemoryProfileAdminService(self)
        self._import_tuning_admin_service = MemoryImportTuningAdminService(self)
        self._v5_admin_service = MemoryV5AdminService(self)
        self._vector_delete_service = MemoryVectorDeleteService(self)
        self._request_dedup_service = MemoryRequestDedupService(self)
        self._summary_service = MemorySummaryService(self)
        self._stats_service = MemoryStatsService(self)

    def _cfg(self, key: str, default: Any = None) -> Any:
        current: Any = self.config
        if key in {
            "storage",
            "embedding",
            "retrieval",
            "graph",
            "episode",
            "web",
            "advanced",
            "threshold",
            "summarization",
            "person_profile",
        } and isinstance(current, dict):
            return current.get(key, default)
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def _set_cfg(self, key: str, value: Any) -> None:
        current: Dict[str, Any] = self.config
        parts = [part for part in str(key or "").split(".") if part]
        if not parts:
            return
        for part in parts[:-1]:
            next_value = current.get(part)
            if not isinstance(next_value, dict):
                next_value = {}
                current[part] = next_value
            current = next_value
        current[parts[-1]] = value

    def _build_runtime_config(self, base_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        service = self._runtime_config_service
        return type(service)._build_runtime_config(service, base_config)

    @staticmethod
    def _merge_runtime_config_patch(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        from .services.runtime_config_service import MemoryRuntimeConfigService

        return MemoryRuntimeConfigService._merge_runtime_config_patch(base, patch)

    async def apply_retrieval_tuning_profile(
        self,
        profile: Dict[str, Any],
        *,
        validate: bool = True,
    ) -> Dict[str, Any]:
        service = self._runtime_config_service
        return await type(service).apply_retrieval_tuning_profile(service, profile, validate=validate)

    def is_runtime_ready(self) -> bool:
        return bool(
            self._initialized
            and self.vector_store is not None
            and self.graph_store is not None
            and self.metadata_store is not None
            and self.embedding_manager is not None
            and self.retriever is not None
        )

    def is_chat_enabled(self, stream_id: str, group_id: str | None = None, user_id: str | None = None) -> bool:
        service = self._chat_filter_service
        return type(service).is_chat_enabled(service, stream_id=stream_id, group_id=group_id, user_id=user_id)

    @staticmethod
    def _chat_filter_config_allows(
        filter_config: Dict[str, Any],
        *,
        stream_id: str = "",
        group_id: str = "",
        user_id: str = "",
        default_when_empty: bool = True,
    ) -> bool:
        from .services.chat_filter_service import MemoryChatFilterService

        return MemoryChatFilterService._chat_filter_config_allows(
            filter_config,
            stream_id=stream_id,
            group_id=group_id,
            user_id=user_id,
            default_when_empty=default_when_empty,
        )

    def _is_chat_filtered(
        self,
        *,
        respect_filter: bool,
        stream_id: str = "",
        group_id: str = "",
        user_id: str = "",
    ) -> bool:
        service = self._chat_filter_service
        return type(service)._is_chat_filtered(
            service,
            respect_filter=respect_filter,
            stream_id=stream_id,
            group_id=group_id,
            user_id=user_id,
        )

    def _stored_vector_dimension(self, store: Optional[VectorStore] = None) -> Optional[int]:
        service = self._embedding_state_service
        return type(service)._stored_vector_dimension(service, store)

    @staticmethod
    def _normalize_embedding_fingerprint(value: Any) -> Optional[Dict[str, Any]]:
        from .services.embedding_state_service import MemoryEmbeddingStateService

        return MemoryEmbeddingStateService._normalize_embedding_fingerprint(value)

    def _current_embedding_status_dimension(self) -> int:
        service = self._embedding_state_service
        return type(service)._current_embedding_status_dimension(service)

    def _current_embedding_fingerprint(self, *, dimension: Optional[int] = None) -> Optional[Dict[str, Any]]:
        service = self._embedding_state_service
        return type(service)._current_embedding_fingerprint(service, dimension=dimension)

    def _stored_embedding_fingerprint(self, store: Optional[VectorStore] = None) -> Optional[Dict[str, Any]]:
        service = self._embedding_state_service
        return type(service)._stored_embedding_fingerprint(service, store)

    def _stamp_missing_embedding_fingerprint_if_dimension_matches(self, store: Optional[VectorStore]) -> bool:
        service = self._embedding_state_service
        return type(service)._stamp_missing_embedding_fingerprint_if_dimension_matches(service, store)

    @staticmethod
    def _embedding_fingerprint_status(
        current: Optional[Dict[str, Any]],
        stored: Optional[Dict[str, Any]],
        *,
        has_stored_vectors: bool,
    ) -> str:
        from .services.embedding_state_service import MemoryEmbeddingStateService

        return MemoryEmbeddingStateService._embedding_fingerprint_status(
            current,
            stored,
            has_stored_vectors=has_stored_vectors,
        )

    def _stored_vectors_compatible_with_current_embedding(self, store: Optional[VectorStore] = None) -> bool:
        service = self._embedding_state_service
        return type(service)._stored_vectors_compatible_with_current_embedding(service, store)

    def _vector_mismatch_error(self, *, stored_dimension: int, detected_dimension: int) -> str:
        service = self._embedding_state_service
        return type(service)._vector_mismatch_error(
            service,
            stored_dimension=stored_dimension,
            detected_dimension=detected_dimension,
        )

    def _vector_rebuild_status(self) -> Dict[str, Any]:
        service = self._vector_runtime_service
        return type(service)._vector_rebuild_status(service)

    def _embedding_fallback_enabled(self) -> bool:
        service = self._embedding_state_service
        return type(service)._embedding_fallback_enabled(service)

    def _allow_metadata_only_write(self) -> bool:
        service = self._embedding_state_service
        return type(service)._allow_metadata_only_write(service)

    def _embedding_probe_interval_seconds(self) -> float:
        service = self._embedding_state_service
        return type(service)._embedding_probe_interval_seconds(service)

    def _paragraph_vector_backfill_enabled(self) -> bool:
        service = self._embedding_state_service
        return type(service)._paragraph_vector_backfill_enabled(service)

    def _paragraph_vector_backfill_interval_seconds(self) -> float:
        service = self._embedding_state_service
        return type(service)._paragraph_vector_backfill_interval_seconds(service)

    def _paragraph_vector_backfill_batch_size(self) -> int:
        service = self._embedding_state_service
        return type(service)._paragraph_vector_backfill_batch_size(service)

    def _paragraph_vector_backfill_max_retry(self) -> int:
        service = self._embedding_state_service
        return type(service)._paragraph_vector_backfill_max_retry(service)

    def _vector_pool_mode(self) -> str:
        service = self._dual_vector_state_service
        return type(service)._vector_pool_mode(service)

    def _dual_vector_pools_config_enabled(self) -> bool:
        service = self._dual_vector_state_service
        return type(service)._dual_vector_pools_config_enabled(service)

    def _dual_vector_pools_enabled(self) -> bool:
        service = self._dual_vector_state_service
        return type(service)._dual_vector_pools_enabled(service)

    def _vectors_root(self) -> Path:
        service = self._dual_vector_state_service
        return type(service)._vectors_root(service)

    def _paragraph_vector_dir(self) -> Path:
        service = self._dual_vector_state_service
        return type(service)._paragraph_vector_dir(service)

    def _graph_vector_dir(self) -> Path:
        service = self._dual_vector_state_service
        return type(service)._graph_vector_dir(service)

    def _dual_vector_ready_manifest_path(self) -> Path:
        service = self._dual_vector_state_service
        return type(service)._dual_vector_ready_manifest_path(service)

    def _read_dual_vector_ready_manifest(self) -> Optional[Dict[str, Any]]:
        service = self._dual_vector_state_service
        return type(service)._read_dual_vector_ready_manifest(service)

    def _dual_vector_ready(self, *, expected_dimension: Optional[int] = None) -> bool:
        service = self._dual_vector_state_service
        return type(service)._dual_vector_ready(service, expected_dimension=expected_dimension)

    def _write_dual_vector_ready_manifest(
        self,
        *,
        stats: Dict[str, Dict[str, int]],
        migration_stats: Dict[str, Dict[str, int]],
    ) -> None:
        service = self._dual_vector_state_service
        return type(service)._write_dual_vector_ready_manifest(
            service,
            stats=stats,
            migration_stats=migration_stats,
        )

    def _remove_dual_vector_ready_manifest(self) -> None:
        service = self._dual_vector_state_service
        return type(service)._remove_dual_vector_ready_manifest(service)

    def _refresh_dual_vector_ready_manifest_from_stores(self) -> None:
        service = self._dual_vector_state_service
        return type(service)._refresh_dual_vector_ready_manifest_from_stores(service)

    def _clear_legacy_single_vector_files_after_dual_ready(self) -> None:
        service = self._dual_vector_state_service
        return type(service)._clear_legacy_single_vector_files_after_dual_ready(service)

    def _prepare_dual_vector_build_dirs(self) -> tuple[Path, Path, Path]:
        service = self._dual_vector_state_service
        return type(service)._prepare_dual_vector_build_dirs(service)

    def _activate_dual_vector_build_dirs(self, build_root: Path) -> None:
        service = self._dual_vector_state_service
        return type(service)._activate_dual_vector_build_dirs(service, build_root)

    def _cleanup_stale_dual_vector_build_dirs(self) -> None:
        service = self._dual_vector_state_service
        return type(service)._cleanup_stale_dual_vector_build_dirs(service)

    def _make_vector_store(self, data_dir: Path, *, dimension: Optional[int] = None) -> VectorStore:
        service = self._dual_vector_state_service
        return type(service)._make_vector_store(service, data_dir, dimension=dimension)

    def _save_vector_store(self, store: Optional[VectorStore]) -> None:
        service = self._dual_vector_state_service
        return type(service)._save_vector_store(service, store)

    def _reload_dual_vector_stores_from_disk(self) -> bool:
        service = self._dual_vector_state_service
        return type(service)._reload_dual_vector_stores_from_disk(service)

    def _try_recover_dual_ready_manifest(self) -> bool:
        service = self._dual_vector_state_service
        return type(service)._try_recover_dual_ready_manifest(service)

    def _drop_dual_build_root(self, build_root: Optional[Path]) -> None:
        service = self._dual_vector_state_service
        return type(service)._drop_dual_build_root(service, build_root)

    def _refresh_relation_write_service(self) -> None:
        service = self._runtime_dependency_service
        return type(service)._refresh_relation_write_service(service)

    @staticmethod
    def _graph_vector_id(item_type: str, hash_value: str) -> str:
        from .services.dual_vector_state_service import MemoryDualVectorStateService

        return MemoryDualVectorStateService._graph_vector_id(item_type, hash_value)

    def _paragraph_store(self) -> Optional[VectorStore]:
        service = self._dual_vector_state_service
        return type(service)._paragraph_store(service)

    def _graph_vector_store(self) -> Optional[VectorStore]:
        service = self._dual_vector_state_service
        return type(service)._graph_vector_store(service)

    def _delete_vectors_by_type(
        self,
        *,
        paragraph_hashes: Sequence[str] = (),
        entity_hashes: Sequence[str] = (),
        relation_hashes: Sequence[str] = (),
    ) -> int:
        service = self._vector_delete_service
        return type(service)._delete_vectors_by_type(
            service,
            paragraph_hashes=paragraph_hashes,
            entity_hashes=entity_hashes,
            relation_hashes=relation_hashes,
        )

    def _is_embedding_degraded(self) -> bool:
        service = self._embedding_state_service
        return type(service)._is_embedding_degraded(service)

    def _embedding_degraded_snapshot(self) -> Dict[str, Any]:
        service = self._embedding_state_service
        return type(service)._embedding_degraded_snapshot(service)

    def _set_embedding_degraded(self, *, active: bool, reason: str = "", checked_at: Optional[float] = None) -> None:
        service = self._embedding_state_service
        return type(service)._set_embedding_degraded(
            service,
            active=active,
            reason=reason,
            checked_at=checked_at,
        )

    def _apply_runtime_sparse_mode(self) -> None:
        service = self._embedding_state_service
        return type(service)._apply_runtime_sparse_mode(service)

    async def _refresh_runtime_self_check(self, *, sample_text: str = "A_Memorix runtime self check") -> Dict[str, Any]:
        service = self._embedding_state_service
        return await type(service)._refresh_runtime_self_check(service, sample_text=sample_text)

    def _mark_startup_self_check_deferred(self) -> None:
        service = self._embedding_state_service
        return type(service)._mark_startup_self_check_deferred(service)

    def _is_startup_self_check_deferred(self) -> bool:
        service = self._embedding_state_service
        return type(service)._is_startup_self_check_deferred(service)

    @staticmethod
    def _self_check_effective_dimension(report: Dict[str, Any]) -> int:
        from .services.embedding_state_service import MemoryEmbeddingStateService

        return MemoryEmbeddingStateService._self_check_effective_dimension(report)

    def _apply_self_check_dimension_result(self, report: Dict[str, Any]) -> str:
        service = self._embedding_state_service
        return type(service)._apply_self_check_dimension_result(service, report)

    def _enqueue_paragraph_vector_backfill(self, paragraph_hash: str, *, error: str = "") -> None:
        service = self._embedding_state_service
        return type(service)._enqueue_paragraph_vector_backfill(service, paragraph_hash, error=error)

    async def _write_paragraph_vector_or_enqueue(
        self,
        *,
        paragraph_hash: str,
        content: str,
        context: str = "",
    ) -> Dict[str, Any]:
        service = self._ingest_service
        return await type(service)._write_paragraph_vector_or_enqueue(
            service,
            paragraph_hash=paragraph_hash,
            content=content,
            context=context,
        )

    def _paragraph_vector_backfill_counts(self) -> Dict[str, int]:
        service = self._vector_runtime_service
        return type(service)._paragraph_vector_backfill_counts(service)

    async def _run_paragraph_backfill_once(
        self,
        *,
        limit: Optional[int] = None,
        max_retry: Optional[int] = None,
        trigger: str = "manual",
    ) -> Dict[str, Any]:
        service = self._vector_runtime_service
        return await type(service)._run_paragraph_backfill_once(
            service,
            limit=limit,
            max_retry=max_retry,
            trigger=trigger,
        )

    def _count_vector_rebuild_targets(self) -> Dict[str, int]:
        service = self._vector_runtime_service
        return type(service)._count_vector_rebuild_targets(service)

    def _table_has_column(self, table: str, column: str) -> bool:
        service = self._vector_runtime_service
        return type(service)._table_has_column(service, table, column)

    def _active_row_filter_sql(self, table: str) -> str:
        service = self._vector_runtime_service
        return type(service)._active_row_filter_sql(service, table)

    async def _backfill_missing_dual_vector_pool_entries(self, *, batch_size: int) -> Dict[str, Any]:
        service = self._vector_runtime_service
        return await type(service)._backfill_missing_dual_vector_pool_entries(service, batch_size=batch_size)

    def _refresh_runtime_dependents(self, *, preserve_managers: bool = True) -> None:
        service = self._runtime_dependency_service
        return type(service)._refresh_runtime_dependents(service, preserve_managers=preserve_managers)

    async def _encode_and_add_rebuild_vectors(
        self,
        *,
        items: Sequence[tuple[str, str]],
        batch_size: int,
        vector_store: Optional[VectorStore] = None,
    ) -> tuple[int, int, str, List[str], List[str]]:
        service = self._vector_runtime_service
        return await type(service)._encode_and_add_rebuild_vectors(
            service,
            items=items,
            batch_size=batch_size,
            vector_store=vector_store,
        )

    def _copy_rebuild_vectors_from_store(
        self,
        *,
        source_store: Optional[VectorStore],
        target_store: Optional[VectorStore],
        id_pairs: Sequence[tuple[str, str]],
        batch_size: int = 1024,
    ) -> tuple[int, List[str], List[tuple[str, str]]]:
        service = self._vector_runtime_service
        return type(service)._copy_rebuild_vectors_from_store(
            service,
            source_store=source_store,
            target_store=target_store,
            id_pairs=id_pairs,
            batch_size=batch_size,
        )

    async def _copy_or_encode_dual_rebuild_vectors(
        self,
        *,
        items: Sequence[tuple[str, str]],
        batch_size: int,
        target_store: Optional[VectorStore],
        target_id_prefix: str = "",
        source_store: Optional[VectorStore] = None,
    ) -> tuple[int, int, str, List[str], List[str], Dict[str, int]]:
        service = self._vector_runtime_service
        return await type(service)._copy_or_encode_dual_rebuild_vectors(
            service,
            items=items,
            batch_size=batch_size,
            target_store=target_store,
            target_id_prefix=target_id_prefix,
            source_store=source_store,
        )

    async def _rebuild_all_vectors(
        self,
        *,
        batch_size: Optional[int] = None,
        include_relations: Optional[bool] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        service = self._vector_runtime_service
        return await type(service)._rebuild_all_vectors(
            service,
            batch_size=batch_size,
            include_relations=include_relations,
            dry_run=dry_run,
        )

    async def _rebuild_all_vectors_locked(
        self,
        *,
        batch_size: Optional[int] = None,
        include_relations: Optional[bool] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        service = self._vector_runtime_service
        return await type(service)._rebuild_all_vectors_locked(
            service,
            batch_size=batch_size,
            include_relations=include_relations,
            dry_run=dry_run,
        )

    async def _detect_current_embedding_dimension_for_rebuild(self) -> int:
        service = self._vector_runtime_service
        return await type(service)._detect_current_embedding_dimension_for_rebuild(service)

    async def _recover_embedding_once(self, *, sample_text: str = "A_Memorix runtime self check") -> Dict[str, Any]:
        service = self._embedding_state_service
        return await type(service)._recover_embedding_once(service, sample_text=sample_text)

    async def initialize(self) -> None:
        return await self._runtime_lifecycle_service.initialize()

    async def shutdown(self) -> None:
        return await self._runtime_lifecycle_service.shutdown()

    def close(self) -> None:
        return self._runtime_lifecycle_service.close()

    async def execute_request_with_dedup(
        self,
        request_key: str,
        executor: Callable[[], Coroutine[Any, Any, Dict[str, Any]]],
    ) -> tuple[bool, Dict[str, Any]]:
        service = self._request_dedup_service
        return await type(service).execute_request_with_dedup(service, request_key, executor)

    async def summarize_chat_stream(
        self,
        *,
        chat_id: str,
        context_length: Optional[int] = None,
        include_personality: Optional[bool] = None,
        time_end: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        service = self._summary_service
        return await type(service).summarize_chat_stream(
            service,
            chat_id=chat_id,
            context_length=context_length,
            include_personality=include_personality,
            time_end=time_end,
            metadata=metadata,
        )

    async def ingest_summary(
        self,
        *,
        external_id: str,
        chat_id: str,
        text: str,
        participants: Optional[Sequence[str]] = None,
        time_start: Optional[float] = None,
        time_end: Optional[float] = None,
        tags: Optional[Sequence[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        respect_filter: bool = True,
        user_id: str = "",
        group_id: str = "",
    ) -> Dict[str, Any]:
        service = self._ingest_service
        return await type(service).ingest_summary(
            service,
            external_id=external_id,
            chat_id=chat_id,
            text=text,
            participants=participants,
            time_start=time_start,
            time_end=time_end,
            tags=tags,
            metadata=metadata,
            respect_filter=respect_filter,
            user_id=user_id,
            group_id=group_id,
        )

    async def ingest_text(
        self,
        *,
        external_id: str,
        source_type: str,
        text: str,
        chat_id: str = "",
        person_ids: Optional[Sequence[str]] = None,
        participants: Optional[Sequence[str]] = None,
        timestamp: Optional[float] = None,
        time_start: Optional[float] = None,
        time_end: Optional[float] = None,
        tags: Optional[Sequence[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        entities: Optional[Sequence[str]] = None,
        relations: Optional[Sequence[Dict[str, Any]]] = None,
        respect_filter: bool = True,
        user_id: str = "",
        group_id: str = "",
    ) -> Dict[str, Any]:
        service = self._ingest_service
        return await type(service).ingest_text(
            service,
            external_id=external_id,
            source_type=source_type,
            text=text,
            chat_id=chat_id,
            person_ids=person_ids,
            participants=participants,
            timestamp=timestamp,
            time_start=time_start,
            time_end=time_end,
            tags=tags,
            metadata=metadata,
            entities=entities,
            relations=relations,
            respect_filter=respect_filter,
            user_id=user_id,
            group_id=group_id,
        )

    async def process_episode_source_rebuild_batch(
        self,
        *,
        sources: Optional[Sequence[str]] = None,
        limit: int = 20,
        max_retry: int = 3,
        lease_seconds: float = 1800.0,
        max_wait_seconds: float = 60.0,
    ) -> Dict[str, Any]:
        service = self._ingest_service
        return await type(service).process_episode_source_rebuild_batch(
            service,
            sources=sources,
            limit=limit,
            max_retry=max_retry,
            lease_seconds=lease_seconds,
            max_wait_seconds=max_wait_seconds,
        )

    async def search_memory(self, request: KernelSearchRequest) -> Dict[str, Any]:
        return await self._search_service.search_memory(request)

    @staticmethod
    def _empty_person_profile_response(*, person_id: str = "", person_name: str = "") -> Dict[str, Any]:
        return {
            "summary": "",
            "traits": [],
            "evidence": [],
            "person_id": str(person_id or "").strip(),
            "person_name": str(person_name or "").strip(),
            "profile_source": "",
            "has_manual_override": False,
        }

    async def _query_person_profile_with_feedback_refresh(
        self,
        *,
        person_id: str = "",
        person_keyword: str = "",
        limit: int = 10,
        force_refresh: bool = False,
        source_note: str,
    ) -> Dict[str, Any]:
        service = self._profile_admin_service
        return await type(service)._query_person_profile_with_feedback_refresh(
            service,
            person_id=person_id,
            person_keyword=person_keyword,
            limit=limit,
            force_refresh=force_refresh,
            source_note=source_note,
        )

    def _build_person_profile_response(
        self,
        profile: Dict[str, Any],
        *,
        requested_person_id: str,
        limit: int,
    ) -> Dict[str, Any]:
        service = self._profile_admin_service
        return type(service)._build_person_profile_response(
            service,
            profile,
            requested_person_id=requested_person_id,
            limit=limit,
        )

    async def get_person_profile(self, *, person_id: str, chat_id: str = "", limit: int = 10) -> Dict[str, Any]:
        service = self._profile_admin_service
        return await type(service).get_person_profile(
            service,
            person_id=person_id,
            chat_id=chat_id,
            limit=limit,
        )

    async def refresh_person_profile(
        self, person_id: str, limit: int = 10, *, mark_active: bool = True
    ) -> Dict[str, Any]:
        service = self._profile_admin_service
        return await type(service).refresh_person_profile(
            service,
            person_id,
            limit=limit,
            mark_active=mark_active,
        )

    async def maintain_memory(
        self,
        *,
        action: str,
        target: str = "",
        hours: Optional[float] = None,
        reason: str = "",
        limit: int = 50,
    ) -> Dict[str, Any]:
        service = self._v5_admin_service
        return await type(service).maintain_memory(
            service,
            action=action,
            target=target,
            hours=hours,
            reason=reason,
            limit=limit,
        )

    async def rebuild_episodes_for_sources(self, sources: Iterable[str]) -> Dict[str, Any]:
        service = self._episode_admin_service
        return await type(service).rebuild_episodes_for_sources(service, sources)

    def memory_stats(self) -> Dict[str, Any]:
        service = self._stats_service
        return type(service).memory_stats(service)

    def _vector_store_snapshot(self, store: Optional[VectorStore]) -> Dict[str, Any]:
        service = self._vector_runtime_service
        return type(service)._vector_store_snapshot(store)

    def _vector_pools_status(self) -> Dict[str, Any]:
        service = self._vector_runtime_service
        return type(service)._vector_pools_status(service)

    def _should_start_dual_vector_auto_migration(self) -> bool:
        service = self._dual_vector_migration_service
        return type(service)._should_start_dual_vector_auto_migration(service)

    def _normalize_dual_vector_auto_migration_progress(
        self,
        progress: Optional[Dict[str, Any]] = None,
        *,
        now: Optional[float] = None,
        explicit_processed: bool = False,
        completed: bool = False,
        success: bool = False,
    ) -> Dict[str, Any]:
        service = self._dual_vector_migration_service
        return type(service)._normalize_dual_vector_auto_migration_progress(
            service,
            progress,
            now=now,
            explicit_processed=explicit_processed,
            completed=completed,
            success=success,
        )

    def _update_dual_vector_auto_migration_stage(self, stage: str, **progress: Any) -> None:
        service = self._dual_vector_migration_service
        return type(service)._update_dual_vector_auto_migration_stage(service, stage, **progress)

    async def memory_graph_admin(self, *args: Any, **kwargs: Any) -> Any:
        return await self._graph_admin_service.memory_graph_admin(*args, **kwargs)

    async def memory_source_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        service = self._source_admin_service
        return await type(service).memory_source_admin(service, action=action, **kwargs)

    async def memory_episode_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        service = self._episode_admin_service
        return await type(service).memory_episode_admin(service, action=action, **kwargs)

    async def memory_profile_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        service = self._profile_admin_service
        return await type(service).memory_profile_admin(service, action=action, **kwargs)

    async def memory_feedback_admin(self, *args: Any, **kwargs: Any) -> Any:
        return await self._feedback_service.memory_feedback_admin(*args, **kwargs)

    async def memory_runtime_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        service = self._vector_runtime_service
        return await type(service).memory_runtime_admin(service, action=action, **kwargs)

    async def memory_import_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        service = self._import_tuning_admin_service
        return await type(service).memory_import_admin(service, action=action, **kwargs)

    async def memory_tuning_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        service = self._import_tuning_admin_service
        return await type(service).memory_tuning_admin(service, action=action, **kwargs)

    async def memory_v5_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        service = self._v5_admin_service
        return await type(service).memory_v5_admin(service, action=action, **kwargs)

    async def memory_delete_admin(self, *args: Any, **kwargs: Any) -> Any:
        return await self._delete_admin_service.memory_delete_admin(*args, **kwargs)

    async def memory_correction_admin(self, *args: Any, **kwargs: Any) -> Any:
        return await self._correction_admin_service.memory_correction_admin(*args, **kwargs)

    async def memory_fuzzy_modify_admin(self, *args: Any, **kwargs: Any) -> Any:
        return await self._correction_admin_service.memory_fuzzy_modify_admin(*args, **kwargs)

    def get_import_task_manager(self) -> Optional[ImportTaskManager]:
        return self.import_task_manager

    def get_retrieval_tuning_manager(self) -> Optional[RetrievalTuningManager]:
        return self.retrieval_tuning_manager

    async def _aggregate_search(self, query: str, limit: int, request: KernelSearchRequest) -> Dict[str, Any]:
        return await self._search_service._aggregate_search(query, limit, request)

    async def _aggregate_time(
        self,
        query: str,
        limit: int,
        request: KernelSearchRequest,
        time_window: _NormalizedSearchTimeWindow,
    ) -> Dict[str, Any]:
        return await self._search_service._aggregate_time(query, limit, request, time_window)

    async def _aggregate_episode(
        self,
        query: str,
        limit: int,
        request: KernelSearchRequest,
        time_window: _NormalizedSearchTimeWindow,
    ) -> Dict[str, Any]:
        return await self._search_service._aggregate_episode(query, limit, request, time_window)

    def _get_search_hit_service(self) -> Any:
        service = getattr(self, "_search_hit_service", None)
        if service is None:
            from .services.search_hit_processing_service import MemorySearchHitProcessingService

            service = MemorySearchHitProcessingService(self)
            self._search_hit_service = service
        return service

    def _persist(self, *, force_vectors: bool = False) -> None:
        service = self._runtime_dependency_service
        return type(service)._persist(service, force_vectors=force_vectors)

    async def _start_background_tasks(self) -> None:
        service = self._background_task_service
        return await type(service)._start_background_tasks(service)

    def _ensure_background_task(
        self,
        name: str,
        factory: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        service = self._background_task_service
        return type(service)._ensure_background_task(service, name, factory)

    async def _sleep_background(self, seconds: float) -> None:
        service = self._background_task_service
        return await type(service)._sleep_background(service, seconds)

    async def _dual_vector_auto_migration_loop(self) -> None:
        service = self._background_task_service
        return await type(service)._dual_vector_auto_migration_loop(service)

    async def _stop_background_tasks(self) -> None:
        service = self._background_task_service
        return await type(service)._stop_background_tasks(service)

    async def _auto_save_loop(self) -> None:
        service = self._background_task_service
        return await type(service)._auto_save_loop(service)

    async def _vector_index_training_loop(self) -> None:
        service = self._background_task_service
        return await type(service)._vector_index_training_loop(service)

    async def _train_runtime_vector_indexes_once(self) -> Dict[str, Dict[str, Any]]:
        service = self._background_task_service
        return await type(service)._train_runtime_vector_indexes_once(service)

    async def _episode_materialization_loop(self) -> None:
        service = self._background_task_service
        return await type(service)._episode_materialization_loop(service)

    async def _embedding_probe_loop(self) -> None:
        service = self._background_task_service
        return await type(service)._embedding_probe_loop(service)

    async def _paragraph_vector_backfill_loop(self) -> None:
        service = self._background_task_service
        return await type(service)._paragraph_vector_backfill_loop(service)

    async def _person_profile_refresh_loop(self) -> None:
        service = self._background_task_service
        return await type(service)._person_profile_refresh_loop(service)

    async def _person_profile_refresh_queue_loop(self) -> None:
        service = self._background_task_service
        return await type(service)._person_profile_refresh_queue_loop(service)

    @staticmethod
    def _relation_status_is_inactive(*args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._relation_status_is_inactive(*args, **kwargs)

    def _load_paragraph_stale_marks(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._load_paragraph_stale_marks(service, *args, **kwargs)

    def _paragraph_hidden_by_stale_marks(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._paragraph_hidden_by_stale_marks(service, *args, **kwargs)

    def _filter_episode_hits(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._filter_episode_hits(service, *args, **kwargs)

    def _filter_user_visible_hits(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._filter_user_visible_hits(service, *args, **kwargs)

    def _filter_current_effective_hits(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._filter_current_effective_hits(service, *args, **kwargs)

    def _current_effective_filter_store_check_needed(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._current_effective_filter_store_check_needed(service, *args, **kwargs)

    def _filter_hits_by_memory_change_metadata(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._filter_hits_by_memory_change_metadata(service, *args, **kwargs)

    @staticmethod
    def _coerce_datetime(value: Any) -> Optional[datetime]:
        return coerce_datetime(value)

    @staticmethod
    def _feedback_signal_tokens() -> tuple[str, ...]:
        return feedback_signal_tokens()

    @classmethod
    def _feedback_contains_signal(cls, text: str) -> bool:
        return feedback_contains_signal(text)

    @staticmethod
    def _feedback_noise(text: str) -> bool:
        return feedback_noise(text)

    @staticmethod
    def _safe_json_loads(raw: Any) -> Dict[str, Any]:
        return safe_json_loads(raw)

    @staticmethod
    def _feedback_cfg_enabled() -> bool:
        return feedback_cfg_enabled()

    @staticmethod
    def _feedback_cfg_window_hours() -> float:
        return feedback_cfg_window_hours()

    @staticmethod
    def _feedback_cfg_check_interval_seconds() -> float:
        return feedback_cfg_check_interval_seconds()

    @staticmethod
    def _feedback_cfg_batch_size() -> int:
        return feedback_cfg_batch_size()

    @staticmethod
    def _feedback_cfg_auto_apply_threshold() -> float:
        return feedback_cfg_auto_apply_threshold()

    @staticmethod
    def _feedback_cfg_max_messages() -> int:
        return feedback_cfg_max_messages()

    @staticmethod
    def _feedback_cfg_prefilter_enabled() -> bool:
        return feedback_cfg_prefilter_enabled()

    @staticmethod
    def _feedback_cfg_paragraph_mark_enabled() -> bool:
        return feedback_cfg_paragraph_mark_enabled()

    @staticmethod
    def _feedback_cfg_paragraph_hard_filter_enabled() -> bool:
        return feedback_cfg_paragraph_hard_filter_enabled()

    @staticmethod
    def _feedback_cfg_profile_refresh_enabled() -> bool:
        return feedback_cfg_profile_refresh_enabled()

    @staticmethod
    def _feedback_cfg_profile_force_refresh_on_read() -> bool:
        return feedback_cfg_profile_force_refresh_on_read()

    @staticmethod
    def _feedback_cfg_episode_rebuild_enabled() -> bool:
        return feedback_cfg_episode_rebuild_enabled()

    @staticmethod
    def _feedback_cfg_episode_query_block_enabled() -> bool:
        return feedback_cfg_episode_query_block_enabled()

    @staticmethod
    def _feedback_cfg_reconcile_interval_seconds() -> float:
        return feedback_cfg_reconcile_interval_seconds()

    @staticmethod
    def _feedback_cfg_reconcile_batch_size() -> int:
        return feedback_cfg_reconcile_batch_size()

    def _should_auto_enqueue_episode(self, *, source_type: str) -> bool:
        return should_auto_enqueue_episode(self._cfg, source_type=source_type)

    def _person_profile_refresh_queue_interval_seconds(self) -> float:
        return person_profile_refresh_queue_interval_seconds(self._cfg)

    def _person_profile_refresh_queue_batch_size(self) -> int:
        return person_profile_refresh_queue_batch_size(self._cfg)

    def _person_profile_refresh_debounce_seconds(self) -> float:
        return person_profile_refresh_debounce_seconds(self._cfg)

    def _person_profile_refresh_retry_backoff_seconds(self) -> float:
        return person_profile_refresh_retry_backoff_seconds(self._cfg)

    def _person_profile_refresh_max_retry(self) -> int:
        return person_profile_refresh_max_retry(self._cfg)

    def _enqueue_person_profile_refresh(self, person_id: str, *, reason: str = "") -> bool:
        return self._profile_admin_service._enqueue_person_profile_refresh(person_id, reason=reason)

    def _has_pending_person_profile_refresh(self, person_id: str) -> bool:
        return self._profile_admin_service._has_pending_person_profile_refresh(person_id)

    async def _process_person_profile_refresh_queue_batch(self, *, limit: int) -> Dict[str, Any]:
        return await self._process_feedback_profile_refresh_batch(
            limit=limit,
            debounce_seconds=self._person_profile_refresh_debounce_seconds(),
            retry_backoff_seconds=self._person_profile_refresh_retry_backoff_seconds(),
            max_retry=self._person_profile_refresh_max_retry(),
        )

    @classmethod
    def _feedback_cfg_window_label(cls) -> str:
        return feedback_cfg_window_label()

    async def _memory_maintenance_loop(self) -> None:
        service = self._maintenance_service
        return await type(service)._memory_maintenance_loop(service)

    async def _run_memory_maintenance_cycle(self, *, interval_hours: float) -> None:
        service = self._maintenance_service
        return await type(service)._run_memory_maintenance_cycle(service, interval_hours=interval_hours)

    async def _process_freeze_and_prune(self) -> None:
        service = self._maintenance_service
        return await type(service)._process_freeze_and_prune(service)

    async def _orphan_gc_phase(self) -> None:
        service = self._maintenance_service
        return await type(service)._orphan_gc_phase(service)

    def _mark_person_active(self, person_id: str) -> None:
        return self._profile_admin_service._mark_person_active(person_id)

    @staticmethod
    def _tokens(values: Optional[Iterable[Any]]) -> List[str]:
        return tokens(values)

    @classmethod
    def _merge_tokens(cls, *groups: Optional[Iterable[Any]]) -> List[str]:
        return merge_tokens(*groups)

    @classmethod
    def _argument_tokens(cls, value: Any) -> List[str]:
        return argument_tokens(value)

    @classmethod
    def _merge_argument_tokens(cls, *groups: Any) -> List[str]:
        return merge_argument_tokens(*groups)

    @staticmethod
    def _build_source(source_type: str, chat_id: str, person_ids: Sequence[str]) -> str:
        return build_source(source_type, chat_id, person_ids)

    @staticmethod
    def _chat_source(*args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._chat_source(*args, **kwargs)

    @classmethod
    def _chat_source_for_search_scope(cls, *args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._chat_source_for_search_scope(*args, **kwargs)

    @staticmethod
    def _scoped_search_limit(*args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._scoped_search_limit(*args, **kwargs)

    @classmethod
    def _resolve_allowed_chat_ids(cls, *args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._resolve_allowed_chat_ids(*args, **kwargs)

    @staticmethod
    def _rank_score_from_item(*args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._rank_score_from_item(*args, **kwargs)

    @classmethod
    def _dedupe_ranked_items(cls, *args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._dedupe_ranked_items(*args, **kwargs)

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
        return await self._search_service._search_execution_once(
            caller=caller,
            query_type=query_type,
            query=query,
            top_k=top_k,
            request=request,
            plugin_config=plugin_config,
            source=source,
            time_from=time_from,
            time_to=time_to,
            enforce_chat_filter=enforce_chat_filter,
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
        return await self._search_service._search_execution_for_chat_scope(
            caller=caller,
            query_type=query_type,
            query=query,
            top_k=top_k,
            request=request,
            plugin_config=plugin_config,
            time_from=time_from,
            time_to=time_to,
            enforce_chat_filter=enforce_chat_filter,
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
        return await self._search_service._episode_query_for_chat_scope(
            query=query,
            top_k=top_k,
            time_from=time_from,
            time_to=time_to,
            person=person,
            chat_id=chat_id,
            shared_chat_ids=shared_chat_ids,
        )

    @classmethod
    def _paragraph_matches_chat_scope(cls, *args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._paragraph_matches_chat_scope(*args, **kwargs)

    @classmethod
    def _hit_metadata_matches_chat_scope(cls, *args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._hit_metadata_matches_chat_scope(*args, **kwargs)

    @staticmethod
    def _extend_chat_scope_ids(*args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._extend_chat_scope_ids(*args, **kwargs)

    @classmethod
    def _metadata_chat_scope_ids(cls, *args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._metadata_chat_scope_ids(*args, **kwargs)

    def _filter_hits_by_chat_scope(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._filter_hits_by_chat_scope(service, *args, **kwargs)

    def _filter_hits_by_retrieval_type_scope(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._filter_hits_by_retrieval_type_scope(service, *args, **kwargs)

    def _has_enabled_retrieval_type_filter(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._has_enabled_retrieval_type_filter(service, *args, **kwargs)

    def _retrieval_type_filter_root(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._retrieval_type_filter_root(service, *args, **kwargs)

    def _retrieval_type_filter_config(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._retrieval_type_filter_config(service, *args, **kwargs)

    def _retrieval_filter_contexts_for_hit(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._retrieval_filter_contexts_for_hit(service, *args, **kwargs)

    def _retrieval_filter_context_from_hit(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._retrieval_filter_context_from_hit(service, *args, **kwargs)

    def _retrieval_filter_context_from_paragraph(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._retrieval_filter_context_from_paragraph(service, *args, **kwargs)

    @staticmethod
    def _retrieval_filter_kind(*args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._retrieval_filter_kind(*args, **kwargs)

    @staticmethod
    def _source_stream_id(*args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._source_stream_id(*args, **kwargs)

    @staticmethod
    def _retrieval_filter_context(*args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._retrieval_filter_context(*args, **kwargs)

    def _current_retrieval_filter_context(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._current_retrieval_filter_context(service, *args, **kwargs)

    @staticmethod
    def _retrieval_filter_context_is_current_source(*args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._retrieval_filter_context_is_current_source(*args, **kwargs)

    def _retrieval_filter_context_allowed(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._retrieval_filter_context_allowed(service, *args, **kwargs)

    @staticmethod
    def _resolve_knowledge_type(source_type: str) -> str:
        return resolve_knowledge_type(source_type)

    @staticmethod
    def _time_meta(
        timestamp: Optional[float], time_start: Optional[float], time_end: Optional[float]
    ) -> Dict[str, Any]:
        return time_meta(timestamp, time_start, time_end)

    @classmethod
    def _normalize_search_time_bound(cls, *args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._normalize_search_time_bound(*args, **kwargs)

    @classmethod
    def _normalize_search_time_window(cls, *args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._normalize_search_time_window(*args, **kwargs)

    @staticmethod
    def _retrieval_result_hit(*args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._retrieval_result_hit(*args, **kwargs)

    @staticmethod
    def _episode_hit(*args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._episode_hit(*args, **kwargs)

    @staticmethod
    def _summary(*args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._summary(*args, **kwargs)

    @staticmethod
    def _filter_hits(*args: Any, **kwargs: Any) -> Any:
        from .services.search_hit_processing_service import MemorySearchHitProcessingService

        return MemorySearchHitProcessingService._filter_hits(*args, **kwargs)

    def _filter_active_relation_hits(self, *args: Any, **kwargs: Any) -> Any:
        service = self._get_search_hit_service()
        return type(service)._filter_active_relation_hits(service, *args, **kwargs)

    def _resolve_relation_hashes(self, target: str) -> List[str]:
        service = self._v5_admin_service
        return type(service)._resolve_relation_hashes(service, target)

    def _resolve_deleted_relation_hashes(self, target: str) -> List[str]:
        service = self._v5_admin_service
        return type(service)._resolve_deleted_relation_hashes(service, target)

    def _memory_v5_status(self, *, target: str = "", limit: int = 50) -> Dict[str, Any]:
        service = self._v5_admin_service
        return type(service)._memory_v5_status(service, target=target, limit=limit)

    def _fuzzy_modify_cfg_enabled(self, *args: Any, **kwargs: Any) -> Any:
        return fuzzy_modify_cfg_enabled()

    def _fuzzy_modify_cfg_auto_execute_enabled(self, *args: Any, **kwargs: Any) -> Any:
        return fuzzy_modify_cfg_auto_execute_enabled()

    def _fuzzy_modify_cfg_confirm_threshold(self, *args: Any, **kwargs: Any) -> Any:
        return fuzzy_modify_cfg_confirm_threshold()

    def _fuzzy_modify_cfg_candidate_limit(self, *args: Any, **kwargs: Any) -> Any:
        return fuzzy_modify_cfg_candidate_limit()

    def _fuzzy_modify_cfg_max_targets(self, *args: Any, **kwargs: Any) -> Any:
        return fuzzy_modify_cfg_max_targets()

    def _fuzzy_modify_cfg_allow_global_scope(self, *args: Any, **kwargs: Any) -> Any:
        return fuzzy_modify_cfg_allow_global_scope()

    def _apply_v5_relation_action(self, *, action: str, hashes: List[str], strength: float = 1.0) -> Dict[str, Any]:
        service = self._v5_admin_service
        return type(service)._apply_v5_relation_action(service, action=action, hashes=hashes, strength=strength)

    async def _ensure_vector_for_text(
        self,
        *,
        item_hash: str,
        text: str,
        vector_store: Optional[VectorStore] = None,
        before_vector_write: Optional[Callable[[], None]] = None,
    ) -> bool:
        service = self._ingest_service
        return await type(service)._ensure_vector_for_text(
            service,
            item_hash=item_hash,
            text=text,
            vector_store=vector_store,
            before_vector_write=before_vector_write,
        )

    async def _ensure_relation_vector(
        self,
        relation: Dict[str, Any],
        *,
        before_vector_write: Optional[Callable[[], None]] = None,
    ) -> bool:
        service = self._ingest_service
        return await type(service)._ensure_relation_vector(
            service,
            relation,
            before_vector_write=before_vector_write,
        )

    async def _ensure_paragraph_vector(
        self,
        paragraph: Dict[str, Any],
        *,
        before_vector_write: Optional[Callable[[], None]] = None,
    ) -> bool:
        service = self._ingest_service
        return await type(service)._ensure_paragraph_vector(
            service,
            paragraph,
            before_vector_write=before_vector_write,
        )

    async def _ensure_entity_vector(
        self,
        entity: Dict[str, Any],
        *,
        before_vector_write: Optional[Callable[[], None]] = None,
    ) -> bool:
        service = self._ingest_service
        return await type(service)._ensure_entity_vector(
            service,
            entity,
            before_vector_write=before_vector_write,
        )

    async def _restore_relation_hashes(
        self,
        hashes: List[str],
        *,
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        service = self._v5_admin_service
        return await type(service)._restore_relation_hashes(
            service,
            hashes,
            requested_by=requested_by,
            reason=reason,
        )

    @staticmethod
    def _profile_evidence_type_from_source(source: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        return profile_evidence_type_from_source(source, metadata)

    @staticmethod
    def _profile_relation_content(relation: Dict[str, Any]) -> str:
        return profile_relation_content(relation)

    def _build_profile_relation_evidence_item(self, relation: Dict[str, Any], *, index: int) -> Dict[str, Any]:
        service = self._profile_admin_service
        return type(service)._build_profile_relation_evidence_item(service, relation, index=index)

    def _build_profile_paragraph_evidence_item(
        self,
        item: Dict[str, Any],
        *,
        index: int,
        fallback_hash: str = "",
    ) -> Dict[str, Any]:
        service = self._profile_admin_service
        return type(service)._build_profile_paragraph_evidence_item(
            service,
            item,
            index=index,
            fallback_hash=fallback_hash,
        )

    def _build_profile_evidence_items(self, profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        service = self._profile_admin_service
        return type(service)._build_profile_evidence_items(service, profile)

    def _profile_evidence_response(
        self, profile: Dict[str, Any], *, requested_person_id: str, limit: int
    ) -> Dict[str, Any]:
        service = self._profile_admin_service
        return type(service)._profile_evidence_response(
            service,
            profile,
            requested_person_id=requested_person_id,
            limit=limit,
        )

    async def _profile_evidence_admin(
        self,
        *,
        person_id: str = "",
        person_keyword: str = "",
        limit: int = 12,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        service = self._profile_admin_service
        return await type(service)._profile_evidence_admin(
            service,
            person_id=person_id,
            person_keyword=person_keyword,
            limit=limit,
            force_refresh=force_refresh,
        )

    async def _profile_correct_evidence_admin(
        self,
        *,
        person_id: str = "",
        person_keyword: str = "",
        evidence_type: str,
        hash_value: str,
        requested_by: str = "webui",
        reason: str = "profile_evidence_correction",
        refresh: bool = True,
        limit: int = 12,
    ) -> Dict[str, Any]:
        service = self._profile_admin_service
        return await type(service)._profile_correct_evidence_admin(
            service,
            person_id=person_id,
            person_keyword=person_keyword,
            evidence_type=evidence_type,
            hash_value=hash_value,
            requested_by=requested_by,
            reason=reason,
            refresh=refresh,
            limit=limit,
        )
