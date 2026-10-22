from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, get_origin

from fastapi import HTTPException

import json
import os
import re
import shutil
import stat

from src.common.logger import get_logger
from src.core.config_types import ConfigField
from src.plugin_runtime.runner.manifest_validator import is_reserved_plugin_directory
from src.webui.core import get_token_manager

logger = get_logger("webui.plugin_routes")

PLUGIN_CHANGELOG_FILENAMES = ("CHANGELOG.md", "Changelog.md", "changelog.md", "CHANGELOG.MD")
PLUGIN_README_FILENAMES = ("README.md", "Readme.md", "readme.md", "README.MD")
MAX_PLUGIN_CHANGELOG_CHARS = 500000
MAX_PLUGIN_README_CHARS = 500000


def require_plugin_token(maibot_session: Optional[str]) -> str:
    token_manager = get_token_manager()
    if not maibot_session or not token_manager.verify_token(maibot_session):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")
    return maibot_session


def validate_safe_path(user_path: str, base_path: Path) -> Path:
    base_resolved = base_path.resolve()
    if any(pattern in user_path for pattern in ["..", "\x00"]):
        logger.warning(f"检测到可疑路径: {user_path}")
        raise HTTPException(status_code=400, detail="路径包含非法字符")

    if user_path.startswith("/") or user_path.startswith("\\") or (len(user_path) > 1 and user_path[1] == ":"):
        logger.warning(f"检测到绝对路径: {user_path}")
        raise HTTPException(status_code=400, detail="不允许使用绝对路径")

    target_path = (base_path / user_path).resolve()
    try:
        target_path.relative_to(base_resolved)
    except ValueError as e:
        logger.warning(f"路径遍历攻击检测: {user_path} -> {target_path}")
        raise HTTPException(status_code=400, detail="路径超出允许范围") from e

    return target_path


def _resolve_safe_plugin_directory(plugin_path: Path, plugins_dir: Path, strict: bool) -> Optional[Path]:
    try:
        if plugin_path.is_symlink():
            raise HTTPException(status_code=400, detail="插件目录不能是符号链接")

        resolved_plugins_dir = plugins_dir.resolve()
        resolved_plugin_path = plugin_path.resolve()
        resolved_plugin_path.relative_to(resolved_plugins_dir)

        return resolved_plugin_path if resolved_plugin_path.is_dir() else None
    except HTTPException:
        if strict:
            raise
        logger.warning(f"已跳过不安全的插件目录: {plugin_path}")
        return None
    except (OSError, RuntimeError, ValueError):
        if strict:
            raise HTTPException(status_code=400, detail="插件目录超出允许范围") from None
        logger.warning(f"已跳过越界的插件目录: {plugin_path}")
        return None


def resolve_plugin_file_path(plugin_path: Path, relative_path: str, allow_missing: bool = True) -> Path:
    plugin_root = plugin_path.resolve()
    target_path = plugin_root / relative_path

    if target_path.exists() and target_path.is_symlink():
        raise HTTPException(status_code=400, detail=f"插件文件不能是符号链接: {relative_path}")

    try:
        resolved_target_path = target_path.resolve()
        resolved_target_path.relative_to(plugin_root)
    except (OSError, RuntimeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"插件文件超出允许范围: {relative_path}") from e

    if not allow_missing and not resolved_target_path.exists():
        raise HTTPException(status_code=404, detail=f"插件文件不存在: {relative_path}")

    return resolved_target_path


def validate_plugin_id(plugin_id: str) -> str:
    if not plugin_id or not plugin_id.strip():
        logger.warning("非法插件 ID: 空字符串")
        raise HTTPException(status_code=400, detail="插件 ID 不能为空")

    for pattern in ["/", "\\", "\x00", "..", "\n", "\r", "\t"]:
        if pattern in plugin_id:
            logger.warning(f"非法插件 ID 格式: {plugin_id} (包含危险字符)")
            raise HTTPException(status_code=400, detail="插件 ID 包含非法字符")

    if plugin_id.startswith(".") or plugin_id.endswith("."):
        logger.warning(f"非法插件 ID: {plugin_id}")
        raise HTTPException(status_code=400, detail="插件 ID 不能以点开头或结尾")

    if plugin_id in {".", ".."}:
        logger.warning(f"非法插件 ID: {plugin_id}")
        raise HTTPException(status_code=400, detail="插件 ID 不能为特殊目录名")

    return plugin_id


def parse_version(version_str: str) -> Tuple[int, int, int]:
    base_version = re.split(r"[-.](?:snapshot|dev|pre|alpha|beta|rc)", version_str, flags=re.IGNORECASE)[0]
    parts = base_version.split(".")
    if len(parts) < 3:
        parts.extend(["0"] * (3 - len(parts)))

    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        logger.warning(f"无法解析版本号: {version_str}，返回默认值 (0, 0, 0)")
        return 0, 0, 0


def deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for key, value in src.items():
        if key in dst and isinstance(dst[key], dict) and isinstance(value, dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = value


def normalize_dotted_keys(obj: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    dotted_items: List[Tuple[str, Any]] = []

    for key, value in obj.items():
        if "." in key:
            dotted_items.append((key, value))
        else:
            result[key] = normalize_dotted_keys(value) if isinstance(value, dict) else value

    for dotted_key, value in dotted_items:
        normalized_value = normalize_dotted_keys(value) if isinstance(value, dict) else value
        parts = dotted_key.split(".")
        if "" in parts:
            logger.warning(f"键路径包含空段: '{dotted_key}'")
            parts = [part for part in parts if part]
        if not parts:
            logger.warning(f"忽略空键路径: '{dotted_key}'")
            continue

        current = result
        for index, part in enumerate(parts[:-1]):
            if part in current and not isinstance(current[part], dict):
                path_ctx = ".".join(parts[: index + 1])
                logger.warning(f"键冲突：{part} 已存在且非字典，覆盖为字典以展开 {dotted_key} (路径 {path_ctx})")
                current[part] = {}
            current = current.setdefault(part, {})

        last_part = parts[-1]
        if last_part in current and isinstance(current[last_part], dict) and isinstance(normalized_value, dict):
            deep_merge(current[last_part], normalized_value)
        else:
            current[last_part] = normalized_value

    return result


def coerce_types(schema_part: Dict[str, Any], config_part: Dict[str, Any]) -> None:
    def is_list_type(tp: Any) -> bool:
        origin = get_origin(tp)
        return tp is list or origin is list

    for key, schema_val in schema_part.items():
        if key not in config_part:
            continue
        value = config_part[key]
        if isinstance(schema_val, ConfigField):
            if is_list_type(schema_val.type) and isinstance(value, str):
                config_part[key] = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(schema_val, dict) and isinstance(value, dict):
            coerce_types(schema_val, value)


def find_plugin_instance(plugin_id: str) -> Optional[Any]:
    from src.plugin_runtime.integration import get_plugin_runtime_manager

    manager = get_plugin_runtime_manager()
    for supervisor in manager.supervisors:
        registered = supervisor._registered_plugins.get(plugin_id)
        if registered is not None:
            return registered
    return None


def get_plugins_dir() -> Path:
    plugins_dir = Path("plugins").resolve()
    plugins_dir.mkdir(exist_ok=True)
    return plugins_dir

def get_plugin_config_path(plugin_id: str, plugin_path: Path) -> Path:
    return resolve_plugin_file_path(plugin_path, "config.toml")


def get_plugin_candidate_paths(plugin_id: str) -> Tuple[Path, Path]:
    plugins_dir = get_plugins_dir()
    folder_name = plugin_id.replace(".", "_")
    return validate_safe_path(folder_name, plugins_dir), validate_safe_path(plugin_id, plugins_dir)


def is_plugin_install_residue(plugin_path: Path) -> bool:
    """判断目录是否只是卸载失败留下的插件安装残留。"""
    if not plugin_path.exists() or not plugin_path.is_dir() or plugin_path.is_symlink():
        return False

    manifest_path = resolve_plugin_file_path(plugin_path, "_manifest.json")
    if manifest_path.exists():
        return False

    allowed_residue_files = {"config.toml"}
    try:
        return all(entry.is_file() and entry.name in allowed_residue_files for entry in plugin_path.iterdir())
    except OSError:
        return False


def resolve_installed_plugin_path(plugin_id: str) -> Optional[Path]:
    return find_plugin_path_by_id(plugin_id)


def parse_repository_url(repository_url: str) -> Tuple[str, str, str]:
    repo_url = repository_url.rstrip("/").removesuffix(".git")
    parts = repo_url.split("/")
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="无效的仓库 URL")
    return repo_url, parts[-2], parts[-1]


def load_manifest_json(manifest_path: Path) -> Optional[Dict[str, Any]]:
    if not manifest_path.exists():
        return None

    if manifest_path.is_symlink():
        logger.warning(f"已拒绝读取符号链接 manifest: {manifest_path}")
        return None

    try:
        manifest_path.resolve().relative_to(manifest_path.parent.resolve())
    except (OSError, RuntimeError, ValueError):
        logger.warning(f"已拒绝读取越界 manifest: {manifest_path}")
        return None

    try:
        with open(manifest_path, "r", encoding="utf-8") as file_obj:
            manifest = json.load(file_obj)
    except (OSError, UnicodeError, json.JSONDecodeError):
        logger.warning(f"读取插件 manifest 失败: {manifest_path}", exc_info=True)
        return None
    if not isinstance(manifest, dict):
        logger.warning(f"插件 manifest 顶层必须是 JSON 对象: {manifest_path}")
        return None
    return manifest


def iter_plugin_directories() -> List[Path]:
    plugins_dir = get_plugins_dir()
    plugin_directories: List[Path] = []
    for path in plugins_dir.iterdir():
        if is_reserved_plugin_directory(path):
            continue
        safe_path = _resolve_safe_plugin_directory(path, plugins_dir, strict=False)
        if safe_path is not None:
            plugin_directories.append(safe_path)
    return plugin_directories


def find_plugin_path_by_id(plugin_id: str) -> Optional[Path]:
    casefold_matched_path: Optional[Path] = None
    normalized_plugin_id = plugin_id.casefold()

    for plugin_path in iter_plugin_directories():
        manifest_path = resolve_plugin_file_path(plugin_path, "_manifest.json")
        manifest = load_manifest_json(manifest_path)
        if manifest is None:
            continue

        manifest_id = str(manifest.get("id") or "").strip()
        if manifest_id == plugin_id:
            return plugin_path

        if casefold_matched_path is None and manifest_id.casefold() == normalized_plugin_id:
            casefold_matched_path = plugin_path

    if casefold_matched_path is not None:
        logger.warning(f"插件 ID 大小写不一致，已按大小写不敏感匹配: {plugin_id} -> {casefold_matched_path}")
        return casefold_matched_path

    return None


def backup_file(file_path: Path, action: str, move_file: bool = False) -> Optional[Path]:
    if not file_path.exists():
        return None

    backup_name = f"{file_path.name}.{action}.{datetime.now().strftime('%Y%m%d%H%M%S')}"
    backup_dir = file_path.parent / "config_back"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / backup_name
    if move_file:
        shutil.move(file_path, backup_path)
    else:
        shutil.copy(file_path, backup_path)
    return backup_path


def remove_tree(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"拒绝删除符号链接路径: {path}")

    def remove_readonly(func: Any, target_path: str, _: Any) -> None:
        os.chmod(target_path, stat.S_IWRITE)
        func(target_path)

    shutil.rmtree(path, onerror=remove_readonly)
