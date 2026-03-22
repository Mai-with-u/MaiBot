"""
Web Import Task Manager

为 A_Memorix WebUI 提供导入任务队列、状态管理、并发调度与取消/重试能力。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.common.logger import get_logger
from src.services import llm_service as llm_api

from ..storage import (
    parse_import_strategy,
    resolve_stored_knowledge_type,
    select_import_strategy,
    KnowledgeType,
    MetadataStore,
)
from ..storage.type_detection import looks_like_quote_text
from ..utils.import_payloads import normalize_paragraph_import_item
from ..utils.runtime_self_check import ensure_runtime_self_check
from ..utils.time_parser import normalize_time_meta
from ..storage.knowledge_types import ImportStrategy
from ..strategies.base import ProcessedChunk, KnowledgeType as StrategyKnowledgeType
from ..strategies.narrative import NarrativeStrategy
from ..strategies.factual import FactualStrategy
from ..strategies.quote import QuoteStrategy

logger = get_logger("A_Memorix.WebImportManager")


TASK_STATUS = {
    "queued",
    "preparing",
    "running",
    "cancel_requested",
    "cancelled",
    "completed",
    "completed_with_errors",
    "failed",
}

FILE_STATUS = {
    "queued",
    "preparing",
    "splitting",
    "extracting",
    "writing",
    "saving",
    "completed",
    "failed",
    "cancelled",
}

CHUNK_STATUS = {
    "queued",
    "extracting",
    "writing",
    "completed",
    "failed",
    "cancelled",
}


def _now() -> float:
    return time.time()


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def _clamp(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, value))


def _coerce_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        text = str(value or "").replace("\r", "\n")
        raw_items = []
        for seg in text.split("\n"):
            raw_items.extend(seg.split(","))

    out: List[str] = []
    seen = set()
    for item in raw_items:
        v = str(item or "").strip()
        if not v:
            continue
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def _parse_optional_positive_int(value: Any, field_name: str) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        parsed = int(text)
    except Exception:
        raise ValueError(f"{field_name} 必须为整数")
    if parsed <= 0:
        raise ValueError(f"{field_name} 必须 > 0")
    return parsed


def _safe_filename(name: str) -> str:
    base = os.path.basename(str(name or "").strip())
    if not base:
        return f"unnamed_{uuid.uuid4().hex[:8]}.txt"
    return base


def _storage_type_from_strategy(strategy_type: StrategyKnowledgeType) -> str:
    if strategy_type == StrategyKnowledgeType.NARRATIVE:
        return KnowledgeType.NARRATIVE.value
    if strategy_type == StrategyKnowledgeType.FACTUAL:
        return KnowledgeType.FACTUAL.value
    if strategy_type == StrategyKnowledgeType.QUOTE:
        return KnowledgeType.QUOTE.value
    return KnowledgeType.MIXED.value


@dataclass
class ImportChunkRecord:
    chunk_id: str
    index: int
    chunk_type: str
    status: str = "queued"
    step: str = "queued"
    failed_at: str = ""
    retryable: bool = False
    error: str = ""
    progress: float = 0.0
    content_preview: str = ""
    updated_at: float = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "index": self.index,
            "chunk_type": self.chunk_type,
            "status": self.status,
            "step": self.step,
            "failed_at": self.failed_at,
            "retryable": self.retryable,
            "error": self.error,
            "progress": self.progress,
            "content_preview": self.content_preview,
            "updated_at": self.updated_at,
        }


@dataclass
class ImportFileRecord:
    file_id: str
    name: str
    source_kind: str
    input_mode: str
    status: str = "queued"
    current_step: str = "queued"
    detected_strategy_type: str = "unknown"
    total_chunks: int = 0
    done_chunks: int = 0
    failed_chunks: int = 0
    cancelled_chunks: int = 0
    progress: float = 0.0
    error: str = ""
    chunks: List[ImportChunkRecord] = field(default_factory=list)
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    temp_path: Optional[str] = None
    source_path: Optional[str] = None
    inline_content: Optional[str] = None
    content_hash: str = ""
    retry_chunk_indexes: List[int] = field(default_factory=list)
    retry_mode: str = ""

    def to_dict(self, include_chunks: bool = False) -> Dict[str, Any]:
        payload = {
            "file_id": self.file_id,
            "name": self.name,
            "source_kind": self.source_kind,
            "input_mode": self.input_mode,
            "status": self.status,
            "current_step": self.current_step,
            "detected_strategy_type": self.detected_strategy_type,
            "total_chunks": self.total_chunks,
            "done_chunks": self.done_chunks,
            "failed_chunks": self.failed_chunks,
            "cancelled_chunks": self.cancelled_chunks,
            "progress": self.progress,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_path": self.source_path or "",
            "content_hash": self.content_hash or "",
            "retry_chunk_indexes": list(self.retry_chunk_indexes or []),
            "retry_mode": self.retry_mode or "",
        }
        if include_chunks:
            payload["chunks"] = [chunk.to_dict() for chunk in self.chunks]
        return payload


@dataclass
class ImportTaskRecord:
    task_id: str
    source: str
    params: Dict[str, Any]
    status: str = "queued"
    current_step: str = "queued"
    total_chunks: int = 0
    done_chunks: int = 0
    failed_chunks: int = 0
    cancelled_chunks: int = 0
    progress: float = 0.0
    error: str = ""
    files: List[ImportFileRecord] = field(default_factory=list)
    created_at: float = field(default_factory=_now)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    updated_at: float = field(default_factory=_now)
    schema_detected: str = ""
    artifact_paths: Dict[str, str] = field(default_factory=dict)
    rollback_info: Dict[str, Any] = field(default_factory=dict)
    retry_parent_task_id: str = ""
    retry_summary: Dict[str, Any] = field(default_factory=dict)

    def to_summary(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "source": self.source,
            "status": self.status,
            "current_step": self.current_step,
            "total_chunks": self.total_chunks,
            "done_chunks": self.done_chunks,
            "failed_chunks": self.failed_chunks,
            "cancelled_chunks": self.cancelled_chunks,
            "progress": self.progress,
            "error": self.error,
            "file_count": len(self.files),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "updated_at": self.updated_at,
            "task_kind": str(self.params.get("task_kind") or self.source),
            "schema_detected": self.schema_detected,
            "artifact_paths": dict(self.artifact_paths),
            "rollback_info": dict(self.rollback_info),
            "retry_parent_task_id": self.retry_parent_task_id or "",
            "retry_summary": dict(self.retry_summary),
        }

    def to_detail(self, include_chunks: bool = False) -> Dict[str, Any]:
        payload = self.to_summary()
        payload["params"] = self.params
        payload["files"] = [f.to_dict(include_chunks=include_chunks) for f in self.files]
        return payload


class ImportTaskManager:
    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._lock = asyncio.Lock()
        self._storage_lock = asyncio.Lock()

        self._tasks: Dict[str, ImportTaskRecord] = {}
        self._task_order: deque[str] = deque()
        self._queue: deque[str] = deque()
        self._active_task_id: Optional[str] = None

        self._worker_task: Optional[asyncio.Task] = None
        self._stopping = False

        self._temp_root = self._resolve_temp_root()
        self._temp_root.mkdir(parents=True, exist_ok=True)
        self._reports_root = self._resolve_reports_root()
        self._reports_root.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._resolve_manifest_path()
        self._manifest_cache: Optional[Dict[str, Any]] = None
        self._write_changed_callback: Optional[Callable[[Dict[str, Any]], Any]] = None

    def set_write_changed_callback(self, callback: Optional[Callable[[Dict[str, Any]], Any]]) -> None:
        self._write_changed_callback = callback

    async def _notify_write_changed(self, payload: Dict[str, Any]) -> None:
        callback = self._write_changed_callback
        if callback is None:
            return
        try:
            maybe_awaitable = callback(payload)
            if asyncio.iscoroutine(maybe_awaitable):
                await maybe_awaitable
        except Exception as e:
            logger.warning(f"写入变更回调执行失败: {e}")

    def _resolve_temp_root(self) -> Path:
        data_dir = Path(self.plugin.get_config("storage.data_dir", "./data"))
        if str(data_dir).startswith("."):
            plugin_dir = Path(__file__).resolve().parents[2]
            data_dir = (plugin_dir / data_dir).resolve()
        return data_dir / "web_import_tmp"

    def _resolve_reports_root(self) -> Path:
        return self._resolve_data_dir() / "web_import_reports"

    def _resolve_manifest_path(self) -> Path:
        return self._resolve_data_dir() / "import_manifest.json"

    def _resolve_staging_root(self) -> Path:
        return self._resolve_data_dir() / "import_staging"

    def _resolve_backup_root(self) -> Path:
        return self._resolve_data_dir() / "import_backup"

    def _resolve_repo_root(self) -> Path:
        return Path(__file__).resolve().parents[3]

    def _resolve_data_dir(self) -> Path:
        data_dir = Path(self.plugin.get_config("storage.data_dir", "./data"))
        if str(data_dir).startswith("."):
            plugin_dir = Path(__file__).resolve().parents[2]
            data_dir = (plugin_dir / data_dir).resolve()
        return data_dir.resolve()

    def _resolve_migration_script(self) -> Path:
        return Path(__file__).resolve().parents[2] / "scripts" / "migrate_maibot_memory.py"

    def _default_maibot_source_db(self) -> Path:
        # A_memorix/core/utils -> workspace root
        return self._resolve_repo_root() / "MaiBot" / "data" / "MaiBot.db"

    def _cfg(self, key: str, default: Any) -> Any:
        return self.plugin.get_config(key, default)

    def _cfg_int(self, key: str, default: int) -> int:
        return _coerce_int(self._cfg(key, default), default)

    def _is_enabled(self) -> bool:
        return bool(self._cfg("web.import.enabled", True))

    def _queue_limit(self) -> int:
        return max(1, self._cfg_int("web.import.max_queue_size", 20))

    def _max_files_per_task(self) -> int:
        return max(1, self._cfg_int("web.import.max_files_per_task", 200))

    def _max_file_size_bytes(self) -> int:
        mb = max(1, self._cfg_int("web.import.max_file_size_mb", 20))
        return mb * 1024 * 1024

    def _max_paste_chars(self) -> int:
        return max(1000, self._cfg_int("web.import.max_paste_chars", 200000))

    def _default_file_concurrency(self) -> int:
        return max(1, self._cfg_int("web.import.default_file_concurrency", 2))

    def _default_chunk_concurrency(self) -> int:
        return max(1, self._cfg_int("web.import.default_chunk_concurrency", 4))

    def _max_file_concurrency(self) -> int:
        return max(1, self._cfg_int("web.import.max_file_concurrency", 6))

    def _max_chunk_concurrency(self) -> int:
        return max(1, self._cfg_int("web.import.max_chunk_concurrency", 12))

    def _llm_retry_config(self) -> Dict[str, float]:
        retries = max(0, self._cfg_int("web.import.llm_retry.max_attempts", 4))
        min_wait = max(0.1, float(self._cfg("web.import.llm_retry.min_wait_seconds", 3) or 3))
        max_wait = max(min_wait, float(self._cfg("web.import.llm_retry.max_wait_seconds", 40) or 40))
        mult = max(1.0, float(self._cfg("web.import.llm_retry.backoff_multiplier", 3) or 3))
        return {
            "retries": retries,
            "min_wait": min_wait,
            "max_wait": max_wait,
            "multiplier": mult,
        }

    def _default_path_aliases(self) -> Dict[str, str]:
        plugin_dir = Path(__file__).resolve().parents[2]
        repo_root = self._resolve_repo_root()
        return {
            "raw": str((plugin_dir / "data" / "raw").resolve()),
            "lpmm": str((repo_root / "data" / "lpmm_storage").resolve()),
            "plugin_data": str((plugin_dir / "data").resolve()),
        }

    def get_path_aliases(self) -> Dict[str, str]:
        configured = self._cfg("web.import.path_aliases", self._default_path_aliases())
        if not isinstance(configured, dict):
            configured = self._default_path_aliases()

        repo_root = self._resolve_repo_root()
        result: Dict[str, str] = {}
        for alias, raw_path in configured.items():
            key = str(alias or "").strip()
            if not key:
                continue
            text = str(raw_path or "").strip()
            if not text:
                continue
            if text.startswith("\\\\"):
                continue
            p = Path(text)
            if not p.is_absolute():
                p = (repo_root / p).resolve()
            else:
                p = p.resolve()
            result[key] = str(p)

        defaults = self._default_path_aliases()
        for key, path in defaults.items():
            result.setdefault(key, path)
        return result

    def resolve_path_alias(
        self,
        alias: str,
        relative_path: str = "",
        *,
        must_exist: bool = False,
    ) -> Path:
        alias_key = str(alias or "").strip()
        aliases = self.get_path_aliases()
        if alias_key not in aliases:
            raise ValueError(f"未知路径别名: {alias_key}")

        root = Path(aliases[alias_key]).resolve()
        rel = str(relative_path or "").strip().replace("\\", "/")
        if rel.startswith("/") or rel.startswith("\\") or rel.startswith("//"):
            raise ValueError("relative_path 不能为绝对路径")
        if ":" in rel:
            raise ValueError("relative_path 不允许包含盘符")

        candidate = (root / rel).resolve() if rel else root
        try:
            candidate.relative_to(root)
        except ValueError:
            raise ValueError("路径越界：relative_path 超出白名单目录")
        if must_exist and not candidate.exists():
            raise ValueError(f"路径不存在: {candidate}")
        return candidate

    async def resolve_path_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        alias = str(payload.get("alias") or "").strip()
        relative_path = str(payload.get("relative_path") or "").strip()
        must_exist = _coerce_bool(payload.get("must_exist"), True)
        resolved = self.resolve_path_alias(alias, relative_path, must_exist=must_exist)
        return {
            "alias": alias,
            "relative_path": relative_path,
            "resolved_path": str(resolved),
            "exists": resolved.exists(),
            "is_file": resolved.is_file(),
            "is_dir": resolved.is_dir(),
        }

    def _load_manifest(self) -> Dict[str, Any]:
        if self._manifest_cache is not None:
            return self._manifest_cache
        path = self._manifest_path
        if not path.exists():
            self._manifest_cache = {}
            return self._manifest_cache
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._manifest_cache = payload
            else:
                self._manifest_cache = {}
        except Exception:
            self._manifest_cache = {}
        return self._manifest_cache

    def _save_manifest(self, payload: Dict[str, Any]) -> None:
        path = self._manifest_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._manifest_cache = payload

    def _clear_manifest(self) -> None:
        self._save_manifest({})

    def _normalize_manifest_path(self, raw_path: str) -> str:
        text = str(raw_path or "").strip()
        if not text:
            return ""
        return text.replace("\\", "/").strip().lower()

    def _match_manifest_item_for_source(self, source: str, item: Dict[str, Any]) -> bool:
        source_text = str(source or "").strip()
        if not source_text or ":" not in source_text:
            return False
        prefix, tail = source_text.split(":", 1)
        source_kind = prefix.strip().lower()
        source_value = tail.strip()
        if not source_value:
            return False

        item_kind = str(item.get("source_kind") or "").strip().lower()
        item_name = str(item.get("name") or "").strip()
        item_path_norm = self._normalize_manifest_path(item.get("source_path") or "")

        if source_kind in {"raw_scan", "lpmm_openie"}:
            source_path_norm = self._normalize_manifest_path(source_value)
            if source_path_norm and item_path_norm and source_path_norm == item_path_norm and item_kind == source_kind:
                return True

        if source_kind == "web_import":
            return item_kind in {"upload", "paste"} and item_name == source_value

        if source_kind == "lpmm_openie":
            source_name = Path(source_value).name
            return item_kind == "lpmm_openie" and item_name == source_name

        return False

    async def invalidate_manifest_for_sources(self, sources: List[str]) -> Dict[str, Any]:
        requested_sources: List[str] = []
        seen_sources = set()
        for raw in sources or []:
            source = str(raw or "").strip()
            if not source:
                continue
            key = source.lower()
            if key in seen_sources:
                continue
            seen_sources.add(key)
            requested_sources.append(source)

        result: Dict[str, Any] = {
            "requested_sources": requested_sources,
            "removed_count": 0,
            "removed_keys": [],
            "remaining_count": 0,
            "unmatched_sources": [],
            "warnings": [],
        }

        async with self._lock:
            manifest = self._load_manifest()
            if not isinstance(manifest, dict):
                manifest = {}

            valid_items: List[Tuple[str, Dict[str, Any]]] = []
            malformed_keys: List[str] = []
            for key, item in manifest.items():
                if isinstance(item, dict):
                    valid_items.append((str(key), item))
                else:
                    malformed_keys.append(str(key))

            keys_to_remove = set()
            for source in requested_sources:
                matched = False
                for key, item in valid_items:
                    if self._match_manifest_item_for_source(source, item):
                        keys_to_remove.add(key)
                        matched = True
                if not matched:
                    result["unmatched_sources"].append(source)

            if keys_to_remove:
                for key in keys_to_remove:
                    manifest.pop(key, None)
                self._save_manifest(manifest)

            result["removed_keys"] = sorted(keys_to_remove)
            result["removed_count"] = len(keys_to_remove)
            result["remaining_count"] = len(manifest)

            if malformed_keys:
                preview = ", ".join(malformed_keys[:5])
                extra = "" if len(malformed_keys) <= 5 else f" ... (+{len(malformed_keys) - 5})"
                result["warnings"].append(
                    f"manifest 条目结构异常，已跳过 {len(malformed_keys)} 项: {preview}{extra}"
                )

        return result

    def _manifest_key_for_file(self, file_record: ImportFileRecord, content_hash: str, dedupe_policy: str) -> str:
        if dedupe_policy == "content_hash":
            return f"hash:{content_hash}"
        if file_record.source_path:
            return f"path:{Path(file_record.source_path).as_posix().lower()}"
        return f"hash:{content_hash}"

    def _is_manifest_hit(
        self,
        file_record: ImportFileRecord,
        content_hash: str,
        dedupe_policy: str,
    ) -> bool:
        key = self._manifest_key_for_file(file_record, content_hash, dedupe_policy)
        manifest = self._load_manifest()
        item = manifest.get(key)
        if not isinstance(item, dict):
            return False
        return str(item.get("hash") or "") == content_hash and bool(item.get("imported"))

    def _record_manifest_import(
        self,
        file_record: ImportFileRecord,
        content_hash: str,
        dedupe_policy: str,
        task_id: str,
    ) -> None:
        key = self._manifest_key_for_file(file_record, content_hash, dedupe_policy)
        manifest = self._load_manifest()
        manifest[key] = {
            "hash": content_hash,
            "imported": True,
            "timestamp": _now(),
            "task_id": task_id,
            "name": file_record.name,
            "source_path": file_record.source_path or "",
            "source_kind": file_record.source_kind,
        }
        self._save_manifest(manifest)

    def _normalize_common_import_params(self, payload: Dict[str, Any], *, default_dedupe: str) -> Dict[str, Any]:
        input_mode = str(payload.get("input_mode", "text") or "text").strip().lower()
        if input_mode not in {"text", "json"}:
            raise ValueError("input_mode 必须为 text 或 json")

        file_concurrency = _coerce_int(
            payload.get("file_concurrency", self._default_file_concurrency()),
            self._default_file_concurrency(),
        )
        chunk_concurrency = _coerce_int(
            payload.get("chunk_concurrency", self._default_chunk_concurrency()),
            self._default_chunk_concurrency(),
        )
        file_concurrency = _clamp(file_concurrency, 1, self._max_file_concurrency())
        chunk_concurrency = _clamp(chunk_concurrency, 1, self._max_chunk_concurrency())

        llm_enabled = _coerce_bool(payload.get("llm_enabled", True), True)
        strategy_override = parse_import_strategy(
            payload.get("strategy_override", "auto"),
            default=ImportStrategy.AUTO,
        ).value

        dedupe_policy = str(payload.get("dedupe_policy", default_dedupe) or default_dedupe).strip().lower()
        if dedupe_policy not in {"content_hash", "manifest", "none"}:
            raise ValueError("dedupe_policy 必须为 content_hash/manifest/none")

        chat_log = _coerce_bool(payload.get("chat_log"), False)
        chat_reference_time = str(payload.get("chat_reference_time") or "").strip() or None
        force = _coerce_bool(payload.get("force"), False)
        clear_manifest = _coerce_bool(payload.get("clear_manifest"), False)

        return {
            "input_mode": input_mode,
            "file_concurrency": file_concurrency,
            "chunk_concurrency": chunk_concurrency,
            "llm_enabled": llm_enabled,
            "strategy_override": strategy_override,
            "chat_log": chat_log,
            "chat_reference_time": chat_reference_time,
            "force": force,
            "clear_manifest": clear_manifest,
            "dedupe_policy": dedupe_policy,
        }

    def _normalize_params(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        params = self._normalize_common_import_params(payload, default_dedupe="content_hash")
        params["task_kind"] = "upload"
        return params

    def _normalize_raw_scan_params(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        params = self._normalize_common_import_params(payload, default_dedupe="manifest")
        alias = str(payload.get("alias") or "raw").strip()
        relative_path = str(payload.get("relative_path") or "").strip()
        glob_pattern = str(payload.get("glob") or "*").strip() or "*"
        recursive = _coerce_bool(payload.get("recursive"), True)
        if ".." in relative_path.replace("\\", "/").split("/"):
            raise ValueError("relative_path 不允许包含 ..")
        params.update(
            {
                "task_kind": "raw_scan",
                "alias": alias,
                "relative_path": relative_path,
                "glob": glob_pattern,
                "recursive": recursive,
            }
        )
        return params

    def _normalize_lpmm_openie_params(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        params = self._normalize_common_import_params(payload, default_dedupe="manifest")
        alias = str(payload.get("alias") or "lpmm").strip()
        relative_path = str(payload.get("relative_path") or "").strip()
        include_all_json = _coerce_bool(payload.get("include_all_json"), False)
        params.update(
            {
                "task_kind": "lpmm_openie",
                "alias": alias,
                "relative_path": relative_path,
                "include_all_json": include_all_json,
                "input_mode": "json",
            }
        )
        return params

    def _normalize_temporal_backfill_params(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        alias = str(payload.get("alias") or "plugin_data").strip()
        relative_path = str(payload.get("relative_path") or "").strip()
        dry_run = _coerce_bool(payload.get("dry_run"), False)
        no_created_fallback = _coerce_bool(payload.get("no_created_fallback"), False)
        limit = _parse_optional_positive_int(payload.get("limit"), "limit") or 100000
        return {
            "task_kind": "temporal_backfill",
            "alias": alias,
            "relative_path": relative_path,
            "dry_run": dry_run,
            "no_created_fallback": no_created_fallback,
            "limit": limit,
        }

    def _normalize_lpmm_convert_params(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        alias = str(payload.get("alias") or "lpmm").strip()
        relative_path = str(payload.get("relative_path") or "").strip()
        target_alias = str(payload.get("target_alias") or "plugin_data").strip()
        target_relative_path = str(payload.get("target_relative_path") or "").strip()
        dimension = _parse_optional_positive_int(payload.get("dimension"), "dimension") or _coerce_int(
            self._cfg("embedding.dimension", 384),
            384,
        )
        batch_size = _parse_optional_positive_int(payload.get("batch_size"), "batch_size") or 1024
        return {
            "task_kind": "lpmm_convert",
            "alias": alias,
            "relative_path": relative_path,
            "target_alias": target_alias,
            "target_relative_path": target_relative_path,
            "dimension": dimension,
            "batch_size": batch_size,
        }

    def _normalize_by_task_kind(self, task_kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(task_kind or "").strip().lower()
        if kind in {"upload", "paste"}:
            params = self._normalize_params(payload)
            params["task_kind"] = kind
            return params
        if kind == "maibot_migration":
            return self._normalize_migration_params(payload)
        if kind == "raw_scan":
            return self._normalize_raw_scan_params(payload)
        if kind == "lpmm_openie":
            return self._normalize_lpmm_openie_params(payload)
        if kind == "temporal_backfill":
            return self._normalize_temporal_backfill_params(payload)
        if kind == "lpmm_convert":
            return self._normalize_lpmm_convert_params(payload)
        # upload/paste 默认走通用文本导入参数
        return self._normalize_params(payload)

    def _normalize_migration_params(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        source_db = str(payload.get("source_db") or "").strip()
        if not source_db:
            source_db = str(self._default_maibot_source_db())

        time_from = str(payload.get("time_from") or "").strip() or None
        time_to = str(payload.get("time_to") or "").strip() or None

        stream_ids = _coerce_list(payload.get("stream_ids"))
        group_ids = _coerce_list(payload.get("group_ids"))
        user_ids = _coerce_list(payload.get("user_ids"))

        start_id = _parse_optional_positive_int(payload.get("start_id"), "start_id")
        end_id = _parse_optional_positive_int(payload.get("end_id"), "end_id")
        if start_id is not None and end_id is not None and start_id > end_id:
            raise ValueError("start_id 不能大于 end_id")

        read_batch_size = _parse_optional_positive_int(payload.get("read_batch_size"), "read_batch_size") or 2000
        commit_window_rows = _parse_optional_positive_int(payload.get("commit_window_rows"), "commit_window_rows") or 20000
        embed_batch_size = _parse_optional_positive_int(payload.get("embed_batch_size"), "embed_batch_size") or 256
        entity_embed_batch_size = (
            _parse_optional_positive_int(payload.get("entity_embed_batch_size"), "entity_embed_batch_size") or 512
        )
        embed_workers = _parse_optional_positive_int(payload.get("embed_workers"), "embed_workers")
        max_errors = _parse_optional_positive_int(payload.get("max_errors"), "max_errors") or 500
        log_every = _parse_optional_positive_int(payload.get("log_every"), "log_every") or 5000
        preview_limit = _parse_optional_positive_int(payload.get("preview_limit"), "preview_limit") or 20

        no_resume = _coerce_bool(payload.get("no_resume"), False)
        reset_state = _coerce_bool(payload.get("reset_state"), False)
        dry_run = _coerce_bool(payload.get("dry_run"), False)
        verify_only = _coerce_bool(payload.get("verify_only"), False)

        return {
            "task_kind": "maibot_migration",
            "source_db": source_db,
            "target_data_dir": str(self._resolve_data_dir()),
            "time_from": time_from,
            "time_to": time_to,
            "stream_ids": stream_ids,
            "group_ids": group_ids,
            "user_ids": user_ids,
            "start_id": start_id,
            "end_id": end_id,
            "read_batch_size": read_batch_size,
            "commit_window_rows": commit_window_rows,
            "embed_batch_size": embed_batch_size,
            "entity_embed_batch_size": entity_embed_batch_size,
            "embed_workers": embed_workers,
            "max_errors": max_errors,
            "log_every": log_every,
            "preview_limit": preview_limit,
            "no_resume": no_resume,
            "reset_state": reset_state,
            "dry_run": dry_run,
            "verify_only": verify_only,
        }

    def _pending_task_count(self) -> int:
        pending = 0
        for task in self._tasks.values():
            if task.status in {"queued", "preparing", "running", "cancel_requested"}:
                pending += 1
        return pending

    async def _ensure_worker(self) -> None:
        async with self._lock:
            if self._worker_task and not self._worker_task.done():
                return
            self._stopping = False
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def get_runtime_settings(self) -> Dict[str, Any]:
        llm_retry = self._llm_retry_config()
        return {
            "max_queue_size": self._queue_limit(),
            "max_files_per_task": self._max_files_per_task(),
            "max_file_size_mb": self._cfg_int("web.import.max_file_size_mb", 20),
            "max_paste_chars": self._max_paste_chars(),
            "default_file_concurrency": self._default_file_concurrency(),
            "default_chunk_concurrency": self._default_chunk_concurrency(),
            "max_file_concurrency": self._max_file_concurrency(),
            "max_chunk_concurrency": self._max_chunk_concurrency(),
            "poll_interval_ms": max(200, self._cfg_int("web.import.poll_interval_ms", 1000)),
            "maibot_source_db_default": str(self._default_maibot_source_db()),
            "maibot_target_data_dir": str(self._resolve_data_dir()),
            "path_aliases": self.get_path_aliases(),
            "llm_retry": llm_retry,
            "convert_enable_staging_switch": _coerce_bool(
                self._cfg("web.import.convert.enable_staging_switch", True), True
            ),
            "convert_keep_backup_count": max(0, self._cfg_int("web.import.convert.keep_backup_count", 3)),
        }

    def is_write_blocked(self) -> bool:
        task_id = self._active_task_id
        if not task_id:
            return False
        task = self._tasks.get(task_id)
        if not task:
            return False
        return task.status in {"preparing", "running", "cancel_requested"}

    def _ensure_ready(self) -> None:
        required_attrs = ("metadata_store", "vector_store", "graph_store", "embedding_manager")

        def _collect_missing() -> List[str]:
            missing_local: List[str] = []
            for attr in required_attrs:
                if getattr(self.plugin, attr, None) is None:
                    missing_local.append(attr)
            return missing_local

        missing = _collect_missing()
        if missing:
            raise ValueError(f"导入依赖未初始化: {', '.join(missing)}")
        ready_checker = getattr(self.plugin, "is_runtime_ready", None)
        if callable(ready_checker) and not ready_checker():
            raise ValueError("插件运行时未就绪，请先完成 on_enable 初始化")

    def _scan_files(
        self,
        base_path: Path,
        *,
        recursive: bool,
        glob_pattern: str,
        allowed_exts: Optional[set[str]] = None,
    ) -> List[Path]:
        if base_path.is_file():
            candidates = [base_path]
        else:
            if recursive:
                candidates = list(base_path.rglob(glob_pattern))
            else:
                candidates = list(base_path.glob(glob_pattern))
        out: List[Path] = []
        for p in candidates:
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if allowed_exts and ext not in allowed_exts:
                continue
            out.append(p.resolve())
        out.sort(key=lambda x: x.as_posix().lower())
        return out

    async def create_upload_task(self, files: List[Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._is_enabled():
            raise ValueError("导入功能已禁用")
        self._ensure_ready()
        if not files:
            raise ValueError("至少需要上传一个文件")

        params = self._normalize_params(payload)
        max_files = self._max_files_per_task()
        if len(files) > max_files:
            raise ValueError(f"单任务文件数超过上限: {max_files}")

        async with self._lock:
            if self._pending_task_count() >= self._queue_limit():
                raise ValueError("任务队列已满，请稍后重试")

            task = ImportTaskRecord(
                task_id=uuid.uuid4().hex,
                source="upload",
                params=params,
                status="queued",
                current_step="queued",
            )
            task_dir = self._temp_root / task.task_id
            task_dir.mkdir(parents=True, exist_ok=True)

            max_size = self._max_file_size_bytes()
            for idx, uploaded in enumerate(files):
                file_id = uuid.uuid4().hex
                if isinstance(uploaded, dict):
                    staged_path_raw = uploaded.get("staged_path") or uploaded.get("path") or ""
                    staged_path = Path(str(staged_path_raw or "")).expanduser().resolve()
                    if not staged_path.is_file():
                        raise ValueError(f"上传暂存文件不存在: {staged_path}")
                    name = _safe_filename(uploaded.get("filename") or uploaded.get("name") or staged_path.name)
                    ext = Path(name).suffix.lower()
                    if ext not in {".txt", ".md", ".json"}:
                        raise ValueError(f"不支持的文件类型: {name}")
                    if staged_path.stat().st_size > max_size:
                        raise ValueError(f"文件超过大小限制: {name}")
                    temp_path = task_dir / f"{file_id}_{name}"
                    shutil.copy2(staged_path, temp_path)
                else:
                    name = _safe_filename(getattr(uploaded, "filename", f"file_{idx}.txt"))
                    ext = Path(name).suffix.lower()
                    if ext not in {".txt", ".md", ".json"}:
                        raise ValueError(f"不支持的文件类型: {name}")
                    content = await uploaded.read()
                    if len(content) > max_size:
                        raise ValueError(f"文件超过大小限制: {name}")
                    temp_path = task_dir / f"{file_id}_{name}"
                    temp_path.write_bytes(content)
                file_mode = "json" if ext == ".json" else params["input_mode"]
                task.files.append(
                    ImportFileRecord(
                        file_id=file_id,
                        name=name,
                        source_kind="upload",
                        input_mode=file_mode,
                        temp_path=str(temp_path),
                    )
                )

            self._tasks[task.task_id] = task
            self._task_order.appendleft(task.task_id)
            self._queue.append(task.task_id)

        await self._ensure_worker()
        return task.to_summary()

    async def create_paste_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._is_enabled():
            raise ValueError("导入功能已禁用")
        self._ensure_ready()

        params = self._normalize_params(payload)
        params["task_kind"] = "paste"
        content = str(payload.get("content", "") or "")
        if not content.strip():
            raise ValueError("content 不能为空")
        if len(content) > self._max_paste_chars():
            raise ValueError(f"粘贴内容超过限制: {self._max_paste_chars()} 字符")

        name = _safe_filename(payload.get("name") or f"paste_{int(_now())}.txt")
        if params["input_mode"] == "json" and Path(name).suffix.lower() != ".json":
            name = f"{Path(name).stem}.json"

        async with self._lock:
            if self._pending_task_count() >= self._queue_limit():
                raise ValueError("任务队列已满，请稍后重试")

            task = ImportTaskRecord(
                task_id=uuid.uuid4().hex,
                source="paste",
                params=params,
                status="queued",
                current_step="queued",
            )
            task.files.append(
                ImportFileRecord(
                    file_id=uuid.uuid4().hex,
                    name=name,
                    source_kind="paste",
                    input_mode=params["input_mode"],
                    inline_content=content,
                )
            )
            self._tasks[task.task_id] = task
            self._task_order.appendleft(task.task_id)
            self._queue.append(task.task_id)

        await self._ensure_worker()
        return task.to_summary()

    async def create_raw_scan_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._is_enabled():
            raise ValueError("导入功能已禁用")
        self._ensure_ready()
        params = self._normalize_raw_scan_params(payload)
        source_path = self.resolve_path_alias(
            params["alias"],
            params["relative_path"],
            must_exist=True,
        )
        files = self._scan_files(
            source_path,
            recursive=bool(params["recursive"]),
            glob_pattern=str(params["glob"] or "*"),
            allowed_exts={".txt", ".md", ".json"},
        )
        if not files:
            raise ValueError("未找到可导入文件")
        if len(files) > self._max_files_per_task():
            raise ValueError(f"单任务文件数超过上限: {self._max_files_per_task()}")

        async with self._lock:
            if self._pending_task_count() >= self._queue_limit():
                raise ValueError("任务队列已满，请稍后重试")

            task = ImportTaskRecord(
                task_id=uuid.uuid4().hex,
                source="raw_scan",
                params=params,
                status="queued",
                current_step="queued",
            )
            for path in files:
                mode = "json" if path.suffix.lower() == ".json" else params["input_mode"]
                task.files.append(
                    ImportFileRecord(
                        file_id=uuid.uuid4().hex,
                        name=path.name,
                        source_kind="raw_scan",
                        input_mode=mode,
                        source_path=str(path),
                    )
                )
            self._tasks[task.task_id] = task
            self._task_order.appendleft(task.task_id)
            self._queue.append(task.task_id)

        await self._ensure_worker()
        return task.to_summary()

    async def create_lpmm_openie_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._is_enabled():
            raise ValueError("导入功能已禁用")
        self._ensure_ready()
        params = self._normalize_lpmm_openie_params(payload)
        source_path = self.resolve_path_alias(
            params["alias"],
            params["relative_path"],
            must_exist=True,
        )
        files: List[Path] = []
        if source_path.is_file():
            files = [source_path]
        else:
            files = self._scan_files(
                source_path,
                recursive=True,
                glob_pattern="*-openie.json",
                allowed_exts={".json"},
            )
            if not files and params.get("include_all_json"):
                files = self._scan_files(
                    source_path,
                    recursive=True,
                    glob_pattern="*.json",
                    allowed_exts={".json"},
                )
        if not files:
            raise ValueError("未找到 LPMM OpenIE JSON 文件")
        if len(files) > self._max_files_per_task():
            raise ValueError(f"单任务文件数超过上限: {self._max_files_per_task()}")

        async with self._lock:
            if self._pending_task_count() >= self._queue_limit():
                raise ValueError("任务队列已满，请稍后重试")
            task = ImportTaskRecord(
                task_id=uuid.uuid4().hex,
                source="lpmm_openie",
                params=params,
                status="queued",
                current_step="queued",
                schema_detected="lpmm_openie",
            )
            for path in files:
                task.files.append(
                    ImportFileRecord(
                        file_id=uuid.uuid4().hex,
                        name=path.name,
                        source_kind="lpmm_openie",
                        input_mode="json",
                        source_path=str(path),
                    )
                )
            self._tasks[task.task_id] = task
            self._task_order.appendleft(task.task_id)
            self._queue.append(task.task_id)

        await self._ensure_worker()
        return task.to_summary()

    async def create_temporal_backfill_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._is_enabled():
            raise ValueError("导入功能已禁用")
        params = self._normalize_temporal_backfill_params(payload)
        target_path = self.resolve_path_alias(
            params["alias"],
            params["relative_path"],
            must_exist=True,
        )
        if not target_path.is_dir():
            raise ValueError("temporal_backfill 目标路径必须为目录")

        async with self._lock:
            if self._pending_task_count() >= self._queue_limit():
                raise ValueError("任务队列已满，请稍后重试")
            task = ImportTaskRecord(
                task_id=uuid.uuid4().hex,
                source="temporal_backfill",
                params=params,
                status="queued",
                current_step="queued",
            )
            task.files.append(
                ImportFileRecord(
                    file_id=uuid.uuid4().hex,
                    name=f"temporal_backfill_{int(_now())}",
                    source_kind="temporal_backfill",
                    input_mode="json",
                    source_path=str(target_path),
                )
            )
            self._tasks[task.task_id] = task
            self._task_order.appendleft(task.task_id)
            self._queue.append(task.task_id)

        await self._ensure_worker()
        return task.to_summary()

    async def create_lpmm_convert_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._is_enabled():
            raise ValueError("导入功能已禁用")
        params = self._normalize_lpmm_convert_params(payload)
        source_path = self.resolve_path_alias(
            params["alias"],
            params["relative_path"],
            must_exist=True,
        )
        if not source_path.is_dir():
            raise ValueError("lpmm_convert 输入路径必须为目录")
        target_path = self.resolve_path_alias(
            params["target_alias"],
            params["target_relative_path"],
            must_exist=False,
        )
        target_path.mkdir(parents=True, exist_ok=True)
        if not target_path.is_dir():
            raise ValueError("lpmm_convert 目标路径必须为目录")

        async with self._lock:
            if self._pending_task_count() >= self._queue_limit():
                raise ValueError("任务队列已满，请稍后重试")
            task = ImportTaskRecord(
                task_id=uuid.uuid4().hex,
                source="lpmm_convert",
                params={**params, "source_path": str(source_path), "target_path": str(target_path)},
                status="queued",
                current_step="queued",
            )
            task.files.append(
                ImportFileRecord(
                    file_id=uuid.uuid4().hex,
                    name=f"lpmm_convert_{int(_now())}",
                    source_kind="lpmm_convert",
                    input_mode="json",
                    source_path=str(source_path),
                )
            )
            self._tasks[task.task_id] = task
            self._task_order.appendleft(task.task_id)
            self._queue.append(task.task_id)

        await self._ensure_worker()
        return task.to_summary()

    async def create_maibot_migration_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._is_enabled():
            raise ValueError("导入功能已禁用")
        self._ensure_ready()

        params = self._normalize_migration_params(payload)
        script_path = self._resolve_migration_script()
        if not script_path.exists():
            raise ValueError(f"迁移脚本不存在: {script_path}")

        async with self._lock:
            if self._pending_task_count() >= self._queue_limit():
                raise ValueError("任务队列已满，请稍后重试")

            task = ImportTaskRecord(
                task_id=uuid.uuid4().hex,
                source="maibot_migration",
                params=params,
                status="queued",
                current_step="queued",
            )
            task.files.append(
                ImportFileRecord(
                    file_id=uuid.uuid4().hex,
                    name=f"maibot_migration_{int(_now())}",
                    source_kind="maibot_migration",
                    input_mode="text",
                    inline_content=json.dumps(params, ensure_ascii=False),
                )
            )
            self._tasks[task.task_id] = task
            self._task_order.appendleft(task.task_id)
            self._queue.append(task.task_id)

        await self._ensure_worker()
        return task.to_summary()

    async def list_tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        async with self._lock:
            task_ids = list(self._task_order)[: max(1, int(limit))]
            return [self._tasks[task_id].to_summary() for task_id in task_ids if task_id in self._tasks]

    async def get_task(self, task_id: str, include_chunks: bool = False) -> Optional[Dict[str, Any]]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            return task.to_detail(include_chunks=include_chunks)

    async def get_chunks(self, task_id: str, file_id: str, offset: int = 0, limit: int = 50) -> Optional[Dict[str, Any]]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            file_obj = self._find_file(task, file_id)
            if not file_obj:
                return None
            start = max(0, int(offset))
            size = max(1, min(500, int(limit)))
            items = file_obj.chunks[start : start + size]
            return {
                "task_id": task_id,
                "file_id": file_id,
                "offset": start,
                "limit": size,
                "total": len(file_obj.chunks),
                "items": [x.to_dict() for x in items],
            }

    async def cancel_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.status == "queued":
                self._mark_task_cancelled_locked(task, "任务已取消")
                self._queue = deque([x for x in self._queue if x != task_id])
            elif task.status in {"preparing", "running"}:
                task.status = "cancel_requested"
                task.current_step = "cancel_requested"
                task.updated_at = _now()
            return task.to_summary()

    def _build_retry_plan(self, task: ImportTaskRecord) -> Dict[str, Any]:
        chunk_retry_candidates: List[Tuple[ImportFileRecord, List[int]]] = []
        file_fallback_candidates: List[ImportFileRecord] = []
        skipped: List[Dict[str, str]] = []

        for file_obj in task.files:
            if file_obj.status == "cancelled":
                continue

            failed_chunks = [c for c in file_obj.chunks if c.status == "failed"]
            has_file_level_failure = file_obj.status == "failed" and not failed_chunks
            if has_file_level_failure:
                file_fallback_candidates.append(file_obj)
                continue

            if not failed_chunks:
                continue

            retry_indexes: List[int] = []
            has_non_retryable = False
            for chunk in failed_chunks:
                failed_at = str(chunk.failed_at or "").strip().lower()
                retryable = bool(chunk.retryable) or (
                    file_obj.input_mode == "text" and failed_at == "extracting"
                )
                if retryable:
                    try:
                        retry_indexes.append(int(chunk.index))
                    except Exception:
                        has_non_retryable = True
                else:
                    has_non_retryable = True

            if has_non_retryable:
                file_fallback_candidates.append(file_obj)
                continue

            retry_indexes = sorted(set(retry_indexes))
            if retry_indexes:
                chunk_retry_candidates.append((file_obj, retry_indexes))
            else:
                skipped.append(
                    {
                        "file_name": file_obj.name,
                        "source_kind": file_obj.source_kind,
                        "reason": "no_retryable_failed_chunks",
                    }
                )

        unique_fallback: List[ImportFileRecord] = []
        fallback_seen = set()
        for file_obj in file_fallback_candidates:
            if file_obj.file_id in fallback_seen:
                continue
            fallback_seen.add(file_obj.file_id)
            unique_fallback.append(file_obj)

        return {
            "chunk_retry_candidates": chunk_retry_candidates,
            "file_fallback_candidates": unique_fallback,
            "skipped": skipped,
        }

    def _clone_failed_file_for_retry(
        self,
        retry_task: ImportTaskRecord,
        failed_file: ImportFileRecord,
        task_dir: Path,
        *,
        retry_mode: str,
        retry_chunk_indexes: Optional[List[int]] = None,
    ) -> Tuple[bool, str]:
        source_kind = str(failed_file.source_kind or "").strip().lower()
        retry_chunk_indexes = list(retry_chunk_indexes or [])

        if source_kind == "upload":
            candidate_paths: List[Path] = []
            if failed_file.temp_path:
                candidate_paths.append(Path(failed_file.temp_path))
            if failed_file.source_path:
                candidate_paths.append(Path(failed_file.source_path))
            src_path = next((p for p in candidate_paths if p.exists() and p.is_file()), None)
            if src_path is None:
                return False, "upload_source_missing"
            data = src_path.read_bytes()
            file_id = uuid.uuid4().hex
            name = _safe_filename(failed_file.name)
            dst = task_dir / f"{file_id}_{name}"
            dst.write_bytes(data)
            retry_task.files.append(
                ImportFileRecord(
                    file_id=file_id,
                    name=name,
                    source_kind="upload",
                    input_mode=failed_file.input_mode,
                    temp_path=str(dst),
                    retry_mode=retry_mode,
                    retry_chunk_indexes=retry_chunk_indexes,
                )
            )
            return True, ""

        if source_kind == "paste":
            if failed_file.inline_content is None:
                return False, "paste_content_missing"
            retry_task.files.append(
                ImportFileRecord(
                    file_id=uuid.uuid4().hex,
                    name=_safe_filename(failed_file.name),
                    source_kind="paste",
                    input_mode=failed_file.input_mode,
                    inline_content=failed_file.inline_content,
                    retry_mode=retry_mode,
                    retry_chunk_indexes=retry_chunk_indexes,
                )
            )
            return True, ""

        if source_kind == "maibot_migration":
            retry_task.files.append(
                ImportFileRecord(
                    file_id=uuid.uuid4().hex,
                    name=_safe_filename(failed_file.name),
                    source_kind="maibot_migration",
                    input_mode="text",
                    inline_content=failed_file.inline_content,
                    retry_mode="file_fallback",
                    retry_chunk_indexes=[],
                )
            )
            return True, ""

        if source_kind in {"raw_scan", "lpmm_openie", "lpmm_convert", "temporal_backfill"}:
            retry_task.files.append(
                ImportFileRecord(
                    file_id=uuid.uuid4().hex,
                    name=_safe_filename(failed_file.name),
                    source_kind=source_kind,
                    input_mode=failed_file.input_mode,
                    source_path=failed_file.source_path,
                    inline_content=failed_file.inline_content,
                    retry_mode=retry_mode,
                    retry_chunk_indexes=retry_chunk_indexes,
                )
            )
            return True, ""

        return False, f"unsupported_source_kind:{source_kind or 'unknown'}"

    async def retry_failed(self, task_id: str, overrides: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            retry_plan = self._build_retry_plan(task)
            chunk_retry_candidates = list(retry_plan["chunk_retry_candidates"])
            file_fallback_candidates = list(retry_plan["file_fallback_candidates"])
            skipped_candidates = list(retry_plan["skipped"])
            if not chunk_retry_candidates and not file_fallback_candidates:
                raise ValueError("当前任务没有可重试失败项")
            base_params = dict(task.params)
            task_kind = str(task.params.get("task_kind") or "").strip().lower()

        if overrides:
            base_params.update(overrides)
        params = self._normalize_by_task_kind(task_kind, base_params)
        params["retry_parent_task_id"] = task_id
        params["retry_strategy"] = "chunk_first_auto_file_fallback"

        async with self._lock:
            if self._pending_task_count() >= self._queue_limit():
                raise ValueError("任务队列已满，请稍后重试")
            retry_task = ImportTaskRecord(
                task_id=uuid.uuid4().hex,
                source=task.source,
                params=params,
                status="queued",
                current_step="queued",
                schema_detected=task.schema_detected,
                retry_parent_task_id=task_id,
            )

            task_dir = self._temp_root / retry_task.task_id
            task_dir.mkdir(parents=True, exist_ok=True)

            retry_summary = {
                "chunk_retry_files": 0,
                "chunk_retry_chunks": 0,
                "file_fallback_files": 0,
                "skipped_files": 0,
                "parent_task_id": task_id,
            }
            skipped_details = list(skipped_candidates)

            for file_obj, chunk_indexes in chunk_retry_candidates:
                ok, reason = self._clone_failed_file_for_retry(
                    retry_task,
                    file_obj,
                    task_dir,
                    retry_mode="chunk",
                    retry_chunk_indexes=chunk_indexes,
                )
                if ok:
                    retry_summary["chunk_retry_files"] += 1
                    retry_summary["chunk_retry_chunks"] += len(chunk_indexes)
                else:
                    skipped_details.append(
                        {
                            "file_name": file_obj.name,
                            "source_kind": file_obj.source_kind,
                            "reason": reason,
                        }
                    )

            for file_obj in file_fallback_candidates:
                ok, reason = self._clone_failed_file_for_retry(
                    retry_task,
                    file_obj,
                    task_dir,
                    retry_mode="file_fallback",
                    retry_chunk_indexes=[],
                )
                if ok:
                    retry_summary["file_fallback_files"] += 1
                else:
                    skipped_details.append(
                        {
                            "file_name": file_obj.name,
                            "source_kind": file_obj.source_kind,
                            "reason": reason,
                        }
                    )

            retry_summary["skipped_files"] = len(skipped_details)
            if skipped_details:
                retry_summary["skipped_details"] = skipped_details
            retry_task.retry_summary = retry_summary

            if not retry_task.files:
                raise ValueError("无可执行的重试输入：失败项均无法构建重试任务")

            self._tasks[retry_task.task_id] = retry_task
            self._task_order.appendleft(retry_task.task_id)
            self._queue.append(retry_task.task_id)
            logger.info(
                "重试任务已创建 "
                f"parent={task_id} retry={retry_task.task_id} "
                f"chunk_files={retry_summary['chunk_retry_files']} "
                f"chunk_chunks={retry_summary['chunk_retry_chunks']} "
                f"file_fallback={retry_summary['file_fallback_files']} "
                f"skipped={retry_summary['skipped_files']}"
            )

        await self._ensure_worker()
        return retry_task.to_summary()

    async def shutdown(self) -> None:
        async with self._lock:
            self._stopping = True
            for task in self._tasks.values():
                if task.status in {"queued", "preparing", "running", "cancel_requested"}:
                    self._mark_task_cancelled_locked(task, "服务关闭")
            self._queue.clear()
            worker = self._worker_task
            self._worker_task = None

        if worker:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        self._cleanup_temp_root()

    def _cleanup_temp_root(self) -> None:
        try:
            if not self._temp_root.exists():
                return
            for child in self._temp_root.rglob("*"):
                if child.is_file():
                    child.unlink(missing_ok=True)
            for child in sorted(self._temp_root.rglob("*"), reverse=True):
                if child.is_dir():
                    child.rmdir()
            self._temp_root.rmdir()
        except Exception as e:
            logger.warning(f"清理临时导入目录失败: {e}")

    async def _worker_loop(self) -> None:
        logger.info("Web 导入任务 worker 已启动")
        while True:
            if self._stopping:
                break

            task_id: Optional[str] = None
            async with self._lock:
                while self._queue:
                    candidate = self._queue.popleft()
                    t = self._tasks.get(candidate)
                    if not t:
                        continue
                    if t.status == "cancelled":
                        continue
                    task_id = candidate
                    self._active_task_id = candidate
                    break

            if not task_id:
                await asyncio.sleep(0.2)
                continue

            try:
                await self._run_task(task_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"导入任务执行失败 task={task_id}: {e}\n{traceback.format_exc()}")
                async with self._lock:
                    task = self._tasks.get(task_id)
                    if task and task.status not in {"cancelled", "completed", "completed_with_errors"}:
                        task.status = "failed"
                        task.current_step = "failed"
                        task.error = str(e)
                        task.finished_at = _now()
                        task.updated_at = _now()
            finally:
                should_cleanup = await self._should_cleanup_task_temp(task_id)
                async with self._lock:
                    if self._active_task_id == task_id:
                        self._active_task_id = None
                if should_cleanup:
                    await self._cleanup_task_temp_files(task_id)

        logger.info("Web 导入任务 worker 已停止")

    async def _cleanup_task_temp_files(self, task_id: str) -> None:
        task_dir = self._temp_root / task_id
        if not task_dir.exists():
            return
        try:
            for child in task_dir.rglob("*"):
                if child.is_file():
                    child.unlink(missing_ok=True)
            for child in sorted(task_dir.rglob("*"), reverse=True):
                if child.is_dir():
                    child.rmdir()
            task_dir.rmdir()
        except Exception as e:
            logger.warning(f"清理任务临时文件失败 task={task_id}: {e}")

    def _task_report_path(self, task_id: str) -> Path:
        self._reports_root.mkdir(parents=True, exist_ok=True)
        return self._reports_root / f"{task_id}_summary.json"

    def _write_task_report(self, task: ImportTaskRecord) -> None:
        path = self._task_report_path(task.task_id)
        payload = task.to_detail(include_chunks=False)
        payload["generated_at"] = _now()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        task.artifact_paths["summary"] = str(path)

    async def _run_task(self, task_id: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.status = "preparing"
            task.current_step = "preparing"
            task.started_at = _now()
            task.updated_at = _now()
            if task.params.get("clear_manifest"):
                self._clear_manifest()

        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if task.status == "cancel_requested":
                task.status = "cancelled"
                task.current_step = "cancelled"
                task.finished_at = _now()
                task.updated_at = _now()
                return
            task.status = "running"
            task.current_step = "running"
            task.updated_at = _now()

        task_kind = str(task.params.get("task_kind") or task.source).strip().lower()
        if task_kind == "maibot_migration":
            if not task.files:
                raise RuntimeError("迁移任务缺少文件记录")
            await self._process_maibot_migration(task_id, task.files[0])
        elif task_kind == "temporal_backfill":
            if not task.files:
                raise RuntimeError("回填任务缺少文件记录")
            await self._process_temporal_backfill(task_id, task.files[0])
        elif task_kind == "lpmm_convert":
            if not task.files:
                raise RuntimeError("转换任务缺少文件记录")
            await self._process_lpmm_convert(task_id, task.files[0])
        else:
            file_semaphore = asyncio.Semaphore(task.params["file_concurrency"])
            chunk_semaphore = asyncio.Semaphore(task.params["chunk_concurrency"])
            jobs = [
                asyncio.create_task(self._process_file(task_id, f, file_semaphore, chunk_semaphore))
                for f in task.files
            ]
            await asyncio.gather(*jobs, return_exceptions=True)

        write_changed_payload: Optional[Dict[str, Any]] = None
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            self._recompute_task_progress(task)
            has_failed = any(
                (f.status == "failed")
                or (f.failed_chunks > 0)
                or bool(str(f.error or "").strip())
                for f in task.files
            )
            has_cancelled = any(f.status == "cancelled" for f in task.files)
            has_completed = any(f.status == "completed" for f in task.files)

            # 统一按文件真实终态收敛任务状态，避免出现“任务已取消但文件已完成”的矛盾结果。
            if has_failed and not has_cancelled:
                task.status = "completed_with_errors"
                task.current_step = "completed_with_errors"
            elif has_cancelled and not has_completed:
                task.status = "cancelled"
                task.current_step = "cancelled"
            elif has_cancelled and has_completed:
                task.status = "cancelled"
                task.current_step = "cancelled"
            else:
                task.status = "completed"
                task.current_step = "completed"
            task.finished_at = _now()
            task.updated_at = _now()
            try:
                self._write_task_report(task)
            except Exception as report_err:
                logger.warning(f"写入任务报告失败 task={task_id}: {report_err}")
            task_kind = str(task.params.get("task_kind") or task.source).strip().lower()
            write_task_kinds = {"upload", "paste", "raw_scan", "lpmm_openie", "maibot_migration", "lpmm_convert"}
            has_written_chunks = (task.done_chunks > 0) or any(f.done_chunks > 0 for f in task.files)
            if task_kind in write_task_kinds and has_written_chunks:
                write_changed_payload = {
                    "task_id": task.task_id,
                    "task_kind": task_kind,
                    "status": task.status,
                    "done_chunks": task.done_chunks,
                    "finished_at": task.finished_at,
                }

        if write_changed_payload:
            await self._notify_write_changed(write_changed_payload)

    def _build_maibot_migration_command(self, params: Dict[str, Any]) -> List[str]:
        script_path = self._resolve_migration_script()
        if not script_path.exists():
            raise RuntimeError(f"迁移脚本不存在: {script_path}")

        cmd = [
            sys.executable,
            str(script_path),
            "--source-db",
            str(params["source_db"]),
            "--target-data-dir",
            str(params["target_data_dir"]),
            "--read-batch-size",
            str(params["read_batch_size"]),
            "--commit-window-rows",
            str(params["commit_window_rows"]),
            "--embed-batch-size",
            str(params["embed_batch_size"]),
            "--entity-embed-batch-size",
            str(params["entity_embed_batch_size"]),
            "--max-errors",
            str(params["max_errors"]),
            "--log-every",
            str(params["log_every"]),
            "--preview-limit",
            str(params["preview_limit"]),
            "--yes",
        ]

        if params.get("embed_workers") is not None:
            cmd.extend(["--embed-workers", str(params["embed_workers"])])
        if params.get("start_id") is not None:
            cmd.extend(["--start-id", str(params["start_id"])])
        if params.get("end_id") is not None:
            cmd.extend(["--end-id", str(params["end_id"])])
        if params.get("time_from"):
            cmd.extend(["--time-from", str(params["time_from"])])
        if params.get("time_to"):
            cmd.extend(["--time-to", str(params["time_to"])])

        for sid in params.get("stream_ids") or []:
            cmd.extend(["--stream-id", str(sid)])
        for gid in params.get("group_ids") or []:
            cmd.extend(["--group-id", str(gid)])
        for uid in params.get("user_ids") or []:
            cmd.extend(["--user-id", str(uid)])

        if params.get("reset_state"):
            cmd.append("--reset-state")
        if params.get("no_resume"):
            cmd.append("--no-resume")
        if params.get("dry_run"):
            cmd.append("--dry-run")
        if params.get("verify_only"):
            cmd.append("--verify-only")

        return cmd

    async def _ensure_maibot_migration_chunk(
        self,
        task_id: str,
        file_id: str,
        *,
        chunk_type: str = "maibot_migration",
        preview: str = "MaiBot chat_history 迁移任务",
    ) -> str:
        chunk_id = f"{file_id}_{chunk_type}"
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return chunk_id
            f = self._find_file(task, file_id)
            if not f:
                return chunk_id
            if not f.chunks:
                f.chunks = [
                    ImportChunkRecord(
                        chunk_id=chunk_id,
                        index=0,
                        chunk_type=chunk_type,
                        status="queued",
                        step="queued",
                        progress=0.0,
                        content_preview=preview,
                    )
                ]
                f.total_chunks = 1
                f.done_chunks = 0
                f.failed_chunks = 0
                f.cancelled_chunks = 0
                f.progress = 0.0
                f.updated_at = _now()
                self._recompute_task_progress(task)
            else:
                chunk_id = f.chunks[0].chunk_id
        return chunk_id

    async def _refresh_maibot_progress_from_state(
        self,
        task_id: str,
        file_id: str,
        chunk_id: str,
        state_path: Path,
    ) -> None:
        if not state_path.exists():
            return
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return

        stats = payload.get("stats", {}) if isinstance(payload, dict) else {}
        if not isinstance(stats, dict):
            stats = {}

        total = max(0, _coerce_int(stats.get("source_matched_total", 0), 0))
        scanned = max(0, _coerce_int(stats.get("scanned_rows", 0), 0))
        bad = max(0, _coerce_int(stats.get("bad_rows", 0), 0))
        done = max(0, scanned - bad)
        migrated = max(0, _coerce_int(stats.get("migrated_rows", 0), 0))
        last_id = max(0, _coerce_int(stats.get("last_committed_id", 0), 0))

        if total <= 0:
            total = max(1, scanned)

        progress = max(0.0, min(1.0, float(scanned) / float(total))) if total > 0 else 0.0
        preview = f"scanned={scanned}/{total}, migrated={migrated}, bad={bad}, last_id={last_id}"

        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            f = self._find_file(task, file_id)
            if not f:
                return
            c = self._find_chunk(f, chunk_id)
            if c:
                if c.status not in {"completed", "failed", "cancelled"}:
                    c.status = "writing"
                    c.step = "migrating"
                c.progress = progress
                c.content_preview = preview
                c.updated_at = _now()
            f.total_chunks = total
            f.done_chunks = done
            f.failed_chunks = bad
            f.cancelled_chunks = 0
            f.progress = progress
            if f.status not in {"failed", "cancelled"}:
                f.status = "writing"
                f.current_step = "migrating"
            f.updated_at = _now()
            self._recompute_task_progress(task)

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except Exception:
            try:
                process.kill()
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except Exception:
                pass

    async def _reload_stores_after_external_migration(self) -> None:
        async with self._storage_lock:
            try:
                if self.plugin.vector_store and self.plugin.vector_store.has_data():
                    self.plugin.vector_store.load()
            except Exception as e:
                logger.warning(f"迁移后重载 VectorStore 失败: {e}")
            try:
                if self.plugin.graph_store and self.plugin.graph_store.has_data():
                    self.plugin.graph_store.load()
            except Exception as e:
                logger.warning(f"迁移后重载 GraphStore 失败: {e}")

    async def _process_maibot_migration(self, task_id: str, file_record: ImportFileRecord) -> None:
        await self._set_file_strategy(task_id, file_record.file_id, "maibot_migration")
        await self._set_file_state(task_id, file_record.file_id, "preparing", "preparing")
        chunk_id = await self._ensure_maibot_migration_chunk(
            task_id,
            file_record.file_id,
            chunk_type="maibot_migration",
            preview="MaiBot chat_history 迁移任务",
        )
        await self._set_chunk_state(task_id, file_record.file_id, chunk_id, "writing", "migrating", 0.0)

        task = self._tasks.get(task_id)
        if not task:
            await self._set_file_failed(task_id, file_record.file_id, "任务不存在")
            return
        params = dict(task.params)

        command = self._build_maibot_migration_command(params)
        project_root = self._resolve_repo_root()
        state_path = Path(params["target_data_dir"]) / "migration_state" / "chat_history_resume.json"
        report_path = Path(params["target_data_dir"]) / "migration_state" / "chat_history_report.json"

        logger.info(f"开始执行 MaiBot 迁移任务: {' '.join(command)}")
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_lines: List[str] = []
        stderr_lines: List[str] = []

        async def _drain(stream: Optional[asyncio.StreamReader], target: List[str]) -> None:
            if stream is None:
                return
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                target.append(text)
                if len(target) > 120:
                    del target[:-120]

        drain_tasks = [
            asyncio.create_task(_drain(process.stdout, stdout_lines)),
            asyncio.create_task(_drain(process.stderr, stderr_lines)),
        ]

        cancelled = False
        return_code: Optional[int] = None
        try:
            while True:
                if await self._is_cancel_requested(task_id):
                    cancelled = True
                    await self._terminate_process(process)
                    break

                await self._refresh_maibot_progress_from_state(task_id, file_record.file_id, chunk_id, state_path)
                try:
                    return_code = await asyncio.wait_for(process.wait(), timeout=1.0)
                    break
                except asyncio.TimeoutError:
                    continue
        finally:
            await asyncio.gather(*drain_tasks, return_exceptions=True)

        if cancelled:
            await self._set_chunk_cancelled(task_id, file_record.file_id, chunk_id, "任务已取消")
            await self._set_file_cancelled(task_id, file_record.file_id, "任务已取消")
            return

        await self._refresh_maibot_progress_from_state(task_id, file_record.file_id, chunk_id, state_path)

        report: Dict[str, Any] = {}
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                report = {}

        stats = report.get("stats", {}) if isinstance(report, dict) else {}
        if not isinstance(stats, dict):
            stats = {}
        bad_rows = max(0, _coerce_int(stats.get("bad_rows", 0), 0))

        if return_code in {0, 2}:
            await self._set_file_state(task_id, file_record.file_id, "saving", "saving")
            await self._reload_stores_after_external_migration()

            async with self._lock:
                task2 = self._tasks.get(task_id)
                if not task2:
                    return
                f = self._find_file(task2, file_record.file_id)
                if not f:
                    return
                c = self._find_chunk(f, chunk_id)
                if c and c.status not in {"cancelled", "failed"}:
                    c.status = "completed"
                    c.step = "completed"
                    c.progress = 1.0
                    c.updated_at = _now()
                if f.total_chunks <= 0:
                    f.total_chunks = 1
                if f.done_chunks + f.failed_chunks <= 0:
                    f.done_chunks = f.total_chunks - bad_rows
                    f.failed_chunks = bad_rows
                f.done_chunks = max(0, min(f.done_chunks, f.total_chunks))
                f.failed_chunks = max(0, min(f.failed_chunks, f.total_chunks))
                f.cancelled_chunks = 0
                f.progress = 1.0
                f.status = "completed"
                f.current_step = "completed"
                if bad_rows > 0 and not f.error:
                    f.error = f"迁移完成，但存在坏行: {bad_rows}"
                f.updated_at = _now()
                self._recompute_task_progress(task2)
            return

        fail_reason = ""
        if isinstance(report, dict):
            fail_reason = str(report.get("fail_reason") or "").strip()
        tail = (stderr_lines[-1] if stderr_lines else "") or (stdout_lines[-1] if stdout_lines else "")
        detail = fail_reason or tail or f"迁移进程退出码: {return_code}"
        await self._set_chunk_failed(task_id, file_record.file_id, chunk_id, detail)
        await self._set_file_failed(task_id, file_record.file_id, detail)

    def _resolve_convert_script(self) -> Path:
        return Path(__file__).resolve().parents[2] / "scripts" / "convert_lpmm.py"

    def _cleanup_old_backups(self) -> None:
        keep = max(0, self._cfg_int("web.import.convert.keep_backup_count", 3))
        backup_root = self._resolve_backup_root()
        if not backup_root.exists() or keep <= 0:
            return
        dirs = [p for p in backup_root.iterdir() if p.is_dir() and p.name.startswith("lpmm_convert_")]
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for old in dirs[keep:]:
            try:
                shutil.rmtree(old, ignore_errors=True)
            except Exception:
                pass

    def _verify_convert_output(self, output_dir: Path) -> Dict[str, Any]:
        vectors = output_dir / "vectors"
        graph = output_dir / "graph"
        metadata = output_dir / "metadata"
        checks = {
            "vectors_exists": vectors.exists(),
            "graph_exists": graph.exists(),
            "metadata_exists": metadata.exists(),
            "vectors_nonempty": vectors.exists() and any(vectors.iterdir()),
            "graph_nonempty": graph.exists() and any(graph.iterdir()),
            "metadata_nonempty": metadata.exists() and any(metadata.iterdir()),
        }
        checks["ok"] = checks["vectors_exists"] and checks["graph_exists"] and checks["metadata_exists"]
        return checks

    async def _preflight_convert_runtime(self) -> Tuple[bool, str]:
        """使用当前服务解释器做 convert 依赖预检，避免子进程报错信息不透明。"""
        probe_code = (
            "import importlib\n"
            "mods=['networkx','scipy','pyarrow']\n"
            "failed=[]\n"
            "for m in mods:\n"
            "    try:\n"
            "        importlib.import_module(m)\n"
            "    except Exception as e:\n"
            "        failed.append(f'{m}:{e.__class__.__name__}:{e}')\n"
            "print('OK' if not failed else ';'.join(failed))\n"
        )
        try:
            probe = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                probe_code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(probe.communicate(), timeout=20.0)
        except Exception as e:
            return False, f"依赖预检执行失败: {e}"

        out = (stdout or b"").decode("utf-8", errors="replace").strip()
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        if probe.returncode != 0:
            detail = err or out or f"return_code={probe.returncode}"
            return False, f"依赖预检失败 (python={sys.executable}): {detail}"
        if out != "OK":
            return False, f"依赖预检失败 (python={sys.executable}): {out}"
        return True, ""

    async def _process_lpmm_convert(self, task_id: str, file_record: ImportFileRecord) -> None:
        await self._set_file_strategy(task_id, file_record.file_id, "lpmm_convert")
        await self._set_file_state(task_id, file_record.file_id, "preparing", "preflight")
        chunk_id = await self._ensure_maibot_migration_chunk(
            task_id,
            file_record.file_id,
            chunk_type="lpmm_convert",
            preview="LPMM 二进制转换任务",
        )
        await self._set_chunk_state(task_id, file_record.file_id, chunk_id, "writing", "converting", 0.05)

        task = self._tasks.get(task_id)
        if not task:
            await self._set_file_failed(task_id, file_record.file_id, "任务不存在")
            return
        params = dict(task.params)
        source_dir = Path(params.get("source_path") or "")
        target_dir = Path(params.get("target_path") or "")
        if not source_dir.exists() or not source_dir.is_dir():
            await self._set_file_failed(task_id, file_record.file_id, f"输入目录无效: {source_dir}")
            return
        if not target_dir.exists() or not target_dir.is_dir():
            await self._set_file_failed(task_id, file_record.file_id, f"目标目录无效: {target_dir}")
            return

        script_path = self._resolve_convert_script()
        if not script_path.exists():
            await self._set_file_failed(task_id, file_record.file_id, f"转换脚本不存在: {script_path}")
            return

        runtime_ok, runtime_detail = await self._preflight_convert_runtime()
        if not runtime_ok:
            await self._set_file_failed(task_id, file_record.file_id, runtime_detail)
            await self._set_chunk_failed(task_id, file_record.file_id, chunk_id, runtime_detail)
            return

        required_inputs = ["paragraph.parquet", "entity.parquet"]
        if not any((source_dir / name).exists() for name in required_inputs):
            await self._set_file_failed(
                task_id,
                file_record.file_id,
                f"输入目录缺少必要文件，至少需要其一: {', '.join(required_inputs)}",
            )
            return

        staging_root = self._resolve_staging_root()
        staging_root.mkdir(parents=True, exist_ok=True)
        staging_dir = staging_root / f"lpmm_convert_{task_id}"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        # 简单空间预检：至少保留 512MB
        usage = shutil.disk_usage(str(target_dir))
        if usage.free < 512 * 1024 * 1024:
            await self._set_file_failed(task_id, file_record.file_id, "磁盘剩余空间不足（<512MB）")
            return

        cmd = [
            sys.executable,
            str(script_path),
            "--input",
            str(source_dir),
            "--output",
            str(staging_dir),
            "--dim",
            str(params.get("dimension", 384)),
            "--batch-size",
            str(params.get("batch_size", 1024)),
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self._resolve_repo_root()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_lines: List[str] = []
        stderr_lines: List[str] = []

        async def _drain(stream: Optional[asyncio.StreamReader], target: List[str]) -> None:
            if stream is None:
                return
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    target.append(text)
                    if len(target) > 120:
                        del target[:-120]

        drain_tasks = [
            asyncio.create_task(_drain(process.stdout, stdout_lines)),
            asyncio.create_task(_drain(process.stderr, stderr_lines)),
        ]

        cancelled = False
        return_code: Optional[int] = None
        try:
            while True:
                if await self._is_cancel_requested(task_id):
                    cancelled = True
                    await self._terminate_process(process)
                    break
                try:
                    return_code = await asyncio.wait_for(process.wait(), timeout=1.0)
                    break
                except asyncio.TimeoutError:
                    continue
        finally:
            await asyncio.gather(*drain_tasks, return_exceptions=True)

        if cancelled:
            await self._set_chunk_cancelled(task_id, file_record.file_id, chunk_id, "任务已取消")
            await self._set_file_cancelled(task_id, file_record.file_id, "任务已取消")
            return
        if return_code != 0:
            detail = (stderr_lines[-1] if stderr_lines else "") or (stdout_lines[-1] if stdout_lines else "")
            await self._set_file_failed(task_id, file_record.file_id, detail or f"转换失败，退出码: {return_code}")
            await self._set_chunk_failed(task_id, file_record.file_id, chunk_id, detail or f"退出码: {return_code}")
            return

        await self._set_chunk_state(task_id, file_record.file_id, chunk_id, "writing", "verifying", 0.65)
        verify = self._verify_convert_output(staging_dir)
        async with self._lock:
            t = self._tasks.get(task_id)
            if t:
                t.artifact_paths["staging_dir"] = str(staging_dir)
                t.artifact_paths["verify"] = json.dumps(verify, ensure_ascii=False)
        if not verify.get("ok"):
            await self._set_file_failed(task_id, file_record.file_id, f"校验失败: {verify}")
            await self._set_chunk_failed(task_id, file_record.file_id, chunk_id, f"校验失败: {verify}")
            return

        enable_switch = _coerce_bool(self._cfg("web.import.convert.enable_staging_switch", True), True)
        if not enable_switch:
            await self._set_file_failed(task_id, file_record.file_id, "未启用 staging 切换")
            await self._set_chunk_failed(task_id, file_record.file_id, chunk_id, "未启用 staging 切换")
            return

        await self._set_chunk_state(task_id, file_record.file_id, chunk_id, "writing", "switching", 0.85)
        backup_root = self._resolve_backup_root()
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_dir = backup_root / f"lpmm_convert_{task_id}_{int(_now())}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        switched = False
        rollback_info: Dict[str, Any] = {"attempted": True, "restored": False, "error": ""}
        moved_items: List[Tuple[Path, Path]] = []
        try:
            for name in ("vectors", "graph", "metadata"):
                src_current = target_dir / name
                src_new = staging_dir / name
                if not src_new.exists():
                    raise RuntimeError(f"staging 缺少目录: {src_new}")
                if src_current.exists():
                    dst_backup = backup_dir / name
                    shutil.move(str(src_current), str(dst_backup))
                    moved_items.append((dst_backup, src_current))
                shutil.move(str(src_new), str(src_current))
            switched = True
        except Exception as switch_err:
            rollback_info["error"] = str(switch_err)
            # 尝试回滚
            for src_backup, dst_original in moved_items:
                if src_backup.exists() and not dst_original.exists():
                    try:
                        shutil.move(str(src_backup), str(dst_original))
                    except Exception:
                        pass
            rollback_info["restored"] = True
            async with self._lock:
                t = self._tasks.get(task_id)
                if t:
                    t.rollback_info = rollback_info
            await self._set_file_failed(task_id, file_record.file_id, f"切换失败并回滚: {switch_err}")
            await self._set_chunk_failed(task_id, file_record.file_id, chunk_id, f"switch failed: {switch_err}")
            return

        if switched:
            async with self._lock:
                t = self._tasks.get(task_id)
                if t:
                    t.rollback_info = rollback_info
                    t.artifact_paths["backup_dir"] = str(backup_dir)
            self._cleanup_old_backups()
            try:
                await self._reload_stores_after_external_migration()
            except Exception as reload_err:
                logger.warning(f"转换后重载存储失败: {reload_err}")

        await self._set_chunk_completed(task_id, file_record.file_id, chunk_id)
        async with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return
            f = self._find_file(t, file_record.file_id)
            if not f:
                return
            f.total_chunks = 1
            f.done_chunks = 1
            f.failed_chunks = 0
            f.cancelled_chunks = 0
            f.progress = 1.0
            f.status = "completed"
            f.current_step = "completed"
            f.updated_at = _now()
            self._recompute_task_progress(t)

    async def _process_temporal_backfill(self, task_id: str, file_record: ImportFileRecord) -> None:
        await self._set_file_strategy(task_id, file_record.file_id, "temporal_backfill")
        await self._set_file_state(task_id, file_record.file_id, "preparing", "backfilling")
        chunk_id = await self._ensure_maibot_migration_chunk(
            task_id,
            file_record.file_id,
            chunk_type="temporal_backfill",
            preview="时序字段回填任务",
        )
        await self._set_chunk_state(task_id, file_record.file_id, chunk_id, "writing", "backfilling", 0.2)

        task = self._tasks.get(task_id)
        if not task:
            await self._set_file_failed(task_id, file_record.file_id, "任务不存在")
            return
        params = dict(task.params)
        target_dir = Path(file_record.source_path or "")
        metadata_dir = target_dir / "metadata"
        if not metadata_dir.exists():
            await self._set_file_failed(task_id, file_record.file_id, f"metadata 目录不存在: {metadata_dir}")
            return

        dry_run = bool(params.get("dry_run"))
        no_created_fallback = bool(params.get("no_created_fallback"))
        limit = max(1, _coerce_int(params.get("limit"), 100000))

        store = MetadataStore(data_dir=metadata_dir)
        updated = 0
        candidates = 0
        try:
            store.connect()
            summary = store.backfill_temporal_metadata_from_created_at(
                limit=limit,
                dry_run=dry_run,
                no_created_fallback=no_created_fallback,
            )
            candidates = int(summary.get("candidates", 0))
            updated = int(summary.get("updated", 0))
        finally:
            try:
                store.close()
            except Exception:
                pass

        async with self._lock:
            t = self._tasks.get(task_id)
            if t:
                t.artifact_paths["temporal_backfill"] = json.dumps(
                    {
                        "target_dir": str(target_dir),
                        "dry_run": dry_run,
                        "no_created_fallback": no_created_fallback,
                        "limit": limit,
                        "candidates": candidates,
                        "updated": updated,
                    },
                    ensure_ascii=False,
                )
        await self._set_chunk_completed(task_id, file_record.file_id, chunk_id)
        async with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return
            f = self._find_file(t, file_record.file_id)
            if not f:
                return
            f.total_chunks = 1
            f.done_chunks = 1
            f.failed_chunks = 0
            f.cancelled_chunks = 0
            f.progress = 1.0
            f.status = "completed"
            f.current_step = "completed"
            f.updated_at = _now()
            self._recompute_task_progress(t)

    async def _process_file(
        self,
        task_id: str,
        file_record: ImportFileRecord,
        file_semaphore: asyncio.Semaphore,
        chunk_semaphore: asyncio.Semaphore,
    ) -> None:
        async with file_semaphore:
            await self._set_file_state(task_id, file_record.file_id, "preparing", "preparing")
            if await self._is_cancel_requested(task_id):
                await self._set_file_cancelled(task_id, file_record.file_id, "任务已取消")
                return

            try:
                content = await self._read_file_content(file_record)
                content_hash = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()
                file_record.content_hash = content_hash
                task = self._tasks.get(task_id)
                if task:
                    dedupe_policy = str(task.params.get("dedupe_policy") or "none")
                    force = bool(task.params.get("force"))
                    if dedupe_policy != "none" and not force:
                        async with self._lock:
                            if self._is_manifest_hit(file_record, content_hash, dedupe_policy):
                                task2 = self._tasks.get(task_id)
                                if task2:
                                    f = self._find_file(task2, file_record.file_id)
                                    if f:
                                        f.status = "completed"
                                        f.current_step = "skipped"
                                        f.progress = 1.0
                                        f.total_chunks = 0
                                        f.done_chunks = 0
                                        f.failed_chunks = 0
                                        f.cancelled_chunks = 0
                                        f.detected_strategy_type = "skipped"
                                        f.error = ""
                                        f.updated_at = _now()
                                        self._recompute_task_progress(task2)
                                return
                if file_record.input_mode == "json":
                    await self._process_json_file(task_id, file_record, content, chunk_semaphore)
                else:
                    await self._process_text_file(task_id, file_record, content, chunk_semaphore)
                task3 = self._tasks.get(task_id)
                if task3:
                    dedupe_policy = str(task3.params.get("dedupe_policy") or "none")
                    f3 = self._find_file(task3, file_record.file_id)
                    if dedupe_policy != "none" and f3 and f3.status == "completed":
                        async with self._lock:
                            self._record_manifest_import(file_record, content_hash, dedupe_policy, task_id)
            except Exception as e:
                await self._set_file_failed(task_id, file_record.file_id, str(e))

    async def _read_file_content(self, file_record: ImportFileRecord) -> str:
        if file_record.inline_content is not None:
            return file_record.inline_content
        if file_record.source_path and Path(file_record.source_path).exists():
            data = Path(file_record.source_path).read_bytes()
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return data.decode("utf-8", errors="replace")
        if file_record.temp_path and Path(file_record.temp_path).exists():
            data = Path(file_record.temp_path).read_bytes()
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return data.decode("utf-8", errors="replace")
        raise RuntimeError("读取文件失败：输入内容缺失")

    async def _process_text_file(
        self,
        task_id: str,
        file_record: ImportFileRecord,
        content: str,
        chunk_semaphore: asyncio.Semaphore,
    ) -> None:
        task = self._tasks[task_id]
        async with self._lock:
            t = self._tasks.get(task_id)
            if t and not t.schema_detected:
                t.schema_detected = "plain_text"
        strategy = self._determine_strategy(
            file_record.name,
            content,
            task.params["strategy_override"],
            chat_log=bool(task.params.get("chat_log")),
        )
        await self._set_file_strategy(task_id, file_record.file_id, strategy)
        await self._set_file_state(task_id, file_record.file_id, "splitting", "splitting")
        await self._ensure_embedding_runtime_ready()

        chunks = strategy.split(content)
        selected_chunks = list(chunks)
        if file_record.retry_mode == "chunk":
            retry_index_set = set()
            for idx in file_record.retry_chunk_indexes:
                try:
                    retry_index_set.add(int(idx))
                except Exception:
                    continue
            selected_chunks = [chunk for chunk in chunks if int(chunk.chunk.index) in retry_index_set]
            if not selected_chunks:
                raise RuntimeError("失败分块重试索引无效，未匹配到可执行分块")
            logger.info(
                "重试任务按失败分块执行: "
                f"file={file_record.name} "
                f"selected={len(selected_chunks)} "
                f"total={len(chunks)}"
            )

        await self._register_chunks(task_id, file_record.file_id, selected_chunks)

        await self._set_file_state(task_id, file_record.file_id, "extracting", "extracting")
        model_cfg = None
        if task.params["llm_enabled"]:
            model_cfg = await self._select_model()

        jobs = []
        for chunk in selected_chunks:
            jobs.append(
                asyncio.create_task(
                    self._process_text_chunk(
                        task_id=task_id,
                        file_record=file_record,
                        chunk=chunk,
                        strategy=strategy,
                        llm_enabled=task.params["llm_enabled"],
                        model_cfg=model_cfg,
                        chunk_semaphore=chunk_semaphore,
                        chat_log=bool(task.params.get("chat_log")),
                        chat_reference_time=str(task.params.get("chat_reference_time") or "").strip() or None,
                    )
                )
            )
        await asyncio.gather(*jobs, return_exceptions=True)

        if await self._is_cancel_requested(task_id):
            await self._set_file_cancelled(task_id, file_record.file_id, "任务已取消")
            return

        await self._set_file_state(task_id, file_record.file_id, "saving", "saving")
        async with self._storage_lock:
            self.plugin.vector_store.save()
            self.plugin.graph_store.save()

        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            f = self._find_file(task, file_record.file_id)
            if not f:
                return
            if f.failed_chunks > 0:
                f.status = "failed"
                f.current_step = "failed"
                if not f.error:
                    f.error = f"存在失败分块: {f.failed_chunks}"
            elif task.status == "cancel_requested":
                f.status = "cancelled"
                f.current_step = "cancelled"
            else:
                f.status = "completed"
                f.current_step = "completed"
                f.progress = 1.0
            f.updated_at = _now()
            self._recompute_task_progress(task)
    async def _process_text_chunk(
        self,
        task_id: str,
        file_record: ImportFileRecord,
        chunk: ProcessedChunk,
        strategy: Any,
        llm_enabled: bool,
        model_cfg: Any,
        chunk_semaphore: asyncio.Semaphore,
        chat_log: bool = False,
        chat_reference_time: Optional[str] = None,
    ) -> None:
        async with chunk_semaphore:
            chunk_id = chunk.chunk.chunk_id
            if await self._is_cancel_requested(task_id):
                await self._set_chunk_cancelled(task_id, file_record.file_id, chunk_id, "任务已取消")
                return

            await self._set_chunk_state(task_id, file_record.file_id, chunk_id, "extracting", "extracting", 0.25)

            processed = chunk
            rescue_strategy = self._chunk_rescue(chunk, file_record.name)
            current_strategy = strategy
            if rescue_strategy:
                chunk.type = StrategyKnowledgeType.QUOTE
                chunk.flags.verbatim = True
                chunk.flags.requires_llm = False
                current_strategy = rescue_strategy
            try:
                if llm_enabled and chunk.flags.requires_llm:
                    processed = await current_strategy.extract(
                        chunk,
                        lambda prompt: self._llm_call(prompt, model_cfg),
                    )
                elif chunk.type == StrategyKnowledgeType.QUOTE:
                    processed = await current_strategy.extract(chunk)
            except Exception as e:
                await self._set_chunk_failed(task_id, file_record.file_id, chunk_id, f"抽取失败: {e}")
                return

            if await self._is_cancel_requested(task_id):
                await self._set_chunk_cancelled(task_id, file_record.file_id, chunk_id, "任务已取消")
                return

            await self._set_chunk_state(task_id, file_record.file_id, chunk_id, "writing", "writing", 0.7)
            try:
                time_meta = None
                if chat_log and llm_enabled and model_cfg is not None:
                    time_meta = await self._extract_chat_time_meta_with_llm(
                        processed.chunk.text,
                        model_cfg,
                        reference_time=chat_reference_time,
                    )
                async with self._storage_lock:
                    await self._persist_processed_chunk(file_record, processed, time_meta=time_meta)
                await self._set_chunk_completed(task_id, file_record.file_id, chunk_id)
            except Exception as e:
                await self._set_chunk_failed(task_id, file_record.file_id, chunk_id, f"写入失败: {e}")

    async def _process_json_file(
        self,
        task_id: str,
        file_record: ImportFileRecord,
        content: str,
        chunk_semaphore: asyncio.Semaphore,
    ) -> None:
        await self._set_file_strategy(task_id, file_record.file_id, "json")
        await self._set_file_state(task_id, file_record.file_id, "splitting", "splitting")
        await self._ensure_embedding_runtime_ready()

        try:
            data = json.loads(content)
        except Exception as e:
            raise RuntimeError(f"JSON 解析失败: {e}")

        schema = self._detect_json_schema(data)
        async with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.schema_detected = schema
                task.updated_at = _now()
        units = self._build_json_units(data, file_record.file_id, file_record.name, schema)
        await self._register_json_units(task_id, file_record.file_id, units)

        await self._set_file_state(task_id, file_record.file_id, "extracting", "extracting")
        jobs = [
            asyncio.create_task(self._process_json_unit(task_id, file_record, unit, chunk_semaphore))
            for unit in units
        ]
        await asyncio.gather(*jobs, return_exceptions=True)

        if await self._is_cancel_requested(task_id):
            await self._set_file_cancelled(task_id, file_record.file_id, "任务已取消")
            return

        await self._set_file_state(task_id, file_record.file_id, "saving", "saving")
        async with self._storage_lock:
            self.plugin.vector_store.save()
            self.plugin.graph_store.save()

        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            f = self._find_file(task, file_record.file_id)
            if not f:
                return
            if f.failed_chunks > 0:
                f.status = "failed"
                f.current_step = "failed"
                if not f.error:
                    f.error = f"存在失败分块: {f.failed_chunks}"
            elif task.status == "cancel_requested":
                f.status = "cancelled"
                f.current_step = "cancelled"
            else:
                f.status = "completed"
                f.current_step = "completed"
                f.progress = 1.0
            f.updated_at = _now()
            self._recompute_task_progress(task)

    def _detect_json_schema(self, data: Any) -> str:
        if isinstance(data, dict) and isinstance(data.get("docs"), list):
            return "lpmm_openie"
        if isinstance(data, dict) and isinstance(data.get("paragraphs"), list):
            paragraphs = data.get("paragraphs", [])
            for p in paragraphs:
                if isinstance(p, dict) and any(
                    key in p for key in ("entities", "relations", "time_meta", "source", "type", "knowledge_type")
                ):
                    return "script_json"
            return "web_json"
        raise RuntimeError("不支持的 JSON 格式：需要 paragraphs 或 docs")

    def _build_json_units(self, data: Any, file_id: str, filename: str, schema: str) -> List[Dict[str, Any]]:
        units: List[Dict[str, Any]] = []
        paragraphs: List[Any] = []
        entities: List[Any] = []
        relations: List[Any] = []

        if schema in {"web_json", "script_json"}:
            paragraphs = data.get("paragraphs", [])
            entities = data.get("entities", [])
            relations = data.get("relations", [])
        elif schema == "lpmm_openie":
            docs = data.get("docs", [])
            for d in docs:
                if not isinstance(d, dict):
                    continue
                content = str(d.get("passage", "") or "").strip()
                if not content:
                    continue
                triples = d.get("extracted_triples", []) or []
                rels = []
                for t in triples:
                    if isinstance(t, list) and len(t) == 3:
                        rels.append(
                            {
                                "subject": str(t[0]),
                                "predicate": str(t[1]),
                                "object": str(t[2]),
                            }
                        )
                para_item = {
                    "content": content,
                    "source": f"lpmm_openie:{filename}",
                    "entities": d.get("extracted_entities", []) or [],
                    "relations": rels,
                    "knowledge_type": "factual",
                }
                paragraphs.append(para_item)

        for p in paragraphs:
            paragraph = normalize_paragraph_import_item(
                p,
                default_source=f"web_import:{filename}",
            )
            units.append(
                {
                    "chunk_id": f"{file_id}_json_{len(units)}",
                    "kind": "paragraph",
                    "content": paragraph["content"],
                    "time_meta": paragraph["time_meta"],
                    "knowledge_type": paragraph["knowledge_type"],
                    "chunk_type": paragraph["knowledge_type"],
                    "source": paragraph["source"],
                    "entities": paragraph["entities"],
                    "relations": paragraph["relations"],
                    "preview": paragraph["content"][:120],
                }
            )

        for e in entities:
            name = str(e or "").strip()
            if name:
                units.append(
                    {
                        "chunk_id": f"{file_id}_json_{len(units)}",
                        "kind": "entity",
                        "name": name,
                        "chunk_type": "entity",
                        "preview": name[:120],
                    }
                )

        for r in relations:
            if not isinstance(r, dict):
                continue
            s = str(r.get("subject", "")).strip()
            p = str(r.get("predicate", "")).strip()
            o = str(r.get("object", "")).strip()
            if s and p and o:
                units.append(
                    {
                        "chunk_id": f"{file_id}_json_{len(units)}",
                        "kind": "relation",
                        "subject": s,
                        "predicate": p,
                        "object": o,
                        "chunk_type": "relation",
                        "preview": f"{s} {p} {o}"[:120],
                    }
                )
        return units

    async def _register_json_units(self, task_id: str, file_id: str, units: List[Dict[str, Any]]) -> None:
        records = [
            ImportChunkRecord(
                chunk_id=u["chunk_id"],
                index=i,
                chunk_type=u.get("chunk_type", "json"),
                status="queued",
                step="queued",
                progress=0.0,
                content_preview=str(u.get("preview", "")),
            )
            for i, u in enumerate(units)
        ]
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            f = self._find_file(task, file_id)
            if not f:
                return
            f.chunks = records
            f.total_chunks = len(records)
            f.done_chunks = 0
            f.failed_chunks = 0
            f.cancelled_chunks = 0
            f.progress = 0.0 if records else 1.0
            f.updated_at = _now()
            self._recompute_task_progress(task)

    async def _process_json_unit(
        self,
        task_id: str,
        file_record: ImportFileRecord,
        unit: Dict[str, Any],
        chunk_semaphore: asyncio.Semaphore,
    ) -> None:
        chunk_id = unit["chunk_id"]
        async with chunk_semaphore:
            if await self._is_cancel_requested(task_id):
                await self._set_chunk_cancelled(task_id, file_record.file_id, chunk_id, "任务已取消")
                return

            await self._set_chunk_state(task_id, file_record.file_id, chunk_id, "writing", "writing", 0.7)
            try:
                async with self._storage_lock:
                    kind = unit["kind"]
                    if kind == "paragraph":
                        content = str(unit.get("content", ""))
                        k_type = resolve_stored_knowledge_type(
                            unit.get("knowledge_type"),
                            content=content,
                        ).value
                        source = str(unit.get("source") or f"web_import:{file_record.name}")
                        para_hash = self.plugin.metadata_store.add_paragraph(
                            content=content,
                            source=source,
                            knowledge_type=k_type,
                            time_meta=unit.get("time_meta"),
                        )
                        emb = await self.plugin.embedding_manager.encode(content)
                        try:
                            self.plugin.vector_store.add(emb.reshape(1, -1), [para_hash])
                        except ValueError:
                            pass
                        for name in unit.get("entities", []) or []:
                            n = str(name or "").strip()
                            if n:
                                await self._add_entity_with_vector(n, source_paragraph=para_hash)
                        for rel in unit.get("relations", []) or []:
                            if not isinstance(rel, dict):
                                continue
                            s = str(rel.get("subject", "")).strip()
                            p = str(rel.get("predicate", "")).strip()
                            o = str(rel.get("object", "")).strip()
                            if s and p and o:
                                await self._add_relation(s, p, o, source_paragraph=para_hash)
                    elif kind == "entity":
                        await self._add_entity_with_vector(unit["name"])
                    elif kind == "relation":
                        await self._add_relation(unit["subject"], unit["predicate"], unit["object"])
                    else:
                        raise RuntimeError(f"未知 JSON 导入单元类型: {kind}")
                await self._set_chunk_completed(task_id, file_record.file_id, chunk_id)
            except Exception as e:
                await self._set_chunk_failed(task_id, file_record.file_id, chunk_id, f"写入失败: {e}")

    def _source_label(self, file_record: ImportFileRecord) -> str:
        if file_record.source_path:
            return f"{file_record.source_kind}:{file_record.source_path}"
        return f"web_import:{file_record.name}"

    async def _ensure_embedding_runtime_ready(self) -> None:
        report = await ensure_runtime_self_check(self.plugin)
        if bool(report.get("ok", False)):
            return
        raise RuntimeError(
            "embedding runtime self-check failed: "
            f"{report.get('message', 'unknown')} "
            f"(configured={report.get('configured_dimension', 0)}, "
            f"store={report.get('vector_store_dimension', 0)}, "
            f"encoded={report.get('encoded_dimension', 0)})"
        )

    async def _persist_processed_chunk(
        self,
        file_record: ImportFileRecord,
        processed: ProcessedChunk,
        *,
        time_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        content = processed.chunk.text
        para_hash = self.plugin.metadata_store.add_paragraph(
            content=content,
            source=self._source_label(file_record),
            knowledge_type=_storage_type_from_strategy(processed.type),
            time_meta=time_meta,
        )

        emb = await self.plugin.embedding_manager.encode(content)
        try:
            self.plugin.vector_store.add(emb.reshape(1, -1), [para_hash])
        except ValueError:
            pass

        data = processed.data or {}
        entities: List[str] = []
        relations: List[Tuple[str, str, str]] = []

        for triple in data.get("triples", []):
            s = str(triple.get("subject", "")).strip()
            p = str(triple.get("predicate", "")).strip()
            o = str(triple.get("object", "")).strip()
            if s and p and o:
                relations.append((s, p, o))
                entities.extend([s, o])

        for rel in data.get("relations", []):
            s = str(rel.get("subject", "")).strip()
            p = str(rel.get("predicate", "")).strip()
            o = str(rel.get("object", "")).strip()
            if s and p and o:
                relations.append((s, p, o))
                entities.extend([s, o])

        for k in ("entities", "events", "verbatim_entities"):
            for e in data.get(k, []):
                name = str(e or "").strip()
                if name:
                    entities.append(name)

        uniq_entities = list({x.strip().lower(): x.strip() for x in entities if str(x).strip()}.values())
        for name in uniq_entities:
            await self._add_entity_with_vector(name, source_paragraph=para_hash)

        for s, p, o in relations:
            await self._add_relation(s, p, o, source_paragraph=para_hash)

    async def _add_entity_with_vector(self, name: str, source_paragraph: str = "") -> str:
        hash_value = self.plugin.metadata_store.add_entity(name=name, source_paragraph=source_paragraph)
        self.plugin.graph_store.add_nodes([name])
        if hash_value not in self.plugin.vector_store:
            emb = await self.plugin.embedding_manager.encode(name)
            try:
                self.plugin.vector_store.add(emb.reshape(1, -1), [hash_value])
            except ValueError:
                pass
        return hash_value

    async def _add_relation(self, subject: str, predicate: str, obj: str, source_paragraph: str = "") -> str:
        await self._add_entity_with_vector(subject, source_paragraph=source_paragraph)
        await self._add_entity_with_vector(obj, source_paragraph=source_paragraph)
        rv_cfg = self.plugin.get_config("retrieval.relation_vectorization", {}) or {}
        if not isinstance(rv_cfg, dict):
            rv_cfg = {}
        write_vector = bool(rv_cfg.get("enabled", False)) and bool(rv_cfg.get("write_on_import", True))

        relation_service = getattr(self.plugin, "relation_write_service", None)
        if relation_service is not None:
            result = await relation_service.upsert_relation_with_vector(
                subject=subject,
                predicate=predicate,
                obj=obj,
                confidence=1.0,
                source_paragraph=source_paragraph,
                write_vector=write_vector,
            )
            return result.hash_value

        rel_hash = self.plugin.metadata_store.add_relation(
            subject=subject,
            predicate=predicate,
            obj=obj,
            source_paragraph=source_paragraph,
            confidence=1.0,
        )
        self.plugin.graph_store.add_edges([(subject, obj)], relation_hashes=[rel_hash])
        try:
            self.plugin.metadata_store.set_relation_vector_state(rel_hash, "none")
        except Exception:
            pass
        return rel_hash
    async def _select_model(self) -> Any:
        models = llm_api.get_available_models()
        if not models:
            raise RuntimeError("没有可用 LLM 模型")

        config_model = str(self._cfg("advanced.extraction_model", "auto") or "auto").strip()
        if config_model.lower() != "auto" and config_model in models:
            return models[config_model]

        for task_name in [
            "lpmm_entity_extract",
            "lpmm_rdf_build",
            "embedding",
            "replyer",
            "utils",
            "planner",
            "tool_use",
        ]:
            if task_name in models:
                return models[task_name]

        return models[next(iter(models))]

    async def _llm_call(self, prompt: str, model_config: Any) -> Dict[str, Any]:
        cfg = self._llm_retry_config()
        retries = int(cfg["retries"])
        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                success, response, _, _ = await llm_api.generate_with_model(
                    prompt=prompt,
                    model_config=model_config,
                    request_type="A_Memorix.WebImport",
                )
                if not success or not response:
                    raise RuntimeError("LLM 生成失败")

                txt = str(response or "").strip()
                if "```" in txt:
                    txt = txt.split("```json")[-1].split("```")[0].strip()
                    if txt.startswith("json"):
                        txt = txt[4:].strip()

                try:
                    return json.loads(txt)
                except Exception:
                    s = txt.find("{")
                    e = txt.rfind("}")
                    if s >= 0 and e > s:
                        return json.loads(txt[s : e + 1])
                    raise
            except Exception as err:
                last_error = err
                if attempt >= retries:
                    break
                delay = min(cfg["max_wait"], cfg["min_wait"] * (cfg["multiplier"] ** attempt))
                await asyncio.sleep(max(0.0, float(delay)))
        raise RuntimeError(f"LLM 抽取失败: {last_error}")

    def _parse_reference_time(self, value: Optional[str]) -> datetime:
        if not value:
            return datetime.now()
        text = str(value).strip()
        formats = [
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d",
            "%Y-%m-%d",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return datetime.now()

    async def _extract_chat_time_meta_with_llm(
        self,
        text: str,
        model_config: Any,
        *,
        reference_time: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not str(text or "").strip():
            return None
        ref_dt = self._parse_reference_time(reference_time)
        reference_now = ref_dt.strftime("%Y/%m/%d %H:%M")
        prompt = f"""You are a time extraction engine for chat logs.
Extract temporal information from the following chat paragraph.

Rules:
1. Use semantic understanding, not regex matching.
2. Convert relative expressions to absolute local datetime using reference_now.
3. If a time span exists, return event_time_start/event_time_end.
4. If only one point in time exists, return event_time.
5. If no reliable time info exists, keep all event_time fields null.
6. Return JSON only.

reference_now: {reference_now}
text:
{text}

JSON schema:
{{
  "event_time": null,
  "event_time_start": null,
  "event_time_end": null,
  "time_range": null,
  "time_granularity": null,
  "time_confidence": 0.0
}}
"""
        try:
            result = await self._llm_call(prompt, model_config)
        except Exception as e:
            logger.warning(f"chat_log 时间语义抽取失败: {e}")
            return None

        raw_time_meta = {
            "event_time": result.get("event_time"),
            "event_time_start": result.get("event_time_start"),
            "event_time_end": result.get("event_time_end"),
            "time_range": result.get("time_range"),
            "time_granularity": result.get("time_granularity"),
            "time_confidence": result.get("time_confidence"),
        }
        try:
            normalized = normalize_time_meta(raw_time_meta)
        except Exception:
            return None
        has_effective = any(k in normalized for k in ("event_time", "event_time_start", "event_time_end"))
        if not has_effective:
            return None
        return normalized

    def _chunk_rescue(self, chunk: ProcessedChunk, filename: str) -> Optional[Any]:
        if chunk.type == StrategyKnowledgeType.QUOTE:
            return None
        if looks_like_quote_text(chunk.chunk.text):
            return QuoteStrategy(filename)
        return None

    def _instantiate_strategy(self, filename: str, strategy: ImportStrategy) -> Any:
        if strategy == ImportStrategy.FACTUAL:
            return FactualStrategy(filename)
        if strategy == ImportStrategy.QUOTE:
            return QuoteStrategy(filename)
        return NarrativeStrategy(filename)

    def _determine_strategy(self, filename: str, content: str, override: str, *, chat_log: bool = False) -> Any:
        strategy = select_import_strategy(
            content,
            override=override,
            chat_log=chat_log,
        )
        return self._instantiate_strategy(filename, strategy)

    async def _set_file_strategy(self, task_id: str, file_id: str, strategy: Any) -> None:
        if isinstance(strategy, str):
            strategy_type = strategy
        elif isinstance(strategy, NarrativeStrategy):
            strategy_type = "narrative"
        elif isinstance(strategy, FactualStrategy):
            strategy_type = "factual"
        elif isinstance(strategy, QuoteStrategy):
            strategy_type = "quote"
        else:
            strategy_type = "unknown"

        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            f = self._find_file(task, file_id)
            if not f:
                return
            f.detected_strategy_type = strategy_type
            f.updated_at = _now()
            task.updated_at = _now()

    async def _register_chunks(self, task_id: str, file_id: str, chunks: List[ProcessedChunk]) -> None:
        records = [
            ImportChunkRecord(
                chunk_id=chunk.chunk.chunk_id,
                index=index,
                chunk_type=chunk.type.value,
                status="queued",
                step="queued",
                progress=0.0,
                content_preview=str(chunk.chunk.text or "")[:120],
            )
            for index, chunk in enumerate(chunks)
        ]

        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            f = self._find_file(task, file_id)
            if not f:
                return
            f.chunks = records
            f.total_chunks = len(records)
            f.done_chunks = 0
            f.failed_chunks = 0
            f.cancelled_chunks = 0
            f.progress = 0.0 if records else 1.0
            f.updated_at = _now()
            self._recompute_task_progress(task)

    async def _set_file_state(self, task_id: str, file_id: str, status: str, step: str) -> None:
        if status not in FILE_STATUS:
            return
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            f = self._find_file(task, file_id)
            if not f:
                return
            f.status = status
            f.current_step = step
            f.updated_at = _now()
            task.updated_at = _now()
            if step in {"preparing", "splitting", "extracting", "writing", "saving"} and task.status in {"queued", "preparing"}:
                task.status = "running"
                task.current_step = "running"

    async def _set_file_failed(self, task_id: str, file_id: str, error: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            f = self._find_file(task, file_id)
            if not f:
                return
            f.status = "failed"
            f.current_step = "failed"
            f.error = str(error)
            f.updated_at = _now()
            task.updated_at = _now()
            self._recompute_task_progress(task)

    async def _set_file_cancelled(self, task_id: str, file_id: str, reason: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            f = self._find_file(task, file_id)
            if not f:
                return
            f.status = "cancelled"
            f.current_step = "cancelled"
            f.error = reason
            additional_cancelled = 0
            for chunk in f.chunks:
                if chunk.status in {"completed", "failed", "cancelled"}:
                    continue
                chunk.status = "cancelled"
                chunk.step = "cancelled"
                chunk.retryable = False
                chunk.error = reason
                chunk.progress = 1.0
                chunk.updated_at = _now()
                additional_cancelled += 1
            if additional_cancelled > 0:
                f.cancelled_chunks += additional_cancelled
                f.progress = self._compute_ratio(
                    f.done_chunks + f.failed_chunks + f.cancelled_chunks, f.total_chunks
                )
            f.updated_at = _now()
            task.updated_at = _now()
            self._recompute_task_progress(task)

    async def _set_chunk_state(
        self,
        task_id: str,
        file_id: str,
        chunk_id: str,
        status: str,
        step: str,
        progress: float,
    ) -> None:
        if status not in CHUNK_STATUS:
            return
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            f = self._find_file(task, file_id)
            if not f:
                return
            c = self._find_chunk(f, chunk_id)
            if not c:
                return
            c.status = status
            c.step = step
            if status in {"queued", "extracting", "writing"}:
                c.error = ""
                c.failed_at = ""
                c.retryable = False
            c.progress = max(0.0, min(1.0, float(progress)))
            c.updated_at = _now()
            if f.status not in {"failed", "cancelled"}:
                f.status = "extracting" if status == "extracting" else "writing"
                f.current_step = step
            f.updated_at = _now()
            task.updated_at = _now()

    async def _set_chunk_completed(self, task_id: str, file_id: str, chunk_id: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            f = self._find_file(task, file_id)
            if not f:
                return
            c = self._find_chunk(f, chunk_id)
            if not c or c.status == "completed":
                return
            c.status = "completed"
            c.step = "completed"
            c.failed_at = ""
            c.retryable = False
            c.progress = 1.0
            c.updated_at = _now()
            f.done_chunks += 1
            f.progress = self._compute_ratio(f.done_chunks + f.failed_chunks + f.cancelled_chunks, f.total_chunks)
            f.updated_at = _now()
            self._recompute_task_progress(task)

    async def _set_chunk_failed(self, task_id: str, file_id: str, chunk_id: str, error: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            f = self._find_file(task, file_id)
            if not f:
                return
            c = self._find_chunk(f, chunk_id)
            if not c or c.status == "failed":
                return
            failed_stage = str(c.step or "").strip().lower()
            if failed_stage in {"", "queued", "failed", "completed", "cancelled"}:
                failed_stage = str(f.current_step or "").strip().lower()
            if failed_stage in {"", "queued", "failed", "completed", "cancelled"}:
                failed_stage = "unknown"
            c.status = "failed"
            c.step = "failed"
            c.failed_at = failed_stage
            c.retryable = bool(f.input_mode == "text" and failed_stage == "extracting")
            c.error = str(error)
            c.progress = 1.0
            c.updated_at = _now()
            f.failed_chunks += 1
            f.progress = self._compute_ratio(f.done_chunks + f.failed_chunks + f.cancelled_chunks, f.total_chunks)
            if not f.error:
                f.error = str(error)
            f.updated_at = _now()
            self._recompute_task_progress(task)

    async def _set_chunk_cancelled(self, task_id: str, file_id: str, chunk_id: str, reason: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            f = self._find_file(task, file_id)
            if not f:
                return
            c = self._find_chunk(f, chunk_id)
            if not c or c.status == "cancelled":
                return
            c.status = "cancelled"
            c.step = "cancelled"
            c.retryable = False
            c.error = reason
            c.progress = 1.0
            c.updated_at = _now()
            f.cancelled_chunks += 1
            f.progress = self._compute_ratio(f.done_chunks + f.failed_chunks + f.cancelled_chunks, f.total_chunks)
            f.updated_at = _now()
            self._recompute_task_progress(task)

    async def _is_cancel_requested(self, task_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return True
            return task.status == "cancel_requested"

    def _find_file(self, task: ImportTaskRecord, file_id: str) -> Optional[ImportFileRecord]:
        for f in task.files:
            if f.file_id == file_id:
                return f
        return None

    def _find_chunk(self, file_record: ImportFileRecord, chunk_id: str) -> Optional[ImportChunkRecord]:
        for c in file_record.chunks:
            if c.chunk_id == chunk_id:
                return c
        return None

    def _compute_ratio(self, done: int, total: int) -> float:
        if total <= 0:
            return 1.0
        return max(0.0, min(1.0, float(done) / float(total)))

    def _recompute_task_progress(self, task: ImportTaskRecord) -> None:
        total = 0
        done = 0
        failed = 0
        cancelled = 0
        for f in task.files:
            total += f.total_chunks
            done += f.done_chunks
            failed += f.failed_chunks
            cancelled += f.cancelled_chunks
        task.total_chunks = total
        task.done_chunks = done
        task.failed_chunks = failed
        task.cancelled_chunks = cancelled
        task.progress = self._compute_ratio(done + failed + cancelled, total)
        task.updated_at = _now()

    async def _should_cleanup_task_temp(self, task_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return True
            for f in task.files:
                if f.status == "failed":
                    return False
            return True

    def _mark_task_cancelled_locked(self, task: ImportTaskRecord, reason: str) -> None:
        for f in task.files:
            if f.status in {"completed", "failed", "cancelled"}:
                continue
            f.status = "cancelled"
            f.current_step = "cancelled"
            f.error = reason
            additional_cancelled = 0
            for c in f.chunks:
                if c.status in {"completed", "failed", "cancelled"}:
                    continue
                c.status = "cancelled"
                c.step = "cancelled"
                c.retryable = False
                c.error = reason
                c.progress = 1.0
                c.updated_at = _now()
                additional_cancelled += 1
            if additional_cancelled > 0:
                f.cancelled_chunks += additional_cancelled
            f.progress = self._compute_ratio(
                f.done_chunks + f.failed_chunks + f.cancelled_chunks, f.total_chunks
            )
            f.updated_at = _now()
        task.status = "cancelled"
        task.current_step = "cancelled"
        task.finished_at = _now()
        task.updated_at = _now()
        self._recompute_task_progress(task)
