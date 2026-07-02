from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Iterable, List, Optional, Sequence

from json_repair import repair_json
import asyncio
import copy
import json
import numpy as np
import pickle
import shutil
import time

from src.chat.message_receive.chat_manager import chat_manager
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.services import message_service as message_api
from src.services.llm_service import LLMServiceClient

from ...paths import default_data_dir, resolve_repo_path
from ..embedding import create_embedding_api_adapter
from ..retrieval import RetrievalResult, SparseBM25Config, SparseBM25Index
from ..storage import GraphStore, MetadataStore, QuantizationType, SparseMatrixFormat, VectorStore
from ..utils.aggregate_query_service import AggregateQueryService
from ..utils.episode_retrieval_service import EpisodeRetrievalService
from ..utils.episode_segmentation_service import EpisodeSegmentationService
from ..utils.episode_service import EpisodeService
from ..utils.hash import compute_hash, normalize_text
from ..utils.metadata import coerce_metadata_dict
from ..utils.person_profile_service import PersonProfileService
from ..utils.relation_write_service import RelationWriteService
from ..utils.retrieval_tuning_manager import RetrievalTuningManager
from ..utils.runtime_self_check import run_embedding_runtime_self_check
from ..utils.search_execution_service import SearchExecutionRequest, SearchExecutionResult, SearchExecutionService
from ..utils.summary_importer import SummaryImporter
from ..utils.time_parser import format_timestamp, parse_query_datetime_to_timestamp
from ..utils.web_import_manager import ImportTaskManager
from .search_runtime_initializer import SearchRuntimeBundle, build_search_runtime

logger = get_logger("A_Memorix.SDKMemoryKernel")

DUAL_VECTOR_AUTO_MIGRATION_INITIAL_DELAY_SECONDS = 5.0
DUAL_VECTOR_AUTO_MIGRATION_LOCK_RETRY_DELAYS_SECONDS = (2.0, 5.0, 10.0)


@dataclass
class KernelSearchRequest:
    query: str = ""
    limit: int = 5
    mode: str = "search"
    chat_id: str = ""
    shared_chat_ids: Sequence[str] = ()
    person_id: str = ""
    time_start: Optional[str | float] = None
    time_end: Optional[str | float] = None
    respect_filter: bool = True
    user_id: str = ""
    group_id: str = ""


@dataclass
class _NormalizedSearchTimeWindow:
    numeric_start: Optional[float] = None
    numeric_end: Optional[float] = None
    query_start: Optional[str] = None
    query_end: Optional[str] = None


class _KernelRuntimeFacade:
    def __init__(self, kernel: "SDKMemoryKernel") -> None:
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
        if self._kernel.metadata_store is None:
            return
        hashes = [str(item or "").strip() for item in relation_hashes if str(item or "").strip()]
        if not hashes:
            return
        self._kernel.metadata_store.reinforce_relations(hashes)
        self._kernel._last_maintenance_at = time.time()

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


class SDKMemoryKernel:
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
        self._runtime_facade = _KernelRuntimeFacade(self)
        self._initialized = False
        self._last_maintenance_at: Optional[float] = None
        self._request_dedup_tasks: Dict[str, asyncio.Task] = {}
        self._vector_rebuild_lock = asyncio.Lock()
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

    def _cfg(self, key: str, default: Any = None) -> Any:
        current: Any = self.config
        if (
            key
            in {
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
            }
            and isinstance(current, dict)
        ):
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
        runtime_config = dict(base_config if isinstance(base_config, dict) else self.config)
        runtime_cfg = runtime_config.get("runtime")
        runtime_config["runtime"] = dict(runtime_cfg) if isinstance(runtime_cfg, dict) else {}
        runtime_config["runtime"]["vector_pools_ready"] = self._dual_vector_pools_enabled()
        runtime_config.update(
            {
                "vector_store": self.vector_store,
                "paragraph_vector_store": self.paragraph_vector_store or self.vector_store,
                "graph_vector_store": self.graph_vector_store or self.vector_store,
                "graph_store": self.graph_store,
                "metadata_store": self.metadata_store,
                "embedding_manager": self.embedding_manager,
                "sparse_index": self.sparse_index,
                "relation_write_service": self.relation_write_service,
                "plugin_instance": self._runtime_facade,
            }
        )
        return runtime_config

    @staticmethod
    def _merge_runtime_config_patch(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        merged = copy.deepcopy(base)
        for key, value in (patch or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = SDKMemoryKernel._merge_runtime_config_patch(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged

    async def apply_retrieval_tuning_profile(
        self,
        profile: Dict[str, Any],
        *,
        validate: bool = True,
    ) -> Dict[str, Any]:
        if not isinstance(profile, dict):
            return {
                "success": False,
                "runtime_rebuilt": False,
                "validation_passed": False,
                "error": "profile 必须是字典",
            }

        next_config = self._merge_runtime_config_patch(self.config, profile)
        runtime_bundle = build_search_runtime(
            plugin_config=self._build_runtime_config(next_config),
            logger_obj=logger,
            owner_tag="sdk_kernel_tuning_apply",
            log_prefix="[sdk]",
        )
        if validate and not runtime_bundle.ready:
            return {
                "success": False,
                "runtime_rebuilt": False,
                "validation_passed": False,
                "error": runtime_bundle.error or "检索运行时热重建失败",
            }
        if runtime_bundle.ready:
            self.config.clear()
            self.config.update(next_config)
            self._runtime_bundle = runtime_bundle
            self.retriever = runtime_bundle.retriever
            self.threshold_filter = runtime_bundle.threshold_filter
            self.sparse_index = runtime_bundle.sparse_index or self.sparse_index
            self._refresh_runtime_dependents(preserve_managers=True)
            self._apply_runtime_sparse_mode()
            return {
                "success": True,
                "runtime_rebuilt": True,
                "validation_passed": True,
                "error": "",
            }
        return {
            "success": False,
            "runtime_rebuilt": False,
            "validation_passed": False,
            "error": runtime_bundle.error or "检索运行时热重建失败",
        }

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
        filter_config = self._cfg("filter", {}) or {}
        if not isinstance(filter_config, dict) or not filter_config:
            return True

        return self._chat_filter_config_allows(
            filter_config,
            stream_id=stream_id,
            group_id=group_id,
            user_id=user_id,
            default_when_empty=True,
        )

    @staticmethod
    def _chat_filter_config_allows(
        filter_config: Dict[str, Any],
        *,
        stream_id: str = "",
        group_id: str = "",
        user_id: str = "",
        default_when_empty: bool = True,
    ) -> bool:
        if not bool(filter_config.get("enabled", True)):
            return True

        mode = str(filter_config.get("mode", "blacklist") or "blacklist").strip().lower()
        patterns = filter_config.get("chats") or []
        if not isinstance(patterns, list):
            patterns = []

        if not patterns:
            return bool(default_when_empty) if mode == "blacklist" else False

        stream_token = str(stream_id or "").strip()
        group_token = str(group_id or "").strip()
        user_token = str(user_id or "").strip()
        candidates = {token for token in (stream_token, group_token, user_token) if token}

        matched = False
        for raw_pattern in patterns:
            pattern = str(raw_pattern or "").strip()
            if not pattern:
                continue
            if ":" in pattern:
                prefix, value = pattern.split(":", 1)
                prefix = prefix.strip().lower()
                value = value.strip()
                if prefix == "group" and value and value == group_token:
                    matched = True
                elif prefix in {"user", "private"} and value and value == user_token:
                    matched = True
                elif prefix == "stream" and value and value == stream_token:
                    matched = True
            elif pattern in candidates:
                matched = True

            if matched:
                break

        if mode == "blacklist":
            return not matched
        return matched

    def _is_chat_filtered(
        self,
        *,
        respect_filter: bool,
        stream_id: str = "",
        group_id: str = "",
        user_id: str = "",
    ) -> bool:
        if not bool(respect_filter):
            return False

        stream_token = str(stream_id or "").strip()
        group_token = str(group_id or "").strip()
        user_token = str(user_id or "").strip()
        if not (stream_token or group_token or user_token):
            return False
        return not self.is_chat_enabled(stream_token, group_token, user_token)

    def _stored_vector_dimension(self, store: Optional[VectorStore] = None) -> Optional[int]:
        ready_manifest = (
            self._read_dual_vector_ready_manifest()
            if store is None and self._dual_vector_pools_config_enabled()
            else None
        )
        if ready_manifest is not None:
            try:
                manifest_dimension = int(ready_manifest.get("dimension") or 0)
            except Exception:
                manifest_dimension = 0
            if manifest_dimension > 0:
                return manifest_dimension
        vector_dir = Path(store.data_dir) if store is not None and store.data_dir is not None else self._vectors_root()
        meta_path = vector_dir / "vectors_metadata.pkl"
        if not meta_path.exists():
            return None
        try:
            with open(meta_path, "rb") as handle:
                meta = pickle.load(handle)
        except Exception as exc:
            logger.warning(f"读取向量元数据失败，将回退到 runtime self-check: {exc}")
            return None
        try:
            value = int(meta.get("dimension") or 0)
        except Exception:
            return None
        return value if value > 0 else None

    @staticmethod
    def _normalize_embedding_fingerprint(value: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(value, dict):
            return None
        hash_value = str(value.get("hash", "") or "").strip()
        if not hash_value:
            return None
        payload = dict(value)
        payload["hash"] = hash_value
        return payload

    def _current_embedding_fingerprint(self) -> Optional[Dict[str, Any]]:
        manager = self.embedding_manager
        getter = getattr(manager, "get_embedding_fingerprint", None)
        if not callable(getter):
            return None
        try:
            return self._normalize_embedding_fingerprint(getter(dimension=int(self.embedding_dimension)))
        except Exception as exc:
            logger.warning(f"生成 embedding 指纹失败: {exc}")
            return None

    def _stored_embedding_fingerprint(self, store: Optional[VectorStore] = None) -> Optional[Dict[str, Any]]:
        ready_manifest = (
            self._read_dual_vector_ready_manifest()
            if store is None and self._dual_vector_pools_config_enabled()
            else None
        )
        if ready_manifest is not None:
            manifest_fingerprint = self._normalize_embedding_fingerprint(
                ready_manifest.get("embedding_fingerprint")
            )
            if manifest_fingerprint is not None:
                return manifest_fingerprint

        vector_dir = Path(store.data_dir) if store is not None and store.data_dir is not None else self._vectors_root()
        meta_path = vector_dir / "vectors_metadata.pkl"
        if not meta_path.exists():
            return None
        try:
            with open(meta_path, "rb") as handle:
                meta = pickle.load(handle)
        except Exception as exc:
            logger.warning(f"读取向量指纹元数据失败: {exc}")
            return None
        if not isinstance(meta, dict):
            return None
        return self._normalize_embedding_fingerprint(meta.get("embedding_fingerprint"))

    def _stamp_missing_embedding_fingerprint_if_dimension_matches(self, store: Optional[VectorStore]) -> bool:
        if store is None:
            return False
        stored_dimension = self._stored_vector_dimension(store)
        if stored_dimension is None or int(stored_dimension) != int(self.embedding_dimension):
            return False
        current_fingerprint = self._current_embedding_fingerprint()
        if current_fingerprint is None:
            return False
        stored_fingerprint = self._stored_embedding_fingerprint(store)
        if stored_fingerprint is not None:
            return False
        store.save(embedding_fingerprint=current_fingerprint)
        logger.warning("旧向量库缺少 embedding 指纹且维度匹配，已写入当前模型指纹以复用旧向量")
        stamped_fingerprint = self._stored_embedding_fingerprint(store)
        return (
            stamped_fingerprint is not None
            and str(stamped_fingerprint.get("hash", "") or "") == str(current_fingerprint.get("hash", "") or "")
        )

    @staticmethod
    def _embedding_fingerprint_status(
        current: Optional[Dict[str, Any]],
        stored: Optional[Dict[str, Any]],
        *,
        has_stored_vectors: bool,
    ) -> str:
        if not has_stored_vectors:
            return "none"
        if current is None:
            return "unknown"
        if stored is None:
            return "missing"
        return "matched" if str(current.get("hash", "")) == str(stored.get("hash", "")) else "mismatched"

    def _stored_vectors_compatible_with_current_embedding(self, store: Optional[VectorStore] = None) -> bool:
        current = self._current_embedding_fingerprint()
        stored = self._stored_embedding_fingerprint(store)
        if current is None:
            return False
        if stored is None:
            stamped = self._stamp_missing_embedding_fingerprint_if_dimension_matches(store or self.vector_store)
            if not stamped:
                return False
            stored = self._stored_embedding_fingerprint(store)
            if stored is None:
                return False
        return str(current.get("hash", "") or "") == str(stored.get("hash", "") or "")

    def _vector_mismatch_error(self, *, stored_dimension: int, detected_dimension: int) -> str:
        return (
            "检测到现有向量库与当前 embedding 输出维度不一致："
            f"stored={stored_dimension}, encoded={detected_dimension}。"
            " 当前版本不会兼容 hash 时代或其他维度的旧向量，请改回原 embedding 配置，"
            "或执行重嵌入/重建向量。"
        )

    def _vector_rebuild_status(self) -> Dict[str, Any]:
        if self.vector_store is not None and not self._vector_rebuild_lock.locked():
            self._stamp_missing_embedding_fingerprint_if_dimension_matches(self.vector_store)
        stored_dimension = self._stored_vector_dimension()
        if self._vector_persist_blocked_until_rebuild and self._vector_rebuild_source_dimension is not None:
            stored_dimension = int(self._vector_rebuild_source_dimension)
        current_dimension = int(self.embedding_dimension)
        dimension_rebuild_required = stored_dimension is not None and stored_dimension != current_dimension
        current_fingerprint = self._current_embedding_fingerprint()
        stored_fingerprint = self._stored_embedding_fingerprint()
        fingerprint_status = self._embedding_fingerprint_status(
            current_fingerprint,
            stored_fingerprint,
            has_stored_vectors=stored_dimension is not None,
        )
        fingerprint_rebuild_required = fingerprint_status in {"missing", "mismatched"}
        rebuild_required = dimension_rebuild_required or fingerprint_rebuild_required
        if dimension_rebuild_required:
            message = self._vector_mismatch_error(
                stored_dimension=int(stored_dimension or 0),
                detected_dimension=current_dimension,
            )
        elif fingerprint_status == "mismatched":
            message = "检测到 embedding 模型指纹与现有向量库不一致，请重建向量。"
        elif fingerprint_status == "missing":
            message = "现有向量库缺少 embedding 模型指纹，无法确认模型一致性，建议重建向量。"
        elif fingerprint_status == "unknown":
            message = "当前 embedding 模型指纹不可用，无法确认向量库模型一致性。"
        else:
            message = ""
        return {
            "stored_vector_dimension": int(stored_dimension or 0),
            "embedding_dimension": current_dimension,
            "vector_rebuild_required": bool(rebuild_required),
            "message": message,
            "embedding_fingerprint": current_fingerprint or {},
            "stored_embedding_fingerprint": stored_fingerprint or {},
            "embedding_fingerprint_status": fingerprint_status,
        }

    def _embedding_fallback_enabled(self) -> bool:
        return bool(self._cfg("embedding.fallback.enabled", True))

    def _allow_metadata_only_write(self) -> bool:
        return bool(self._cfg("embedding.fallback.allow_metadata_only_write", True))

    def _embedding_probe_interval_seconds(self) -> float:
        return max(10.0, float(self._cfg("embedding.fallback.probe_interval_seconds", 180) or 180))

    def _paragraph_vector_backfill_enabled(self) -> bool:
        return bool(self._cfg("embedding.paragraph_vector_backfill.enabled", True))

    def _paragraph_vector_backfill_interval_seconds(self) -> float:
        return max(10.0, float(self._cfg("embedding.paragraph_vector_backfill.interval_seconds", 60) or 60))

    def _paragraph_vector_backfill_batch_size(self) -> int:
        return max(1, int(self._cfg("embedding.paragraph_vector_backfill.batch_size", 64) or 64))

    def _paragraph_vector_backfill_max_retry(self) -> int:
        return max(1, int(self._cfg("embedding.paragraph_vector_backfill.max_retry", 5) or 5))

    def _vector_pool_mode(self) -> str:
        mode = str(self._cfg("retrieval.vector_pools.mode", "dual") or "dual").strip().lower()
        return mode if mode in {"single", "dual"} else "single"

    def _dual_vector_pools_config_enabled(self) -> bool:
        return self._vector_pool_mode() == "dual"

    def _dual_vector_pools_enabled(self) -> bool:
        return self._dual_vector_pools_config_enabled() and self._dual_vector_pools_ready

    def _vectors_root(self) -> Path:
        return self.data_dir / "vectors"

    def _paragraph_vector_dir(self) -> Path:
        return self._vectors_root() / "paragraph"

    def _graph_vector_dir(self) -> Path:
        return self._vectors_root() / "graph"

    def _dual_vector_ready_manifest_path(self) -> Path:
        return self._vectors_root() / "dual_ready.json"

    def _read_dual_vector_ready_manifest(self) -> Optional[Dict[str, Any]]:
        path = self._dual_vector_ready_manifest_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"读取双池 ready manifest 失败: {exc}")
            return None
        return payload if isinstance(payload, dict) else None

    def _dual_vector_ready(self, *, expected_dimension: Optional[int] = None) -> bool:
        manifest = self._read_dual_vector_ready_manifest()
        if not manifest or manifest.get("status") != "ready":
            return False
        dimension = int(expected_dimension or self.embedding_dimension or 0)
        manifest_dimension = int(manifest.get("dimension", 0) or 0)
        if dimension > 0 and manifest_dimension not in {0, dimension}:
            logger.warning(
                "双池 ready manifest 维度不匹配: "
                f"manifest={manifest_dimension}, expected={dimension}"
            )
            return False
        paragraph_count = int(manifest.get("paragraph_vectors", 0) or 0)
        graph_count = int(manifest.get("graph_vectors", 0) or 0)
        if paragraph_count < 0 or graph_count < 0:
            return False
        current_fingerprint = self._current_embedding_fingerprint()
        manifest_fingerprint = self._normalize_embedding_fingerprint(manifest.get("embedding_fingerprint"))
        if current_fingerprint is None or manifest_fingerprint is None:
            logger.warning("双池 ready manifest 缺少可校验 embedding 指纹，保持单池降级")
            return False
        if str(current_fingerprint.get("hash", "") or "") != str(manifest_fingerprint.get("hash", "") or ""):
            logger.warning(
                "双池 ready manifest embedding 指纹不匹配，保持单池降级: "
                f"manifest={manifest_fingerprint.get('hash', '')}, "
                f"current={current_fingerprint.get('hash', '')}"
            )
            return False
        return self._paragraph_vector_dir().exists() and self._graph_vector_dir().exists()

    def _write_dual_vector_ready_manifest(
        self,
        *,
        stats: Dict[str, Dict[str, int]],
        migration_stats: Dict[str, Dict[str, int]],
    ) -> None:
        embedding_fingerprint = self._current_embedding_fingerprint()
        payload = {
            "status": "ready",
            "version": 1,
            "mode": "dual",
            "dimension": int(self.embedding_dimension),
            "created_at": time.time(),
            "paragraph_vectors": int(stats.get("paragraphs", {}).get("done", 0) or 0),
            "graph_vectors": int(stats.get("entities", {}).get("done", 0) or 0)
            + int(stats.get("relations", {}).get("done", 0) or 0),
            "stats": stats,
            "migration": migration_stats,
        }
        if embedding_fingerprint is not None:
            payload["embedding_fingerprint"] = embedding_fingerprint
        path = self._dual_vector_ready_manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def _remove_dual_vector_ready_manifest(self) -> None:
        try:
            self._dual_vector_ready_manifest_path().unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(f"删除双池 ready manifest 失败: {exc}")

    def _refresh_dual_vector_ready_manifest_from_stores(self) -> None:
        paragraph_count = int(getattr(self.paragraph_vector_store, "num_vectors", 0) or 0)
        graph_count = int(getattr(self.graph_vector_store, "num_vectors", 0) or 0)
        entity_count = graph_count
        relation_count = 0
        if self.metadata_store is not None:
            try:
                target_counts = self._count_vector_rebuild_targets()
                entity_count = min(graph_count, int(target_counts.get("entities", 0) or 0))
                relation_count = max(0, graph_count - entity_count)
            except Exception as exc:
                logger.warning(f"刷新双池 ready manifest 统计失败，使用向量池计数: {exc}")
        stats = {
            "paragraphs": {"done": paragraph_count, "failed": 0},
            "entities": {"done": entity_count, "failed": 0},
            "relations": {"done": relation_count, "failed": 0},
        }
        migration_stats = {
            "paragraphs": {"copied": 0, "encoded": 0, "missing": 0},
            "entities": {"copied": 0, "encoded": 0, "missing": 0},
            "relations": {"copied": 0, "encoded": 0, "missing": 0},
        }
        self._write_dual_vector_ready_manifest(stats=stats, migration_stats=migration_stats)

    def _clear_legacy_single_vector_files_after_dual_ready(self) -> None:
        root = self._vectors_root()
        for filename in ("vectors.bin", "vectors_ids.bin", "vectors.index", "vectors_metadata.pkl"):
            try:
                (root / filename).unlink(missing_ok=True)
            except Exception as exc:
                logger.warning(f"清理旧单池向量文件失败: file={filename}, error={exc}")
        if self.vector_store is not None:
            self.vector_store = self._make_vector_store(root)

    def _prepare_dual_vector_build_dirs(self) -> tuple[Path, Path, Path]:
        build_root = self._vectors_root() / f"dual_build_{int(time.time() * 1000)}"
        if build_root.exists():
            shutil.rmtree(build_root, ignore_errors=True)
        paragraph_dir = build_root / "paragraph"
        graph_dir = build_root / "graph"
        paragraph_dir.mkdir(parents=True, exist_ok=True)
        graph_dir.mkdir(parents=True, exist_ok=True)
        return build_root, paragraph_dir, graph_dir

    def _activate_dual_vector_build_dirs(self, build_root: Path) -> None:
        paragraph_src = build_root / "paragraph"
        graph_src = build_root / "graph"
        if not paragraph_src.exists() or not graph_src.exists():
            raise RuntimeError("dual vector build dirs missing")

        backup_root = self._vectors_root() / f"dual_backup_{int(time.time() * 1000)}"
        backup_paragraph = backup_root / "paragraph"
        backup_graph = backup_root / "graph"
        backup_root.mkdir(parents=True, exist_ok=True)
        paragraph_dst = self._paragraph_vector_dir()
        graph_dst = self._graph_vector_dir()
        try:
            if paragraph_dst.exists():
                shutil.move(str(paragraph_dst), str(backup_paragraph))
            if graph_dst.exists():
                shutil.move(str(graph_dst), str(backup_graph))
            shutil.move(str(paragraph_src), str(paragraph_dst))
            shutil.move(str(graph_src), str(graph_dst))
            shutil.rmtree(build_root, ignore_errors=True)
            shutil.rmtree(backup_root, ignore_errors=True)
        except Exception:
            if paragraph_dst.exists():
                shutil.rmtree(paragraph_dst, ignore_errors=True)
            if graph_dst.exists():
                shutil.rmtree(graph_dst, ignore_errors=True)
            if backup_paragraph.exists():
                shutil.move(str(backup_paragraph), str(paragraph_dst))
            if backup_graph.exists():
                shutil.move(str(backup_graph), str(graph_dst))
            raise

    def _cleanup_stale_dual_vector_build_dirs(self) -> None:
        vectors_root = self._vectors_root()
        if not vectors_root.exists():
            return
        for child in vectors_root.iterdir():
            if child.is_dir() and child.name.startswith("dual_build_"):
                shutil.rmtree(child, ignore_errors=True)
            elif child.is_dir() and child.name.startswith("dual_backup_"):
                shutil.rmtree(child, ignore_errors=True)

    def _make_vector_store(self, data_dir: Path, *, dimension: Optional[int] = None) -> VectorStore:
        return VectorStore(
            dimension=max(1, int(dimension or self.embedding_dimension)),
            quantization_type=QuantizationType.INT8,
            data_dir=data_dir,
        )

    def _save_vector_store(self, store: Optional[VectorStore]) -> None:
        if store is None:
            return
        store.save(embedding_fingerprint=self._current_embedding_fingerprint())

    def _reload_dual_vector_stores_from_disk(self) -> bool:
        if not self._dual_vector_ready(expected_dimension=self.embedding_dimension):
            self._try_recover_dual_ready_manifest()
        if not self._dual_vector_ready(expected_dimension=self.embedding_dimension):
            self.paragraph_vector_store = self._make_vector_store(self._paragraph_vector_dir())
            self.graph_vector_store = self._make_vector_store(self._graph_vector_dir())
            self._dual_vector_pools_ready = False
            return False
        try:
            paragraph_store = self._make_vector_store(self._paragraph_vector_dir())
            graph_store = self._make_vector_store(self._graph_vector_dir())
            if paragraph_store.has_data():
                paragraph_store.load()
                paragraph_store.warmup_index(force_train=True)
            if graph_store.has_data():
                graph_store.load()
                graph_store.warmup_index(force_train=True)
        except Exception as exc:
            logger.warning(f"加载双池向量失败，将暂时回退单池: {exc}")
            self._dual_vector_pools_ready = False
            return False
        self.paragraph_vector_store = paragraph_store
        self.graph_vector_store = graph_store
        self._dual_vector_pools_ready = True
        return True

    def _try_recover_dual_ready_manifest(self) -> bool:
        if not self._dual_vector_pools_config_enabled() or self.metadata_store is None:
            return False
        if self._dual_vector_ready_manifest_path().exists():
            return False
        paragraph_dir = self._paragraph_vector_dir()
        graph_dir = self._graph_vector_dir()
        if not paragraph_dir.exists() or not graph_dir.exists():
            return False
        paragraph_store = self._make_vector_store(paragraph_dir)
        graph_store = self._make_vector_store(graph_dir)
        if not paragraph_store.has_data() or not graph_store.has_data():
            return False
        try:
            if paragraph_store.has_data():
                paragraph_store.load()
            if graph_store.has_data():
                graph_store.load()
        except Exception as exc:
            logger.warning(f"双池 ready manifest 自愈失败，加载向量池异常: {exc}")
            return False

        if (
            not self._stored_vectors_compatible_with_current_embedding(paragraph_store)
            or not self._stored_vectors_compatible_with_current_embedding(graph_store)
        ):
            logger.warning("双池 ready manifest 缺失且向量池指纹无法确认或不匹配，保持单池降级")
            return False

        counts = self._count_vector_rebuild_targets()
        expected_paragraphs = int(counts.get("paragraphs", 0) or 0)
        expected_graph = int(counts.get("entities", 0) or 0)
        if bool(self.relation_vectors_enabled):
            expected_graph += int(counts.get("relations", 0) or 0)
        if paragraph_store.num_vectors != expected_paragraphs or graph_store.num_vectors != expected_graph:
            logger.warning(
                "双池 ready manifest 缺失且向量数量不匹配，保持单池降级: "
                f"paragraph={paragraph_store.num_vectors}/{expected_paragraphs}, "
                f"graph={graph_store.num_vectors}/{expected_graph}"
            )
            return False

        stats = {
            "paragraphs": {"done": expected_paragraphs, "failed": 0},
            "entities": {"done": int(counts.get("entities", 0) or 0), "failed": 0},
            "relations": {"done": int(counts.get("relations", 0) or 0) if bool(self.relation_vectors_enabled) else 0, "failed": 0},
        }
        migration_stats = {
            "paragraphs": {"copied": 0, "encoded": 0, "missing": 0},
            "entities": {"copied": 0, "encoded": 0, "missing": 0},
            "relations": {"copied": 0, "encoded": 0, "missing": 0},
        }
        self._write_dual_vector_ready_manifest(stats=stats, migration_stats=migration_stats)
        logger.warning("检测到双池目录完整但 ready manifest 缺失，已自动重建 manifest")
        return True

    def _drop_dual_build_root(self, build_root: Optional[Path]) -> None:
        if build_root is None:
            return
        try:
            shutil.rmtree(build_root, ignore_errors=True)
        except Exception as exc:
            logger.warning(f"清理双池临时构建目录失败: {exc}")

    def _refresh_relation_write_service(self) -> None:
        if (
            self.metadata_store is None
            or self.graph_store is None
            or self.vector_store is None
            or self.embedding_manager is None
        ):
            self.relation_write_service = None
            return
        self.relation_write_service = RelationWriteService(
            metadata_store=self.metadata_store,
            graph_store=self.graph_store,
            vector_store=self.vector_store,
            graph_vector_store=self._graph_vector_store(),
            embedding_manager=self.embedding_manager,
            use_typed_relation_ids=self._dual_vector_pools_enabled(),
        )

    @staticmethod
    def _graph_vector_id(item_type: str, hash_value: str) -> str:
        return f"{str(item_type or '').strip()}:{str(hash_value or '').strip()}"

    def _paragraph_store(self) -> Optional[VectorStore]:
        if self._dual_vector_pools_enabled():
            return self.paragraph_vector_store or self.vector_store
        return self.vector_store

    def _graph_vector_store(self) -> Optional[VectorStore]:
        if self._dual_vector_pools_enabled():
            return self.graph_vector_store or self.vector_store
        return self.vector_store

    def _delete_vectors_by_type(
        self,
        *,
        paragraph_hashes: Sequence[str] = (),
        entity_hashes: Sequence[str] = (),
        relation_hashes: Sequence[str] = (),
    ) -> int:
        deleted = 0
        legacy_ids = self._merge_tokens(paragraph_hashes, entity_hashes, relation_hashes)
        if self.vector_store is not None and legacy_ids:
            deleted += int(self.vector_store.delete(legacy_ids) or 0)
        if not self._dual_vector_pools_enabled():
            return deleted
        paragraph_ids = self._merge_tokens(paragraph_hashes)
        if self.paragraph_vector_store is not None and paragraph_ids:
            deleted += int(self.paragraph_vector_store.delete(paragraph_ids) or 0)
        graph_ids = [
            self._graph_vector_id("entity", hash_value)
            for hash_value in self._merge_tokens(entity_hashes)
        ]
        graph_ids.extend(
            self._graph_vector_id("relation", hash_value)
            for hash_value in self._merge_tokens(relation_hashes)
        )
        if self.graph_vector_store is not None and graph_ids:
            deleted += int(self.graph_vector_store.delete(graph_ids) or 0)
        return deleted

    def _is_embedding_degraded(self) -> bool:
        return bool(self._embedding_degraded.get("active", False))

    def _embedding_degraded_snapshot(self) -> Dict[str, Any]:
        return {
            "active": bool(self._embedding_degraded.get("active", False)),
            "reason": str(self._embedding_degraded.get("reason", "") or ""),
            "since": self._embedding_degraded.get("since"),
            "last_check": self._embedding_degraded.get("last_check"),
        }

    def _set_embedding_degraded(self, *, active: bool, reason: str = "", checked_at: Optional[float] = None) -> None:
        now = float(checked_at or time.time())
        prev = self._embedding_degraded_snapshot()
        if active:
            since = prev.get("since") if bool(prev.get("active", False)) else now
            self._embedding_degraded = {
                "active": True,
                "reason": str(reason or "").strip(),
                "since": since,
                "last_check": now,
            }
        else:
            self._embedding_degraded = {
                "active": False,
                "reason": "",
                "since": None,
                "last_check": now,
            }
        if bool(prev.get("active", False)) != bool(active):
            if active:
                logger.warning(
                    "embedding 进入降级态，将启用 sparse-only 与 metadata-only 写入回退: "
                    f"reason={self._embedding_degraded.get('reason', '')}"
                )
            else:
                logger.info("embedding 已恢复，退出降级态")
        self._apply_runtime_sparse_mode()

    def _apply_runtime_sparse_mode(self) -> None:
        retriever = self.retriever
        if retriever is None:
            return
        setter = getattr(retriever, "set_runtime_sparse_only", None)
        if not callable(setter):
            return
        try:
            setter(self._is_embedding_degraded())
        except Exception as exc:
            logger.warning(f"设置 retriever sparse-only 运行时状态失败: {exc}")

    async def _refresh_runtime_self_check(self, *, sample_text: str = "A_Memorix runtime self check") -> Dict[str, Any]:
        report = await run_embedding_runtime_self_check(
            config=self._build_runtime_config(),
            vector_store=self.vector_store,
            embedding_manager=self.embedding_manager,
            sample_text=sample_text,
        )
        self._runtime_facade._runtime_self_check_report = dict(report)
        checked_at = float(report.get("checked_at") or time.time())
        self._embedding_degraded["last_check"] = checked_at
        return report

    def _mark_startup_self_check_deferred(self) -> None:
        """记录启动阶段跳过真实 embedding encode 自检，避免阻塞主启动流程。"""
        configured_dimension = max(
            1,
            int(self._cfg("embedding.dimension", self.embedding_dimension) or self.embedding_dimension),
        )
        requested_dimension = int(self.embedding_dimension)
        vector_store_dimension = int(getattr(self.vector_store, "dimension", 0) or 0)
        degraded = self._embedding_degraded_snapshot()
        is_degraded = bool(degraded.get("active", False))
        self._runtime_facade._runtime_self_check_report = {
            "ok": not is_degraded,
            "code": "startup_self_check_deferred_degraded" if is_degraded else "startup_self_check_deferred",
            "message": str(degraded.get("reason", "") or "").strip()
            or "启动阶段已跳过真实 embedding encode 自检，将由后台探测或手动 self_check 执行",
            "configured_dimension": configured_dimension,
            "requested_dimension": requested_dimension,
            "vector_store_dimension": vector_store_dimension,
            "detected_dimension": requested_dimension,
            "encoded_dimension": 0,
            "elapsed_ms": 0.0,
            "sample_text": "",
            "checked_at": None,
        }

    def _is_startup_self_check_deferred(self) -> bool:
        report = self._runtime_facade._runtime_self_check_report
        code = str(report.get("code", "") or "") if isinstance(report, dict) else ""
        return code in {"startup_self_check_deferred", "startup_self_check_deferred_degraded"}

    @staticmethod
    def _self_check_effective_dimension(report: Dict[str, Any]) -> int:
        for key in ("encoded_dimension", "detected_dimension", "requested_dimension"):
            try:
                value = int(report.get(key, 0) or 0)
            except Exception:
                value = 0
            if value > 0:
                return value
        return 0

    def _apply_self_check_dimension_result(self, report: Dict[str, Any]) -> str:
        detected_dimension = self._self_check_effective_dimension(report)
        if detected_dimension <= 0:
            return ""

        self.embedding_dimension = int(detected_dimension)
        vector_dimension = int(getattr(self.vector_store, "dimension", 0) or 0)
        if vector_dimension <= 0 or vector_dimension == detected_dimension:
            return ""

        stored_dimension = self._stored_vector_dimension() or vector_dimension
        message = self._vector_mismatch_error(
            stored_dimension=int(stored_dimension),
            detected_dimension=int(detected_dimension),
        )
        self._vector_persist_blocked_until_rebuild = True
        self._vector_rebuild_source_dimension = int(stored_dimension)
        return message

    def _enqueue_paragraph_vector_backfill(self, paragraph_hash: str, *, error: str = "") -> None:
        if self.metadata_store is None:
            return
        try:
            self.metadata_store.enqueue_paragraph_vector_backfill(
                paragraph_hash,
                error=str(error or ""),
            )
        except Exception as exc:
            logger.warning(f"登记 paragraph 向量回填任务失败: {exc}")

    async def _write_paragraph_vector_or_enqueue(
        self,
        *,
        paragraph_hash: str,
        content: str,
        context: str = "",
    ) -> Dict[str, Any]:
        token = str(paragraph_hash or "").strip()
        text = str(content or "").strip()
        if not token or not text:
            return {
                "success": False,
                "vector_written": False,
                "queued": False,
                "warning": "",
                "detail": "invalid_paragraph_input",
            }

        allow_metadata_only = self._allow_metadata_only_write()

        target_store = self._paragraph_store()
        if target_store is None or self.embedding_manager is None:
            if not allow_metadata_only:
                raise RuntimeError("向量写入依赖未初始化")
            self._enqueue_paragraph_vector_backfill(token, error="vector_runtime_components_missing")
            return {
                "success": True,
                "vector_written": False,
                "queued": True,
                "warning": "vector_degraded_write",
                "detail": "vector_runtime_components_missing",
            }

        if self._is_embedding_degraded():
            if not allow_metadata_only:
                raise RuntimeError("embedding 处于降级态，metadata-only 写入已禁用")
            self._enqueue_paragraph_vector_backfill(token, error="embedding_degraded")
            return {
                "success": True,
                "vector_written": False,
                "queued": True,
                "warning": "vector_degraded_write",
                "detail": "embedding_degraded",
            }

        if token in target_store:
            return {
                "success": True,
                "vector_written": True,
                "queued": False,
                "warning": "",
                "detail": "vector_already_exists",
            }

        try:
            embedding = await self.embedding_manager.encode(text)
            if getattr(embedding, "ndim", 1) == 1:
                embedding = embedding.reshape(1, -1)
            target_store.add(vectors=embedding, ids=[token])
            return {
                "success": True,
                "vector_written": True,
                "queued": False,
                "warning": "",
                "detail": "",
            }
        except Exception as exc:
            error_text = str(exc)
            if self._embedding_fallback_enabled():
                self._set_embedding_degraded(active=True, reason=error_text[:500], checked_at=time.time())
            if not allow_metadata_only:
                raise
            self._enqueue_paragraph_vector_backfill(token, error=error_text)
            return {
                "success": True,
                "vector_written": False,
                "queued": True,
                "warning": "vector_degraded_write",
                "detail": f"{str(context or 'paragraph')} vector write failed: {error_text}",
            }

    def _paragraph_vector_backfill_counts(self) -> Dict[str, int]:
        if self.metadata_store is None:
            return {"pending": 0, "running": 0, "failed": 0, "done": 0}
        try:
            return self.metadata_store.get_paragraph_vector_backfill_status_counts()
        except Exception as exc:
            logger.warning(f"读取 paragraph 回填状态失败: {exc}")
            return {"pending": 0, "running": 0, "failed": 0, "done": 0}

    async def _run_paragraph_backfill_once(
        self,
        *,
        limit: Optional[int] = None,
        max_retry: Optional[int] = None,
        trigger: str = "manual",
    ) -> Dict[str, Any]:
        target_store = self._paragraph_store()
        if self.metadata_store is None or target_store is None or self.embedding_manager is None:
            return {"success": False, "processed": 0, "done": 0, "failed": 0, "trigger": trigger}
        if self._is_embedding_degraded():
            return {
                "success": False,
                "processed": 0,
                "done": 0,
                "failed": 0,
                "trigger": trigger,
                "detail": "embedding_degraded",
            }

        safe_limit = max(1, int(limit or self._paragraph_vector_backfill_batch_size()))
        safe_retry = max(1, int(max_retry or self._paragraph_vector_backfill_max_retry()))
        rows = self.metadata_store.fetch_paragraph_vector_backfill_batch(limit=safe_limit, max_retry=safe_retry)
        if not rows:
            return {"success": True, "processed": 0, "done": 0, "failed": 0, "trigger": trigger}

        pending_hashes = [
            str(row.get("paragraph_hash", "") or "").strip()
            for row in rows
            if str(row.get("paragraph_hash", "") or "").strip()
        ]
        if pending_hashes:
            self.metadata_store.mark_paragraph_vector_backfill_running(pending_hashes)

        done_hashes: List[str] = []
        encode_items: List[tuple[str, str]] = []
        paragraph_map = self.metadata_store.get_paragraphs_by_hashes(pending_hashes)
        for paragraph_hash in pending_hashes:
            if paragraph_hash in target_store:
                done_hashes.append(paragraph_hash)
                continue
            paragraph = paragraph_map.get(paragraph_hash)
            if paragraph is None:
                done_hashes.append(paragraph_hash)
                continue
            content = str(paragraph.get("content", "") or "").strip()
            if not content:
                done_hashes.append(paragraph_hash)
                continue
            encode_items.append((paragraph_hash, content))

        done_count, failed_count, last_error, encoded_done_hashes, failed_hashes = await self._encode_and_add_rebuild_vectors(
            items=encode_items,
            batch_size=safe_limit,
            vector_store=target_store,
        )
        del done_count
        done_hashes.extend(encoded_done_hashes)
        for paragraph_hash in failed_hashes:
            self.metadata_store.mark_paragraph_vector_backfill_failed(paragraph_hash, last_error)
        if failed_hashes and self._embedding_fallback_enabled():
            self._set_embedding_degraded(active=True, reason=last_error[:500], checked_at=time.time())

        if done_hashes:
            self.metadata_store.mark_paragraph_vector_backfill_done(done_hashes)
            self._persist()

        return {
            "success": failed_count == 0,
            "processed": len(done_hashes) + failed_count,
            "done": len(done_hashes),
            "failed": failed_count,
            "trigger": trigger,
        }

    def _count_vector_rebuild_targets(self) -> Dict[str, int]:
        if self.metadata_store is None:
            return {"paragraphs": 0, "entities": 0, "relations": 0}
        paragraph_where = self._active_row_filter_sql("paragraphs")
        entity_where = self._active_row_filter_sql("entities")
        relation_where = self._active_row_filter_sql("relations")
        rows = self.metadata_store.query(
            f"""
            SELECT
                (SELECT COUNT(*) FROM paragraphs WHERE {paragraph_where}) AS paragraphs,
                (SELECT COUNT(*) FROM entities WHERE {entity_where}) AS entities,
                (SELECT COUNT(*) FROM relations WHERE {relation_where}) AS relations
            """
        )
        row = rows[0] if rows else {}
        return {
            "paragraphs": int(row.get("paragraphs", 0) or 0),
            "entities": int(row.get("entities", 0) or 0),
            "relations": int(row.get("relations", 0) or 0),
        }

    def _table_has_column(self, table: str, column: str) -> bool:
        if self.metadata_store is None:
            return False
        token = str(table or "").strip()
        col = str(column or "").strip()
        if token not in {"paragraphs", "entities", "relations"} or not col:
            return False
        rows = self.metadata_store.query(f"PRAGMA table_info({token})")
        return any(str(row.get("name", "") or "") == col for row in rows)

    def _active_row_filter_sql(self, table: str) -> str:
        if str(table or "").strip() == "relations" and self._table_has_column("relations", "is_inactive"):
            return "is_inactive IS NULL OR is_inactive = 0"
        return "is_deleted IS NULL OR is_deleted = 0" if self._table_has_column(table, "is_deleted") else "1 = 1"

    async def _backfill_missing_dual_vector_pool_entries(self, *, batch_size: int) -> Dict[str, Any]:
        if (
            self.metadata_store is None
            or self.vector_store is None
            or self.paragraph_vector_store is None
            or self.graph_vector_store is None
            or not self._dual_vector_pools_enabled()
        ):
            return {"success": False, "error": "dual_pool_not_ready"}

        safe_batch_size = max(1, int(batch_size or self._cfg("embedding.batch_size", 32) or 32))
        stats = {
            "paragraphs": {"done": 0, "failed": 0},
            "entities": {"done": 0, "failed": 0},
            "relations": {"done": 0, "failed": 0},
        }
        migration_stats = {
            "paragraphs": {"copied": 0, "encoded": 0, "missing": 0},
            "entities": {"copied": 0, "encoded": 0, "missing": 0},
            "relations": {"copied": 0, "encoded": 0, "missing": 0},
        }
        errors: List[str] = []
        source_store = self.vector_store
        if source_store is not None and not self._stored_vectors_compatible_with_current_embedding(source_store):
            source_store = None
        if source_store is not None and source_store.has_data():
            try:
                source_store.load()
                source_store.warmup_index(force_train=False)
            except Exception as exc:
                logger.warning(f"加载旧单池向量用于双池增量补齐失败，将回退 embedding 重建: {exc}")

        paragraph_where = self._active_row_filter_sql("paragraphs")
        paragraph_rows = self.metadata_store.query(
            f"""
            SELECT hash, content
            FROM paragraphs
            WHERE {paragraph_where}
            ORDER BY created_at ASC
            """
        )
        paragraph_items = [
            (str(row.get("hash", "") or ""), str(row.get("content", "") or "").strip())
            for row in paragraph_rows
            if str(row.get("hash", "") or "").strip()
            and str(row.get("content", "") or "").strip()
            and str(row.get("hash", "") or "").strip() not in self.paragraph_vector_store
        ]
        done, failed, error, _done_ids, _failed_ids, copy_stats = await self._copy_or_encode_dual_rebuild_vectors(
            items=paragraph_items,
            batch_size=safe_batch_size,
            target_store=self.paragraph_vector_store,
            source_store=source_store,
        )
        stats["paragraphs"] = {"done": done, "failed": failed}
        migration_stats["paragraphs"] = copy_stats
        if error:
            errors.append(f"paragraph_pool_backfill:{error}")

        entity_where = self._active_row_filter_sql("entities")
        entity_rows = self.metadata_store.query(
            f"""
            SELECT hash, name
            FROM entities
            WHERE {entity_where}
            ORDER BY created_at ASC
            """
        )
        entity_items = []
        for row in entity_rows:
            hash_value = str(row.get("hash", "") or "").strip()
            name = str(row.get("name", "") or "").strip()
            if not hash_value or not name:
                continue
            if self._graph_vector_id("entity", hash_value) in self.graph_vector_store:
                continue
            entity_items.append((hash_value, name))
        done, failed, error, _done_ids, _failed_ids, copy_stats = await self._copy_or_encode_dual_rebuild_vectors(
            items=entity_items,
            batch_size=safe_batch_size,
            target_store=self.graph_vector_store,
            target_id_prefix="entity",
            source_store=source_store,
        )
        stats["entities"] = {"done": done, "failed": failed}
        migration_stats["entities"] = copy_stats
        if error:
            errors.append(f"entity_graph_pool_backfill:{error}")

        if self.relation_vectors_enabled:
            relation_where = self._active_row_filter_sql("relations")
            relation_rows = self.metadata_store.query(
                f"""
                SELECT hash, subject, predicate, object
                FROM relations
                WHERE {relation_where}
                ORDER BY created_at ASC
                """
            )
            relation_items = []
            for row in relation_rows:
                hash_value = str(row.get("hash", "") or "").strip()
                if not hash_value:
                    continue
                if self._graph_vector_id("relation", hash_value) in self.graph_vector_store:
                    continue
                relation_items.append(
                    (
                        hash_value,
                        RelationWriteService.build_relation_vector_text(
                            str(row.get("subject", "") or ""),
                            str(row.get("predicate", "") or ""),
                            str(row.get("object", "") or ""),
                        ),
                    )
                )
            done, failed, error, done_ids, failed_ids, copy_stats = await self._copy_or_encode_dual_rebuild_vectors(
                items=relation_items,
                batch_size=safe_batch_size,
                target_store=self.graph_vector_store,
                target_id_prefix="relation",
                source_store=source_store,
            )
            stats["relations"] = {"done": done, "failed": failed}
            migration_stats["relations"] = copy_stats
            if error:
                errors.append(f"relation_graph_pool_backfill:{error}")

            if done_ids or failed_ids:
                conn = self.metadata_store.get_connection()
                cursor = conn.cursor()
                now_ts = time.time()
                for start in range(0, len(done_ids), 500):
                    batch_ids = done_ids[start : start + 500]
                    if not batch_ids:
                        continue
                    placeholders = ",".join("?" for _ in batch_ids)
                    cursor.execute(
                        f"""
                        UPDATE relations
                        SET vector_state = 'ready',
                            vector_updated_at = ?,
                            vector_error = NULL
                        WHERE hash IN ({placeholders})
                        """,
                        (now_ts, *batch_ids),
                    )
                for start in range(0, len(failed_ids), 500):
                    batch_ids = failed_ids[start : start + 500]
                    if not batch_ids:
                        continue
                    placeholders = ",".join("?" for _ in batch_ids)
                    cursor.execute(
                        f"""
                        UPDATE relations
                        SET vector_state = 'failed',
                            vector_updated_at = ?,
                            vector_error = ?,
                            vector_retry_count = COALESCE(vector_retry_count, 0) + 1
                        WHERE hash IN ({placeholders})
                        """,
                        (now_ts, (error or "dual_pool_backfill_failed")[:500], *batch_ids),
                    )
                conn.commit()

        failed_total = sum(int(item["failed"]) for item in stats.values())
        if failed_total:
            self._set_embedding_degraded(
                active=True,
                reason="; ".join(errors)[:500] or "dual_pool_backfill_failed",
                checked_at=time.time(),
            )
        if self.paragraph_vector_store is not None:
            self._save_vector_store(self.paragraph_vector_store)
        if self.graph_vector_store is not None:
            self._save_vector_store(self.graph_vector_store)
        self._refresh_dual_vector_ready_manifest_from_stores()
        return {
            "success": failed_total == 0,
            "stats": stats,
            "migration": migration_stats,
            "failed": int(failed_total),
            "errors": errors[:5],
        }

    def _refresh_runtime_dependents(self, *, preserve_managers: bool = True) -> None:
        if (
            self.metadata_store is None
            or self.graph_store is None
            or self.vector_store is None
            or self.embedding_manager is None
            or self.retriever is None
        ):
            return

        runtime_config = self._build_runtime_config()
        self.episode_retriever = EpisodeRetrievalService(metadata_store=self.metadata_store, retriever=self.retriever)
        self.aggregate_query_service = AggregateQueryService(plugin_config=runtime_config)
        self.person_profile_service = PersonProfileService(
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
        self.episode_segmentation_service = EpisodeSegmentationService(plugin_config=runtime_config)
        self.episode_service = EpisodeService(
            metadata_store=self.metadata_store,
            plugin_config=runtime_config,
            segmentation_service=self.episode_segmentation_service,
        )
        self.summary_importer = SummaryImporter(
            vector_store=self.vector_store,
            graph_store=self.graph_store,
            metadata_store=self.metadata_store,
            embedding_manager=self.embedding_manager,
            plugin_config=runtime_config,
        )
        if not preserve_managers:
            self.import_task_manager = ImportTaskManager(self._runtime_facade)
            self.retrieval_tuning_manager = RetrievalTuningManager(
                self._runtime_facade,
                import_write_blocked_provider=self.import_task_manager.is_write_blocked,
            )

    async def _encode_and_add_rebuild_vectors(
        self,
        *,
        items: Sequence[tuple[str, str]],
        batch_size: int,
        vector_store: Optional[VectorStore] = None,
    ) -> tuple[int, int, str, List[str], List[str]]:
        target_store = vector_store or self.vector_store
        if target_store is None or self.embedding_manager is None:
            failed_ids = [item_id for item_id, _ in items]
            return 0, len(items), "vector_runtime_components_missing", [], failed_ids

        done = 0
        failed = 0
        last_error = ""
        done_ids: List[str] = []
        failed_ids: List[str] = []
        safe_batch_size = max(1, int(batch_size))
        for start in range(0, len(items), safe_batch_size):
            batch = list(items[start : start + safe_batch_size])
            ids = [item_id for item_id, _ in batch]
            texts = [text for _, text in batch]
            try:
                encoder = getattr(self.embedding_manager, "encode_batch", None)
                if callable(encoder):
                    embeddings = await encoder(texts, batch_size=safe_batch_size)
                else:
                    embeddings = await self.embedding_manager.encode(texts)
                embedding_array = np.asarray(embeddings, dtype=np.float32)
                if embedding_array.ndim == 1:
                    embedding_array = embedding_array.reshape(1, -1)
                if embedding_array.shape[0] != len(ids):
                    raise ValueError(f"embedding 返回数量异常: expected={len(ids)}, got={embedding_array.shape[0]}")
                target_store.add(vectors=embedding_array, ids=ids)
                done += len(ids)
                done_ids.extend(ids)
            except Exception as exc:
                last_error = str(exc)[:500]
                failed += len(ids)
                failed_ids.extend(ids)
                logger.warning(f"重建向量批次失败: start={start}, count={len(ids)}, error={last_error}")
        return done, failed, last_error, done_ids, failed_ids

    def _copy_rebuild_vectors_from_store(
        self,
        *,
        source_store: Optional[VectorStore],
        target_store: Optional[VectorStore],
        id_pairs: Sequence[tuple[str, str]],
        batch_size: int = 1024,
    ) -> tuple[int, List[str], List[tuple[str, str]]]:
        if source_store is None or target_store is None or not id_pairs:
            return 0, [], list(id_pairs)

        pair_by_source = {source_id: target_id for source_id, target_id in id_pairs}
        source_ids = list(pair_by_source.keys())
        iterator = getattr(source_store, "iter_vectors_by_ids", None)
        getter = getattr(source_store, "get_vectors", None)
        if not callable(iterator) and not callable(getter):
            return 0, [], list(id_pairs)

        try:
            if callable(iterator):
                vector_batches = iterator(source_ids, batch_size=max(1, int(batch_size or 1024)))
            else:
                vector_batches = [getter(source_ids)]
        except Exception as exc:
            logger.warning(f"读取旧向量失败，将回退 embedding 重建: {exc}")
            return 0, [], list(id_pairs)

        copied_source_ids: List[str] = []
        copied_set: set[str] = set()
        try:
            for source_vectors in vector_batches:
                if not isinstance(source_vectors, dict) or not source_vectors:
                    continue
                target_ids: List[str] = []
                vectors: List[np.ndarray] = []
                for source_id, vector in source_vectors.items():
                    target_id = pair_by_source.get(source_id)
                    if target_id is None or source_id in copied_set:
                        continue
                    target_ids.append(target_id)
                    vectors.append(np.asarray(vector, dtype=np.float32))
                    copied_source_ids.append(source_id)
                    copied_set.add(source_id)
                if not target_ids:
                    continue
                vector_array = np.asarray(vectors, dtype=np.float32)
                if vector_array.ndim == 1:
                    vector_array = vector_array.reshape(1, -1)
                added = int(target_store.add(vectors=vector_array, ids=target_ids) or 0)
                if added < len(target_ids):
                    logger.debug(f"复制旧向量到新池时存在已写入项: requested={len(target_ids)} added={added}")
        except Exception as exc:
            logger.warning(f"复制旧向量到新池失败，将回退 embedding 重建: {exc}")
            return 0, [], list(id_pairs)

        missing_pairs = [(source_id, target_id) for source_id, target_id in id_pairs if source_id not in copied_set]
        return len(copied_source_ids), copied_source_ids, missing_pairs

    async def _copy_or_encode_dual_rebuild_vectors(
        self,
        *,
        items: Sequence[tuple[str, str]],
        batch_size: int,
        target_store: Optional[VectorStore],
        target_id_prefix: str = "",
        source_store: Optional[VectorStore] = None,
    ) -> tuple[int, int, str, List[str], List[str], Dict[str, int]]:
        id_pairs = [
            (
                str(item_id or "").strip(),
                f"{target_id_prefix}:{str(item_id or '').strip()}" if target_id_prefix else str(item_id or "").strip(),
            )
            for item_id, _text in items
            if str(item_id or "").strip()
        ]
        copied, copied_source_ids, missing_pairs = self._copy_rebuild_vectors_from_store(
            source_store=source_store,
            target_store=target_store,
            id_pairs=id_pairs,
            batch_size=batch_size,
        )
        missing_source_ids = {source_id for source_id, _target_id in missing_pairs}
        text_by_id = {str(item_id or "").strip(): text for item_id, text in items}
        encode_items = [
            (
                target_id,
                text_by_id.get(source_id, ""),
            )
            for source_id, target_id in missing_pairs
            if str(text_by_id.get(source_id, "") or "").strip()
        ]
        done, failed, error, encoded_done_ids, encoded_failed_ids = await self._encode_and_add_rebuild_vectors(
            items=encode_items,
            batch_size=batch_size,
            vector_store=target_store,
        )

        def _source_id(target_id: str) -> str:
            if target_id_prefix and target_id.startswith(f"{target_id_prefix}:"):
                return target_id.split(":", 1)[1]
            return target_id

        done_source_ids = copied_source_ids + [_source_id(item_id) for item_id in encoded_done_ids]
        failed_source_ids = [_source_id(item_id) for item_id in encoded_failed_ids]
        skipped_missing = len(missing_source_ids) - len(encode_items)
        failed += max(0, skipped_missing)
        return (
            copied + done,
            failed,
            error,
            done_source_ids,
            failed_source_ids,
            {
                "copied": copied,
                "encoded": done,
                "missing": len(missing_pairs),
            },
        )

    async def _rebuild_all_vectors(
        self,
        *,
        batch_size: Optional[int] = None,
        include_relations: Optional[bool] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        if self._vector_rebuild_lock.locked():
            return {
                "success": False,
                "error": "vector_rebuild_running",
                "detail": "已有向量重建任务正在运行",
            }
        async with self._vector_rebuild_lock:
            return await self._rebuild_all_vectors_locked(
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
        if self.metadata_store is None or self.vector_store is None or self.embedding_manager is None:
            return {"success": False, "error": "runtime_components_missing"}

        target_counts = self._count_vector_rebuild_targets()
        relation_enabled = bool(self.relation_vectors_enabled if include_relations is None else include_relations)
        if not relation_enabled:
            target_counts["relations"] = 0
        total = target_counts["paragraphs"] + target_counts["entities"] + target_counts["relations"]
        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "counts": target_counts,
                "total": int(total),
                **self._vector_rebuild_status(),
            }

        started = time.time()
        safe_batch_size = max(1, int(batch_size or self._cfg("embedding.batch_size", 32) or 32))
        self._set_embedding_degraded(
            active=True,
            reason="正在重建全部向量，检索临时降级",
            checked_at=started,
        )

        dual_mode = self._dual_vector_pools_config_enabled()
        legacy_source_store = self.vector_store if dual_mode else None
        self._update_dual_vector_auto_migration_stage(
            "prepare_rebuild",
            dual_mode=dual_mode,
            total=int(total),
            counts=dict(target_counts),
            legacy_source_available=legacy_source_store is not None,
        )
        if legacy_source_store is not None and not self._stored_vectors_compatible_with_current_embedding(
            legacy_source_store
        ):
            legacy_source_store = None
            self._update_dual_vector_auto_migration_stage("legacy_source_incompatible")
        dual_build_root: Optional[Path] = None
        build_paragraph_vector_store: Optional[VectorStore] = None
        build_graph_vector_store: Optional[VectorStore] = None
        if dual_mode and legacy_source_store is not None and legacy_source_store.has_data():
            try:
                self._update_dual_vector_auto_migration_stage("legacy_source_load")
                legacy_source_store.load()
                self._update_dual_vector_auto_migration_stage("legacy_source_warmup")
                legacy_source_store.warmup_index(force_train=False)
                self._update_dual_vector_auto_migration_stage("legacy_source_ready")
            except Exception as exc:
                logger.warning(f"加载旧单池向量用于双池迁移失败，将回退 embedding 重建: {exc}")
        if not dual_mode:
            self._dual_vector_pools_ready = False
            self._remove_dual_vector_ready_manifest()
            self.vector_store = self._make_vector_store(self._vectors_root())
            self.vector_store.clear()
            self.paragraph_vector_store = self._make_vector_store(self._paragraph_vector_dir())
            self.graph_vector_store = self._make_vector_store(self._graph_vector_dir())
            self._refresh_relation_write_service()
        else:
            dual_build_root, paragraph_data_dir, graph_data_dir = self._prepare_dual_vector_build_dirs()
            build_paragraph_vector_store = self._make_vector_store(paragraph_data_dir)
            build_graph_vector_store = self._make_vector_store(graph_data_dir)
        stats = {
            "paragraphs": {"done": 0, "failed": 0},
            "entities": {"done": 0, "failed": 0},
            "relations": {"done": 0, "failed": 0},
        }
        migration_stats = {
            "paragraphs": {"copied": 0, "encoded": 0, "missing": 0},
            "entities": {"copied": 0, "encoded": 0, "missing": 0},
            "relations": {"copied": 0, "encoded": 0, "missing": 0},
        }
        errors: List[str] = []
        paragraph_where = self._active_row_filter_sql("paragraphs")
        entity_where = self._active_row_filter_sql("entities")
        relation_where = self._active_row_filter_sql("relations")

        paragraph_rows = self.metadata_store.query(
            f"""
            SELECT hash, content
            FROM paragraphs
            WHERE {paragraph_where}
            ORDER BY created_at ASC
            """
        )
        paragraph_items = [
            (str(row.get("hash", "") or ""), str(row.get("content", "") or "").strip())
            for row in paragraph_rows
            if str(row.get("hash", "") or "").strip() and str(row.get("content", "") or "").strip()
        ]
        self._update_dual_vector_auto_migration_stage("paragraphs_start", paragraph_items=len(paragraph_items))
        if dual_mode:
            done, failed, error, _done_ids, _failed_ids, copy_stats = await self._copy_or_encode_dual_rebuild_vectors(
                items=paragraph_items,
                batch_size=safe_batch_size,
                target_store=build_paragraph_vector_store,
                source_store=legacy_source_store,
            )
            migration_stats["paragraphs"] = copy_stats
            if error:
                errors.append(f"paragraph_pool:{error}")
        else:
            done, failed, error, _done_ids, _failed_ids = await self._encode_and_add_rebuild_vectors(
                items=paragraph_items,
                batch_size=safe_batch_size,
            )
            if error:
                errors.append(error)
        stats["paragraphs"] = {"done": done, "failed": failed}
        self._update_dual_vector_auto_migration_stage(
            "paragraphs_done",
            paragraph_done=done,
            paragraph_failed=failed,
            paragraph_migration=dict(migration_stats.get("paragraphs") or {}),
        )

        entity_rows = self.metadata_store.query(
            f"""
            SELECT hash, name
            FROM entities
            WHERE {entity_where}
            ORDER BY created_at ASC
            """
        )
        entity_items = [
            (str(row.get("hash", "") or ""), str(row.get("name", "") or "").strip())
            for row in entity_rows
            if str(row.get("hash", "") or "").strip() and str(row.get("name", "") or "").strip()
        ]
        self._update_dual_vector_auto_migration_stage("entities_start", entity_items=len(entity_items))
        if dual_mode:
            done, failed, error, _done_ids, _failed_ids, copy_stats = await self._copy_or_encode_dual_rebuild_vectors(
                items=entity_items,
                batch_size=safe_batch_size,
                target_store=build_graph_vector_store,
                target_id_prefix="entity",
                source_store=legacy_source_store,
            )
            migration_stats["entities"] = copy_stats
            if error:
                errors.append(f"entity_graph_pool:{error}")
        else:
            done, failed, error, _done_ids, _failed_ids = await self._encode_and_add_rebuild_vectors(
                items=entity_items,
                batch_size=safe_batch_size,
            )
            if error:
                errors.append(error)
        stats["entities"] = {"done": done, "failed": failed}
        self._update_dual_vector_auto_migration_stage(
            "entities_done",
            entity_done=done,
            entity_failed=failed,
            entity_migration=dict(migration_stats.get("entities") or {}),
        )

        if relation_enabled:
            relation_rows = self.metadata_store.query(
                f"""
                SELECT hash, subject, predicate, object
                FROM relations
                WHERE {relation_where}
                ORDER BY created_at ASC
                """
            )
            relation_items = [
                (
                    str(row.get("hash", "") or ""),
                    RelationWriteService.build_relation_vector_text(
                        str(row.get("subject", "") or ""),
                        str(row.get("predicate", "") or ""),
                        str(row.get("object", "") or ""),
                    ),
                )
                for row in relation_rows
                if str(row.get("hash", "") or "").strip()
            ]
            self._update_dual_vector_auto_migration_stage("relations_start", relation_items=len(relation_items))
            if dual_mode:
                done, failed, error, done_ids, failed_ids, copy_stats = await self._copy_or_encode_dual_rebuild_vectors(
                    items=relation_items,
                    batch_size=safe_batch_size,
                    target_store=build_graph_vector_store,
                    target_id_prefix="relation",
                    source_store=legacy_source_store,
                )
                migration_stats["relations"] = copy_stats
                if error:
                    errors.append(f"relation_graph_pool:{error}")
            else:
                done, failed, error, done_ids, failed_ids = await self._encode_and_add_rebuild_vectors(
                    items=relation_items,
                    batch_size=safe_batch_size,
                )
                if error:
                    errors.append(error)
            stats["relations"] = {"done": done, "failed": failed}
            self._update_dual_vector_auto_migration_stage(
                "relations_done",
                relation_done=done,
                relation_failed=failed,
                relation_migration=dict(migration_stats.get("relations") or {}),
            )

            conn = self.metadata_store.get_connection()
            cursor = conn.cursor()
            now_ts = time.time()
            for start in range(0, len(done_ids), 500):
                batch_ids = done_ids[start : start + 500]
                if not batch_ids:
                    continue
                placeholders = ",".join("?" for _ in batch_ids)
                cursor.execute(
                    f"""
                    UPDATE relations
                    SET vector_state = 'ready',
                        vector_updated_at = ?,
                        vector_error = NULL
                    WHERE hash IN ({placeholders})
                    """,
                    (now_ts, *batch_ids),
                )
            for start in range(0, len(failed_ids), 500):
                batch_ids = failed_ids[start : start + 500]
                if not batch_ids:
                    continue
                placeholders = ",".join("?" for _ in batch_ids)
                cursor.execute(
                    f"""
                    UPDATE relations
                    SET vector_state = 'failed',
                        vector_updated_at = ?,
                        vector_error = ?
                    WHERE hash IN ({placeholders})
                    """,
                    (now_ts, error[:500], *batch_ids),
                )
            conn.commit()

        done_total = sum(int(item["done"]) for item in stats.values())
        failed_total = sum(int(item["failed"]) for item in stats.values())
        activation_ok = True
        if dual_mode:
            expected_paragraph_vectors = int(stats["paragraphs"]["done"])
            expected_graph_vectors = int(stats["entities"]["done"]) + int(stats["relations"]["done"])
            actual_paragraph_vectors = (
                int(build_paragraph_vector_store.num_vectors) if build_paragraph_vector_store else 0
            )
            actual_graph_vectors = int(build_graph_vector_store.num_vectors) if build_graph_vector_store else 0
            self._update_dual_vector_auto_migration_stage(
                "activation_check",
                stats=dict(stats),
                migration=dict(migration_stats),
                actual_paragraph_vectors=actual_paragraph_vectors,
                expected_paragraph_vectors=expected_paragraph_vectors,
                actual_graph_vectors=actual_graph_vectors,
                expected_graph_vectors=expected_graph_vectors,
            )
            if (
                failed_total == 0
                and actual_paragraph_vectors == expected_paragraph_vectors
                and actual_graph_vectors == expected_graph_vectors
            ):
                try:
                    if build_paragraph_vector_store is not None:
                        self._update_dual_vector_auto_migration_stage("paragraph_pool_warmup")
                        build_paragraph_vector_store.warmup_index(force_train=True)
                        self._update_dual_vector_auto_migration_stage("paragraph_pool_save")
                        self._save_vector_store(build_paragraph_vector_store)
                    if build_graph_vector_store is not None:
                        self._update_dual_vector_auto_migration_stage("graph_pool_warmup")
                        build_graph_vector_store.warmup_index(force_train=True)
                        self._update_dual_vector_auto_migration_stage("graph_pool_save")
                        self._save_vector_store(build_graph_vector_store)
                    self._update_dual_vector_auto_migration_stage("activate_dirs")
                    self._activate_dual_vector_build_dirs(dual_build_root)
                    self._update_dual_vector_auto_migration_stage("write_manifest")
                    self._write_dual_vector_ready_manifest(stats=stats, migration_stats=migration_stats)
                    self._update_dual_vector_auto_migration_stage("reload_dual_stores")
                    activation_ok = self._reload_dual_vector_stores_from_disk()
                    if not activation_ok:
                        errors.append("dual_pool_activation:ready_manifest_unusable")
                    else:
                        self._update_dual_vector_auto_migration_stage("dual_backfill")
                        backfill_result = await self._backfill_missing_dual_vector_pool_entries(
                            batch_size=safe_batch_size,
                        )
                        self._update_dual_vector_auto_migration_stage("dual_backfill_done", backfill=backfill_result)
                        if not bool(backfill_result.get("success", False)):
                            for item in backfill_result.get("errors", []) or []:
                                errors.append(str(item))
                        self._update_dual_vector_auto_migration_stage("clear_legacy_single_pool")
                        self._clear_legacy_single_vector_files_after_dual_ready()
                except Exception as exc:
                    activation_ok = False
                    self._dual_vector_pools_ready = False
                    errors.append(f"dual_pool_activation:{str(exc)[:500]}")
                    logger.warning(f"双池临时构建目录切换失败，保留原有向量池: {exc}")
                    self._drop_dual_build_root(dual_build_root)
                    self._reload_dual_vector_stores_from_disk()
            else:
                activation_ok = False
                if failed_total == 0:
                    errors.append(
                        "dual_pool_activation:vector_count_mismatch "
                        f"paragraph={actual_paragraph_vectors}/{expected_paragraph_vectors}, "
                        f"graph={actual_graph_vectors}/{expected_graph_vectors}"
                    )
                self._drop_dual_build_root(dual_build_root)
                self._reload_dual_vector_stores_from_disk()
            self._refresh_relation_write_service()
        else:
            self._update_dual_vector_auto_migration_stage("single_pool_warmup")
            self.vector_store.warmup_index(force_train=True)
            self.paragraph_vector_store = self._make_vector_store(self._paragraph_vector_dir())
            self.graph_vector_store = self._make_vector_store(self._graph_vector_dir())
            self._refresh_relation_write_service()
        self._update_dual_vector_auto_migration_stage("runtime_rebuild")
        self._runtime_bundle = build_search_runtime(
            plugin_config=self._build_runtime_config(),
            logger_obj=logger,
            owner_tag="sdk_kernel",
            log_prefix="[sdk]",
        )
        if self._runtime_bundle.ready:
            self.retriever = self._runtime_bundle.retriever
            self.threshold_filter = self._runtime_bundle.threshold_filter
            self.sparse_index = self._runtime_bundle.sparse_index or self.sparse_index
            self._refresh_runtime_dependents(preserve_managers=True)
            self._apply_runtime_sparse_mode()

        self._update_dual_vector_auto_migration_stage("self_check")
        report = await self._refresh_runtime_self_check(sample_text="A_Memorix vector rebuild self check")
        if bool(report.get("ok", False)) and not errors:
            self._set_embedding_degraded(active=False, checked_at=float(report.get("checked_at") or time.time()))
        else:
            self._set_embedding_degraded(
                active=True,
                reason=str(report.get("message") or "; ".join(errors) or "vector_rebuild_incomplete")[:500],
                checked_at=float(report.get("checked_at") or time.time()),
            )

        elapsed_ms = (time.time() - started) * 1000.0
        rebuild_success = failed_total == 0 and bool(report.get("ok", False)) and (not dual_mode or activation_ok)
        if rebuild_success:
            self._vector_persist_blocked_until_rebuild = False
            self._vector_rebuild_source_dimension = None
        self._update_dual_vector_auto_migration_stage("persist", rebuild_success=rebuild_success, errors=list(errors[:5]))
        self._persist(force_vectors=rebuild_success)
        return {
            "success": rebuild_success,
            "dry_run": False,
            "counts": target_counts,
            "stats": stats,
            "migration": migration_stats,
            "total": int(total),
            "done": int(done_total),
            "failed": int(failed_total),
            "errors": errors[:5],
            "elapsed_ms": elapsed_ms,
            "self_check": report,
            **self._vector_rebuild_status(),
        }

    async def _recover_embedding_once(self, *, sample_text: str = "A_Memorix runtime self check") -> Dict[str, Any]:
        report = await self._refresh_runtime_self_check(sample_text=sample_text)
        checked_at = float(report.get("checked_at") or time.time())
        ok = bool(report.get("ok", False))
        dimension_mismatch = self._apply_self_check_dimension_result(report)
        if dimension_mismatch:
            self._set_embedding_degraded(active=True, reason=dimension_mismatch, checked_at=checked_at)
            return {
                "success": False,
                "recovered": False,
                "report": report,
                "detail": "dimension_mismatch",
            }

        if ok:
            self._set_embedding_degraded(active=False, checked_at=checked_at)
            backfill_result: Dict[str, Any] = {}
            if self._paragraph_vector_backfill_enabled():
                backfill_result = await self._run_paragraph_backfill_once(
                    limit=self._paragraph_vector_backfill_batch_size(),
                    max_retry=self._paragraph_vector_backfill_max_retry(),
                    trigger="embedding_recovered",
                )
            return {
                "success": True,
                "recovered": True,
                "report": report,
                "backfill": backfill_result,
            }

        reason = str(report.get("message", "runtime self-check failed") or "runtime self-check failed")
        if self._embedding_fallback_enabled():
            self._set_embedding_degraded(active=True, reason=reason, checked_at=checked_at)
            return {
                "success": False,
                "recovered": False,
                "report": report,
                "detail": "still_degraded",
            }
        return {
            "success": False,
            "recovered": False,
            "report": report,
            "detail": "fallback_disabled",
        }

    async def initialize(self) -> None:
        if self._initialized:
            self._apply_runtime_sparse_mode()
            await self._start_background_tasks()
            return

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_manager = create_embedding_api_adapter(
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
        graph_format = SparseMatrixFormat.CSC if matrix_format == "csc" else SparseMatrixFormat.CSR

        self.vector_store = self._make_vector_store(self._vectors_root(), dimension=provisional_dimension)
        self.paragraph_vector_store = self._make_vector_store(
            self._paragraph_vector_dir(),
            dimension=provisional_dimension,
        )
        self.graph_vector_store = self._make_vector_store(
            self._graph_vector_dir(),
            dimension=provisional_dimension,
        )
        self.graph_store = GraphStore(matrix_format=graph_format, data_dir=self.data_dir / "graph")
        self.metadata_store = MetadataStore(data_dir=self.data_dir / "metadata")
        self.metadata_store.connect()

        skip_vector_load = False
        if self.graph_store.has_data():
            self.graph_store.load()

        sparse_cfg_raw = self._cfg("retrieval.sparse", {}) or {}
        try:
            sparse_cfg = SparseBM25Config(**sparse_cfg_raw)
        except Exception as exc:
            logger.warning(f"sparse 配置非法，回退默认: {exc}")
            sparse_cfg = SparseBM25Config()
        self.sparse_index = SparseBM25Index(metadata_store=self.metadata_store, config=sparse_cfg)
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
        self._runtime_bundle = build_search_runtime(
            plugin_config=runtime_config,
            logger_obj=logger,
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
        self.import_task_manager = ImportTaskManager(self._runtime_facade)
        self.retrieval_tuning_manager = RetrievalTuningManager(
            self._runtime_facade,
            import_write_blocked_provider=self.import_task_manager.is_write_blocked,
        )

        self._mark_startup_self_check_deferred()

        self._initialized = True
        await self._start_background_tasks()

    async def shutdown(self) -> None:
        await self._stop_background_tasks()
        if self.import_task_manager is not None:
            try:
                await self.import_task_manager.shutdown()
            except Exception as exc:
                logger.warning(f"关闭导入任务管理器失败: {exc}")
        if self.retrieval_tuning_manager is not None:
            try:
                await self.retrieval_tuning_manager.shutdown()
            except Exception as exc:
                logger.warning(f"关闭调优任务管理器失败: {exc}")
        self.close()

    def close(self) -> None:
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

    async def execute_request_with_dedup(
        self,
        request_key: str,
        executor: Callable[[], Coroutine[Any, Any, Dict[str, Any]]],
    ) -> tuple[bool, Dict[str, Any]]:
        token = str(request_key or "").strip()
        if not token:
            return False, await executor()

        existing = self._request_dedup_tasks.get(token)
        if existing is not None:
            return True, await existing

        task = asyncio.create_task(executor())
        self._request_dedup_tasks[token] = task
        try:
            payload = await task
            return False, payload
        finally:
            current = self._request_dedup_tasks.get(token)
            if current is task:
                self._request_dedup_tasks.pop(token, None)

    async def summarize_chat_stream(
        self,
        *,
        chat_id: str,
        context_length: Optional[int] = None,
        include_personality: Optional[bool] = None,
        time_end: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        await self.initialize()
        assert self.summary_importer
        import_result = await self.summary_importer.import_from_stream(
            stream_id=str(chat_id or "").strip(),
            context_length=context_length,
            include_personality=include_personality,
            time_end=time_end,
            metadata=metadata,
        )
        success = bool(getattr(import_result, "success", False))
        detail = str(getattr(import_result, "detail", "") or "")
        paragraph_hash = str(getattr(import_result, "paragraph_hash", "") or "").strip()
        source = (
            str(getattr(import_result, "source", "") or "").strip()
            or self._build_source("chat_summary", chat_id, [])
        )
        stored_ids: List[str] = []
        episode_pending_ids: List[str] = []
        if success:
            if not paragraph_hash:
                raise RuntimeError("聊天摘要导入成功但未返回 paragraph_hash，无法执行 Episode 增量入队")
            assert self.metadata_store is not None
            if self._should_auto_enqueue_episode(source_type="chat_summary"):
                self.metadata_store.enqueue_episode_pending(paragraph_hash, source=source)
                episode_pending_ids.append(paragraph_hash)
            stored_ids.append(paragraph_hash)
            self._persist()
        payload = {"success": success, "detail": detail}
        if stored_ids:
            payload["stored_ids"] = stored_ids
        if episode_pending_ids:
            payload["episode_pending_ids"] = episode_pending_ids
        return payload

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
        external_token = str(external_id or "").strip() or compute_hash(f"chat_summary:{chat_id}:{text}")
        if self._is_chat_filtered(
            respect_filter=respect_filter,
            stream_id=chat_id,
            group_id=group_id,
            user_id=user_id,
        ):
            return {
                "success": True,
                "stored_ids": [],
                "skipped_ids": [external_token],
                "detail": "chat_filtered",
            }

        summary_meta = coerce_metadata_dict(metadata)
        summary_meta.setdefault("kind", "chat_summary")
        if not str(text or "").strip() or bool(summary_meta.get("generate_from_chat", False)):
            result = await self.summarize_chat_stream(
                chat_id=chat_id,
                context_length=self._optional_int(summary_meta.get("context_length")),
                include_personality=summary_meta.get("include_personality"),
                time_end=time_end,
                metadata={
                    **summary_meta,
                    "external_id": external_token,
                    "chat_id": str(chat_id or "").strip(),
                    "source_type": "chat_summary",
                },
            )
            result.setdefault("external_id", external_id)
            result.setdefault("chat_id", chat_id)
            return result
        return await self.ingest_text(
            external_id=external_id,
            source_type="chat_summary",
            text=text,
            chat_id=chat_id,
            participants=participants,
            time_start=time_start,
            time_end=time_end,
            tags=tags,
            metadata=summary_meta,
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
        content = normalize_text(text)
        external_token = str(external_id or "").strip() or compute_hash(f"{source_type}:{chat_id}:{content}")
        if self._is_chat_filtered(
            respect_filter=respect_filter,
            stream_id=chat_id,
            group_id=group_id,
            user_id=user_id,
        ):
            return {
                "success": True,
                "stored_ids": [],
                "skipped_ids": [external_token],
                "detail": "chat_filtered",
            }

        await self.initialize()
        assert self.metadata_store is not None
        assert self.vector_store is not None
        assert self.graph_store is not None
        assert self.embedding_manager is not None
        assert self.relation_write_service is not None

        if not content:
            return {"stored_ids": [], "skipped_ids": [external_token], "reason": "empty_text"}

        existing_ref = self.metadata_store.get_external_memory_ref(external_token)
        if existing_ref:
            return {
                "stored_ids": [],
                "skipped_ids": [str(existing_ref.get("paragraph_hash", "") or "")],
                "reason": "exists",
            }

        person_tokens = self._tokens(person_ids)
        participant_tokens = self._tokens(participants)
        entity_tokens = self._merge_tokens(entities, person_tokens, participant_tokens)
        source = self._build_source(source_type, chat_id, person_tokens)
        paragraph_meta = coerce_metadata_dict(metadata)
        paragraph_meta.update(
            {
                "external_id": external_token,
                "source_type": str(source_type or "").strip(),
                "chat_id": str(chat_id or "").strip(),
                "person_ids": person_tokens,
                "participants": participant_tokens,
                "tags": self._tokens(tags),
            }
        )
        warnings: List[str] = []

        paragraph_hash = self.metadata_store.add_paragraph(
            content=content,
            source=source,
            metadata=paragraph_meta,
            knowledge_type=self._resolve_knowledge_type(source_type),
            time_meta=self._time_meta(timestamp, time_start, time_end),
        )
        vector_result = await self._write_paragraph_vector_or_enqueue(
            paragraph_hash=paragraph_hash,
            content=content,
            context="ingest_text",
        )
        warning = str(vector_result.get("warning", "") or "").strip()
        if warning:
            warnings.append(warning)

        for name in entity_tokens:
            entity_hash = self.metadata_store.add_entity(name=name, source_paragraph=paragraph_hash)
            await self._ensure_entity_vector({"hash": entity_hash, "name": name})

        stored_relations: List[str] = []
        for row in [dict(item) for item in (relations or []) if isinstance(item, dict)]:
            subject = str(row.get("subject", "") or "").strip()
            predicate = str(row.get("predicate", "") or "").strip()
            obj = str(row.get("object", "") or "").strip()
            if not (subject and predicate and obj):
                continue
            result = await self.relation_write_service.upsert_relation_with_vector(
                subject=subject,
                predicate=predicate,
                obj=obj,
                confidence=float(row.get("confidence", 1.0) or 1.0),
                source_paragraph=paragraph_hash,
                metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {"external_id": external_token, "source_type": source_type},
                write_vector=self.relation_vectors_enabled,
            )
            self.metadata_store.link_paragraph_relation(paragraph_hash, result.hash_value)
            stored_relations.append(result.hash_value)

        self.metadata_store.upsert_external_memory_ref(
            external_id=external_token,
            paragraph_hash=paragraph_hash,
            source_type=source_type,
            metadata={"chat_id": chat_id, "person_ids": person_tokens},
        )
        if self._should_auto_enqueue_episode(source_type=source_type):
            self.metadata_store.enqueue_episode_pending(paragraph_hash, source=source)
        self._persist()
        for person_id in person_tokens:
            self._mark_person_active(person_id)
            self._enqueue_person_profile_refresh(person_id, reason=str(source_type or "ingest_text"))
        payload = {"stored_ids": [paragraph_hash, *stored_relations], "skipped_ids": []}
        if warnings:
            payload["warnings"] = warnings
            payload["detail"] = "vector_degraded_write"
        return payload

    async def process_episode_pending_batch(self, *, limit: int = 20, max_retry: int = 3) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store is not None
        assert self.episode_service is not None

        pending_rows = self.metadata_store.fetch_episode_pending_batch(limit=max(1, int(limit)), max_retry=max(1, int(max_retry)))
        if not pending_rows:
            return {"processed": 0, "episode_count": 0, "fallback_count": 0, "failed": 0}

        source_to_hashes: Dict[str, List[str]] = {}
        pending_hashes = [str(row.get("paragraph_hash", "") or "").strip() for row in pending_rows if str(row.get("paragraph_hash", "") or "").strip()]
        for row in pending_rows:
            paragraph_hash = str(row.get("paragraph_hash", "") or "").strip()
            source = str(row.get("source", "") or "").strip()
            if not paragraph_hash or not source:
                continue
            source_to_hashes.setdefault(source, []).append(paragraph_hash)

        if pending_hashes:
            self.metadata_store.mark_episode_pending_running(pending_hashes)

        result = await self.episode_service.process_pending_rows(pending_rows)
        done_hashes = [str(item or "").strip() for item in result.get("done_hashes", []) if str(item or "").strip()]
        failed_hashes = {
            str(hash_value or "").strip(): str(error or "").strip()
            for hash_value, error in (result.get("failed_hashes", {}) or {}).items()
            if str(hash_value or "").strip()
        }

        if done_hashes:
            self.metadata_store.mark_episode_pending_done(done_hashes)
        for hash_value, error in failed_hashes.items():
            self.metadata_store.mark_episode_pending_failed(hash_value, error)

        untouched = [hash_value for hash_value in pending_hashes if hash_value not in set(done_hashes) and hash_value not in failed_hashes]
        for hash_value in untouched:
            self.metadata_store.mark_episode_pending_failed(hash_value, "episode processing finished without explicit status")

        for source, paragraph_hashes in source_to_hashes.items():
            counts = self.metadata_store.get_episode_pending_status_counts(source)
            if counts.get("failed", 0) > 0:
                source_error = next(
                    (
                        failed_hashes.get(hash_value)
                        for hash_value in paragraph_hashes
                        if failed_hashes.get(hash_value)
                    ),
                    "episode pending source contains failed rows",
                )
                self.metadata_store.mark_episode_source_failed(source, str(source_error or "episode pending source contains failed rows"))
            elif counts.get("pending", 0) == 0 and counts.get("running", 0) == 0:
                self.metadata_store.mark_episode_source_done(source)

        self._persist()
        return {
            "processed": len(done_hashes) + len(failed_hashes),
            "episode_count": int(result.get("episode_count") or 0),
            "fallback_count": int(result.get("fallback_count") or 0),
            "failed": len(failed_hashes) + len(untouched),
            "group_count": int(result.get("group_count") or 0),
            "missing_count": int(result.get("missing_count") or 0),
        }

    async def search_memory(self, request: KernelSearchRequest) -> Dict[str, Any]:
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
        assert self.metadata_store is not None
        assert self.person_profile_service is not None

        pid = str(person_id or "").strip()
        if not pid and person_keyword:
            pid = self.person_profile_service.resolve_person_id(str(person_keyword or "").strip())

        dirty_request = self.metadata_store.get_person_profile_refresh_request(pid) if pid else None
        should_force_refresh = bool(force_refresh)
        if (
            pid
            and self._feedback_cfg_profile_refresh_enabled()
            and self._feedback_cfg_profile_force_refresh_on_read()
            and isinstance(dirty_request, dict)
            and str(dirty_request.get("status", "") or "").strip().lower() in {"pending", "running", "failed"}
        ):
            should_force_refresh = True

        profile = await self.person_profile_service.query_person_profile(
            person_id=pid,
            person_keyword=str(person_keyword or "").strip(),
            top_k=max(1, int(limit or 10)),
            force_refresh=should_force_refresh,
            source_note=source_note,
        )
        payload = profile if isinstance(profile, dict) else {"success": False, "error": "invalid profile payload"}
        if dirty_request:
            payload["feedback_refresh_request"] = dirty_request
        if should_force_refresh and dirty_request and not bool(payload.get("success")):
            payload.setdefault("error", "feedback_refresh_failed")
            payload["feedback_refresh_failed"] = True
        return payload

    def _build_person_profile_response(
        self,
        profile: Dict[str, Any],
        *,
        requested_person_id: str,
        limit: int,
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        if not bool(profile.get("success")):
            return self._empty_person_profile_response(
                person_id=str(profile.get("person_id", "") or requested_person_id),
                person_name=str(profile.get("person_name", "") or ""),
            )

        evidence: List[Dict[str, Any]] = []
        evidence_limit = max(1, int(limit or 10))
        for hash_value in profile.get("evidence_ids", [])[:evidence_limit]:
            paragraph = self.metadata_store.get_paragraph(hash_value)
            if paragraph is not None:
                evidence.append(
                    {
                        "hash": hash_value,
                        "content": str(paragraph.get("content", "") or "")[:220],
                        "metadata": paragraph.get("metadata", {}) or {},
                        "type": "paragraph",
                    }
                )
                continue

            relation = self.metadata_store.get_relation(hash_value)
            if relation is not None:
                evidence.append(
                    {
                        "hash": hash_value,
                        "content": " ".join(
                            [
                                str(relation.get("subject", "") or "").strip(),
                                str(relation.get("predicate", "") or "").strip(),
                                str(relation.get("object", "") or "").strip(),
                            ]
                        ).strip(),
                        "metadata": {
                            "confidence": relation.get("confidence"),
                            "source_paragraph": relation.get("source_paragraph"),
                        },
                        "type": "relation",
                    }
                )

        evidence = self._filter_user_visible_hits(evidence)
        text = str(profile.get("profile_text", "") or "").strip()
        traits = [line.strip("- ").strip() for line in text.splitlines() if line.strip()][:8]
        return {
            "summary": text,
            "traits": traits,
            "evidence": evidence,
            "person_id": str(profile.get("person_id", "") or requested_person_id),
            "person_name": str(profile.get("person_name", "") or ""),
            "profile_source": str(profile.get("profile_source", "") or "auto_snapshot"),
            "has_manual_override": bool(profile.get("has_manual_override", False)),
        }

    async def get_person_profile(self, *, person_id: str, chat_id: str = "", limit: int = 10) -> Dict[str, Any]:
        del chat_id
        await self.initialize()
        assert self.metadata_store is not None
        assert self.person_profile_service is not None
        self._mark_person_active(person_id)
        profile = await self._query_person_profile_with_feedback_refresh(
            person_id=person_id,
            limit=max(4, int(limit or 10)),
            source_note="sdk_memory_kernel.get_person_profile",
        )
        return self._build_person_profile_response(profile, requested_person_id=person_id, limit=limit)

    async def refresh_person_profile(self, person_id: str, limit: int = 10, *, mark_active: bool = True) -> Dict[str, Any]:
        await self.initialize()
        assert self.person_profile_service
        if mark_active:
            self._mark_person_active(person_id)
        profile = await self.person_profile_service.query_person_profile(
            person_id=person_id,
            top_k=max(4, int(limit or 10)),
            force_refresh=True,
            source_note="sdk_memory_kernel.refresh_person_profile",
        )
        return profile if isinstance(profile, dict) else {}

    async def maintain_memory(
        self,
        *,
        action: str,
        target: str = "",
        hours: Optional[float] = None,
        reason: str = "",
        limit: int = 50,
    ) -> Dict[str, Any]:
        del reason
        await self.initialize()
        assert self.metadata_store
        act = str(action or "").strip().lower()
        if act == "recycle_bin":
            items = self.metadata_store.get_deleted_relations(limit=max(1, int(limit or 50)))
            return {"success": True, "items": items, "count": len(items)}

        hashes = self._resolve_deleted_relation_hashes(target) if act == "restore" else self._resolve_relation_hashes(target)
        if not hashes:
            return {"success": False, "detail": "未命中可维护关系"}

        if act == "reinforce":
            self.metadata_store.reinforce_relations(hashes)
        elif act == "freeze":
            self.metadata_store.mark_relations_inactive(hashes)
            self._rebuild_graph_from_metadata()
        elif act == "protect":
            ttl_seconds = max(0.0, float(hours or 0.0)) * 3600.0
            self.metadata_store.protect_relations(hashes, ttl_seconds=ttl_seconds, is_pinned=ttl_seconds <= 0)
        elif act == "restore":
            restored = sum(1 for hash_value in hashes if self.metadata_store.restore_relation(hash_value))
            if restored <= 0:
                return {"success": False, "detail": "未恢复任何关系"}
            self._rebuild_graph_from_metadata()
        else:
            return {"success": False, "detail": f"不支持的维护动作: {act}"}

        self._last_maintenance_at = time.time()
        self._persist()
        return {"success": True, "detail": f"{act} {len(hashes)} 条关系"}

    async def rebuild_episodes_for_sources(self, sources: Iterable[str]) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store is not None
        assert self.episode_service is not None

        items: List[Dict[str, Any]] = []
        failures: List[Dict[str, str]] = []
        for source in self._tokens(sources):
            self.metadata_store.mark_episode_source_running(source)
            try:
                result = await self.episode_service.rebuild_source(source)
                self.metadata_store.mark_episode_source_done(source)
                items.append(result)
            except Exception as exc:
                err = str(exc)[:500]
                self.metadata_store.mark_episode_source_failed(source, err)
                failures.append({"source": source, "error": err})
        self._persist()
        return {
            "rebuilt": len(items),
            "items": items,
            "failures": failures,
            "sources": [str(item.get("source", "") or "") for item in items] or self._tokens(sources),
        }

    def memory_stats(self) -> Dict[str, Any]:
        assert self.metadata_store
        stats = self.metadata_store.get_statistics()
        episodes = self.metadata_store.query("SELECT COUNT(*) AS c FROM episodes")[0]["c"]
        profiles = self.metadata_store.query("SELECT COUNT(*) AS c FROM person_profile_snapshots")[0]["c"]
        pending = self.metadata_store.query(
            "SELECT COUNT(*) AS c FROM episode_pending_paragraphs WHERE status IN ('pending', 'running', 'failed')"
        )[0]["c"]
        backfill = self._paragraph_vector_backfill_counts()
        episode_rebuild_summary = self.metadata_store.get_episode_source_rebuild_summary()
        episode_rebuild_counts = episode_rebuild_summary.get("counts", {}) if isinstance(episode_rebuild_summary, dict) else {}
        return {
            "paragraphs": int(stats.get("paragraph_count", 0) or 0),
            "relations": int(stats.get("relation_count", 0) or 0),
            "episodes": int(episodes or 0),
            "profiles": int(profiles or 0),
            "episode_pending": int(pending or 0),
            "stale_paragraph_marks": int(stats.get("stale_paragraph_mark_count", 0) or 0),
            "profile_refresh_pending": int(stats.get("person_profile_refresh_pending_count", 0) or 0),
            "profile_refresh_failed": int(stats.get("person_profile_refresh_failed_count", 0) or 0),
            "episode_rebuild_pending": int(
                (episode_rebuild_counts.get("pending", 0) or 0)
                + (episode_rebuild_counts.get("running", 0) or 0)
                + (episode_rebuild_counts.get("failed", 0) or 0)
            ),
            "paragraph_vector_backfill_pending": int(backfill.get("pending", 0) or 0),
            "paragraph_vector_backfill_failed": int(backfill.get("failed", 0) or 0),
            "last_maintenance_at": self._last_maintenance_at,
        }

    @staticmethod
    def _vector_store_snapshot(store: Optional[VectorStore]) -> Dict[str, Any]:
        if store is None:
            return {
                "available": False,
                "dimension": 0,
                "num_vectors": 0,
                "has_data": False,
            }
        has_data = False
        try:
            has_data = bool(store.has_data())
        except Exception:
            has_data = False
        return {
            "available": True,
            "dimension": int(getattr(store, "dimension", 0) or 0),
            "num_vectors": int(getattr(store, "num_vectors", 0) or 0),
            "has_data": has_data,
        }

    def _vector_pools_status(self) -> Dict[str, Any]:
        configured_mode = self._vector_pool_mode()
        ready = self._dual_vector_pools_enabled()
        return {
            "configured_mode": configured_mode,
            "effective_mode": "dual" if configured_mode == "dual" and ready else "single",
            "ready": ready,
            "single_pool": self._vector_store_snapshot(self.vector_store),
            "paragraph_pool": self._vector_store_snapshot(self.paragraph_vector_store),
            "graph_pool": self._vector_store_snapshot(self.graph_vector_store),
            "ready_manifest": str(self._dual_vector_ready_manifest_path()),
            "auto_migration": dict(self._dual_vector_auto_migration_status),
        }

    def _should_start_dual_vector_auto_migration(self) -> bool:
        return (
            self._dual_vector_pools_config_enabled()
            and not self._dual_vector_pools_enabled()
            and not self._dual_vector_auto_migration_attempted
            and not self._background_stopping
        )

    def _normalize_dual_vector_auto_migration_progress(
        self,
        progress: Optional[Dict[str, Any]] = None,
        *,
        now: Optional[float] = None,
        explicit_processed: bool = False,
        completed: bool = False,
        success: bool = False,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = dict(progress or {})
        now_ts = float(now if now is not None else time.time())
        started_at = self._dual_vector_auto_migration_status.get("started_at")
        elapsed_seconds = 0.0
        if isinstance(started_at, (int, float)):
            elapsed_seconds = max(0.0, now_ts - float(started_at))

        def _coerce_non_negative_int(value: Any, default: int = 0) -> int:
            try:
                number = int(float(value))
            except (TypeError, ValueError):
                return default
            return max(0, number)

        total = _coerce_non_negative_int(payload.get("total"), 0)
        if total <= 0:
            counts = payload.get("counts")
            if isinstance(counts, dict):
                total = sum(
                    _coerce_non_negative_int(counts.get(key), 0)
                    for key in ("paragraphs", "entities", "relations")
                )

        processed_keys = (
            "paragraph_done",
            "paragraph_failed",
            "entity_done",
            "entity_failed",
            "relation_done",
            "relation_failed",
        )
        if explicit_processed:
            processed = _coerce_non_negative_int(payload.get("processed"), 0)
        elif any(key in payload for key in processed_keys):
            processed = sum(_coerce_non_negative_int(payload.get(key), 0) for key in processed_keys)
        else:
            processed = _coerce_non_negative_int(payload.get("processed"), 0)
        if total > 0:
            processed = min(processed, total)

        if completed and success:
            if total > 0:
                processed = total
            percent = 100.0
        elif total > 0:
            percent = min(99.5, max(0.0, (float(processed) / float(total)) * 100.0))
        else:
            percent = 0.0

        estimated_remaining_seconds: Optional[int] = None
        if not completed and total > 0 and 0 < processed < total and elapsed_seconds > 0.0:
            rate = float(processed) / elapsed_seconds
            if rate > 0.0:
                remaining = (float(total) - float(processed)) / rate
                estimated_remaining_seconds = max(0, int(remaining + 0.999))

        payload.update(
            {
                "total": int(total),
                "processed": int(processed),
                "percent": round(percent, 2),
                "elapsed_seconds": round(elapsed_seconds, 3),
                "estimated_remaining_seconds": estimated_remaining_seconds,
            }
        )
        return payload

    def _update_dual_vector_auto_migration_stage(self, stage: str, **progress: Any) -> None:
        if not bool(self._dual_vector_auto_migration_status.get("running", False)):
            return
        now_ts = time.time()
        explicit_processed = "processed" in progress
        payload = dict(self._dual_vector_auto_migration_status.get("progress") or {})
        payload.update(progress)
        payload = self._normalize_dual_vector_auto_migration_progress(
            payload,
            now=now_ts,
            explicit_processed=explicit_processed,
        )
        self._dual_vector_auto_migration_status.update(
            {
                "stage": str(stage or "unknown"),
                "progress": payload,
                "updated_at": now_ts,
            }
        )

    async def memory_graph_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store is not None
        assert self.graph_store is not None

        act = str(action or "").strip().lower()
        if act == "get_graph":
            return {"success": True, **self._serialize_graph(limit=max(1, int(kwargs.get("limit", 200) or 200)))}
        if act == "search":
            return self._search_graph(
                query=str(kwargs.get("query", "") or "").strip(),
                limit=max(1, min(200, int(kwargs.get("limit", 50) or 50))),
            )
        if act == "node_detail":
            detail = self._build_graph_node_detail(
                node_id=str(kwargs.get("node_id", "") or kwargs.get("node", "") or "").strip(),
                relation_limit=max(1, int(kwargs.get("relation_limit", 20) or 20)),
                paragraph_limit=max(1, int(kwargs.get("paragraph_limit", 20) or 20)),
                evidence_node_limit=max(12, int(kwargs.get("evidence_node_limit", 80) or 80)),
            )
            return detail
        if act == "edge_detail":
            detail = self._build_graph_edge_detail(
                source=str(kwargs.get("source", "") or "").strip(),
                target=str(kwargs.get("target", "") or kwargs.get("object", "") or "").strip(),
                paragraph_limit=max(1, int(kwargs.get("paragraph_limit", 20) or 20)),
                evidence_node_limit=max(12, int(kwargs.get("evidence_node_limit", 80) or 80)),
            )
            return detail

        if act == "create_node":
            name = str(kwargs.get("name", "") or kwargs.get("node", "") or "").strip()
            if not name:
                return {"success": False, "error": "node name 不能为空"}
            entity_hash = self.metadata_store.add_entity(name=name, metadata=kwargs.get("metadata") or {})
            self._rebuild_graph_from_metadata()
            self._persist()
            return {"success": True, "node": {"name": name, "hash": entity_hash}}

        if act == "delete_node":
            name = str(kwargs.get("name", "") or kwargs.get("node", "") or kwargs.get("hash_or_name", "") or "").strip()
            if not name:
                return {"success": False, "error": "node name 不能为空"}
            result = await self._execute_delete_action(
                mode="entity",
                selector={"query": name},
                requested_by=str(kwargs.get("requested_by", "") or "memory_graph_admin"),
                reason=str(kwargs.get("reason", "") or "graph_delete_node"),
            )
            return {
                **result,
                "deleted": bool(result.get("deleted_entity_count", 0) or result.get("deleted_count", 0)),
                "node": name,
            }

        if act == "rename_node":
            old_name = str(kwargs.get("name", "") or kwargs.get("old_name", "") or kwargs.get("node", "") or "").strip()
            new_name = str(kwargs.get("new_name", "") or kwargs.get("target_name", "") or "").strip()
            return self._rename_node(old_name, new_name)

        if act == "create_edge":
            subject = str(kwargs.get("subject", "") or kwargs.get("source", "") or "").strip()
            predicate = str(kwargs.get("predicate", "") or kwargs.get("label", "") or "").strip()
            obj = str(kwargs.get("object", "") or kwargs.get("target", "") or "").strip()
            if not all([subject, predicate, obj]):
                return {"success": False, "error": "subject/predicate/object 不能为空"}
            if self.relation_write_service is not None:
                result = await self.relation_write_service.upsert_relation_with_vector(
                    subject=subject,
                    predicate=predicate,
                    obj=obj,
                    confidence=float(kwargs.get("confidence", 1.0) or 1.0),
                    source_paragraph=str(kwargs.get("source_paragraph", "") or "") or None,
                    metadata=kwargs.get("metadata") or {},
                    write_vector=self.relation_vectors_enabled,
                )
                relation_hash = result.hash_value
            else:
                relation_hash = self.metadata_store.add_relation(
                    subject=subject,
                    predicate=predicate,
                    obj=obj,
                    confidence=float(kwargs.get("confidence", 1.0) or 1.0),
                    source_paragraph=kwargs.get("source_paragraph"),
                    metadata=kwargs.get("metadata") or {},
                )
            self._rebuild_graph_from_metadata()
            self._persist()
            return {
                "success": True,
                "edge": {
                    "hash": relation_hash,
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "weight": float(kwargs.get("confidence", 1.0) or 1.0),
                },
            }

        if act == "delete_edge":
            relation_hash = str(kwargs.get("hash", "") or kwargs.get("relation_hash", "") or "").strip()
            if relation_hash:
                result = await self._execute_delete_action(
                    mode="relation",
                    selector={"query": relation_hash},
                    requested_by=str(kwargs.get("requested_by", "") or "memory_graph_admin"),
                    reason=str(kwargs.get("reason", "") or "graph_delete_edge"),
                )
                return {
                    **result,
                    "deleted": int(result.get("deleted_relation_count", 0) or result.get("deleted_count", 0)),
                    "hash": relation_hash,
                }

            subject = str(kwargs.get("subject", "") or kwargs.get("source", "") or "").strip()
            obj = str(kwargs.get("object", "") or kwargs.get("target", "") or "").strip()
            deleted_hashes = [
                str(row.get("hash", "") or "")
                for row in self.metadata_store.get_relations(subject=subject)
                if str(row.get("object", "") or "").strip() == obj
            ]
            result = await self._execute_delete_action(
                mode="relation",
                selector={"hashes": deleted_hashes, "subject": subject, "object": obj},
                requested_by=str(kwargs.get("requested_by", "") or "memory_graph_admin"),
                reason=str(kwargs.get("reason", "") or "graph_delete_edge"),
            )
            return {
                **result,
                "deleted": int(result.get("deleted_relation_count", 0) or result.get("deleted_count", 0)),
                "subject": subject,
                "object": obj,
            }

        if act == "update_edge_weight":
            return self._update_edge_weight(
                relation_hash=str(kwargs.get("hash", "") or kwargs.get("relation_hash", "") or "").strip(),
                subject=str(kwargs.get("subject", "") or kwargs.get("source", "") or "").strip(),
                obj=str(kwargs.get("object", "") or kwargs.get("target", "") or "").strip(),
                weight=float(kwargs.get("weight", kwargs.get("confidence", 1.0)) or 1.0),
            )

        return {"success": False, "error": f"不支持的 graph action: {act}"}

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

    async def memory_episode_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store

        act = str(action or "").strip().lower()
        if act in {"query", "list"}:
            items = self.metadata_store.query_episodes(
                query=str(kwargs.get("query", "") or "").strip(),
                time_from=self._optional_float(kwargs.get("time_start", kwargs.get("time_from"))),
                time_to=self._optional_float(kwargs.get("time_end", kwargs.get("time_to"))),
                person=str(kwargs.get("person_id", "") or kwargs.get("person", "") or "").strip() or None,
                source=str(kwargs.get("source", "") or "").strip() or None,
                limit=max(1, int(kwargs.get("limit", 20) or 20)),
            )
            return {"success": True, "items": items, "count": len(items)}

        if act == "get":
            episode_id = str(kwargs.get("episode_id", "") or "").strip()
            if not episode_id:
                return {"success": False, "error": "episode_id 不能为空"}
            episode = self.metadata_store.get_episode_by_id(episode_id)
            if episode is None:
                return {"success": False, "error": "episode 不存在"}
            episode["paragraphs"] = self.metadata_store.get_episode_paragraphs(
                episode_id,
                limit=max(1, int(kwargs.get("paragraph_limit", 100) or 100)),
            )
            return {"success": True, "episode": episode}

        if act == "status":
            summary = self.metadata_store.get_episode_source_rebuild_summary(
                failed_limit=max(1, int(kwargs.get("limit", 20) or 20))
            )
            summary["pending_queue"] = self.metadata_store.query(
                "SELECT COUNT(*) AS c FROM episode_pending_paragraphs WHERE status IN ('pending', 'running', 'failed')"
            )[0]["c"]
            return {"success": True, **summary}

        if act == "rebuild":
            sources = self._tokens(kwargs.get("sources"))
            if not sources:
                source = str(kwargs.get("source", "") or "").strip()
                if source:
                    sources = [source]
            if not sources and bool(kwargs.get("all", False)):
                sources = self.metadata_store.list_episode_sources_for_rebuild()
                if not sources:
                    sources = [str(row.get("source", "") or "").strip() for row in self.metadata_store.get_all_sources()]
            if not sources:
                return {"success": False, "error": "未提供可重建的 source"}
            result = await self.rebuild_episodes_for_sources(sources)
            return {"success": len(result.get("failures", [])) == 0, **result}

        if act == "process_pending":
            result = await self.process_episode_pending_batch(
                limit=max(1, int(kwargs.get("limit", 20) or 20)),
                max_retry=max(1, int(kwargs.get("max_retry", 3) or 3)),
            )
            return {"success": True, **result}

        return {"success": False, "error": f"不支持的 episode action: {act}"}

    async def memory_profile_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store is not None
        assert self.person_profile_service is not None

        act = str(action or "").strip().lower()
        if act == "query":
            profile = await self._query_person_profile_with_feedback_refresh(
                person_id=str(kwargs.get("person_id", "") or "").strip(),
                person_keyword=str(kwargs.get("person_keyword", "") or kwargs.get("keyword", "") or "").strip(),
                limit=max(1, int(kwargs.get("limit", kwargs.get("top_k", 12)) or 12)),
                force_refresh=bool(kwargs.get("force_refresh", False)),
                source_note="sdk_memory_kernel.memory_profile_admin.query",
            )
            return profile if isinstance(profile, dict) else {"success": False, "error": "invalid profile payload"}

        if act == "evidence":
            return await self._profile_evidence_admin(
                person_id=str(kwargs.get("person_id", "") or "").strip(),
                person_keyword=str(kwargs.get("person_keyword", "") or kwargs.get("keyword", "") or "").strip(),
                limit=max(1, int(kwargs.get("limit", kwargs.get("top_k", 12)) or 12)),
                force_refresh=bool(kwargs.get("force_refresh", False)),
            )

        if act == "correct_evidence":
            return await self._profile_correct_evidence_admin(
                person_id=str(kwargs.get("person_id", "") or "").strip(),
                person_keyword=str(kwargs.get("person_keyword", "") or kwargs.get("keyword", "") or "").strip(),
                evidence_type=str(kwargs.get("evidence_type", "") or "").strip(),
                hash_value=str(kwargs.get("hash", "") or kwargs.get("hash_value", "") or "").strip(),
                requested_by=str(kwargs.get("requested_by", "") or "webui").strip(),
                reason=str(kwargs.get("reason", "") or "profile_evidence_correction").strip(),
                refresh=bool(kwargs.get("refresh", True)),
                limit=max(1, int(kwargs.get("limit", kwargs.get("top_k", 12)) or 12)),
            )

        if act == "status":
            summary = self.metadata_store.get_person_profile_refresh_summary(
                failed_limit=max(1, int(kwargs.get("limit", 20) or 20))
            )
            return {"success": True, **summary}

        if act == "process_pending":
            result = await self._process_feedback_profile_refresh_batch(
                limit=max(1, int(kwargs.get("limit", self._feedback_cfg_reconcile_batch_size()) or self._feedback_cfg_reconcile_batch_size()))
            )
            return {"success": True, **result}

        if act == "list":
            limit = max(1, int(kwargs.get("limit", 50) or 50))
            rows = self.metadata_store.query(
                """
                SELECT s.person_id, s.profile_version, s.profile_text, s.updated_at, s.expires_at, s.source_note
                FROM person_profile_snapshots s
                JOIN (
                    SELECT person_id, MAX(profile_version) AS max_version
                    FROM person_profile_snapshots
                    GROUP BY person_id
                ) latest
                  ON latest.person_id = s.person_id
                 AND latest.max_version = s.profile_version
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            items = []
            for row in rows:
                person_id = str(row.get("person_id", "") or "").strip()
                override = self.metadata_store.get_person_profile_override(person_id)
                items.append(
                    {
                        "person_id": person_id,
                        "profile_version": int(row.get("profile_version", 0) or 0),
                        "profile_text": str(row.get("profile_text", "") or ""),
                        "updated_at": row.get("updated_at"),
                        "expires_at": row.get("expires_at"),
                        "source_note": str(row.get("source_note", "") or ""),
                        "has_manual_override": bool(override),
                        "manual_override": override,
                    }
                )
            return {"success": True, "items": items, "count": len(items)}

        if act == "set_override":
            person_id = str(kwargs.get("person_id", "") or "").strip()
            override = self.metadata_store.set_person_profile_override(
                person_id=person_id,
                override_text=str(kwargs.get("override_text", "") or kwargs.get("text", "") or ""),
                updated_by=str(kwargs.get("updated_by", "") or ""),
                source=str(kwargs.get("source", "") or "memory_profile_admin"),
            )
            return {"success": True, "override": override}

        if act == "delete_override":
            person_id = str(kwargs.get("person_id", "") or "").strip()
            deleted = self.metadata_store.delete_person_profile_override(person_id)
            return {"success": bool(deleted), "deleted": bool(deleted), "person_id": person_id}

        return {"success": False, "error": f"不支持的 profile action: {act}"}

    async def memory_feedback_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store is not None

        act = str(action or "").strip().lower()
        if act == "list":
            items = self.metadata_store.list_feedback_tasks(
                limit=max(1, int(kwargs.get("limit", 50) or 50)),
                statuses=self._tokens(kwargs.get("status") or kwargs.get("statuses")),
                rollback_statuses=self._tokens(kwargs.get("rollback_status") or kwargs.get("rollback_statuses")),
                query=str(kwargs.get("query", "") or "").strip(),
            )
            return {
                "success": True,
                "items": [self._build_feedback_task_summary(task) for task in items],
                "count": len(items),
            }

        if act == "get":
            task = self.metadata_store.get_feedback_task_by_id(int(kwargs.get("task_id", 0) or 0))
            if task is None:
                return {"success": False, "error": "反馈纠错任务不存在"}
            return {"success": True, "task": self._build_feedback_task_detail(task)}

        if act == "rollback":
            return await self._rollback_feedback_task(
                task_id=int(kwargs.get("task_id", 0) or 0),
                requested_by=str(kwargs.get("requested_by", "") or "").strip(),
                reason=str(kwargs.get("reason", "") or "").strip(),
            )

        return {"success": False, "error": f"不支持的 feedback action: {act}"}

    async def memory_runtime_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        act = str(action or "").strip().lower()

        if act == "save":
            self._persist()
            return {"success": True, "saved": True, "data_dir": str(self.data_dir)}

        if act == "get_config":
            degraded = self._embedding_degraded_snapshot()
            backfill_counts = self._paragraph_vector_backfill_counts()
            rebuild_status = self._vector_rebuild_status()
            vector_pools_status = self._vector_pools_status()
            return {
                "success": True,
                "config": self.config,
                "data_dir": str(self.data_dir),
                "embedding_dimension": int(self.embedding_dimension),
                "stored_vector_dimension": int(rebuild_status["stored_vector_dimension"]),
                "vector_rebuild_required": bool(rebuild_status["vector_rebuild_required"]),
                "vector_rebuild_message": str(rebuild_status["message"]),
                "embedding_fingerprint": rebuild_status.get("embedding_fingerprint") or {},
                "stored_embedding_fingerprint": rebuild_status.get("stored_embedding_fingerprint") or {},
                "embedding_fingerprint_status": str(rebuild_status.get("embedding_fingerprint_status") or "unknown"),
                "auto_save": bool(self._cfg("advanced.enable_auto_save", True)),
                "relation_vectors_enabled": bool(self.relation_vectors_enabled),
                "vector_pools": vector_pools_status,
                "vector_pools_ready": bool(vector_pools_status.get("ready", False)),
                "vector_pools_effective_mode": str(vector_pools_status.get("effective_mode", "single")),
                "runtime_ready": self.is_runtime_ready(),
                "embedding_degraded": bool(degraded.get("active", False)),
                "embedding_degraded_reason": str(degraded.get("reason", "") or ""),
                "embedding_degraded_since": degraded.get("since"),
                "embedding_last_check": degraded.get("last_check"),
                "paragraph_vector_backfill_pending": int(backfill_counts.get("pending", 0) or 0),
                "paragraph_vector_backfill_running": int(backfill_counts.get("running", 0) or 0),
                "paragraph_vector_backfill_failed": int(backfill_counts.get("failed", 0) or 0),
                "paragraph_vector_backfill_done": int(backfill_counts.get("done", 0) or 0),
            }

        if act in {"self_check", "refresh_self_check"}:
            report = await self._refresh_runtime_self_check(
                sample_text=str(kwargs.get("sample_text", "") or "A_Memorix runtime self check")
            )
            checked_at = float(report.get("checked_at") or time.time())
            dimension_mismatch = self._apply_self_check_dimension_result(report)
            if dimension_mismatch:
                self._set_embedding_degraded(active=True, reason=dimension_mismatch, checked_at=checked_at)
            elif bool(report.get("ok", False)):
                self._set_embedding_degraded(active=False, checked_at=checked_at)
            elif self._embedding_fallback_enabled():
                self._set_embedding_degraded(
                    active=True,
                    reason=str(report.get("message", "runtime self-check failed") or "runtime self-check failed"),
                    checked_at=checked_at,
                )
            return {"success": bool(report.get("ok", False)), "report": report}

        if act == "set_auto_save":
            enabled = bool(kwargs.get("enabled", False))
            self._set_cfg("advanced.enable_auto_save", enabled)
            return {"success": True, "auto_save": enabled}

        if act == "recover_embedding":
            result = await self._recover_embedding_once(
                sample_text=str(kwargs.get("sample_text", "") or "A_Memorix runtime self check")
            )
            result["embedding_degraded"] = self._is_embedding_degraded()
            result["embedding_state"] = self._embedding_degraded_snapshot()
            result["backfill_counts"] = self._paragraph_vector_backfill_counts()
            return result

        if act == "rebuild_all_vectors":
            include_relations = kwargs.get("include_relations")
            result = await self._rebuild_all_vectors(
                batch_size=self._optional_int(kwargs.get("batch_size")),
                include_relations=include_relations if isinstance(include_relations, bool) else None,
                dry_run=bool(kwargs.get("dry_run", False)),
            )
            result["embedding_degraded"] = self._is_embedding_degraded()
            result["backfill_counts"] = self._paragraph_vector_backfill_counts()
            return result

        if act == "paragraph_backfill_once":
            result = await self._run_paragraph_backfill_once(
                limit=self._optional_int(kwargs.get("limit")),
                max_retry=self._optional_int(kwargs.get("max_retry")),
                trigger="manual",
            )
            result["embedding_degraded"] = self._is_embedding_degraded()
            result["backfill_counts"] = self._paragraph_vector_backfill_counts()
            return result

        return {"success": False, "error": f"不支持的 runtime action: {act}"}

    async def memory_import_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        manager = self.import_task_manager
        if manager is None:
            return {"success": False, "error": "import manager 未初始化"}

        act = str(action or "").strip().lower()
        if act in {"settings", "get_settings", "get_guide"}:
            return {"success": True, "settings": await manager.get_runtime_settings()}
        if act in {"path_aliases", "get_path_aliases"}:
            return {"success": True, "path_aliases": manager.get_path_aliases()}
        if act in {"resolve_path", "resolve"}:
            return await manager.resolve_path_request(kwargs)
        if act == "create_upload":
            task = await manager.create_upload_task(
                list(kwargs.get("staged_files") or kwargs.get("files") or kwargs.get("uploads") or []),
                kwargs,
            )
            return {"success": True, "task": task}
        if act == "create_paste":
            return {"success": True, "task": await manager.create_paste_task(kwargs)}
        if act == "create_raw_scan":
            return {"success": True, "task": await manager.create_raw_scan_task(kwargs)}
        if act == "create_lpmm_openie":
            return {"success": True, "task": await manager.create_lpmm_openie_task(kwargs)}
        if act == "create_lpmm_convert":
            return {"success": True, "task": await manager.create_lpmm_convert_task(kwargs)}
        if act == "create_temporal_backfill":
            return {"success": True, "task": await manager.create_temporal_backfill_task(kwargs)}
        if act == "create_maibot_migration":
            return {"success": True, "task": await manager.create_maibot_migration_task(kwargs)}
        if act == "list":
            items = await manager.list_tasks(limit=max(1, int(kwargs.get("limit", 50) or 50)))
            return {"success": True, "items": items, "count": len(items)}
        if act == "get":
            task = await manager.get_task(
                str(kwargs.get("task_id", "") or ""),
                include_chunks=bool(kwargs.get("include_chunks", False)),
            )
            return {"success": task is not None, "task": task, "error": "" if task is not None else "任务不存在"}
        if act in {"chunks", "get_chunks"}:
            payload = await manager.get_chunks(
                str(kwargs.get("task_id", "") or ""),
                str(kwargs.get("file_id", "") or ""),
                offset=max(0, int(kwargs.get("offset", 0) or 0)),
                limit=max(1, int(kwargs.get("limit", 50) or 50)),
            )
            return {"success": payload is not None, **(payload or {}), "error": "" if payload is not None else "任务或文件不存在"}
        if act == "cancel":
            task = await manager.cancel_task(str(kwargs.get("task_id", "") or ""))
            return {"success": task is not None, "task": task, "error": "" if task is not None else "任务不存在"}
        if act == "retry_failed":
            overrides = kwargs.get("overrides") if isinstance(kwargs.get("overrides"), dict) else kwargs
            task = await manager.retry_failed(str(kwargs.get("task_id", "") or ""), overrides=overrides)
            return {"success": task is not None, "task": task, "error": "" if task is not None else "任务不存在"}
        return {"success": False, "error": f"不支持的 import action: {act}"}

    async def memory_tuning_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        manager = self.retrieval_tuning_manager
        if manager is None:
            return {"success": False, "error": "tuning manager 未初始化"}

        act = str(action or "").strip().lower()
        if act in {"settings", "get_settings"}:
            return {"success": True, "settings": manager.get_runtime_settings()}
        if act == "get_profile":
            profile = manager.get_profile_snapshot()
            persistable_profile = manager.get_persistable_profile(profile)
            return {
                "success": True,
                "profile": profile,
                "runtime_profile": profile,
                "persistable_profile": persistable_profile,
                "toml": manager.export_toml_snippet(persistable_profile),
            }
        if act == "apply_profile":
            profile_raw = kwargs.get("profile")
            if isinstance(profile_raw, dict):
                profile_payload: Dict[str, Any] = dict(profile_raw)
            else:
                profile_payload = {
                    key: value
                    for key, value in kwargs.items()
                    if key not in {"reason", "profile"}
                }
            return {
                "success": True,
                **await manager.apply_profile(
                    profile_payload,
                    reason=str(kwargs.get("reason", "manual") or "manual"),
                    validate=bool(kwargs.get("validate", True)),
                ),
            }
        if act == "rollback_profile":
            return {"success": True, **await manager.rollback_profile()}
        if act == "export_profile":
            profile = manager.get_profile_snapshot()
            persistable_profile = manager.get_persistable_profile(profile)
            return {
                "success": True,
                "profile": profile,
                "runtime_profile": profile,
                "persistable_profile": persistable_profile,
                "toml": manager.export_toml_snippet(persistable_profile),
            }
        if act == "create_task":
            payload = kwargs.get("payload") if isinstance(kwargs.get("payload"), dict) else kwargs
            return {"success": True, "task": await manager.create_task(payload)}
        if act == "list_tasks":
            items = await manager.list_tasks(limit=max(1, int(kwargs.get("limit", 50) or 50)))
            return {"success": True, "items": items, "count": len(items)}
        if act == "get_task":
            task = await manager.get_task(
                str(kwargs.get("task_id", "") or ""),
                include_rounds=bool(kwargs.get("include_rounds", False)),
            )
            return {"success": task is not None, "task": task, "error": "" if task is not None else "任务不存在"}
        if act == "get_rounds":
            payload = await manager.get_rounds(
                str(kwargs.get("task_id", "") or ""),
                offset=max(0, int(kwargs.get("offset", 0) or 0)),
                limit=max(1, int(kwargs.get("limit", 50) or 50)),
            )
            return {"success": payload is not None, **(payload or {}), "error": "" if payload is not None else "任务不存在"}
        if act == "cancel":
            task = await manager.cancel_task(str(kwargs.get("task_id", "") or ""))
            return {"success": task is not None, "task": task, "error": "" if task is not None else "任务不存在"}
        if act == "apply_best":
            return {
                "success": True,
                **await manager.apply_best(
                    str(kwargs.get("task_id", "") or ""),
                    validate=bool(kwargs.get("validate", True)),
                ),
            }
        if act == "get_report":
            report = await manager.get_report(str(kwargs.get("task_id", "") or ""), fmt=str(kwargs.get("format", "md") or "md"))
            return {"success": report is not None, "report": report, "error": "" if report is not None else "任务不存在"}
        return {"success": False, "error": f"不支持的 tuning action: {act}"}

    async def memory_v5_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store

        act = str(action or "").strip().lower()
        target = str(kwargs.get("target", "") or kwargs.get("query", "") or "").strip()
        reason = str(kwargs.get("reason", "") or "").strip()
        updated_by = str(kwargs.get("updated_by", "") or kwargs.get("requested_by", "") or "").strip()
        limit = max(1, int(kwargs.get("limit", 50) or 50))

        if act == "recycle_bin":
            items = self.metadata_store.get_deleted_relations(limit=limit)
            return {"success": True, "items": items, "count": len(items)}

        if act == "status":
            return self._memory_v5_status(target=target, limit=limit)

        if act == "restore":
            hashes = self._resolve_deleted_relation_hashes(target)
            if not hashes:
                return {"success": False, "error": "未命中可恢复关系"}
            result = await self._restore_relation_hashes(hashes)
            operation = self.metadata_store.record_v5_operation(
                action=act,
                target=target,
                resolved_hashes=hashes,
                reason=reason,
                updated_by=updated_by,
                result=result,
            )
            return {"success": bool(result.get("restored_count", 0) > 0), "operation": operation, **result}

        hashes = self._resolve_relation_hashes(target)
        if not hashes:
            return {"success": False, "error": "未命中可维护关系"}

        result = self._apply_v5_relation_action(
            action=act,
            hashes=hashes,
            strength=float(kwargs.get("strength", 1.0) or 1.0),
        )
        operation = self.metadata_store.record_v5_operation(
            action=act,
            target=target,
            resolved_hashes=hashes,
            reason=reason,
            updated_by=updated_by,
            result=result,
        )
        return {"success": bool(result.get("success", False)), "operation": operation, **result}

    async def memory_delete_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        act = str(action or "").strip().lower()
        mode = str(kwargs.get("mode", "") or "").strip().lower()
        selector = kwargs.get("selector")
        if selector is None:
            selector = {
                key: value
                for key, value in kwargs.items()
                if key
                not in {
                    "action",
                    "mode",
                    "dry_run",
                    "cascade",
                    "operation_id",
                    "reason",
                    "requested_by",
                }
            }
        reason = str(kwargs.get("reason", "") or "").strip()
        requested_by = str(kwargs.get("requested_by", "") or "").strip()

        if act == "preview":
            return await self._preview_delete_action(mode=mode, selector=selector)
        if act == "execute":
            result = await self._execute_delete_action(
                mode=mode,
                selector=selector,
                requested_by=requested_by,
                reason=reason,
            )
            await self._invalidate_import_manifest_for_sources(result)
            return result
        if act == "restore":
            return await self._restore_delete_action(
                mode=mode,
                selector=selector,
                operation_id=str(kwargs.get("operation_id", "") or "").strip(),
                requested_by=requested_by,
                reason=reason,
            )
        if act == "get_operation":
            operation = self.metadata_store.get_delete_operation(str(kwargs.get("operation_id", "") or "").strip())
            return {"success": operation is not None, "operation": operation, "error": "" if operation is not None else "operation 不存在"}
        if act == "list_operations":
            items = self.metadata_store.list_delete_operations(
                limit=max(1, int(kwargs.get("limit", 50) or 50)),
                mode=mode,
            )
            return {"success": True, "items": items, "count": len(items)}
        if act == "purge":
            return await self._purge_deleted_memory(
                grace_hours=self._optional_float(kwargs.get("grace_hours")),
                limit=max(1, int(kwargs.get("limit", 1000) or 1000)),
            )
        return {"success": False, "error": f"不支持的 delete action: {act}"}

    async def memory_correction_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store is not None

        act = str(action or "").strip().lower()
        if act in {"preview", "plan"}:
            return await self._preview_fuzzy_modify_action(
                request_text=str(kwargs.get("request_text", "") or kwargs.get("text", "") or "").strip(),
                scope=str(kwargs.get("scope", "") or "person_profile").strip(),
                person_id=str(kwargs.get("person_id", "") or "").strip(),
                person_keyword=str(kwargs.get("person_keyword", "") or kwargs.get("keyword", "") or "").strip(),
                chat_id=str(kwargs.get("chat_id", "") or "").strip(),
                limit=max(1, int(kwargs.get("limit", self._fuzzy_modify_cfg_candidate_limit()) or self._fuzzy_modify_cfg_candidate_limit())),
                requested_by=str(kwargs.get("requested_by", "") or "webui").strip(),
                reason=str(kwargs.get("reason", "") or "").strip(),
            )
        if act == "execute":
            return await self._execute_fuzzy_modify_action(
                plan_id=str(kwargs.get("plan_id", "") or "").strip(),
                confirmed=bool(kwargs.get("confirmed", False)),
                requested_by=str(kwargs.get("requested_by", "") or "webui").strip(),
                reason=str(kwargs.get("reason", "") or "").strip(),
            )
        if act == "get":
            plan = self.metadata_store.get_fuzzy_modify_plan(str(kwargs.get("plan_id", "") or "").strip())
            return {"success": plan is not None, "plan": plan, "error": "" if plan is not None else "修改计划不存在"}
        if act == "list":
            raw_statuses = kwargs.get("statuses")
            if raw_statuses is None:
                raw_statuses = kwargs.get("status")
            statuses = self._tokens([raw_statuses] if isinstance(raw_statuses, str) else raw_statuses)
            items = self.metadata_store.list_fuzzy_modify_plans(
                limit=max(1, int(kwargs.get("limit", 50) or 50)),
                statuses=statuses,
                scope=str(kwargs.get("scope", "") or "").strip(),
            )
            return {"success": True, "items": items, "count": len(items)}
        if act == "rollback":
            return await self._rollback_fuzzy_modify_action(
                plan_id=str(kwargs.get("plan_id", "") or "").strip(),
                requested_by=str(kwargs.get("requested_by", "") or "webui").strip(),
                reason=str(kwargs.get("reason", "") or "").strip(),
            )
        return {"success": False, "error": f"不支持的记忆修正操作: {act}"}

    async def memory_fuzzy_modify_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        return await self.memory_correction_admin(action=action, **kwargs)

    def get_import_task_manager(self) -> Optional[ImportTaskManager]:
        return self.import_task_manager

    def get_retrieval_tuning_manager(self) -> Optional[RetrievalTuningManager]:
        return self.retrieval_tuning_manager

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

    def _persist(self, *, force_vectors: bool = False) -> None:
        rebuild_required = False if force_vectors else bool(
            self._vector_rebuild_status().get("vector_rebuild_required", False)
        )
        if self.vector_store is not None and not self._dual_vector_pools_enabled():
            if rebuild_required:
                logger.debug("检测到向量库需要重建，跳过向量库持久化以保留重建提示")
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

    async def _start_background_tasks(self) -> None:
        async with self._background_lock:
            self._background_stopping = False
            self._ensure_background_task("auto_save", self._auto_save_loop)
            self._ensure_background_task("episode_pending", self._episode_pending_loop)
            self._ensure_background_task("embedding_probe", self._embedding_probe_loop)
            self._ensure_background_task("paragraph_vector_backfill", self._paragraph_vector_backfill_loop)
            self._ensure_background_task("memory_maintenance", self._memory_maintenance_loop)
            self._ensure_background_task("person_profile_refresh", self._person_profile_refresh_loop)
            self._ensure_background_task("person_profile_refresh_queue", self._person_profile_refresh_queue_loop)
            self._ensure_background_task("feedback_correction", self._feedback_correction_loop)
            self._ensure_background_task("feedback_correction_reconcile", self._feedback_correction_reconcile_loop)
            if self._should_start_dual_vector_auto_migration():
                self._ensure_background_task("dual_vector_auto_migration", self._dual_vector_auto_migration_loop)

    def _ensure_background_task(
        self,
        name: str,
        factory: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        task = self._background_tasks.get(name)
        if task is not None and not task.done():
            return
        self._background_tasks[name] = asyncio.create_task(factory(), name=f"A_Memorix.{name}")

    async def _sleep_background(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, float(seconds or 0.0)))

    async def _dual_vector_auto_migration_loop(self) -> None:
        if not self._should_start_dual_vector_auto_migration():
            return

        self._dual_vector_auto_migration_attempted = True
        started_at = time.time()
        self._dual_vector_auto_migration_status.update(
            {
                "running": True,
                "attempted": True,
                "success": False,
                "stage": "initial_delay",
                "progress": self._normalize_dual_vector_auto_migration_progress(
                    {"total": 0, "processed": 0},
                    now=started_at,
                    explicit_processed=True,
                ),
                "last_error": "",
                "started_at": started_at,
                "finished_at": None,
                "updated_at": started_at,
            }
        )
        try:
            await self._sleep_background(DUAL_VECTOR_AUTO_MIGRATION_INITIAL_DELAY_SECONDS)
            if self._background_stopping or self._dual_vector_pools_enabled():
                finished_at = time.time()
                success = self._dual_vector_pools_enabled()
                progress = self._normalize_dual_vector_auto_migration_progress(
                    self._dual_vector_auto_migration_status.get("progress"),
                    now=finished_at,
                    completed=True,
                    success=success,
                )
                self._dual_vector_auto_migration_status.update(
                    {
                        "running": False,
                        "success": success,
                        "stage": "skipped",
                        "progress": progress,
                        "finished_at": finished_at,
                        "updated_at": finished_at,
                    }
                )
                return

            retry_delays = [0.0, *DUAL_VECTOR_AUTO_MIGRATION_LOCK_RETRY_DELAYS_SECONDS]
            result: Dict[str, Any] = {}
            for index, delay in enumerate(retry_delays):
                if self._background_stopping or self._dual_vector_pools_enabled():
                    break
                if delay > 0:
                    self._update_dual_vector_auto_migration_stage("retry_delay", retry_index=index, delay_seconds=delay)
                    await self._sleep_background(delay)
                if self._vector_rebuild_lock.locked():
                    self._update_dual_vector_auto_migration_stage("waiting_rebuild_lock", retry_index=index)
                    if index == len(retry_delays) - 1:
                        result = {
                            "success": False,
                            "error": "vector_rebuild_running",
                            "detail": "已有向量重建任务正在运行",
                        }
                    continue
                self._update_dual_vector_auto_migration_stage("rebuild_start", retry_index=index)
                result = await self._rebuild_all_vectors()
                if str(result.get("error", "") or "") != "vector_rebuild_running":
                    break

            success = bool(result.get("success", False)) or self._dual_vector_pools_enabled()
            last_error = ""
            if not success:
                errors = result.get("errors") if isinstance(result, dict) else None
                if isinstance(errors, list) and errors:
                    last_error = "; ".join(str(item) for item in errors[:5])
                else:
                    last_error = str(
                        result.get("detail")
                        or result.get("error")
                        or "dual_vector_auto_migration_failed"
                    )
                logger.warning(f"双池后台自动迁移未完成，继续使用单池: {last_error}")
            else:
                logger.info("双池后台自动迁移完成，已切换到双池检索")
            finished_at = time.time()
            progress = {
                **dict(self._dual_vector_auto_migration_status.get("progress") or {}),
                "result": result,
            }
            progress = self._normalize_dual_vector_auto_migration_progress(
                progress,
                now=finished_at,
                completed=True,
                success=success,
            )
            self._dual_vector_auto_migration_status.update(
                {
                    "running": False,
                    "success": success,
                    "stage": "completed" if success else "failed",
                    "progress": progress,
                    "last_error": last_error[:500],
                    "finished_at": finished_at,
                    "updated_at": finished_at,
                }
            )
        except asyncio.CancelledError:
            finished_at = time.time()
            progress = self._normalize_dual_vector_auto_migration_progress(
                self._dual_vector_auto_migration_status.get("progress"),
                now=finished_at,
                completed=True,
                success=False,
            )
            self._dual_vector_auto_migration_status.update(
                {
                    "running": False,
                    "stage": "cancelled",
                    "progress": progress,
                    "last_error": "cancelled",
                    "finished_at": finished_at,
                    "updated_at": finished_at,
                }
            )
            raise
        except Exception as exc:
            logger.warning(f"双池后台自动迁移异常，继续使用单池: {exc}")
            finished_at = time.time()
            progress = self._normalize_dual_vector_auto_migration_progress(
                self._dual_vector_auto_migration_status.get("progress"),
                now=finished_at,
                completed=True,
                success=False,
            )
            self._dual_vector_auto_migration_status.update(
                {
                    "running": False,
                    "success": False,
                    "stage": "exception",
                    "progress": progress,
                    "last_error": str(exc)[:500],
                    "finished_at": finished_at,
                    "updated_at": finished_at,
                }
            )

    async def _stop_background_tasks(self) -> None:
        async with self._background_lock:
            self._background_stopping = True
            tasks = [task for task in self._background_tasks.values() if task is not None and not task.done()]
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning(f"后台任务退出异常: {exc}")
            self._background_tasks.clear()

    async def _auto_save_loop(self) -> None:
        try:
            while not self._background_stopping:
                interval_minutes = max(1.0, float(self._cfg("advanced.auto_save_interval_minutes", 5) or 5))
                await asyncio.sleep(interval_minutes * 60.0)
                if self._background_stopping:
                    break
                if bool(self._cfg("advanced.enable_auto_save", True)):
                    self._persist()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"auto_save loop 异常: {exc}")

    async def _episode_pending_loop(self) -> None:
        try:
            while not self._background_stopping:
                await asyncio.sleep(60.0)
                if self._background_stopping:
                    break
                if not bool(self._cfg("episode.enabled", True)):
                    continue
                if not bool(self._cfg("episode.generation_enabled", True)):
                    continue
                await self.process_episode_pending_batch(
                    limit=max(1, int(self._cfg("episode.pending_batch_size", 50) or 50)),
                    max_retry=max(1, int(self._cfg("episode.pending_max_retry", 3) or 3)),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"episode_pending loop 异常: {exc}")

    async def _embedding_probe_loop(self) -> None:
        try:
            while not self._background_stopping:
                await asyncio.sleep(self._embedding_probe_interval_seconds())
                if self._background_stopping:
                    break
                startup_deferred = self._is_startup_self_check_deferred()
                if not self._embedding_fallback_enabled() and not startup_deferred:
                    continue
                if not self._is_embedding_degraded() and not startup_deferred:
                    continue
                try:
                    await self._recover_embedding_once()
                except Exception as exc:
                    logger.warning(f"embedding 恢复探测失败: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"embedding_probe loop 异常: {exc}")

    async def _paragraph_vector_backfill_loop(self) -> None:
        try:
            while not self._background_stopping:
                await asyncio.sleep(self._paragraph_vector_backfill_interval_seconds())
                if self._background_stopping:
                    break
                if not self._paragraph_vector_backfill_enabled():
                    continue
                if self._is_embedding_degraded():
                    continue
                await self._run_paragraph_backfill_once(
                    limit=self._paragraph_vector_backfill_batch_size(),
                    max_retry=self._paragraph_vector_backfill_max_retry(),
                    trigger="loop",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"paragraph_vector_backfill loop 异常: {exc}")

    async def _person_profile_refresh_loop(self) -> None:
        try:
            while not self._background_stopping:
                interval_minutes = max(1.0, float(self._cfg("person_profile.refresh_interval_minutes", 30) or 30))
                await asyncio.sleep(max(60.0, interval_minutes * 60.0))
                if self._background_stopping:
                    break
                if not bool(self._cfg("person_profile.enabled", True)):
                    continue
                active_window_hours = max(1.0, float(self._cfg("person_profile.active_window_hours", 72.0) or 72.0))
                max_refresh = max(1, int(self._cfg("person_profile.max_refresh_per_cycle", 50) or 50))
                cutoff = time.time() - active_window_hours * 3600.0
                candidates = [
                    person_id
                    for person_id, seen_at in sorted(
                        self._active_person_timestamps.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )
                    if seen_at >= cutoff
                ][:max_refresh]
                for person_id in candidates:
                    try:
                        if self._has_pending_person_profile_refresh(person_id):
                            continue
                        await self.refresh_person_profile(person_id, limit=max(4, int(self._cfg("person_profile.top_k_evidence", 12) or 12)), mark_active=False)
                    except Exception as exc:
                        logger.warning(f"刷新人物画像失败: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"person_profile_refresh loop 异常: {exc}")

    async def _person_profile_refresh_queue_loop(self) -> None:
        try:
            while not self._background_stopping:
                await asyncio.sleep(self._person_profile_refresh_queue_interval_seconds())
                if self._background_stopping:
                    break
                if not bool(self._cfg("person_profile.enabled", True)):
                    continue
                await self._process_person_profile_refresh_queue_batch(
                    limit=self._person_profile_refresh_queue_batch_size()
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"person_profile_refresh_queue loop 异常: {exc}")

    @staticmethod
    def _relation_status_is_inactive(status: Optional[Dict[str, Any]]) -> bool:
        if status is None:
            return True
        return bool(status.get("is_inactive"))

    def _load_paragraph_stale_marks(
        self,
        paragraph_hashes: Sequence[str],
    ) -> tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Any]]]:
        if self.metadata_store is None:
            return {}, {}
        normalized = self._tokens(paragraph_hashes)
        if not normalized:
            return {}, {}
        marks_by_paragraph = self.metadata_store.get_paragraph_stale_relation_marks_batch(normalized)
        relation_hashes = self._tokens(
            mark.get("relation_hash", "")
            for marks in marks_by_paragraph.values()
            for mark in marks
            if isinstance(mark, dict)
        )
        status_map = self.metadata_store.get_relation_status_batch(relation_hashes) if relation_hashes else {}
        return marks_by_paragraph, status_map

    def _paragraph_hidden_by_stale_marks(
        self,
        paragraph_hash: str,
        *,
        marks_by_paragraph: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        relation_status_map: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> bool:
        token = str(paragraph_hash or "").strip()
        if not token or self.metadata_store is None or not self._feedback_cfg_paragraph_hard_filter_enabled():
            return False

        marks_map = marks_by_paragraph if isinstance(marks_by_paragraph, dict) else {}
        status_map = relation_status_map if isinstance(relation_status_map, dict) else {}
        if not marks_map:
            marks_map, status_map = self._load_paragraph_stale_marks([token])
        elif not status_map:
            relation_hashes = self._tokens(
                mark.get("relation_hash", "")
                for mark in marks_map.get(token, [])
                if isinstance(mark, dict)
            )
            status_map = self.metadata_store.get_relation_status_batch(relation_hashes) if relation_hashes else {}

        for mark in marks_map.get(token, []):
            relation_hash = str((mark or {}).get("relation_hash", "") or "").strip()
            if not relation_hash:
                continue
            if self._relation_status_is_inactive(status_map.get(relation_hash)):
                return True
        return False

    def _filter_episode_hits(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self.metadata_store is None or not self._feedback_cfg_episode_query_block_enabled():
            return hits
        filtered: List[Dict[str, Any]] = []
        for item in hits:
            if str(item.get("type", "") or "").strip() != "episode":
                filtered.append(item)
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            source = str(metadata.get("source", "") or item.get("source", "") or "").strip()
            if source and self.metadata_store.is_episode_source_query_blocked(source):
                continue
            filtered.append(item)
        return filtered

    def _filter_user_visible_hits(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self._filter_current_effective_hits(self._filter_active_relation_hits(self._filter_episode_hits(hits)))

    def _filter_current_effective_hits(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self.metadata_store is None:
            return self._filter_hits_by_memory_change_metadata(hits)

        if not self._current_effective_filter_store_check_needed(hits):
            return self._filter_hits_by_memory_change_metadata(hits)

        now = time.time()
        paragraph_hashes: List[str] = []
        relation_hashes: List[str] = []
        for item in hits:
            item_type = str(item.get("type", "") or "").strip()
            hash_value = str(item.get("hash", "") or "").strip()
            if item_type == "paragraph" and hash_value:
                paragraph_hashes.append(hash_value)
            elif item_type == "relation" and hash_value:
                relation_hashes.append(hash_value)

        paragraph_map = self.metadata_store.get_paragraphs_by_hashes(paragraph_hashes) if paragraph_hashes else {}
        relation_map = self.metadata_store.get_relations_by_hashes(relation_hashes) if relation_hashes else {}
        filtered: List[Dict[str, Any]] = []
        for item in hits:
            metadata = coerce_metadata_dict(item.get("metadata"))
            item_type = str(item.get("type", "") or "").strip()
            hash_value = str(item.get("hash", "") or "").strip()
            if hash_value:
                stored: Optional[Dict[str, Any]] = None
                if item_type == "paragraph":
                    stored = paragraph_map.get(hash_value)
                elif item_type == "relation":
                    stored = relation_map.get(hash_value)
                if stored is not None:
                    metadata = coerce_metadata_dict(stored.get("metadata"))
            memory_change = metadata.get("memory_change") if isinstance(metadata.get("memory_change"), dict) else {}
            valid_to = self._optional_float(memory_change.get("valid_to"))
            if valid_to is not None and valid_to <= now:
                continue
            next_item = dict(item)
            next_item["metadata"] = metadata
            filtered.append(next_item)
        return filtered

    def _current_effective_filter_store_check_needed(self, hits: List[Dict[str, Any]]) -> bool:
        if any(isinstance(coerce_metadata_dict(item.get("metadata")).get("memory_change"), dict) for item in hits):
            return True
        cache = self._current_effective_filter_cache
        now = time.time()
        if now - float(cache.get("checked_at", 0.0) or 0.0) < 60.0:
            return bool(cache.get("needed", False))
        needed = False
        try:
            plans = self.metadata_store.list_fuzzy_modify_plans(
                limit=1,
                statuses=["executing", "executed", "rolled_back", "rollback_failed"],
            )
            needed = bool(plans)
        except Exception as exc:
            logger.warning(f"检查当前有效记忆过滤状态失败，将保守启用回表过滤: {exc}")
            needed = True
        cache["checked_at"] = now
        cache["needed"] = needed
        return needed

    def _filter_hits_by_memory_change_metadata(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now = time.time()
        filtered: List[Dict[str, Any]] = []
        for item in hits:
            metadata = coerce_metadata_dict(item.get("metadata"))
            memory_change = metadata.get("memory_change") if isinstance(metadata.get("memory_change"), dict) else {}
            valid_to = self._optional_float(memory_change.get("valid_to"))
            if valid_to is not None and valid_to <= now:
                continue
            next_item = dict(item)
            next_item["metadata"] = metadata
            filtered.append(next_item)
        return filtered

    def _resolve_feedback_related_person_ids(
        self,
        *,
        old_relation_rows: Sequence[Dict[str, Any]],
        corrected_relations: Sequence[Dict[str, Any]],
    ) -> List[str]:
        candidates = self._tokens(
            value
            for row in list(old_relation_rows) + list(corrected_relations)
            if isinstance(row, dict)
            for value in (row.get("subject"), row.get("object"))
        )
        resolved: List[str] = []
        seen = set()
        for candidate in candidates:
            person_id = PersonProfileService.resolve_person_id(candidate)
            if not person_id or person_id in seen:
                continue
            seen.add(person_id)
            resolved.append(person_id)
        return resolved

    def _mark_feedback_stale_paragraphs(
        self,
        *,
        task_id: int,
        query_tool_id: str,
        relation_hashes: Sequence[str],
        reason: str,
    ) -> Dict[str, List[str]]:
        if self.metadata_store is None or not self._feedback_cfg_paragraph_mark_enabled():
            return {}

        relation_tokens = self._tokens(relation_hashes)
        paragraph_map = self.metadata_store.get_paragraph_hashes_by_relation_hashes(relation_tokens)
        for relation_hash, paragraph_hashes in paragraph_map.items():
            for paragraph_hash in paragraph_hashes:
                self.metadata_store.upsert_paragraph_stale_relation_mark(
                    paragraph_hash=paragraph_hash,
                    relation_hash=relation_hash,
                    query_tool_id=query_tool_id,
                    task_id=task_id,
                    reason=reason,
                    source_type="feedback_correction",
                    source_id=str(task_id),
                    source_operation_id=f"feedback_correction:{task_id}:{paragraph_hash}:{relation_hash}",
                )
        return paragraph_map

    def _enqueue_feedback_episode_rebuilds(
        self,
        *,
        paragraph_hashes: Sequence[str],
        session_id: str,
        include_correction_source: bool,
    ) -> List[str]:
        if self.metadata_store is None or not self._feedback_cfg_episode_rebuild_enabled():
            return []

        sources = self._tokens(
            row.get("source", "")
            for row in self._load_paragraph_rows(paragraph_hashes)
            if isinstance(row, dict)
        )
        correction_source = self._chat_source(session_id)
        if include_correction_source and correction_source:
            sources = self._merge_tokens(sources, [correction_source])

        queued: List[str] = []
        for source in sources:
            if self.metadata_store.enqueue_episode_source_rebuild(source, reason="feedback_correction"):
                queued.append(source)
        return queued

    def _enqueue_feedback_profile_refreshes(
        self,
        *,
        person_ids: Sequence[str],
        query_tool_id: str,
    ) -> List[str]:
        if self.metadata_store is None or not self._feedback_cfg_profile_refresh_enabled():
            return []
        queued: List[str] = []
        for person_id in self._tokens(person_ids):
            payload = self.metadata_store.enqueue_person_profile_refresh(
                person_id=person_id,
                reason="feedback_correction",
                source_query_tool_id=query_tool_id,
            )
            if isinstance(payload, dict):
                queued.append(person_id)
        return queued

    @staticmethod
    def _feedback_affected_counts(task: Dict[str, Any]) -> Dict[str, int]:
        decision_payload = task.get("decision_payload") if isinstance(task.get("decision_payload"), dict) else {}
        apply_result = decision_payload.get("apply_result") if isinstance(decision_payload.get("apply_result"), dict) else {}
        rollback_plan = task.get("rollback_plan") if isinstance(task.get("rollback_plan"), dict) else {}
        corrected_write = rollback_plan.get("corrected_write") if isinstance(rollback_plan.get("corrected_write"), dict) else {}
        return {
            "relations": len(list(apply_result.get("relation_hashes") or rollback_plan.get("forgotten_relations") or [])),
            "stale_paragraphs": len(list(apply_result.get("stale_paragraph_hashes") or rollback_plan.get("stale_marks") or [])),
            "episode_sources": len(list(apply_result.get("episode_rebuild_sources") or rollback_plan.get("episode_sources") or [])),
            "profile_person_ids": len(list(apply_result.get("profile_refresh_person_ids") or rollback_plan.get("profile_person_ids") or [])),
            "correction_paragraphs": len(list(corrected_write.get("paragraph_hashes") or [])),
            "corrected_relations": len(list(corrected_write.get("corrected_relations") or [])),
        }

    def _build_feedback_rollback_plan_summary(self, rollback_plan: Dict[str, Any]) -> Dict[str, Any]:
        corrected_write = rollback_plan.get("corrected_write") if isinstance(rollback_plan.get("corrected_write"), dict) else {}
        return {
            "forgotten_relations": list(rollback_plan.get("forgotten_relations") or []),
            "corrected_write": corrected_write,
            "stale_marks": list(rollback_plan.get("stale_marks") or []),
            "episode_sources": self._tokens(rollback_plan.get("episode_sources")),
            "profile_person_ids": self._tokens(rollback_plan.get("profile_person_ids")),
            "affected_counts": {
                "forgotten_relations": len(list(rollback_plan.get("forgotten_relations") or [])),
                "corrected_relations": len(list(corrected_write.get("corrected_relations") or [])),
                "correction_paragraphs": len(list(corrected_write.get("paragraph_hashes") or [])),
                "stale_marks": len(list(rollback_plan.get("stale_marks") or [])),
                "episode_sources": len(self._tokens(rollback_plan.get("episode_sources"))),
                "profile_person_ids": len(self._tokens(rollback_plan.get("profile_person_ids"))),
            },
        }

    def _build_feedback_task_summary(self, task: Dict[str, Any]) -> Dict[str, Any]:
        query_snapshot = task.get("query_snapshot") if isinstance(task.get("query_snapshot"), dict) else {}
        decision_payload = task.get("decision_payload") if isinstance(task.get("decision_payload"), dict) else {}
        return {
            "task_id": int(task.get("id", 0) or 0),
            "query_tool_id": str(task.get("query_tool_id", "") or "").strip(),
            "session_id": str(task.get("session_id", "") or "").strip(),
            "query_text": str(query_snapshot.get("query", "") or "").strip(),
            "query_timestamp": task.get("query_timestamp"),
            "task_status": str(task.get("status", "") or "").strip().lower(),
            "decision": str(decision_payload.get("decision", "") or "").strip().lower(),
            "decision_confidence": float(decision_payload.get("confidence", 0.0) or 0.0),
            "feedback_message_count": int(decision_payload.get("feedback_message_count", 0) or 0),
            "rollback_status": str(task.get("rollback_status", "") or "none").strip().lower() or "none",
            "affected_counts": self._feedback_affected_counts(task),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
        }

    def _build_feedback_task_detail(self, task: Dict[str, Any]) -> Dict[str, Any]:
        detail = self._build_feedback_task_summary(task)
        detail.update(
            {
                "query_snapshot": task.get("query_snapshot") if isinstance(task.get("query_snapshot"), dict) else {},
                "decision_payload": task.get("decision_payload") if isinstance(task.get("decision_payload"), dict) else {},
                "rollback_plan_summary": self._build_feedback_rollback_plan_summary(
                    task.get("rollback_plan") if isinstance(task.get("rollback_plan"), dict) else {}
                ),
                "rollback_result": task.get("rollback_result") if isinstance(task.get("rollback_result"), dict) else {},
                "rollback_error": str(task.get("rollback_error", "") or "").strip(),
                "rollback_requested_by": str(task.get("rollback_requested_by", "") or "").strip(),
                "rollback_reason": str(task.get("rollback_reason", "") or "").strip(),
                "rollback_requested_at": task.get("rollback_requested_at"),
                "rolled_back_at": task.get("rolled_back_at"),
                "action_logs": self.metadata_store.list_feedback_action_logs(int(task.get("id", 0) or 0))
                if self.metadata_store is not None
                else [],
            }
        )
        return detail

    def _soft_delete_feedback_correction_paragraphs(self, paragraph_hashes: Sequence[str]) -> Dict[str, Any]:
        assert self.metadata_store is not None
        hashes = self._tokens(paragraph_hashes)
        if not hashes:
            return {"deleted_hashes": [], "deleted_external_refs": []}

        paragraph_rows = {hash_value: self.metadata_store.get_paragraph(hash_value) for hash_value in hashes}
        self.metadata_store.mark_as_deleted(hashes, "paragraph")
        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"DELETE FROM paragraph_entities WHERE paragraph_hash IN ({','.join(['?'] * len(hashes))})",
            tuple(hashes),
        )
        cursor.execute(
            f"DELETE FROM paragraph_relations WHERE paragraph_hash IN ({','.join(['?'] * len(hashes))})",
            tuple(hashes),
        )
        conn.commit()
        deleted_external_refs = self.metadata_store.delete_external_memory_refs_by_paragraphs(hashes)
        return {
            "deleted_hashes": hashes,
            "paragraph_rows": paragraph_rows,
            "deleted_external_refs": deleted_external_refs,
        }

    async def _rollback_feedback_task(
        self,
        *,
        task_id: int,
        requested_by: str,
        reason: str,
    ) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store is not None

        task = self.metadata_store.get_feedback_task_by_id(task_id)
        if task is None:
            return {"success": False, "error": "反馈纠错任务不存在"}
        if str(task.get("status", "") or "").strip().lower() != "applied":
            return {"success": False, "error": "仅 applied 的反馈纠错任务允许回退"}
        rollback_status = str(task.get("rollback_status", "") or "none").strip().lower()
        if rollback_status == "rolled_back":
            return {
                "success": True,
                "already_rolled_back": True,
                "task": self._build_feedback_task_detail(task),
                "result": task.get("rollback_result") if isinstance(task.get("rollback_result"), dict) else {},
            }
        if rollback_status == "running":
            return {"success": False, "error": "该反馈纠错任务正在回退中", "task": self._build_feedback_task_detail(task)}

        query_tool_id = str(task.get("query_tool_id", "") or "").strip()
        rollback_plan = task.get("rollback_plan") if isinstance(task.get("rollback_plan"), dict) else {}
        if not rollback_plan:
            running_task = self.metadata_store.mark_feedback_task_rollback_running(
                task_id=task_id,
                requested_by=requested_by,
                reason=reason,
            )
            if running_task is None:
                latest_task = self.metadata_store.get_feedback_task_by_id(task_id)
                latest_status = str((latest_task or {}).get("rollback_status", "") or "none").strip().lower()
                if latest_status == "running":
                    return {
                        "success": False,
                        "error": "该反馈纠错任务正在回退中",
                        "task": self._build_feedback_task_detail(latest_task) if isinstance(latest_task, dict) else None,
                    }
                if latest_status == "rolled_back":
                    return {
                        "success": True,
                        "already_rolled_back": True,
                        "task": self._build_feedback_task_detail(latest_task) if isinstance(latest_task, dict) else None,
                        "result": (latest_task or {}).get("rollback_result") if isinstance((latest_task or {}).get("rollback_result"), dict) else {},
                    }
                return {
                    "success": False,
                    "error": "无法进入回退状态",
                    "task": self._build_feedback_task_detail(latest_task) if isinstance(latest_task, dict) else None,
                }
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="rollback_error",
                reason="rollback_plan_missing",
            )
            failed = self.metadata_store.finalize_feedback_task_rollback(
                task_id=task_id,
                rollback_status="error",
                rollback_error="rollback_plan_missing",
            )
            return {"success": False, "error": "缺少 rollback_plan，无法回退", "task": failed}

        running_task = self.metadata_store.mark_feedback_task_rollback_running(
            task_id=task_id,
            requested_by=requested_by,
            reason=reason,
        )
        if running_task is None:
            latest_task = self.metadata_store.get_feedback_task_by_id(task_id)
            latest_status = str((latest_task or {}).get("rollback_status", "") or "none").strip().lower()
            if latest_status == "running":
                return {
                    "success": False,
                    "error": "该反馈纠错任务正在回退中",
                    "task": self._build_feedback_task_detail(latest_task) if isinstance(latest_task, dict) else None,
                }
            if latest_status == "rolled_back":
                return {
                    "success": True,
                    "already_rolled_back": True,
                    "task": self._build_feedback_task_detail(latest_task) if isinstance(latest_task, dict) else None,
                    "result": (latest_task or {}).get("rollback_result") if isinstance((latest_task or {}).get("rollback_result"), dict) else {},
                }
            return {
                "success": False,
                "error": "无法进入回退状态",
                "task": self._build_feedback_task_detail(latest_task) if isinstance(latest_task, dict) else None,
            }

        result: Dict[str, Any] = {
            "task_id": task_id,
            "query_tool_id": query_tool_id,
            "restored_relation_hashes": [],
            "reverted_corrected_relation_hashes": [],
            "deleted_correction_paragraph_hashes": [],
            "cleared_stale_mark_count": 0,
            "episode_sources_queued": [],
            "profile_person_ids_queued": [],
            "warnings": [],
        }
        try:
            forgotten_relations = rollback_plan.get("forgotten_relations") if isinstance(rollback_plan.get("forgotten_relations"), list) else []
            for item in forgotten_relations:
                if not isinstance(item, dict):
                    continue
                relation_hash = str(item.get("hash", "") or "").strip()
                snapshot = item.get("before_status") if isinstance(item.get("before_status"), dict) else {}
                if not relation_hash or not snapshot:
                    continue
                before_status = self.metadata_store.get_relation_status_batch([relation_hash]).get(relation_hash, {})
                after_status = self.metadata_store.restore_relation_status_from_snapshot(relation_hash, snapshot)
                if after_status is None:
                    result["warnings"].append(f"restore_old_relation_failed:{relation_hash}")
                    continue
                result["restored_relation_hashes"].append(relation_hash)
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_restore_relation",
                    target_hash=relation_hash,
                    before_payload=before_status,
                    after_payload=after_status,
                    reason=reason,
                )

            corrected_write = rollback_plan.get("corrected_write") if isinstance(rollback_plan.get("corrected_write"), dict) else {}
            correction_paragraph_hashes = self._tokens(corrected_write.get("paragraph_hashes"))
            deleted_paragraphs = self._soft_delete_feedback_correction_paragraphs(correction_paragraph_hashes)
            result["deleted_correction_paragraph_hashes"] = deleted_paragraphs.get("deleted_hashes", [])
            paragraph_rows = deleted_paragraphs.get("paragraph_rows") if isinstance(deleted_paragraphs.get("paragraph_rows"), dict) else {}
            deleted_external_refs = deleted_paragraphs.get("deleted_external_refs") if isinstance(deleted_paragraphs.get("deleted_external_refs"), list) else []
            deleted_ref_map: Dict[str, List[Dict[str, Any]]] = {}
            for ref in deleted_external_refs:
                if not isinstance(ref, dict):
                    continue
                paragraph_hash = str(ref.get("paragraph_hash", "") or "").strip()
                if not paragraph_hash:
                    continue
                deleted_ref_map.setdefault(paragraph_hash, []).append(ref)
            for paragraph_hash in result["deleted_correction_paragraph_hashes"]:
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_delete_correction_paragraph",
                    target_hash=paragraph_hash,
                    before_payload={
                        "paragraph": paragraph_rows.get(paragraph_hash) if isinstance(paragraph_rows.get(paragraph_hash), dict) else {},
                        "external_refs": deleted_ref_map.get(paragraph_hash, []),
                    },
                    reason=reason,
                )

            corrected_relations = corrected_write.get("corrected_relations") if isinstance(corrected_write.get("corrected_relations"), list) else []
            for item in corrected_relations:
                if not isinstance(item, dict):
                    continue
                relation_hash = str(item.get("hash", "") or "").strip()
                if not relation_hash:
                    continue
                before_status = self.metadata_store.get_relation_status_batch([relation_hash]).get(relation_hash, {})
                if bool(item.get("existed_before")):
                    snapshot = item.get("before_status") if isinstance(item.get("before_status"), dict) else {}
                    after_status = self.metadata_store.restore_relation_status_from_snapshot(relation_hash, snapshot)
                else:
                    self.metadata_store.update_relations_protection([relation_hash], protected_until=0.0, is_pinned=False)
                    self.metadata_store.mark_relations_inactive([relation_hash], inactive_since=time.time())
                    after_status = self.metadata_store.get_relation_status_batch([relation_hash]).get(relation_hash)
                if after_status is None:
                    result["warnings"].append(f"revert_corrected_relation_failed:{relation_hash}")
                    continue
                result["reverted_corrected_relation_hashes"].append(relation_hash)
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_revert_corrected_relation",
                    target_hash=relation_hash,
                    before_payload=before_status,
                    after_payload=after_status,
                    reason=reason,
                )

            stale_marks_raw = rollback_plan.get("stale_marks") if isinstance(rollback_plan.get("stale_marks"), list) else []
            stale_mark_rollbacks: List[Dict[str, Any]] = []
            for item in stale_marks_raw:
                if not isinstance(item, dict):
                    continue
                paragraph_hash = str(item.get("paragraph_hash", "") or "").strip()
                relation_hash = str(item.get("relation_hash", "") or "").strip()
                if not paragraph_hash or not relation_hash:
                    continue
                source_operation_id = str(
                    item.get("source_operation_id", "")
                    or f"feedback_correction:{task_id}:{paragraph_hash}:{relation_hash}"
                ).strip()
                rollback_mark = self.metadata_store.rollback_paragraph_stale_relation_mark(
                    paragraph_hash=paragraph_hash,
                    relation_hash=relation_hash,
                    expected_source_type=str(item.get("source_type", "") or "feedback_correction"),
                    expected_source_id=str(item.get("source_id", "") or task_id),
                    expected_source_operation_id=source_operation_id,
                    previous_mark=(
                        item.get("previous_mark")
                        if isinstance(item.get("previous_mark"), dict)
                        else None
                    ),
                )
                stale_mark_rollbacks.append(rollback_mark)
            result["cleared_stale_mark_count"] = sum(
                1 for item in stale_mark_rollbacks if item.get("action") == "deleted"
            )
            result["stale_mark_rollbacks"] = stale_mark_rollbacks
            for rollback_mark in stale_mark_rollbacks:
                paragraph_hash = str(rollback_mark.get("paragraph_hash", "") or "").strip()
                relation_hash = str(rollback_mark.get("relation_hash", "") or "").strip()
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_clear_stale_mark",
                    target_hash=paragraph_hash,
                    after_payload={"relation_hash": relation_hash, "rollback": rollback_mark},
                    reason=reason,
                )

            for source in self._tokens(rollback_plan.get("episode_sources")):
                if self.metadata_store.enqueue_episode_source_rebuild(source, reason="feedback_correction_rollback"):
                    result["episode_sources_queued"].append(source)
                    self.metadata_store.append_feedback_action_log(
                        task_id=task_id,
                        query_tool_id=query_tool_id,
                        action_type="rollback_enqueue_episode_rebuild",
                        target_hash=source,
                        reason=reason,
                    )

            for person_id in self._tokens(rollback_plan.get("profile_person_ids")):
                payload = self.metadata_store.enqueue_person_profile_refresh(
                    person_id=person_id,
                    reason="feedback_correction_rollback",
                    source_query_tool_id=query_tool_id,
                )
                if not isinstance(payload, dict):
                    continue
                result["profile_person_ids_queued"].append(person_id)
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_enqueue_profile_refresh",
                    target_hash=person_id,
                    reason=reason,
                )

            self._rebuild_graph_from_metadata()
            self._persist()
            final_task = self.metadata_store.finalize_feedback_task_rollback(
                task_id=task_id,
                rollback_status="rolled_back",
                rollback_result=result,
            )
            return {"success": True, "result": result, "task": self._build_feedback_task_detail(final_task or running_task)}
        except Exception as exc:
            logger.warning(f"反馈纠错回退失败: task_id={task_id} err={exc}", exc_info=True)
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="rollback_error",
                reason=str(exc),
                after_payload=result if result else None,
            )
            final_task = self.metadata_store.finalize_feedback_task_rollback(
                task_id=task_id,
                rollback_status="error",
                rollback_result=result if result else None,
                rollback_error=str(exc),
            )
            return {
                "success": False,
                "error": str(exc),
                "result": result,
                "task": self._build_feedback_task_detail(final_task or running_task),
            }

    async def _process_feedback_profile_refresh_batch(
        self,
        *,
        limit: int,
        debounce_seconds: float = 0.0,
        retry_backoff_seconds: float = 0.0,
        max_retry: Optional[int] = None,
    ) -> Dict[str, Any]:
        if self.metadata_store is None or self.person_profile_service is None:
            return {"processed": 0, "refreshed": 0, "failed": 0, "items": [], "failures": []}

        rows = self.metadata_store.fetch_person_profile_refresh_batch(
            limit=max(1, int(limit or 1)),
            max_retry=self._person_profile_refresh_max_retry() if max_retry is None else max(0, int(max_retry)),
            debounce_seconds=max(0.0, float(debounce_seconds or 0.0)),
            retry_backoff_seconds=max(0.0, float(retry_backoff_seconds or 0.0)),
        )
        items: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        for row in rows:
            person_id = str(row.get("person_id", "") or "").strip()
            requested_at = row.get("requested_at")
            if not person_id:
                continue
            if not self.metadata_store.mark_person_profile_refresh_running(person_id, requested_at=requested_at):
                continue
            try:
                profile = await self.refresh_person_profile(
                    person_id,
                    limit=max(4, int(self._cfg("person_profile.top_k_evidence", 12) or 12)),
                    mark_active=False,
                )
                if isinstance(profile, dict) and bool(profile.get("success")):
                    self.metadata_store.mark_person_profile_refresh_done(person_id, requested_at=requested_at)
                    items.append(
                        {
                            "person_id": person_id,
                            "profile_version": int(profile.get("profile_version", 0) or 0),
                            "profile_source": str(profile.get("profile_source", "") or ""),
                        }
                    )
                else:
                    error = str((profile or {}).get("error", "") or "person profile refresh failed")
                    self.metadata_store.mark_person_profile_refresh_failed(person_id, error, requested_at=requested_at)
                    failures.append({"person_id": person_id, "error": error})
            except Exception as exc:
                error = str(exc)[:500]
                self.metadata_store.mark_person_profile_refresh_failed(person_id, error, requested_at=requested_at)
                failures.append({"person_id": person_id, "error": error})
        return {
            "processed": len(items) + len(failures),
            "refreshed": len(items),
            "failed": len(failures),
            "items": items,
            "failures": failures,
        }

    async def _process_feedback_episode_rebuild_batch(self, *, limit: int) -> Dict[str, Any]:
        if self.metadata_store is None or self.episode_service is None:
            return {"processed": 0, "rebuilt": 0, "failed": 0, "items": [], "failures": []}

        rows = self.metadata_store.fetch_episode_source_rebuild_batch(
            limit=max(1, int(limit or 1)),
            max_retry=max(1, int(self._cfg("episode.pending_max_retry", 3) or 3)),
        )
        items: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        for row in rows:
            source = str(row.get("source", "") or "").strip()
            requested_at = row.get("requested_at")
            if not source:
                continue
            if not self.metadata_store.mark_episode_source_running(source, requested_at=requested_at):
                continue
            try:
                result = await self.episode_service.rebuild_source(source)
                self.metadata_store.mark_episode_source_done(source, requested_at=requested_at)
                items.append(result if isinstance(result, dict) else {"source": source})
            except Exception as exc:
                error = str(exc)[:500]
                self.metadata_store.mark_episode_source_failed(source, error, requested_at=requested_at)
                failures.append({"source": source, "error": error})
        if items or failures:
            self._persist()
        return {
            "processed": len(items) + len(failures),
            "rebuilt": len(items),
            "failed": len(failures),
            "items": items,
            "failures": failures,
        }

    async def _feedback_correction_reconcile_loop(self) -> None:
        try:
            while not self._background_stopping:
                await asyncio.sleep(self._feedback_cfg_reconcile_interval_seconds())
                if self._background_stopping:
                    break
                if self.metadata_store is None or not self._feedback_cfg_enabled():
                    continue
                batch_size = self._feedback_cfg_reconcile_batch_size()
                if self._feedback_cfg_profile_refresh_enabled():
                    await self._process_person_profile_refresh_queue_batch(limit=batch_size)
                if self._feedback_cfg_episode_rebuild_enabled():
                    await self._process_feedback_episode_rebuild_batch(limit=batch_size)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"feedback_correction_reconcile loop 异常: {exc}")

    @staticmethod
    def _coerce_datetime(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value))
            except Exception:
                return None
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except Exception:
            return None

    @staticmethod
    def _feedback_signal_tokens() -> tuple[str, ...]:
        return (
            "不对",
            "错了",
            "你记错",
            "记错了",
            "不是",
            "并不是",
            "纠正",
            "更正",
            "改成",
            "应该是",
            "实际是",
            "说反了",
        )

    @classmethod
    def _feedback_contains_signal(cls, text: str) -> bool:
        content = str(text or "").strip().lower()
        if not content:
            return False
        return any(token in content for token in cls._feedback_signal_tokens())

    @staticmethod
    def _feedback_noise(text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return True
        if SDKMemoryKernel._feedback_contains_signal(content):
            return False
        if len(content) <= 2:
            return True
        markers = (
            "哈哈",
            "好的",
            "收到",
            "谢谢",
            "嗯嗯",
            "晚安",
            "早安",
            "拜拜",
            "在吗",
        )
        return len(content) <= 8 and any(marker in content for marker in markers)

    @staticmethod
    def _safe_json_loads(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            repaired = repair_json(text)
            payload = json.loads(repaired) if isinstance(repaired, str) else repaired
        except Exception:
            payload = None
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _feedback_cfg_enabled() -> bool:
        memory_cfg = global_config.a_memorix.integration
        return bool(getattr(memory_cfg, "feedback_correction_enabled", False))

    @staticmethod
    def _feedback_cfg_window_hours() -> float:
        memory_cfg = global_config.a_memorix.integration
        return max(0.1, float(getattr(memory_cfg, "feedback_correction_window_hours", 12.0) or 12.0))

    @staticmethod
    def _feedback_cfg_check_interval_seconds() -> float:
        memory_cfg = global_config.a_memorix.integration
        minutes = max(1, int(getattr(memory_cfg, "feedback_correction_check_interval_minutes", 30) or 30))
        return float(minutes) * 60.0

    @staticmethod
    def _feedback_cfg_batch_size() -> int:
        memory_cfg = global_config.a_memorix.integration
        return max(1, int(getattr(memory_cfg, "feedback_correction_batch_size", 20) or 20))

    @staticmethod
    def _feedback_cfg_auto_apply_threshold() -> float:
        memory_cfg = global_config.a_memorix.integration
        value = float(getattr(memory_cfg, "feedback_correction_auto_apply_threshold", 0.85) or 0.85)
        return min(1.0, max(0.0, value))

    @staticmethod
    def _feedback_cfg_max_messages() -> int:
        memory_cfg = global_config.a_memorix.integration
        return max(1, int(getattr(memory_cfg, "feedback_correction_max_feedback_messages", 30) or 30))

    @staticmethod
    def _feedback_cfg_prefilter_enabled() -> bool:
        memory_cfg = global_config.a_memorix.integration
        return bool(getattr(memory_cfg, "feedback_correction_prefilter_enabled", True))

    @staticmethod
    def _feedback_cfg_paragraph_mark_enabled() -> bool:
        memory_cfg = global_config.a_memorix.integration
        return bool(getattr(memory_cfg, "feedback_correction_paragraph_mark_enabled", True))

    @staticmethod
    def _feedback_cfg_paragraph_hard_filter_enabled() -> bool:
        memory_cfg = global_config.a_memorix.integration
        return bool(getattr(memory_cfg, "feedback_correction_paragraph_hard_filter_enabled", True))

    @staticmethod
    def _feedback_cfg_profile_refresh_enabled() -> bool:
        memory_cfg = global_config.a_memorix.integration
        return bool(getattr(memory_cfg, "feedback_correction_profile_refresh_enabled", True))

    @staticmethod
    def _feedback_cfg_profile_force_refresh_on_read() -> bool:
        memory_cfg = global_config.a_memorix.integration
        return bool(getattr(memory_cfg, "feedback_correction_profile_force_refresh_on_read", True))

    @staticmethod
    def _feedback_cfg_episode_rebuild_enabled() -> bool:
        memory_cfg = global_config.a_memorix.integration
        return bool(getattr(memory_cfg, "feedback_correction_episode_rebuild_enabled", True))

    @staticmethod
    def _feedback_cfg_episode_query_block_enabled() -> bool:
        memory_cfg = global_config.a_memorix.integration
        return bool(getattr(memory_cfg, "feedback_correction_episode_query_block_enabled", True))

    @staticmethod
    def _feedback_cfg_reconcile_interval_seconds() -> float:
        memory_cfg = global_config.a_memorix.integration
        minutes = max(1, int(getattr(memory_cfg, "feedback_correction_reconcile_interval_minutes", 5) or 5))
        return float(minutes) * 60.0

    @staticmethod
    def _feedback_cfg_reconcile_batch_size() -> int:
        memory_cfg = global_config.a_memorix.integration
        return max(1, int(getattr(memory_cfg, "feedback_correction_reconcile_batch_size", 20) or 20))

    def _should_auto_enqueue_episode(self, *, source_type: str) -> bool:
        if not bool(self._cfg("episode.enabled", True)):
            return False
        if not bool(self._cfg("episode.generation_enabled", True)):
            return False

        normalized_source_type = str(source_type or "").strip().lower()
        disabled_types = {
            str(item or "").strip().lower()
            for item in self._argument_tokens(self._cfg("episode.disabled_source_types", ["person_fact"]))
        }
        return normalized_source_type not in disabled_types

    def _person_profile_refresh_queue_interval_seconds(self) -> float:
        return max(1.0, float(self._cfg("person_profile.refresh_queue_interval_seconds", 60) or 60))

    def _person_profile_refresh_queue_batch_size(self) -> int:
        return max(1, int(self._cfg("person_profile.refresh_queue_batch_size", 10) or 10))

    def _person_profile_refresh_debounce_seconds(self) -> float:
        return max(0.0, float(self._cfg("person_profile.refresh_debounce_seconds", 120) or 0))

    def _person_profile_refresh_retry_backoff_seconds(self) -> float:
        return max(0.0, float(self._cfg("person_profile.refresh_retry_backoff_seconds", 300) or 0))

    def _person_profile_refresh_max_retry(self) -> int:
        return max(0, int(self._cfg("person_profile.max_retry", 3) or 0))

    def _enqueue_person_profile_refresh(self, person_id: str, *, reason: str = "") -> bool:
        if self.metadata_store is None or not bool(self._cfg("person_profile.enabled", True)):
            return False
        payload = self.metadata_store.enqueue_person_profile_refresh(
            person_id=person_id,
            reason=str(reason or "").strip() or "memory_ingest",
        )
        return isinstance(payload, dict)

    def _has_pending_person_profile_refresh(self, person_id: str) -> bool:
        if self.metadata_store is None:
            return False
        request = self.metadata_store.get_person_profile_refresh_request(person_id)
        if not isinstance(request, dict):
            return False
        status = str(request.get("status", "") or "").strip().lower()
        if status in {"pending", "running"}:
            return True
        if status != "failed":
            return False
        return int(request.get("retry_count", 0) or 0) < self._person_profile_refresh_max_retry()

    async def _process_person_profile_refresh_queue_batch(self, *, limit: int) -> Dict[str, Any]:
        return await self._process_feedback_profile_refresh_batch(
            limit=limit,
            debounce_seconds=self._person_profile_refresh_debounce_seconds(),
            retry_backoff_seconds=self._person_profile_refresh_retry_backoff_seconds(),
            max_retry=self._person_profile_refresh_max_retry(),
        )

    @classmethod
    def _feedback_cfg_window_label(cls) -> str:
        hours = cls._feedback_cfg_window_hours()
        if abs(hours - round(hours)) < 1e-9:
            return f"{int(round(hours))}h"
        return f"{hours:.2f}h"

    async def enqueue_feedback_task(
        self,
        *,
        query_tool_id: str,
        session_id: str,
        query_timestamp: Any = None,
        structured_content: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self._feedback_cfg_enabled():
            return {"success": False, "queued": False, "reason": "feedback_correction_disabled"}
        if self.metadata_store is None:
            return {"success": False, "queued": False, "reason": "metadata_store_unavailable"}

        clean_tool_id = str(query_tool_id or "").strip()
        clean_session_id = str(session_id or "").strip()
        if not clean_tool_id or not clean_session_id:
            return {"success": False, "queued": False, "reason": "missing_required_fields"}

        content = structured_content if isinstance(structured_content, dict) else {}
        hits = content.get("hits")
        if not isinstance(hits, list) or not hits:
            return {"success": False, "queued": False, "reason": "no_hits"}

        query_time = self._coerce_datetime(query_timestamp) or datetime.now()
        due_at = query_time + timedelta(hours=self._feedback_cfg_window_hours())
        saved = self.metadata_store.enqueue_feedback_task(
            query_tool_id=clean_tool_id,
            session_id=clean_session_id,
            query_timestamp=query_time.timestamp(),
            due_at=due_at.timestamp(),
            query_snapshot=content,
        )
        if not isinstance(saved, dict):
            return {"success": False, "queued": False, "reason": "db_save_failed"}

        logger.debug(
            f"反馈纠错任务入队: query_tool_id={clean_tool_id} due_at={due_at.isoformat()}",
        )
        return {
            "success": True,
            "queued": True,
            "query_tool_id": clean_tool_id,
            "due_at": due_at.isoformat(),
            "task": saved,
        }

    @staticmethod
    def _extract_feedback_messages(
        *,
        session_id: str,
        query_time: datetime,
        due_time: datetime,
        max_messages: int,
    ) -> List[str]:
        raw_messages = message_api.get_messages_by_time_in_chat(
            chat_id=session_id,
            start_time=query_time.timestamp(),
            end_time=due_time.timestamp(),
            limit=max(1, int(max_messages) * 4),
            limit_mode="latest",
            filter_mai=True,
            filter_command=True,
        )
        collected: List[str] = []
        seen = set()
        for item in raw_messages:
            text = str(getattr(item, "processed_plain_text", "") or "").strip()
            if SDKMemoryKernel._feedback_noise(text):
                continue
            if text in seen:
                continue
            seen.add(text)
            collected.append(text)
        if len(collected) > max_messages:
            collected = collected[-max_messages:]
        return collected

    def _build_feedback_hit_briefs(self, hits: List[Dict[str, Any]], *, limit: int = 12) -> List[Dict[str, Any]]:
        briefs: List[Dict[str, Any]] = []
        for raw in hits[: max(1, int(limit))]:
            if not isinstance(raw, dict):
                continue
            metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            subject = str(metadata.get("subject", "") or "").strip()
            predicate = str(metadata.get("predicate", "") or "").strip()
            obj = str(metadata.get("object", "") or "").strip()
            linked_relation_hashes: List[str] = []
            linked_relation_texts: List[str] = []

            item_type = str(raw.get("type", "") or "").strip()
            item_hash = str(raw.get("hash", "") or "").strip()
            if item_type == "paragraph" and item_hash and self.metadata_store is not None:
                linked_relations = self.metadata_store.get_paragraph_relations(item_hash)
                for relation in linked_relations:
                    relation_hash = str(relation.get("hash", "") or "").strip()
                    if not relation_hash or relation_hash in linked_relation_hashes:
                        continue
                    linked_relation_hashes.append(relation_hash)
                    rel_subject = str(relation.get("subject", "") or "").strip()
                    rel_predicate = str(relation.get("predicate", "") or "").strip()
                    rel_object = str(relation.get("object", "") or "").strip()
                    relation_text = self._format_relation_text(rel_subject, rel_predicate, rel_object)
                    if relation_text:
                        linked_relation_texts.append(relation_text)
                    if not (subject and predicate and obj):
                        subject = rel_subject
                        predicate = rel_predicate
                        obj = rel_object
            briefs.append(
                {
                    "hash": item_hash,
                    "type": item_type,
                    "content": str(raw.get("content", "") or "").strip(),
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "linked_relation_hashes": linked_relation_hashes[:6],
                    "linked_relation_texts": linked_relation_texts[:3],
                }
            )
        return briefs

    @staticmethod
    def _should_invoke_feedback_classifier(feedback_messages: List[str]) -> bool:
        if not feedback_messages:
            return False
        lowered = "\n".join(feedback_messages).lower()
        return any(token in lowered for token in SDKMemoryKernel._feedback_signal_tokens())

    async def _classify_feedback(
        self,
        *,
        query_tool_id: str,
        query_text: str,
        hit_briefs: List[Dict[str, Any]],
        feedback_messages: List[str],
    ) -> Dict[str, Any]:
        prompt = (
            "你是长期记忆纠错分类器。"
            "你会根据“记忆检索命中列表”和“用户后续反馈”判断是否需要修正记忆。"
            "请严格输出 JSON 对象，不要输出解释文字。\n\n"
            f"query_tool_id: {query_tool_id}\n"
            f"原查询: {query_text}\n"
            f"候选命中: {json.dumps(hit_briefs, ensure_ascii=False)}\n"
            f"反馈消息: {json.dumps(feedback_messages, ensure_ascii=False)}\n\n"
            "输出 JSON schema:\n"
            "{"
            "\"decision\":\"confirm|reject|correct|supplement|none\","
            "\"confidence\":0.0,"
            "\"target_hashes\":[\"命中列表中的 hash\"],"
            "\"corrected_relations\":[{\"subject\":\"\",\"predicate\":\"\",\"object\":\"\",\"confidence\":1.0}],"
            "\"reason\":\"\""
            "}\n"
            "约束:\n"
            "1. 只有当反馈明确指向错误时才输出 reject/correct。\n"
            "2. target_hashes 必须来自候选命中 hash。\n"
            "3. corrected_relations 仅在 decision=correct 时填写，且必须是明确三元组。\n"
            "4. 不确定时输出 decision=none, confidence<=0.5。"
        )
        try:
            if self._feedback_classifier is None:
                self._feedback_classifier = LLMServiceClient(
                    task_name="utils",
                    request_type="memory_feedback_correction",
                )
            response = await self._feedback_classifier.generate_response(prompt)
            payload = self._safe_json_loads(getattr(response, "response", ""))
        except Exception as exc:
            logger.warning(f"反馈分类器调用失败: {exc}")
            payload = {}
        return payload

    @staticmethod
    def _normalize_feedback_decision(
        payload: Dict[str, Any],
        *,
        hit_hashes: Sequence[str],
    ) -> Dict[str, Any]:
        allowed = {"confirm", "reject", "correct", "supplement", "none"}
        decision = str(payload.get("decision", "") or "").strip().lower()
        if decision not in allowed:
            decision = "none"
        try:
            confidence = float(payload.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))

        valid_hashes = {str(item or "").strip() for item in hit_hashes if str(item or "").strip()}
        target_hashes_raw = payload.get("target_hashes")
        if isinstance(target_hashes_raw, str):
            target_hashes_candidates = [target_hashes_raw]
        elif isinstance(target_hashes_raw, list):
            target_hashes_candidates = target_hashes_raw
        else:
            target_hashes_candidates = []
        target_hashes = [
            str(item or "").strip()
            for item in target_hashes_candidates
            if str(item or "").strip() in valid_hashes
        ]

        corrected_relations: List[Dict[str, Any]] = []
        raw_relations = payload.get("corrected_relations")
        if isinstance(raw_relations, list):
            for item in raw_relations:
                if not isinstance(item, dict):
                    continue
                subject = str(item.get("subject", "") or "").strip()
                predicate = str(item.get("predicate", "") or "").strip()
                obj = str(item.get("object", "") or "").strip()
                if not (subject and predicate and obj):
                    continue
                try:
                    rel_conf = float(item.get("confidence", 1.0) or 1.0)
                except (TypeError, ValueError):
                    rel_conf = 1.0
                corrected_relations.append(
                    {
                        "subject": subject,
                        "predicate": predicate,
                        "object": obj,
                        "confidence": min(1.0, max(0.0, rel_conf)),
                    }
                )
        corrected_relations = corrected_relations[:6]

        return {
            "decision": decision,
            "confidence": confidence,
            "target_hashes": target_hashes,
            "corrected_relations": corrected_relations,
            "reason": str(payload.get("reason", "") or "").strip(),
            "raw": payload,
        }

    @staticmethod
    def _feedback_apply_result_status(apply_result: Dict[str, Any]) -> str:
        if bool(apply_result.get("applied")):
            return "applied"

        reason = str(apply_result.get("reason", "") or "").strip().lower()
        if reason in {"low_confidence", "no_relation_targets"} or reason.startswith("decision_"):
            return "skipped"
        return "error"

    def _restore_feedback_relations_from_snapshots(
        self,
        *,
        task_id: int,
        query_tool_id: str,
        relation_hashes: Sequence[str],
        snapshots: Dict[str, Dict[str, Any]],
        current_statuses: Optional[Dict[str, Dict[str, Any]]] = None,
        reason: str,
    ) -> Dict[str, List[str]]:
        assert self.metadata_store is not None

        restored_hashes: List[str] = []
        failed_hashes: List[str] = []
        status_map = current_statuses if isinstance(current_statuses, dict) else {}

        for relation_hash in self._tokens(relation_hashes):
            snapshot = snapshots.get(relation_hash) if isinstance(snapshots, dict) else None
            if not isinstance(snapshot, dict) or not snapshot:
                failed_hashes.append(relation_hash)
                continue

            after_status = self.metadata_store.restore_relation_status_from_snapshot(relation_hash, snapshot)
            if after_status is None:
                failed_hashes.append(relation_hash)
                continue

            restored_hashes.append(relation_hash)
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="compensate_restore_relation",
                target_hash=relation_hash,
                before_payload=status_map.get(relation_hash, {}),
                after_payload=after_status,
                reason=reason,
            )

        if restored_hashes or failed_hashes:
            self._rebuild_graph_from_metadata()
            self._persist()

        return {
            "restored_hashes": restored_hashes,
            "failed_hashes": failed_hashes,
        }

    async def _ingest_feedback_relations(
        self,
        *,
        query_tool_id: str,
        session_id: str,
        relation_hashes: List[str],
        corrected_relations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        supersedes_hash = relation_hashes[0] if relation_hashes else ""
        relation_rows: List[Dict[str, Any]] = []
        for row in corrected_relations:
            relation_rows.append(
                {
                    "subject": str(row.get("subject", "") or "").strip(),
                    "predicate": str(row.get("predicate", "") or "").strip(),
                    "object": str(row.get("object", "") or "").strip(),
                    "confidence": float(row.get("confidence", 1.0) or 1.0),
                    "metadata": {
                        "supersedes_hash": supersedes_hash,
                        "supersedes_hashes": relation_hashes,
                        "from_query_tool_id": query_tool_id,
                        "feedback_window": self._feedback_cfg_window_label(),
                    },
                }
            )
        plain_text = "；".join(
            f"{item['subject']} {item['predicate']} {item['object']}"
            for item in relation_rows
            if item.get("subject") and item.get("predicate") and item.get("object")
        )
        external_id = compute_hash(
            "feedback_correction:"
            + query_tool_id
            + ":"
            + json.dumps(relation_rows, ensure_ascii=False, sort_keys=True)
        )
        payload = await self.ingest_text(
            external_id=external_id,
            source_type="chat_summary",
            text=plain_text,
            chat_id=session_id,
            relations=relation_rows,
            metadata={
                "from_query_tool_id": query_tool_id,
                "feedback_window": self._feedback_cfg_window_label(),
                "supersedes_hashes": relation_hashes,
                "feedback_correction_source": True,
            },
            respect_filter=False,
        )
        if isinstance(payload, dict):
            stored_ids = self._tokens(payload.get("stored_ids"))
            corrected_relation_hashes = stored_ids[1:]
            payload["external_id"] = external_id
            payload["source"] = self._chat_source(session_id)
            payload["paragraph_hashes"] = stored_ids[:1]
            payload["corrected_relation_hashes"] = corrected_relation_hashes
            base_success = bool(payload.get("success")) if "success" in payload else True
            payload["success"] = base_success and bool(corrected_relation_hashes)
            if not payload["success"] and not str(payload.get("error", "") or "").strip():
                payload["error"] = "missing_corrected_relations"
            return payload
        return {"success": False, "error": "invalid_ingest_payload"}

    async def _apply_feedback_decision(
        self,
        *,
        task_id: int,
        query_tool_id: str,
        session_id: str,
        decision: Dict[str, Any],
        hit_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        threshold = self._feedback_cfg_auto_apply_threshold()
        confidence = float(decision.get("confidence", 0.0) or 0.0)
        if confidence < threshold:
            return {
                "applied": False,
                "reason": "low_confidence",
                "threshold": threshold,
                "confidence": confidence,
            }

        decision_type = str(decision.get("decision", "none") or "none").strip().lower()
        if decision_type not in {"reject", "correct"}:
            return {
                "applied": False,
                "reason": f"decision_{decision_type}_no_auto_apply",
            }

        target_hashes = [
            str(item or "").strip()
            for item in (decision.get("target_hashes") or [])
            if str(item or "").strip()
        ]
        relation_hashes = self._resolve_feedback_relation_hashes(
            target_hashes=target_hashes,
            hit_map=hit_map,
        )
        if not relation_hashes:
            return {
                "applied": False,
                "reason": "no_relation_targets",
            }

        corrected_relations = [
            dict(item)
            for item in (decision.get("corrected_relations") or [])
            if isinstance(item, dict)
        ]
        if decision_type == "correct" and not corrected_relations:
            return {
                "applied": False,
                "reason": "missing_corrected_relations",
                "relation_hashes": relation_hashes,
                "stale_paragraph_hashes": [],
                "episode_rebuild_sources": [],
                "profile_refresh_person_ids": [],
                "rollback_plan_summary": {},
            }

        assert self.metadata_store is not None
        old_relation_rows = self._query_relation_rows_by_hashes(relation_hashes, include_inactive=True)
        before_status = self.metadata_store.get_relation_status_batch(relation_hashes)
        forget_result = self._apply_v5_relation_action(action="forget", hashes=relation_hashes, strength=1.0)
        forget_success = bool(forget_result.get("success"))
        after_status = self.metadata_store.get_relation_status_batch(relation_hashes)
        for hash_value in relation_hashes:
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="forget_relation",
                target_hash=hash_value,
                before_payload=before_status.get(hash_value) if isinstance(before_status, dict) else {},
                after_payload=after_status.get(hash_value) if isinstance(after_status, dict) else {},
                reason=str(decision.get("reason", "") or ""),
            )

        ingest_result = None
        corrected_relation_hash_candidates: List[str] = []
        corrected_relation_specs_by_hash: Dict[str, Dict[str, Any]] = {}
        if decision_type == "correct" and corrected_relations and self.metadata_store is not None:
            for item in corrected_relations:
                try:
                    relation_hash = self.metadata_store.compute_relation_hash(
                        str(item.get("subject", "") or "").strip(),
                        str(item.get("predicate", "") or "").strip(),
                        str(item.get("object", "") or "").strip(),
                    )
                except Exception:
                    continue
                if not relation_hash:
                    continue
                corrected_relation_hash_candidates.append(relation_hash)
                corrected_relation_specs_by_hash[relation_hash] = {
                    "subject": str(item.get("subject", "") or "").strip(),
                    "predicate": str(item.get("predicate", "") or "").strip(),
                    "object": str(item.get("object", "") or "").strip(),
                }
        corrected_relation_before_status = (
            self.metadata_store.get_relation_status_batch(corrected_relation_hash_candidates)
            if corrected_relation_hash_candidates
            else {}
        )
        if not forget_success:
            return {
                "applied": False,
                "reason": "forget_failed",
                "error": str(forget_result.get("error", "") or "forget_failed"),
                "forget": forget_result,
                "ingest": ingest_result,
                "relation_hashes": relation_hashes,
                "stale_paragraph_hashes": [],
                "episode_rebuild_sources": [],
                "profile_refresh_person_ids": [],
                "rollback_plan_summary": {},
            }

        stale_paragraph_map: Dict[str, List[str]] = {}
        stale_paragraph_hashes: List[str] = []
        episode_rebuild_sources: List[str] = []
        profile_refresh_person_ids: List[str] = []
        rollback_plan: Dict[str, Any] = {}
        if decision_type == "correct" and corrected_relations:
            ingest_result = await self._ingest_feedback_relations(
                query_tool_id=query_tool_id,
                session_id=session_id,
                relation_hashes=relation_hashes,
                corrected_relations=corrected_relations,
            )
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="ingest_correction",
                target_hash=relation_hashes[0] if relation_hashes else "",
                before_payload={"target_hashes": relation_hashes},
                after_payload=ingest_result,
                reason=str(decision.get("reason", "") or ""),
            )

            ingest_success = bool((ingest_result or {}).get("success")) if isinstance(ingest_result, dict) else False
            if not ingest_success:
                compensation_result = self._restore_feedback_relations_from_snapshots(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    relation_hashes=relation_hashes,
                    snapshots=before_status if isinstance(before_status, dict) else {},
                    current_statuses=after_status if isinstance(after_status, dict) else {},
                    reason=str(decision.get("reason", "") or "") or "feedback_correction_ingest_failed",
                )
                restore_failed_hashes = compensation_result.get("failed_hashes", [])
                return {
                    "applied": False,
                    "reason": "correction_restore_failed" if restore_failed_hashes else "correction_ingest_failed",
                    "error": str((ingest_result or {}).get("error", "") or "correction_ingest_failed"),
                    "forget": forget_result,
                    "ingest": ingest_result,
                    "relation_hashes": relation_hashes,
                    "stale_paragraph_hashes": [],
                    "episode_rebuild_sources": [],
                    "profile_refresh_person_ids": [],
                    "restored_relation_hashes": compensation_result.get("restored_hashes", []),
                    "restore_failed_hashes": restore_failed_hashes,
                    "rollback_plan_summary": {},
                }
        else:
            ingest_success = False

        applied = forget_success if decision_type == "reject" else (forget_success and ingest_success)
        if applied:
            stale_paragraph_map = self._mark_feedback_stale_paragraphs(
                task_id=task_id,
                query_tool_id=query_tool_id,
                relation_hashes=relation_hashes,
                reason=str(decision.get("reason", "") or "") or "feedback_correction",
            )
            stale_paragraph_hashes = self._merge_tokens(
                *[
                    paragraph_hashes
                    for paragraph_hashes in stale_paragraph_map.values()
                    if isinstance(paragraph_hashes, list)
                ]
            )
            episode_rebuild_sources = self._enqueue_feedback_episode_rebuilds(
                paragraph_hashes=stale_paragraph_hashes,
                session_id=session_id,
                include_correction_source=bool(ingest_success),
            )
            profile_refresh_person_ids = self._enqueue_feedback_profile_refreshes(
                person_ids=self._resolve_feedback_related_person_ids(
                    old_relation_rows=old_relation_rows,
                    corrected_relations=corrected_relations,
                ),
                query_tool_id=query_tool_id,
            )
            for relation_hash, paragraph_hashes in stale_paragraph_map.items():
                for paragraph_hash in paragraph_hashes:
                    self.metadata_store.append_feedback_action_log(
                        task_id=task_id,
                        query_tool_id=query_tool_id,
                        action_type="mark_stale_paragraph",
                        target_hash=paragraph_hash,
                        after_payload={"relation_hash": relation_hash},
                        reason=str(decision.get("reason", "") or ""),
                    )
            for source in episode_rebuild_sources:
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="enqueue_episode_rebuild",
                    target_hash=source,
                    reason=str(decision.get("reason", "") or ""),
                )
            for person_id in profile_refresh_person_ids:
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="enqueue_profile_refresh",
                    target_hash=person_id,
                    reason=str(decision.get("reason", "") or ""),
                )
            forgotten_relations = []
            for row in old_relation_rows:
                relation_hash = str(row.get("hash", "") or "").strip()
                if not relation_hash:
                    continue
                forgotten_relations.append(
                    {
                        "hash": relation_hash,
                        "subject": str(row.get("subject", "") or "").strip(),
                        "predicate": str(row.get("predicate", "") or "").strip(),
                        "object": str(row.get("object", "") or "").strip(),
                        "before_status": before_status.get(relation_hash) if isinstance(before_status, dict) else {},
                    }
                )

            corrected_write: Dict[str, Any] = {}
            if isinstance(ingest_result, dict):
                stored_relation_hashes = self._tokens(ingest_result.get("corrected_relation_hashes"))
                corrected_write = {
                    "external_id": str(ingest_result.get("external_id", "") or "").strip(),
                    "source": str(ingest_result.get("source", "") or "").strip(),
                    "paragraph_hashes": self._tokens(ingest_result.get("paragraph_hashes")),
                    "corrected_relation_hashes": stored_relation_hashes,
                    "corrected_relations": [
                        {
                            "hash": relation_hash,
                            **corrected_relation_specs_by_hash.get(relation_hash, {}),
                            "existed_before": relation_hash in corrected_relation_before_status,
                            "before_status": corrected_relation_before_status.get(relation_hash, {}),
                        }
                        for relation_hash in stored_relation_hashes
                    ],
                }

            rollback_plan = {
                "task_id": task_id,
                "query_tool_id": query_tool_id,
                "session_id": session_id,
                "decision_type": decision_type,
                "forgotten_relations": forgotten_relations,
                "corrected_write": corrected_write,
                "stale_marks": [
                    {
                        "paragraph_hash": paragraph_hash,
                        "relation_hash": relation_hash,
                        "source_type": "feedback_correction",
                        "source_id": str(task_id),
                        "source_operation_id": f"feedback_correction:{task_id}:{paragraph_hash}:{relation_hash}",
                    }
                    for relation_hash, paragraph_hashes in stale_paragraph_map.items()
                    for paragraph_hash in (paragraph_hashes or [])
                    if str(paragraph_hash or "").strip()
                ],
                "episode_sources": episode_rebuild_sources,
                "profile_person_ids": profile_refresh_person_ids,
                "created_at": time.time(),
            }
            update_rollback_plan = getattr(self.metadata_store, "update_feedback_task_rollback_plan", None)
            if callable(update_rollback_plan):
                update_rollback_plan(
                    task_id=task_id,
                    rollback_plan=rollback_plan,
                )
        return {
            "applied": applied,
            "forget": forget_result,
            "ingest": ingest_result,
            "relation_hashes": relation_hashes,
            "stale_paragraph_hashes": stale_paragraph_hashes,
            "episode_rebuild_sources": episode_rebuild_sources,
            "profile_refresh_person_ids": profile_refresh_person_ids,
            "rollback_plan_summary": self._build_feedback_rollback_plan_summary(rollback_plan) if rollback_plan else {},
        }

    def _resolve_feedback_relation_hashes(
        self,
        *,
        target_hashes: Sequence[str],
        hit_map: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        resolved: List[str] = []
        seen: set[str] = set()
        for target_hash in target_hashes:
            token = str(target_hash or "").strip()
            if not token:
                continue
            hit = hit_map.get(token) if isinstance(hit_map, dict) else None
            item_type = str((hit or {}).get("type", "") or "").strip()
            if item_type == "relation":
                if token not in seen:
                    seen.add(token)
                    resolved.append(token)
                continue
            if item_type != "paragraph":
                continue

            linked_candidates = self._tokens((hit or {}).get("linked_relation_hashes"))
            if not linked_candidates and self.metadata_store is not None:
                for relation in self.metadata_store.get_paragraph_relations(token):
                    linked_hash = str(relation.get("hash", "") or "").strip()
                    if linked_hash:
                        linked_candidates.append(linked_hash)

            for linked_hash in linked_candidates:
                if linked_hash in seen:
                    continue
                seen.add(linked_hash)
                resolved.append(linked_hash)
        return resolved

    async def _process_feedback_task(self, task: Dict[str, Any]) -> None:
        task_id = int(task.get("id") or 0)
        query_tool_id = str(task.get("query_tool_id", "") or "").strip()
        if task_id <= 0 or not query_tool_id:
            return

        assert self.metadata_store is not None
        self.metadata_store.mark_feedback_task_running(task_id)

        decision_payload: Dict[str, Any] = {}
        session_id = str(task.get("session_id", "") or "").strip()
        try:
            structured = task.get("query_snapshot") if isinstance(task.get("query_snapshot"), dict) else {}
            if not session_id:
                session_id = str(structured.get("chat_id", "") or "").strip()
            if not session_id:
                raise RuntimeError("反馈任务缺少 session_id")
            hits_raw = structured.get("hits")
            if not isinstance(hits_raw, list) or not hits_raw:
                decision_payload = {"decision": "none", "confidence": 1.0, "reason": "no_hits"}
                self.metadata_store.finalize_feedback_task(
                    task_id=task_id,
                    status="skipped",
                    decision_payload=decision_payload,
                )
                return

            query_timestamp = self._coerce_datetime(task.get("query_timestamp")) or datetime.now()
            due_at = self._coerce_datetime(task.get("due_at")) or (
                query_timestamp + timedelta(hours=self._feedback_cfg_window_hours())
            )
            if due_at <= query_timestamp:
                due_at = query_timestamp + timedelta(hours=self._feedback_cfg_window_hours())

            feedback_messages = self._extract_feedback_messages(
                session_id=session_id,
                query_time=query_timestamp,
                due_time=due_at,
                max_messages=self._feedback_cfg_max_messages(),
            )
            if not feedback_messages:
                decision_payload = {"decision": "none", "confidence": 1.0, "reason": "no_feedback_messages"}
                self.metadata_store.finalize_feedback_task(
                    task_id=task_id,
                    status="skipped",
                    decision_payload=decision_payload,
                )
                return

            if self._feedback_cfg_prefilter_enabled() and not self._should_invoke_feedback_classifier(feedback_messages):
                decision_payload = {"decision": "none", "confidence": 1.0, "reason": "prefilter_skipped"}
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="skip",
                    reason="prefilter_skipped",
                    after_payload={"feedback_messages": feedback_messages},
                )
                self.metadata_store.finalize_feedback_task(
                    task_id=task_id,
                    status="skipped",
                    decision_payload=decision_payload,
                )
                return

            hit_briefs = self._build_feedback_hit_briefs(hits_raw)
            hit_map = {str(item.get("hash", "") or "").strip(): item for item in hit_briefs if str(item.get("hash", "") or "").strip()}
            raw_decision = await self._classify_feedback(
                query_tool_id=query_tool_id,
                query_text=str(structured.get("query", "") or ""),
                hit_briefs=hit_briefs,
                feedback_messages=feedback_messages,
            )
            decision_payload = self._normalize_feedback_decision(raw_decision, hit_hashes=list(hit_map.keys()))
            decision_payload["feedback_message_count"] = len(feedback_messages)
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="classification",
                after_payload=decision_payload,
                reason=str(decision_payload.get("reason", "") or ""),
            )

            apply_result = await self._apply_feedback_decision(
                task_id=task_id,
                query_tool_id=query_tool_id,
                session_id=session_id,
                decision=decision_payload,
                hit_map=hit_map,
            )
            decision_payload["apply_result"] = apply_result
            final_status = self._feedback_apply_result_status(apply_result)
            self.metadata_store.finalize_feedback_task(
                task_id=task_id,
                status=final_status,
                decision_payload=decision_payload,
                last_error=str(apply_result.get("error", "") or "") if final_status == "error" else "",
            )
        except Exception as exc:
            logger.warning(f"反馈纠错任务处理失败: task_id={task_id} err={exc}", exc_info=True)
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="error",
                reason=str(exc),
                after_payload=decision_payload if decision_payload else None,
            )
            self.metadata_store.finalize_feedback_task(
                task_id=task_id,
                status="error",
                decision_payload=decision_payload if decision_payload else None,
                last_error=str(exc),
            )

    async def _feedback_correction_loop(self) -> None:
        try:
            while not self._background_stopping:
                interval_seconds = self._feedback_cfg_check_interval_seconds()
                if not self._feedback_cfg_enabled():
                    await asyncio.sleep(interval_seconds)
                    continue
                if self.metadata_store is None:
                    await asyncio.sleep(interval_seconds)
                    continue
                tasks = self.metadata_store.fetch_due_feedback_tasks(
                    limit=self._feedback_cfg_batch_size(),
                    now=datetime.now().timestamp(),
                )
                if not tasks:
                    await asyncio.sleep(interval_seconds)
                    continue
                for task in tasks:
                    if self._background_stopping:
                        break
                    if not isinstance(task, dict):
                        continue
                    await self._process_feedback_task(task)
                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"feedback_correction loop 异常: {exc}")

    async def _memory_maintenance_loop(self) -> None:
        try:
            while not self._background_stopping:
                interval_hours = max(1.0 / 60.0, float(self._cfg("memory.base_decay_interval_hours", 1.0) or 1.0))
                await asyncio.sleep(max(60.0, interval_hours * 3600.0))
                if self._background_stopping:
                    break
                if not bool(self._cfg("memory.enabled", True)):
                    continue
                await self._run_memory_maintenance_cycle(interval_hours=interval_hours)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"memory_maintenance loop 异常: {exc}")

    async def _run_memory_maintenance_cycle(self, *, interval_hours: float) -> None:
        assert self.graph_store is not None
        assert self.metadata_store is not None
        half_life = float(self._cfg("memory.half_life_hours", 24.0) or 24.0)
        if half_life > 0:
            factor = 0.5 ** (float(interval_hours) / half_life)
            self.graph_store.decay(factor)

        await self._process_freeze_and_prune()
        await self._orphan_gc_phase()
        self._last_maintenance_at = time.time()
        self._persist()

    async def _process_freeze_and_prune(self) -> None:
        assert self.metadata_store is not None
        assert self.graph_store is not None
        prune_threshold = max(0.0, float(self._cfg("memory.prune_threshold", 0.1) or 0.1))
        freeze_duration = max(0.0, float(self._cfg("memory.freeze_duration_hours", 24.0) or 24.0)) * 3600.0
        now = time.time()

        low_edges = self.graph_store.get_low_weight_edges(prune_threshold)
        hashes_to_freeze: List[str] = []
        edges_to_deactivate: List[tuple[str, str]] = []
        for src, tgt in low_edges:
            relation_hashes = list(self.graph_store.get_relation_hashes_for_edge(src, tgt))
            if not relation_hashes:
                continue
            statuses = self.metadata_store.get_relation_status_batch(relation_hashes)
            current_hashes: List[str] = []
            protected = False
            for hash_value, status in statuses.items():
                if bool(status.get("is_pinned")) or float(status.get("protected_until") or 0.0) > now:
                    protected = True
                    break
                current_hashes.append(hash_value)
            if protected or not current_hashes:
                continue
            hashes_to_freeze.extend(current_hashes)
            edges_to_deactivate.append((src, tgt))

        if hashes_to_freeze:
            self.metadata_store.mark_relations_inactive(hashes_to_freeze, inactive_since=now)
            self.graph_store.deactivate_edges(edges_to_deactivate)

        cutoff = now - freeze_duration
        expired_hashes = self.metadata_store.get_prune_candidates(cutoff)
        if not expired_hashes:
            return
        relation_info = self.metadata_store.get_relations_subject_object_map(expired_hashes)
        operations = [(src, tgt, hash_value) for hash_value, (src, tgt) in relation_info.items()]
        if operations:
            self.graph_store.prune_relation_hashes(operations)
        deleted_hashes = [hash_value for hash_value in expired_hashes if hash_value in relation_info]
        if deleted_hashes:
            self.metadata_store.backup_and_delete_relations(deleted_hashes)
            self._delete_vectors_by_type(relation_hashes=deleted_hashes)

    async def _orphan_gc_phase(self) -> None:
        assert self.metadata_store is not None
        assert self.graph_store is not None
        orphan_cfg = self._cfg("memory.orphan", {}) or {}
        if not bool(orphan_cfg.get("enable_soft_delete", True)):
            return
        entity_retention = max(0.0, float(orphan_cfg.get("entity_retention_days", 7.0) or 7.0)) * 86400.0
        paragraph_retention = max(0.0, float(orphan_cfg.get("paragraph_retention_days", 7.0) or 7.0)) * 86400.0
        grace_period = max(0.0, float(orphan_cfg.get("sweep_grace_hours", 24.0) or 24.0)) * 3600.0

        isolated = self.graph_store.get_isolated_nodes(include_inactive=True)
        if isolated:
            entity_hashes = self.metadata_store.get_entity_gc_candidates(isolated, retention_seconds=entity_retention)
            if entity_hashes:
                self.metadata_store.mark_as_deleted(entity_hashes, "entity")

        paragraph_hashes = self.metadata_store.get_paragraph_gc_candidates(retention_seconds=paragraph_retention)
        if paragraph_hashes:
            self.metadata_store.mark_as_deleted(paragraph_hashes, "paragraph")

        dead_paragraphs = self.metadata_store.sweep_deleted_items("paragraph", grace_period)
        if dead_paragraphs:
            hashes = [str(item[0] or "").strip() for item in dead_paragraphs if item and str(item[0] or "").strip()]
            if hashes:
                self.metadata_store.physically_delete_paragraphs(hashes)
                self._delete_vectors_by_type(paragraph_hashes=hashes)

        dead_entities = self.metadata_store.sweep_deleted_items("entity", grace_period)
        if dead_entities:
            entity_hashes = [str(item[0] or "").strip() for item in dead_entities if item and str(item[0] or "").strip()]
            entity_names = [str(item[1] or "").strip() for item in dead_entities if item and str(item[1] or "").strip()]
            if entity_names:
                self.graph_store.delete_nodes(entity_names)
            if entity_hashes:
                self.metadata_store.physically_delete_entities(entity_hashes)
                self._delete_vectors_by_type(entity_hashes=entity_hashes)

    def _mark_person_active(self, person_id: str) -> None:
        token = str(person_id or "").strip()
        if not token:
            return
        self._active_person_timestamps[token] = time.time()

    def _serialize_graph(self, *, limit: int = 200) -> Dict[str, Any]:
        assert self.graph_store is not None
        assert self.metadata_store is not None
        nodes = self.graph_store.get_nodes()
        if limit > 0:
            nodes = nodes[:limit]
        node_set = set(nodes)
        node_payload = []
        for name in nodes:
            attrs = self.graph_store.get_node_attributes(name) or {}
            node_payload.append({"id": name, "name": name, "attributes": attrs})

        edge_payload = []
        for source, target, relation_hashes in self.graph_store.iter_edge_hash_entries():
            if source not in node_set or target not in node_set:
                continue
            relation_hash_tokens = sorted(str(item) for item in relation_hashes if str(item).strip())
            relation_rows = self._query_relation_rows_by_hashes(relation_hash_tokens)
            predicates = self._dedupe_strings(row.get("predicate", "") for row in relation_rows)
            evidence_hashes = self._query_distinct_paragraph_hashes_for_relations(relation_hash_tokens)
            edge_payload.append(
                {
                    "source": source,
                    "target": target,
                    "weight": float(self.graph_store.get_edge_weight(source, target)),
                    "relation_hashes": relation_hash_tokens,
                    "predicates": predicates,
                    "relation_count": len(relation_hash_tokens),
                    "evidence_count": len(evidence_hashes),
                    "label": self._build_graph_edge_label(predicates),
                }
            )
        return {
            "nodes": node_payload,
            "edges": edge_payload,
            "total_nodes": int(self.graph_store.num_nodes),
            "total_edges": int(self.graph_store.num_edges),
        }

    @staticmethod
    def _graph_search_match_rank(value: str, keyword: str) -> Optional[int]:
        token = str(value or "").strip().lower()
        if not token or not keyword:
            return None
        if token == keyword:
            return 0
        if token.startswith(keyword):
            return 1
        if keyword in token:
            return 2
        return None

    @classmethod
    def _pick_graph_search_match(
        cls,
        fields: Sequence[tuple[str, str]],
        keyword: str,
    ) -> Optional[tuple[str, str, int]]:
        best_match: Optional[tuple[str, str, int]] = None
        for field, raw_value in fields:
            value = str(raw_value or "").strip()
            if not value:
                continue
            rank = cls._graph_search_match_rank(value, keyword)
            if rank is None:
                continue
            if best_match is None or rank < best_match[2]:
                best_match = (field, value, rank)
        return best_match

    def _search_graph(self, *, query: str, limit: int) -> Dict[str, Any]:
        assert self.metadata_store is not None
        token = str(query or "").strip()
        normalized_query = token.lower()
        safe_limit = max(1, int(limit or 50))
        if not token:
            return {
                "success": False,
                "query": token,
                "limit": safe_limit,
                "count": 0,
                "items": [],
                "error": "query 不能为空",
            }

        like_keyword = f"%{normalized_query}%"
        entity_rows = self.metadata_store.query(
            """
            SELECT hash, name, appearance_count, created_at
            FROM entities
            WHERE (is_deleted IS NULL OR is_deleted = 0)
              AND (
                LOWER(COALESCE(name, '')) LIKE ?
                OR LOWER(COALESCE(hash, '')) LIKE ?
              )
            """,
            (like_keyword, like_keyword),
        )

        relation_rows = self.metadata_store.query(
            """
            SELECT hash, subject, predicate, object, confidence, created_at
            FROM relations
            WHERE (is_inactive IS NULL OR is_inactive = 0)
              AND (
                LOWER(COALESCE(subject, '')) LIKE ?
                OR LOWER(COALESCE(object, '')) LIKE ?
                OR LOWER(COALESCE(predicate, '')) LIKE ?
                OR LOWER(COALESCE(hash, '')) LIKE ?
              )
            """,
            (like_keyword, like_keyword, like_keyword, like_keyword),
        )

        entity_items: List[Dict[str, Any]] = []
        seen_entity_keys: set[str] = set()
        for row in entity_rows:
            name = str(row.get("name", "") or "").strip()
            hash_value = str(row.get("hash", "") or "").strip()
            match = self._pick_graph_search_match(
                [("name", name), ("hash", hash_value)],
                normalized_query,
            )
            if match is None:
                continue
            dedupe_key = hash_value or f"name:{name.lower()}"
            if dedupe_key in seen_entity_keys:
                continue
            seen_entity_keys.add(dedupe_key)
            matched_field, matched_value, rank = match
            entity_items.append(
                {
                    "type": "entity",
                    "title": name or hash_value,
                    "matched_field": matched_field,
                    "matched_value": matched_value,
                    "entity_name": name or hash_value,
                    "entity_hash": hash_value,
                    "appearance_count": int(row.get("appearance_count", 0) or 0),
                    "_rank": rank,
                }
            )

        relation_items: List[Dict[str, Any]] = []
        seen_relation_keys: set[str] = set()
        for row in relation_rows:
            subject = str(row.get("subject", "") or "").strip()
            predicate = str(row.get("predicate", "") or "").strip()
            obj = str(row.get("object", "") or "").strip()
            relation_hash = str(row.get("hash", "") or "").strip()
            match = self._pick_graph_search_match(
                [
                    ("subject", subject),
                    ("object", obj),
                    ("predicate", predicate),
                    ("hash", relation_hash),
                ],
                normalized_query,
            )
            if match is None:
                continue
            dedupe_key = relation_hash or f"{subject.lower()}|{predicate.lower()}|{obj.lower()}"
            if dedupe_key in seen_relation_keys:
                continue
            seen_relation_keys.add(dedupe_key)
            matched_field, matched_value, rank = match
            relation_items.append(
                {
                    "type": "relation",
                    "title": self._format_relation_text(subject, predicate, obj),
                    "matched_field": matched_field,
                    "matched_value": matched_value,
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "relation_hash": relation_hash,
                    "confidence": float(row.get("confidence", 0.0) or 0.0),
                    "created_at": float(row.get("created_at", 0.0) or 0.0),
                    "_rank": rank,
                }
            )

        items = entity_items + relation_items
        items.sort(
            key=lambda item: (
                int(item["_rank"]) if item.get("_rank") is not None else 99,
                0 if str(item.get("type", "") or "") == "entity" else 1,
                -int(item.get("appearance_count", 0) or 0)
                if str(item.get("type", "") or "") == "entity"
                else -float(item.get("confidence", 0.0) or 0.0),
                0.0 if str(item.get("type", "") or "") == "entity" else -float(item.get("created_at", 0.0) or 0.0),
                str(item.get("entity_name", item.get("subject", "")) or "").lower(),
                str(item.get("predicate", "") or "").lower(),
                str(item.get("object", "") or "").lower(),
                str(item.get("entity_hash", item.get("relation_hash", "")) or "").lower(),
            )
        )

        normalized_items: List[Dict[str, Any]] = []
        for item in items[:safe_limit]:
            normalized = dict(item)
            normalized.pop("_rank", None)
            normalized_items.append(normalized)

        return {
            "success": True,
            "query": token,
            "limit": safe_limit,
            "count": len(normalized_items),
            "items": normalized_items,
        }

    @staticmethod
    def _dedupe_strings(values: Iterable[Any]) -> List[str]:
        deduped: List[str] = []
        for value in values:
            token = str(value or "").strip()
            if token and token not in deduped:
                deduped.append(token)
        return deduped

    @staticmethod
    def _build_graph_edge_label(predicates: Sequence[str]) -> str:
        labels = [str(item or "").strip() for item in predicates if str(item or "").strip()]
        if not labels:
            return ""
        if len(labels) == 1:
            return labels[0]
        return f"{labels[0]} +{len(labels) - 1}"

    @staticmethod
    def _trim_text(value: str, limit: int = 220) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."

    @staticmethod
    def _format_relation_text(subject: Any, predicate: Any, obj: Any) -> str:
        return " ".join(
            [
                str(subject or "").strip(),
                str(predicate or "").strip(),
                str(obj or "").strip(),
            ]
        ).strip()

    def _query_relation_rows_by_hashes(
        self,
        relation_hashes: Sequence[str],
        *,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        assert self.metadata_store is not None
        hashes = [str(item or "").strip() for item in relation_hashes if str(item or "").strip()]
        if not hashes:
            return []
        placeholders = ",".join(["?"] * len(hashes))
        inactive_clause = "" if include_inactive else "AND (is_inactive IS NULL OR is_inactive = 0)"
        rows = self.metadata_store.query(
            f"""
            SELECT hash, subject, predicate, object, confidence, created_at, source_paragraph
            FROM relations
            WHERE hash IN ({placeholders})
              {inactive_clause}
            """,
            tuple(hashes),
        )
        order = {hash_value: index for index, hash_value in enumerate(hashes)}
        rows.sort(key=lambda row: order.get(str(row.get("hash", "") or ""), len(order)))
        return rows

    def _query_distinct_paragraph_hashes_for_relations(
        self,
        relation_hashes: Sequence[str],
        *,
        limit: Optional[int] = None,
    ) -> List[str]:
        assert self.metadata_store is not None
        hashes = [str(item or "").strip() for item in relation_hashes if str(item or "").strip()]
        if not hashes:
            return []
        placeholders = ",".join(["?"] * len(hashes))
        sql = f"""
            SELECT DISTINCT p.hash, p.updated_at, p.created_at
            FROM paragraphs p
            JOIN paragraph_relations pr ON p.hash = pr.paragraph_hash
            WHERE pr.relation_hash IN ({placeholders})
              AND (p.is_deleted IS NULL OR p.is_deleted = 0)
            ORDER BY p.updated_at DESC, p.created_at DESC, p.hash ASC
        """
        params: List[Any] = list(hashes)
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.metadata_store.query(sql, tuple(params))
        return [str(row.get("hash", "") or "").strip() for row in rows if str(row.get("hash", "") or "").strip()]

    def _load_paragraph_rows(self, paragraph_hashes: Sequence[str]) -> List[Dict[str, Any]]:
        assert self.metadata_store is not None
        hashes = [str(item or "").strip() for item in paragraph_hashes if str(item or "").strip()]
        if not hashes:
            return []
        rows: List[Dict[str, Any]] = []
        for hash_value in hashes:
            row = self.metadata_store.get_paragraph(hash_value)
            if row is None:
                continue
            if bool(row.get("is_deleted", 0)):
                continue
            rows.append(row)
        return rows

    def _resolve_graph_node_name(self, node_id: str) -> str:
        assert self.metadata_store is not None
        assert self.graph_store is not None
        token = str(node_id or "").strip()
        if not token:
            return ""
        graph_nodes = self.graph_store.get_nodes()
        for candidate in graph_nodes:
            if str(candidate or "").strip().lower() == token.lower():
                return str(candidate)
        entity_rows = self.metadata_store.query(
            """
            SELECT name
            FROM entities
            WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
               OR hash = ?
            ORDER BY appearance_count DESC, created_at ASC
            LIMIT 1
            """,
            (token, token),
        )
        if entity_rows:
            return str(entity_rows[0].get("name", "") or token)
        relation_rows = self.metadata_store.query(
            """
            SELECT subject, object
            FROM relations
            WHERE (LOWER(TRIM(subject)) = LOWER(TRIM(?)) OR LOWER(TRIM(object)) = LOWER(TRIM(?)))
              AND (is_inactive IS NULL OR is_inactive = 0)
            LIMIT 1
            """,
            (token, token),
        )
        if relation_rows:
            subject = str(relation_rows[0].get("subject", "") or "").strip()
            obj = str(relation_rows[0].get("object", "") or "").strip()
            if subject.lower() == token.lower():
                return subject
            if obj.lower() == token.lower():
                return obj
        return token

    def _get_related_relation_rows_for_entity(self, entity_name: str, *, limit: int) -> List[Dict[str, Any]]:
        assert self.metadata_store is not None
        rows = self.metadata_store.query(
            """
            SELECT hash, subject, predicate, object, confidence, created_at, source_paragraph
            FROM relations
            WHERE (LOWER(TRIM(subject)) = LOWER(TRIM(?)) OR LOWER(TRIM(object)) = LOWER(TRIM(?)))
              AND (is_inactive IS NULL OR is_inactive = 0)
            ORDER BY confidence DESC, created_at DESC
            LIMIT ?
            """,
            (entity_name, entity_name, limit),
        )
        return rows

    def _build_relation_summary(self, row: Dict[str, Any], paragraph_hashes: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        relation_hash = str(row.get("hash", "") or "").strip()
        hashes = [str(item or "").strip() for item in (paragraph_hashes or []) if str(item or "").strip()]
        if not hashes and relation_hash:
            hashes = self._query_distinct_paragraph_hashes_for_relations([relation_hash])
        return {
            "hash": relation_hash,
            "subject": str(row.get("subject", "") or "").strip(),
            "predicate": str(row.get("predicate", "") or "").strip(),
            "object": str(row.get("object", "") or "").strip(),
            "text": self._format_relation_text(row.get("subject"), row.get("predicate"), row.get("object")),
            "confidence": float(row.get("confidence", 0.0) or 0.0),
            "paragraph_count": len(hashes),
            "paragraph_hashes": hashes,
            "source_paragraph": str(row.get("source_paragraph", "") or "").strip(),
        }

    def _build_paragraph_summary(self, row: Dict[str, Any]) -> Dict[str, Any]:
        assert self.metadata_store is not None
        paragraph_hash = str(row.get("hash", "") or "").strip()
        entities = self.metadata_store.get_paragraph_entities(paragraph_hash)
        relations = self.metadata_store.get_paragraph_relations(paragraph_hash)
        stale_marks_map, stale_status_map = self._load_paragraph_stale_marks([paragraph_hash])
        stale_marks = [
            {
                **mark,
                "relation_inactive": self._relation_status_is_inactive(
                    stale_status_map.get(str(mark.get("relation_hash", "") or "").strip())
                ),
            }
            for mark in stale_marks_map.get(paragraph_hash, [])
        ]
        return {
            "hash": paragraph_hash,
            "content": str(row.get("content", "") or ""),
            "preview": self._trim_text(str(row.get("content", "") or "")),
            "source": str(row.get("source", "") or ""),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "entity_count": len(entities),
            "relation_count": len(relations),
            "entities": self._dedupe_strings(entity.get("name", "") for entity in entities),
            "relations": [
                self._format_relation_text(
                    relation.get("subject", ""),
                    relation.get("predicate", ""),
                    relation.get("object", ""),
                )
                for relation in relations
            ],
            "is_stale": bool(stale_marks),
            "stale_relation_marks": stale_marks,
        }

    @staticmethod
    def _evidence_entity_node_id(name: str) -> str:
        return f"entity:{name}"

    @staticmethod
    def _evidence_relation_node_id(hash_value: str) -> str:
        return f"relation:{hash_value}"

    @staticmethod
    def _evidence_paragraph_node_id(hash_value: str) -> str:
        return f"paragraph:{hash_value}"

    def _build_evidence_graph(
        self,
        *,
        focus_entities: Sequence[str],
        relation_rows: Sequence[Dict[str, Any]],
        paragraph_rows: Sequence[Dict[str, Any]],
        node_limit: int,
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None

        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []
        edge_keys: set[tuple[str, str, str]] = set()
        relation_hash_set = {str(row.get("hash", "") or "").strip() for row in relation_rows if str(row.get("hash", "") or "").strip()}

        def add_node(node_id: str, *, node_type: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
            if not node_id or node_id in nodes:
                return
            nodes[node_id] = {
                "id": node_id,
                "type": node_type,
                "content": content,
                "metadata": metadata or {},
            }

        def add_edge(source: str, target: str, *, kind: str, label: str, weight: float = 1.0) -> None:
            key = (source, target, kind)
            if not source or not target or key in edge_keys:
                return
            edge_keys.add(key)
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "kind": kind,
                    "label": label,
                    "weight": float(weight or 1.0),
                }
            )

        for entity_name in self._dedupe_strings(focus_entities):
            add_node(
                self._evidence_entity_node_id(entity_name),
                node_type="entity",
                content=entity_name,
                metadata={"entity_name": entity_name},
            )

        for row in relation_rows:
            relation_hash = str(row.get("hash", "") or "").strip()
            if not relation_hash:
                continue
            subject = str(row.get("subject", "") or "").strip()
            obj = str(row.get("object", "") or "").strip()
            predicate = str(row.get("predicate", "") or "").strip()
            paragraph_hashes = self._query_distinct_paragraph_hashes_for_relations([relation_hash])
            add_node(
                self._evidence_relation_node_id(relation_hash),
                node_type="relation",
                content=self._format_relation_text(subject, predicate, obj),
                metadata={
                    "hash": relation_hash,
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "confidence": float(row.get("confidence", 0.0) or 0.0),
                    "paragraph_count": len(paragraph_hashes),
                    "paragraph_hashes": paragraph_hashes,
                    "text": self._format_relation_text(subject, predicate, obj),
                },
            )
            add_node(
                self._evidence_entity_node_id(subject),
                node_type="entity",
                content=subject,
                metadata={"entity_name": subject},
            )
            add_node(
                self._evidence_entity_node_id(obj),
                node_type="entity",
                content=obj,
                metadata={"entity_name": obj},
            )
            add_edge(
                self._evidence_relation_node_id(relation_hash),
                self._evidence_entity_node_id(subject),
                kind="subject",
                label="主语",
            )
            add_edge(
                self._evidence_relation_node_id(relation_hash),
                self._evidence_entity_node_id(obj),
                kind="object",
                label="宾语",
            )

        for paragraph in paragraph_rows:
            paragraph_hash = str(paragraph.get("hash", "") or "").strip()
            if not paragraph_hash:
                continue
            paragraph_entities = self.metadata_store.get_paragraph_entities(paragraph_hash)
            paragraph_relations = self.metadata_store.get_paragraph_relations(paragraph_hash)
            add_node(
                self._evidence_paragraph_node_id(paragraph_hash),
                node_type="paragraph",
                content=str(paragraph.get("content", "") or ""),
                metadata={
                    "hash": paragraph_hash,
                    "source": str(paragraph.get("source", "") or ""),
                    "updated_at": paragraph.get("updated_at"),
                    "entity_count": len(paragraph_entities),
                    "relation_count": len(paragraph_relations),
                    "preview": self._trim_text(str(paragraph.get("content", "") or "")),
                },
            )
            for entity in paragraph_entities:
                entity_name = str(entity.get("name", "") or "").strip()
                if not entity_name:
                    continue
                mention_count = int(entity.get("mention_count", 1) or 1)
                add_node(
                    self._evidence_entity_node_id(entity_name),
                    node_type="entity",
                    content=entity_name,
                    metadata={"entity_name": entity_name},
                )
                add_edge(
                    self._evidence_paragraph_node_id(paragraph_hash),
                    self._evidence_entity_node_id(entity_name),
                    kind="mentions",
                    label=f"提及 ×{mention_count}" if mention_count > 1 else "提及",
                    weight=float(max(1, mention_count)),
                )
            for relation in paragraph_relations:
                relation_hash = str(relation.get("hash", "") or "").strip()
                if relation_hash not in relation_hash_set:
                    continue
                add_edge(
                    self._evidence_paragraph_node_id(paragraph_hash),
                    self._evidence_relation_node_id(relation_hash),
                    kind="supports",
                    label="支撑",
                )

        if len(nodes) > node_limit:
            priority = {"entity": 0, "relation": 1, "paragraph": 2}
            kept_ids = {
                node["id"]
                for node in sorted(
                    nodes.values(),
                    key=lambda node: (
                        priority.get(str(node.get("type", "")), 9),
                        str(node.get("id", "")),
                    ),
                )[:node_limit]
            }
            nodes = {node_id: node for node_id, node in nodes.items() if node_id in kept_ids}
            edges = [edge for edge in edges if edge["source"] in nodes and edge["target"] in nodes]

        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "focus_entities": self._dedupe_strings(focus_entities),
        }

    def _build_graph_node_detail(
        self,
        *,
        node_id: str,
        relation_limit: int,
        paragraph_limit: int,
        evidence_node_limit: int,
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        resolved_name = self._resolve_graph_node_name(node_id)
        if not resolved_name:
            return {"success": False, "error": "node_id 不能为空"}

        entity_row = None
        entity_matches = self.metadata_store.query(
            """
            SELECT *
            FROM entities
            WHERE (LOWER(TRIM(name)) = LOWER(TRIM(?))
               OR hash = ?)
              AND (is_deleted IS NULL OR is_deleted = 0)
            ORDER BY appearance_count DESC, created_at ASC
            LIMIT 1
            """,
            (resolved_name, resolved_name),
        )
        if entity_matches and hasattr(self.metadata_store, "_row_to_dict"):
            entity_row = self.metadata_store._row_to_dict(entity_matches[0], "entity")

        relation_rows = self._get_related_relation_rows_for_entity(resolved_name, limit=relation_limit)
        if not relation_rows and entity_row is None:
            return {"success": False, "error": f"未找到节点: {resolved_name}"}

        relation_hashes = [str(row.get("hash", "") or "").strip() for row in relation_rows if str(row.get("hash", "") or "").strip()]
        direct_paragraph_rows = self.metadata_store.get_paragraphs_by_entity(resolved_name)
        relation_paragraph_hashes = self._query_distinct_paragraph_hashes_for_relations(relation_hashes)
        relation_paragraph_rows = self._load_paragraph_rows(relation_paragraph_hashes)
        paragraph_rows_map: Dict[str, Dict[str, Any]] = {}
        for row in direct_paragraph_rows + relation_paragraph_rows:
            paragraph_hash = str(row.get("hash", "") or "").strip()
            if paragraph_hash and not bool(row.get("is_deleted", 0)):
                paragraph_rows_map[paragraph_hash] = row
        paragraph_rows = list(paragraph_rows_map.values())
        paragraph_rows.sort(key=lambda row: (float(row.get("updated_at", 0) or 0), float(row.get("created_at", 0) or 0)), reverse=True)
        paragraph_rows = paragraph_rows[:paragraph_limit]

        relation_summaries = []
        for row in relation_rows:
            relation_hash = str(row.get("hash", "") or "").strip()
            relation_summaries.append(
                self._build_relation_summary(
                    row,
                    paragraph_hashes=self._query_distinct_paragraph_hashes_for_relations([relation_hash]),
                )
            )

        paragraph_summaries = [self._build_paragraph_summary(row) for row in paragraph_rows]
        evidence_graph = self._build_evidence_graph(
            focus_entities=[resolved_name],
            relation_rows=relation_rows,
            paragraph_rows=paragraph_rows,
            node_limit=evidence_node_limit,
        )

        return {
            "success": True,
            "node": {
                "id": resolved_name,
                "type": "entity",
                "content": resolved_name,
                "hash": str(entity_row.get("hash", "") or "") if isinstance(entity_row, dict) else "",
                "appearance_count": int(entity_row.get("appearance_count", 0) or 0) if isinstance(entity_row, dict) else 0,
            },
            "relations": relation_summaries,
            "paragraphs": paragraph_summaries,
            "evidence_graph": evidence_graph,
        }

    def _build_graph_edge_detail(
        self,
        *,
        source: str,
        target: str,
        paragraph_limit: int,
        evidence_node_limit: int,
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        source_name = self._resolve_graph_node_name(source)
        target_name = self._resolve_graph_node_name(target)
        if not source_name or not target_name:
            return {"success": False, "error": "source/target 不能为空"}

        relation_rows = self.metadata_store.query(
            """
            SELECT hash, subject, predicate, object, confidence, created_at, source_paragraph
            FROM relations
            WHERE LOWER(TRIM(subject)) = LOWER(TRIM(?))
              AND LOWER(TRIM(object)) = LOWER(TRIM(?))
              AND (is_inactive IS NULL OR is_inactive = 0)
            ORDER BY confidence DESC, created_at DESC
            """,
            (source_name, target_name),
        )
        if not relation_rows:
            return {"success": False, "error": f"未找到边: {source_name} -> {target_name}"}

        relation_hashes = [str(row.get("hash", "") or "").strip() for row in relation_rows if str(row.get("hash", "") or "").strip()]
        paragraph_hashes = self._query_distinct_paragraph_hashes_for_relations(relation_hashes, limit=paragraph_limit)
        paragraph_rows = self._load_paragraph_rows(paragraph_hashes)
        relation_summaries = [
            self._build_relation_summary(
                row,
                paragraph_hashes=self._query_distinct_paragraph_hashes_for_relations([str(row.get("hash", "") or "").strip()]),
            )
            for row in relation_rows
        ]
        paragraph_summaries = [self._build_paragraph_summary(row) for row in paragraph_rows]
        predicates = self._dedupe_strings(row.get("predicate", "") for row in relation_rows)
        evidence_graph = self._build_evidence_graph(
            focus_entities=[source_name, target_name],
            relation_rows=relation_rows,
            paragraph_rows=paragraph_rows,
            node_limit=evidence_node_limit,
        )
        return {
            "success": True,
            "edge": {
                "source": source_name,
                "target": target_name,
                "weight": float(self.graph_store.get_edge_weight(source_name, target_name)) if self.graph_store is not None else 0.0,
                "relation_hashes": relation_hashes,
                "predicates": predicates,
                "relation_count": len(relation_hashes),
                "evidence_count": len(paragraph_hashes),
                "label": self._build_graph_edge_label(predicates),
            },
            "relations": relation_summaries,
            "paragraphs": paragraph_summaries,
            "evidence_graph": evidence_graph,
        }

    def _delete_sources(self, sources: Iterable[Any]) -> Dict[str, Any]:
        assert self.metadata_store
        source_tokens = self._tokens(sources)
        if not source_tokens:
            return {"success": False, "error": "source 不能为空"}

        deleted_paragraphs = 0
        deleted_sources: List[str] = []
        for source in source_tokens:
            paragraphs = self.metadata_store.get_paragraphs_by_source(source)
            if not paragraphs:
                self.metadata_store.replace_episodes_for_source(source, [])
                continue
            for row in paragraphs:
                paragraph_hash = str(row.get("hash", "") or "").strip()
                if not paragraph_hash:
                    continue
                cleanup = self.metadata_store.delete_paragraph_atomic(paragraph_hash)
                self._apply_cleanup_plan(cleanup)
                deleted_paragraphs += 1
            self.metadata_store.replace_episodes_for_source(source, [])
            deleted_sources.append(source)

        self._rebuild_graph_from_metadata()
        self._persist()
        return {
            "success": True,
            "sources": deleted_sources,
            "deleted_source_count": len(deleted_sources),
            "deleted_paragraph_count": deleted_paragraphs,
        }

    def _apply_cleanup_plan(self, cleanup: Dict[str, Any]) -> None:
        if not isinstance(cleanup, dict):
            return
        paragraph_hash = str(cleanup.get("vector_id_to_remove", "") or "").strip()
        relation_hashes = [
            str(relation_hash or "").strip()
            for _, _, relation_hash in cleanup.get("relation_prune_ops", []) or []
            if str(relation_hash or "").strip()
        ]
        self._delete_vectors_by_type(
            paragraph_hashes=[paragraph_hash] if paragraph_hash else [],
            relation_hashes=relation_hashes,
        )

    def _rebuild_graph_from_metadata(self) -> Dict[str, int]:
        assert self.metadata_store is not None
        assert self.graph_store is not None
        entity_rows = self.metadata_store.query(
            """
            SELECT name
            FROM entities
            WHERE is_deleted IS NULL OR is_deleted = 0
            ORDER BY name ASC
            """
        )
        raw_relation_rows = self.metadata_store.query(
            """
            SELECT subject, object, confidence, hash
            FROM relations
            WHERE is_inactive IS NULL OR is_inactive = 0
            """
        )
        relation_rows = [
            row
            for row in raw_relation_rows
            if str(row.get("subject", "") or "").strip() and str(row.get("object", "") or "").strip()
        ]

        names = list(
            dict.fromkeys(
                [
                    str(row.get("name", "") or "").strip()
                    for row in entity_rows
                    if str(row.get("name", "") or "").strip()
                ]
                + [
                    str(row.get("subject", "") or "").strip()
                    for row in relation_rows
                    if str(row.get("subject", "") or "").strip()
                ]
                + [
                    str(row.get("object", "") or "").strip()
                    for row in relation_rows
                    if str(row.get("object", "") or "").strip()
                ]
            )
        )
        self.graph_store.clear()
        if names:
            self.graph_store.add_nodes(names)
        if relation_rows:
            self.graph_store.add_edges(
                [
                    (
                        str(row.get("subject", "") or "").strip(),
                        str(row.get("object", "") or "").strip(),
                    )
                    for row in relation_rows
                ],
                weights=[float(row.get("confidence", 1.0) or 1.0) for row in relation_rows],
                relation_hashes=[str(row.get("hash", "") or "") for row in relation_rows],
            )
        return {"node_count": int(self.graph_store.num_nodes), "edge_count": int(self.graph_store.num_edges)}

    def _rename_node(self, old_name: str, new_name: str) -> Dict[str, Any]:
        assert self.metadata_store
        source = str(old_name or "").strip()
        target = str(new_name or "").strip()
        if not source or not target:
            return {"success": False, "error": "old_name/new_name 不能为空"}
        if source == target:
            return {"success": True, "renamed": False, "old_name": source, "new_name": target}

        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        old_hash = compute_hash(source.lower())
        target_hash = compute_hash(target.lower())

        cursor.execute(
            """
            SELECT hash, name, vector_index, appearance_count, created_at, metadata
            FROM entities
            WHERE hash = ?
               OR LOWER(TRIM(name)) = LOWER(TRIM(?))
            LIMIT 1
            """,
            (old_hash, source),
        )
        old_row = cursor.fetchone()
        if old_row is None:
            return {"success": False, "error": "原节点不存在"}

        cursor.execute(
            """
            SELECT hash, appearance_count
            FROM entities
            WHERE hash = ?
               OR LOWER(TRIM(name)) = LOWER(TRIM(?))
            LIMIT 1
            """,
            (target_hash, target),
        )
        target_row = cursor.fetchone()

        try:
            cursor.execute("BEGIN IMMEDIATE")
            if target_row is None:
                cursor.execute(
                    """
                    INSERT INTO entities (hash, name, vector_index, appearance_count, created_at, metadata, is_deleted, deleted_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
                    """,
                    (
                        target_hash,
                        target,
                        old_row["vector_index"],
                        old_row["appearance_count"],
                        old_row["created_at"],
                        old_row["metadata"],
                    ),
                )
                resolved_target_hash = target_hash
            else:
                resolved_target_hash = str(target_row["hash"] or "").strip()
                cursor.execute(
                    """
                    UPDATE entities
                    SET name = ?,
                        appearance_count = COALESCE(appearance_count, 0) + ?,
                        is_deleted = 0,
                        deleted_at = NULL
                    WHERE hash = ?
                    """,
                    (
                        target,
                        int(old_row["appearance_count"] or 0),
                        resolved_target_hash,
                    ),
                )

            cursor.execute(
                "UPDATE OR IGNORE paragraph_entities SET entity_hash = ? WHERE entity_hash = ?",
                (resolved_target_hash, old_row["hash"]),
            )
            cursor.execute("DELETE FROM paragraph_entities WHERE entity_hash = ?", (old_row["hash"],))
            cursor.execute(
                "UPDATE relations SET subject = ? WHERE LOWER(TRIM(subject)) = LOWER(TRIM(?))",
                (target, old_row["name"]),
            )
            cursor.execute(
                "UPDATE relations SET object = ? WHERE LOWER(TRIM(object)) = LOWER(TRIM(?))",
                (target, old_row["name"]),
            )
            cursor.execute("DELETE FROM entities WHERE hash = ?", (old_row["hash"],))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            return {"success": False, "error": f"rename failed: {exc}"}

        self._rebuild_graph_from_metadata()
        self._persist()
        return {"success": True, "renamed": True, "old_name": source, "new_name": target}

    def _update_edge_weight(
        self,
        *,
        relation_hash: str,
        subject: str,
        obj: str,
        weight: float,
    ) -> Dict[str, Any]:
        assert self.metadata_store
        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        target_weight = max(0.0, float(weight or 0.0))
        if relation_hash:
            cursor.execute("UPDATE relations SET confidence = ? WHERE hash = ?", (target_weight, relation_hash))
            updated = cursor.rowcount
        else:
            cursor.execute(
                """
                UPDATE relations
                SET confidence = ?
                WHERE LOWER(TRIM(subject)) = LOWER(TRIM(?))
                  AND LOWER(TRIM(object)) = LOWER(TRIM(?))
                """,
                (target_weight, subject, obj),
            )
            updated = cursor.rowcount
        conn.commit()
        if updated <= 0:
            return {"success": False, "error": "未找到可更新的关系"}
        self._rebuild_graph_from_metadata()
        self._persist()
        return {
            "success": True,
            "updated": int(updated),
            "weight": target_weight,
            "hash": relation_hash,
            "subject": subject,
            "object": obj,
        }

    @staticmethod
    def _tokens(values: Optional[Iterable[Any]]) -> List[str]:
        result: List[str] = []
        seen = set()
        for item in values or []:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            result.append(token)
        return result

    @classmethod
    def _merge_tokens(cls, *groups: Optional[Iterable[Any]]) -> List[str]:
        merged: List[str] = []
        seen = set()
        for group in groups:
            for item in cls._tokens(group):
                if item in seen:
                    continue
                seen.add(item)
                merged.append(item)
        return merged

    @classmethod
    def _argument_tokens(cls, value: Any) -> List[str]:
        if isinstance(value, str):
            return cls._tokens([value])
        return cls._tokens(value)

    @classmethod
    def _merge_argument_tokens(cls, *groups: Any) -> List[str]:
        merged: List[str] = []
        seen = set()
        for group in groups:
            for item in cls._argument_tokens(group):
                if item in seen:
                    continue
                seen.add(item)
                merged.append(item)
        return merged

    @staticmethod
    def _build_source(source_type: str, chat_id: str, person_ids: Sequence[str]) -> str:
        clean_type = str(source_type or "").strip() or "memory"
        if clean_type == "chat_summary" and chat_id:
            return f"chat_summary:{chat_id}"
        if clean_type == "person_fact" and person_ids:
            return f"person_fact:{person_ids[0]}"
        return f"{clean_type}:{chat_id}" if chat_id else clean_type

    @staticmethod
    def _chat_source(chat_id: str) -> Optional[str]:
        clean = str(chat_id or "").strip()
        return f"chat_summary:{clean}" if clean else None

    @classmethod
    def _chat_source_for_search_scope(cls, chat_id: str, shared_chat_ids: Sequence[str] = ()) -> Optional[str]:
        allowed_chat_ids = cls._resolve_allowed_chat_ids(chat_id, shared_chat_ids)
        if len(allowed_chat_ids) > 1:
            return None
        return cls._chat_source(chat_id)

    @staticmethod
    def _scoped_search_limit(limit: int, *, chat_id: str, shared_chat_ids: Sequence[str] = ()) -> int:
        safe_limit = max(1, int(limit or 5))
        allowed_chat_ids = SDKMemoryKernel._resolve_allowed_chat_ids(chat_id, shared_chat_ids)
        if not allowed_chat_ids:
            return safe_limit
        multiplier = max(5, len(allowed_chat_ids) * 5)
        return min(50, max(safe_limit, safe_limit * multiplier))

    @classmethod
    def _resolve_allowed_chat_ids(cls, chat_id: str, shared_chat_ids: Sequence[str] = ()) -> set[str]:
        allowed_chat_ids = {str(item or "").strip() for item in shared_chat_ids if str(item or "").strip()}
        clean_chat_id = str(chat_id or "").strip()
        if clean_chat_id:
            allowed_chat_ids.add(clean_chat_id)
        return allowed_chat_ids

    @staticmethod
    def _rank_score_from_item(item: Any) -> float:
        if isinstance(item, dict):
            raw_score = item.get("score", item.get("final_score", item.get("relevance", 0.0)))
        else:
            raw_score = getattr(item, "score", 0.0)
        try:
            return float(raw_score or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _dedupe_ranked_items(cls, items: Sequence[Any], *, limit: int) -> List[Any]:
        ranked: Dict[str, Any] = {}
        for index, item in enumerate(items):
            if isinstance(item, dict):
                item_hash = str(item.get("hash", "") or "").strip()
                item_type = str(item.get("type", "") or "").strip()
                content = str(item.get("content", "") or "").strip()
            else:
                item_hash = str(getattr(item, "hash_value", "") or "").strip()
                item_type = str(getattr(item, "result_type", "") or "").strip()
                content = str(getattr(item, "content", "") or "").strip()
            key = item_hash or f"{item_type}:{content}"
            if not key:
                key = f"item:{index}"
            current = ranked.get(key)
            if current is None or cls._rank_score_from_item(item) > cls._rank_score_from_item(current):
                ranked[key] = item
        return sorted(ranked.values(), key=cls._rank_score_from_item, reverse=True)[: max(1, int(limit or 5))]

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

    @classmethod
    def _paragraph_matches_chat_scope(cls, paragraph: Optional[Dict[str, Any]], allowed_chat_ids: set[str]) -> bool:
        if not paragraph:
            return False

        if not allowed_chat_ids:
            return True

        metadata = coerce_metadata_dict(paragraph.get("metadata"))
        if cls._metadata_chat_scope_ids(metadata) & allowed_chat_ids:
            return True

        source = str(paragraph.get("source", "") or metadata.get("source", "") or "").strip()
        return any(source == str(cls._chat_source(allowed_chat_id) or "") for allowed_chat_id in allowed_chat_ids)

    @classmethod
    def _hit_metadata_matches_chat_scope(cls, hit: Dict[str, Any], allowed_chat_ids: set[str]) -> Optional[bool]:
        if not allowed_chat_ids:
            return True

        metadata = coerce_metadata_dict(hit.get("metadata"))
        hit_type = str(hit.get("type", "") or "").strip()
        metadata_chat_ids = cls._metadata_chat_scope_ids(metadata)
        if metadata_chat_ids:
            if metadata_chat_ids & allowed_chat_ids:
                return True
            if hit_type in {"paragraph", "relation"}:
                return None
            return False

        source = str(metadata.get("source", "") or hit.get("source", "") or "").strip()
        chat_sources = {str(cls._chat_source(allowed_chat_id) or "") for allowed_chat_id in allowed_chat_ids}
        if hit_type == "episode":
            return source in chat_sources
        if source.startswith("chat_summary:"):
            return source in chat_sources
        return None

    @staticmethod
    def _extend_chat_scope_ids(tokens: set[str], value: Any) -> None:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                SDKMemoryKernel._extend_chat_scope_ids(tokens, item)
            return

        token = str(value or "").strip()
        if token:
            tokens.add(token)

    @classmethod
    def _metadata_chat_scope_ids(cls, metadata: Dict[str, Any]) -> set[str]:
        tokens: set[str] = set()
        for key in ("chat_id", "session_id", "stream_id", "chat_ids", "session_ids", "stream_ids"):
            cls._extend_chat_scope_ids(tokens, metadata.get(key))
        return tokens

    def _filter_hits_by_chat_scope(
        self,
        hits: List[Dict[str, Any]],
        chat_id: str,
        shared_chat_ids: Sequence[str] = (),
    ) -> List[Dict[str, Any]]:
        allowed_chat_ids = self._resolve_allowed_chat_ids(chat_id, shared_chat_ids)
        if not allowed_chat_ids or self.metadata_store is None:
            return hits

        allowed_indexes: set[int] = set()
        unresolved_paragraph_hashes: List[str] = []
        unresolved_relation_hashes: List[str] = []
        pending_indexes: Dict[int, Dict[str, str]] = {}

        for index, item in enumerate(hits):
            hit = dict(item)
            hit_type = str(hit.get("type", "") or "").strip()
            metadata_decision = self._hit_metadata_matches_chat_scope(hit, allowed_chat_ids)
            if metadata_decision is True:
                allowed_indexes.add(index)
                continue
            if metadata_decision is False:
                continue

            hit_hash = str(hit.get("hash", "") or "").strip()
            if hit_type == "paragraph" and hit_hash:
                unresolved_paragraph_hashes.append(hit_hash)
                pending_indexes[index] = {"type": hit_type, "hash": hit_hash}
                continue
            if hit_type == "relation" and hit_hash:
                unresolved_relation_hashes.append(hit_hash)
                pending_indexes[index] = {"type": hit_type, "hash": hit_hash}

        paragraph_map = self.metadata_store.get_paragraphs_by_hashes(unresolved_paragraph_hashes)
        relation_paragraph_map = self.metadata_store.get_paragraphs_by_relation_hashes(unresolved_relation_hashes)
        for index, pending in pending_indexes.items():
            hit_hash = pending["hash"]
            if pending["type"] == "paragraph":
                if self._paragraph_matches_chat_scope(paragraph_map.get(hit_hash), allowed_chat_ids):
                    allowed_indexes.add(index)
                continue
            if any(
                self._paragraph_matches_chat_scope(paragraph, allowed_chat_ids)
                for paragraph in relation_paragraph_map.get(hit_hash, [])
            ):
                allowed_indexes.add(index)

        return [dict(hit) for index, hit in enumerate(hits) if index in allowed_indexes]

    def _filter_hits_by_retrieval_type_scope(
        self,
        hits: List[Dict[str, Any]],
        *,
        current_stream_id: str = "",
        current_group_id: str = "",
        current_user_id: str = "",
    ) -> List[Dict[str, Any]]:
        """按检索结果类型应用跨聊天流过滤，不改变本聊天流读取自身记忆。"""

        if not hits or not self._has_enabled_retrieval_type_filter():
            return hits
        current_context = self._current_retrieval_filter_context(
            stream_id=current_stream_id,
            group_id=current_group_id,
            user_id=current_user_id,
        )

        paragraph_hashes: List[str] = []
        relation_hashes: List[str] = []
        for item in hits:
            item_type = str(item.get("type", "") or "").strip()
            item_hash = str(item.get("hash", "") or "").strip()
            if not item_hash:
                continue
            if item_type == "paragraph":
                paragraph_hashes.append(item_hash)
            elif item_type == "relation":
                relation_hashes.append(item_hash)

        paragraph_map: Dict[str, Dict[str, Any]] = {}
        relation_paragraph_map: Dict[str, List[Dict[str, Any]]] = {}
        if self.metadata_store is not None:
            paragraph_map = self.metadata_store.get_paragraphs_by_hashes(paragraph_hashes)
            relation_paragraph_map = self.metadata_store.get_paragraphs_by_relation_hashes(relation_hashes)

        filtered: List[Dict[str, Any]] = []
        for item in hits:
            contexts = self._retrieval_filter_contexts_for_hit(
                item,
                paragraph_map=paragraph_map,
                relation_paragraph_map=relation_paragraph_map,
            )
            if any(
                self._retrieval_filter_context_is_current_source(context, current_context)
                for context in contexts
            ):
                filtered.append(dict(item))
                continue
            if any(self._retrieval_filter_context_allowed(context) for context in contexts):
                filtered.append(dict(item))
        return filtered

    def _has_enabled_retrieval_type_filter(self) -> bool:
        retrieval_config = self._retrieval_type_filter_root()
        if not retrieval_config:
            return False
        for kind in ("chat_stream", "chat_summary", "episode"):
            type_config = retrieval_config.get(kind)
            if isinstance(type_config, dict) and bool(type_config.get("enabled", False)):
                return True
        return False

    def _retrieval_type_filter_root(self) -> Dict[str, Any]:
        filter_config = self._cfg("filter", {}) or {}
        if not isinstance(filter_config, dict):
            return {}
        retrieval_config = filter_config.get("retrieval") or {}
        return retrieval_config if isinstance(retrieval_config, dict) else {}

    def _retrieval_type_filter_config(self, kind: str) -> Dict[str, Any]:
        retrieval_config = self._retrieval_type_filter_root()
        type_config = retrieval_config.get(str(kind or "").strip())
        return type_config if isinstance(type_config, dict) else {}

    def _retrieval_filter_contexts_for_hit(
        self,
        hit: Dict[str, Any],
        *,
        paragraph_map: Dict[str, Dict[str, Any]],
        relation_paragraph_map: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, str]]:
        hit_type = str(hit.get("type", "") or "").strip()
        hit_hash = str(hit.get("hash", "") or "").strip()

        if hit_type == "paragraph" and hit_hash in paragraph_map:
            return [self._retrieval_filter_context_from_paragraph(paragraph_map[hit_hash])]

        if hit_type == "relation" and hit_hash in relation_paragraph_map:
            contexts = [
                self._retrieval_filter_context_from_paragraph(paragraph)
                for paragraph in relation_paragraph_map.get(hit_hash, [])
                if isinstance(paragraph, dict)
            ]
            if contexts:
                return contexts

        return [self._retrieval_filter_context_from_hit(hit)]

    def _retrieval_filter_context_from_hit(self, hit: Dict[str, Any]) -> Dict[str, str]:
        metadata = coerce_metadata_dict(hit.get("metadata"))
        source = str(metadata.get("source", "") or hit.get("source", "") or "").strip()
        source_type = str(metadata.get("source_type", "") or "").strip()
        hit_type = str(hit.get("type", "") or "").strip()
        stream_id = str(metadata.get("chat_id", "") or "").strip()
        if not stream_id:
            stream_id = self._source_stream_id(source)
        return self._retrieval_filter_context(
            kind=self._retrieval_filter_kind(hit_type=hit_type, source_type=source_type, source=source),
            stream_id=stream_id,
        )

    def _retrieval_filter_context_from_paragraph(self, paragraph: Dict[str, Any]) -> Dict[str, str]:
        metadata = coerce_metadata_dict(paragraph.get("metadata"))
        source = str(paragraph.get("source", "") or metadata.get("source", "") or "").strip()
        source_type = str(metadata.get("source_type", "") or "").strip()
        stream_id = str(metadata.get("chat_id", "") or "").strip()
        if not stream_id:
            stream_id = self._source_stream_id(source)
        return self._retrieval_filter_context(
            kind=self._retrieval_filter_kind(hit_type="paragraph", source_type=source_type, source=source),
            stream_id=stream_id,
        )

    @staticmethod
    def _retrieval_filter_kind(*, hit_type: str, source_type: str, source: str) -> str:
        if str(hit_type or "").strip() == "episode":
            return "episode"
        clean_source_type = str(source_type or "").strip()
        clean_source = str(source or "").strip()
        if clean_source_type == "chat_summary" or clean_source.startswith("chat_summary:"):
            return "chat_summary"
        if clean_source_type in {"chat_history", "chat_stream", "maibot.chat_history"}:
            return "chat_stream"
        if clean_source.startswith("chat_stream:") or clean_source.startswith("maibot.chat_history:"):
            return "chat_stream"
        return ""

    @staticmethod
    def _source_stream_id(source: str) -> str:
        token = str(source or "").strip()
        for prefix in ("chat_summary:", "chat_stream:", "maibot.chat_history:"):
            if token.startswith(prefix):
                return token[len(prefix):].strip()
        return ""

    @staticmethod
    def _retrieval_filter_context(*, kind: str, stream_id: str) -> Dict[str, str]:
        stream_token = str(stream_id or "").strip()
        group_id = ""
        user_id = ""
        if stream_token:
            session = chat_manager.get_existing_session_by_session_id(stream_token)
            if session is not None:
                group_id = str(getattr(session, "group_id", "") or "").strip()
                user_id = str(getattr(session, "user_id", "") or "").strip()
        return {
            "kind": str(kind or "").strip(),
            "stream_id": stream_token,
            "group_id": group_id,
            "user_id": user_id,
        }

    def _current_retrieval_filter_context(
        self,
        *,
        stream_id: str,
        group_id: str,
        user_id: str,
    ) -> Dict[str, str]:
        resolved_context = self._retrieval_filter_context(kind="", stream_id=stream_id)
        resolved_context["group_id"] = str(group_id or "").strip() or resolved_context["group_id"]
        resolved_context["user_id"] = str(user_id or "").strip() or resolved_context["user_id"]
        return resolved_context

    @staticmethod
    def _retrieval_filter_context_is_current_source(
        context: Dict[str, str],
        current_context: Dict[str, str],
    ) -> bool:
        current_stream_id = str(current_context.get("stream_id", "") or "").strip()
        source_stream_id = str(context.get("stream_id", "") or "").strip()
        if current_stream_id and source_stream_id and current_stream_id == source_stream_id:
            return True

        current_group_id = str(current_context.get("group_id", "") or "").strip()
        source_group_id = str(context.get("group_id", "") or "").strip()
        if current_group_id and source_group_id and current_group_id == source_group_id:
            return True

        current_user_id = str(current_context.get("user_id", "") or "").strip()
        source_user_id = str(context.get("user_id", "") or "").strip()
        current_is_private = bool(current_user_id) and not current_group_id
        source_is_private = bool(source_user_id) and not source_group_id
        return current_is_private and source_is_private and current_user_id == source_user_id

    def _retrieval_filter_context_allowed(self, context: Dict[str, str]) -> bool:
        kind = str(context.get("kind", "") or "").strip()
        if not kind:
            return True
        type_config = self._retrieval_type_filter_config(kind)
        if not type_config or not bool(type_config.get("enabled", False)):
            return True
        return self._chat_filter_config_allows(
            type_config,
            stream_id=str(context.get("stream_id", "") or "").strip(),
            group_id=str(context.get("group_id", "") or "").strip(),
            user_id=str(context.get("user_id", "") or "").strip(),
            default_when_empty=True,
        )

    @staticmethod
    def _resolve_knowledge_type(source_type: str) -> str:
        clean_type = str(source_type or "").strip().lower()
        if clean_type == "person_fact":
            return "factual"
        if clean_type == "chat_summary":
            return "narrative"
        return "mixed"

    @staticmethod
    def _time_meta(timestamp: Optional[float], time_start: Optional[float], time_end: Optional[float]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if timestamp is not None:
            payload["event_time"] = float(timestamp)
        if time_start is not None:
            payload["event_time_start"] = float(time_start)
        if time_end is not None:
            payload["event_time_end"] = float(time_end)
        if payload:
            payload["time_granularity"] = "minute"
            payload["time_confidence"] = 0.95
        return payload

    @classmethod
    def _normalize_search_time_bound(cls, value: Any, *, is_end: bool) -> tuple[Optional[float], Optional[str]]:
        if value in {None, ""}:
            return None, None
        if isinstance(value, (int, float)):
            ts = float(value)
            return ts, format_timestamp(ts)

        text = str(value or "").strip()
        if not text:
            return None, None

        numeric = cls._optional_float(text)
        if numeric is not None:
            return numeric, format_timestamp(numeric)

        try:
            ts = parse_query_datetime_to_timestamp(text, is_end=is_end)
        except ValueError as exc:
            raise ValueError(f"时间参数错误: {exc}") from exc
        return ts, text

    @classmethod
    def _normalize_search_time_window(cls, time_start: Any, time_end: Any) -> _NormalizedSearchTimeWindow:
        numeric_start, query_start = cls._normalize_search_time_bound(time_start, is_end=False)
        numeric_end, query_end = cls._normalize_search_time_bound(time_end, is_end=True)
        if numeric_start is not None and numeric_end is not None and numeric_start > numeric_end:
            raise ValueError("时间参数错误: time_start 不能晚于 time_end")
        return _NormalizedSearchTimeWindow(
            numeric_start=numeric_start,
            numeric_end=numeric_end,
            query_start=query_start,
            query_end=query_end,
        )

    @staticmethod
    def _retrieval_result_hit(item: RetrievalResult) -> Dict[str, Any]:
        payload = item.to_dict()
        return {
            "hash": payload.get("hash", ""),
            "content": payload.get("content", ""),
            "score": payload.get("score", 0.0),
            "type": payload.get("type", ""),
            "source": payload.get("source", ""),
            "metadata": payload.get("metadata", {}) or {},
        }

    @staticmethod
    def _episode_hit(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "episode",
            "episode_id": str(row.get("episode_id", "") or ""),
            "title": str(row.get("title", "") or ""),
            "content": str(row.get("summary", "") or ""),
            "score": float(row.get("lexical_score", 0.0) or 0.0),
            "source": "episode",
            "metadata": {
                "participants": row.get("participants", []) or [],
                "keywords": row.get("keywords", []) or [],
                "source": row.get("source"),
                "event_time_start": row.get("event_time_start"),
                "event_time_end": row.get("event_time_end"),
            },
        }

    @staticmethod
    def _summary(hits: Sequence[Dict[str, Any]]) -> str:
        if not hits:
            return ""
        lines = []
        for index, item in enumerate(hits[:5], start=1):
            content = str(item.get("content", "") or "").strip().replace("\n", " ")
            lines.append(f"{index}. {(content[:120] + '...') if len(content) > 120 else content}")
        return "\n".join(lines)

    @staticmethod
    def _filter_hits(hits: List[Dict[str, Any]], person_id: str) -> List[Dict[str, Any]]:
        if not person_id:
            return hits
        filtered = []
        for item in hits:
            metadata = item.get("metadata", {}) or {}
            if person_id in (metadata.get("person_ids", []) or []):
                filtered.append(item)
                continue
            if person_id and person_id in str(item.get("content", "") or ""):
                filtered.append(item)
        return filtered or hits

    def _filter_active_relation_hits(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self.metadata_store is None:
            return hits
        relation_hashes: List[str] = []
        paragraph_relation_cache: Dict[str, List[str]] = {}
        paragraph_hashes: List[str] = []
        seen_relation_hashes: set[str] = set()

        for item in hits:
            item_type = str(item.get("type", "") or "").strip()
            item_hash = str(item.get("hash", "") or "").strip()
            if item_type == "relation" and item_hash and item_hash not in seen_relation_hashes:
                seen_relation_hashes.add(item_hash)
                relation_hashes.append(item_hash)
                continue
            if item_type != "paragraph" or not item_hash:
                continue
            paragraph_hashes.append(item_hash)
            linked_relations = self.metadata_store.get_paragraph_relations(item_hash)
            linked_hashes: List[str] = []
            for relation in linked_relations:
                linked_hash = str(relation.get("hash", "") or "").strip()
                if not linked_hash or linked_hash in seen_relation_hashes:
                    continue
                seen_relation_hashes.add(linked_hash)
                relation_hashes.append(linked_hash)
                linked_hashes.append(linked_hash)
            if linked_hashes:
                paragraph_relation_cache[item_hash] = linked_hashes

        marks_by_paragraph, _ = self._load_paragraph_stale_marks(paragraph_hashes)
        stale_relation_hashes = self._tokens(
            mark.get("relation_hash", "")
            for marks in marks_by_paragraph.values()
            for mark in marks
            if isinstance(mark, dict)
        )
        for relation_hash in stale_relation_hashes:
            if relation_hash in seen_relation_hashes:
                continue
            seen_relation_hashes.add(relation_hash)
            relation_hashes.append(relation_hash)

        if not relation_hashes and not marks_by_paragraph:
            return hits

        status_map = self.metadata_store.get_relation_status_batch(relation_hashes)
        filtered: List[Dict[str, Any]] = []
        for item in hits:
            item_type = str(item.get("type", "") or "").strip()
            if item_type == "paragraph":
                paragraph_hash = str(item.get("hash", "") or "").strip()
                if self._paragraph_hidden_by_stale_marks(
                    paragraph_hash,
                    marks_by_paragraph=marks_by_paragraph,
                    relation_status_map=status_map,
                ):
                    continue
                linked_hashes = paragraph_relation_cache.get(paragraph_hash, [])
                if not linked_hashes:
                    filtered.append(item)
                    continue
                if any(
                    not bool((status_map.get(linked_hash) or {}).get("is_inactive"))
                    for linked_hash in linked_hashes
                ):
                    filtered.append(item)
                continue
            if item_type != "relation":
                filtered.append(item)
                continue
            hash_value = str(item.get("hash", "") or "").strip()
            status = status_map.get(hash_value) if isinstance(status_map, dict) else None
            if status is None:
                continue
            if bool(status.get("is_inactive")):
                continue
            filtered.append(item)
        return filtered

    def _resolve_relation_hashes(self, target: str) -> List[str]:
        assert self.metadata_store
        token = str(target or "").strip()
        if not token:
            return []
        if len(token) == 64 and all(ch in "0123456789abcdef" for ch in token.lower()):
            return [token]
        hashes = self.metadata_store.search_relation_hashes_by_text(token, limit=10)
        if hashes:
            return hashes
        return [
            str(row.get("hash", "") or "")
            for row in self.metadata_store.get_relations(subject=token)[:10]
            if str(row.get("hash", "")).strip()
        ]

    def _resolve_deleted_relation_hashes(self, target: str) -> List[str]:
        assert self.metadata_store
        token = str(target or "").strip()
        if not token:
            return []
        if len(token) == 64 and all(ch in "0123456789abcdef" for ch in token.lower()):
            return [token]
        return self.metadata_store.search_deleted_relation_hashes_by_text(token, limit=10)

    def _memory_v5_status(self, *, target: str = "", limit: int = 50) -> Dict[str, Any]:
        assert self.metadata_store
        now = time.time()
        summary = self.metadata_store.get_memory_status_summary(now)
        payload: Dict[str, Any] = {
            "success": True,
            **summary,
            "config": {
                "half_life_hours": float(self._cfg("memory.half_life_hours", 24.0) or 24.0),
                "base_decay_interval_hours": float(self._cfg("memory.base_decay_interval_hours", 1.0) or 1.0),
                "prune_threshold": float(self._cfg("memory.prune_threshold", 0.1) or 0.1),
                "freeze_duration_hours": float(self._cfg("memory.freeze_duration_hours", 24.0) or 24.0),
            },
            "last_maintenance_at": self._last_maintenance_at,
        }
        token = str(target or "").strip()
        if not token:
            return payload

        active_hashes = self._resolve_relation_hashes(token)[:limit]
        deleted_hashes = self._resolve_deleted_relation_hashes(token)[:limit]
        active_statuses = self.metadata_store.get_relation_status_batch(active_hashes)
        items: List[Dict[str, Any]] = []
        for hash_value in active_hashes:
            relation = self.metadata_store.get_relation(hash_value) or {}
            status = active_statuses.get(hash_value, {})
            items.append(
                {
                    "hash": hash_value,
                    "subject": str(relation.get("subject", "") or ""),
                    "predicate": str(relation.get("predicate", "") or ""),
                    "object": str(relation.get("object", "") or ""),
                    "state": "inactive" if bool(status.get("is_inactive")) else "active",
                    "is_pinned": bool(status.get("is_pinned", False)),
                    "temp_protected": bool(float(status.get("protected_until") or 0.0) > now),
                    "protected_until": status.get("protected_until"),
                    "last_reinforced": status.get("last_reinforced"),
                    "weight": float(status.get("weight", relation.get("confidence", 0.0)) or 0.0),
                }
            )
        for hash_value in deleted_hashes:
            relation = self.metadata_store.get_deleted_relation(hash_value) or {}
            items.append(
                {
                    "hash": hash_value,
                    "subject": str(relation.get("subject", "") or ""),
                    "predicate": str(relation.get("predicate", "") or ""),
                    "object": str(relation.get("object", "") or ""),
                    "state": "deleted",
                    "is_pinned": bool(relation.get("is_pinned", False)),
                    "temp_protected": False,
                    "protected_until": relation.get("protected_until"),
                    "last_reinforced": relation.get("last_reinforced"),
                    "weight": float(relation.get("confidence", 0.0) or 0.0),
                    "deleted_at": relation.get("deleted_at"),
                }
            )
        payload["items"] = items[:limit]
        payload["count"] = len(payload["items"])
        payload["target"] = token
        return payload

    async def _preview_fuzzy_modify_action(
        self,
        *,
        request_text: str,
        scope: str,
        person_id: str = "",
        person_keyword: str = "",
        chat_id: str = "",
        limit: int = 20,
        requested_by: str = "webui",
        reason: str = "",
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        if not self._fuzzy_modify_cfg_enabled():
            return {"success": False, "error": "记忆修正功能未启用"}
        text = str(request_text or "").strip()
        if not text:
            return {"success": False, "error": "修改描述不能为空"}

        scope_token = self._normalize_fuzzy_modify_scope(scope)
        pid = str(person_id or "").strip()
        keyword = str(person_keyword or "").strip()
        if scope_token == "person_profile":
            if not pid and keyword and self.person_profile_service is not None:
                pid = self.person_profile_service.resolve_person_id(keyword)
            if not pid:
                return {"success": False, "error": "人物画像修改需要提供 person_id 或 person_keyword"}
        elif not chat_id and not self._fuzzy_modify_cfg_allow_global_scope():
            return {"success": False, "error": "非人物画像修正需要提供 chat_id，或开启全局记忆修正范围"}

        candidate_limit = min(max(1, int(limit or 20)), self._fuzzy_modify_cfg_candidate_limit())
        candidates = await self._collect_fuzzy_modify_candidates(
            request_text=text,
            scope=scope_token,
            person_id=pid,
            person_keyword=keyword,
            chat_id=str(chat_id or "").strip(),
            limit=candidate_limit,
        )
        if not candidates:
            return {"success": False, "error": "未找到可修改的候选记忆", "candidates": []}

        plan_payload = await self._build_fuzzy_modify_llm_plan(
            request_text=text,
            scope=scope_token,
            person_id=pid,
            person_keyword=keyword,
            chat_id=str(chat_id or "").strip(),
            candidates=candidates,
        )
        plan = self._normalize_fuzzy_modify_plan(
            plan_payload,
            request_text=text,
            scope=scope_token,
            person_id=pid,
            chat_id=str(chat_id or "").strip(),
            candidates=candidates,
        )
        if not plan.get("operations"):
            return {
                "success": False,
                "error": str(plan.get("reason", "") or "LLM 未生成可执行修改计划"),
                "raw_plan": plan_payload,
                "candidates": candidates,
            }

        confidence = float(plan.get("confidence", 0.0) or 0.0)
        cascade_preview = self._build_fuzzy_modify_cascade_preview(
            operations=plan.get("operations", []),
        )
        preview = {
            "request_text": text,
            "scope": scope_token,
            "person_id": pid,
            "person_keyword": keyword,
            "chat_id": str(chat_id or "").strip(),
            "candidates": candidates,
            "operations": plan.get("operations", []),
            "cascade_preview": cascade_preview,
            "requires_confirmation": True,
            "confirm_threshold": self._fuzzy_modify_cfg_confirm_threshold(),
            "reason": str(plan.get("reason", "") or ""),
        }
        record = self.metadata_store.create_fuzzy_modify_plan(
            request_text=text,
            scope=scope_token,
            target_person_id=pid,
            target_chat_id=str(chat_id or "").strip(),
            plan=plan,
            preview=preview,
            status="awaiting_confirmation",
            confidence=confidence,
            requested_by=requested_by,
            reason=reason,
        )
        return {
            "success": True,
            "plan_id": str(record.get("plan_id", "") or ""),
            "plan": record,
            "preview": preview,
            "requires_confirmation": True,
        }

    async def _execute_fuzzy_modify_action(
        self,
        *,
        plan_id: str,
        confirmed: bool,
        requested_by: str = "webui",
        reason: str = "",
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        token = str(plan_id or "").strip()
        if not token:
            return {"success": False, "error": "plan_id 不能为空"}
        plan_record = self.metadata_store.get_fuzzy_modify_plan(token)
        if plan_record is None:
            return {"success": False, "error": "修改计划不存在"}
        status = str(plan_record.get("status", "") or "").strip()
        if status not in {"awaiting_confirmation", "failed", "executing"}:
            return {"success": False, "error": f"当前计划状态不可执行: {status}"}
        if not confirmed:
            confidence = self._optional_float(plan_record.get("confidence")) or 0.0
            if not self._fuzzy_modify_cfg_auto_execute_enabled() or confidence < self._fuzzy_modify_cfg_confirm_threshold():
                return {"success": False, "error": "需要用户确认后才能执行", "requires_confirmation": True}

        previous_execution = plan_record.get("execution") if isinstance(plan_record.get("execution"), dict) else {}
        attempt_started_at = time.time()
        executing_payload = {
            **previous_execution,
            "attempt": {
                "status": "executing",
                "started_at": attempt_started_at,
                "requested_by": requested_by,
                "reason": reason,
                "recovered_from_stale_executing": status == "executing",
            },
        }
        self.metadata_store.update_fuzzy_modify_plan(token, status="executing", execution=executing_payload)
        try:
            execution = await self._apply_fuzzy_modify_plan(
                plan_record=plan_record,
                requested_by=requested_by,
                reason=reason,
            )
            execution = {
                **execution,
                "attempt": {
                    **executing_payload["attempt"],
                    "status": "finished",
                    "finished_at": time.time(),
                },
            }
            updated = self.metadata_store.update_fuzzy_modify_plan(
                token,
                status="executed" if bool(execution.get("success")) else "failed",
                execution=execution,
                executed_at=time.time() if bool(execution.get("success")) else None,
                reason=reason if reason else None,
            )
            return {"success": bool(execution.get("success")), "plan": updated, "execution": execution}
        except Exception as exc:
            execution = {
                **executing_payload,
                "success": False,
                "error": str(exc),
                "attempt": {
                    **executing_payload["attempt"],
                    "status": "failed",
                    "finished_at": time.time(),
                },
            }
            updated = self.metadata_store.update_fuzzy_modify_plan(
                token,
                status="failed",
                execution=execution,
                reason=reason if reason else None,
            )
            logger.warning(f"记忆修正执行失败: {exc}")
            return {"success": False, "plan": updated, "execution": execution, "error": str(exc)}

    async def _rollback_fuzzy_modify_action(
        self,
        *,
        plan_id: str,
        requested_by: str = "webui",
        reason: str = "",
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        token = str(plan_id or "").strip()
        if not token:
            return {"success": False, "error": "plan_id 不能为空"}
        plan_record = self.metadata_store.get_fuzzy_modify_plan(token)
        if plan_record is None:
            return {"success": False, "error": "修改计划不存在"}
        if str(plan_record.get("status", "") or "") != "executed":
            return {"success": False, "error": "只有已执行的修改计划可以回滚"}

        execution = plan_record.get("execution") if isinstance(plan_record.get("execution"), dict) else {}
        stored_ids = self._tokens(execution.get("stored_ids"))
        paragraph_hashes = [hash_value for hash_value in stored_ids if self.metadata_store.get_paragraph(hash_value)]
        relation_hashes = [hash_value for hash_value in stored_ids if self.metadata_store.get_relation(hash_value)]
        rollback_items: List[Dict[str, Any]] = []
        if paragraph_hashes:
            delete_result = await self._execute_delete_action(
                mode="paragraph",
                selector={"hashes": paragraph_hashes},
                requested_by=requested_by,
                reason=reason or "fuzzy_modify_rollback",
            )
            rollback_items.append({"type": "delete_new_paragraphs", "result": delete_result})
            if not bool(delete_result.get("success", False)):
                rollback_result = {
                    "success": False,
                    "error": str(delete_result.get("error", "") or "回滚删除新增记忆失败"),
                    "stored_ids_delete_requested": paragraph_hashes,
                    "new_relations_deactivated": [],
                    "restored_targets": [],
                    "items": rollback_items,
                    "requested_by": requested_by,
                    "reason": reason,
                }
                updated = self.metadata_store.update_fuzzy_modify_plan(
                    token,
                    status="rollback_failed",
                    execution={**execution, "rollback": rollback_result},
                    reason=reason if reason else None,
                )
                return {"success": False, "plan": updated, "rollback": rollback_result, "error": rollback_result["error"]}

        restored_targets: List[Dict[str, Any]] = []
        restore_failures: List[Dict[str, str]] = []
        stale_marks_deleted: List[Dict[str, Any]] = []
        stale_marks_restored: List[Dict[str, Any]] = []
        stale_marks_skipped: List[Dict[str, Any]] = []
        for item in execution.get("superseded_targets") or []:
            if not isinstance(item, dict):
                continue
            target_type = str(item.get("target_type", "") or "").strip()
            hash_value = str(item.get("hash", "") or "").strip()
            previous_metadata = item.get("previous_metadata") if isinstance(item.get("previous_metadata"), dict) else {}
            if target_type == "paragraph" and hash_value:
                cascade = item.get("cascade") if isinstance(item.get("cascade"), dict) else {}
                for relation_item in cascade.get("relations_marked_inactive") or []:
                    if not isinstance(relation_item, dict):
                        continue
                    relation_hash = str(relation_item.get("relation_hash", "") or "").strip()
                    if not relation_hash:
                        continue
                    previous_relation_metadata = (
                        relation_item.get("previous_metadata")
                        if isinstance(relation_item.get("previous_metadata"), dict)
                        else {}
                    )
                    updated_relation = self.metadata_store.update_relation_metadata(
                        relation_hash,
                        previous_relation_metadata,
                        merge=False,
                    )
                    if updated_relation is None:
                        restore_failures.append(
                            {"target_type": "relation", "hash": relation_hash, "error": "级联关系不存在"}
                        )
                        continue
                    if bool(relation_item.get("previous_is_inactive", False)):
                        self.metadata_store.mark_relations_inactive(
                            [relation_hash],
                            inactive_since=self._optional_float(relation_item.get("previous_inactive_since")),
                        )
                    else:
                        self.metadata_store.mark_relations_active([relation_hash])
                    restored_targets.append(
                        {"target_type": "relation", "hash": relation_hash, "cascade_from": hash_value}
                    )

                for snapshot in cascade.get("stale_mark_snapshots") or []:
                    if not isinstance(snapshot, dict):
                        continue
                    paragraph_hash = str(snapshot.get("paragraph_hash", "") or hash_value).strip()
                    relation_hash = str(snapshot.get("relation_hash", "") or "").strip()
                    if not paragraph_hash or not relation_hash:
                        continue
                    rollback_mark = self.metadata_store.rollback_paragraph_stale_relation_mark(
                        paragraph_hash=paragraph_hash,
                        relation_hash=relation_hash,
                        expected_source_type=str(snapshot.get("source_type", "") or "memory_correction"),
                        expected_source_id=str(snapshot.get("source_id", "") or token),
                        expected_source_operation_id=str(snapshot.get("source_operation_id", "") or ""),
                        previous_mark=(
                            snapshot.get("previous_mark")
                            if isinstance(snapshot.get("previous_mark"), dict)
                            else None
                        ),
                    )
                    action = str(rollback_mark.get("action", "") or "").strip()
                    if action == "deleted":
                        stale_marks_deleted.append(rollback_mark)
                    elif action == "restored":
                        stale_marks_restored.append(rollback_mark)
                    elif action in {"skipped_due_to_source_mismatch", "restore_failed", "invalid_target"}:
                        stale_marks_skipped.append(rollback_mark)
                        if action in {"restore_failed", "invalid_target"}:
                            restore_failures.append(
                                {
                                    "target_type": "stale_mark",
                                    "hash": f"{paragraph_hash}:{relation_hash}",
                                    "error": action,
                                }
                            )
                    else:
                        stale_marks_skipped.append(rollback_mark)

                updated = self.metadata_store.update_paragraph_metadata(hash_value, previous_metadata, merge=False)
                if updated is not None:
                    restored_targets.append({"target_type": target_type, "hash": hash_value})
                else:
                    restore_failures.append({"target_type": target_type, "hash": hash_value, "error": "目标段落不存在或已删除"})
                continue
            if target_type == "relation" and hash_value:
                updated = self.metadata_store.update_relation_metadata(hash_value, previous_metadata, merge=False)
                if updated is not None:
                    if bool(item.get("previous_is_inactive", False)):
                        self.metadata_store.mark_relations_inactive(
                            [hash_value],
                            inactive_since=self._optional_float(item.get("previous_inactive_since")),
                        )
                    else:
                        self.metadata_store.mark_relations_active([hash_value])
                    restored_targets.append({"target_type": target_type, "hash": hash_value})
                else:
                    restore_failures.append({"target_type": target_type, "hash": hash_value, "error": "目标关系不存在"})

        if relation_hashes:
            self.metadata_store.mark_relations_inactive(relation_hashes, inactive_since=time.time())
        if restored_targets:
            self._rebuild_graph_from_metadata()
            self._persist()
        rollback_success = not restore_failures
        rollback_result = {
            "success": rollback_success,
            "stored_ids_deleted": paragraph_hashes,
            "new_relations_deactivated": relation_hashes,
            "restored_targets": restored_targets,
            "restore_failures": restore_failures,
            "stale_marks_deleted": stale_marks_deleted,
            "stale_marks_restored": stale_marks_restored,
            "stale_marks_skipped": stale_marks_skipped,
            "items": rollback_items,
            "requested_by": requested_by,
            "reason": reason,
        }
        updated = self.metadata_store.update_fuzzy_modify_plan(
            token,
            status="rolled_back" if rollback_success else "rollback_failed",
            execution={**execution, "rollback": rollback_result},
            reason=reason if reason else None,
        )
        return {"success": rollback_success, "plan": updated, "rollback": rollback_result}

    async def _collect_fuzzy_modify_candidates(
        self,
        *,
        request_text: str,
        scope: str,
        person_id: str = "",
        person_keyword: str = "",
        chat_id: str = "",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def append_candidate(item: Dict[str, Any]) -> None:
            candidate = self._normalize_fuzzy_modify_candidate(item)
            candidate_type = str(candidate.get("target_type", "") or "").strip()
            hash_value = str(candidate.get("hash", "") or "").strip()
            key = (candidate_type, hash_value)
            if not candidate_type or not hash_value or key in seen:
                return
            if not self._is_fuzzy_modify_candidate_mutable(candidate, item):
                return
            seen.add(key)
            candidates.append(candidate)

        if scope == "person_profile":
            evidence = await self._profile_evidence_admin(
                person_id=person_id,
                person_keyword=person_keyword,
                limit=max(limit, 12),
                force_refresh=False,
            )
            for item in evidence.get("evidence") or []:
                if isinstance(item, dict):
                    append_candidate(item)

        search_result = await self.search_memory(
            KernelSearchRequest(
                query=request_text,
                limit=limit,
                mode="aggregate",
                chat_id=chat_id,
                person_id=person_id,
                respect_filter=True,
            )
        )
        for item in search_result.get("hits") or []:
            if isinstance(item, dict):
                append_candidate(item)
        return candidates[:limit]

    def _is_fuzzy_modify_candidate_mutable(self, candidate: Dict[str, Any], raw_item: Dict[str, Any]) -> bool:
        assert self.metadata_store is not None
        if raw_item.get("deletable") is False:
            return False
        target_type = str(candidate.get("target_type", "") or "").strip()
        hash_value = str(candidate.get("hash", "") or "").strip()
        if not target_type or not hash_value:
            return False
        if target_type == "paragraph":
            paragraph = self.metadata_store.get_paragraph(hash_value)
            return isinstance(paragraph, dict) and not bool(paragraph.get("is_deleted", 0))
        if target_type == "relation":
            relation = self.metadata_store.get_relation(hash_value, include_inactive=False)
            if relation is None:
                return False
            status = self.metadata_store.get_relation_status_batch([hash_value]).get(hash_value, {})
            if bool(status.get("is_inactive", False)) or bool(status.get("is_pinned", False)):
                return False
            protected_until = self._optional_float(status.get("protected_until")) or 0.0
            return protected_until <= time.time()
        return False

    async def _build_fuzzy_modify_llm_plan(
        self,
        *,
        request_text: str,
        scope: str,
        person_id: str = "",
        person_keyword: str = "",
        chat_id: str = "",
        candidates: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payload = {
            "request_text": request_text,
            "scope": scope,
            "person_id": person_id,
            "person_keyword": person_keyword,
            "chat_id": chat_id,
            "max_targets": self._fuzzy_modify_cfg_max_targets(),
            "candidates": [
                {
                    "candidate_id": str(item.get("candidate_id", "") or ""),
                    "target_type": str(item.get("target_type", "") or ""),
                    "evidence_type": str(item.get("evidence_type", "") or ""),
                    "hash": str(item.get("hash", "") or ""),
                    "content": str(item.get("content", "") or ""),
                    "source": str(item.get("source", "") or ""),
                    "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                }
                for item in candidates
            ],
        }
        prompt = load_prompt(
            "memory_fuzzy_modify_plan",
            request_payload=json.dumps(payload, ensure_ascii=False, indent=2),
        )
        if self._fuzzy_modify_planner is None:
            self._fuzzy_modify_planner = LLMServiceClient(
                task_name="utils",
                request_type="A_Memorix.fuzzy_modify_plan",
            )
        response = await self._fuzzy_modify_planner.generate_response(prompt)
        return self._safe_json_loads(getattr(response, "response", ""))

    def _normalize_fuzzy_modify_plan(
        self,
        payload: Dict[str, Any],
        *,
        request_text: str,
        scope: str,
        person_id: str,
        chat_id: str,
        candidates: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        candidate_map = {
            str(item.get("candidate_id", "") or "").strip(): item
            for item in candidates
            if str(item.get("candidate_id", "") or "").strip()
        }
        hash_to_candidate = {
            str(item.get("hash", "") or "").strip(): item
            for item in candidates
            if str(item.get("hash", "") or "").strip()
        }
        confidence = min(1.0, max(0.0, float(payload.get("confidence", 0.0) or 0.0)))
        max_targets = self._fuzzy_modify_cfg_max_targets()
        operations: List[Dict[str, Any]] = []
        for raw in payload.get("operations") or []:
            if not isinstance(raw, dict):
                continue
            action = str(raw.get("action", "") or raw.get("op", "") or "").strip().lower()
            if action == "mark_superseded":
                candidate = candidate_map.get(str(raw.get("candidate_id", "") or "").strip())
                if candidate is None:
                    candidate = hash_to_candidate.get(str(raw.get("hash", "") or "").strip())
                if candidate is None:
                    candidate_id = str(raw.get("candidate_id", "") or "").strip()
                    raw_hash = str(raw.get("hash", "") or "").strip()
                    logger.warning(
                        f"记忆修正计划引用了候选集外的目标: action={action} candidate_id={candidate_id} hash={raw_hash}"
                    )
                    continue
                operations.append(
                    {
                        "action": "mark_superseded",
                        "candidate_id": str(candidate.get("candidate_id", "") or ""),
                        "target_type": str(candidate.get("target_type", "") or ""),
                        "hash": str(candidate.get("hash", "") or ""),
                        "reason": str(raw.get("reason", "") or payload.get("reason", "") or request_text).strip(),
                        "valid_to": self._optional_float(raw.get("valid_to")),
                    }
                )
                continue
            if action == "ingest_text":
                text = str(raw.get("text", "") or "").strip()
                if not text:
                    continue
                operation: Dict[str, Any] = {
                    "action": "ingest_text",
                    "text": text,
                    "source_type": str(raw.get("source_type", "") or ("person_fact" if person_id else "memory")).strip(),
                    "chat_id": str(raw.get("chat_id", "") or chat_id).strip(),
                    "person_ids": self._merge_argument_tokens(raw.get("person_ids"), [person_id]),
                    "participants": self._argument_tokens(raw.get("participants")),
                    "tags": self._merge_argument_tokens(raw.get("tags"), ["fuzzy_modify"]),
                    "relations": self._normalize_fuzzy_modify_relations(raw.get("relations")),
                    "valid_from": self._optional_float(raw.get("valid_from")),
                    "reason": str(raw.get("reason", "") or payload.get("reason", "") or request_text).strip(),
                }
                operations.append(operation)
                continue
            if action == "refresh_person_profile":
                target_person_id = str(raw.get("person_id", "") or person_id).strip()
                if target_person_id:
                    operations.append({"action": "refresh_person_profile", "person_id": target_person_id})
        operations = operations[: max(1, max_targets * 2)]
        target_count = sum(1 for item in operations if item.get("action") == "mark_superseded")
        if target_count > max_targets:
            kept = 0
            limited: List[Dict[str, Any]] = []
            for item in operations:
                if item.get("action") != "mark_superseded":
                    limited.append(item)
                    continue
                kept += 1
                if kept <= max_targets:
                    limited.append(item)
            operations = limited
        if operations and not any(item.get("action") == "refresh_person_profile" for item in operations) and person_id:
            operations.append({"action": "refresh_person_profile", "person_id": person_id})
        return {
            "scope": scope,
            "request_text": request_text,
            "person_id": person_id,
            "chat_id": chat_id,
            "confidence": confidence,
            "risk_level": str(payload.get("risk_level", "medium") or "medium").strip(),
            "reason": str(payload.get("reason", "") or "").strip(),
            "operations": operations,
        }

    def _normalize_fuzzy_modify_candidate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        evidence_type = str(item.get("evidence_type", "") or item.get("type", "") or "").strip()
        target_type = "relation" if evidence_type == "relation" else "paragraph"
        hash_value = str(item.get("hash", "") or "").strip()
        metadata = coerce_metadata_dict(item.get("metadata"))
        return {
            "candidate_id": f"{target_type}:{hash_value}",
            "target_type": target_type,
            "evidence_type": evidence_type,
            "hash": hash_value,
            "content": self._trim_text(str(item.get("content", "") or item.get("title", "") or ""), 420),
            "source": str(item.get("source", "") or metadata.get("source", "") or "").strip(),
            "metadata": metadata,
            "score": item.get("score"),
        }

    @staticmethod
    def _normalize_fuzzy_modify_relations(value: Any) -> List[Dict[str, Any]]:
        relations: List[Dict[str, Any]] = []
        for row in value or []:
            if not isinstance(row, dict):
                continue
            subject = str(row.get("subject", "") or "").strip()
            predicate = str(row.get("predicate", "") or "").strip()
            obj = str(row.get("object", "") or "").strip()
            if not (subject and predicate and obj):
                continue
            relations.append(
                {
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "confidence": min(1.0, max(0.0, float(row.get("confidence", 1.0) or 1.0))),
                    "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
                }
            )
        return relations

    def _build_fuzzy_modify_cascade_preview(self, *, operations: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        relations: List[Dict[str, Any]] = []
        entities: List[Dict[str, Any]] = []
        seen_relations: set[tuple[str, str]] = set()
        seen_entities: set[tuple[str, str]] = set()
        for operation in operations or []:
            if not isinstance(operation, dict):
                continue
            if operation.get("action") != "mark_superseded":
                continue
            if str(operation.get("target_type", "") or "").strip() != "paragraph":
                continue
            paragraph_hash = str(operation.get("hash", "") or "").strip()
            if not paragraph_hash:
                continue
            cascade = self._build_fuzzy_modify_paragraph_cascade(
                paragraph_hash=paragraph_hash,
                reason=str(operation.get("reason", "") or "").strip(),
                preview_only=True,
                plan_id="",
            )
            for item in cascade.get("relations", []):
                if not isinstance(item, dict):
                    continue
                relation_hash = str(item.get("relation_hash", "") or "").strip()
                key = (paragraph_hash, relation_hash)
                if not relation_hash or key in seen_relations:
                    continue
                seen_relations.add(key)
                relations.append(item)
            for item in cascade.get("entities", []):
                if not isinstance(item, dict):
                    continue
                entity_hash = str(item.get("entity_hash", "") or "").strip()
                key = (paragraph_hash, entity_hash)
                if not entity_hash or key in seen_entities:
                    continue
                seen_entities.add(key)
                entities.append(item)
        counts = {
            "relations": len(relations),
            "relations_mark_inactive": sum(1 for item in relations if item.get("action") == "mark_inactive"),
            "relations_mark_stale_evidence": sum(1 for item in relations if item.get("action") == "mark_stale_evidence"),
            "relations_skipped_protected": sum(1 for item in relations if item.get("action") == "skipped_protected"),
            "entities": len(entities),
        }
        return {"relations": relations, "entities": entities, "counts": counts}

    def _build_fuzzy_modify_paragraph_cascade(
        self,
        *,
        paragraph_hash: str,
        reason: str,
        preview_only: bool,
        plan_id: str,
    ) -> Dict[str, List[Dict[str, Any]]]:
        assert self.metadata_store is not None
        paragraph_token = str(paragraph_hash or "").strip()
        if not paragraph_token:
            return {"relations": [], "entities": []}

        relations: List[Dict[str, Any]] = []
        raw_relations = self.metadata_store.get_paragraph_relations(paragraph_token)
        relation_hashes = [
            str(item.get("hash", "") or "").strip()
            for item in raw_relations
            if isinstance(item, dict) and str(item.get("hash", "") or "").strip()
        ]
        statuses = self.metadata_store.get_relation_status_batch(relation_hashes) if relation_hashes else {}
        now = time.time()
        for relation in raw_relations:
            if not isinstance(relation, dict):
                continue
            relation_hash = str(relation.get("hash", "") or "").strip()
            if not relation_hash:
                continue
            status = statuses.get(relation_hash, {})
            protected_until = self._optional_float(status.get("protected_until")) or 0.0
            is_pinned = bool(status.get("is_pinned", False))
            protected = is_pinned or protected_until > now
            if protected:
                action = "skipped_protected"
                action_reason = "relation_is_pinned" if is_pinned else "relation_is_temporarily_protected"
            elif self._relation_has_remaining_paragraphs(relation_hash, [paragraph_token]):
                action = "mark_stale_evidence"
                action_reason = "relation_has_other_active_paragraphs"
            else:
                action = "mark_inactive"
                action_reason = "only_supported_by_superseded_paragraph"
            relations.append(
                {
                    "paragraph_hash": paragraph_token,
                    "relation_hash": relation_hash,
                    "action": action,
                    "reason": action_reason,
                    "source_reason": reason,
                    "subject": str(relation.get("subject", "") or ""),
                    "predicate": str(relation.get("predicate", "") or ""),
                    "object": str(relation.get("object", "") or ""),
                    "is_pinned": is_pinned,
                    "protected_until": protected_until or None,
                    "is_inactive": bool(status.get("is_inactive", False)),
                    "inactive_since": status.get("inactive_since"),
                    "preview_only": preview_only,
                    "source_operation_id": (
                        self._fuzzy_modify_stale_source_operation_id(
                            plan_id=plan_id,
                            paragraph_hash=paragraph_token,
                            relation_hash=relation_hash,
                        )
                        if plan_id
                        else ""
                    ),
                }
            )

        entities: List[Dict[str, Any]] = []
        for entity in self.metadata_store.get_paragraph_entities(paragraph_token):
            if not isinstance(entity, dict):
                continue
            entity_hash = str(entity.get("hash", "") or "").strip()
            if not entity_hash:
                continue
            entities.append(
                {
                    "paragraph_hash": paragraph_token,
                    "entity_hash": entity_hash,
                    "action": "record_impact_only",
                    "reason": "entity_state_has_no_superseded_semantics",
                    "name": str(entity.get("name", "") or entity.get("entity", "") or ""),
                    "type": str(entity.get("type", "") or entity.get("entity_type", "") or ""),
                    "preview_only": preview_only,
                }
            )
        return {"relations": relations, "entities": entities}

    @staticmethod
    def _fuzzy_modify_stale_source_operation_id(
        *,
        plan_id: str,
        paragraph_hash: str,
        relation_hash: str,
    ) -> str:
        return f"{str(plan_id or '').strip()}:{str(paragraph_hash or '').strip()}:{str(relation_hash or '').strip()}"

    def _execute_fuzzy_modify_paragraph_cascade(
        self,
        *,
        paragraph_hash: str,
        plan_id: str,
        changed_at: float,
        reason: str,
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        paragraph_token = str(paragraph_hash or "").strip()
        plan_token = str(plan_id or "").strip()
        cascade = self._build_fuzzy_modify_paragraph_cascade(
            paragraph_hash=paragraph_token,
            reason=reason,
            preview_only=False,
            plan_id=plan_token,
        )
        result = {
            "relations_marked_inactive": [],
            "relations_marked_stale": [],
            "relations_skipped": [],
            "impacted_entities": cascade.get("entities", []),
            "stale_mark_snapshots": [],
        }

        for relation in cascade.get("relations", []):
            if not isinstance(relation, dict):
                continue
            relation_hash = str(relation.get("relation_hash", "") or "").strip()
            if not relation_hash:
                continue
            action = str(relation.get("action", "") or "").strip()
            if action == "skipped_protected":
                result["relations_skipped"].append(relation)
                continue
            if action == "mark_inactive":
                previous = self.metadata_store.get_relation(relation_hash)
                previous_metadata = coerce_metadata_dict((previous or {}).get("metadata"))
                patch = {
                    "memory_change": {
                        "change_id": plan_token,
                        "change_type": "paragraph_cascade_inactive",
                        "changed_at": changed_at,
                        "changed_by": "memory_correction",
                        "reason": reason,
                        "source_paragraph_hash": paragraph_token,
                    }
                }
                updated_metadata = self.metadata_store.update_relation_metadata(relation_hash, patch, merge=True)
                self.metadata_store.mark_relations_inactive([relation_hash], inactive_since=changed_at)
                result["relations_marked_inactive"].append(
                    {
                        **relation,
                        "previous_metadata": previous_metadata,
                        "updated_metadata": updated_metadata if isinstance(updated_metadata, dict) else {},
                        "previous_is_inactive": bool((previous or {}).get("is_inactive", False)),
                        "previous_inactive_since": (previous or {}).get("inactive_since"),
                    }
                )
                continue
            if action == "mark_stale_evidence":
                source_operation_id = self._fuzzy_modify_stale_source_operation_id(
                    plan_id=plan_token,
                    paragraph_hash=paragraph_token,
                    relation_hash=relation_hash,
                )
                previous_mark = self.metadata_store.get_paragraph_stale_relation_mark(
                    paragraph_hash=paragraph_token,
                    relation_hash=relation_hash,
                )
                written = self.metadata_store.upsert_paragraph_stale_relation_mark(
                    paragraph_hash=paragraph_token,
                    relation_hash=relation_hash,
                    reason=reason or "memory_correction_paragraph_superseded",
                    source_type="memory_correction",
                    source_id=plan_token,
                    source_operation_id=source_operation_id,
                )
                snapshot = {
                    "paragraph_hash": paragraph_token,
                    "relation_hash": relation_hash,
                    "source_type": "memory_correction",
                    "source_id": plan_token,
                    "source_operation_id": source_operation_id,
                    "previous_mark": previous_mark if isinstance(previous_mark, dict) else None,
                    "written_mark": written if isinstance(written, dict) else {},
                }
                result["stale_mark_snapshots"].append(snapshot)
                result["relations_marked_stale"].append({**relation, "written_mark": written or {}})
        return result

    async def _apply_fuzzy_modify_plan(
        self,
        *,
        plan_record: Dict[str, Any],
        requested_by: str = "webui",
        reason: str = "",
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        plan = plan_record.get("plan") if isinstance(plan_record.get("plan"), dict) else {}
        operations = [dict(item) for item in plan.get("operations") or [] if isinstance(item, dict)]
        change_id = str(plan_record.get("plan_id", "") or f"fuzzy_{int(time.time())}")
        changed_at = time.time()
        stored_ids: List[str] = []
        ingest_results: List[Dict[str, Any]] = []
        superseded_targets: List[Dict[str, Any]] = []

        supersede_hashes = [
            str(item.get("hash", "") or "").strip()
            for item in operations
            if item.get("action") == "mark_superseded" and str(item.get("hash", "") or "").strip()
        ]
        for index, operation in enumerate([item for item in operations if item.get("action") == "ingest_text"], start=1):
            op_reason = str(operation.get("reason", "") or reason or plan.get("request_text", "") or "").strip()
            metadata = {
                "memory_change": {
                    "change_id": change_id,
                    "change_type": "ingest_text",
                    "changed_at": changed_at,
                    "changed_by": requested_by,
                    "reason": op_reason,
                    "supersedes_hashes": supersede_hashes,
                    "valid_from": operation.get("valid_from") or changed_at,
                },
                "source_request": str(plan.get("request_text", "") or plan_record.get("request_text", "") or ""),
            }
            result = await self.ingest_text(
                external_id=f"{change_id}:ingest:{index}",
                source_type=str(operation.get("source_type", "") or "memory"),
                text=str(operation.get("text", "") or ""),
                chat_id=str(operation.get("chat_id", "") or plan.get("chat_id", "") or ""),
                person_ids=self._argument_tokens(operation.get("person_ids")),
                participants=self._argument_tokens(operation.get("participants")),
                timestamp=self._optional_float(operation.get("valid_from")) or changed_at,
                tags=self._argument_tokens(operation.get("tags")),
                metadata=metadata,
                relations=operation.get("relations") if isinstance(operation.get("relations"), list) else [],
                respect_filter=False,
            )
            result_ids = self._tokens(result.get("stored_ids"))
            stored_ids.extend(result_ids)
            ingest_results.append({"operation": operation, "result": result})

        replacement_hashes = list(stored_ids)
        for operation in [item for item in operations if item.get("action") == "mark_superseded"]:
            marked = self._mark_fuzzy_modify_target_superseded(
                operation=operation,
                change_id=change_id,
                changed_at=changed_at,
                changed_by=requested_by,
                replacement_hashes=replacement_hashes,
                plan_id=change_id,
                default_reason=reason or str(plan.get("request_text", "") or ""),
            )
            if marked:
                superseded_targets.append(marked)

        refreshed_profiles: List[Dict[str, Any]] = []
        for operation in [item for item in operations if item.get("action") == "refresh_person_profile"]:
            person_id = str(operation.get("person_id", "") or "").strip()
            if not person_id:
                continue
            refreshed_profiles.append(await self.refresh_person_profile(person_id))

        if superseded_targets:
            self._current_effective_filter_cache = {"checked_at": 0.0, "needed": True}
            self._rebuild_graph_from_metadata()
            self._persist()

        return {
            "success": bool(stored_ids or superseded_targets or refreshed_profiles),
            "stored_ids": stored_ids,
            "ingest_results": ingest_results,
            "superseded_targets": superseded_targets,
            "refreshed_profiles": refreshed_profiles,
            "changed_at": changed_at,
            "changed_by": requested_by,
            "reason": reason,
        }

    def _mark_fuzzy_modify_target_superseded(
        self,
        *,
        operation: Dict[str, Any],
        change_id: str,
        changed_at: float,
        changed_by: str,
        replacement_hashes: Sequence[str],
        plan_id: str,
        default_reason: str = "",
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        target_type = str(operation.get("target_type", "") or "").strip()
        hash_value = str(operation.get("hash", "") or "").strip()
        if target_type not in {"paragraph", "relation"} or not hash_value:
            return {}
        valid_to = self._optional_float(operation.get("valid_to")) or changed_at
        reason = str(operation.get("reason", "") or default_reason or "").strip()
        patch = {
                "memory_change": {
                    "change_id": change_id,
                    "change_type": "mark_superseded",
                "changed_at": changed_at,
                "changed_by": changed_by,
                "reason": reason,
                "valid_to": valid_to,
                "superseded_by_hashes": [str(item or "").strip() for item in replacement_hashes if str(item or "").strip()],
            }
        }
        if target_type == "paragraph":
            previous = self.metadata_store.get_paragraph(hash_value)
            if previous is None:
                return {}
            previous_metadata = coerce_metadata_dict(previous.get("metadata"))
            updated = self.metadata_store.update_paragraph_metadata(hash_value, patch, merge=True)
            if updated is None:
                return {}
            cascade = self._execute_fuzzy_modify_paragraph_cascade(
                paragraph_hash=hash_value,
                plan_id=plan_id,
                changed_at=changed_at,
                reason=reason,
            )
            return {
                "target_type": target_type,
                "hash": hash_value,
                "previous_metadata": previous_metadata,
                "updated_metadata": updated,
                "cascade": cascade,
            }
        previous = self.metadata_store.get_relation(hash_value)
        if previous is None:
            return {}
        previous_metadata = coerce_metadata_dict(previous.get("metadata"))
        updated = self.metadata_store.update_relation_metadata(hash_value, patch, merge=True)
        if updated is None:
            return {}
        self.metadata_store.mark_relations_inactive([hash_value], inactive_since=valid_to)
        return {
            "target_type": target_type,
            "hash": hash_value,
            "previous_metadata": previous_metadata,
            "updated_metadata": updated,
            "previous_is_inactive": bool(previous.get("is_inactive", False)),
            "previous_inactive_since": previous.get("inactive_since"),
        }

    @staticmethod
    def _normalize_fuzzy_modify_scope(scope: str) -> str:
        token = str(scope or "").strip().lower()
        aliases = {
            "profile": "person_profile",
            "person": "person_profile",
            "person_fact": "person_profile",
            "memory": "memory",
            "general": "memory",
            "chat": "memory",
        }
        return aliases.get(token, token or "person_profile")

    @staticmethod
    def _fuzzy_modify_cfg_enabled() -> bool:
        memory_cfg = global_config.a_memorix.integration
        return bool(getattr(memory_cfg, "fuzzy_modify_enabled", True))

    @staticmethod
    def _fuzzy_modify_cfg_auto_execute_enabled() -> bool:
        memory_cfg = global_config.a_memorix.integration
        return bool(getattr(memory_cfg, "fuzzy_modify_auto_execute_enabled", False))

    @staticmethod
    def _fuzzy_modify_cfg_confirm_threshold() -> float:
        memory_cfg = global_config.a_memorix.integration
        return float(getattr(memory_cfg, "fuzzy_modify_confirm_threshold", 0.85) or 0.85)

    @staticmethod
    def _fuzzy_modify_cfg_candidate_limit() -> int:
        memory_cfg = global_config.a_memorix.integration
        return max(1, int(getattr(memory_cfg, "fuzzy_modify_candidate_limit", 20) or 20))

    @staticmethod
    def _fuzzy_modify_cfg_max_targets() -> int:
        memory_cfg = global_config.a_memorix.integration
        return max(1, int(getattr(memory_cfg, "fuzzy_modify_max_targets", 5) or 5))

    @staticmethod
    def _fuzzy_modify_cfg_allow_global_scope() -> bool:
        memory_cfg = global_config.a_memorix.integration
        return bool(getattr(memory_cfg, "fuzzy_modify_allow_global_scope", False))

    def _adjust_relation_confidence(self, hashes: List[str], *, delta: float) -> Dict[str, float]:
        assert self.metadata_store
        normalized = [str(item or "").strip() for item in hashes if str(item or "").strip()]
        if not normalized:
            return {}
        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        chunk_size = 200
        for index in range(0, len(normalized), chunk_size):
            chunk = normalized[index : index + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cursor.execute(
                f"""
                UPDATE relations
                SET confidence = MAX(0.0, COALESCE(confidence, 0.0) + ?)
                WHERE hash IN ({placeholders})
                """,
                tuple([float(delta)] + chunk),
            )
        conn.commit()
        statuses = self.metadata_store.get_relation_status_batch(normalized)
        return {hash_value: float((statuses.get(hash_value) or {}).get("weight", 0.0) or 0.0) for hash_value in normalized}

    def _apply_v5_relation_action(self, *, action: str, hashes: List[str], strength: float = 1.0) -> Dict[str, Any]:
        assert self.metadata_store
        act = str(action or "").strip().lower()
        normalized = [str(item or "").strip() for item in hashes if str(item or "").strip()]
        if not normalized:
            return {"success": False, "error": "未命中可维护关系"}

        now = time.time()
        strength_value = max(0.1, float(strength or 1.0))
        prune_threshold = max(0.0, float(self._cfg("memory.prune_threshold", 0.1) or 0.1))
        detail = ""

        if act == "reinforce":
            weights = self._adjust_relation_confidence(normalized, delta=0.5 * strength_value)
            protect_hours = max(1.0, 24.0 * strength_value)
            self.metadata_store.reinforce_relations(normalized)
            self.metadata_store.mark_relations_active(normalized, boost_weight=max(prune_threshold, 0.1))
            self.metadata_store.update_relations_protection(
                normalized,
                protected_until=now + protect_hours * 3600.0,
                last_reinforced=now,
            )
            detail = f"reinforce {len(normalized)} 条关系"
        elif act == "weaken":
            weights = self._adjust_relation_confidence(normalized, delta=-0.5 * strength_value)
            to_freeze = [hash_value for hash_value, weight in weights.items() if weight <= prune_threshold]
            if to_freeze:
                self.metadata_store.mark_relations_inactive(to_freeze, inactive_since=now)
            detail = f"weaken {len(normalized)} 条关系"
        elif act == "remember_forever":
            self.metadata_store.mark_relations_active(normalized, boost_weight=max(prune_threshold, 0.1))
            self.metadata_store.update_relations_protection(normalized, protected_until=0.0, is_pinned=True)
            weights = {hash_value: float((self.metadata_store.get_relation_status_batch([hash_value]).get(hash_value) or {}).get("weight", 0.0) or 0.0) for hash_value in normalized}
            detail = f"remember_forever {len(normalized)} 条关系"
        elif act == "forget":
            weights = self._adjust_relation_confidence(normalized, delta=-2.0 * strength_value)
            self.metadata_store.update_relations_protection(normalized, protected_until=0.0, is_pinned=False)
            self.metadata_store.mark_relations_inactive(normalized, inactive_since=now)
            detail = f"forget {len(normalized)} 条关系"
        else:
            return {"success": False, "error": f"不支持的 V5 动作: {act}"}

        self._rebuild_graph_from_metadata()
        self._last_maintenance_at = now
        self._persist()
        statuses = self.metadata_store.get_relation_status_batch(normalized)
        return {
            "success": True,
            "detail": detail,
            "hashes": normalized,
            "count": len(normalized),
            "weights": weights,
            "statuses": statuses,
        }

    async def _ensure_vector_for_text(
        self,
        *,
        item_hash: str,
        text: str,
        vector_store: Optional[VectorStore] = None,
    ) -> bool:
        target_store = vector_store or self.vector_store
        if target_store is None or self.embedding_manager is None:
            return False
        token = str(item_hash or "").strip()
        content = str(text or "").strip()
        if not token or not content:
            return False
        embedding = await self.embedding_manager.encode([content])
        if getattr(embedding, "ndim", 1) == 1:
            embedding = embedding.reshape(1, -1)
        if getattr(embedding, "size", 0) <= 0:
            return False
        try:
            target_store.add(embedding, [token])
            return True
        except Exception as exc:
            logger.warning(f"重建向量失败: {exc}")
            return False

    async def _ensure_relation_vector(self, relation: Dict[str, Any]) -> bool:
        if not bool(self.relation_vectors_enabled):
            return False
        relation_service = self.relation_write_service
        if relation_service is not None:
            result = await relation_service.ensure_relation_vector(
                hash_value=str(relation.get("hash", "") or ""),
                subject=str(relation.get("subject", "") or "").strip(),
                predicate=str(relation.get("predicate", "") or "").strip(),
                obj=str(relation.get("object", "") or "").strip(),
                typed_id=self._dual_vector_pools_enabled(),
            )
            return bool(result.vector_written or result.vector_already_exists)
        return await self._ensure_vector_for_text(
            item_hash=str(relation.get("hash", "") or ""),
            text=RelationWriteService.build_relation_vector_text(
                str(relation.get("subject", "") or "").strip(),
                str(relation.get("predicate", "") or "").strip(),
                str(relation.get("object", "") or "").strip(),
            ),
        )

    async def _ensure_paragraph_vector(self, paragraph: Dict[str, Any]) -> bool:
        return await self._ensure_vector_for_text(
            item_hash=str(paragraph.get("hash", "") or ""),
            text=str(paragraph.get("content", "") or ""),
            vector_store=self._paragraph_store(),
        )

    async def _ensure_entity_vector(self, entity: Dict[str, Any]) -> bool:
        if self._dual_vector_pools_enabled():
            return await self._ensure_vector_for_text(
                item_hash=self._graph_vector_id("entity", str(entity.get("hash", "") or "")),
                text=str(entity.get("name", "") or ""),
                vector_store=self._graph_vector_store(),
            )
        return await self._ensure_vector_for_text(
            item_hash=str(entity.get("hash", "") or ""),
            text=str(entity.get("name", "") or ""),
        )

    async def _restore_relation_hashes(
        self,
        hashes: List[str],
        *,
        payloads: Optional[Dict[str, Dict[str, Any]]] = None,
        rebuild_graph: bool = True,
        persist: bool = True,
    ) -> Dict[str, Any]:
        assert self.metadata_store
        restored: List[str] = []
        failures: List[Dict[str, str]] = []
        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        payload_map = payloads or {}
        for hash_value in [str(item or "").strip() for item in hashes if str(item or "").strip()]:
            relation = self.metadata_store.restore_relation(hash_value)
            if relation is None:
                relation = self.metadata_store.get_relation(hash_value)
            if relation is None:
                failures.append({"hash": hash_value, "error": "relation 不存在"})
                continue
            payload = payload_map.get(hash_value) if isinstance(payload_map.get(hash_value), dict) else {}
            paragraph_hashes = self._tokens(payload.get("paragraph_hashes"))
            for paragraph_hash in paragraph_hashes:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO paragraph_relations (paragraph_hash, relation_hash)
                    VALUES (?, ?)
                    """,
                    (paragraph_hash, hash_value),
                )
            await self._ensure_relation_vector({**relation, "hash": hash_value})
            restored.append(hash_value)
        conn.commit()
        if restored and rebuild_graph:
            self._rebuild_graph_from_metadata()
        if restored and persist:
            self._persist()
        return {"restored_hashes": restored, "restored_count": len(restored), "failures": failures}

    @staticmethod
    def _profile_evidence_type_from_source(source: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        meta = metadata if isinstance(metadata, dict) else {}
        source_type = str(meta.get("source_type", "") or "").strip()
        if source_type in {"person_fact", "chat_summary"}:
            return source_type
        token = str(source or meta.get("source", "") or "").strip()
        if token.startswith("person_fact:"):
            return "person_fact"
        if token.startswith("chat_summary:"):
            return "chat_summary"
        return "paragraph"

    @staticmethod
    def _profile_relation_content(relation: Dict[str, Any]) -> str:
        subject = str(relation.get("subject", "") or "").strip()
        predicate = str(relation.get("predicate", "") or "").strip()
        obj = str(relation.get("object", "") or "").strip()
        if subject and predicate and obj:
            return f"{subject} -[{predicate}]-> {obj}"
        return " ".join(item for item in (subject, predicate, obj) if item).strip()

    def _build_profile_relation_evidence_item(self, relation: Dict[str, Any], *, index: int) -> Dict[str, Any]:
        relation_hash = str(relation.get("hash", "") or "").strip()
        metadata = coerce_metadata_dict(relation.get("metadata"))
        return {
            "evidence_key": f"relation:{relation_hash or index}",
            "evidence_type": "relation",
            "hash": relation_hash,
            "content": self._profile_relation_content(relation),
            "source": str(relation.get("source_paragraph", "") or metadata.get("source", "") or "").strip(),
            "source_type": "relation",
            "metadata": metadata,
            "score": None,
            "confidence": relation.get("confidence"),
            "correction_mode": "delete_relation",
            "deletable": bool(relation_hash),
            "not_deletable_reason": "" if relation_hash else "缺少关系 hash",
            "raw": relation,
        }

    def _build_profile_paragraph_evidence_item(
        self,
        item: Dict[str, Any],
        *,
        index: int,
        fallback_hash: str = "",
    ) -> Dict[str, Any]:
        hash_value = str(item.get("hash", "") or fallback_hash or "").strip()
        metadata = coerce_metadata_dict(item.get("metadata"))
        source = str(item.get("source", "") or metadata.get("source", "") or "").strip()
        content = str(item.get("content", "") or "").strip()
        source_type = self._profile_evidence_type_from_source(source, metadata)
        is_deleted = False
        if hash_value:
            try:
                paragraph = self.metadata_store.get_paragraph(hash_value) if self.metadata_store else None
            except Exception:
                paragraph = None
            if isinstance(paragraph, dict):
                paragraph_metadata = coerce_metadata_dict(paragraph.get("metadata"))
                metadata = {**paragraph_metadata, **metadata}
                source = source or str(paragraph.get("source", "") or "").strip()
                content = content or str(paragraph.get("content", "") or "").strip()
                source_type = self._profile_evidence_type_from_source(source, metadata)
                is_deleted = bool(paragraph.get("is_deleted", 0))
        return {
            "evidence_key": f"{source_type}:{hash_value or index}",
            "evidence_type": source_type,
            "hash": hash_value,
            "content": self._trim_text(content, 260),
            "source": source,
            "source_type": source_type,
            "metadata": metadata,
            "score": item.get("score"),
            "confidence": None,
            "correction_mode": "delete_paragraph",
            "deletable": bool(hash_value) and not is_deleted,
            "not_deletable_reason": "" if hash_value and not is_deleted else ("证据已删除" if is_deleted else "缺少段落 hash"),
            "raw": item,
        }

    def _build_profile_evidence_items(self, profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        assert self.metadata_store is not None
        evidence: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def append(item: Dict[str, Any]) -> None:
            evidence_type = str(item.get("evidence_type", "") or "").strip()
            hash_value = str(item.get("hash", "") or "").strip()
            key = (evidence_type, hash_value or str(item.get("evidence_key", "") or ""))
            if not key[0] or key in seen:
                return
            seen.add(key)
            evidence.append(item)

        for index, relation in enumerate(profile.get("relation_edges") or [], start=1):
            if isinstance(relation, dict):
                append(self._build_profile_relation_evidence_item(relation, index=index))

        for index, item in enumerate(profile.get("vector_evidence") or [], start=1):
            if isinstance(item, dict):
                append(self._build_profile_paragraph_evidence_item(item, index=index))

        for index, hash_value in enumerate(self._tokens(profile.get("evidence_ids")), start=1):
            if any(str(item.get("hash", "") or "").strip() == hash_value for item in evidence):
                continue
            paragraph = self.metadata_store.get_paragraph(hash_value)
            if isinstance(paragraph, dict):
                append(
                    self._build_profile_paragraph_evidence_item(
                        {
                            "hash": hash_value,
                            "content": str(paragraph.get("content", "") or ""),
                            "source": str(paragraph.get("source", "") or ""),
                            "metadata": coerce_metadata_dict(paragraph.get("metadata")),
                        },
                        index=index,
                    )
                )
                continue
            relation = self.metadata_store.get_relation(hash_value)
            if isinstance(relation, dict):
                append(self._build_profile_relation_evidence_item(relation, index=index))

        return evidence

    def _profile_evidence_response(self, profile: Dict[str, Any], *, requested_person_id: str, limit: int) -> Dict[str, Any]:
        if not bool(profile.get("success")):
            return {
                "success": False,
                "error": str(profile.get("error", "") or "人物画像查询失败"),
                "person_id": str(profile.get("person_id", "") or requested_person_id),
                "evidence": [],
            }
        evidence = self._build_profile_evidence_items(profile)
        return {
            "success": True,
            "person_id": str(profile.get("person_id", "") or requested_person_id),
            "person_name": str(profile.get("person_name", "") or ""),
            "profile_text": str(profile.get("profile_text", "") or ""),
            "auto_profile_text": str(profile.get("auto_profile_text", "") or profile.get("profile_text", "") or ""),
            "profile_version": profile.get("profile_version"),
            "updated_at": profile.get("updated_at"),
            "expires_at": profile.get("expires_at"),
            "profile_source": str(profile.get("profile_source", "") or "auto_snapshot"),
            "has_manual_override": bool(profile.get("has_manual_override", False)),
            "manual_override_text": str(profile.get("manual_override_text", "") or ""),
            "evidence": evidence[: max(1, int(limit or 12))],
            "evidence_count": len(evidence),
            "raw_profile": profile,
        }

    async def _profile_evidence_admin(
        self,
        *,
        person_id: str = "",
        person_keyword: str = "",
        limit: int = 12,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        profile = await self._query_person_profile_with_feedback_refresh(
            person_id=person_id,
            person_keyword=person_keyword,
            limit=max(1, int(limit or 12)),
            force_refresh=force_refresh,
            source_note="sdk_memory_kernel.memory_profile_admin.evidence",
        )
        requested_person_id = str(profile.get("person_id", "") or person_id or "").strip() if isinstance(profile, dict) else person_id
        return self._profile_evidence_response(profile if isinstance(profile, dict) else {}, requested_person_id=requested_person_id, limit=limit)

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
        normalized_type = str(evidence_type or "").strip().lower()
        normalized_hash = str(hash_value or "").strip()
        if normalized_type not in {"relation", "paragraph", "person_fact", "chat_summary"}:
            return {"success": False, "error": "不支持的画像证据类型"}
        if not normalized_hash:
            return {"success": False, "error": "画像证据 hash 不能为空"}

        evidence_payload = await self._profile_evidence_admin(
            person_id=person_id,
            person_keyword=person_keyword,
            limit=max(50, int(limit or 12)),
            force_refresh=False,
        )
        if not bool(evidence_payload.get("success")):
            return evidence_payload
        matched = None
        for item in evidence_payload.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("hash", "") or "").strip() != normalized_hash:
                continue
            item_type = str(item.get("evidence_type", "") or "").strip().lower()
            if normalized_type == item_type or (normalized_type == "paragraph" and item_type in {"person_fact", "chat_summary"}):
                matched = item
                break
        if matched is None:
            return {"success": False, "error": "当前画像证据中未找到目标 hash"}
        if not bool(matched.get("deletable", False)):
            return {
                "success": False,
                "error": str(matched.get("not_deletable_reason", "") or "该画像证据不可纠错"),
                "evidence": matched,
            }

        delete_mode = "relation" if normalized_type == "relation" else "paragraph"
        delete_result = await self._execute_delete_action(
            mode=delete_mode,
            selector={"hashes": [normalized_hash]},
            requested_by=requested_by or "webui",
            reason=reason or "profile_evidence_correction",
        )
        if bool(delete_result.get("success")):
            await self._invalidate_import_manifest_for_sources(delete_result)

        refreshed_profile: Dict[str, Any] = {}
        refreshed_evidence: Dict[str, Any] = {}
        if refresh and bool(delete_result.get("success")):
            refreshed_profile = await self.person_profile_service.query_person_profile(
                person_id=str(evidence_payload.get("person_id", "") or person_id),
                top_k=max(4, int(limit or 12)),
                force_refresh=True,
                source_note="sdk_memory_kernel.memory_profile_admin.correct_evidence",
            )
            refreshed_evidence = self._profile_evidence_response(
                refreshed_profile if isinstance(refreshed_profile, dict) else {},
                requested_person_id=str(evidence_payload.get("person_id", "") or person_id),
                limit=limit,
            )

        return {
            "success": bool(delete_result.get("success")),
            "person_id": str(evidence_payload.get("person_id", "") or person_id),
            "evidence": matched,
            "delete_result": delete_result,
            "operation_id": str(delete_result.get("operation_id", "") or ""),
            "refreshed_profile": refreshed_profile,
            "refreshed_evidence": refreshed_evidence,
            "error": str(delete_result.get("error", "") or ""),
        }

    @staticmethod
    def _selector_dict(selector: Any) -> Dict[str, Any]:
        if isinstance(selector, dict):
            return dict(selector)
        if isinstance(selector, (list, tuple)):
            return {"items": list(selector)}
        token = str(selector or "").strip()
        return {"query": token} if token else {}

    def _resolve_paragraph_targets(self, selector: Any, *, include_deleted: bool = False) -> List[Dict[str, Any]]:
        assert self.metadata_store
        raw = self._selector_dict(selector)
        rows: List[Dict[str, Any]] = []
        hashes = self._merge_tokens(raw.get("hashes"), raw.get("items"), [raw.get("hash")])
        for hash_value in hashes:
            row = self.metadata_store.get_paragraph(hash_value)
            if row is None:
                continue
            if not include_deleted and bool(row.get("is_deleted", 0)):
                continue
            rows.append(row)
        if rows:
            return rows
        query = str(raw.get("query", "") or raw.get("content", "") or "").strip()
        if not query:
            return []
        if len(query) == 64 and all(ch in "0123456789abcdef" for ch in query.lower()):
            row = self.metadata_store.get_paragraph(query)
            if row is None:
                return []
            if not include_deleted and bool(row.get("is_deleted", 0)):
                return []
            return [row]
        matches = self.metadata_store.search_paragraphs_by_content(query)
        return [row for row in matches if include_deleted or not bool(row.get("is_deleted", 0))]

    def _resolve_entity_targets(self, selector: Any, *, include_deleted: bool = False) -> List[Dict[str, Any]]:
        assert self.metadata_store
        raw = self._selector_dict(selector)
        rows: List[Dict[str, Any]] = []
        hashes = self._merge_tokens(raw.get("hashes"), raw.get("items"), [raw.get("hash")])
        for hash_value in hashes:
            row = self.metadata_store.get_entity(hash_value)
            if row is None:
                continue
            if not include_deleted and bool(row.get("is_deleted", 0)):
                continue
            rows.append(row)
        names = self._merge_tokens(raw.get("names"), [raw.get("name")], [raw.get("query")])
        for name in names:
            if not name:
                continue
            matches = self.metadata_store.query(
                """
                SELECT *
                FROM entities
                WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
                   OR hash = ?
                ORDER BY appearance_count DESC, created_at ASC
                """,
                (name, compute_hash(str(name).strip().lower())),
            )
            for row in matches:
                if not include_deleted and bool(row.get("is_deleted", 0)):
                    continue
                rows.append(self.metadata_store._row_to_dict(row, "entity") if hasattr(self.metadata_store, "_row_to_dict") else row)
        dedup: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            token = str(row.get("hash", "") or "").strip()
            if token and token not in dedup:
                dedup[token] = row
        return list(dedup.values())

    def _resolve_source_targets(self, selector: Any) -> List[str]:
        raw = self._selector_dict(selector)
        return self._merge_tokens(raw.get("sources"), [raw.get("source")], [raw.get("query")], raw.get("items"))

    def _snapshot_relation_item(self, hash_value: str) -> Optional[Dict[str, Any]]:
        assert self.metadata_store
        relation = self.metadata_store.get_relation(hash_value)
        if relation is None:
            relation = self.metadata_store.get_deleted_relation(hash_value)
        if relation is None:
            return None
        paragraph_hashes = [
            str(row.get("paragraph_hash", "") or "").strip()
            for row in self.metadata_store.query(
                "SELECT paragraph_hash FROM paragraph_relations WHERE relation_hash = ? ORDER BY paragraph_hash ASC",
                (hash_value,),
            )
            if str(row.get("paragraph_hash", "") or "").strip()
        ]
        return {
            "item_type": "relation",
            "item_hash": hash_value,
            "item_key": hash_value,
            "payload": {
                "relation": relation,
                "paragraph_hashes": paragraph_hashes,
            },
        }

    def _snapshot_paragraph_item(self, hash_value: str) -> Optional[Dict[str, Any]]:
        assert self.metadata_store
        paragraph = self.metadata_store.get_paragraph(hash_value)
        if paragraph is None:
            return None
        entity_links = [
            {
                "paragraph_hash": hash_value,
                "entity_hash": str(row.get("entity_hash", "") or ""),
                "mention_count": int(row.get("mention_count", 1) or 1),
            }
            for row in self.metadata_store.query(
                """
                SELECT paragraph_hash, entity_hash, mention_count
                FROM paragraph_entities
                WHERE paragraph_hash = ?
                ORDER BY entity_hash ASC
                """,
                (hash_value,),
            )
        ]
        relation_hashes = [
            str(row.get("relation_hash", "") or "").strip()
            for row in self.metadata_store.query(
                """
                SELECT relation_hash
                FROM paragraph_relations
                WHERE paragraph_hash = ?
                ORDER BY relation_hash ASC
                """,
                (hash_value,),
            )
            if str(row.get("relation_hash", "") or "").strip()
        ]
        return {
            "item_type": "paragraph",
            "item_hash": hash_value,
            "item_key": hash_value,
            "payload": {
                "paragraph": paragraph,
                "entity_links": entity_links,
                "relation_hashes": relation_hashes,
                "external_refs": self.metadata_store.list_external_memory_refs_by_paragraphs([hash_value]),
            },
        }

    def _snapshot_entity_item(self, hash_value: str) -> Optional[Dict[str, Any]]:
        assert self.metadata_store
        entity = self.metadata_store.get_entity(hash_value)
        if entity is None:
            return None
        paragraph_links = [
            {
                "paragraph_hash": str(row.get("paragraph_hash", "") or ""),
                "entity_hash": hash_value,
                "mention_count": int(row.get("mention_count", 1) or 1),
            }
            for row in self.metadata_store.query(
                """
                SELECT paragraph_hash, mention_count
                FROM paragraph_entities
                WHERE entity_hash = ?
                ORDER BY paragraph_hash ASC
                """,
                (hash_value,),
            )
        ]
        return {
            "item_type": "entity",
            "item_hash": hash_value,
            "item_key": hash_value,
            "payload": {
                "entity": entity,
                "paragraph_links": paragraph_links,
            },
        }

    def _relation_has_remaining_paragraphs(self, relation_hash: str, removing_hashes: Sequence[str]) -> bool:
        assert self.metadata_store
        excluded = [str(item or "").strip() for item in removing_hashes if str(item or "").strip()]
        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        if excluded:
            placeholders = ",".join(["?"] * len(excluded))
            cursor.execute(
                f"""
                SELECT p.hash, p.metadata
                FROM paragraph_relations pr
                JOIN paragraphs p ON p.hash = pr.paragraph_hash
                WHERE pr.relation_hash = ?
                  AND pr.paragraph_hash NOT IN ({placeholders})
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                """,
                tuple([relation_hash] + excluded),
            )
        else:
            cursor.execute(
                """
                SELECT p.hash, p.metadata
                FROM paragraph_relations pr
                JOIN paragraphs p ON p.hash = pr.paragraph_hash
                WHERE pr.relation_hash = ?
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                """,
                (relation_hash,),
            )
        now = time.time()
        for row in cursor.fetchall():
            paragraph = self.metadata_store._row_to_dict(row, "paragraph")
            metadata = coerce_metadata_dict(paragraph.get("metadata"))
            memory_change = metadata.get("memory_change") if isinstance(metadata.get("memory_change"), dict) else {}
            valid_to = self._optional_float(memory_change.get("valid_to"))
            if valid_to is None or valid_to > now:
                return True
        return False

    def _build_delete_preview_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        item_type = str(item.get("item_type", "") or "").strip()
        item_hash = str(item.get("item_hash", "") or "").strip()
        item_key = str(item.get("item_key", "") or item_hash).strip()
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        preview = {
            "item_type": item_type,
            "item_hash": item_hash,
            "item_key": item_key,
        }
        if item_type == "entity":
            entity = payload.get("entity") if isinstance(payload.get("entity"), dict) else {}
            name = str(entity.get("name", "") or item_key).strip()
            preview["label"] = name
            preview["preview"] = name
        elif item_type == "relation":
            relation = payload.get("relation") if isinstance(payload.get("relation"), dict) else {}
            subject = str(relation.get("subject", "") or "").strip()
            predicate = str(relation.get("predicate", "") or "").strip()
            obj = str(relation.get("object", "") or "").strip()
            text = self._format_relation_text(subject, predicate, obj)
            preview["label"] = text or item_key
            preview["preview"] = text or item_key
        elif item_type == "paragraph":
            paragraph = payload.get("paragraph") if isinstance(payload.get("paragraph"), dict) else {}
            content = str(paragraph.get("content", "") or "").strip()
            source = str(paragraph.get("source", "") or "").strip()
            preview["label"] = source or item_key
            preview["preview"] = self._trim_text(content)
            preview["source"] = source
        return preview

    def _build_standard_delete_result(
        self,
        *,
        mode: str,
        operation_id: str = "",
        counts: Optional[Dict[str, Any]] = None,
        sources: Optional[Sequence[str]] = None,
        deleted_entity_count: int = 0,
        deleted_relation_count: int = 0,
        deleted_paragraph_count: int = 0,
        deleted_source_count: int = 0,
        deleted_vector_count: int = 0,
        requested_source_count: int = 0,
        matched_source_count: int = 0,
        error: str = "",
    ) -> Dict[str, Any]:
        normalized_counts = dict(counts or {})
        normalized_counts.setdefault("entities", int(normalized_counts.get("entities", 0) or 0))
        normalized_counts.setdefault("relations", int(normalized_counts.get("relations", 0) or 0))
        normalized_counts.setdefault("paragraphs", int(normalized_counts.get("paragraphs", 0) or 0))
        normalized_counts.setdefault("sources", int(normalized_counts.get("sources", 0) or 0))
        if requested_source_count:
            normalized_counts["requested_sources"] = int(requested_source_count or 0)
        if matched_source_count:
            normalized_counts["matched_sources"] = int(matched_source_count or 0)

        deleted_count = (
            int(deleted_entity_count or 0)
            + int(deleted_relation_count or 0)
            + int(deleted_paragraph_count or 0)
            + int(deleted_source_count or 0)
        )
        return {
            "success": bool(not error and deleted_count > 0),
            "mode": str(mode or "").strip().lower(),
            "operation_id": str(operation_id or "").strip(),
            "counts": normalized_counts,
            "sources": [str(item or "").strip() for item in (sources or []) if str(item or "").strip()],
            "deleted_count": deleted_count,
            "deleted_entity_count": int(deleted_entity_count or 0),
            "deleted_relation_count": int(deleted_relation_count or 0),
            "deleted_paragraph_count": int(deleted_paragraph_count or 0),
            "deleted_source_count": int(deleted_source_count or 0),
            "deleted_vector_count": int(deleted_vector_count or 0),
            "requested_source_count": int(requested_source_count or 0),
            "matched_source_count": int(matched_source_count or 0),
            "error": str(error or ""),
        }

    async def _build_delete_plan(self, *, mode: str, selector: Any) -> Dict[str, Any]:
        assert self.metadata_store
        act_mode = str(mode or "").strip().lower()
        normalized_selector = self._selector_dict(selector)
        items: List[Dict[str, Any]] = []
        counts = {"relations": 0, "paragraphs": 0, "entities": 0, "sources": 0}
        vector_ids: List[str] = []
        sources: List[str] = []
        target_hashes: Dict[str, List[str]] = {
            "relations": [],
            "paragraphs": [],
            "entities": [],
            "sources": [],
            "matched_sources": [],
        }
        seen_items: set[tuple[str, str]] = set()
        relation_hashes: List[str] = []
        paragraph_hashes: List[str] = []
        entity_hashes: List[str] = []
        paragraph_relation_candidates: List[str] = []

        def append_item(snapshot: Optional[Dict[str, Any]]) -> None:
            if not isinstance(snapshot, dict):
                return
            item_type = str(snapshot.get("item_type", "") or "").strip()
            item_hash = str(snapshot.get("item_hash", "") or snapshot.get("item_key", "") or "").strip()
            if not item_type or not item_hash:
                return
            key = (item_type, item_hash)
            if key in seen_items:
                return
            seen_items.add(key)
            items.append(snapshot)

        def append_relation_hash(hash_value: str) -> None:
            token = str(hash_value or "").strip()
            if not token or token in relation_hashes:
                return
            row = self.metadata_store.get_relation(token)
            if row is None:
                return
            relation_hashes.append(token)
            append_item(self._snapshot_relation_item(token))
            vector_ids.append(token)

        def append_paragraph_row(row: Optional[Dict[str, Any]]) -> None:
            if not isinstance(row, dict):
                return
            paragraph_hash = str(row.get("hash", "") or "").strip()
            if not paragraph_hash or paragraph_hash in paragraph_hashes or bool(row.get("is_deleted", 0)):
                return
            paragraph_hashes.append(paragraph_hash)
            snapshot = self._snapshot_paragraph_item(paragraph_hash)
            append_item(snapshot)
            vector_ids.append(paragraph_hash)
            paragraph = (snapshot or {}).get("payload", {}).get("paragraph") if isinstance((snapshot or {}).get("payload"), dict) else {}
            source = str((paragraph or {}).get("source", "") or "").strip()
            if source:
                sources.append(source)
            paragraph_relation_candidates.extend(self._tokens(((snapshot or {}).get("payload") or {}).get("relation_hashes")))

        def append_entity_row(row: Optional[Dict[str, Any]]) -> None:
            if not isinstance(row, dict):
                return
            entity_hash = str(row.get("hash", "") or "").strip()
            if not entity_hash or entity_hash in entity_hashes or bool(row.get("is_deleted", 0)):
                return
            entity_hashes.append(entity_hash)
            append_item(self._snapshot_entity_item(entity_hash))
            vector_ids.append(entity_hash)

        if act_mode == "relation":
            direct_hashes = self._merge_tokens(
                normalized_selector.get("hashes"),
                normalized_selector.get("items"),
                [normalized_selector.get("hash")],
            )
            query_hashes = self._resolve_relation_hashes(str(normalized_selector.get("query", "") or ""))
            for hash_value in direct_hashes or query_hashes:
                append_relation_hash(hash_value)
            counts["relations"] = len(relation_hashes)
            target_hashes["relations"] = list(relation_hashes)

        elif act_mode in {"paragraph", "source"}:
            paragraph_rows: List[Dict[str, Any]] = []
            if act_mode == "source":
                source_tokens = self._resolve_source_targets(normalized_selector)
                target_hashes["sources"] = source_tokens
                counts["requested_sources"] = len(source_tokens)
                matched_source_tokens: List[str] = []
                for source in source_tokens:
                    source_rows = self.metadata_store.query(
                        """
                        SELECT *
                        FROM paragraphs
                        WHERE source = ?
                          AND (is_deleted IS NULL OR is_deleted = 0)
                        ORDER BY created_at ASC
                        """,
                        (source,),
                    )
                    if source_rows:
                        matched_source_tokens.append(source)
                        sources.append(source)
                        paragraph_rows.extend(source_rows)
                target_hashes["matched_sources"] = matched_source_tokens
                counts["sources"] = len(matched_source_tokens)
                counts["matched_sources"] = len(matched_source_tokens)
            else:
                paragraph_rows = self._resolve_paragraph_targets(normalized_selector, include_deleted=False)
            for row in paragraph_rows:
                append_paragraph_row(row)
            target_hashes["paragraphs"] = list(paragraph_hashes)
            counts["paragraphs"] = len(paragraph_hashes)

            for relation_hash in self._tokens(paragraph_relation_candidates):
                if not self._relation_has_remaining_paragraphs(relation_hash, paragraph_hashes):
                    append_relation_hash(relation_hash)
            target_hashes["relations"] = list(relation_hashes)
            counts["relations"] = len(relation_hashes)

        elif act_mode == "entity":
            entity_rows = self._resolve_entity_targets(normalized_selector, include_deleted=False)
            for row in entity_rows:
                append_entity_row(row)
            target_hashes["entities"] = list(entity_hashes)
            counts["entities"] = len(entity_hashes)
            entity_names = [str(row.get("name", "") or "").strip() for row in entity_rows if str(row.get("name", "") or "").strip()]
            for entity_name in entity_names:
                for relation in self.metadata_store.get_relations(subject=entity_name) + self.metadata_store.get_relations(object=entity_name):
                    append_relation_hash(str(relation.get("hash", "") or "").strip())
            target_hashes["relations"] = list(relation_hashes)
            counts["relations"] = len(relation_hashes)
        elif act_mode == "mixed":
            source_tokens = self._merge_tokens(normalized_selector.get("sources"), [normalized_selector.get("source")])
            target_hashes["sources"] = list(source_tokens)
            counts["requested_sources"] = len(source_tokens)
            matched_source_tokens: List[str] = []

            for row in self._resolve_entity_targets({"hashes": normalized_selector.get("entity_hashes")}, include_deleted=False):
                append_entity_row(row)
            target_hashes["entities"] = list(entity_hashes)
            counts["entities"] = len(entity_hashes)

            for row in self._resolve_paragraph_targets({"hashes": normalized_selector.get("paragraph_hashes")}, include_deleted=False):
                append_paragraph_row(row)

            for source in source_tokens:
                source_rows = self.metadata_store.query(
                    """
                    SELECT *
                    FROM paragraphs
                    WHERE source = ?
                      AND (is_deleted IS NULL OR is_deleted = 0)
                    ORDER BY created_at ASC
                    """,
                    (source,),
                )
                if source_rows:
                    matched_source_tokens.append(source)
                    sources.append(source)
                    for row in source_rows:
                        append_paragraph_row(row)

            target_hashes["paragraphs"] = list(paragraph_hashes)
            counts["paragraphs"] = len(paragraph_hashes)
            target_hashes["matched_sources"] = matched_source_tokens
            counts["sources"] = len(matched_source_tokens)
            counts["matched_sources"] = len(matched_source_tokens)

            for hash_value in self._tokens(normalized_selector.get("relation_hashes")):
                append_relation_hash(hash_value)

            entity_names = [
                str(row.get("name", "") or "").strip()
                for row in self._resolve_entity_targets({"hashes": entity_hashes}, include_deleted=False)
                if str(row.get("name", "") or "").strip()
            ]
            for entity_name in entity_names:
                for relation in self.metadata_store.get_relations(subject=entity_name) + self.metadata_store.get_relations(object=entity_name):
                    append_relation_hash(str(relation.get("hash", "") or "").strip())

            for relation_hash in self._tokens(paragraph_relation_candidates):
                if not self._relation_has_remaining_paragraphs(relation_hash, paragraph_hashes):
                    append_relation_hash(relation_hash)

            target_hashes["relations"] = list(relation_hashes)
            counts["relations"] = len(relation_hashes)
        else:
            return {"success": False, "error": f"不支持的 delete mode: {act_mode}"}

        sources = self._tokens(sources)
        vector_ids = self._tokens(vector_ids)
        primary_count = counts.get(f"{act_mode}s", 0) if act_mode not in {"source", "mixed"} else counts.get("matched_sources", 0)
        success = (
            primary_count > 0 or counts.get("paragraphs", 0) > 0 or counts.get("relations", 0) > 0 or counts.get("entities", 0) > 0
            if act_mode != "source"
            else (counts.get("matched_sources", 0) > 0 and counts.get("paragraphs", 0) > 0)
        )
        return {
            "success": success,
            "mode": act_mode,
            "selector": normalized_selector,
            "items": items,
            "counts": counts,
            "vector_ids": vector_ids,
            "sources": sources,
            "target_hashes": target_hashes,
            "requested_source_count": counts.get("requested_sources", 0) if act_mode == "source" else 0,
            "matched_source_count": counts.get("matched_sources", 0) if act_mode == "source" else 0,
            "error": "" if success else "未命中可删除内容",
        }

    async def _preview_delete_action(self, *, mode: str, selector: Any) -> Dict[str, Any]:
        plan = await self._build_delete_plan(mode=mode, selector=selector)
        if not plan.get("success", False):
            return {"success": False, "error": plan.get("error", "未命中可删除内容")}
        preview_items = [self._build_delete_preview_item(item) for item in plan.get("items", [])[:100]]
        return {
            "success": True,
            "mode": plan.get("mode"),
            "selector": plan.get("selector"),
            "counts": plan.get("counts", {}),
            "requested_source_count": int(plan.get("requested_source_count", 0) or 0),
            "matched_source_count": int(plan.get("matched_source_count", 0) or 0),
            "sources": plan.get("sources", []),
            "vector_ids": plan.get("vector_ids", []),
            "items": preview_items,
            "item_count": len(plan.get("items", [])),
            "dry_run": True,
        }

    async def _execute_delete_action(
        self,
        *,
        mode: str,
        selector: Any,
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        assert self.metadata_store
        plan = await self._build_delete_plan(mode=mode, selector=selector)
        if not plan.get("success", False):
            return {"success": False, "error": plan.get("error", "未命中可删除内容")}

        act_mode = str(plan.get("mode", "") or "").strip().lower()
        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        paragraph_hashes = self._tokens((plan.get("target_hashes") or {}).get("paragraphs"))
        entity_hashes = self._tokens((plan.get("target_hashes") or {}).get("entities"))
        relation_hashes = self._tokens((plan.get("target_hashes") or {}).get("relations"))
        requested_source_tokens = self._tokens((plan.get("target_hashes") or {}).get("sources"))
        matched_source_tokens = self._tokens((plan.get("target_hashes") or {}).get("matched_sources"))

        try:
            if paragraph_hashes:
                self.metadata_store.mark_as_deleted(paragraph_hashes, "paragraph")
                cursor.execute(
                    f"DELETE FROM paragraph_entities WHERE paragraph_hash IN ({','.join(['?'] * len(paragraph_hashes))})",
                    tuple(paragraph_hashes),
                )
                cursor.execute(
                    f"DELETE FROM paragraph_relations WHERE paragraph_hash IN ({','.join(['?'] * len(paragraph_hashes))})",
                    tuple(paragraph_hashes),
                )
                self.metadata_store.delete_external_memory_refs_by_paragraphs(paragraph_hashes)
            if act_mode == "source" and matched_source_tokens:
                for source in matched_source_tokens:
                    self.metadata_store.replace_episodes_for_source(source, [])

            if entity_hashes:
                self.metadata_store.mark_as_deleted(entity_hashes, "entity")
                cursor.execute(
                    f"DELETE FROM paragraph_entities WHERE entity_hash IN ({','.join(['?'] * len(entity_hashes))})",
                    tuple(entity_hashes),
                )

            conn.commit()

            deleted_relations = self.metadata_store.backup_and_delete_relations(relation_hashes)
            deleted_vectors = self._delete_vectors_by_type(
                paragraph_hashes=paragraph_hashes,
                entity_hashes=entity_hashes,
                relation_hashes=relation_hashes,
            )

            operation = self.metadata_store.create_delete_operation(
                mode=act_mode,
                selector=plan.get("selector"),
                items=plan.get("items", []),
                reason=reason,
                requested_by=requested_by,
                summary={
                    "counts": plan.get("counts", {}),
                    "sources": plan.get("sources", []),
                    "vector_ids": plan.get("vector_ids", []),
                    "deleted_relation_rows": deleted_relations,
                },
            )

            if plan.get("sources"):
                self.metadata_store._enqueue_episode_source_rebuilds(list(plan.get("sources") or []), reason="delete_admin_execute")
            self._rebuild_graph_from_metadata()
            self._persist()
            return self._build_standard_delete_result(
                mode=act_mode,
                operation_id=str(operation.get("operation_id", "") or ""),
                counts=plan.get("counts", {}),
                sources=plan.get("sources", []),
                deleted_entity_count=len(entity_hashes),
                deleted_relation_count=len(relation_hashes),
                deleted_paragraph_count=len(paragraph_hashes),
                deleted_source_count=len(matched_source_tokens),
                deleted_vector_count=int(deleted_vectors or 0),
                requested_source_count=len(requested_source_tokens),
                matched_source_count=len(matched_source_tokens),
                error="" if (entity_hashes or relation_hashes or paragraph_hashes or matched_source_tokens) else "未命中可删除内容",
            )
        except Exception as exc:
            conn.rollback()
            logger.warning(f"delete_admin execute 失败: {exc}")
            return self._build_standard_delete_result(mode=act_mode, error=str(exc))

    async def _invalidate_import_manifest_for_sources(self, result: Dict[str, Any]) -> None:
        if not isinstance(result, dict) or not result.get("success"):
            return
        manager = self.import_task_manager
        if manager is None:
            return
        sources = self._tokens(result.get("sources"))
        if not sources:
            return
        try:
            manifest_result = await manager.invalidate_manifest_for_sources(sources)
        except Exception as exc:
            logger.warning(f"删除来源后清理导入清单失败: sources={sources}, err={exc}")
            result["manifest_invalidation"] = {"success": False, "error": str(exc), "sources": sources}
            return
        result["manifest_invalidation"] = manifest_result

    async def _restore_delete_action(
        self,
        *,
        mode: str,
        selector: Any,
        operation_id: str = "",
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        del requested_by
        del reason
        assert self.metadata_store

        op_id = str(operation_id or "").strip()
        if op_id:
            operation = self.metadata_store.get_delete_operation(op_id)
            if operation is None:
                return {"success": False, "error": "operation 不存在"}
            return await self._restore_delete_operation(operation)

        act_mode = str(mode or "").strip().lower()
        if act_mode != "relation":
            return {"success": False, "error": "paragraph/entity/source 恢复必须提供 operation_id"}

        raw = self._selector_dict(selector)
        target = str(raw.get("query", "") or raw.get("target", "") or raw.get("hash", "") or "").strip()
        hashes = self._resolve_deleted_relation_hashes(target)
        if not hashes:
            return {"success": False, "error": "未命中可恢复关系"}
        result = await self._restore_relation_hashes(hashes)
        return {"success": bool(result.get("restored_count", 0) > 0), **result}

    async def _restore_delete_operation(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        assert self.metadata_store
        items = operation.get("items") if isinstance(operation.get("items"), list) else []
        entity_payloads: Dict[str, Dict[str, Any]] = {}
        paragraph_payloads: Dict[str, Dict[str, Any]] = {}
        relation_payloads: Dict[str, Dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("item_type", "") or "").strip()
            item_hash = str(item.get("item_hash", "") or "").strip()
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            if item_type == "entity" and item_hash:
                entity_payloads[item_hash] = payload
            elif item_type == "paragraph" and item_hash:
                paragraph_payloads[item_hash] = payload
            elif item_type == "relation" and item_hash:
                relation_payloads[item_hash] = payload

        restored_entities: List[str] = []
        restored_paragraphs: List[str] = []
        for hash_value, payload in entity_payloads.items():
            entity_row = payload.get("entity") if isinstance(payload.get("entity"), dict) else {}
            if entity_row:
                self.metadata_store.restore_entity_by_hash(hash_value)
                await self._ensure_entity_vector(entity_row)
                restored_entities.append(hash_value)
        for hash_value, payload in paragraph_payloads.items():
            paragraph_row = payload.get("paragraph") if isinstance(payload.get("paragraph"), dict) else {}
            if paragraph_row:
                self.metadata_store.restore_paragraph_by_hash(hash_value)
                await self._ensure_paragraph_vector(paragraph_row)
                restored_paragraphs.append(hash_value)

        restored_relations = await self._restore_relation_hashes(list(relation_payloads.keys()), payloads=relation_payloads, rebuild_graph=False, persist=False)

        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        for payload in entity_payloads.values():
            for link in payload.get("paragraph_links") or []:
                paragraph_hash = str(link.get("paragraph_hash", "") or "").strip()
                entity_hash = str(link.get("entity_hash", "") or "").strip()
                mention_count = max(1, int(link.get("mention_count", 1) or 1))
                if not paragraph_hash or not entity_hash:
                    continue
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO paragraph_entities (paragraph_hash, entity_hash, mention_count)
                    VALUES (?, ?, ?)
                    """,
                    (paragraph_hash, entity_hash, mention_count),
                )
        for payload in paragraph_payloads.values():
            for link in payload.get("entity_links") or []:
                paragraph_hash = str(link.get("paragraph_hash", "") or "").strip()
                entity_hash = str(link.get("entity_hash", "") or "").strip()
                mention_count = max(1, int(link.get("mention_count", 1) or 1))
                if not paragraph_hash or not entity_hash:
                    continue
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO paragraph_entities (paragraph_hash, entity_hash, mention_count)
                    VALUES (?, ?, ?)
                    """,
                    (paragraph_hash, entity_hash, mention_count),
                )
            for relation_hash in self._tokens(payload.get("relation_hashes")):
                paragraph_hash = str((payload.get("paragraph") or {}).get("hash", "") or "").strip()
                if not paragraph_hash or not relation_hash:
                    continue
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO paragraph_relations (paragraph_hash, relation_hash)
                    VALUES (?, ?)
                    """,
                    (paragraph_hash, relation_hash),
                )
            self.metadata_store.restore_external_memory_refs(list(payload.get("external_refs") or []))
        conn.commit()

        sources = self._tokens(
            [
                str(((payload.get("paragraph") or {}).get("source", "") or "")).strip()
                for payload in paragraph_payloads.values()
            ]
        )
        if sources:
            self.metadata_store._enqueue_episode_source_rebuilds(sources, reason="delete_admin_restore")
        self._rebuild_graph_from_metadata()
        self._persist()
        summary = {
            "restored_entities": restored_entities,
            "restored_paragraphs": restored_paragraphs,
            "restored_relations": restored_relations.get("restored_hashes", []),
            "sources": sources,
        }
        self.metadata_store.mark_delete_operation_restored(str(operation.get("operation_id", "") or ""), summary=summary)
        return {
            "success": True,
            "operation_id": str(operation.get("operation_id", "") or ""),
            **summary,
            "restored_relation_count": restored_relations.get("restored_count", 0),
            "relation_failures": restored_relations.get("failures", []),
        }

    async def _purge_deleted_memory(self, *, grace_hours: Optional[float], limit: int) -> Dict[str, Any]:
        assert self.metadata_store
        orphan_cfg = self._cfg("memory.orphan", {}) or {}
        grace = float(grace_hours) if grace_hours is not None else max(
            1.0,
            float(orphan_cfg.get("sweep_grace_hours", 24.0) or 24.0),
        )
        cutoff = time.time() - grace * 3600.0
        deleted_relation_hashes = self.metadata_store.purge_deleted_relations(cutoff_time=cutoff, limit=limit)
        dead_paragraphs = self.metadata_store.sweep_deleted_items("paragraph", grace * 3600.0)
        paragraph_hashes = [str(item[0] or "").strip() for item in dead_paragraphs if str(item[0] or "").strip()]
        dead_entities = self.metadata_store.sweep_deleted_items("entity", grace * 3600.0)
        entity_hashes = [str(item[0] or "").strip() for item in dead_entities if str(item[0] or "").strip()]
        entity_names = [str(item[1] or "").strip() for item in dead_entities if str(item[1] or "").strip()]

        if paragraph_hashes:
            self.metadata_store.physically_delete_paragraphs(paragraph_hashes)
        if entity_hashes:
            self.metadata_store.physically_delete_entities(entity_hashes)
        if entity_names:
            self.graph_store.delete_nodes(entity_names)
        self._delete_vectors_by_type(
            paragraph_hashes=paragraph_hashes,
            entity_hashes=entity_hashes,
            relation_hashes=deleted_relation_hashes,
        )
        self._rebuild_graph_from_metadata()
        self._persist()
        return {
            "success": True,
            "grace_hours": grace,
            "purged_deleted_relations": deleted_relation_hashes,
            "purged_paragraph_hashes": paragraph_hashes,
            "purged_entity_hashes": entity_hashes,
            "purged_counts": {
                "relations": len(deleted_relation_hashes),
                "paragraphs": len(paragraph_hashes),
                "entities": len(entity_hashes),
            },
        }

    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        if value in {None, ""}:
            return None
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        if value in {None, ""}:
            return None
        try:
            return int(value)
        except Exception:
            return None
