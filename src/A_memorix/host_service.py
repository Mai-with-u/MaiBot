from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence

import tomlkit

from src.common.logger import get_logger
from src.common.utils.utils_config import AMemorixConfigUtils
from src.config.official_configs import AMemorixConfig
from src.webui.utils.toml_utils import _update_toml_doc

from .paths import repo_root, schema_path
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
        except Exception:
            pass

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
        self._kernel: Optional[SDKMemoryKernel] = None
        self._config_cache: Dict[str, Any] | None = None
        self._reload_callback_registered = False

    async def start(self) -> None:
        if not self.is_enabled():
            logger.info("A_Memorix 未启用，跳过长期记忆运行时初始化")
            return
        await self._ensure_kernel()

    async def stop(self) -> None:
        async with self._lock:
            await self._shutdown_locked()

    async def reload(self) -> None:
        async with self._lock:
            await self._shutdown_locked()
            self._config_cache = None
            config = self._read_config()

        if self._is_enabled_config(config):
            await self._ensure_kernel()
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
        del timeout_ms
        payload = args or {}
        if not self.is_enabled():
            return self._disabled_response(component_name)
        kernel = await self._ensure_kernel()

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
            relations = payload.get("relations") if isinstance(payload.get("relations"), list) else []
            entities = payload.get("entities") if isinstance(payload.get("entities"), list) else []
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
                entities=entities,
                relations=relations,
                respect_filter=bool(payload.get("respect_filter", True)),
                user_id=str(payload.get("user_id", "") or "").strip(),
                group_id=str(payload.get("group_id", "") or "").strip(),
            )

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
            return await getattr(kernel, component_name)(action=action, **kwargs)

        raise RuntimeError(f"不支持的 A_Memorix 调用: {component_name}")

    async def _ensure_kernel(self) -> SDKMemoryKernel:
        async with self._lock:
            if self._kernel is None:
                from .core.runtime.sdk_memory_kernel import SDKMemoryKernel

                config = self._read_config()
                if not self._is_enabled_config(config):
                    raise RuntimeError("A_Memorix 未启用")
                kernel = SDKMemoryKernel(plugin_root=repo_root(), config=config)
                try:
                    await kernel.initialize()
                except Exception:
                    kernel.close()
                    raise
                self._kernel = kernel
                set_runtime_kernel(kernel)
            return self._kernel

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
        if self._kernel is None:
            return
        shutdown = getattr(self._kernel, "shutdown", None)
        if callable(shutdown):
            await shutdown()
        else:
            self._kernel.close()
        self._kernel = None
        set_runtime_kernel(None)


a_memorix_host_service = AMemorixHostService()
