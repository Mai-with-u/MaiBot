"""运行时控件的完整快照、操作白名单和配置隔离。"""

from typing import Any, Dict

import pytest

from src.plugin_runtime.config_widgets import ConfigWidgetSnapshot, invoke_widget_action, read_widget_snapshot


class Provider:
    def __init__(self):
        self.config = {"token": "unchanged"}
        self.calls = []
        self.disabled = False

    async def get_config_widget_state(self) -> Dict[str, Any]:
        return {"login": {"text": "等待扫码"}, "refresh": {"disabled": self.disabled}}

    async def on_config_widget_action(self, action_id: str) -> None:
        self.calls.append(action_id)


WIDGETS = [
    {"id": "login", "kind": "qrcode", "label": "扫码登录"},
    {"id": "refresh", "kind": "action", "label": "刷新"},
]


@pytest.mark.asyncio
async def test_action_whitelist_and_config_isolation():
    provider = Provider()
    snapshot = await read_widget_snapshot(provider, WIDGETS)
    for action_id in ["login", "__init__", "unknown"]:
        with pytest.raises(ValueError, match="未声明"):
            await invoke_widget_action(provider, snapshot, action_id)
    await invoke_widget_action(provider, snapshot, "refresh")
    assert provider.calls == ["refresh"]
    assert provider.config == {"token": "unchanged"}
    provider.disabled = True
    snapshot = await read_widget_snapshot(provider, WIDGETS)
    with pytest.raises(ValueError, match="不可用"):
        await invoke_widget_action(provider, snapshot, "refresh")
    assert provider.calls == ["refresh"]


@pytest.mark.parametrize("states", [{}, {"login": {}}, {"login": {}, "refresh": {}, "extra": {}}])
def test_reject_incomplete_or_undeclared_state(states):
    with pytest.raises(ValueError):
        ConfigWidgetSnapshot.model_validate({"widgets": WIDGETS, "states": states})


@pytest.mark.parametrize(
    "image", ["https://example.com/code.png", "javascript:alert(1)", "data:image/svg+xml;base64,AAAA"]
)
def test_reject_remote_or_active_image(image):
    with pytest.raises(ValueError):
        ConfigWidgetSnapshot.model_validate({"widgets": WIDGETS, "states": {"login": {"image": image}, "refresh": {}}})


def test_duplicate_widget_ids_rejected():
    with pytest.raises(ValueError, match="重复"):
        ConfigWidgetSnapshot.model_validate({"widgets": [WIDGETS[0], WIDGETS[0]], "states": {"login": {}}})


@pytest.mark.asyncio
async def test_runner_dispatch_rejects_missing_plugin_and_concurrent_action(monkeypatch):
    from types import SimpleNamespace

    from src.plugin_runtime.protocol.envelope import Envelope, MessageType
    from src.plugin_runtime.runner.runner_main import PluginRunner

    runner = PluginRunner("unused", "test", [])
    provider = Provider()
    meta = SimpleNamespace(instance=provider, config_error="")
    runner._loader = SimpleNamespace(get_plugin=lambda plugin_id: meta if plugin_id == "adapter.test" else None)
    monkeypatch.setattr(runner, "_get_plugin_config_schema", lambda _: {"runtime_widgets": WIDGETS})

    def request(plugin_id="adapter.test", action_id=None):
        return Envelope(
            request_id=1,
            message_type=MessageType.REQUEST,
            method="plugin.config_widgets",
            plugin_id=plugin_id,
            payload={"action_id": action_id},
        )

    response = await runner._handle_config_widgets(request())
    assert response.error is None
    assert response.payload["states"]["login"]["text"] == "等待扫码"
    response = await runner._handle_config_widgets(request(action_id="refresh"))
    assert response.error is None
    assert provider.calls == ["refresh"]
    response = await runner._handle_config_widgets(request(plugin_id="missing"))
    assert response.error
    response = await runner._handle_config_widgets(request(action_id="get_config_widget_state"))
    assert response.error
    lock = runner._config_widget_locks["adapter.test"]
    async with lock:
        response = await runner._handle_config_widgets(request(action_id="refresh"))
    assert response.error
    assert provider.calls == ["refresh"]
    assert not lock.locked()
