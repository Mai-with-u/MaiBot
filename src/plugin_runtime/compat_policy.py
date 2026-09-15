"""插件兼容策略。

集中处理「强制插件兼容」开关：Host 侧从主程序配置读取，Runner 侧从环境变量读取，
两侧共用同一套取值编码，避免各调用点自行解释。
"""

from typing import Optional

_ENV_ENABLED_VALUES = {"1", "true", "yes", "on"}


def is_force_plugin_compatibility_enabled() -> bool:
    """读取主程序配置中的「强制插件兼容」开关。

    Returns:
        bool: 开启时返回 ``True``，表示 Manifest 校验跳过 Host / SDK 版本范围。
    """

    from src.config.config import global_config

    return bool(global_config.debug.force_plugin_compatibility)


def build_force_plugin_compatibility_env(enabled: bool) -> str:
    """把开关编码为 Runner 环境变量取值。

    Args:
        enabled: 是否开启强制插件兼容。

    Returns:
        str: 开启时为 ``"1"``，否则为 ``"0"``。
    """

    return "1" if enabled else "0"


def parse_force_plugin_compatibility_env(raw_value: Optional[str]) -> bool:
    """解析 Runner 环境变量中的开关取值。

    Args:
        raw_value: 环境变量原始取值。

    Returns:
        bool: 取值表示开启时返回 ``True``。
    """

    return str(raw_value or "").strip().lower() in _ENV_ENABLED_VALUES
