"""将实例中的供应商和模型定义一次性收归公共目录，任务选择留在原实例。"""

from pathlib import Path
from typing import Any, Dict, List

import copy
import hashlib
import tomlkit

from src.common.logger import get_logger
from src.web_search.config import WebServiceConfig

from src.config.config_base import AttributeData, ConfigBase
from src.config.model_configs import APIProvider, ModelInfo
from src.config.config_upgrade_hooks import MODEL_CONFIG_UPGRADE_HOOKS, apply_config_upgrade_hooks
from src.config.shared_models import CATALOG_VERSION, CATALOG_SECTIONS, validate_catalog

from .registry import BotRecord


logger = get_logger("host.model_migration")


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
    """在内存中迁移当前模型配置，并分别验证历史版本。"""
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
        if _has_shared_definitions(local):
            _plan_model_document(catalog, local, bot, bot.bot_id[:8])

        version_root = bot.directory / "config" / "versions" / "model"
        for path in sorted(path for path in documents if path.parent == version_root):
            snapshot = documents[path]
            # 历史版本单独验证，成功后才纳入公共目录及事务提交。
            try:
                if not _has_shared_definitions(snapshot):
                    continue
                candidate = copy.deepcopy(catalog)
                snapshot = copy.deepcopy(snapshot)
                namespace = bot.bot_id[:8] + "-" + hashlib.sha256(path.name.encode()).hexdigest()[:8]
                _plan_model_document(candidate, snapshot, bot, namespace)
                validate_catalog(candidate)
            except (KeyError, TypeError, ValueError):
                logger.exception(f"历史模型配置无效，已跳过迁移：{path}")
                continue
            for section in (*CATALOG_SECTIONS, "web"):
                catalog[section] = candidate[section]
            documents[path] = snapshot

    catalog["inner"] = {"version": CATALOG_VERSION}
    validate_catalog(catalog)
    for section in CATALOG_SECTIONS:
        if not catalog[section]:
            catalog[section] = []


def _has_shared_definitions(document: dict) -> bool:
    web = document.get("web", {})
    if not isinstance(web, dict):
        raise ValueError("模型配置 web 必须是表")
    return any(section in document for section in CATALOG_SECTIONS) or "services" in web


def _plan_model_document(catalog: dict, local: dict, bot: BotRecord, namespace: str) -> None:
    old_version = local["inner"]["version"]
    local_services = local.get("web", {}).get("services", {})
    local_tasks = local.get("model_task_config", {})
    if not isinstance(old_version, str) or not isinstance(local_services, dict) or not isinstance(local_tasks, dict):
        raise ValueError("模型配置版本、联网服务或任务结构无效")
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
        known = {entry.get("name", ""): entry for entry in catalog[section]}
        for original in local.get(section, []):
            cleaned = schema.from_dict(AttributeData(), copy.deepcopy(dict(original)))
            entry = _config_values(cleaned)
            entry["name"] = cleaned.name
            if section == "models":
                entry["api_provider"] = renames["api_providers"].get(cleaned.api_provider, cleaned.api_provider)
            name = entry["name"]
            if name in known and schema.model_validate(known[name]) != schema.model_validate(entry):
                # 副本可能与当前配置同名却有不同定义；先复用已经迁入的同一配置。
                equivalent = next((key for key, value in known.items()
                    if schema.model_validate(value) == schema.model_validate({**entry, "name": key})), None)
                name = equivalent if equivalent is not None else f"{name} ({namespace})"
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
    for original_name, original in local_services.items():
        cleaned = WebServiceConfig.from_dict(AttributeData(), copy.deepcopy(dict(original)))
        entry = _config_values(cleaned)
        name = original_name
        if name in services and WebServiceConfig.model_validate(services[name]) != cleaned:
            equivalent = next((key for key, value in services.items()
                if WebServiceConfig.model_validate(value) == cleaned), None)
            name = equivalent if equivalent is not None else f"{name} ({namespace})"
            if name in services and WebServiceConfig.model_validate(services[name]) != cleaned:
                raise ValueError(f"{bot.name} 的联网服务迁移名称冲突: {name}")
        services[name] = entry
        web_renames[original_name] = name
    if "web" in local:
        local["web"].pop("services", None)
    for task_name, task in local_tasks.items():
        rename_tasks(task, web_renames if task_name == "web_search" else renames["models"])
