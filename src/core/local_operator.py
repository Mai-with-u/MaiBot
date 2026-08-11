"""本地操作员身份与插件管理权限判定。"""

from typing import Any, Dict, Iterable, Mapping, Set

BOT_CONSOLE_PLATFORM = "bot_console"
BOT_CONSOLE_USER_ID = "local_operator"
BOT_CONSOLE_BOT_ID = "__maibot_console_bot__"
MAISAKA_CLI_PLATFORM = "maisaka_cli"
MAISAKA_CLI_BOT_ID = "__maisaka_cli_bot__"
LOCAL_OPERATOR_CONFIG_KEY = "is_local_operator"
LOCAL_PLATFORM_BOT_IDS: Dict[str, str] = {
    BOT_CONSOLE_PLATFORM: BOT_CONSOLE_BOT_ID,
    MAISAKA_CLI_PLATFORM: MAISAKA_CLI_BOT_ID,
}


def is_local_operator(platform: str, additional_config: Dict[str, Any]) -> bool:
    """判断一条消息是否来自主程序创建的本地操作员终端。"""

    return platform == BOT_CONSOLE_PLATFORM and additional_config.get(LOCAL_OPERATOR_CONFIG_KEY) is True


def build_scoped_user_id(platform: str, user_id: str) -> str:
    """构造插件管理权限使用的跨平台用户 ID。"""

    normalized_platform = platform.strip().lower()
    normalized_user_id = user_id.strip()
    if not normalized_platform or not normalized_user_id:
        return ""
    return f"{normalized_platform}:{normalized_user_id}"


def normalize_operator_permissions(permission_list: Iterable[str]) -> Set[str]:
    """规范化插件管理权限列表。"""

    return {
        permission.strip().lower()
        for permission in permission_list
        if permission.strip()
    }


def has_plugin_management_permission(
    platform: str,
    user_id: str,
    permission_list: Iterable[str],
    *,
    local_operator: bool,
) -> bool:
    """判断用户是否具有插件生命周期管理权限。"""

    if local_operator:
        return True
    scoped_user_id = build_scoped_user_id(platform, user_id)
    return bool(scoped_user_id) and scoped_user_id in normalize_operator_permissions(permission_list)


def has_command_permission(
    command_id: str,
    platform: str,
    user_id: str,
    session_id: str,
    operator_permissions: Iterable[str],
    command_permissions: Mapping[str, Any],
    *,
    local_operator: bool,
) -> bool:
    """判断用户或聊天是否被允许执行受保护命令。"""

    if has_plugin_management_permission(
        platform,
        user_id,
        operator_permissions,
        local_operator=local_operator,
    ):
        return True

    rule = command_permissions.get(command_id)
    if rule is None:
        return False

    if isinstance(rule, Mapping):
        rule_users = rule.get("allow_users", [])
        rule_chats = rule.get("allow_chats", [])
    else:
        rule_users = rule.allow_users
        rule_chats = rule.allow_chats
    allowed_users = normalize_operator_permissions(rule_users)
    allowed_chats = {chat_id.strip() for chat_id in rule_chats if chat_id.strip()}
    scoped_user_id = build_scoped_user_id(platform, user_id)
    return scoped_user_id in allowed_users or session_id in allowed_chats
