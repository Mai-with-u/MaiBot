from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.plugin_runtime.desktop_pet_buttons import (
    DesktopPetButtonSnapshot,
    invoke_button,
    read_button_declarations,
    read_button_snapshot,
)
from src.plugin_runtime.protocol.envelope import Envelope, MessageType, RegisterPluginPayload


class Provider:
    desktop_pet_buttons = [{"id": "volume", "label": "钢管音量", "icon": "volume"}]

    def __init__(self):
        self.disabled = False
        self.visible = True
        self.calls = []

    async def get_desktop_pet_button_states(self):
        return {"volume": {"visible": self.visible, "disabled": self.disabled, "text": "1%"}}

    async def on_desktop_pet_button_click(self, button_id):
        self.calls.append(button_id)


class PanelProvider(Provider):
    async def get_desktop_pet_panel(self, button_id):
        return {"title": "统计", "actions": [{"id": "refresh", "label": "刷新"},
                                             {"id": "disabled", "label": "禁用", "disabled": True}]}

    async def on_desktop_pet_button_click(self, button_id):
        return await self.get_desktop_pet_panel(button_id)

    async def on_desktop_pet_panel_action(self, button_id, action_id):
        self.calls.append((button_id, action_id))
        return await self.get_desktop_pet_panel(button_id)


@pytest.mark.asyncio
async def test_panel_actions_require_current_declared_enabled_action_and_button():
    provider = PanelProvider()
    snapshot = await read_button_snapshot(provider)
    assert (await invoke_button(provider, snapshot, "volume")).title == "统计"
    for action in ("missing", "disabled"):
        with pytest.raises(ValueError, match="面板操作"):
            await invoke_button(provider, snapshot, "volume", action)
    assert provider.calls == []
    await invoke_button(provider, snapshot, "volume", "refresh")
    assert provider.calls == [("volume", "refresh")]
    provider.visible = False
    with pytest.raises(ValueError, match="不可用"):
        await invoke_button(provider, await read_button_snapshot(provider), "volume", "refresh")


def test_panel_rejects_active_content_and_duplicate_actions():
    from src.plugin_runtime.desktop_pet_panels import DesktopPetPanel

    for content in ({"html": "<script>alert(1)</script>"}, {"image": "https://example.com/pic.png"},
                    {"actions": [{"id": "x", "label": "x"}] * 2}):
        with pytest.raises(ValueError):
            DesktopPetPanel.model_validate({"title": "test", **content})


@pytest.mark.asyncio
async def test_runner_returns_panel_and_dispatches_panel_action():
    from src.plugin_runtime.runner.runner_main import PluginRunner

    runner = PluginRunner("unused", "test", [])
    provider = PanelProvider()
    meta = SimpleNamespace(instance=provider, config_error="")
    runner._loader = SimpleNamespace(get_plugin=lambda plugin_id: meta)
    envelope = request(button_id="volume")
    response = await runner._handle_desktop_pet_buttons(envelope)
    assert response.error is None and response.payload["panel"]["title"] == "统计"
    envelope.payload["action_id"] = "refresh"
    response = await runner._handle_desktop_pet_buttons(envelope)
    assert response.error is None and provider.calls == [("volume", "refresh")]
    envelope.payload["action_id"] = "unregistered"
    assert (await runner._handle_desktop_pet_buttons(envelope)).error


@pytest.mark.asyncio
async def test_only_declared_visible_enabled_buttons_execute():
    provider = Provider()
    snapshot = await read_button_snapshot(provider)
    with pytest.raises(ValueError, match="未声明"):
        await invoke_button(provider, snapshot, "on_load")
    await invoke_button(provider, snapshot, "volume")
    assert provider.calls == ["volume"]
    for field in ("disabled", "visible"):
        provider.disabled, provider.visible = field == "disabled", field != "visible"
        snapshot = await read_button_snapshot(provider)
        with pytest.raises(ValueError, match="不可用"):
            await invoke_button(provider, snapshot, "volume")
    assert provider.calls == ["volume"]


def test_registration_rejects_duplicate_ids_and_active_content():
    buttons = Provider.desktop_pet_buttons
    with pytest.raises(ValueError, match="重复"):
        RegisterPluginPayload(plugin_id="one", desktop_pet_buttons=buttons * 2)
    with pytest.raises(ValueError):
        RegisterPluginPayload(plugin_id="one", desktop_pet_buttons=[{**buttons[0], "script": "alert(1)"}])
    with pytest.raises(ValueError):
        RegisterPluginPayload(plugin_id="one", desktop_pet_buttons=buttons * 11)
    assert read_button_declarations(object()) == []


@pytest.mark.parametrize("states", [{}, {"wrong": {}}, {"volume": {}, "extra": {}}])
def test_snapshot_requires_exact_states(states):
    with pytest.raises(ValueError):
        DesktopPetButtonSnapshot.model_validate({"buttons": Provider.desktop_pet_buttons, "states": states})


def request(plugin_id="test.pet", button_id=None, request_id=1):
    return Envelope(
        request_id=request_id, message_type=MessageType.REQUEST, plugin_id=plugin_id,
        method="plugin.desktop_pet_buttons", payload={"button_id": button_id}, timeout_ms=10000,
    )


@pytest.mark.asyncio
async def test_runner_routes_owner_and_rejects_repeated_action():
    from src.plugin_runtime.runner.runner_main import PluginRunner

    runner = PluginRunner("unused", "test", [])
    provider = Provider()
    meta = SimpleNamespace(instance=provider, config_error="")
    runner._loader = SimpleNamespace(get_plugin=lambda plugin_id: meta if plugin_id == "test.pet" else None)
    response = await runner._handle_desktop_pet_buttons(request())
    assert response.error is None and response.payload["states"]["volume"]["text"] == "1%"
    assert (await runner._handle_desktop_pet_buttons(request("other", "volume"))).error
    assert (await runner._handle_desktop_pet_buttons(request(button_id="on_load"))).error
    response = await runner._handle_desktop_pet_buttons(request(button_id="volume"))
    assert response.error is None and provider.calls == ["volume"]
    async with runner._desktop_pet_button_locks[("test.pet", "volume")]:
        assert (await runner._handle_desktop_pet_buttons(request(button_id="volume"))).error
    assert provider.calls == ["volume"] and not runner._inflight_rpcs


@pytest.mark.asyncio
async def test_supervisor_rejects_response_from_reloaded_plugin(monkeypatch):
    from src.plugin_runtime.host.supervisor import PluginSupervisor

    supervisor = PluginSupervisor.__new__(PluginSupervisor)
    monkeypatch.setattr(supervisor, "_ensure_accepting_runner_rpc", lambda: None)
    registration = RegisterPluginPayload(plugin_id="one", desktop_pet_buttons=Provider.desktop_pet_buttons)
    supervisor._registered_plugins = {"one": registration}
    snapshot = await read_button_snapshot(Provider())
    async def respond(*args, **kwargs):
        supervisor._registered_plugins.clear()
        return SimpleNamespace(error=None, payload=snapshot.model_dump())
    supervisor._rpc_server = SimpleNamespace(send_request=respond)
    with pytest.raises(ValueError, match="卸载或重载"):
        await supervisor.desktop_pet_buttons("one", "volume")
    assert supervisor.get_desktop_pet_button_plugins() == {}


@pytest.mark.asyncio
async def test_window_sync_removes_unloaded_plugin_buttons(monkeypatch):
    from src.desktop_pet.service import DesktopPetService
    from src.plugin_runtime import integration

    provider = Provider()
    snapshot = (await read_button_snapshot(provider)).model_dump()
    plugins = {"one": {"name": "One", "buttons": snapshot["buttons"]}}
    runtime = SimpleNamespace(get_desktop_pet_button_plugins=lambda: dict(plugins), desktop_pet_buttons=AsyncMock(return_value=snapshot))
    monkeypatch.setattr(integration, "get_plugin_runtime_manager", lambda: runtime)
    service = DesktopPetService()
    service._ready, service._closing = True, False
    command = AsyncMock()
    monkeypatch.setattr(service, "_command", command)
    await service._sync_buttons()
    assert command.await_args.args[0]["buttons"][0]["plugin_id"] == "one"
    plugins.clear()
    await service._sync_buttons()
    assert command.await_args.args[0] == {"type": "buttons", "buttons": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("action_id", [None, "refresh"])
async def test_panel_routes_through_service_manager_supervisor_and_runner(monkeypatch, action_id):
    from src.desktop_pet.service import DesktopPetService
    from src.plugin_runtime import integration
    from src.plugin_runtime.host.supervisor import PluginSupervisor
    from src.plugin_runtime.runner.runner_main import PluginRunner

    provider = PanelProvider()
    meta = SimpleNamespace(instance=provider, config_error="")
    runner = PluginRunner("unused", "test", [])
    runner._loader = SimpleNamespace(get_plugin=lambda plugin_id: meta)

    async def send_request(method, *, plugin_id, payload, timeout_ms):
        assert method == "plugin.desktop_pet_buttons"
        assert payload == {"button_id": "volume", "action_id": action_id}
        envelope = Envelope(request_id=1, message_type=MessageType.REQUEST, plugin_id=plugin_id,
                            method=method, payload=payload, timeout_ms=timeout_ms)
        return await runner._handle_desktop_pet_buttons(envelope)

    supervisor = PluginSupervisor.__new__(PluginSupervisor)
    monkeypatch.setattr(supervisor, "_ensure_accepting_runner_rpc", lambda: None)
    supervisor._registered_plugins = {
        "test.pet": RegisterPluginPayload(plugin_id="test.pet", desktop_pet_buttons=provider.desktop_pet_buttons),
    }
    supervisor._rpc_server = SimpleNamespace(send_request=send_request)
    manager = integration.PluginRuntimeManager.__new__(integration.PluginRuntimeManager)
    manager._started = True
    monkeypatch.setattr(manager, "_get_supervisor_for_plugin", lambda plugin_id: supervisor)
    monkeypatch.setattr(integration, "get_plugin_runtime_manager", lambda: manager)
    service = DesktopPetService()
    service._ready, service._closing = True, False
    command = AsyncMock()
    monkeypatch.setattr(service, "_command", command)
    monkeypatch.setattr(service, "_buttons_changed", lambda: None)

    await service._click_button("test.pet", "volume", action_id)

    result = command.await_args.args[0]
    assert result["error"] == ""
    assert result["panel"]["title"] == "统计"
    assert provider.calls == ([] if action_id is None else [("volume", "refresh")])
