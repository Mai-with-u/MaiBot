"""答疑助手的插件配置读取与局部修改，复用插件配置页的校验及保存流程。"""

from typing import Any, Dict, List, Set, Tuple
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
import re

from src.llm_models.payload_content.tool_option import ToolDefinitionInput

from .ai_help_diagnostics import redact_diagnostic_message
from .ai_search_local_config import LOCAL_CONFIG_REDACTED_VALUE, redact_config_value
from .ai_search_models import AISearchConfigChange, AISearchConfigUpdate, PluginReference


class PluginFieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: List[str] = Field(min_length=2, max_length=2, description="read_plugin_config 返回的字段 path 数组")
    value: Any = Field(description="要设置的新值，保持字段所需的数据类型")


class PluginConfigPatch(PluginReference):
    changes: List[PluginFieldChange] = Field(min_length=1, max_length=20)


async def load_plugin_config(plugin_id: str) -> Dict[str, Any]:
    from src.webui.routers.plugin.config_routes import get_plugin_config_bundle

    return await get_plugin_config_bundle(plugin_id)


async def save_plugin_config(plugin_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    from src.webui.routers.plugin.config_routes import update_plugin_config
    from src.webui.routers.plugin.schemas import UpdatePluginConfigRequest

    return await update_plugin_config(plugin_id, UpdatePluginConfigRequest(config=patch))


async def list_installed_plugins() -> Dict[str, Any]:
    from src.webui.routers.plugin.management import get_installed_plugins

    result = await get_installed_plugins()
    return {
        "plugins": [
            {
                "id": plugin["id"],
                "name": plugin["manifest"].get("name", plugin["id"]),
                "version": plugin["manifest"].get("version", ""),
                "enabled": plugin["enabled"],
                "load_status": plugin["load_status"],
                "load_error": redact_diagnostic_message(plugin["load_error"]) if plugin["load_error"] else "",
            }
            for plugin in result["plugins"]
        ]
    }


def build_plugin_config_tools() -> List[ToolDefinitionInput]:
    return [
        {
            "name": "list_installed_plugins",
            "description": "列出本机已安装插件的真实 ID、版本、启用和加载状态，以及脱敏后的加载失败或超时原因。排查本机插件加载问题时优先调用。",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {
            "name": "read_plugin_config",
            "description": "读取已安装插件的配置字段定义和当前值，敏感值隐藏。配置读取失败时可返回插件加载诊断。修改前必须成功读取字段。",
            "parameters": PluginReference.model_json_schema(),
        },
        {
            "name": "update_plugin_config",
            "description": "仅在用户明确要求修改时，提交已读取插件字段的局部改动；保留其他字段，执行配置校验与备份。不能修改敏感或只读字段。",
            "parameters": PluginConfigPatch.model_json_schema(),
        },
    ]


def _redact(key: str, value: Any) -> Any:
    """兼容插件的 camelCase 字段名，递归处理复合字段及 URL 凭据。"""

    normalized_key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    if redact_config_value(normalized_key, "probe") == LOCAL_CONFIG_REDACTED_VALUE:
        return LOCAL_CONFIG_REDACTED_VALUE if value else value
    if isinstance(value, dict):
        return {name: _redact(name, item) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(key, item) for item in value]
    return redact_config_value(normalized_key, value)


def _fields(bundle: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    if bundle.get("config_error"):
        raise ValueError("插件配置存在错误，请先在插件配置页修复")
    fields: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for section, definition in bundle["schema"]["sections"].items():
        for name, metadata in definition["fields"].items():
            # 点号键会被现有保存接口展开，不能把这种字段误当普通字段写入。
            if "." in section or "." in name:
                continue
            current = bundle["config"].get(section, {}).get(name)
            qualified_name = f"{section}_{name}"
            sensitive = (
                _redact(qualified_name, "probe") == LOCAL_CONFIG_REDACTED_VALUE
                or metadata.get("input_type") == "password"
                or metadata.get("ui_type") == "password"
                or bool(metadata.get("hidden"))
                or _redact(qualified_name, current) != current
            )
            fields[(section, name)] = {
                "path": [section, name],
                "type": metadata["type"],
                "description": metadata.get("description", ""),
                "label": metadata.get("label", name),
                "choices": None if sensitive else metadata.get("choices"),
                "value": LOCAL_CONFIG_REDACTED_VALUE if sensitive else jsonable_encoder(current),
                "read_only": sensitive or bool(metadata.get("disabled")),
            }
    return fields


class PluginConfigStore:
    def __init__(self) -> None:
        self.snapshots: Dict[str, Dict[Tuple[str, str], Dict[str, Any]]] = {}
        self.updates: List[AISearchConfigUpdate] = []

    async def read(self, plugin_id: str) -> Dict[str, Any]:
        bundle = await load_plugin_config(plugin_id)
        fields = _fields(bundle)
        # 并发校验只需要已展示的字段快照，避免保留原始 TOML 和敏感值。
        self.snapshots[plugin_id] = fields
        return {
            "plugin": {"id": plugin_id, "name": bundle["schema"]["plugin_info"]["name"]},
            "fields": list(fields.values()),
        }

    async def update(self, request: PluginConfigPatch) -> Dict[str, Any]:
        known = self.snapshots.get(request.plugin_id)
        if known is None:
            raise ValueError("修改前必须先调用 read_plugin_config 读取真实字段与当前值")
        bundle = await load_plugin_config(request.plugin_id)
        current = _fields(bundle)
        patch: Dict[str, Any] = {}
        changes: List[AISearchConfigChange] = []
        seen: Set[Tuple[str, str]] = set()
        for change in request.changes:
            section, name = change.path
            path = (section, name)
            if path in seen or path not in known or path not in current:
                raise ValueError(f"字段不存在或重复: {'.'.join(path)}")
            seen.add(path)
            field = current[path]
            previous = known[path]
            if (
                previous["read_only"]
                or field["read_only"]
                or _redact(f"{section}_{name}", change.value) != change.value
            ):
                raise ValueError(f"敏感或只读字段请在插件配置页修改: {'.'.join(path)}")
            if field["value"] != previous["value"]:
                raise ValueError(f"字段已被其他操作修改，请重新读取配置: {'.'.join(path)}")
            if field["value"] == change.value:
                continue
            patch.setdefault(section, {})[name] = change.value
            changes.append(AISearchConfigChange(path=change.path, before=field["value"], after=change.value))
        if not changes:
            return {
                "plugin": {"id": request.plugin_id, "name": bundle["schema"]["plugin_info"]["name"]},
                "message": "配置已是目标值，无需修改",
            }
        result = await save_plugin_config(request.plugin_id, patch)
        if not result["success"]:
            raise ValueError(result["message"])
        # 保存接口返回校验后的实际值；SDK 虚构的 general 节在磁盘上已还原为根级字段。
        saved = result["config"]
        for change in changes:
            section, name = change.path
            actual = saved[name] if section == "general" and "general" not in saved else saved[section][name]
            change.after = _redact("_".join(change.path), jsonable_encoder(actual))
        update = AISearchConfigUpdate(
            id=uuid4().hex,
            plugin_id=request.plugin_id,
            name=bundle["schema"]["plugin_info"]["name"],
            changes=changes,
            message=result["message"],
        )
        self.updates.append(update)
        # 保存后的下一次操作仍需重新读取，以取得运行时规范化后的真实值。
        self.snapshots.pop(request.plugin_id)
        return {"plugin": {"id": request.plugin_id, "name": update.name}, "config_update": update.model_dump()}
