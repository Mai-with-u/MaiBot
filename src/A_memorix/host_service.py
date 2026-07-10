from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence

import tomlkit

from src.common.logger import get_logger
from src.common.utils.utils_config import AMemorixConfigUtils
from src.config.official_configs import AMemorixConfig
from src.webui.utils.toml_utils import _update_toml_doc

from .paths import default_data_dir, repo_root, resolve_repo_path, schema_path
from .runtime_registry import set_runtime_kernel

if TYPE_CHECKING:
    from .core.runtime.sdk_memory_kernel import SDKMemoryKernel

logger = get_logger("a_memorix.host_service")

_INTERNAL_CONFIG_FIELDS = {"field_docs", "_validate_any", "suppress_any_warning"}


def _get_config_manager():
    from src.config.config import config_manager

    return config_manager


def _get_bot_config_path() -> Path:
    from src.config.config import BOT_CONFIG_PATH

    return BOT_CONFIG_PATH


def _to_builtin_data(obj: Any) -> Any:
    if hasattr(obj, "unwrap"):
        try:
            obj = obj.unwrap()
        except (AttributeError, TypeError, ValueError) as exc:
            logger.debug(f"配置值 unwrap 失败，保留原值: type={type(obj).__name__}, error={exc}")

    if isinstance(obj, dict):
        return {str(key): _to_builtin_data(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_to_builtin_data(value) for value in obj]
    return obj


def _strip_internal_config_fields(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            str(key): _strip_internal_config_fields(value)
            for key, value in obj.items()
            if str(key) not in _INTERNAL_CONFIG_FIELDS
        }
    if isinstance(obj, list):
        return [_strip_internal_config_fields(value) for value in obj]
    return obj


def _backup_config_file(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    backup_name = f"{path.name}.backup.{datetime.now().strftime('%Y%m%d%H%M%S')}"
    backup_path = path.parent / backup_name
    backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


class AMemorixHostService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._queue_lock = asyncio.Lock()
        self._kernel: Optional[SDKMemoryKernel] = None
        self._startup_task: Optional[asyncio.Task] = None
        self._runtime_state = "stopped"
        self._startup_error = ""
        self._startup_error_stage = ""
        self._startup_started_at: Optional[float] = None
        self._startup_finished_at: Optional[float] = None
        self._startup_queue_pending_count = 0
        self._startup_queue_cache_loaded = False
        self._config_cache: Dict[str, Any] | None = None
        self._reload_callback_registered = False

    async def start(self) -> None:
        if not self.is_enabled():
            logger.info("A_Memorix 未启用，跳过长期记忆运行时初始化")
            return
        await self._ensure_startup_task()

    async def stop(self) -> None:
        async with self._lock:
            await self._shutdown_locked()

    async def reload(self) -> None:
        async with self._lock:
            await self._shutdown_locked()
            self._config_cache = None
            config = self._read_config()

        if self._is_enabled_config(config):
            await self._ensure_startup_task()
        else:
            logger.info("A_Memorix 配置为未启用，运行时保持关闭")

    def get_config_path(self) -> Path:
        return _get_bot_config_path()

    def get_schema_path(self) -> Path:
        return schema_path()

    def get_config_schema(self) -> Dict[str, Any]:
        path = self.get_schema_path()
        if not path.exists():
            return {
                "plugin_id": "a_memorix",
                "plugin_info": {
                    "name": "A_Memorix",
                    "version": "",
                    "description": "A_Memorix 配置结构",
                    "author": "A_Dawn",
                },
                "sections": {},
                "layout": {"type": "auto", "tabs": []},
            }

        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def get_config(self) -> Dict[str, Any]:
        return dict(self._read_config())

    def is_enabled(self) -> bool:
        return self._is_enabled_config(self._read_config())

    @staticmethod
    def _is_enabled_config(config: Dict[str, Any]) -> bool:
        plugin_config = config.get("plugin") if isinstance(config, dict) else None
        if not isinstance(plugin_config, dict):
            return True
        return bool(plugin_config.get("enabled", True))

    def _build_default_config(self) -> Dict[str, Any]:
        return self._config_model_to_runtime_dict(AMemorixConfig())

    def get_raw_config_with_meta(self) -> Dict[str, Any]:
        config = self.get_config()
        default_config = self._build_default_config()
        raw_doc = tomlkit.document()
        raw_doc.add("a_memorix", config)
        return {
            "config": tomlkit.dumps(raw_doc),
            "exists": self.get_config_path().exists(),
            "using_default": config == default_config,
        }

    def get_raw_config(self) -> str:
        payload = self.get_raw_config_with_meta()
        return str(payload.get("config", "") or "")

    async def update_raw_config(self, raw_config: str) -> Dict[str, Any]:
        loaded = tomlkit.loads(raw_config)
        raw_payload = _to_builtin_data(loaded) if isinstance(loaded, dict) else {}
        config_payload = raw_payload.get("a_memorix") if isinstance(raw_payload.get("a_memorix"), dict) else raw_payload
        path, backup_path = await self._write_config_to_bot_config(config_payload)
        return {
            "success": True,
            "message": "配置已保存",
            "backup_path": str(backup_path) if backup_path is not None else "",
            "config_path": str(path),
        }

    async def update_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        path, backup_path = await self._write_config_to_bot_config(config)
        return {
            "success": True,
            "message": "配置已保存",
            "backup_path": str(backup_path) if backup_path is not None else "",
            "config_path": str(path),
        }

    async def invoke(self, component_name: str, args: Dict[str, Any] | None = None, *, timeout_ms: int = 30000) -> Any:
        """将 MaiBot 宿主请求路由到共享 A_Memorix 内核。

        本层负责启动状态、宿主参数适配、共享聊天范围、启动期写入排队和管理命令
        分发；检索、写入及维护操作的业务语义由内核服务负责。
        """
        del timeout_ms
        payload = args or {}
        if not self.is_enabled():
            return self._disabled_response(component_name)
        kernel = self._kernel
        if kernel is None or self._runtime_state != "ready":
            if self._runtime_state == "stopped":
                await self._ensure_startup_task()
            return await self._unavailable_response(component_name, payload)

        if component_name == "search_memory":
            from .core.runtime.sdk_memory_kernel import KernelSearchRequest

            chat_id = str(payload.get("chat_id", "") or "").strip()
            config = self._read_config()
            global_memory_sharing_enabled = bool(config.get("global_memory_sharing_enabled", False))
            search_chat_id = "" if global_memory_sharing_enabled else chat_id
            shared_chat_ids = ()
            if not global_memory_sharing_enabled:
                shared_chat_ids = tuple(AMemorixConfigUtils.get_shared_memory_session_ids(chat_id))

            return await kernel.search_memory(
                KernelSearchRequest(
                    query=str(payload.get("query", "") or ""),
                    limit=int(payload.get("limit", 5) or 5),
                    mode=str(payload.get("mode", "search") or "search"),
                    chat_id=search_chat_id,
                    shared_chat_ids=shared_chat_ids,
                    person_id=str(payload.get("person_id", "") or ""),
                    time_start=payload.get("time_start"),
                    time_end=payload.get("time_end"),
                    respect_filter=bool(payload.get("respect_filter", True)),
                    user_id=str(payload.get("user_id", "") or "").strip(),
                    group_id=str(payload.get("group_id", "") or "").strip(),
                )
            )

        if component_name == "enqueue_feedback_task":
            return await kernel.enqueue_feedback_task(
                query_tool_id=str(payload.get("query_tool_id", "") or ""),
                session_id=str(payload.get("session_id", "") or ""),
                query_timestamp=payload.get("query_timestamp"),
                structured_content=payload.get("structured_content")
                if isinstance(payload.get("structured_content"), dict)
                else {},
            )

        if component_name in {"ingest_summary", "ingest_text"}:
            return await self._dispatch_ingest_write(kernel, component_name, payload)

        if component_name == "get_person_profile":
            return await kernel.get_person_profile(
                person_id=str(payload.get("person_id", "") or ""),
                chat_id=str(payload.get("chat_id", "") or ""),
                limit=max(1, int(payload.get("limit", 10) or 10)),
            )

        if component_name == "maintain_memory":
            return await kernel.maintain_memory(
                action=str(payload.get("action", "") or ""),
                target=str(payload.get("target", "") or ""),
                hours=payload.get("hours"),
                reason=str(payload.get("reason", "") or ""),
                limit=max(1, int(payload.get("limit", 50) or 50)),
            )

        if component_name == "memory_stats":
            return kernel.memory_stats()

        admin_action_names = {
            "memory_graph_admin",
            "memory_source_admin",
            "memory_episode_admin",
            "memory_profile_admin",
            "memory_feedback_admin",
            "memory_runtime_admin",
            "memory_import_admin",
            "memory_tuning_admin",
            "memory_v5_admin",
            "memory_delete_admin",
            "memory_correction_admin",
            "memory_fuzzy_modify_admin",
        }
        if component_name in admin_action_names:
            kwargs = dict(payload)
            action = str(kwargs.pop("action", "") or "")
            result = await getattr(kernel, component_name)(action=action, **kwargs)
            if component_name == "memory_runtime_admin" and isinstance(result, dict):
                return {**result, **self._startup_status_payload()}
            return result

        raise RuntimeError(f"不支持的 A_Memorix 调用: {component_name}")

    async def _ensure_kernel(self) -> SDKMemoryKernel:
        async with self._lock:
            if self._kernel is None:
                raise RuntimeError(self._startup_error or "A_Memorix 正在初始化")
            return self._kernel

    async def _ensure_startup_task(self) -> None:
        await self._ensure_startup_queue_cache()
        async with self._lock:
            if self._kernel is not None and self._runtime_state == "ready":
                return
            if self._startup_task is not None and not self._startup_task.done():
                return
            self._runtime_state = "starting"
            self._startup_error = ""
            self._startup_error_stage = ""
            self._startup_started_at = time.time()
            self._startup_finished_at = None
            self._startup_task = asyncio.create_task(self._startup_kernel_task(), name="A_Memorix.host_startup")

    async def _startup_kernel_task(self) -> None:
        from .core.runtime.sdk_memory_kernel import SDKMemoryKernel

        kernel: Optional[SDKMemoryKernel] = None
        try:
            config = self._read_config()
            if not self._is_enabled_config(config):
                async with self._lock:
                    self._runtime_state = "stopped"
                    self._startup_finished_at = time.time()
                return
            async with self._lock:
                self._runtime_state = "migrating"
                self._startup_error_stage = "startup_migration"
            kernel = SDKMemoryKernel(plugin_root=repo_root(), config=config)
            await kernel.initialize()
            async with self._lock:
                self._kernel = kernel
                self._runtime_state = "ready"
                self._startup_error = ""
                self._startup_error_stage = ""
                self._startup_finished_at = time.time()
                set_runtime_kernel(kernel)
            await self._replay_startup_write_queue(kernel)
            async with self._lock:
                if self._kernel is kernel and self._runtime_state == "ready":
                    self._startup_finished_at = time.time()
        except asyncio.CancelledError:
            if kernel is not None:
                shutdown = getattr(kernel, "shutdown", None)
                if callable(shutdown):
                    await shutdown()
                else:
                    kernel.close()
            if self._kernel is kernel:
                self._kernel = None
            self._runtime_state = "stopped"
            self._startup_finished_at = time.time()
            set_runtime_kernel(None)
            raise
        except Exception as exc:
            if kernel is not None:
                shutdown = getattr(kernel, "shutdown", None)
                if callable(shutdown):
                    await shutdown()
                else:
                    kernel.close()
            set_runtime_kernel(None)
            async with self._lock:
                self._kernel = None
                self._runtime_state = "failed"
                self._startup_error = str(exc)
                if not self._startup_error_stage:
                    self._startup_error_stage = "startup"
                self._startup_finished_at = time.time()
            logger.error(f"A_Memorix 后台初始化失败: {exc}", exc_info=True)

    def _read_config(self) -> Dict[str, Any]:
        if self._config_cache is not None:
            return dict(self._config_cache)

        try:
            config_model = _get_config_manager().get_global_config().a_memorix
        except Exception as exc:
            logger.warning(f"读取 A_Memorix 主配置失败，使用默认值: {exc}")
            defaults = self._build_default_config()
            self._config_cache = defaults
            return dict(defaults)

        self._config_cache = self._config_model_to_runtime_dict(config_model)
        return dict(self._config_cache)

    def _runtime_data_dir(self) -> Path:
        config = self._read_config()
        storage_cfg = config.get("storage") if isinstance(config, dict) else {}
        data_dir = "./data"
        if isinstance(storage_cfg, dict):
            data_dir = str(storage_cfg.get("data_dir", data_dir) or data_dir)
        return resolve_repo_path(data_dir, fallback=default_data_dir())

    def _startup_queue_path(self) -> Path:
        return self._runtime_data_dir() / "startup_write_queue.jsonl"

    def _startup_queue_done_path(self) -> Path:
        return self._runtime_data_dir() / "startup_write_queue.done.jsonl"

    def _startup_queue_failed_path(self) -> Path:
        return self._runtime_data_dir() / "startup_write_queue.failed.jsonl"

    @staticmethod
    def _ensure_jsonl_append_boundary(path: Path) -> None:
        if not path.exists() or path.stat().st_size <= 0:
            return
        with path.open("rb+") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) == b"\n":
                return
            handle.seek(0, os.SEEK_END)
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _append_jsonl_sync(self, path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self._ensure_jsonl_append_boundary(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    async def _append_jsonl(self, path: Path, payload: Dict[str, Any]) -> None:
        async with self._queue_lock:
            await asyncio.to_thread(self._append_jsonl_sync, path, payload)

    def _read_jsonl(self, path: Path) -> list[Dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                token = line.strip()
                if not token:
                    continue
                try:
                    payload = json.loads(token)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
        return rows

    def _queue_done_ids(self) -> set[str]:
        return {
            str(item.get("record_id", "") or "").strip()
            for item in self._read_jsonl(self._startup_queue_done_path())
            if str(item.get("record_id", "") or "").strip()
        }

    def _startup_queue_pending_records(self) -> list[Dict[str, Any]]:
        done_ids = self._queue_done_ids()
        records = []
        for item in self._read_jsonl(self._startup_queue_path()):
            record_id = str(item.get("record_id", "") or "").strip()
            component_name = str(item.get("component_name", "") or "").strip()
            if not record_id or record_id in done_ids:
                continue
            if component_name not in {"ingest_summary", "ingest_text"}:
                continue
            records.append(item)
        records.sort(key=lambda item: float(item.get("created_at", 0.0) or 0.0))
        return records

    async def _ensure_startup_queue_cache(self) -> None:
        if self._startup_queue_cache_loaded:
            return
        async with self._queue_lock:
            if self._startup_queue_cache_loaded:
                return
            records = await asyncio.to_thread(self._startup_queue_pending_records)
            self._startup_queue_pending_count = len(records)
            self._startup_queue_cache_loaded = True

    def _startup_status_payload(self) -> Dict[str, Any]:
        return {
            "enabled": self.is_enabled(),
            "runtime_ready": self._runtime_state == "ready",
            "startup_state": self._runtime_state,
            "initializing": self._runtime_state in {"starting", "migrating"},
            "initialization_failed": self._runtime_state == "failed",
            "error_stage": self._startup_error_stage,
            "error": self._startup_error,
            "startup_started_at": self._startup_started_at,
            "startup_finished_at": self._startup_finished_at,
            "startup_queue_pending": self._startup_queue_pending_count,
            "data_dir": str(self._runtime_data_dir()),
        }

    async def _enqueue_startup_write(self, component_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = {
            "record_id": uuid.uuid4().hex,
            "component_name": component_name,
            "payload": payload,
            "created_at": time.time(),
        }
        await self._append_jsonl(self._startup_queue_path(), record)
        self._startup_queue_pending_count += 1
        return {
            "success": True,
            "queued": True,
            "initializing": True,
            "reason": "a_memorix_initializing_queued",
            "record_id": record["record_id"],
            "stored_ids": [],
            "skipped_ids": [],
            "detail": "A_Memorix 正在初始化，写入已进入启动队列",
        }

    async def _unavailable_response(self, component_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        status = self._startup_status_payload()
        initializing = bool(status.get("initializing"))
        failed = bool(status.get("initialization_failed"))
        if initializing and component_name in {"ingest_summary", "ingest_text"}:
            return await self._enqueue_startup_write(component_name, payload)

        reason = "a_memorix_initializing" if initializing else "a_memorix_initialization_failed"
        message = "A_Memorix 正在初始化" if initializing else f"A_Memorix 初始化失败: {self._startup_error}"
        base = {
            "success": component_name == "memory_runtime_admin"
            or (not failed and component_name in {"search_memory", "get_person_profile", "memory_stats"}),
            "reason": reason,
            "message": message,
            **status,
        }
        if component_name == "search_memory":
            return {**base, "summary": "", "hits": [], "filtered": False}
        if component_name == "get_person_profile":
            return {**base, "summary": "", "traits": [], "evidence": []}
        if component_name == "memory_stats":
            return {**base, "paragraph_count": 0, "relation_count": 0, "episode_count": 0}
        if component_name == "memory_runtime_admin":
            return base
        if component_name in {"ingest_summary", "ingest_text"}:
            return {
                **base,
                "success": False,
                "queued": False,
                "stored_ids": [],
                "skipped_ids": [reason],
                "detail": message,
            }
        if component_name == "enqueue_feedback_task":
            return {**base, "queued": False}
        return {**base, "success": False, "error": message}

    async def _dispatch_ingest_write(self, kernel: SDKMemoryKernel, component_name: str, payload: Dict[str, Any]) -> Any:
        if component_name == "ingest_summary":
            return await kernel.ingest_summary(
                external_id=str(payload.get("external_id", "") or ""),
                chat_id=str(payload.get("chat_id", "") or ""),
                text=str(payload.get("text", "") or ""),
                participants=list(payload.get("participants") or []),
                time_start=payload.get("time_start"),
                time_end=payload.get("time_end"),
                tags=list(payload.get("tags") or []),
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
                respect_filter=bool(payload.get("respect_filter", True)),
                user_id=str(payload.get("user_id", "") or "").strip(),
                group_id=str(payload.get("group_id", "") or "").strip(),
            )
        if component_name == "ingest_text":
            return await kernel.ingest_text(
                external_id=str(payload.get("external_id", "") or ""),
                source_type=str(payload.get("source_type", "") or ""),
                text=str(payload.get("text", "") or ""),
                chat_id=str(payload.get("chat_id", "") or ""),
                person_ids=list(payload.get("person_ids") or []),
                participants=list(payload.get("participants") or []),
                timestamp=payload.get("timestamp"),
                time_start=payload.get("time_start"),
                time_end=payload.get("time_end"),
                tags=list(payload.get("tags") or []),
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
                entities=list(payload.get("entities") or []),
                relations=list(payload.get("relations") or []),
                respect_filter=bool(payload.get("respect_filter", True)),
                user_id=str(payload.get("user_id", "") or "").strip(),
                group_id=str(payload.get("group_id", "") or "").strip(),
            )
        raise ValueError(f"不支持的启动队列写入类型: {component_name}")

    async def _replay_startup_write_queue(self, kernel: SDKMemoryKernel) -> None:
        records = await asyncio.to_thread(self._startup_queue_pending_records)
        self._startup_queue_pending_count = len(records)
        self._startup_queue_cache_loaded = True
        if not records:
            return
        logger.info(f"A_Memorix 开始回放启动写入队列: pending={len(records)}")
        for record in records:
            record_id = str(record.get("record_id", "") or "").strip()
            component_name = str(record.get("component_name", "") or "").strip()
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            try:
                result = await self._dispatch_ingest_write(kernel, component_name, payload)
                if isinstance(result, dict) and result.get("success") is False:
                    raise RuntimeError(str(result.get("detail") or result.get("error") or result))
                await self._append_jsonl(
                    self._startup_queue_done_path(),
                    {
                        "record_id": record_id,
                        "component_name": component_name,
                        "finished_at": time.time(),
                        "result": result if isinstance(result, dict) else {},
                    },
                )
                self._startup_queue_pending_count = max(0, self._startup_queue_pending_count - 1)
            except Exception as exc:
                await self._append_jsonl(
                    self._startup_queue_failed_path(),
                    {
                        "record_id": record_id,
                        "component_name": component_name,
                        "failed_at": time.time(),
                        "error": str(exc),
                    },
                )
                logger.warning(f"A_Memorix 启动队列回放失败: record={record_id}, error={exc}")

    @staticmethod
    def _config_model_to_runtime_dict(config_model: AMemorixConfig) -> Dict[str, Any]:
        payload = config_model.model_dump(mode="json")
        web_config = payload.get("web")
        if isinstance(web_config, dict) and "import_config" in web_config:
            web_config["import"] = web_config.pop("import_config")
        payload = _to_builtin_data(payload) if isinstance(payload, dict) else {}
        return _strip_internal_config_fields(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _runtime_dict_to_bot_config_dict(config: Dict[str, Any]) -> Dict[str, Any]:
        payload = _to_builtin_data(config)
        if not isinstance(payload, dict):
            return {}
        payload = _strip_internal_config_fields(payload)
        web_config = payload.get("web")
        if isinstance(web_config, dict) and "import_config" in web_config and "import" not in web_config:
            web_config["import"] = web_config.pop("import_config")
        return payload

    async def _write_config_to_bot_config(self, config: Dict[str, Any]) -> tuple[Path, Optional[Path]]:
        path = self.get_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        backup_path = _backup_config_file(path)
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                doc = tomlkit.load(handle)
        else:
            doc = tomlkit.document()

        bot_config_payload = self._runtime_dict_to_bot_config_dict(config)
        current = doc.get("a_memorix")
        if isinstance(current, dict):
            _update_toml_doc(current, bot_config_payload)
        else:
            doc["a_memorix"] = bot_config_payload

        with path.open("w", encoding="utf-8") as handle:
            tomlkit.dump(doc, handle)

        await _get_config_manager().reload_config(changed_scopes=("bot",))
        if not self._reload_callback_registered:
            await self.reload()
        return path, backup_path

    def register_config_reload_callback(self) -> None:
        if self._reload_callback_registered:
            return
        _get_config_manager().register_reload_callback(self.on_config_reload)
        self._reload_callback_registered = True

    async def on_config_reload(self, changed_scopes: Sequence[str] | None = None) -> None:
        normalized = {str(scope or "").strip().lower() for scope in (changed_scopes or [])}
        if normalized and "bot" not in normalized:
            return
        await self.reload()

    @staticmethod
    def _disabled_response(component_name: str) -> Dict[str, Any]:
        reason = "a_memorix_disabled"
        message = "A_Memorix 未启用，请在长期记忆配置中开启后再使用。"

        if component_name == "search_memory":
            return {
                "success": True,
                "disabled": True,
                "reason": reason,
                "summary": "",
                "hits": [],
                "filtered": False,
            }

        if component_name in {"ingest_summary", "ingest_text"}:
            return {
                "success": True,
                "disabled": True,
                "reason": reason,
                "stored_ids": [],
                "skipped_ids": [reason],
                "detail": reason,
            }

        if component_name == "get_person_profile":
            return {
                "success": True,
                "disabled": True,
                "reason": reason,
                "summary": "",
                "traits": [],
                "evidence": [],
            }

        if component_name == "memory_stats":
            return {
                "success": True,
                "enabled": False,
                "disabled": True,
                "reason": reason,
                "message": message,
                "paragraph_count": 0,
                "relation_count": 0,
                "episode_count": 0,
            }

        if component_name == "memory_runtime_admin":
            return {
                "success": True,
                "enabled": False,
                "disabled": True,
                "reason": reason,
                "message": message,
                "runtime_ready": False,
                "embedding_degraded": False,
                "embedding_dimension": 0,
                "auto_save": False,
                "data_dir": "",
            }

        if component_name == "enqueue_feedback_task":
            return {
                "success": True,
                "queued": False,
                "disabled": True,
                "reason": reason,
            }

        return {
            "success": False,
            "enabled": False,
            "disabled": True,
            "reason": reason,
            "error": message,
        }

    async def _shutdown_locked(self) -> None:
        task = self._startup_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._startup_task = None

        if self._kernel is None:
            self._runtime_state = "stopped"
            self._startup_finished_at = time.time()
            set_runtime_kernel(None)
            return
        shutdown = getattr(self._kernel, "shutdown", None)
        if callable(shutdown):
            await shutdown()
        else:
            self._kernel.close()
        self._kernel = None
        self._runtime_state = "stopped"
        self._startup_finished_at = time.time()
        set_runtime_kernel(None)


a_memorix_host_service = AMemorixHostService()
