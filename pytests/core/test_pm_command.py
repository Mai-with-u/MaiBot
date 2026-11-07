from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.chat.message_receive import bot
from src.core.commands import plugin_management, pm_aliases


@pytest.mark.asyncio
async def test_pm_matches_instance_alias_and_obeys_command_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(tmp_path))
    monkeypatch.setattr(
        plugin_management,
        "global_config",
        SimpleNamespace(
            plugin=SimpleNamespace(disabled_commands=[]),
        ),
    )
    path = tmp_path / "config" / "commands" / "pm.toml"
    path.parent.mkdir(parents=True)
    path.write_text('[aliases.shortcuts]\n"/插件.列表" = "/pm plugin list"\n', encoding="utf-8")

    assert await plugin_management.match_pm_command("/插件.列表") == "/pm plugin list"
    assert await plugin_management.match_pm_command("/插件管理 help") == "/pm help"
    assert pm_aliases.update_shortcut("/插件.列表", None)
    assert await plugin_management.match_pm_command("/插件.列表") is None

    plugin_management.global_config.plugin.disabled_commands.append(pm_aliases.PM_COMMAND_ID)
    assert await plugin_management.match_pm_command("/pm help") is None
    assert await plugin_management.match_pm_command("/插件管理 help") is None


@pytest.mark.asyncio
async def test_pm_dispatch_checks_permission_without_registered_plugin(monkeypatch):
    handler = object.__new__(bot.ChatBot)
    message = SimpleNamespace(
        processed_plain_text="/pm help",
        platform="qq",
        session_id="test-session",
        message_info=SimpleNamespace(additional_config={}, user_info=SimpleNamespace(user_id="123")),
    )
    config = SimpleNamespace(
        plugin=SimpleNamespace(
            disabled_commands=[],
            permission=[],
            command_permissions={},
            silent_permission_denied=True,
            command_require_self_mention=False,
        )
    )
    monkeypatch.setattr(bot, "global_config", config)
    monkeypatch.setattr(bot, "can_use_plugin", lambda *args, **kwargs: pytest.fail("检查了插件使用权限"))
    monkeypatch.setattr(bot.global_announcement_manager, "get_disabled_chat_commands", lambda _: [])
    monkeypatch.setattr(bot.component_query_service, "find_command_by_text", lambda _: pytest.fail("查询了插件注册表"))
    hook = SimpleNamespace(aborted=False, kwargs={})
    monkeypatch.setattr(handler, "_invoke_message_hook", AsyncMock(return_value=(hook, message)))
    send = AsyncMock()
    monkeypatch.setattr(plugin_management, "text_to_stream", send)

    assert await handler._process_commands(message, "/pm help") == (True, "没有权限", False)
    send.assert_not_awaited()

    config.plugin.command_permissions[pm_aliases.PM_COMMAND_ID] = {"allow_users": ["qq:123"]}
    assert await handler._process_commands(message, "/pm help") == (True, "帮助已发送", False)
    send.assert_awaited_once_with(plugin_management.HELP_ALL, "test-session")
