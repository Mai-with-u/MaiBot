"""将实例中的供应商和模型定义一次性收归公共目录，任务选择留在原实例。"""

from pathlib import Path
from typing import Any, Dict, List

import copy
import tomlkit

from src.web_search.config import WebServiceConfig

from src.config.config_base import AttributeData, ConfigBase
from src.config.model_configs import APIProvider, ModelInfo
from src.config.config_upgrade_hooks import MODEL_CONFIG_UPGRADE_HOOKS, apply_config_upgrade_hooks
from src.config.shared_models import CATALOG_VERSION, CATALOG_SECTIONS, validate_catalog

from .registry import BotRecord


def _config_values(value: Any) -> Any:
    """只写配置中显式提供的值，递归排除配置对象自动生成的字段文档。"""
    if isinstance(value, ConfigBase):
        return {
            key: _config_values(item) for key, item in value
            if key in value.model_fields_set and key not in {"field_docs", "suppress_any_warning"}
        }
    if isinstance(value, dict):
        return {key: _config_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_config_values(item) for item in value]
    return value


def plan_model_catalog(catalog: dict, bots: List[BotRecord], documents: Dict[Path, dict]) -> None:
    """合并旧实例定义并改写任务引用；不修改文件，失败时整个计划作废。"""
    version = catalog.get("inner", {}).get("version")
    if version is not None and version != CATALOG_VERSION:
        raise ValueError("不支持的公共模型配置版本")
    for section in CATALOG_SECTIONS:
        catalog.setdefault(section, tomlkit.aot())
    catalog.setdefault("web", {}).setdefault("services", {})
    for bot in bots:
        local_path = bot.directory / "config" / "model_config.toml"
        if bot.initialization != "initialized" or local_path not in documents:
            continue
        local = documents[local_path]
        if not any(section in local for section in CATALOG_SECTIONS) and "services" not in local.get("web", {}):
            continue
        old_version = local["inner"]["version"]
        hook_version = MODEL_CONFIG_UPGRADE_HOOKS[-1].target_version
        if local.get("models"):
            upgraded = apply_config_upgrade_hooks(
                local.unwrap(), "model_config.toml", old_version, hook_version,
            ).data
            local["models"] = upgraded["models"]
        if tuple(map(int, old_version.split("."))) < tuple(map(int, hook_version.split("."))):
            local["inner"]["version"] = hook_version
        renames: Dict[str, Dict[str, str]] = {section: {} for section in CATALOG_SECTIONS}
        for section, schema in (("api_providers", APIProvider), ("models", ModelInfo)):
            known = {entry["name"]: entry for entry in catalog[section]}
            for original in local.get(section, []):
                cleaned = schema.from_dict(AttributeData(), copy.deepcopy(dict(original)))
                entry = _config_values(cleaned)
                if section == "models":
                    entry["api_provider"] = renames["api_providers"].get(entry["api_provider"], entry["api_provider"])
                name = entry["name"]
                if name in known and schema.model_validate(known[name]) != schema.model_validate(entry):
                    name = f"{name} ({bot.bot_id[:8]})"
                    if name in known and schema.model_validate(known[name]) != schema.model_validate({**entry, "name": name}):
                        raise ValueError(f"{bot.name} 的模型迁移名称冲突: {name}")
                renames[section][entry["name"]] = name
                entry["name"] = name
                if name not in known:
                    catalog[section].append(entry)
                    known[name] = entry
            local.pop(section, None)

        def rename_tasks(node: Any, model_names: Dict[str, str]) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "model_list" and isinstance(value, list):
                        node[key] = [model_names.get(name, name) for name in value]
                    else:
                        rename_tasks(value, model_names)

        services = catalog["web"]["services"]
        web_renames: Dict[str, str] = {}
        for original_name, original in local.get("web", {}).get("services", {}).items():
            cleaned = WebServiceConfig.from_dict(AttributeData(), copy.deepcopy(dict(original)))
            entry = _config_values(cleaned)
            name = original_name
            if name in services and WebServiceConfig.model_validate(services[name]) != cleaned:
                name = f"{name} ({bot.bot_id[:8]})"
                if name in services and WebServiceConfig.model_validate(services[name]) != cleaned:
                    raise ValueError(f"{bot.name} 的联网服务迁移名称冲突: {name}")
            services[name] = entry
            web_renames[original_name] = name
        if "web" in local:
            local["web"].pop("services", None)
        for task_name, task in local.get("model_task_config", {}).items():
            rename_tasks(task, web_renames if task_name == "web_search" else renames["models"])

    catalog["inner"] = {"version": CATALOG_VERSION}
    validate_catalog(catalog)
    for section in CATALOG_SECTIONS:
        if not catalog[section]:
            catalog[section] = []
