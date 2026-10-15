from pathlib import Path
from tomlkit.exceptions import ParseError
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.platform_io.adapter_policy import AdapterPolicyManager
from src.platform_io.drivers.base import PlatformIODriver
from src.platform_io.manager import PlatformIOManager
from src.platform_io.types import DeliveryReceipt, DeliveryStatus, DriverDescriptor, DriverKind, RouteBinding, RouteKey


@pytest.fixture
def policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AdapterPolicyManager:
    manager = AdapterPolicyManager(tmp_path / "policy.toml")
    monkeypatch.setattr("src.platform_io.manager.get_adapter_policy_manager", lambda: manager)
    return manager


def register_driver(manager: PlatformIOManager, name: str, kind: DriverKind = DriverKind.PLUGIN) -> Mock:
    driver = Mock(spec=PlatformIODriver)
    driver.driver_id = f"gateway:adapter.qq:{name}"
    driver.descriptor = DriverDescriptor(
        driver_id=driver.driver_id,
        kind=kind,
        platform="qq",
        account_id="bot",
        scope="main",
        plugin_id="adapter.qq",
        metadata={"gateway_name": name},
    )
    driver.send_message = AsyncMock(
        return_value=DeliveryReceipt(
            internal_message_id="message",
            route_key=RouteKey("qq", "bot", "main"),
            status=DeliveryStatus.SENT,
            driver_id=driver.driver_id,
            driver_kind=kind,
        )
    )
    manager.register_driver(driver)
    manager.bind_send_route(RouteBinding(driver.descriptor.route_key, driver.driver_id, kind))
    return driver


def message(chat_type: str, target_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        message_id="message",
        message_info=SimpleNamespace(
            group_info=SimpleNamespace(group_id=target_id) if chat_type == "group" else None,
            user_info=SimpleNamespace(user_id="bot"),
            additional_config={"platform_io_target_user_id": target_id},
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["group", "private"])
@pytest.mark.parametrize("kind", [DriverKind.PLUGIN, DriverKind.LEGACY])
async def test_policy_reload_blocks_outbound_without_tracking(policy, chat_type, kind):
    manager = PlatformIOManager()
    driver = register_driver(manager, "main", kind)
    route = driver.descriptor.route_key
    assert (await manager.send_message(message(chat_type, "blocked"), route)).has_success
    policy.policy_path.write_text(
        f'[defaults.{chat_type}]\ndefault_action = "block"\nallow_ids = ["allowed"]\n', encoding="utf-8"
    )

    batch = await manager.send_message(message(chat_type, "blocked"), route)

    assert not batch.has_success
    assert batch.receipts[0].status == DeliveryStatus.DROPPED
    assert batch.receipts[0].metadata["adapter_policy"]["reason"] == "default_block"
    assert driver.send_message.await_count == 1
    assert manager.outbound_tracker.get_pending("message", driver.driver_id) is None
    assert not await manager.is_target_allowed(route, chat_type=chat_type, target_id="blocked")
    assert await manager.is_target_allowed(route, chat_type=chat_type, target_id="allowed")
    assert (await manager.send_message(message(chat_type, "allowed"), route)).has_success


@pytest.mark.asyncio
async def test_each_gateway_uses_full_identity_and_only_allowed_route_sends(policy):
    policy.policy_path.write_text(
        """
[defaults.group]
default_action = "block"
[[adapters]]
adapter_id = "gateway:adapter.qq:allowed"
plugin_id = "adapter.qq"
gateway_name = "allowed"
platform = "qq"
account_id = "bot"
scope = "main"
[adapters.group]
allow_ids = ["target"]
""",
        encoding="utf-8",
    )
    manager = PlatformIOManager()
    blocked = register_driver(manager, "blocked")
    allowed = register_driver(manager, "allowed")
    route = allowed.descriptor.route_key

    assert await manager.is_target_allowed(route, chat_type="group", target_id="target")
    batch = await manager.send_message(message("group", "target"), route)

    assert batch.has_success
    assert [receipt.driver_id for receipt in batch.sent_receipts] == [allowed.driver_id]
    assert batch.failed_receipts[0].status == DeliveryStatus.DROPPED
    blocked.send_message.assert_not_awaited()
    allowed.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_unregistered_route_cannot_run_chat(policy):
    assert not await PlatformIOManager().is_target_allowed(RouteKey("qq"), chat_type="group", target_id="target")


@pytest.mark.asyncio
async def test_private_message_without_recipient_is_rejected(policy):
    manager = PlatformIOManager()
    driver = register_driver(manager, "main")
    outbound = message("private", "")
    batch = await manager.send_message(outbound, driver.descriptor.route_key)
    assert batch.receipts[0].metadata["adapter_policy"]["reason"] == "missing_target_id"
    driver.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_policy_raises_instead_of_sending(policy):
    policy.policy_path.write_text("[defaults.group\n", encoding="utf-8")
    manager = PlatformIOManager()
    driver = register_driver(manager, "main")
    with pytest.raises(ParseError):
        await manager.send_message(message("group", "target"), driver.descriptor.route_key)
    driver.send_message.assert_not_awaited()
