"""共享供应商与模型目录的管理；宿主永不加载某个 Bot 的全局配置。"""

from pathlib import Path
from typing import Any, Dict


import hashlib
import tomllib
import tomlkit

from src.config.shared_models import CATALOG_SECTIONS, extract_catalog, extract_bot_model_settings, merge_catalog, missing_references, read_catalog, validate_catalog
from src.common.process_lease import ProcessLease

from .registry import Registry
from .config_transaction import commit_config_files, recover_config_transaction


def _revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()


def _check_references(registry: Registry, previous: Dict[str, Any], catalog: Dict[str, Any],
                      selected_path: Path | None = None, submitted: Dict[str, Any] | None = None) -> None:
    for bot in registry.list_bots():
        local_path = bot.directory / "config" / "model_config.toml"
        if bot.initialization != "initialized":
            continue
        with local_path.open("rb") as stream:
            local = tomllib.load(stream)
        current = submitted if selected_path is not None and local_path.resolve() == selected_path.resolve() else local
        new_errors = missing_references(merge_catalog(current, catalog)) - missing_references(merge_catalog(local, previous))
        if new_errors:
            raise ValueError(f"{bot.name}: {'；'.join(sorted(new_errors))}")


def save_effective_config(catalog_path: Path, local_path: Path, submitted: Dict[str, Any], revision: str | None) -> str:
    """同一表单按归属提交：模型定义写公共目录，任务等设置写当前 Bot。"""
    host = catalog_path.parent
    with ProcessLease(host / "model-config.lock"):
        recover_config_transaction(host)
        if revision is not None and revision != _revision(catalog_path):
            raise ValueError("公共模型配置已改变，请重新加载后保存")
        previous = read_catalog(catalog_path)
        catalog = extract_catalog(submitted)
        validate_catalog(catalog)
        _check_references(Registry(host.parent), previous, catalog, local_path, submitted)
        local = tomlkit.parse(local_path.read_text(encoding="utf-8"))
        local_data = extract_bot_model_settings(submitted)
        for section in CATALOG_SECTIONS:
            local.pop(section, None)
        if "web" in local:
            local["web"].pop("services", None)
        for key, value in local_data.items():
            local[key] = value
        files = {local_path: tomlkit.dumps(local)}
        if catalog != previous:
            files = {catalog_path: tomlkit.dumps(catalog), **files}
        commit_config_files(host, files)
        return _revision(catalog_path)
