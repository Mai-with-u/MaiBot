from src.config.official_configs import CommandPermissionConfig
from src.core.local_operator import has_command_permission


def test_global_operator_can_use_protected_command() -> None:
    assert has_command_permission(
        "core.clear",
        "qq",
        "10001",
        "group-session",
        ["qq:10001"],
        {},
        local_operator=False,
    )


def test_command_can_allow_specific_user_or_chat() -> None:
    rules = {
        "core.clear": CommandPermissionConfig(
            allow_users=["qq:10002"],
            allow_chats=["allowed-session"],
        )
    }

    assert has_command_permission(
        "core.clear", "qq", "10002", "other-session", [], rules, local_operator=False
    )
    assert has_command_permission(
        "core.clear", "qq", "other-user", "allowed-session", [], rules, local_operator=False
    )
    assert not has_command_permission(
        "core.clear", "qq", "other-user", "other-session", [], rules, local_operator=False
    )


def test_local_operator_is_always_allowed() -> None:
    assert has_command_permission(
        "core.clear", "bot_console", "local_operator", "console", [], {}, local_operator=True
    )
