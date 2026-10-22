"""无需导入插件代码即可读取宿主保留的启用开关。"""

from pathlib import Path

import tomllib

from pydantic import TypeAdapter, ValidationError

from src.common.logger import get_logger

logger = get_logger("plugin_runtime.plugin_enabled")
_ENABLED_VALIDATOR = TypeAdapter(bool)


def is_plugin_disabled(plugin_dir: Path) -> bool:
    """只跳过明确禁用的插件；配置错误交由 Runner 原有配置管理流程报告。"""
    config_path = plugin_dir / "config.toml"
    try:
        with config_path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as exc:
        logger.error(f"读取插件配置失败 {config_path}: {exc}")
        return False

    section = config.get("plugin")
    if not isinstance(section, dict) or "enabled" not in section:
        return False
    try:
        return not _ENABLED_VALIDATOR.validate_python(section["enabled"])
    except ValidationError as exc:
        logger.error(f"插件配置 plugin.enabled 无效 {config_path}: {exc}")
        return False
