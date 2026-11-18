from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, Mock

import json
import time

import pytest

from src.plugins.built_in.qq_official_adapter.codec import decode_message
from src.plugins.built_in.qq_official_adapter.identity_sync import UserProfile, text_fingerprint
from src.plugins.built_in.qq_official_adapter.plugin import QQOfficialAdapterPlugin
from src.plugins.built_in.qq_official_adapter.settings import AdapterSettings
from src.plugins.built_in.qq_official_adapter.union_identity import UNION_IDENTITIES_FILE, UnionIdentities


@pytest.fixture
def adapter(tmp_path: Path) -> QQOfficialAdapterPlugin:
    plugin = QQOfficialAdapterPlugin()
    plugin._plugin_config_instance = AdapterSettings()
    plugin.config.qq_official.enable_mapping = True
    plugin.config.qq_official.app_id = "app"
    plugin.config.qq_official.unified_account_id = "999"
    plugin._unified_account_id = "999"
    plugin._account_id = "bot"
    plugin._id_map = {"group": {"group-a": "123", "group-b": "124"}, "user": {}}
    plugin._set_context(SimpleNamespace(
        logger=Mock(), paths=SimpleNamespace(data_dir=tmp_path),
        gateway=SimpleNamespace(route_message=AsyncMock(return_value=True)), api=SimpleNamespace(call=AsyncMock()),
    ))
    plugin._build_attachments = AsyncMock(return_value=([], [], False, False))
    plugin._get_group_name = AsyncMock(return_value="测试群")
    plugin._api_post = AsyncMock(return_value={"id": "sent"})
    plugin._claim_passive_fields = AsyncMock(return_value={"msg_id": "incoming", "msg_seq": 1})
    plugin._union_identities = UnionIdentities().updated("app", "group-a", "member-a", "union-user")
    plugin._ready = True
    plugin._inbound_ready.set()
    return plugin


def incoming(group: bool = True, **author: Any) -> Dict[str, Any]:
    return {"id": "incoming", "timestamp": "2026-11-16T20:00:00+08:00", "content": "这是一条足够长且有不同字符的正文",
            "group_openid": "group-a", "author": {
                "member_openid" if group else "user_openid": "member-a" if group else "private-openid",
                "union_openid": "union-user", "username": "官方昵称", **author,
            }}


@pytest.mark.asyncio
async def test_identity_persistence_and_scene_specific_routes(adapter: QQOfficialAdapterPlugin) -> None:
    adapter._id_map["user"]["member-a"] = "456"
    await adapter._receive_message("GROUP_MESSAGE_CREATE", incoming(), account_id="bot", bot_name="麦麦")
    info = adapter.ctx.gateway.route_message.call_args.kwargs["message"]["message_info"]
    assert info["user_info"]["user_id"] == "456"
    assert info["additional_config"]["qq_official_user_openid"] == "member-a"
    assert adapter._group_user_map("group-a") == {"member-a": "456"}
    assert adapter._resolve_member_openid("union-user", None, "group-a") == "member-a"
    assert adapter._resolve_member_openid("union-user", None, "group-b") == ""
    assert adapter._resolve_user_openid("union-user") == ""
    assert adapter._resolve_user_openid("member-a") == ""
    await adapter._receive_message("C2C_MESSAGE_CREATE", incoming(False), account_id="bot", bot_name="麦麦")
    assert adapter._resolve_user_openid("union-user") == "private-openid"
    assert adapter._resolve_user_openid("456") == "private-openid"
    assert adapter._resolve_user_openid("member-a") == "private-openid"
    assert adapter._resolve_member_openid("union-user", None, "group-a") == "member-a"
    assert adapter._resolve_member_openid("private-openid", None, "group-a") == "member-a"
    restored = UnionIdentities.load(adapter.ctx.paths.data_dir / UNION_IDENTITIES_FILE)
    assert restored.reverse_map("app", "group-a") == {"union-user": "member-a"}
    assert restored.reverse_map("app") == {"union-user": "private-openid"}
    assert {row["source"] for row in restored.relations()} == {"member-a", "private-openid", "456"}
    adapter.config.qq_official.app_id = "another-app"
    assert adapter._group_user_map("group-a") == {"member-a": "456"}


@pytest.mark.asyncio
async def test_private_reply_claims_union_window_and_sends_c2c_openid(adapter: QQOfficialAdapterPlugin) -> None:
    await adapter._record_union_identity("group-a", "member-a", "union-user")
    outbound = {"message_info": {"group_info": None, "additional_config": {"platform_io_target_user_id": "union-user"}},
                "raw_message": [{"type": "text", "data": "回复正文"}], "reply_to": "incoming"}
    assert (await adapter.handle_qq_official_gateway(outbound))["success"] is False
    adapter._api_post.assert_not_awaited()
    await adapter._record_union_identity("", "private-openid", "union-user")
    result = await adapter.handle_qq_official_gateway(outbound)
    assert result["success"] is True
    adapter._claim_passive_fields.assert_awaited_with("private", "private-openid", "incoming")
    assert adapter._api_post.call_args.args[0] == "/v2/users/private-openid/messages"


@pytest.mark.asyncio
async def test_group_mentions_and_management_keep_legacy_qq_protection(adapter: QQOfficialAdapterPlugin) -> None:
    adapter._id_map["user"]["member-a"] = "456"
    await adapter._record_union_identity("group-a", "member-a", "union-user")
    await adapter._record_union_identity("group-b", "member-b", "union-user")
    adapter.config.mute.admin_users = ["456"]
    assert (await adapter.api_set_group_ban("123", "union-user", 60))["success"] is False
    adapter._api_post.assert_not_awaited()
    adapter.config.mute.admin_users = []
    assert (await adapter.api_set_group_ban("124", "union-user", 60))["success"] is True
    assert adapter._api_post.call_args.args[1]["members"][0]["member_openid"] == "member-b"
    message = {"message_info": {"group_info": {"group_id": "123"}, "additional_config": {}},
               "raw_message": [{"type": "at", "data": {"target_user_id": "union-user"}}, {"type": "text", "data": "你好"}],
               "reply_to": "incoming"}
    assert (await adapter.handle_qq_official_gateway(message))["success"] is True
    assert 'id="member-a"' in adapter._api_post.call_args.args[1]["markdown"]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_mapping", [False, True])
async def test_silent_sync_associates_qq_without_replacing_union(
    adapter: QQOfficialAdapterPlugin, monkeypatch: pytest.MonkeyPatch, enable_mapping: bool,
) -> None:
    adapter.config.qq_official.enable_mapping = enable_mapping
    base = datetime.fromisoformat(incoming()["timestamp"]).timestamp()
    clock = {"monotonic": 200.0}
    monkeypatch.setattr("time.time", lambda: base + 10)
    monkeypatch.setattr("time.monotonic", lambda: clock["monotonic"])
    await adapter._record_union_identity("group-a", "member-a", "union-user")
    scope = adapter._identity_scope("group-a", "member-a")
    remote = []
    for index in range(3):
        message = {"message_id": f"message-{index}", "timestamp": base + index,
                   "raw_message": [{"type": "text", "data": f"这是第 {index} 条用于匹配的不同内容消息"}]}
        adapter._identity_sync.observe(scope, message)
        remote.append({"key": f"remote-{index}", "account_id": "999", "group_id": "123", "user_id": "456",
                       "timestamp": base + index, "fingerprint": text_fingerprint(message["raw_message"]),
                       "nickname": "SL昵称", "cardname": "SL名片"})
    clock["monotonic"] += 6
    adapter.ctx.api.call.return_value = {"protocol": 1, "observations": remote}
    await adapter._sync_identities_once()
    profile = adapter._identity_sync.get_profile(scope)
    assert profile is not None and profile.user_id == "456"
    assert adapter._group_user_map("group-a").get("member-a", "member-a") == ("456" if enable_mapping else "member-a")
    assert adapter._union_identities.legacy_qq_ids("app", "union-user") == ["456"]
    data = incoming()
    del data["author"]["username"]
    await adapter._receive_message("GROUP_MESSAGE_CREATE", data, account_id="bot", bot_name="麦麦")
    info = adapter.ctx.gateway.route_message.call_args.kwargs["message"]["message_info"]
    assert info["user_info"] == {"user_id": "456" if enable_mapping else "member-a", "user_nickname": "SL昵称", "user_cardname": "SL名片"}


def test_conflicting_raw_identity_is_not_overwritten(tmp_path: Path) -> None:
    identities = UnionIdentities().updated("app", "group", "raw", "union-a", qq_id="456", qq_source="manual")
    identities = identities.updated("app", "other-group", "other-raw", "union-b", qq_id="456", qq_source="manual")
    identities.save(tmp_path / "identities.json")
    assert all(row["conflict"] for row in identities.relations() if row["source"] == "456")
    with pytest.raises(ValueError, match="冲突"):
        identities.updated("app", "group", "raw", "union-b")
    assert UnionIdentities.load(tmp_path / "identities.json").user_map("app", "group") == {"raw": "union-a"}


@pytest.mark.asyncio
async def test_manual_qq_disagreement_does_not_use_wrong_silent_profile(adapter: QQOfficialAdapterPlugin) -> None:
    adapter._id_map["user"]["member-a"] = "456"
    scope = adapter._identity_scope("group-a", "member-a")
    adapter._identity_sync.profiles[scope] = UserProfile(scope, "789", "另一个用户", "不应采用的名片", time.time(), time.time())
    data = incoming()
    del data["author"]["username"]
    await adapter._receive_message("GROUP_MESSAGE_CREATE", data, account_id="bot", bot_name="麦麦")
    user = adapter.ctx.gateway.route_message.call_args.kwargs["message"]["message_info"]["user_info"]
    assert user == {"user_id": "456", "user_nickname": "456"}
    assert adapter._union_identities.legacy_qq_ids("app", "union-user") == ["456"]


def test_one_union_with_different_qq_numbers_is_a_migration_conflict() -> None:
    identities = UnionIdentities().updated("app", "a", "raw-a", "union", qq_id="456", qq_source="manual")
    identities = identities.updated("app", "b", "raw-b", "union", qq_id="789", qq_source="silent")
    assert {row["source"] for row in identities.relations() if row["conflict"]} == {"456", "789"}


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_mapping", [False, True])
async def test_observed_union_never_switches_existing_qq_or_openid(adapter: QQOfficialAdapterPlugin, enable_mapping: bool) -> None:
    adapter._union_identities = UnionIdentities()
    adapter.config.qq_official.enable_mapping = enable_mapping
    adapter._id_map["user"]["member-a"] = "456"
    await adapter._receive_message("GROUP_MESSAGE_CREATE", incoming(), account_id="bot", bot_name="麦麦")
    message = adapter.ctx.gateway.route_message.call_args.kwargs["message"]
    assert message["message_info"]["user_info"]["user_id"] == ("456" if enable_mapping else "member-a")
    await adapter._receive_message("C2C_MESSAGE_CREATE", incoming(False), account_id="bot", bot_name="麦麦")
    message = adapter.ctx.gateway.route_message.call_args.kwargs["message"]
    assert message["message_info"]["user_info"]["user_id"] == "private-openid"
    assert adapter._id_map["user"] == {"member-a": "456"}


@pytest.mark.parametrize("union", [None, "", " ", "union-user"])
def test_legacy_decoder_preserves_qq_mapping_without_requiring_union(union: Any) -> None:
    message, _, _ = decode_message("GROUP_MESSAGE_CREATE", incoming(union_openid=union), "bot",
                                   id_map={"user": {"member-a": "456"}})
    assert message["message_info"]["user_info"]["user_id"] == "456"


@pytest.mark.asyncio
async def test_late_silent_conflict_revokes_qq_evidence_but_preserves_union(adapter: QQOfficialAdapterPlugin) -> None:
    scope = adapter._identity_scope("group-a", "member-a")
    profile = UserProfile(scope, "456", "昵称", "名片", time.time(), time.time())
    adapter._identity_sync.profiles[scope] = profile
    await adapter._record_union_identity("group-a", "member-a", "union-user")
    assert adapter._union_identities.legacy_qq_ids("app", "union-user") == ["456"]
    profile.conflict = True
    await adapter._record_union_identity("group-a", "member-a", "union-user")
    assert adapter._union_identities.legacy_qq_ids("app", "union-user") == []
    assert adapter._group_user_map("group-a").get("member-a", "member-a") == "member-a"


@pytest.mark.asyncio
@pytest.mark.parametrize("protected_id,blocked", [("456", False), ("union-user", False), ("member-a", True)])
async def test_openid_mode_protection_only_matches_openid(
    adapter: QQOfficialAdapterPlugin, protected_id: str, blocked: bool,
) -> None:
    adapter.config.qq_official.enable_mapping = False
    adapter._id_map["user"]["member-a"] = "456"
    await adapter._record_union_identity("group-a", "member-a", "union-user")
    # 即使操作参数来自旧 QQ 号身份，也只能使用解析出的 OpenID 匹配保护名单。
    assert adapter._member_identity_ids("group-a", "member-a", "456") == {"member-a"}
    adapter.config.mute.admin_users = [protected_id]
    result = await adapter.api_set_group_ban("123", "member-a", 60)
    assert result["success"] is not blocked
    if blocked:
        adapter._api_post.assert_not_awaited()
    else:
        adapter._api_post.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,deleted", [("group", "group-a"), ("user", "member-a")])
async def test_delete_binding_clears_persisted_related_identities(
    adapter: QQOfficialAdapterPlugin, kind: str, deleted: str,
) -> None:
    adapter._stop = AsyncMock()
    adapter._id_map["user"] = {"member-a": "456", "keep": "789"}
    scope = adapter._identity_scope("group-a", "member-a")
    adapter._identity_sync.profiles[scope] = UserProfile(scope, "456", "昵称", "名片", time.time(), time.time())
    await adapter._record_union_identity("group-a", "member-a", "union-user")
    await adapter.webui_delete_bindings(kind, json.dumps([deleted]))
    adapter._stop.assert_awaited_once()
    assert deleted not in adapter._id_map[kind]
    assert adapter._id_map["user"]["keep"] == "789"
    assert scope not in adapter._identity_sync.profiles
    assert not adapter._union_identities.bindings
    restored = UnionIdentities.load(adapter.ctx.paths.data_dir / UNION_IDENTITIES_FILE)
    assert not restored.bindings
    assert json.loads((adapter.ctx.paths.data_dir / "auto_user_profiles.json").read_text(encoding="utf-8")) == []
    stored = json.loads((adapter.ctx.paths.data_dir / "id_map.json").read_text(encoding="utf-8"))
    assert deleted not in stored[kind]
    assert not (adapter.ctx.paths.data_dir / "binding_delete_pending.json").exists()
