from unittest.mock import AsyncMock, MagicMock

import pytest

from src.chat.message_receive.bot import chat_bot
from src.core.local_operator import has_command_permission, has_plugin_management_permission
from src.plugin_runtime.capabilities import internal_chat
from src.plugin_runtime.host.authorization import AuthorizationManager
from src.plugin_runtime.host.capability_service import CapabilityService
from src.plugin_runtime.protocol.envelope import Envelope, MessageType
from src.services import internal_chat_profile
from src.webui.routers.chat import routes as chat_routes
from src.webui.routers.chat import service as chat_service


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(internal_chat_profile, "PROFILE_PATH", tmp_path / "profile.json")


def _request(capability, args):
    return Envelope(
        request_id=1, message_type=MessageType.REQUEST, plugin_id="author.pet", method="cap.call",
        payload={"capability": capability, "args": args},
    )


def _capabilities(granted):
    authorization = AuthorizationManager()
    authorization.register_plugin("author.pet", granted)
    capabilities = CapabilityService(authorization)
    capabilities.register_capability("internal_chat.identity", internal_chat.identity)
    capabilities.register_capability("internal_chat.send", internal_chat.send)
    return capabilities


@pytest.mark.asyncio
async def test_identity_names_the_single_owner_stream():
    result = await internal_chat.identity("author.pet", "internal_chat.identity", {})
    assert result["result"] == {"platform": "internal", "user_id": "owner"}


@pytest.mark.asyncio
async def test_send_relays_as_owner_marks_plugin_and_syncs_web_chat(monkeypatch):
    process = AsyncMock()
    broadcast = AsyncMock()
    monkeypatch.setattr(chat_bot, "message_process", process)
    monkeypatch.setattr(chat_service.chat_manager, "broadcast_to_group", broadcast)

    result = await internal_chat.send("author.pet", "internal_chat.send", {"text": "  你好  "})

    message_data = process.await_args.args[0]
    info = message_data["message_info"]
    assert result["result"]["message_id"] == info["message_id"]
    assert info["platform"] == "internal" and "group_info" not in info
    assert info["user_info"]["user_id"] == "owner"
    assert info["user_info"]["user_nickname"] == "人类"
    assert info["additional_config"]["relayed_by_plugin"] == "author.pet"
    assert message_data["message_segment"]["data"] == [{"type": "text", "data": "你好"}]
    echo = broadcast.await_args.args[1]
    assert broadcast.await_args.kwargs["user_id"] == "owner"
    assert echo["type"] == "user_message" and echo["origin"] == "plugin:author.pet"
    assert echo["message_id"] == info["message_id"] and echo["content"] == "你好"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"text": "   ", "user_name": "主人"},
        {"text": "hi", "user_name": ""},
        {"text": "hi", "user_name": "主人", "user_id": "someone"},
    ],
)
async def test_send_rejects_blank_text_and_caller_chosen_identity(monkeypatch, args):
    process = AsyncMock()
    monkeypatch.setattr(chat_bot, "message_process", process)

    response = await _capabilities(["internal_chat.send"]).handle_capability_request(_request("internal_chat.send", args))

    assert response.error is not None
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_requires_declared_capability(monkeypatch):
    process = AsyncMock()
    monkeypatch.setattr(chat_bot, "message_process", process)

    denied = await _capabilities([]).handle_capability_request(
        _request("internal_chat.send", {"text": "hi"})
    )

    assert denied.error["code"] == "E_CAPABILITY_DENIED"
    process.assert_not_awaited()


def test_plugin_relayed_messages_never_get_protected_permissions():
    operators = ["internal:owner"]
    rules = {"core.clear": {"allow_users": ["internal:owner"], "allow_chats": ["s1"]}}
    assert has_command_permission("core.clear", "internal", "owner", "s1", operators, rules, local_operator=False)
    assert not has_command_permission(
        "core.clear", "internal", "owner", "s1", operators, rules, local_operator=False, relayed_by_plugin=True
    )
    assert has_plugin_management_permission("internal", "owner", operators, local_operator=False)
    assert not has_plugin_management_permission(
        "internal", "owner", operators, local_operator=False, relayed_by_plugin=True
    )


def test_web_chat_history_reads_the_owner_stream(monkeypatch):
    get_history = MagicMock(return_value=[])
    monkeypatch.setattr(chat_routes.chat_history, "get_history", get_history)

    chat_routes.get_chat_history(limit=20, group_id=None)

    get_history.assert_called_once_with(20, user_id="owner")


def test_shared_name_keeps_first_browser_name_and_rejects_stale_browser_values():
    assert internal_chat_profile.get_internal_chat_user_name() == "人类"
    assert not internal_chat_profile.PROFILE_PATH.exists()
    assert internal_chat_profile.get_internal_chat_user_name("小明") == "小明"
    assert internal_chat_profile.get_internal_chat_user_name("旧浏览器昵称") == "小明"
    assert internal_chat_profile.set_internal_chat_user_name("  新称呼  ") == "新称呼"
    assert internal_chat_profile.get_internal_chat_user_name() == "新称呼"


def test_corrupt_shared_name_is_preserved_and_chat_can_open():
    path = internal_chat_profile.PROFILE_PATH
    path.write_bytes(bytes(32))

    assert internal_chat_profile.get_internal_chat_user_name("新称呼") == "新称呼"
    assert internal_chat_profile.get_internal_chat_user_name() == "新称呼"
    backup, = path.parent.glob(f"{path.name}.corrupt-*")
    assert backup.read_bytes() == bytes(32)


@pytest.mark.asyncio
async def test_web_rename_reaches_other_windows_and_pet_without_changing_virtual_identity(monkeypatch):
    manager = chat_service.ChatConnectionManager()
    monkeypatch.setattr(chat_service, "chat_manager", manager)
    process = AsyncMock()
    monkeypatch.setattr(chat_bot, "message_process", process)
    senders = [AsyncMock(), AsyncMock(), AsyncMock()]
    for index, sender in enumerate(senders):
        virtual = chat_service.VirtualIdentityConfig(enabled=True, platform="qq", user_id="123", group_id="g") if index == 2 else None
        await manager.connect(str(index), str(index), "tab", "owner", "旧名字", virtual, None, sender)
    await chat_service.handle_nickname_update("0", {"user_name": "统一称呼"}, "旧名字")
    assert manager.get_session("1").user_name == "统一称呼"
    assert manager.get_session("2").user_name == "旧名字"
    assert senders[1].await_args.args[0]["user_name"] == "统一称呼"
    senders[2].assert_not_awaited()

    await internal_chat.send("author.pet", "internal_chat.send", {"text": "桌宠发言"})
    assert process.await_args.args[0]["message_info"]["user_info"]["user_nickname"] == "统一称呼"
    # 旧标签页发送携带过期昵称时不能改回共享昵称。
    await chat_service.handle_chat_message("1", {"content": "网页发言", "user_name": "过期昵称"}, "旧名字", "owner", None)
    assert process.await_args.args[0]["message_info"]["user_info"]["user_nickname"] == "统一称呼"


@pytest.mark.asyncio
async def test_desktop_image_and_text_share_message(monkeypatch):
    process, broadcast = AsyncMock(), AsyncMock()
    monkeypatch.setattr(chat_bot, "message_process", process)
    monkeypatch.setattr(chat_service.chat_manager, "broadcast_to_group", broadcast)
    images = [{"base64": "aW1hZ2U=", "mime_type": "image/png", "name": "clipboard.png"}]
    await chat_service.relay_plugin_chat_message("builtin.desktop_pet", "看这张", images=images)
    data = process.await_args.args[0]
    assert data["message_segment"]["data"] == [
        {"type": "text", "data": "看这张"}, {"type": "image", "data": "aW1hZ2U="},
    ]
    assert broadcast.await_args.args[1]["images"] == images
