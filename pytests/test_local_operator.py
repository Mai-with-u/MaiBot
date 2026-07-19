from src.core.local_operator import (
    BOT_CONSOLE_PLATFORM,
    LOCAL_OPERATOR_CONFIG_KEY,
    has_plugin_management_permission,
    is_local_operator,
)


def test_local_console_operator_has_management_permission() -> None:
    local_operator = is_local_operator(
        BOT_CONSOLE_PLATFORM,
        {LOCAL_OPERATOR_CONFIG_KEY: True},
    )

    assert local_operator is True
    assert has_plugin_management_permission(
        BOT_CONSOLE_PLATFORM,
        "local_operator",
        [],
        local_operator=local_operator,
    ) is True


def test_chat_operator_requires_scoped_permission() -> None:
    permissions = ["qq:123456789"]

    assert has_plugin_management_permission(
        "QQ",
        "123456789",
        permissions,
        local_operator=False,
    ) is True
    assert has_plugin_management_permission(
        "qq",
        "987654321",
        permissions,
        local_operator=False,
    ) is False
