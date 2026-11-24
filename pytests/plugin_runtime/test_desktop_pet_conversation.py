from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    require_current_conversation("existing", "existing")
    for expected in (None, "", "former"):
        with pytest.raises(ValueError, match="会话"):
            require_current_conversation("existing", expected)
    with pytest.raises(ValueError, match="会话"):
        require_current_conversation("", "former")


def test_register_bound_private_message_keeps_existing_route_and_identity():
    from src.chat.message_receive.chat_manager import BotChatSession, ChatManager

    manager = ChatManager()
    session = BotChatSession("existing-id", "qq", user_id="12345", user_nickname="联系人", account_id="98765")
    manager.sessions[session.session_id] = session
    message = SimpleNamespace(session_id="", message_info=SimpleNamespace(user_info=SimpleNamespace(user_id="owner")))
    manager.register_bound_message(session, message)

    assert message.session_id == "existing-id"
    assert list(manager.sessions) == ["existing-id"]
    assert manager.last_messages["existing-id"] is message
    assert session.user_nickname == "联系人"
    assert session.user_id == "12345"


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
