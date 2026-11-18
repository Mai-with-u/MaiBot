"""宿主公共行为与保留策略；无全局配置或日志导入。"""
from pathlib import Path
from typing import Any, Dict, Optional

import copy
import os
import tomllib

RETENTION_DEFAULTS = {
    "images": {"enabled": True, "check_interval_hours": 6.0, "image_file_retention_days": 14, "no_file_result_retention_days": 30},
    "emoji": {"enabled": True, "check_interval_hours": 6.0, "emoji_file_retention_days": 30, "no_file_record_retention_days": 30},
    "logs": {"log_file_max_bytes": 5242880, "max_log_files": 30, "log_cleanup_days": 30},
}
RETENTION_PATHS = {"images": ("visual", "image_cache_cleanup"), "emoji": ("emoji", "cache_cleanup")}


def shared_settings_path() -> Optional[Path]:
    catalog = os.environ.get("MAIBOT_SHARED_MODEL_CATALOG")
    return Path(catalog).parent / "config.toml" if catalog else None


def validate_retention(data: Dict[str, Any]) -> Dict[str, Any]:
    if set(data) != set(RETENTION_DEFAULTS):
        raise ValueError("公共保留策略必须包含 images、emoji 和 logs")
    for section, defaults in RETENTION_DEFAULTS.items():
        if not isinstance(data[section], dict) or set(data[section]) != set(defaults):
            raise ValueError(f"公共保留策略 {section} 字段不完整或含未知字段")
        for key, value in data[section].items():
            if key == "enabled":
                if type(value) is not bool:
                    raise ValueError(f"{section}.{key} 必须是布尔值")
            elif key == "check_interval_hours":
                if (not isinstance(value, (int, float)) or isinstance(value, bool)) or not 1 / 60 <= value <= 8760:
                    raise ValueError(f"{section}.{key} 必须在 1 分钟到 1 年之间")
            elif (not isinstance(value, int) or isinstance(value, bool)) or value < 1:
                raise ValueError(f"{section}.{key} 必须是正整数")
    return copy.deepcopy(data)


def read_retention(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    path = path if path is not None else shared_settings_path()
    if path is None:
        return None
    with path.open("rb") as stream:
        return validate_retention(tomllib.load(stream)["retention"])


def merge_retention(local: Dict[str, Any], retention: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    policy = read_retention() if retention is None else retention
    result = copy.deepcopy(local)
    if policy is not None:
        for section, (parent, key) in RETENTION_PATHS.items():
            result.setdefault(parent, {})[key] = copy.deepcopy(policy[section])
        result.setdefault("log", {}).update(policy["logs"])
    return result


def strip_retention(local: Dict[str, Any]) -> None:
    for parent, key in RETENTION_PATHS.values():
        if parent in local:
            local[parent].pop(key, None)
    for key in RETENTION_DEFAULTS["logs"]:
        if "log" in local:
            local["log"].pop(key, None)


WEBUI_DEFAULTS = {"webui_style": 2, "enforce_public_outbound_url": True}


def validate_shared_webui(data: Dict[str, Any]) -> Dict[str, Any]:
    style = data["webui_style"]
    enforce = data["enforce_public_outbound_url"]
    if type(style) is not int or style not in (0, 1, 2):
        raise ValueError("界面风格必须是 0、1 或 2")
    if type(enforce) is not bool:
        raise ValueError("公网出站 URL 校验必须是布尔值")
    return {"webui_style": style, "enforce_public_outbound_url": enforce}


def read_shared_webui() -> Optional[Dict[str, Any]]:
    path = shared_settings_path()
    if path is None:
        return None
    with path.open("rb") as stream:
        return validate_shared_webui(tomllib.load(stream)["webui"])


def merge_shared_settings(local: Dict[str, Any]) -> Dict[str, Any]:
    path = shared_settings_path()
    if path is None:
        return copy.deepcopy(local)
    with path.open("rb") as stream:
        shared = tomllib.load(stream)
    result = merge_retention(local, validate_retention(shared["retention"]))
    result.setdefault("webui", {}).update(validate_shared_webui(shared["webui"]))
    return result


def strip_shared_settings(local: Dict[str, Any]) -> None:
    strip_retention(local)
    for key in WEBUI_DEFAULTS:
        local.get("webui", {}).pop(key, None)
    if "webui" in local and not local["webui"]:
        local.pop("webui")
