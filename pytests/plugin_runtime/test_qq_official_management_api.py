from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import logging

import pytest

from src.plugins.built_in.qq_official_adapter.plugin import QQOfficialAdapterPlugin
from src.plugins.built_in.qq_official_adapter.settings import AdapterSettings


@pytest.fixture
def adapter():
    plugin = QQOfficialAdapterPlugin()
    plugin._plugin_config_instance = AdapterSettings()
    plugin._set_context(SimpleNamespace(
        logger=logging.getLogger("qq-management-test"),
        message=SimpleNamespace(get_by_id=AsyncMock()),
    ))
    plugin._ready = True
    plugin._id_map = {"group": {"group-openid": "123"}, "user": {"user-openid": "456"}}
    plugin._api_post = AsyncMock(return_value={})
    return plugin


def test_management_apis_are_public(adapter):
    apis = {item["name"]: item for item in adapter.get_components() if item["type"] == "API"}
    for name in ("adapter.qq_official.group.set_group_ban", "adapter.qq_official.message.delete_msg"):
        assert apis[name]["metadata"]["public"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("duration,op", [(120, "add"), (0, "del")])
async def test_ban_resolves_ids_and_unmutes(adapter, duration, op):
    result = await adapter.api_set_group_ban("123", "456", duration)
    assert result["success"] is True
    path, body = adapter._api_post.call_args.args
    assert path == "/v2/groups/group-openid/restrict_chat_setting"
    member = body["members"][0]
    assert member["member_openid"] == "user-openid"
    assert member["op"] == op
    assert bool(member["mute_expire_at"]) == (duration > 0)


@pytest.mark.asyncio
async def test_ban_preserves_policies_with_openid_input(adapter):
    adapter.config.mute.admin_users = ["456"]
    assert not (await adapter.api_set_group_ban("group-openid", "user-openid", 60))["success"]
    adapter._api_post.assert_not_awaited()
    assert (await adapter.api_set_group_ban("group-openid", "user-openid", 0))["success"]
    adapter._api_post.reset_mock()
    adapter.config.mute.allowed_groups = ["789"]
    assert not (await adapter.api_set_group_ban("123", "456", 0))["success"]
    adapter._api_post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [-1, True, 1.5, "60"])
async def test_ban_rejects_invalid_duration(adapter, duration):
    assert not (await adapter.api_set_group_ban("123", "456", duration))["success"]
    adapter._api_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_ban_rejects_unmapped_group(adapter):
    assert not (await adapter.api_set_group_ban("789", "456", 60))["success"]
    adapter._api_post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [True, False])
@pytest.mark.parametrize("status", [204, 403, 400])
async def test_recall_resolves_message_and_reports_platform_error(adapter, group, status):
    adapter.ctx.message.get_by_id.return_value = {
        "message_info": {
            "group_info": {"group_id": "123"} if group else None,
            "additional_config": {} if group else {"platform_io_target_user_id": "456"},
        }
    }
    body = '{"message":"无操作权限","code":40062003}' if status == 400 else "permission denied"
    response = SimpleNamespace(status=status, text=AsyncMock(return_value=body))
    request = AsyncMock()
    request.__aenter__.return_value = response
    session = SimpleNamespace(delete=Mock(return_value=request))
    adapter._require_session = Mock(return_value=session)
    adapter._authorize = AsyncMock(return_value="token")
    result = await adapter.api_delete_msg("message/id", "stream")
    assert result["success"] == (status == 204)
    if status == 403:
        assert "403" in result["error"]
    elif status == 400:
        assert result["error"] == "无法撤回此消息，可能是因为没有开启权限，或用户是群主或管理员"
    target = "groups/group-openid" if group else "users/user-openid"
    assert session.delete.call_args.args[0].endswith(f"/v2/{target}/messages/message%2Fid")
    adapter.ctx.message.get_by_id.assert_awaited_once_with(
        "message/id", stream_id="stream", include_binary_data=False,
    )


@pytest.mark.asyncio
async def test_disabled_management_does_not_call_platform(adapter):
    adapter.config.mute.enabled = False
    adapter.config.recall.enabled = False
    assert not (await adapter.api_set_group_ban("123", "456", 60))["success"]
    assert not (await adapter.api_delete_msg("message"))["success"]
    adapter._api_post.assert_not_awaited()
    adapter.ctx.message.get_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_kick_disabled_by_default(adapter):
    assert not (await adapter.tool_kick_member("message"))["success"]
    adapter.ctx.message.get_by_id.assert_not_awaited()


def kick_message(group=True):
    return {"message_info": {
        "group_info": {"group_id": "123"} if group else None,
        "user_info": {"user_id": "456"},
        "additional_config": {"qq_official_user_openid": "user-openid"},
    }}


@pytest.mark.asyncio
async def test_kick_removes_member_without_blacklisting(adapter):
    adapter.config.kick.enabled = True
    adapter.ctx.message.get_by_id.return_value = kick_message()
    adapter._api_post.return_value = {"remove_members_result": "success"}
    result = await adapter.tool_kick_member("message", reason="广告", stream_id="stream")
    assert result["success"] is True
    adapter._api_post.assert_awaited_once_with(
        "/v2/groups/group-openid/batch_remove_members",
        {"member_openids": ["user-openid"], "add_to_member_blacklist": False},
    )
    adapter.ctx.message.get_by_id.assert_awaited_once_with(
        "message", stream_id="stream", include_binary_data=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["private", "whitelist", "protected_id", "protected_openid", "self", "missing"])
async def test_kick_rejects_disallowed_targets(adapter, blocked):
    adapter.config.kick.enabled = True
    adapter.ctx.message.get_by_id.return_value = kick_message(group=blocked != "private")
    if blocked == "whitelist":
        adapter.config.kick.allowed_groups = ["789"]
    elif blocked == "protected_id":
        adapter.config.kick.admin_users = ["456"]
    elif blocked == "protected_openid":
        adapter.config.kick.admin_users = ["user-openid"]
    elif blocked == "self":
        adapter._account_id = "user-openid"
    elif blocked == "missing":
        adapter.ctx.message.get_by_id.return_value = None
    assert not (await adapter.tool_kick_member("message"))["success"]
    adapter._api_post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_error", [True, False])
async def test_kick_reports_platform_failure(adapter, platform_error):
    adapter.config.kick.enabled = True
    adapter.ctx.message.get_by_id.return_value = kick_message()
    if platform_error:
        adapter._api_post.side_effect = RuntimeError("QQ API: 11253 无接口访问权限")
    result = await adapter.tool_kick_member("message")
    assert not result["success"]
    if platform_error:
        assert "11253" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_kick_component_follows_config(adapter, enabled):
    adapter.config.kick.enabled = enabled
    adapter.ctx.plugin_id = "Mai-with-u.qq-adapter"
    adapter.ctx.component = SimpleNamespace(
        enable_component=AsyncMock(return_value={"success": True}),
        disable_component=AsyncMock(return_value={"success": True}),
    )
    await adapter._sync_tool_states()
    method = adapter.ctx.component.enable_component if enabled else adapter.ctx.component.disable_component
    method.assert_any_await("Mai-with-u.qq-adapter.kick_member", "TOOL")
