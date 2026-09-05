from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List

import asyncio
import pytest

from src.chat.message_receive.message import SessionMessage
from src.cli.maisaka_cli_sender import CLI_PLATFORM_NAME, CliPlatformDriver
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.platform_io.drivers.legacy_driver import LegacyPlatformDriver, extract_legacy_ws_platform
from src.platform_io.inbound import dispatch_core_inbound
from src.platform_io.manager import PlatformIOManager
from src.platform_io.types import DeliveryStatus, DriverKind, RouteBinding, RouteKey


def _qq_inbound_payload() -> Dict[str, Any]:
    return {
        "message_info": {
            "platform": "qq",
            "message_id": "m1",
            "user_info": {"user_id": 123, "user_nickname": "用户"},
            "group_info": None,
        }
    }


def test_extract_legacy_ws_platform_from_message_info() -> None:
    assert extract_legacy_ws_platform(_qq_inbound_payload()) == "qq"


def test_extract_legacy_ws_platform_from_top_level() -> None:
    assert extract_legacy_ws_platform({"platform": "telegram"}) == "telegram"


@pytest.mark.asyncio
async def test_legacy_ws_inbound_reaches_receive_message(monkeypatch: pytest.MonkeyPatch) -> None:
    received: List[Any] = []
    fake_message = SimpleNamespace(
        message_id="m1",
        platform="qq",
        message_info=SimpleNamespace(additional_config={}),
    )

    class FakeChatBot:
        async def _ensure_started(self) -> None:
            return None

        async def receive_message(self, message: Any) -> None:
            received.append(message)

    monkeypatch.setattr("src.chat.message_receive.bot.chat_bot", FakeChatBot())
    monkeypatch.setattr(
        "src.chat.message_receive.message.SessionMessage.from_maim_message",
        lambda _raw: fake_message,
    )
    monkeypatch.setattr("maim_message.MessageBase.from_dict", lambda data: data)

    manager = PlatformIOManager()
    manager.set_inbound_dispatcher(dispatch_core_inbound)
    driver = LegacyPlatformDriver(driver_id="legacy.send.qq", platform="qq")
    manager.register_driver(driver)
    manager.bind_receive_route(
        RouteBinding(
            route_key=RouteKey(platform="qq"),
            driver_id=driver.driver_id,
            driver_kind=DriverKind.LEGACY,
        )
    )

    accepted = await driver.emit_inbound(_qq_inbound_payload())
    if manager._inbound_dispatch_tasks:
        await asyncio_gather_inbound(manager)

    assert accepted is True
    assert received == [fake_message]


@pytest.mark.asyncio
async def test_handle_ws_inbound_uses_manager_legacy_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    received: List[Any] = []
    fake_message = SimpleNamespace(
        message_id="m1",
        platform="qq",
        message_info=SimpleNamespace(additional_config={}),
    )

    class FakeChatBot:
        async def _ensure_started(self) -> None:
            return None

        async def receive_message(self, message: Any) -> None:
            received.append(message)

    monkeypatch.setattr("src.chat.message_receive.bot.chat_bot", FakeChatBot())
    monkeypatch.setattr(
        "src.chat.message_receive.message.SessionMessage.from_maim_message",
        lambda _raw: fake_message,
    )
    monkeypatch.setattr("maim_message.MessageBase.from_dict", lambda data: data)
    monkeypatch.setattr("src.platform_io.drivers.legacy_driver._register_ws_inbound_handler", lambda: None)

    manager = PlatformIOManager()
    manager.set_inbound_dispatcher(dispatch_core_inbound)
    monkeypatch.setattr("src.platform_io.manager.get_platform_io_manager", lambda: manager)

    await LegacyPlatformDriver.handle_ws_inbound(_qq_inbound_payload())
    if manager._inbound_dispatch_tasks:
        await asyncio_gather_inbound(manager)

    assert "legacy.send.qq" in [driver.driver_id for driver in manager.driver_registry.list()]
    assert received == [fake_message]


@pytest.mark.asyncio
async def test_send_pipeline_binds_legacy_receive_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.chat.utils.utils.get_configured_bot_accounts", lambda: {})
    monkeypatch.setattr("src.platform_io.drivers.legacy_driver._register_ws_inbound_handler", lambda: None)
    manager = PlatformIOManager()

    await manager.ensure_send_pipeline_ready()

    assert manager.receive_route_table.has_binding_for_driver(RouteKey(platform="webui"), "legacy.send.webui")
    await manager.stop()


@pytest.mark.asyncio
async def test_cli_driver_renders_text_and_returns_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    rendered: List[str] = []
    monkeypatch.setattr(
        "src.cli.maisaka_cli_sender.render_cli_message",
        lambda content, title="": rendered.append(content),
    )

    message = SessionMessage(message_id="cli-1", timestamp=datetime.now(), platform=CLI_PLATFORM_NAME)
    message.message_info = MessageInfo(user_info=UserInfo(user_id="maisaka_user", user_nickname="用户"))
    message.raw_message = MessageSequence([TextComponent("你好")])
    message.processed_plain_text = "你好"

    driver = CliPlatformDriver()
    receipt = await driver.send_message(message, RouteKey(platform=CLI_PLATFORM_NAME))

    assert receipt.status == DeliveryStatus.SENT
    assert receipt.driver_id == CliPlatformDriver.DRIVER_ID
    assert rendered == ["你好"]


async def asyncio_gather_inbound(manager: PlatformIOManager) -> None:
    await asyncio.gather(*list(manager._inbound_dispatch_tasks))
