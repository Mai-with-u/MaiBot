from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.chat.heart_flow.heartflow_manager import ChatPausedError, HeartflowManager
from src.chat.message_receive import bot
from src.chat.message_receive.chat_manager import BotChatSession, chat_manager
from src.core.chat_pause import ChatPauseState
from src.services import send_service


def test_type_rules_and_session_overrides():
    state = ChatPauseState()
    state.set_type(True, True)
    assert state.is_paused("future-group", True)
    assert not state.is_paused("private", False)
    state.set_session("group", True, False)
    assert not state.is_paused("group", True)
    state.set_type(True, True)
    assert state.is_paused("group", True)
    state.set_session("private", False, True)
    state.set_type(True, False)
    assert not state.is_paused("group", True)
    assert state.is_paused("private", False)
    state.set_type(False, False)
    assert not state.is_paused("private", False)


@pytest.fixture
def commands(monkeypatch):
    from src.chat.heart_flow import heartflow_manager as manager_module

    state = ChatPauseState()
    monkeypatch.setattr(bot, "chat_pause_state", state)
    monkeypatch.setattr(manager_module, "chat_pause_state", state)
    sessions = {
        "group-id": BotChatSession("group-id", "qq", group_id="123", group_name="测试群"),
        "private-id": BotChatSession("private-id", "qq", user_id="456", user_nickname="测试用户"),
    }
    monkeypatch.setattr(chat_manager, "sessions", sessions)
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(send_service, "text_to_stream", send)
    monkeypatch.setattr(bot, "has_plugin_management_permission", lambda *args, **kwargs: True)
    stop = AsyncMock()
    monkeypatch.setattr(bot.heartflow_manager, "stop_paused_chats", stop)
    handler = object.__new__(bot.ChatBot)
    monkeypatch.setattr(handler, "_mark_command_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(handler, "_store_intercepted_command_message", AsyncMock())
    message = SimpleNamespace(
        processed_plain_text="", platform="qq", session_id="private-id",
        message_info=SimpleNamespace(additional_config={}, user_info=SimpleNamespace(user_id="456")),
    )
    return handler, message, state, send, stop


@pytest.mark.asyncio
async def test_private_commands_pause_group_and_restore_one(commands):
    handler, message, state, send, stop = commands
    for text in ["/offline group", "/online group-id", "/offline private", "/online private-id"]:
        message.processed_plain_text = text
        assert handler._is_command_candidate(message)
        assert await handler._process_adapter_lifecycle_command(message)
    assert state.is_paused("new-group", True)
    assert not state.is_paused("group-id", True)
    assert state.is_paused("new-private", False)
    assert not state.is_paused("private-id", False)
    assert stop.await_count == 2
    assert send.await_count == 4


@pytest.mark.asyncio
async def test_invalid_id_and_syntax_do_not_pause(commands):
    handler, message, state, send, stop = commands
    for text in ["/offline 123", "/offline group extra"]:
        message.processed_plain_text = text
        assert await handler._process_adapter_lifecycle_command(message)
    assert not state.is_paused("group-id", True)
    stop.assert_not_awaited()
    assert send.await_count == 2


@pytest.mark.asyncio
async def test_list_shows_names_ids_and_status(commands, monkeypatch):
    handler, message, state, send, _ = commands
    state.set_session("group-id", True, True)
    chat_manager.sessions["active-id"] = BotChatSession("active-id", "qq", group_id="789", group_name="活跃群")
    chat_manager.sessions["old-id"] = BotChatSession("old-id", "qq", group_id="987", group_name="历史群")
    monkeypatch.setattr(bot.heartflow_manager, "heartflow_chat_list", {"active-id": object(), "private-id": object()})
    message.processed_plain_text = "/list chat"
    assert handler._is_command_candidate(message)
    assert await handler._process_adapter_lifecycle_command(message)
    text = send.call_args.args[0]
    assert all(value in text for value in ["测试群", "活跃群", "group-id", "active-id", "已暂停"])
    assert all(value not in text for value in ["测试用户的私聊", "private-id", "历史群", "old-id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/offline group", "/offline private-id", "/online group", "/list chat"])
async def test_permission_denied(commands, monkeypatch, text):
    handler, message, state, send, stop = commands
    monkeypatch.setattr(bot, "has_plugin_management_permission", lambda *args, **kwargs: False)
    message.processed_plain_text = text
    assert await handler._process_adapter_lifecycle_command(message)
    assert not state.is_paused("group-id", True)
    stop.assert_not_awaited()
    assert not any("ID:" in call.args[0] for call in send.call_args_list)


@pytest.mark.asyncio
async def test_paused_runtime_cannot_be_created_and_existing_runtime_stops(commands, monkeypatch):
    from src.chat.heart_flow import heartflow_manager as manager_module

    _, _, state, _, _ = commands
    manager = HeartflowManager()
    chat = SimpleNamespace(chat_stream=chat_manager.sessions["group-id"], stop=AsyncMock())
    manager.heartflow_chat_list["group-id"] = chat
    state.set_type(True, True)
    with pytest.raises(ChatPausedError):
        await manager.get_or_create_heartflow_chat("group-id")
    monkeypatch.setattr(manager_module.focus_mode_manager, "clear_context", AsyncMock())
    await manager.stop_paused_chats()
    chat.stop.assert_awaited_once()
    assert "group-id" not in manager.heartflow_chat_list
    with pytest.raises(ChatPausedError):
        await manager.get_or_create_heartflow_chat("group-id")


@pytest.mark.asyncio
async def test_bare_offline_retains_adapter_behavior(commands, monkeypatch):
    handler, message, state, _, stop = commands
    result = SimpleNamespace(changed_plugin_ids=["adapter"], failed_plugins={}, pending_plugin_ids=[])
    runtime = SimpleNamespace(take_adapters_offline=AsyncMock(return_value=result))
    monkeypatch.setattr(handler, "_get_runtime_manager", lambda: runtime)
    message.processed_plain_text = "/offline"
    assert await handler._process_adapter_lifecycle_command(message)
    runtime.take_adapters_offline.assert_awaited_once()
    stop.assert_not_awaited()
    assert not state.is_paused("group-id", True)
