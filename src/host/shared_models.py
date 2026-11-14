"""共享供应商与模型目录的管理；宿主永不加载某个 Bot 的全局配置。"""

from pathlib import Path
from typing import Any, Dict, Set

import hashlib
import tomllib
import tomlkit

from src.common.process_lease import ProcessLease
from src.config.shared_models import (
    CATALOG_SECTIONS,
    extract_bot_model_settings,
    extract_catalog,
    merge_catalog,
    missing_references,
    read_catalog,
    validate_catalog,
)

from .config_transaction import commit_config_files, recover_config_transaction
from .registry import Registry


def _revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()


def _remove_task_references(data: Dict[str, Any], removed_models: Set[str]) -> None:
    """只移除本次删除的模型引用；联网任务引用的是服务名称。"""
    def clean(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "model_list" and isinstance(value, list):
                    for index in range(len(value) - 1, -1, -1):
                        if value[index] in removed_models:
                            del value[index]
                else:
                    clean(value)

    for task_name, task in data.get("model_task_config", {}).items():
        if task_name != "web_search":
            clean(task)


def _prepare_bot_configs(
    registry: Registry,
    previous: Dict[str, Any],
    catalog: Dict[str, Any],
    selected_path: Path,
    selected: tomlkit.TOMLDocument,
    removed_models: Set[str],
) -> Dict[Path, str]:
    """校验清理后的全部实例配置，只将当前实例及发生清理的文件纳入事务。"""
    _remove_task_references(selected, removed_models)
    files = {selected_path: tomlkit.dumps(selected)}
    for bot in registry.list_bots():
        local_path = bot.directory / "config" / "model_config.toml"
        if bot.initialization != "initialized":
            continue
        original = local_path.read_text(encoding="utf-8")
        local = tomllib.loads(original)
        is_selected = local_path.resolve() == selected_path.resolve()
        current = selected if is_selected else tomlkit.parse(original)
        if not is_selected:
            _remove_task_references(current, removed_models)
        new_errors = missing_references(merge_catalog(current.unwrap(), catalog)) - missing_references(
            merge_catalog(local, previous)
        )
        if new_errors:
            raise ValueError(f"{bot.name}: {'；'.join(sorted(new_errors))}")
        if not is_selected and tomlkit.dumps(current) != original:
            files[local_path] = tomlkit.dumps(current)
    return files


def save_effective_config(catalog_path: Path, local_path: Path, submitted: Dict[str, Any], revision: str | None) -> str:
    """模型定义与当前 Bot 设置按归属保存，删除引起的跨实例引用清理一并提交。"""
    host = catalog_path.parent
    with ProcessLease(host / "model-config.lock"):
        recover_config_transaction(host)
        if revision is not None and revision != _revision(catalog_path):
            raise ValueError("公共模型配置已改变，请重新加载后保存")
        previous = read_catalog(catalog_path)
        catalog = extract_catalog(submitted)
        removed_providers = {item["name"] for item in previous.get("api_providers", [])} - {
            item["name"] for item in catalog["api_providers"]
        }
        catalog["models"] = [
            item for item in catalog["models"] if item["api_provider"] not in removed_providers
        ]
        removed_models = {item["name"] for item in previous.get("models", [])} - {
            item["name"] for item in catalog["models"]
        }
        validate_catalog(catalog)
        local = tomlkit.parse(local_path.read_text(encoding="utf-8"))
        local_data = extract_bot_model_settings(submitted)
        for section in CATALOG_SECTIONS:
            local.pop(section, None)
        if "web" in local:
            local["web"].pop("services", None)
        for key, value in local_data.items():
            local[key] = value
        files = _prepare_bot_configs(Registry(host.parent), previous, catalog, local_path, local, removed_models)
        if catalog != previous:
            files = {catalog_path: tomlkit.dumps(catalog), **files}
        commit_config_files(host, files)
        return _revision(catalog_path)
