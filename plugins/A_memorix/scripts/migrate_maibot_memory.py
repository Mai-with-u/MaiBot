#!/usr/bin/env python3
"""
MaiBot 记忆迁移脚本（chat_history -> A_memorix）

特性：
1. 高性能：分页读取 + 批量 embedding + 批量写入
2. 断点续传：基于 last_committed_id 的窗口提交
3. 精确一次语义：稳定哈希 + 幂等写入 + 向量存在性检查
4. 可确认筛选：支持时间区间、聊天流（stream/group/user）筛选，并先预览后确认
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import logging
import os
import pickle
import sqlite3
import sys
import time
import traceback
import types
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import tomlkit


CURRENT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = CURRENT_DIR.parent
WORKSPACE_ROOT = PLUGIN_ROOT.parent
MAIBOT_ROOT = WORKSPACE_ROOT / "MaiBot"
RUNTIME_CORE_PACKAGE = "_a_memorix_runtime_core"

VectorStore = None
GraphStore = None
MetadataStore = None
create_embedding_api_adapter = None
KnowledgeType = None
QuantizationType = None
SparseMatrixFormat = None
compute_hash = None
normalize_text = None
atomic_write = None
model_config = None
RelationWriteService = None


def _create_bootstrap_logger():
    fallback = logging.getLogger("A_Memorix.MaiBotMigration")
    if not fallback.handlers:
        fallback.addHandler(logging.NullHandler())
    try:
        for path in (WORKSPACE_ROOT, MAIBOT_ROOT, PLUGIN_ROOT):
            path_str = str(path)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)
        from src.common.logger import get_logger

        return get_logger("A_Memorix.MaiBotMigration")
    except Exception:
        return fallback


logger = _create_bootstrap_logger()


def _ensure_import_paths() -> None:
    for path in (WORKSPACE_ROOT, MAIBOT_ROOT, PLUGIN_ROOT):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


def _ensure_runtime_core_package() -> str:
    existing = sys.modules.get(RUNTIME_CORE_PACKAGE)
    if existing is not None and hasattr(existing, "__path__"):
        return RUNTIME_CORE_PACKAGE

    pkg = types.ModuleType(RUNTIME_CORE_PACKAGE)
    pkg.__path__ = [str(PLUGIN_ROOT / "core")]
    pkg.__package__ = RUNTIME_CORE_PACKAGE
    sys.modules[RUNTIME_CORE_PACKAGE] = pkg
    return RUNTIME_CORE_PACKAGE


def _disable_unavailable_gemini_provider() -> None:
    global model_config
    try:
        from google import genai  # type: ignore  # noqa: F401
        return
    except Exception:
        pass

    from src.config.config import model_config as loaded_model_config

    providers = list(getattr(loaded_model_config, "api_providers", []))
    if not providers:
        model_config = loaded_model_config
        return

    kept_providers = [p for p in providers if str(getattr(p, "client_type", "")).lower() != "gemini"]
    if len(kept_providers) == len(providers):
        model_config = loaded_model_config
        return

    loaded_model_config.api_providers = kept_providers
    loaded_model_config.api_providers_dict = {p.name: p for p in kept_providers}

    models = list(getattr(loaded_model_config, "models", []))
    kept_models = [m for m in models if m.api_provider in loaded_model_config.api_providers_dict]
    loaded_model_config.models = kept_models
    loaded_model_config.models_dict = {m.name: m for m in kept_models}

    task_cfg = loaded_model_config.model_task_config
    for field_name in task_cfg.__dataclass_fields__.keys():
        task = getattr(task_cfg, field_name, None)
        if task is None or not hasattr(task, "model_list"):
            continue
        task.model_list = [m for m in list(task.model_list) if m in loaded_model_config.models_dict]

    model_config = loaded_model_config
    logger.warning("检测到缺少 google.genai，已临时禁用 gemini provider 以保证脚本可运行。")


def _bootstrap_runtime_symbols() -> None:
    global VectorStore
    global GraphStore
    global MetadataStore
    global KnowledgeType
    global QuantizationType
    global SparseMatrixFormat
    global compute_hash
    global normalize_text
    global atomic_write
    global RelationWriteService
    global logger

    if VectorStore is not None and compute_hash is not None and atomic_write is not None:
        return

    _ensure_import_paths()

    import src  # noqa: F401
    from src.common.logger import get_logger

    logger = get_logger("A_Memorix.MaiBotMigration")

    pkg = _ensure_runtime_core_package()

    vector_store_module = importlib.import_module(f"{pkg}.storage.vector_store")
    graph_store_module = importlib.import_module(f"{pkg}.storage.graph_store")
    metadata_store_module = importlib.import_module(f"{pkg}.storage.metadata_store")
    knowledge_types_module = importlib.import_module(f"{pkg}.storage.knowledge_types")
    hash_module = importlib.import_module(f"{pkg}.utils.hash")
    io_module = importlib.import_module(f"{pkg}.utils.io")
    relation_write_service_module = importlib.import_module(f"{pkg}.utils.relation_write_service")

    VectorStore = vector_store_module.VectorStore
    GraphStore = graph_store_module.GraphStore
    MetadataStore = metadata_store_module.MetadataStore
    KnowledgeType = knowledge_types_module.KnowledgeType
    QuantizationType = vector_store_module.QuantizationType
    SparseMatrixFormat = graph_store_module.SparseMatrixFormat
    compute_hash = hash_module.compute_hash
    normalize_text = hash_module.normalize_text
    atomic_write = io_module.atomic_write
    RelationWriteService = relation_write_service_module.RelationWriteService


def _load_embedding_adapter_factory() -> None:
    global create_embedding_api_adapter
    global model_config

    if create_embedding_api_adapter is not None:
        return

    _ensure_import_paths()

    from src.config.config import model_config as loaded_model_config

    model_config = loaded_model_config
    _disable_unavailable_gemini_provider()

    pkg = _ensure_runtime_core_package()
    api_adapter_module = importlib.import_module(f"{pkg}.embedding.api_adapter")
    create_embedding_api_adapter = api_adapter_module.create_embedding_api_adapter


DEFAULT_SOURCE_DB = MAIBOT_ROOT / "data" / "MaiBot.db"
DEFAULT_TARGET_DATA_DIR = PLUGIN_ROOT / "data"
DEFAULT_CONFIG_PATH = PLUGIN_ROOT / "config.toml"

MIGRATION_STATE_DIRNAME = "migration_state"
STATE_FILENAME = "chat_history_resume.json"
BAD_ROWS_FILENAME = "chat_history_bad_rows.jsonl"
REPORT_FILENAME = "chat_history_report.json"


class MigrationError(Exception):
    """迁移流程错误。"""


@dataclass
class SelectionFilter:
    time_from_ts: Optional[float]
    time_to_ts: Optional[float]
    stream_ids: List[str]
    stream_filter_requested: bool
    start_id: Optional[int]
    end_id: Optional[int]
    time_from_raw: Optional[str]
    time_to_raw: Optional[str]

    def fingerprint_payload(self) -> Dict[str, Any]:
        return {
            "time_from_ts": self.time_from_ts,
            "time_to_ts": self.time_to_ts,
            "time_from_raw": self.time_from_raw,
            "time_to_raw": self.time_to_raw,
            "stream_ids": sorted(self.stream_ids),
            "stream_filter_requested": self.stream_filter_requested,
            "start_id": self.start_id,
            "end_id": self.end_id,
        }


@dataclass
class PreviewResult:
    total: int
    distribution: List[Tuple[str, int]]
    samples: List[Dict[str, Any]]


@dataclass
class MappedRow:
    row_id: int
    chat_id: str
    paragraph_hash: str
    content: str
    source: str
    time_meta: Dict[str, Any]
    entities: List[str]
    relations: List[Tuple[str, str, str]]
    existing_paragraph_vector: bool


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _normalize_name(value: Any) -> str:
    return str(value or "").strip()


def _canonical_name(value: Any) -> str:
    return _normalize_name(value).lower()


def _dedup_keep_order(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in items:
        v = _normalize_name(raw)
        if not v:
            continue
        k = v.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(v)
    return out


def _format_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "-"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def _parse_cli_datetime(text: str, is_end: bool = False) -> float:
    value = str(text or "").strip()
    if not value:
        raise ValueError("时间不能为空")

    formats = [
        ("%Y-%m-%d %H:%M:%S", False),
        ("%Y/%m/%d %H:%M:%S", False),
        ("%Y-%m-%d %H:%M", False),
        ("%Y/%m/%d %H:%M", False),
        ("%Y-%m-%d", True),
        ("%Y/%m/%d", True),
    ]

    for fmt, is_date_only in formats:
        try:
            dt = datetime.strptime(value, fmt)
            if is_date_only and is_end:
                dt = dt.replace(hour=23, minute=59, second=59, microsecond=0)
            return dt.timestamp()
        except ValueError:
            continue

    raise ValueError(
        f"时间格式错误: {value}，仅支持 YYYY-MM-DD、YYYY/MM/DD、YYYY-MM-DD HH:mm[:ss]、YYYY/MM/DD HH:mm[:ss]"
    )


def _json_hash(payload: Dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(data.encode("utf-8")).hexdigest()


def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def _extract_schema_defaults(schema_obj: Dict[str, Any]) -> Dict[str, Any]:
    defaults: Dict[str, Any] = {}
    if not isinstance(schema_obj, dict):
        return defaults

    for key, spec in schema_obj.items():
        if not isinstance(spec, dict):
            continue
        if "default" in spec:
            defaults[key] = spec.get("default")
            continue
        props = spec.get("properties")
        if isinstance(props, dict):
            defaults[key] = _extract_schema_defaults(props)
    return defaults


def _load_manifest_defaults() -> Dict[str, Any]:
    manifest_path = PLUGIN_ROOT / "_manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        schema = payload.get("config_schema")
        if isinstance(schema, dict):
            return _extract_schema_defaults(schema)
    except Exception as e:
        logger.warning(f"读取 manifest 默认配置失败，已回退空配置: {e}")
    return {}


def _build_source_db_fingerprint(db_path: Path) -> Dict[str, Any]:
    stat = db_path.stat()
    payload = {
        "path": str(db_path.resolve()),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }
    payload["sha1"] = _json_hash(payload)
    return payload


def _state_path(target_data_dir: Path) -> Path:
    return target_data_dir / MIGRATION_STATE_DIRNAME / STATE_FILENAME


def _bad_rows_path(target_data_dir: Path) -> Path:
    return target_data_dir / MIGRATION_STATE_DIRNAME / BAD_ROWS_FILENAME


def _report_path(target_data_dir: Path) -> Path:
    return target_data_dir / MIGRATION_STATE_DIRNAME / REPORT_FILENAME


def _dump_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    if atomic_write is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return

    with atomic_write(path, mode="w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


class SourceDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        if not self.db_path.exists():
            raise MigrationError(f"源数据库不存在: {self.db_path}")

        uri = f"file:{self.db_path.resolve().as_posix()}?mode=ro"
        try:
            self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        except sqlite3.OperationalError:
            self.conn = sqlite3.connect(str(self.db_path.resolve()), check_same_thread=False)

        self.conn.row_factory = sqlite3.Row
        pragmas = [
            "PRAGMA query_only = ON",
            "PRAGMA cache_size = -128000",
            "PRAGMA temp_store = MEMORY",
            "PRAGMA synchronous = OFF",
            "PRAGMA journal_mode = WAL",
        ]
        for sql in pragmas:
            try:
                self.conn.execute(sql)
            except sqlite3.OperationalError:
                # 部分 PRAGMA 在 mode=ro 下会失败，不影响只读扫描能力
                continue

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def _require_conn(self) -> sqlite3.Connection:
        if self.conn is None:
            raise MigrationError("源数据库尚未连接")
        return self.conn

    def resolve_stream_ids(
        self,
        stream_ids: Sequence[str],
        group_ids: Sequence[str],
        user_ids: Sequence[str],
    ) -> List[str]:
        conn = self._require_conn()
        resolved: set[str] = set(_normalize_name(x) for x in stream_ids if _normalize_name(x))
        has_group_or_user = any(_normalize_name(x) for x in group_ids) or any(_normalize_name(x) for x in user_ids)
        if not has_group_or_user:
            return sorted(resolved)

        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_streams' LIMIT 1"
        ).fetchone()
        if table_exists is None:
            raise MigrationError("源库缺少 chat_streams 表，无法根据 --group-id/--user-id 映射 stream_id")

        def _select_by_field(field: str, values: Sequence[str]) -> None:
            values_norm = [_normalize_name(v) for v in values if _normalize_name(v)]
            if not values_norm:
                return
            placeholders = ",".join("?" for _ in values_norm)
            sql = f"SELECT DISTINCT stream_id FROM chat_streams WHERE {field} IN ({placeholders})"
            cur = conn.execute(sql, tuple(values_norm))
            for row in cur.fetchall():
                sid = _normalize_name(row["stream_id"])
                if sid:
                    resolved.add(sid)

        _select_by_field("group_id", group_ids)
        _select_by_field("user_id", user_ids)
        return sorted(resolved)

    @staticmethod
    def _build_where(
        selection: SelectionFilter,
        start_after_id: Optional[int] = None,
    ) -> Tuple[str, List[Any]]:
        conditions: List[str] = []
        params: List[Any] = []

        if selection.start_id is not None:
            conditions.append("id >= ?")
            params.append(selection.start_id)
        if selection.end_id is not None:
            conditions.append("id <= ?")
            params.append(selection.end_id)
        if start_after_id is not None:
            conditions.append("id > ?")
            params.append(start_after_id)

        if selection.stream_ids:
            placeholders = ",".join("?" for _ in selection.stream_ids)
            conditions.append(f"chat_id IN ({placeholders})")
            params.extend(selection.stream_ids)
        elif selection.stream_filter_requested:
            conditions.append("1=0")

        if selection.time_from_ts is not None and selection.time_to_ts is not None:
            conditions.append("(end_time >= ? AND start_time <= ?)")
            params.extend([selection.time_from_ts, selection.time_to_ts])
        elif selection.time_from_ts is not None:
            conditions.append("(end_time >= ?)")
            params.append(selection.time_from_ts)
        elif selection.time_to_ts is not None:
            conditions.append("(start_time <= ?)")
            params.append(selection.time_to_ts)

        where_sql = "WHERE " + " AND ".join(conditions) if conditions else ""
        return where_sql, params

    def count_candidates(self, selection: SelectionFilter) -> int:
        conn = self._require_conn()
        where_sql, params = self._build_where(selection, start_after_id=None)
        sql = f"SELECT COUNT(*) AS c FROM chat_history {where_sql}"
        cur = conn.execute(sql, tuple(params))
        return int(cur.fetchone()["c"])

    def preview(self, selection: SelectionFilter, preview_limit: int) -> PreviewResult:
        conn = self._require_conn()
        where_sql, params = self._build_where(selection, start_after_id=None)

        total_sql = f"SELECT COUNT(*) AS c FROM chat_history {where_sql}"
        total = int(conn.execute(total_sql, tuple(params)).fetchone()["c"])

        dist_sql = (
            f"SELECT chat_id, COUNT(*) AS c FROM chat_history {where_sql} "
            "GROUP BY chat_id ORDER BY c DESC LIMIT 30"
        )
        distribution = [
            (_normalize_name(row["chat_id"]), int(row["c"]))
            for row in conn.execute(dist_sql, tuple(params)).fetchall()
        ]

        sample_sql = (
            "SELECT id, chat_id, start_time, end_time, theme, summary "
            f"FROM chat_history {where_sql} ORDER BY id ASC LIMIT ?"
        )
        sample_params = list(params)
        sample_params.append(max(1, int(preview_limit)))
        samples = [dict(row) for row in conn.execute(sample_sql, tuple(sample_params)).fetchall()]

        return PreviewResult(total=total, distribution=distribution, samples=samples)

    def iter_rows(
        self,
        selection: SelectionFilter,
        batch_size: int,
        start_after_id: int,
    ) -> Generator[List[sqlite3.Row], None, None]:
        conn = self._require_conn()
        cursor = int(start_after_id)
        while True:
            where_sql, params = self._build_where(selection, start_after_id=cursor)
            sql = (
                "SELECT id, chat_id, start_time, end_time, participants, theme, keywords, summary "
                f"FROM chat_history {where_sql} ORDER BY id ASC LIMIT ?"
            )
            bind = list(params)
            bind.append(max(1, int(batch_size)))
            rows = conn.execute(sql, tuple(bind)).fetchall()
            if not rows:
                break
            yield rows
            cursor = int(rows[-1]["id"])

    def sample_rows_for_verify(
        self,
        selection: SelectionFilter,
        sample_size: int,
    ) -> List[sqlite3.Row]:
        conn = self._require_conn()
        where_sql, params = self._build_where(selection, start_after_id=None)
        sql = (
            "SELECT id, chat_id, start_time, end_time, participants, theme, keywords, summary "
            f"FROM chat_history {where_sql} ORDER BY RANDOM() LIMIT ?"
        )
        bind = list(params)
        bind.append(max(1, int(sample_size)))
        return conn.execute(sql, tuple(bind)).fetchall()


class MigrationRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.source_db_path = Path(args.source_db).resolve()
        self.target_data_dir = Path(args.target_data_dir).resolve()
        self.state_file = _state_path(self.target_data_dir)
        self.bad_rows_file = _bad_rows_path(self.target_data_dir)
        self.report_file = _report_path(self.target_data_dir)

        self.source_db = SourceDB(self.source_db_path)

        self.vector_store = None
        self.graph_store = None
        self.metadata_store = None
        self.embedding_manager = None
        self.relation_write_service = None
        self.plugin_config: Dict[str, Any] = {}
        self.embed_workers: int = 5

        self.selection: Optional[SelectionFilter] = None
        self.filter_fingerprint: str = ""
        self.source_db_fingerprint: Dict[str, Any] = {}
        self.source_db_fingerprint_hash: str = ""
        self.state: Dict[str, Any] = {}

        self.started_at = time.time()
        self.exit_code = 0
        self.failed = False
        self.fail_reason: Optional[str] = None

        self.stats: Dict[str, Any] = {
            "source_matched_total": 0,
            "scanned_rows": 0,
            "valid_rows": 0,
            "migrated_rows": 0,
            "skipped_existing_rows": 0,
            "bad_rows": 0,
            "paragraph_vectors_added": 0,
            "entity_vectors_added": 0,
            "relations_written": 0,
            "relation_vectors_written": 0,
            "relation_vectors_failed": 0,
            "relation_vectors_skipped": 0,
            "graph_edges_written": 0,
            "windows_committed": 0,
            "last_committed_id": 0,
            "verify_sample_size": 0,
            "verify_paragraph_missing": 0,
            "verify_vector_missing": 0,
            "verify_relation_missing": 0,
            "verify_edge_missing": 0,
            "verify_passed": False,
        }

    async def run(self) -> int:
        try:
            _bootstrap_runtime_symbols()
            self._prepare_paths()

            self.source_db.connect()
            self.selection = self._build_selection_filter()
            self.filter_fingerprint = _json_hash(self.selection.fingerprint_payload())

            self.source_db_fingerprint = _build_source_db_fingerprint(self.source_db_path)
            self.source_db_fingerprint_hash = str(self.source_db_fingerprint.get("sha1", ""))

            preview = self.source_db.preview(self.selection, preview_limit=self.args.preview_limit)
            self.stats["source_matched_total"] = int(preview.total)
            self._print_preview(preview)

            if preview.total <= 0:
                logger.info("筛选后无数据，退出。")
                self.stats["verify_passed"] = True
                if self.args.verify_only:
                    self._load_plugin_config()
                    await self._init_target_stores(require_embedding=False)
                    await self._verify(strict=True)
                return self._finalize()

            if self.args.verify_only:
                self._load_plugin_config()
                await self._init_target_stores(require_embedding=False)
                await self._verify(strict=True)
                return self._finalize()

            if self.args.dry_run:
                logger.info("dry-run 模式：仅预览，不写入。")
                return self._finalize()

            if not self.args.yes:
                if not self._confirm():
                    logger.info("用户取消执行。")
                    return self._finalize()

            self._load_plugin_config()
            await self._init_target_stores(require_embedding=True)
            self._load_or_init_state()

            start_after_id = self._resolve_start_after_id()
            await self._migrate(start_after_id=start_after_id)
            await self._verify(strict=True)
            return self._finalize()
        except Exception as e:
            self.failed = True
            self.fail_reason = str(e)
            logger.error(f"迁移失败: {e}\n{traceback.format_exc()}")
            return self._finalize()
        finally:
            self._close()

    def _prepare_paths(self) -> None:
        (self.target_data_dir / MIGRATION_STATE_DIRNAME).mkdir(parents=True, exist_ok=True)
        if self.args.reset_state and self.state_file.exists():
            self.state_file.unlink()
        if self.args.reset_state and self.bad_rows_file.exists():
            self.bad_rows_file.unlink()

    def _load_plugin_config(self) -> None:
        merged = _load_manifest_defaults()

        config_path = DEFAULT_CONFIG_PATH
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    raw = tomlkit.load(f)
                if isinstance(raw, dict):
                    merged = _deep_merge_dict(merged, dict(raw))
            except Exception as e:
                logger.warning(f"读取插件配置失败，继续使用默认配置: {e}")

        self.plugin_config = merged

    def _read_existing_vector_dimension(self, fallback_dimension: int) -> int:
        meta_path = self.target_data_dir / "vectors" / "vectors_metadata.pkl"
        if not meta_path.exists():
            return fallback_dimension
        try:
            with open(meta_path, "rb") as f:
                payload = pickle.load(f)
            value = _safe_int(payload.get("dimension"), fallback_dimension)
            return max(1, value)
        except Exception:
            return fallback_dimension

    async def _init_target_stores(self, require_embedding: bool) -> None:
        if VectorStore is None or GraphStore is None or MetadataStore is None:
            raise MigrationError("运行时初始化失败：存储组件不可用")

        emb_cfg = self.plugin_config.get("embedding", {}) if isinstance(self.plugin_config, dict) else {}
        graph_cfg = self.plugin_config.get("graph", {}) if isinstance(self.plugin_config, dict) else {}

        self.embed_workers = max(1, _safe_int(self.args.embed_workers, _safe_int(emb_cfg.get("max_concurrent"), 5)))
        emb_batch_size = max(1, _safe_int(emb_cfg.get("batch_size"), 32))
        emb_default_dim = max(1, _safe_int(emb_cfg.get("dimension"), 1024))
        emb_model_name = str(emb_cfg.get("model_name", "auto"))
        emb_retry = emb_cfg.get("retry", {}) if isinstance(emb_cfg.get("retry", {}), dict) else {}

        if require_embedding:
            _load_embedding_adapter_factory()
            if create_embedding_api_adapter is None:
                raise MigrationError("运行时初始化失败：embedding 适配器不可用")

            if model_config is not None:
                embedding_task = getattr(getattr(model_config, "model_task_config", None), "embedding", None)
                if embedding_task is not None and hasattr(embedding_task, "model_list"):
                    if not list(embedding_task.model_list):
                        raise MigrationError(
                            "当前配置没有可用 embedding 模型。若你使用 gemini provider，请先安装 `google-genai` "
                            "或切换到可用的 embedding provider。"
                        )

            self.embedding_manager = create_embedding_api_adapter(
                batch_size=emb_batch_size,
                max_concurrent=self.embed_workers,
                default_dimension=emb_default_dim,
                model_name=emb_model_name,
                retry_config=emb_retry,
            )

            try:
                detected_dim = self._read_existing_vector_dimension(emb_default_dim)
                has_existing_vectors = (self.target_data_dir / "vectors" / "vectors_metadata.pkl").exists()
                if not has_existing_vectors:
                    detected_dim = await self.embedding_manager._detect_dimension()
            except Exception as e:
                logger.warning(f"嵌入维度探测失败，回退配置维度: {e}")
                detected_dim = self._read_existing_vector_dimension(emb_default_dim)
        else:
            detected_dim = self._read_existing_vector_dimension(emb_default_dim)
            self.embedding_manager = None

        q_type = str(emb_cfg.get("quantization_type", "int8")).lower()
        if q_type != "int8":
            raise MigrationError(
                "embedding.quantization_type 在 vNext 仅允许 int8(SQ8)。"
                " 请先执行 scripts/release_vnext_migrate.py migrate。"
            )
        quantization = QuantizationType.INT8

        matrix_fmt = str(graph_cfg.get("sparse_matrix_format", "csr")).lower()
        fmt_map = {
            "csr": SparseMatrixFormat.CSR,
            "csc": SparseMatrixFormat.CSC,
        }
        sparse_fmt = fmt_map.get(matrix_fmt, SparseMatrixFormat.CSR)

        self.vector_store = VectorStore(
            dimension=detected_dim,
            quantization_type=quantization,
            data_dir=self.target_data_dir / "vectors",
        )
        self.graph_store = GraphStore(
            matrix_format=sparse_fmt,
            data_dir=self.target_data_dir / "graph",
        )
        self.metadata_store = MetadataStore(data_dir=self.target_data_dir / "metadata")
        self.metadata_store.connect()

        if self.vector_store.has_data():
            self.vector_store.load()
        if self.graph_store.has_data():
            self.graph_store.load()

        self.relation_write_service = None
        if require_embedding and RelationWriteService is not None and self.embedding_manager is not None:
            self.relation_write_service = RelationWriteService(
                metadata_store=self.metadata_store,
                graph_store=self.graph_store,
                vector_store=self.vector_store,
                embedding_manager=self.embedding_manager,
            )

        logger.info(
            f"目标存储初始化完成: dim={self.vector_store.dimension}, quant={q_type}, graph_fmt={matrix_fmt}, "
            f"embed_workers={self.embed_workers}"
        )

    def _should_write_relation_vectors(self) -> bool:
        retrieval_cfg = self.plugin_config.get("retrieval", {}) if isinstance(self.plugin_config, dict) else {}
        if not isinstance(retrieval_cfg, dict):
            return False
        rv_cfg = retrieval_cfg.get("relation_vectorization", {})
        if not isinstance(rv_cfg, dict):
            return False
        return bool(rv_cfg.get("enabled", False)) and bool(rv_cfg.get("write_on_import", True))

    async def _ensure_relation_vectors_for_records(
        self,
        relation_records: Dict[str, Tuple[str, str, str, float, Optional[str], bytes]],
    ) -> None:
        if not relation_records:
            return
        if self.relation_write_service is None:
            return

        success = 0
        failed = 0
        skipped = 0
        for relation_hash, rel in relation_records.items():
            result = await self.relation_write_service.ensure_relation_vector(
                hash_value=relation_hash,
                subject=str(rel[0]),
                predicate=str(rel[1]),
                obj=str(rel[2]),
            )
            if result.vector_state == "ready":
                if result.vector_written:
                    success += 1
                else:
                    skipped += 1
            else:
                failed += 1

        self.stats["relation_vectors_written"] += success
        self.stats["relation_vectors_failed"] += failed
        self.stats["relation_vectors_skipped"] += skipped

    def _build_selection_filter(self) -> SelectionFilter:
        if self.args.start_id is not None and self.args.start_id <= 0:
            raise MigrationError("--start-id 必须 > 0")
        if self.args.end_id is not None and self.args.end_id <= 0:
            raise MigrationError("--end-id 必须 > 0")
        if self.args.start_id is not None and self.args.end_id is not None and self.args.start_id > self.args.end_id:
            raise MigrationError("--start-id 不能大于 --end-id")

        time_from_ts = _parse_cli_datetime(self.args.time_from, is_end=False) if self.args.time_from else None
        time_to_ts = _parse_cli_datetime(self.args.time_to, is_end=True) if self.args.time_to else None
        if time_from_ts is not None and time_to_ts is not None and time_from_ts > time_to_ts:
            raise MigrationError("--time-from 不能晚于 --time-to")

        stream_filter_requested = bool(
            (self.args.stream_id or []) or (self.args.group_id or []) or (self.args.user_id or [])
        )
        stream_ids = self.source_db.resolve_stream_ids(
            stream_ids=self.args.stream_id or [],
            group_ids=self.args.group_id or [],
            user_ids=self.args.user_id or [],
        )
        if stream_filter_requested and not stream_ids:
            logger.warning("已指定 stream/group/user 筛选，但未解析到任何 stream_id，结果将为空。")

        logger.info(
            f"筛选条件: time_from={self.args.time_from or '-'}, time_to={self.args.time_to or '-'}, "
            f"stream_ids={len(stream_ids)}, stream_filter_requested={stream_filter_requested}"
        )

        return SelectionFilter(
            time_from_ts=time_from_ts,
            time_to_ts=time_to_ts,
            stream_ids=stream_ids,
            stream_filter_requested=stream_filter_requested,
            start_id=self.args.start_id,
            end_id=self.args.end_id,
            time_from_raw=self.args.time_from,
            time_to_raw=self.args.time_to,
        )

    def _load_or_init_state(self) -> None:
        if self.args.start_id is not None:
            logger.info("检测到 --start-id，已按用户指定起点覆盖断点状态。")
            self.state = self._new_state(last_committed_id=int(self.args.start_id) - 1)
            return

        if self.args.no_resume:
            self.state = self._new_state(last_committed_id=0)
            return

        if not self.state_file.exists():
            self.state = self._new_state(last_committed_id=0)
            return

        with open(self.state_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        loaded_filter_fp = str(loaded.get("filter_fingerprint", ""))
        loaded_source_fp = str(loaded.get("source_db_fingerprint", ""))

        if loaded_filter_fp != self.filter_fingerprint or loaded_source_fp != self.source_db_fingerprint_hash:
            if self.args.dry_run or self.args.verify_only:
                logger.info("检测到断点与当前筛选不一致；当前为只读模式，将忽略旧断点。")
                self.state = self._new_state(last_committed_id=0)
                return
            raise MigrationError(
                "检测到筛选条件或源库指纹变化，已拒绝继续续传。请使用 --reset-state 或调整参数后重试。"
            )

        self.state = loaded
        stored_stats = loaded.get("stats", {})
        if isinstance(stored_stats, dict):
            for k, v in stored_stats.items():
                if k in self.stats and isinstance(v, (int, float, bool)):
                    self.stats[k] = v

    def _new_state(self, last_committed_id: int) -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": time.time(),
            "last_committed_id": int(last_committed_id),
            "filter_fingerprint": self.filter_fingerprint,
            "source_db_fingerprint": self.source_db_fingerprint_hash,
            "source_db_meta": self.source_db_fingerprint,
            "stats": dict(self.stats),
        }

    def _flush_state(self, last_committed_id: int) -> None:
        self.stats["last_committed_id"] = int(last_committed_id)
        self.state = {
            "version": 1,
            "updated_at": time.time(),
            "last_committed_id": int(last_committed_id),
            "filter_fingerprint": self.filter_fingerprint,
            "source_db_fingerprint": self.source_db_fingerprint_hash,
            "source_db_meta": self.source_db_fingerprint,
            "stats": dict(self.stats),
        }
        _dump_json_atomic(self.state_file, self.state)

    def _resolve_start_after_id(self) -> int:
        if self.selection is None:
            raise MigrationError("selection 未初始化")

        if self.args.start_id is not None:
            return int(self.args.start_id) - 1

        if self.args.no_resume:
            return 0

        state_last = _safe_int(self.state.get("last_committed_id"), 0) if self.state else 0
        return max(0, state_last)

    def _print_preview(self, preview: PreviewResult) -> None:
        print("\n=== Migration Preview ===")
        print(f"source_db: {self.source_db_path}")
        print(f"target_data_dir: {self.target_data_dir}")
        if self.selection:
            print(
                f"time_window: [{self.selection.time_from_raw or '-'} ~ {self.selection.time_to_raw or '-'}] "
                f"(ts: {_format_ts(self.selection.time_from_ts)} ~ {_format_ts(self.selection.time_to_ts)})"
            )
            print(
                f"id_window: [{self.selection.start_id or '-'} ~ {self.selection.end_id or '-'}], "
                f"selected_streams={len(self.selection.stream_ids)}"
            )
        print(f"matched_rows: {preview.total}")

        if preview.distribution:
            print("top_chat_distribution:")
            for cid, cnt in preview.distribution[:10]:
                print(f"  - {cid}: {cnt}")
        else:
            print("top_chat_distribution: (none)")

        if preview.samples:
            print(f"samples (first {len(preview.samples)}):")
            for row in preview.samples:
                summary_preview = _normalize_name(row.get("summary", ""))[:60]
                theme_preview = _normalize_name(row.get("theme", ""))[:30]
                print(
                    f"  - id={row.get('id')} chat_id={row.get('chat_id')} "
                    f"[{_format_ts(row.get('start_time'))} ~ {_format_ts(row.get('end_time'))}] "
                    f"theme={theme_preview!r} summary={summary_preview!r}"
                )
        print("=========================\n")

    def _confirm(self) -> bool:
        answer = input("确认按以上筛选执行迁移？输入 y 继续 [y/N]: ").strip().lower()
        return answer in {"y", "yes"}

    def _parse_json_list_field(self, raw: Any, field_name: str, row_id: int) -> List[str]:
        if raw is None:
            return []
        if isinstance(raw, list):
            data = raw
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except Exception as e:
                raise ValueError(f"{field_name} JSON 解析失败: {e}") from e
            if not isinstance(parsed, list):
                raise ValueError(f"{field_name} JSON 必须是 list，当前为 {type(parsed).__name__}")
            data = parsed
        else:
            raise ValueError(f"{field_name} 字段类型不支持: {type(raw).__name__}")
        return _dedup_keep_order(str(x) for x in data if _normalize_name(x))

    def _map_row(self, row: sqlite3.Row) -> MappedRow:
        row_id = int(row["id"])
        chat_id = _normalize_name(row["chat_id"])
        theme = _normalize_name(row["theme"])
        summary = _normalize_name(row["summary"])

        participants = self._parse_json_list_field(row["participants"], "participants", row_id)
        keywords = self._parse_json_list_field(row["keywords"], "keywords", row_id)
        keywords_top = keywords[:8]

        participants_text = "、".join(participants) if participants else ""
        keywords_text = "、".join(keywords_top) if keywords_top else ""

        content = (
            f"话题：{theme}\n"
            f"概括：{summary}\n"
            f"参与者：{participants_text}\n"
            f"关键词：{keywords_text}"
        ).strip()

        paragraph_hash = compute_hash(normalize_text(content))
        source = f"maibot.chat_history:{chat_id}"

        start_time = _safe_float(row["start_time"], 0.0)
        end_time = _safe_float(row["end_time"], start_time)
        time_meta = {
            "event_time_start": start_time,
            "event_time_end": end_time,
            "time_granularity": "minute",
            "time_confidence": 0.95,
        }

        entities = _dedup_keep_order([*participants, theme, *keywords_top])
        relations: List[Tuple[str, str, str]] = []
        if theme:
            for participant in participants:
                relations.append((participant, "参与话题", theme))
            for keyword in keywords_top:
                relations.append((theme, "关键词", keyword))

        existing_vector = paragraph_hash in self.vector_store
        return MappedRow(
            row_id=row_id,
            chat_id=chat_id,
            paragraph_hash=paragraph_hash,
            content=content,
            source=source,
            time_meta=time_meta,
            entities=entities,
            relations=relations,
            existing_paragraph_vector=existing_vector,
        )

    def _append_bad_row(self, row: sqlite3.Row, reason: str) -> None:
        payload = {
            "id": int(row["id"]),
            "chat_id": _normalize_name(row["chat_id"]),
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "participants": row["participants"],
            "theme": _normalize_name(row["theme"]),
            "keywords": row["keywords"],
            "summary": row["summary"],
            "error": reason,
            "timestamp": time.time(),
        }
        self.bad_rows_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.bad_rows_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
            f.write("\n")

    async def _migrate(self, start_after_id: int) -> None:
        if self.selection is None:
            raise MigrationError("selection 未初始化")

        read_batch_size = max(1, int(self.args.read_batch_size))
        commit_window_rows = max(1, int(self.args.commit_window_rows))
        log_every = max(1, int(self.args.log_every))

        window_rows: List[MappedRow] = []
        window_scanned = 0
        last_seen_id = start_after_id

        logger.info(
            f"开始迁移: start_after_id={start_after_id}, read_batch_size={read_batch_size}, "
            f"commit_window_rows={commit_window_rows}"
        )

        for batch in self.source_db.iter_rows(self.selection, read_batch_size, start_after_id):
            for row in batch:
                row_id = int(row["id"])
                last_seen_id = row_id
                self.stats["scanned_rows"] += 1
                window_scanned += 1

                try:
                    mapped = self._map_row(row)
                except Exception as e:
                    self.stats["bad_rows"] += 1
                    self._append_bad_row(row, str(e))
                    if self.stats["bad_rows"] > int(self.args.max_errors):
                        raise MigrationError(
                            f"坏行数量超过上限 max_errors={self.args.max_errors}，已中止。"
                        )
                    continue

                self.stats["valid_rows"] += 1
                if mapped.existing_paragraph_vector:
                    self.stats["skipped_existing_rows"] += 1
                else:
                    self.stats["migrated_rows"] += 1
                window_rows.append(mapped)

                if window_scanned >= commit_window_rows:
                    await self._commit_window(window_rows, last_seen_id)
                    window_rows = []
                    window_scanned = 0

                if self.stats["scanned_rows"] % log_every == 0:
                    logger.info(
                        f"迁移进度: scanned={self.stats['scanned_rows']}/{self.stats['source_matched_total']}, "
                        f"valid={self.stats['valid_rows']}, bad={self.stats['bad_rows']}, "
                        f"last_id={last_seen_id}"
                    )

        if window_scanned > 0 or window_rows:
            await self._commit_window(window_rows, last_seen_id)

        logger.info(
            f"迁移主流程完成: scanned={self.stats['scanned_rows']}, valid={self.stats['valid_rows']}, "
            f"bad={self.stats['bad_rows']}, last_committed_id={self.stats['last_committed_id']}"
        )

    async def _commit_window(self, rows: List[MappedRow], last_seen_id: int) -> None:
        if not rows:
            self._flush_state(last_seen_id)
            self.stats["windows_committed"] += 1
            return

        now_ts = time.time()
        empty_meta_blob = pickle.dumps({})

        conn = self.metadata_store.get_connection()

        cursor = conn.cursor()

        # 批量查询本窗口内已存在的段落，保证重跑时 entity/mention 不重复累计
        existing_paragraph_hashes: set[str] = set()
        all_hashes = [item.paragraph_hash for item in rows]
        for i in range(0, len(all_hashes), 800):
            batch_hashes = all_hashes[i : i + 800]
            if not batch_hashes:
                continue
            placeholders = ",".join("?" for _ in batch_hashes)
            existing_rows = cursor.execute(
                f"SELECT hash FROM paragraphs WHERE hash IN ({placeholders})",
                tuple(batch_hashes),
            ).fetchall()
            for row in existing_rows:
                existing_paragraph_hashes.add(str(row["hash"]))

        paragraph_records: List[Tuple[Any, ...]] = []
        paragraph_embed_map: Dict[str, str] = {}

        entity_display: Dict[str, str] = {}
        entity_counts: Dict[str, int] = defaultdict(int)
        paragraph_entity_mentions: Dict[Tuple[str, str], int] = defaultdict(int)
        entity_embed_map: Dict[str, str] = {}

        relation_records: Dict[str, Tuple[str, str, str, float, Optional[str], bytes]] = {}
        paragraph_relation_links: set[Tuple[str, str]] = set()

        for item in rows:
            is_new_paragraph = item.paragraph_hash not in existing_paragraph_hashes

            start_ts = _safe_float(item.time_meta.get("event_time_start"), 0.0)
            end_ts = _safe_float(item.time_meta.get("event_time_end"), start_ts)
            confidence = _safe_float(item.time_meta.get("time_confidence"), 0.95)
            granularity = _normalize_name(item.time_meta.get("time_granularity")) or "minute"

            if is_new_paragraph:
                paragraph_records.append(
                    (
                        item.paragraph_hash,
                        item.content,
                        None,
                        now_ts,
                        now_ts,
                        empty_meta_blob,
                        item.source,
                        len(normalize_text(item.content).split()),
                        None,
                        start_ts,
                        end_ts,
                        granularity,
                        confidence,
                        KnowledgeType.NARRATIVE.value,
                    )
                )

            if item.paragraph_hash not in self.vector_store:
                paragraph_embed_map[item.paragraph_hash] = item.content

            for entity in item.entities:
                name = _normalize_name(entity)
                if not name:
                    continue
                canon = _canonical_name(name)
                if not canon:
                    continue
                entity_hash = compute_hash(canon)
                entity_display.setdefault(entity_hash, name)
                if is_new_paragraph:
                    entity_counts[entity_hash] += 1
                    paragraph_entity_mentions[(item.paragraph_hash, entity_hash)] += 1
                if entity_hash not in self.vector_store:
                    entity_embed_map.setdefault(entity_hash, name)

            for subject, predicate, obj in item.relations:
                s = _normalize_name(subject)
                p = _normalize_name(predicate)
                o = _normalize_name(obj)
                if not (s and p and o):
                    continue

                s_canon = _canonical_name(s)
                p_canon = _canonical_name(p)
                o_canon = _canonical_name(o)
                relation_hash = compute_hash(f"{s_canon}|{p_canon}|{o_canon}")

                if is_new_paragraph:
                    relation_records.setdefault(
                        relation_hash,
                        (s, p, o, 1.0, item.paragraph_hash, empty_meta_blob),
                    )
                    paragraph_relation_links.add((item.paragraph_hash, relation_hash))

                for relation_entity in (s, o):
                    e_canon = _canonical_name(relation_entity)
                    if not e_canon:
                        continue
                    e_hash = compute_hash(e_canon)
                    entity_display.setdefault(e_hash, relation_entity)
                    if is_new_paragraph:
                        entity_counts[e_hash] += 1
                        paragraph_entity_mentions[(item.paragraph_hash, e_hash)] += 1
                    if e_hash not in self.vector_store:
                        entity_embed_map.setdefault(e_hash, relation_entity)

        try:
            cursor.execute("BEGIN")

            if paragraph_records:
                cursor.executemany(
                    """
                    INSERT OR IGNORE INTO paragraphs
                    (
                        hash, content, vector_index, created_at, updated_at, metadata, source, word_count,
                        event_time, event_time_start, event_time_end, time_granularity, time_confidence, knowledge_type
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    paragraph_records,
                )

            if entity_counts:
                entity_rows = [
                    (
                        entity_hash,
                        entity_display[entity_hash],
                        None,
                        int(count),
                        now_ts,
                        empty_meta_blob,
                    )
                    for entity_hash, count in entity_counts.items()
                ]
                try:
                    cursor.executemany(
                        """
                        INSERT INTO entities
                        (hash, name, vector_index, appearance_count, created_at, metadata)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(hash) DO UPDATE SET
                            appearance_count = entities.appearance_count + excluded.appearance_count
                        """,
                        entity_rows,
                    )
                except sqlite3.OperationalError:
                    cursor.executemany(
                        """
                        INSERT OR IGNORE INTO entities
                        (hash, name, vector_index, appearance_count, created_at, metadata)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        entity_rows,
                    )
                    cursor.executemany(
                        "UPDATE entities SET appearance_count = appearance_count + ? WHERE hash = ?",
                        [(int(count), entity_hash) for entity_hash, count in entity_counts.items()],
                    )

            if paragraph_entity_mentions:
                pe_rows = [
                    (paragraph_hash, entity_hash, int(mentions))
                    for (paragraph_hash, entity_hash), mentions in paragraph_entity_mentions.items()
                ]
                try:
                    cursor.executemany(
                        """
                        INSERT INTO paragraph_entities
                        (paragraph_hash, entity_hash, mention_count)
                        VALUES (?, ?, ?)
                        ON CONFLICT(paragraph_hash, entity_hash) DO UPDATE SET
                            mention_count = paragraph_entities.mention_count + excluded.mention_count
                        """,
                        pe_rows,
                    )
                except sqlite3.OperationalError:
                    cursor.executemany(
                        """
                        INSERT OR IGNORE INTO paragraph_entities
                        (paragraph_hash, entity_hash, mention_count)
                        VALUES (?, ?, ?)
                        """,
                        pe_rows,
                    )
                    cursor.executemany(
                        """
                        UPDATE paragraph_entities
                        SET mention_count = mention_count + ?
                        WHERE paragraph_hash = ? AND entity_hash = ?
                        """,
                        [(m, p, e) for (p, e, m) in pe_rows],
                    )

            if relation_records:
                relation_rows = [
                    (
                        relation_hash,
                        rel[0],
                        rel[1],
                        rel[2],
                        None,
                        rel[3],
                        now_ts,
                        rel[4],
                        rel[5],
                    )
                    for relation_hash, rel in relation_records.items()
                ]
                cursor.executemany(
                    """
                    INSERT OR IGNORE INTO relations
                    (hash, subject, predicate, object, vector_index, confidence, created_at, source_paragraph, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    relation_rows,
                )

            if paragraph_relation_links:
                pr_rows = [(p_hash, r_hash) for p_hash, r_hash in paragraph_relation_links]
                cursor.executemany(
                    """
                    INSERT OR IGNORE INTO paragraph_relations
                    (paragraph_hash, relation_hash)
                    VALUES (?, ?)
                    """,
                    pr_rows,
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise

        self.stats["relations_written"] += len(relation_records)

        if relation_records:
            edge_pairs = []
            relation_hashes = []
            for relation_hash, rel in relation_records.items():
                edge_pairs.append((rel[0], rel[2]))
                relation_hashes.append(relation_hash)

            with self.graph_store.batch_update():
                self.graph_store.add_edges(edge_pairs, relation_hashes=relation_hashes)
            self.stats["graph_edges_written"] += len(edge_pairs)

            if self._should_write_relation_vectors():
                await self._ensure_relation_vectors_for_records(relation_records)

        para_added = await self._embed_and_add_vectors(
            id_to_text=paragraph_embed_map,
            batch_size=max(1, int(self.args.embed_batch_size)),
            workers=self.embed_workers,
        )
        ent_added = await self._embed_and_add_vectors(
            id_to_text=entity_embed_map,
            batch_size=max(1, int(self.args.entity_embed_batch_size)),
            workers=self.embed_workers,
        )
        self.stats["paragraph_vectors_added"] += para_added
        self.stats["entity_vectors_added"] += ent_added

        self.vector_store.save()
        self.graph_store.save()

        self.stats["windows_committed"] += 1
        self._flush_state(last_seen_id)

    async def _embed_and_add_vectors(
        self,
        id_to_text: Dict[str, str],
        batch_size: int,
        workers: int,
    ) -> int:
        if not id_to_text:
            return 0
        if self.embedding_manager is None:
            raise MigrationError("embedding_manager 未初始化，无法写入向量")

        ids = []
        texts = []
        for hash_id, text in id_to_text.items():
            if hash_id in self.vector_store:
                continue
            ids.append(hash_id)
            texts.append(text)

        if not ids:
            return 0

        total_added = 0
        chunk_size = max(1, int(batch_size))
        for i in range(0, len(ids), chunk_size):
            chunk_ids = ids[i : i + chunk_size]
            chunk_texts = texts[i : i + chunk_size]

            embeddings = await self.embedding_manager.encode_batch(
                chunk_texts,
                batch_size=chunk_size,
                num_workers=max(1, int(workers)),
            )

            emb_arr = np.asarray(embeddings, dtype=np.float32)
            if emb_arr.ndim == 1:
                emb_arr = emb_arr.reshape(1, -1)
            if emb_arr.shape[0] != len(chunk_ids):
                logger.warning(
                    f"embedding 返回数量异常: expected={len(chunk_ids)}, got={emb_arr.shape[0]}，跳过该批次"
                )
                continue

            valid_vectors = []
            valid_ids = []
            for idx, vec in enumerate(emb_arr):
                if vec.ndim != 1:
                    continue
                if vec.shape[0] != self.vector_store.dimension:
                    logger.warning(
                        f"向量维度不匹配，跳过: id={chunk_ids[idx]}, got={vec.shape[0]}, expected={self.vector_store.dimension}"
                    )
                    continue
                if not np.all(np.isfinite(vec)):
                    logger.warning(f"向量含 NaN/Inf，跳过: id={chunk_ids[idx]}")
                    continue
                if chunk_ids[idx] in self.vector_store:
                    continue
                valid_vectors.append(vec)
                valid_ids.append(chunk_ids[idx])

            if valid_vectors:
                batch_vectors = np.stack(valid_vectors).astype(np.float32, copy=False)
                added = self.vector_store.add(batch_vectors, valid_ids)
                total_added += int(added)

        return total_added

    async def _verify(self, strict: bool) -> None:
        if self.selection is None:
            raise MigrationError("selection 未初始化")

        sample_size = min(2000, max(0, int(self.stats.get("source_matched_total", 0))))
        self.stats["verify_sample_size"] = sample_size

        if sample_size <= 0:
            self.stats["verify_passed"] = True
            return

        sample_rows = self.source_db.sample_rows_for_verify(self.selection, sample_size)
        para_missing = 0
        vec_missing = 0
        rel_missing = 0
        edge_missing = 0

        for row in sample_rows:
            try:
                mapped = self._map_row(row)
            except Exception:
                continue

            paragraph = self.metadata_store.get_paragraph(mapped.paragraph_hash)
            if paragraph is None:
                para_missing += 1
            if mapped.paragraph_hash not in self.vector_store:
                vec_missing += 1

            for s, p, o in mapped.relations:
                relation_hash = compute_hash(f"{_canonical_name(s)}|{_canonical_name(p)}|{_canonical_name(o)}")
                relation = self.metadata_store.get_relation(relation_hash)
                if relation is None:
                    rel_missing += 1
                if self.graph_store.get_edge_weight(s, o) <= 0.0:
                    edge_missing += 1

        self.stats["verify_paragraph_missing"] = para_missing
        self.stats["verify_vector_missing"] = vec_missing
        self.stats["verify_relation_missing"] = rel_missing
        self.stats["verify_edge_missing"] = edge_missing

        verify_passed = all(x == 0 for x in [para_missing, vec_missing, rel_missing, edge_missing])
        if strict and not verify_passed:
            self.failed = True
            self.fail_reason = (
                "严格校验失败: "
                f"paragraph_missing={para_missing}, vector_missing={vec_missing}, "
                f"relation_missing={rel_missing}, edge_missing={edge_missing}"
            )

        self.stats["verify_passed"] = verify_passed

    def _finalize(self) -> int:
        elapsed = time.time() - self.started_at
        self.stats["elapsed_seconds"] = elapsed

        report = {
            "success": not self.failed,
            "fail_reason": self.fail_reason,
            "args": vars(self.args),
            "source_db": str(self.source_db_path),
            "target_data_dir": str(self.target_data_dir),
            "selection": self.selection.fingerprint_payload() if self.selection else {},
            "filter_fingerprint": self.filter_fingerprint,
            "source_db_fingerprint": self.source_db_fingerprint,
            "state_file": str(self.state_file),
            "bad_rows_file": str(self.bad_rows_file),
            "stats": dict(self.stats),
            "timestamp": time.time(),
        }

        _dump_json_atomic(self.report_file, report)

        if self.failed:
            self.exit_code = 1
        elif self.stats.get("bad_rows", 0) > 0:
            self.exit_code = 2
        else:
            self.exit_code = 0

        print("\n=== Migration Report ===")
        print(f"success: {not self.failed}")
        if self.fail_reason:
            print(f"fail_reason: {self.fail_reason}")
        print(f"elapsed: {elapsed:.2f}s")
        print(f"source_matched_total: {self.stats['source_matched_total']}")
        print(f"scanned_rows: {self.stats['scanned_rows']}")
        print(f"valid_rows: {self.stats['valid_rows']}")
        print(f"migrated_rows: {self.stats['migrated_rows']}")
        print(f"skipped_existing_rows: {self.stats['skipped_existing_rows']}")
        print(f"bad_rows: {self.stats['bad_rows']}")
        print(f"paragraph_vectors_added: {self.stats['paragraph_vectors_added']}")
        print(f"entity_vectors_added: {self.stats['entity_vectors_added']}")
        print(f"relations_written: {self.stats['relations_written']}")
        print(
            "relation_vectors: "
            f"written={self.stats['relation_vectors_written']}, "
            f"failed={self.stats['relation_vectors_failed']}, "
            f"skipped={self.stats['relation_vectors_skipped']}"
        )
        print(f"graph_edges_written: {self.stats['graph_edges_written']}")
        print(f"windows_committed: {self.stats['windows_committed']}")
        print(f"last_committed_id: {self.stats['last_committed_id']}")
        print(
            "verify: "
            f"sample={self.stats['verify_sample_size']}, "
            f"paragraph_missing={self.stats['verify_paragraph_missing']}, "
            f"vector_missing={self.stats['verify_vector_missing']}, "
            f"relation_missing={self.stats['verify_relation_missing']}, "
            f"edge_missing={self.stats['verify_edge_missing']}, "
            f"passed={self.stats['verify_passed']}"
        )
        print(f"report_file: {self.report_file}")
        print("========================\n")

        return self.exit_code

    def _close(self) -> None:
        try:
            if self.metadata_store is not None:
                self.metadata_store.close()
        except Exception:
            pass
        self.source_db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="迁移 MaiBot chat_history 到 A_memorix（高性能 + 可断点续传 + 可确认筛选）"
    )

    parser.add_argument("--source-db", default=str(DEFAULT_SOURCE_DB), help="源数据库路径（默认 data/MaiBot.db）")
    parser.add_argument(
        "--target-data-dir",
        default=str(DEFAULT_TARGET_DATA_DIR),
        help="A_memorix 数据目录（默认 plugins/A_memorix/data）",
    )

    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument("--resume", dest="no_resume", action="store_false", help="启用断点续传（默认）")
    resume_group.add_argument("--no-resume", dest="no_resume", action="store_true", help="禁用断点续传")
    parser.set_defaults(no_resume=False)

    parser.add_argument("--reset-state", action="store_true", help="清空迁移状态文件后执行")
    parser.add_argument("--start-id", type=int, default=None, help="从指定 chat_history.id 开始迁移（覆盖断点）")
    parser.add_argument("--end-id", type=int, default=None, help="迁移到指定 chat_history.id")

    parser.add_argument("--read-batch-size", type=int, default=2000, help="源库分页读取大小（默认 2000）")
    parser.add_argument("--commit-window-rows", type=int, default=20000, help="每窗口提交行数（默认 20000）")
    parser.add_argument("--embed-batch-size", type=int, default=256, help="段落 embedding 批次大小（默认 256）")
    parser.add_argument(
        "--entity-embed-batch-size",
        type=int,
        default=512,
        help="实体 embedding 批次大小（默认 512）",
    )
    parser.add_argument("--embed-workers", type=int, default=None, help="embedding 并发数（默认读取配置）")
    parser.add_argument("--max-errors", type=int, default=500, help="坏行上限（默认 500）")
    parser.add_argument("--log-every", type=int, default=5000, help="日志输出步长（默认 5000）")

    parser.add_argument("--dry-run", action="store_true", help="仅预览不写入")
    parser.add_argument("--verify-only", action="store_true", help="仅执行严格校验")

    parser.add_argument("--time-from", default=None, help="开始时间：YYYY-MM-DD / YYYY/MM/DD / YYYY-MM-DD HH:mm[:ss]")
    parser.add_argument("--time-to", default=None, help="结束时间：YYYY-MM-DD / YYYY/MM/DD / YYYY-MM-DD HH:mm[:ss]")
    parser.add_argument("--stream-id", action="append", default=[], help="聊天流 stream_id（可重复）")
    parser.add_argument("--group-id", action="append", default=[], help="群号（可重复，自动映射 stream_id）")
    parser.add_argument("--user-id", action="append", default=[], help="用户号（可重复，自动映射 stream_id）")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    parser.add_argument("--preview-limit", type=int, default=20, help="预览样本条数（默认 20）")

    return parser


async def async_main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    runner = MigrationRunner(args)
    return await runner.run()


def main() -> int:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
