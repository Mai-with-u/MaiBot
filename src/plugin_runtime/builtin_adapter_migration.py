"""将已安装的 QQ 官方适配器迁入内置配置布局，保留旧代码和数据。"""

from pathlib import Path
from typing import Optional
from uuid import uuid4

import json
import shutil
import tomllib

from src.common.logger import get_logger
from src.common.paths import builtin_plugin_root, get_instance_root, resolve_plugin_config_path, third_party_plugin_root

logger = get_logger("plugin_runtime.builtin_migration")
PLUGIN_ID = "Mai-with-u.qq-adapter"


def _backup_directory(root: Path, source: Path, label: str) -> Path:
    """将旧目录移出扫描范围，所有备份都留在已校验的实例插件根目录内。"""
    backups = root / ".builtin-backups"
    if backups.is_symlink() or backups.resolve().parent != root:
        raise ValueError(f"QQ 官方适配器备份目录包含链接或越界: {backups}")
    backups.mkdir(exist_ok=True)
    backup = backups / f"{label}-{uuid4().hex}"
    if source.resolve().parent != root or source.is_symlink():
        raise ValueError(f"QQ 官方适配器待备份目录越界: {source}")
    source.rename(backup)
    return backup


def _migrate_legacy_data(root: Path) -> None:
    """兼容旧 Docker 把持久数据挂在插件扫描目录的布局。"""
    source = root / PLUGIN_ID
    target = get_instance_root() / "data" / "plugins" / PLUGIN_ID
    if not source.exists() or source == target:
        return
    if any((source / name).exists() for name in ("_manifest.json", "plugin.py", ".git")):
        return  # 目录名也可能正好是插件 ID，不能把旧代码当作数据搬动。
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"QQ 官方适配器旧数据目录无效: {source}")
    if not target.resolve().is_relative_to(get_instance_root().resolve()) or target.is_symlink():
        raise ValueError(f"QQ 官方适配器数据目录越界: {target}")
    files = list(source.rglob("*"))
    # 先检查全部冲突；不猜测哪个映射更新，也不以空数据覆盖历史绑定。
    for path in files:
        destination = target / path.relative_to(source)
        if path.is_symlink():
            raise ValueError(f"QQ 官方适配器旧数据包含链接: {path}")
        if not destination.resolve().is_relative_to(target.resolve()):
            raise ValueError(f"QQ 官方适配器数据路径越界: {destination}")
        if destination.exists():
            if path.is_dir() != destination.is_dir() or (
                path.is_file() and path.read_bytes() != destination.read_bytes()
            ):
                raise ValueError(f"QQ 官方适配器新旧数据冲突，请核对后再启动: {path} -> {destination}")
    for path in files:
        destination = target / path.relative_to(source)
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".migration-{uuid4().hex}")
            try:
                shutil.copy2(path, temporary)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
    backup = _backup_directory(root, source, "qq_official_adapter-data")
    logger.info(f"QQ 官方适配器旧数据已迁至 {target}，原数据保留于 {backup}")


def migrate_qq_official_adapter() -> Optional[Path]:
    """在索引和加载前迁移；配置不覆盖，数据目录因插件 ID 不变而无需搬动。"""
    builtin = builtin_plugin_root() / "qq_official_adapter"
    if not (builtin / "_manifest.json").is_file():
        return None
    root = third_party_plugin_root()
    _migrate_legacy_data(root)
    candidates = []
    for directory in sorted(root.iterdir()):
        if directory.name.startswith(".") or not directory.is_dir():
            continue
        manifest = directory / "_manifest.json"
        if not manifest.is_file():
            continue
        # 不让无关插件的无效清单阻止启动；真正迁移的目录必须严格校验边界。
        try:
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(metadata, dict) and str(metadata.get("id", "")).casefold() == PLUGIN_ID.casefold():
            if directory.is_symlink() or directory.resolve().parent != root or manifest.is_symlink():
                raise ValueError(f"QQ 官方适配器迁移目录包含链接或越界: {directory}")
            candidates.append(directory)
    if not candidates:
        return None
    if len(candidates) != 1:
        raise ValueError(f"QQ 官方适配器存在多个旧安装，请先处理重复目录: {candidates}")

    source = candidates[0]
    old_config = source / "config.toml"
    config = resolve_plugin_config_path(builtin)
    if old_config.is_symlink():
        raise ValueError(f"QQ 官方适配器配置不能是符号链接: {old_config}")
    if old_config.is_file() and not config.exists():
        # 先验证并原子复制，失败时保留旧安装；已存在的内置配置始终优先。
        with old_config.open("rb") as handle:
            tomllib.load(handle)
        config.parent.mkdir(parents=True, exist_ok=True)
        temporary = config.with_name(f".migration-{uuid4().hex}.toml")
        try:
            shutil.copy2(old_config, temporary)
            temporary.replace(config)
        finally:
            temporary.unlink(missing_ok=True)

    # 隐藏目录不参与插件扫描；完整保留旧安装、配置备份及可能的历史数据。
    backup = _backup_directory(root, source, "qq_official_adapter")
    logger.info(f"QQ 官方适配器已迁为内置插件，继续使用原插件数据；旧安装保留于 {backup}")
    return backup
