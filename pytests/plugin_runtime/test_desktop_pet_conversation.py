from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.desktop_pet.conversation import describe_conversation, require_current_conversation, route_relay_message


def test_selected_group_preserves_route_without_impersonating_a_member():
    session = SimpleNamespace(
        session_id="group-route",
        platform="qq",
        group_id="123456",
        group_name="麦麦群",
        account_id="98765",
        scope="primary",
        user_id="ordinary-member",
    )
    packet = {
        "message_info": {
            "user_info": {"user_id": "desktop-owner", "user_nickname": "主人"},
            "additional_config": {"at_bot": True},
        }
    }
    route_relay_message(packet, session)
    assert packet["account_id"] == "98765"
    assert packet["scope"] == "primary"
    assert packet["message_info"]["group_info"] == {"group_id": "123456", "group_name": "麦麦群", "platform": "qq"}
    assert packet["message_info"]["user_info"]["user_id"] == "desktop-owner"
    assert describe_conversation(session)["target_id"] == "123456"


def test_private_chat_uses_existing_target_without_assigning_the_targets_identity():
    session = SimpleNamespace(
        session_id="private-route",
        platform="qq",
        group_id=None,
        user_id="11112222",
        user_nickname="麦麦朋友",
        user_cardname=None,
        account_id="98765",
        scope=None,
    )
    packet = {"message_info": {"user_info": {"user_id": "desktop-owner", "user_nickname": "主人"}}}
    route_relay_message(packet, session)
    assert "group_info" not in packet["message_info"]
    assert packet["message_info"]["user_info"]["user_id"] != session.user_id
    assert describe_conversation(session)["target_id"] == "11112222"


def test_stale_and_legacy_clients_cannot_send_to_the_new_target():
    require_current_conversation("", None)
    require_current_conversation("", "")
    require_current_conversation("existing", "existing")
    for expected in (None, "", "former"):
        with pytest.raises(ValueError, match="会话"):
            require_current_conversation("existing", expected)
    with pytest.raises(ValueError, match="会话"):
        require_current_conversation("new-binding", "")
    with pytest.raises(ValueError, match="会话"):
        require_current_conversation("", "former")


def test_register_bound_private_message_keeps_existing_route_and_identity(monkeypatch):
    from src.chat.message_receive.chat_manager import BotChatSession, ChatManager
    from src.common.utils.utils_session import SessionUtils

    manager = ChatManager()
    save_session = Mock()
    monkeypatch.setattr(manager, "_save_session", save_session)
    session_id = SessionUtils.calculate_session_id("qq", user_id="12345", account_id="98765")
    session = BotChatSession(session_id, "qq", user_id="12345", user_nickname="联系人", account_id="98765")
    manager.sessions[session.session_id] = session
    message = SimpleNamespace(
        session_id="",
        message_info=SimpleNamespace(
            group_info=None,
            user_info=SimpleNamespace(user_id="owner", user_nickname="桌宠 · 主人", user_cardname="桌宠 · 主人"),
        ),
    )
    manager.register_bound_message(session, message)

    assert message.session_id == session_id
    assert list(manager.sessions) == [session_id]
    assert manager.last_messages[session_id] is message
    assert session.user_nickname == "联系人"
    assert session.user_id == "12345"

    restored = manager._get_or_create_session("qq", "12345", account_id="98765")
    assert restored is session
    assert session.user_nickname == "联系人"
    assert session.user_cardname is None
    save_session.assert_not_called()


def test_real_platform_message_still_updates_private_contact_identity(monkeypatch):
    from src.chat.message_receive.chat_manager import BotChatSession, ChatManager
    from src.common.utils.utils_session import SessionUtils

    manager = ChatManager()
    monkeypatch.setattr(manager, "_save_session", Mock())
    session_id = SessionUtils.calculate_session_id("qq", user_id="12345", account_id="98765")
    session = BotChatSession(session_id, "qq", user_id="12345", user_nickname="旧昵称", account_id="98765")
    manager.sessions[session.session_id] = session
    message = SimpleNamespace(
        platform="qq",
        account_id="98765",
        scope=None,
        session_id="",
        message_info=SimpleNamespace(
            group_info=None,
            user_info=SimpleNamespace(user_id="12345", user_nickname="新昵称", user_cardname="新名片"),
        ),
    )

    manager.register_message(message)

    assert session.user_nickname == "新昵称"
    assert session.user_cardname == "新名片"


@pytest.mark.asyncio
async def test_relay_uses_the_exact_selected_session_and_never_broadcasts_to_owner_chat(monkeypatch):
    from src.desktop_pet import conversation
    from src.webui.routers.chat import service

    bound = SimpleNamespace(
        session_id="private-with-scope",
        platform="qq",
        group_id=None,
        user_id="12345",
        account_id="98765",
        scope="adapter-a",
    )
    monkeypatch.setattr(
        conversation, "resolve_conversation", lambda value: bound if value == bound.session_id else None
    )
    monkeypatch.setattr(service, "get_internal_chat_user_name", lambda: "主人")
    process = AsyncMock()
    broadcast = AsyncMock()
    monkeypatch.setattr(service.chat_bot, "message_process", process)
    monkeypatch.setattr(service.chat_manager, "broadcast_to_group", broadcast)
    message_id = await service.relay_plugin_chat_message(
        "builtin.desktop_pet", "测试", conversation_id=bound.session_id
    )
    process.assert_awaited_once()
    assert process.await_args.kwargs["bound_session"] is bound
    message = process.await_args.args[0]
    assert message["message_info"]["message_id"] == message_id
    assert message["message_info"]["user_info"]["user_id"] == "owner"
    assert message["message_info"]["additional_config"]["relayed_by_plugin"] == "builtin.desktop_pet"
    assert message["scope"] == "adapter-a"
    broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_bound_relay_rechecks_the_binding_after_message_processing(monkeypatch):
    from src.chat.message_receive import bot as bot_module
    from src.webui.routers.chat.service import create_message_data

    bound = SimpleNamespace(session_id="session-a", platform="qq", group_id=None, account_id="98765", scope=None)
    config = bot_module.global_config.desktop_pet
    monkeypatch.setattr(config, "conversation_session_id", bound.session_id)
    bot = bot_module.ChatBot()
    register = Mock(side_effect=AssertionError("stale message reached registration"))
    monkeypatch.setattr(bot_module.chat_manager, "register_bound_message", register)

    async def switch_after_process(hook, message):
        if hook == "chat.receive.after_process":
            config.conversation_session_id = "session-b"
        return SimpleNamespace(aborted=False), message

    monkeypatch.setattr(bot, "_invoke_message_hook", switch_after_process)
    packet = create_message_data("测试", "owner", "主人")
    route_relay_message(packet, bound)
    with pytest.raises(ValueError, match="会话已切换"):
        await bot.message_process(packet, bound_session=bound)
    register.assert_not_called()
