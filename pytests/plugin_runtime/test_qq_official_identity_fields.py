from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, Mock

import pytest

from src.plugins.built_in.qq_official_adapter.codec import decode_message
from src.plugins.built_in.qq_official_adapter.plugin import QQOfficialAdapterPlugin
from src.plugins.built_in.qq_official_adapter.settings import AdapterSettings


def payload(group: bool = True) -> Dict[str, Any]:
    return {
        "id": "message-id",
        "timestamp": "2026-11-16T20:00:00+08:00",
        "content": "你好",
        "group_openid": "group-openid",
        "union_group_openid": "union-group",
        "author": {
            "id": "user-openid",
            "member_openid" if group else "user_openid": "user-openid",
            "username": "官方昵称",
            "avatar": "https://thirdqq.qlogo.cn/avatar",
            "bot": False,
            "union_openid": "union-user",
            **({"member_role": "admin", "member_nickname": "群内昵称"} if group else {}),
        },
    }


@pytest.mark.parametrize("event", ["GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"])
def test_identity_fields_survive_id_mapping(event: str) -> None:
    group = event != "C2C_MESSAGE_CREATE"
    data = payload(group)
    message, scene, target = decode_message(
        event, data, "bot",
        id_map={"group": {"group-openid": "123"}, "user": {"user-openid": "456"}},
    )
    info = message["message_info"]
    assert info["user_info"]["user_id"] == "456"
    assert info["user_info"]["user_nickname"] == "官方昵称"
    assert scene == ("group" if group else "private")
    assert target == ("123" if group else "456")
    config = info["additional_config"]
    assert config["qq_official_user_openid"] == "user-openid"
    for field, value in data["author"].items():
        if field not in {"user_openid", "member_openid"}:
            assert config[f"qq_official_author_{field}"] == value
    if group:
        assert info["user_info"]["user_cardname"] == "群内昵称"
        assert config["qq_official_union_group_openid"] == "union-group"
    else:
        assert "user_cardname" not in info["user_info"]
        assert "qq_official_union_group_openid" not in config


def test_missing_and_explicitly_empty_identity_fields_are_distinct() -> None:
    data = payload()
    data["author"] = {"member_openid": "user-openid", "union_openid": "union-user"}
    del data["union_group_openid"]
    message, _, _ = decode_message("GROUP_MESSAGE_CREATE", data, "bot")
    info = message["message_info"]
    assert "user_cardname" not in info["user_info"]
    assert info["additional_config"]["qq_official_author_union_openid"] == "union-user"
    assert "qq_official_union_group_openid" not in info["additional_config"]

    data["author"].update(member_nickname="", bot=False)
    message, _, _ = decode_message("GROUP_MESSAGE_CREATE", data, "bot")
    info = message["message_info"]
    assert info["user_info"]["user_cardname"] is None
    assert info["additional_config"]["qq_official_author_union_openid"] == "union-user"
    assert info["additional_config"]["qq_official_author_bot"] is False


def test_mentions_use_group_card_and_preserve_platform_fields() -> None:
    data = payload()
    data["content"] = "你好 <@other>"
    data["mentions"] = [{
        "id": "other", "member_openid": "other", "username": "昵称",
        "member_nickname": "名片", "member_role": "owner", "bot": False,
        "is_you": False, "scope": "single",
    }]
    message, _, _ = decode_message(
        "GROUP_MESSAGE_CREATE", data, "bot", id_map={"user": {"other": "789"}}, union_user_map={"other": "union-other"},
    )
    mention = next(segment["data"] for segment in message["raw_message"] if segment["type"] == "at")
    assert mention == {
        "target_user_id": "union-other", "target_user_nickname": "昵称", "target_user_cardname": "名片",
    }
    assert "@名片" in message["processed_plain_text"]
    assert message["message_info"]["additional_config"]["qq_official_mentions"] == data["mentions"]


@pytest.mark.asyncio
async def test_message_avatar_is_available_under_openid_and_mapped_id(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = QQOfficialAdapterPlugin()
    adapter._plugin_config_instance = AdapterSettings()
    adapter.config.qq_official.app_id = "app-1"
    message, _, _ = decode_message(
        "GROUP_MESSAGE_CREATE", payload(), "bot", id_map={"user": {"user-openid": "456"}},
    )
    monkeypatch.setattr("src.plugins.built_in.qq_official_adapter.plugin.time.time", lambda: 100.0)
    adapter._remember_user_avatar(message)
    for target in ("user-openid", "456"):
        result = await adapter.api_get_avatar("qq", target)
        assert result["url"] == "https://thirdqq.qlogo.cn/avatar"
        assert result["expires_in"] == 86400
    adapter.config.qq_official.app_id = "app-2"
    assert (await adapter.api_get_avatar("qq", "user-openid"))["url"] != "https://thirdqq.qlogo.cn/avatar"
    adapter.config.qq_official.app_id = "app-1"
    monkeypatch.setattr("src.plugins.built_in.qq_official_adapter.plugin.time.time", lambda: 86501.0)
    assert (await adapter.api_get_avatar("qq", "user-openid"))["url"] != "https://thirdqq.qlogo.cn/avatar"


@pytest.mark.asyncio
@pytest.mark.parametrize("official_fields", ["present", "empty_card", "missing"])
async def test_official_names_take_priority_over_silent_profiles(official_fields: str, tmp_path: Path) -> None:
    adapter = QQOfficialAdapterPlugin()
    adapter._plugin_config_instance = AdapterSettings()
    adapter.config.qq_official.silent_user_sync = True
    adapter.config.qq_official.unified_account_id = "999"
    adapter._id_map = {"group": {"group-openid": "123"}, "user": {"user-openid": "456"}}
    adapter._identity_sync = Mock(profiles={}, candidates={})
    adapter._identity_sync.get_profile.return_value = SimpleNamespace(
        user_id="456", nickname="旧昵称", cardname="旧名片",
    )
    adapter._build_attachments = AsyncMock(return_value=([], [], False, False))
    adapter._get_group_name = AsyncMock(return_value="群名")
    gateway = SimpleNamespace(route_message=AsyncMock(return_value=True))
    adapter._set_context(SimpleNamespace(logger=Mock(), gateway=gateway, paths=SimpleNamespace(data_dir=tmp_path)))
    adapter.config.qq_official.app_id = "app"
    adapter._ready = True
    adapter._inbound_ready.set()
    data = payload()
    if official_fields == "missing":
        del data["author"]["username"]
        del data["author"]["member_nickname"]
    elif official_fields == "empty_card":
        data["author"]["member_nickname"] = ""
    await adapter._receive_message("GROUP_MESSAGE_CREATE", data, account_id="bot", bot_name="麦麦")
    info = gateway.route_message.call_args.kwargs["message"]["message_info"]
    assert info["user_info"]["user_nickname"] == ("旧昵称" if official_fields == "missing" else "官方昵称")
    expected_card = {"missing": "旧名片", "present": "群内昵称", "empty_card": None}[official_fields]
    assert info["user_info"]["user_cardname"] == expected_card
