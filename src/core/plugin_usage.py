"""核心统一插件使用鉴权；身份必须来自核心上下文，不能取自模型工具参数。"""

from typing import Mapping

from src.config.official_configs import PluginUsagePermissionConfig

from .local_operator import build_scoped_user_id, normalize_operator_permissions


def has_plugin_usage_permission(
    plugin_id: str,
    permissions: Mapping[str, PluginUsagePermissionConfig],
    *,
    platform: str = "",
    user_id: str = "",
    session_id: str = "",
) -> bool:
    """未限制则放行；限制后用户或会话任意命中即可，空白名单拒绝所有调用。"""

    rule = permissions.get(plugin_id)
    if rule is None or not rule.enabled:
        return True
    if session_id and session_id in rule.allow_chats:
        return True
    scoped_user = build_scoped_user_id(platform, user_id)
    return bool(scoped_user) and scoped_user in normalize_operator_permissions(rule.allow_users)


def can_use_plugin(plugin_id: str, *, platform: str = "", user_id: str = "", session_id: str = "") -> bool:
    """每次调用读取当前核心配置，使保存后的权限更新也约束已取得的执行器。"""

    from src.config.config import global_config

    return has_plugin_usage_permission(
        plugin_id, global_config.plugin.usage_permissions,
        platform=platform, user_id=user_id, session_id=session_id,
    )
