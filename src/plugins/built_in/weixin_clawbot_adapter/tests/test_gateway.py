from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import base64
import logging

import pytest

from weixin_clawbot_adapter.client import APIError, trusted_url
from weixin_clawbot_adapter.codec import inbound, outbound
from weixin_clawbot_adapter.plugin import WeixinClawbotPlugin
from weixin_clawbot_adapter.storage import Store


@pytest.fixture
async def plugin(tmp_path):
    p = WeixinClawbotPlugin()
    p.set_plugin_config({"plugin": {"enabled": True, "config_version": "0.1.0"}})
    p._ctx = SimpleNamespace(
        paths=SimpleNamespace(data_dir=tmp_path),
        logger=logging.getLogger("test"),
        gateway=SimpleNamespace(update_state=AsyncMock(return_value=True), route_message=AsyncMock(return_value=True)),
    )
    owner = p
    p = owner.make_connection(0)
    owner.connections[0] = p
    p.store.state.update(token="test-token", account_id="bot")
    p.client = SimpleNamespace(
        max_bytes=1024,
        send=AsyncMock(),
        upload_image=AsyncMock(),
        download_image=AsyncMock(return_value=b"image"),
        close=AsyncMock(),
    )
    p.ready = True
    yield p
    await owner.on_unload()


def raw(identifier="1", user="user"):
    return {
        "from_user_id": user,
        "message_id": identifier,
        "context_token": "context",
        "message_type": 1,
        "create_time_ms": 1700000000000,
        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
    }


def outgoing(parts=None):
    return {
        "account_id": "bot",
        "message_info": {"user_info": {"user_id": "bot"}, "additional_config": {"platform_io_target_user_id": "user"}},
        "raw_message": parts or [{"type": "text", "data": "你好"}],
    }


async def test_restart_dedup_and_context_before_routing(plugin):
    async def route(**kwargs):
        assert plugin.store.state["contexts"]["user"] == "context"
        assert kwargs["message"]["raw_message"][0]["data"] == "你好"
        assert kwargs["message"]["timestamp"] == "1700000000.0"
        return True

    plugin.ctx.gateway.route_message.side_effect = route
    batch = {"msgs": [raw(), raw()], "get_updates_buf": "next"}
    await plugin.process_batch(batch, plugin.client)
    plugin.ctx.gateway.route_message.assert_awaited_once()
    reloaded = Store(plugin.store.path)
    await reloaded.load()
    plugin.store = reloaded
    await plugin.process_batch(batch, plugin.client)
    plugin.ctx.gateway.route_message.assert_awaited_once()
    assert plugin.store.state["cursor"] == "next"


async def test_failed_batch_keeps_cursor_and_replays_only_unaccepted(plugin):
    plugin.ctx.gateway.route_message.side_effect = [True, RuntimeError("RPC disconnected"), True]
    batch = {"msgs": [raw("1"), raw("2")], "get_updates_buf": "next"}
    with pytest.raises(RuntimeError):
        await plugin.process_batch(batch, plugin.client)
    assert plugin.store.state["cursor"] == ""
    assert len(plugin.store.state["seen"]) == 1
    await plugin.process_batch(batch, plugin.client)
    assert plugin.ctx.gateway.route_message.await_count == 3
    assert plugin.store.state["cursor"] == "next"


async def test_allowlist_and_echo_filter(plugin):
    plugin.owner.set_plugin_config(
        {"plugin": {"enabled": True, "config_version": "0.1.0"}, "weixin": {"allowed_users": ["allowed"]}}
    )
    echo = {**raw(user="allowed"), "message_type": 2}
    await plugin.process_batch({"msgs": [raw(), echo], "get_updates_buf": "next"}, plugin.client)
    plugin.ctx.gateway.route_message.assert_not_awaited()
    assert plugin.store.state["contexts"] == {}
    assert plugin.store.state["cursor"] == "next"


async def test_images_voice_and_unsupported_notice(plugin):
    message = raw()
    message["item_list"] = [{"type": 2, "image_item": {}}, {"type": 3, "voice_item": {"text": "转写"}}, {"type": 4}]
    await plugin.process_batch({"msgs": [message]}, plugin.client)
    result = plugin.ctx.gateway.route_message.call_args.kwargs["message"]["raw_message"]
    assert base64.b64decode(result[0]["binary_data_base64"]) == b"image"
    assert result[1]["data"] == "转写"
    assert "不支持" in result[2]["data"]


async def test_oversized_message_does_not_poison_polling(plugin):
    message = raw()
    message["item_list"] = [{"type": 1, "text_item": {"text": "x" * (16 * 1024**2)}}]
    await plugin.process_batch({"msgs": [message], "get_updates_buf": "next"}, plugin.client)
    routed = plugin.ctx.gateway.route_message.call_args.kwargs["message"]
    assert "消息过大" in routed["raw_message"][0]["data"]
    assert plugin.store.state["cursor"] == "next"


async def test_send_uses_recipient_not_bot_and_requires_context(plugin):
    with pytest.raises(ValueError, match="会话凭证"):
        await plugin.send_to_platform(outgoing())
    plugin.client.send.assert_not_awaited()
    plugin.store.state["contexts"]["user"] = "ctx"
    result = await plugin.send_to_platform(outgoing())
    assert result["success"] is True
    assert plugin.client.send.call_args.args[:2] == ("user", "ctx")


async def test_validate_all_segments_before_sending(plugin):
    plugin.store.state["contexts"]["user"] = "ctx"
    with pytest.raises(ValueError, match="不支持发送"):
        await plugin.send_to_platform(outgoing([{"type": "text", "data": "hello"}, {"type": "voice"}]))
    plugin.client.send.assert_not_awaited()


async def test_send_timeout_not_retried(plugin):
    plugin.store.state["contexts"]["user"] = "ctx"
    plugin.client.send.side_effect = asyncio.TimeoutError
    with pytest.raises(RuntimeError, match="结果不确定"):
        await plugin.send_to_platform(outgoing())
    plugin.client.send.assert_awaited_once()


async def test_session_expiration_clears_persisted_account(plugin, monkeypatch):
    client = plugin.client
    client.updates = AsyncMock(side_effect=APIError(-14, "getupdates"))
    client.token = "test-token"
    monkeypatch.setattr("weixin_clawbot_adapter.connection.Client", lambda *a: client)
    await plugin.run(False)
    assert plugin.store.state["token"] == ""
    assert not plugin.ready
    assert "失效" in plugin.status
    restored = Store(plugin.store.path)
    await restored.load()
    assert restored.state["contexts"] == {}


async def test_qr_login_persists_account(plugin):
    client = SimpleNamespace(
        base_url="https://ilinkai.weixin.qq.com",
        call=AsyncMock(
            side_effect=[
                {"qrcode": "id", "qrcode_img_content": "https://weixin.qq.com/example"},
                {"status": "confirmed", "bot_token": "new-token", "ilink_bot_id": "new-bot"},
            ]
        ),
    )
    plugin.store.state["account_id"] = ""
    assert await plugin.login(client)
    assert client.token == "new-token"
    assert plugin.qr is None
    restored = Store(plugin.store.path)
    await restored.load()
    assert restored.state["account_id"] == "new-bot"


async def test_widget_action_returns_before_slow_lifecycle(plugin):
    plugin.ready = False
    gate = asyncio.Event()
    original = plugin.stop

    async def slow_stop():
        await gate.wait()
        await original()

    plugin.stop = slow_stop
    await asyncio.wait_for(plugin.owner.remove_account(0), timeout=0.5)
    assert not plugin.action_task.done()
    gate.set()
    await plugin.action_task
    assert plugin.store.state["token"] == ""


def test_media_bounds_and_ipc_limit():
    message = outgoing([{"type": "image", "binary_data_base64": base64.b64encode(b"12345").decode()}])
    with pytest.raises(ValueError, match="大小限制"):
        outbound(message, 4)
    with pytest.raises(ValueError, match="IPC"):
        inbound("bot", raw(), [{"type": "text", "data": "x" * (16 * 1024**2)}])


@pytest.mark.parametrize(
    "url",
    [
        "http://ilinkai.weixin.qq.com",
        "https://weixin.qq.com.attacker.test",
        "https://localhost",
        "https://user@ilinkai.weixin.qq.com",
    ],
)
def test_reject_untrusted_service_url(url):
    with pytest.raises(ValueError):
        trusted_url(url)


async def test_accounts_isolate_inbound_outbound_and_nicknames(plugin):
    owner = plugin.owner
    second = owner.make_connection(1)
    owner.connections[1] = second
    second.store.state.update(token="second-token", account_id="bot2", remark="工作微信", user_id="user")
    second.client = SimpleNamespace(max_bytes=1024, send=AsyncMock(), close=AsyncMock())
    second.ready = True
    plugin.store.state.update(remark="私人微信", user_id="user")
    await asyncio.gather(
        plugin.process_batch({"msgs": [raw()], "get_updates_buf": "cursor1"}, plugin.client),
        second.process_batch({"msgs": [raw()], "get_updates_buf": "cursor2"}, second.client),
    )
    routes = {call.kwargs["message"]["account_id"]: call.kwargs
              for call in plugin.ctx.gateway.route_message.call_args_list}
    assert routes["bot"]["gateway_name"] != routes["bot2"]["gateway_name"]
    assert routes["bot"]["message"]["message_info"]["user_info"]["user_nickname"] == "私人微信"
    assert routes["bot2"]["message"]["message_info"]["user_info"]["user_nickname"] == "工作微信"
    assert routes["bot"]["dedupe_key"] != routes["bot2"]["dedupe_key"]
    assert plugin.store.state["cursor"] == "cursor1"
    assert second.store.state["cursor"] == "cursor2"
    await owner.send_account_0(outgoing())
    await owner.send_account_1({**outgoing(), "account_id": "bot2"})
    plugin.client.send.assert_awaited_once()
    second.client.send.assert_awaited_once()
    with pytest.raises(ValueError, match="归属"):
        await owner.send_account_0({**outgoing(), "account_id": "bot2"})
    with pytest.raises(ValueError, match="归属"):
        await owner.send_account_1({**outgoing(), "account_id": ""})


async def test_add_account_keeps_existing_connection_and_renames(plugin, monkeypatch):
    from weixin_clawbot_adapter.connection import AccountConnection
    started = []
    monkeypatch.setattr(AccountConnection, "start", lambda self, login=False: started.append((self.slot, login)))
    owner = plugin.owner
    result = await owner.add_account(" 工作微信 ")
    second = owner.connections[result["slot"]]
    await second.action_task
    assert plugin.ready
    plugin.client.close.assert_not_awaited()
    assert started == [(1, True)]
    await owner.rename_account(1, "新的备注")
    restored = Store(second.store.path)
    await restored.load()
    assert restored.state["remark"] == "新的备注"
    assert restored.state["token"] == ""
    snapshot = await owner.accounts()
    assert "test-token" not in str(snapshot)
    assert snapshot["accounts"][1]["remark"] == "新的备注"


async def test_remove_one_account_keeps_other_online(plugin):
    owner = plugin.owner
    second = owner.make_connection(1)
    owner.connections[1] = second
    second.store.state.update(account_id="bot2", token="token2", remark="second")
    await second.store.save()
    await owner.remove_account(1)
    await second.action_task
    assert list(owner.connections) == [0]
    assert plugin.ready
    plugin.client.close.assert_not_awaited()
    restored = Store(second.store.path)
    await restored.load()
    assert restored.state["token"] == restored.state["account_id"] == restored.state["remark"] == ""


async def test_restore_legacy_and_multiple_accounts(plugin, monkeypatch):
    from weixin_clawbot_adapter.connection import AccountConnection
    started = []
    monkeypatch.setattr(AccountConnection, "start", lambda self, login=False: started.append(self.slot))
    await plugin.store.save()
    second = plugin.owner.make_connection(1)
    second.store.state.update(token="second", account_id="bot2", remark="工作微信")
    await second.store.save()
    restored = WeixinClawbotPlugin()
    restored._ctx = plugin.ctx
    restored.set_plugin_config({"plugin": {"enabled": True, "config_version": "0.2.0"}})
    await restored.on_load()
    assert started == [0, 1]
    assert restored.connections[0].store.state["token"] == "test-token"
    assert restored.connections[1].store.state["remark"] == "工作微信"
    assert restored.connections[0].gateway_name == "weixin_clawbot_gateway"
    await restored.on_unload()


async def test_duplicate_login_does_not_replace_other_credentials(plugin):
    second = plugin.owner.make_connection(1)
    plugin.owner.connections[1] = second
    client = SimpleNamespace(base_url="https://ilinkai.weixin.qq.com", call=AsyncMock(side_effect=[
        {"qrcode": "qr", "qrcode_img_content": "example"},
        {"status": "confirmed", "bot_token": "duplicate", "ilink_bot_id": "bot"},
    ]))
    with pytest.raises(ValueError, match="已绑定"):
        await second.login(client)
    assert plugin.store.state["token"] == "test-token"
    assert second.store.state["token"] == ""


async def test_expiration_preserves_identity_and_remark(plugin):
    plugin.store.state.update(remark="我的微信", user_id="user")
    await plugin.store.invalidate()
    restored = Store(plugin.store.path)
    await restored.load()
    assert restored.state["account_id"] == "bot"
    assert restored.state["remark"] == "我的微信"
    assert restored.state["user_id"] == "user"
    assert restored.state["token"] == ""


async def test_remark_not_assigned_to_another_sender(plugin):
    plugin.store.state.update(remark="我的微信", user_id="owner")
    await plugin.process_batch({"msgs": [raw(user="guest")]}, plugin.client)
    message = plugin.ctx.gateway.route_message.call_args.kwargs["message"]
    assert message["message_info"]["user_info"]["user_nickname"] == "guest"
