import pytest

from src.platform_io.manager import PlatformIOManager
from src.platform_io.types import RouteKey


@pytest.mark.asyncio
async def test_send_pipeline_always_registers_webui_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """WebUI 无需配置外部平台账号，也必须保留本地 WebSocket 发送路由。"""
    monkeypatch.setattr("src.chat.utils.utils.get_configured_bot_accounts", lambda: {})
    monkeypatch.setattr("src.platform_io.drivers.legacy_driver._register_ws_inbound_handler", lambda: None)
    manager = PlatformIOManager()

    await manager.ensure_send_pipeline_ready()
    drivers = manager.resolve_drivers(RouteKey(platform="webui", account_id="self"))

    assert [driver.driver_id for driver in drivers] == ["legacy.send.webui"]
    assert manager.receive_route_table.has_binding_for_driver(RouteKey(platform="webui"), "legacy.send.webui")
    await manager.stop()
