from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.platform_io.route_key_factory import RouteKeyFactory
from src.platform_io.types import RouteKey
from src.plugin_runtime.host.message_utils import PluginMessageUtils
from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor, _MessageGatewayRuntimeState
from src.plugin_runtime.protocol.envelope import Envelope, MessageType


def _message(**fields):
    return {
        "message_id": "msg-1",
        "timestamp": "1791504000.0",
        "platform": "qq",
        "message_info": {
            "user_info": {"user_id": "user-1", "user_nickname": "用户"},
            "additional_config": {},
        },
        "raw_message": [{"type": "text", "data": "你好"}],
        **fields,
    }


def _inbound(message, metadata=None):
    state = _MessageGatewayRuntimeState(ready=True, platform="qq", account_id="bot-1", scope="connection-1")
    gateway = SimpleNamespace(platform="qq", full_name="adapter:gateway")
    route = PluginRunnerSupervisor._build_inbound_route_key(
        None, gateway, state, message, metadata or {}
    )
    assert state.account_id == "bot-1"
    assert state.scope == "connection-1"
    return route


def test_formal_identity_roundtrip_and_old_fields_cannot_override():
    message = _message(account_id="bot-1", scope=None)
    message["message_info"]["additional_config"] = {"self_id": "wrong", "platform_io_scope": "wrong"}
    internal = PluginMessageUtils._build_session_message_from_dict(message)
    assert RouteKeyFactory.from_session_message(internal) == RouteKey("qq", "bot-1")
    serialized = PluginMessageUtils._session_message_to_dict(internal)
    assert serialized["account_id"] == "bot-1"
    assert serialized["scope"] is None
    restored = type(internal).from_db_instance(internal.to_db_instance())
    assert (restored.account_id, restored.scope) == ("bot-1", None)


@pytest.mark.parametrize("fields", [
    {"platform": "telegram", "account_id": "bot-1", "scope": "connection-1"},
    {"account_id": "other-bot", "scope": "connection-1"},
    {"account_id": "bot-1", "scope": "other-connection"},
    {"account_id": "bot-1", "scope": None},
    {"account_id": "", "scope": "connection-1"},
])
def test_inbound_rejects_undeclared_identity(fields):
    with pytest.raises(ValueError):
        _inbound(_message(**fields))


def test_inbound_uses_formal_identity_not_legacy_metadata():
    assert _inbound(
        _message(account_id="bot-1", scope="connection-1"),
        {"self_id": "wrong", "connection_id": "wrong"},
    ) == RouteKey("qq", "bot-1", "connection-1")


def test_legacy_identity_warns_and_is_checked_against_gateway(monkeypatch):
    warnings = []
    monkeypatch.setattr("src.platform_io.route_key_factory.logger.warning", warnings.append)
    message = _message()
    message["message_info"]["additional_config"] = {"self_id": "bot-1"}
    assert _inbound(message) == RouteKey("qq", "bot-1", "connection-1")
    assert "下个版本移除" in warnings[0]
    message["message_info"]["additional_config"]["self_id"] = "other-bot"
    with pytest.raises(ValueError, match="未由网关"):
        _inbound(message)


def test_formal_empty_identity_does_not_fall_back_to_legacy():
    message = _message(account_id="", scope="connection-1")
    message["message_info"]["additional_config"]["self_id"] = "bot-1"
    with pytest.raises(ValueError, match="缺少明确"):
        _inbound(message)


def test_route_metadata_is_not_injected_as_identity():
    internal = PluginMessageUtils._build_session_message_from_dict(_message(account_id="bot-1", scope=None))
    PluginRunnerSupervisor._attach_inbound_route_metadata(
        internal, RouteKey("qq", "bot-1"), {"self_id": "wrong", "connection_id": "wrong", "custom": "value"}
    )
    assert internal.message_info.additional_config == {"custom": "value"}
    assert internal.account_id == "bot-1"


@pytest.mark.asyncio
async def test_route_rpc_does_not_discover_accounts(monkeypatch):
    supervisor = object.__new__(PluginRunnerSupervisor)
    gateway = SimpleNamespace(
        name="gateway", full_name="adapter:gateway", supports_receive=True,
        platform="qq", protocol="test",
    )
    state = _MessageGatewayRuntimeState(ready=True, platform="qq", account_id="bot-1")
    supervisor._message_gateway_states = {"adapter": {"gateway": state}}
    supervisor._message_gateway = SimpleNamespace(
        build_session_message=PluginMessageUtils._build_session_message_from_dict
    )
    monkeypatch.setattr(supervisor, "_resolve_message_gateway_entry", lambda *_: gateway)
    monkeypatch.setattr(supervisor, "_evaluate_inbound_adapter_policy", lambda *_: {})
    discovery = AsyncMock()
    monkeypatch.setattr("src.plugin_runtime.host.supervisor.record_adapter_account", discovery)
    manager = SimpleNamespace(accept_inbound=AsyncMock(return_value=True))
    monkeypatch.setattr("src.plugin_runtime.host.supervisor.get_platform_io_manager", lambda: manager)
    request = Envelope(
        request_id=1, message_type=MessageType.REQUEST, plugin_id="adapter",
        payload={"gateway_name": "gateway", "message": _message(account_id="bot-1", scope=None)},
    )
    response = await supervisor._handle_route_message(request)
    assert response.error is None
    assert response.payload["accepted"] is True
    discovery.assert_not_called()
    internal = manager.accept_inbound.call_args.args[0].session_message
    assert internal.account_id == "bot-1"
    assert internal.message_info.additional_config == {}
