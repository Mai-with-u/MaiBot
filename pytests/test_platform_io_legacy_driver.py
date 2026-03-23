"""Platform IO legacy driver 回归测试。"""

from typing import Any, Dict, Optional

import pytest

from src.chat.utils import utils as chat_utils
from src.chat.message_receive import uni_message_sender
from src.platform_io.drivers.base import PlatformIODriver
from src.platform_io.drivers.legacy_driver import LegacyPlatformDriver
from src.platform_io.manager import PlatformIOManager
from src.platform_io.types import DeliveryReceipt, DeliveryStatus, DriverDescriptor, DriverKind, RouteBinding, RouteKey


class _PluginDriver(PlatformIODriver):
    """测试用插件发送驱动。"""

    def __init__(self, driver_id: str, platform: str) -> None:
        """初始化测试驱动。

        Args:
            driver_id: 驱动 ID。
            platform: 负责的平台名称。
        """
        super().__init__(
            DriverDescriptor(
                driver_id=driver_id,
                kind=DriverKind.PLUGIN,
                platform=platform,
                plugin_id="test.plugin",
            )
        )

    async def send_message(
        self,
        message: Any,
        route_key: RouteKey,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """返回一个固定成功回执。

        Args:
            message: 待发送消息。
            route_key: 当前路由键。
            metadata: 发送元数据。

        Returns:
            DeliveryReceipt: 固定成功回执。
        """
        del metadata
        return DeliveryReceipt(
            internal_message_id=str(message.message_id),
            route_key=route_key,
            status=DeliveryStatus.SENT,
            driver_id=self.driver_id,
            driver_kind=self.descriptor.kind,
        )


@pytest.mark.asyncio
async def test_platform_io_uses_legacy_driver_when_no_explicit_send_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有显式发送路由时，应由 Platform IO 回退到 legacy driver。"""
    manager = PlatformIOManager()
    monkeypatch.setattr(chat_utils, "get_all_bot_accounts", lambda: {"qq": "bot-qq"})

    try:
        await manager.ensure_send_pipeline_ready()

        fallback_drivers = manager.resolve_drivers(RouteKey(platform="qq"))
        assert [driver.driver_id for driver in fallback_drivers] == ["legacy.send.qq"]

        plugin_driver = _PluginDriver(driver_id="plugin.qq.sender", platform="qq")
        await manager.add_driver(plugin_driver)
        manager.bind_send_route(
            RouteBinding(
                route_key=RouteKey(platform="qq"),
                driver_id=plugin_driver.driver_id,
                driver_kind=plugin_driver.descriptor.kind,
            )
        )

        explicit_drivers = manager.resolve_drivers(RouteKey(platform="qq"))
        assert [driver.driver_id for driver in explicit_drivers] == ["plugin.qq.sender"]
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_legacy_platform_driver_uses_prepared_universal_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """legacy driver 应复用已预处理消息的旧链发送函数。"""
    calls: list[dict[str, Any]] = []

    async def _fake_send_prepared_message_to_platform(message: Any, show_log: bool = True) -> bool:
        """记录 legacy driver 调用。"""
        calls.append({"message": message, "show_log": show_log})
        return True

    monkeypatch.setattr(
        uni_message_sender,
        "send_prepared_message_to_platform",
        _fake_send_prepared_message_to_platform,
    )

    driver = LegacyPlatformDriver(
        driver_id="legacy.send.qq",
        platform="qq",
        account_id="bot-qq",
    )
    message = type("FakeMessage", (), {"message_id": "message-1"})()
    receipt = await driver.send_message(
        message=message,
        route_key=RouteKey(platform="qq"),
        metadata={"show_log": False},
    )

    assert len(calls) == 1
    assert calls[0]["message"] is message
    assert calls[0]["show_log"] is False
    assert receipt.status == DeliveryStatus.SENT
    assert receipt.driver_id == "legacy.send.qq"
