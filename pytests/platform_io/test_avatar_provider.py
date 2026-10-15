from types import SimpleNamespace
from unittest.mock import AsyncMock
import sys

import pytest

from src.platform_io import PlatformIOManager, RouteBinding, RouteKey
from src.platform_io.drivers.legacy_driver import LegacyPlatformDriver
from src.platform_io.drivers.plugin_driver import PluginPlatformDriver
from src.plugin_runtime import avatar_provider
from src.plugin_runtime.host.api_registry import APIRegistry


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch):
    broker = PlatformIOManager()
    registry = APIRegistry()
    invoke = AsyncMock(
        return_value=SimpleNamespace(
            error=None,
            payload={"success": True, "result": {"status": "available", "url": "https://example.com/avatar"}},
        )
    )
    supervisor = SimpleNamespace(api_registry=registry, invoke_api=invoke)
    runtime = SimpleNamespace(supervisors=[supervisor])
    monkeypatch.setattr(avatar_provider, "get_platform_io_manager", lambda: broker)
    monkeypatch.setitem(
        sys.modules, "src.plugin_runtime.integration", SimpleNamespace(get_plugin_runtime_manager=lambda: runtime)
    )
    return broker, registry, invoke, supervisor


def add_adapter(provider, plugin_id: str, account_id: str, *, api: bool = True):
    broker, registry, invoke, supervisor = provider
    driver = PluginPlatformDriver(
        driver_id=plugin_id,
        platform="custom",
        supervisor=supervisor,
        component_name="gateway",
        supports_send=True,
        account_id=account_id,
        scope="connection",
        plugin_id=plugin_id,
    )
    broker.register_driver(driver)
    broker.bind_send_route(
        RouteBinding(
            route_key=driver.descriptor.route_key,
            driver_id=driver.driver_id,
            driver_kind=driver.descriptor.kind,
        )
    )
    if api:
        registry.register_api(
            "adapter.avatar.get", plugin_id, {"public": True, "version": "1", "handler_name": "avatar"}
        )


@pytest.mark.asyncio
async def test_platform_without_provider_is_explicitly_unsupported(provider):
    assert await avatar_provider.query_adapter_avatar("unknown", "1", "user") == {"status": "unsupported"}
    add_adapter(provider, "old_adapter", "1", api=False)
    assert await avatar_provider.query_adapter_avatar("custom", "1", "user", account_id="1") == {
        "status": "unsupported"
    }
    provider[2].assert_not_awaited()


@pytest.mark.asyncio
async def test_routes_to_matching_adapter_and_rejects_ambiguous_default(provider):
    add_adapter(provider, "first", "1")
    add_adapter(provider, "second", "2")
    result = await avatar_provider.query_adapter_avatar("custom", "person", "group", account_id="2")
    assert result["status"] == "available"
    kwargs = provider[2].await_args.kwargs
    assert kwargs["plugin_id"] == "second"
    assert kwargs["args"]["account_id"] == "2"
    assert kwargs["args"]["scope"] == "connection"
    with pytest.raises(ValueError, match="多个适配器"):
        await avatar_provider.query_adapter_avatar("custom", "person", "user")


@pytest.mark.asyncio
async def test_provider_failure_is_not_reported_as_absent(provider):
    add_adapter(provider, "adapter", "1")
    provider[2].return_value = SimpleNamespace(error={"message": "connection failed"}, payload={})
    with pytest.raises(RuntimeError, match="connection failed"):
        await avatar_provider.query_adapter_avatar("custom", "person", "user", account_id="1")


@pytest.mark.asyncio
async def test_legacy_send_driver_does_not_hide_account_scoped_avatar_provider(provider):
    broker = provider[0]
    add_adapter(provider, "adapter", "1")
    legacy = LegacyPlatformDriver(driver_id="legacy.send.custom", platform="custom", account_id="1")
    broker.register_driver(legacy)
    # 模拟实际启用的旧发送链：平台级查询命中 legacy，插件只绑定账号级路由。
    broker._legacy_send_drivers["custom"] = legacy
    assert broker.resolve_drivers(RouteKey(platform="custom")) == [legacy]
    result = await avatar_provider.query_adapter_avatar("custom", "person", "user")
    assert result["status"] == "available"
    assert provider[2].await_args.kwargs["plugin_id"] == "adapter"

    # legacy 存在时也不能绕过多适配器归属检查。
    add_adapter(provider, "second", "2")
    with pytest.raises(ValueError, match="多个适配器"):
        await avatar_provider.query_adapter_avatar("custom", "person", "user")
    result = await avatar_provider.query_adapter_avatar("custom", "person", "user", account_id="2")
    assert result["status"] == "available"
    assert provider[2].await_args.kwargs["plugin_id"] == "second"
