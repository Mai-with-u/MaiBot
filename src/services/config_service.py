"""配置服务模块

提供配置读取的核心功能。
"""

from typing import Any

from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("config_service")


def get_global_config(key: str, default: Any = None) -> Any:
    """
    安全地从全局配置中获取一个值。

    Args:
        key: 命名空间式配置键名，使用嵌套访问，如 "section.subsection.key"，大小写敏感
        default: 如果配置不存在时返回的默认值

    Returns:
        Any: 配置值或默认值
    """
    keys = key.split(".")
    current = global_config

    try:
        for k in keys:
            if hasattr(current, k):
                current = getattr(current, k)
            else:
                raise KeyError(f"配置中不存在子空间或键 '{k}'")
        return current
    except Exception as e:
        logger.warning(f"[ConfigService] 获取全局配置 {key} 失败: {e}")
        return default


def get_plugin_config(plugin_config: dict, key: str, default: Any = None) -> Any:
    """
    从插件配置中获取值，支持嵌套键访问

    Args:
        plugin_config: 插件配置字典
        key: 配置键名，支持嵌套访问如 "section.subsection.key"，大小写敏感
        default: 如果配置不存在时返回的默认值

    Returns:
        Any: 配置值或默认值
    """
    keys = key.split(".")
    current = plugin_config

    try:
        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            elif hasattr(current, k):
                current = getattr(current, k)
            else:
                raise KeyError(f"配置中不存在子空间或键 '{k}'")
        return current
    except Exception as e:
        logger.warning(f"[ConfigService] 获取插件配置 {key} 失败: {e}")
        return default
