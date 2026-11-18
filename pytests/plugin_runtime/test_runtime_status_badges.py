from types import SimpleNamespace
from unittest.mock import Mock

import asyncio
import time

import pytest

from src.plugin_runtime.capabilities.components import RuntimeComponentCapabilityMixin
from src.plugin_runtime.host.status_badges import RuntimeStatusBadges
from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor
from src.plugin_runtime.integration import PluginRuntimeManager
from src.plugin_runtime.plugin_catalog import PluginCatalogEntry
from src.webui.routers.plugin.management import _collect_installed_plugins


@pytest.mark.asyncio
async def test_badge_expiry_pushes_change_without_reads_and_renewal_cancels_old_deadline():
    changed = asyncio.Event()
    store = RuntimeStatusBadges(changed.set)
    store.update("example.adapter", [{"id": "mode", "label": "查询模式", "expires_at": time.time() + 60}])
    previous_timer = store._timer
    store.update("example.adapter", [{"id": "mode", "label": "查询模式", "expires_at": time.time() + 120}])
    assert previous_timer.cancelled()
    assert store.snapshot()["example.adapter"][0]["label"] == "查询模式"
    store.update("example.adapter", [{"id": "mode", "label": "查询模式", "expires_at": time.time() + 0.01}])
    changed.clear()
    await asyncio.wait_for(changed.wait(), 1)
    assert not store.snapshot()
    assert store._timer is None


@pytest.mark.asyncio
async def test_release_disconnect_and_snapshot_copies_cannot_leave_stale_badges():
    store = RuntimeStatusBadges(Mock())
    badge = {"id": "mode", "label": "只读", "expires_at": time.time() + 60}
    store.update("example.adapter", [badge])
    store.snapshot()["example.adapter"][0]["label"] = "篡改"
    assert store.snapshot()["example.adapter"][0]["label"] == "只读"
    store.update("example.adapter", [])
    assert not store.snapshot() and store._timer is None
    store.update("example.adapter", [badge])
    store.remove("example.adapter")
    assert not store.snapshot() and store._timer is None
    supervisor = PluginRunnerSupervisor(plugin_dirs=[])
    supervisor.runtime_status_badges.update("example.adapter", [badge])
    supervisor._handle_usage_connection_lost()
    assert not supervisor.runtime_status_badges.snapshot()
    supervisor.runtime_status_badges.update("example.adapter", [badge])
    supervisor._apply_plugin_unload_result(["example.adapter"])
    assert not supervisor.runtime_status_badges.snapshot()
    supervisor.runtime_status_badges.update("example.adapter", [badge])
    supervisor._clear_runner_state()
    assert not supervisor.runtime_status_badges.snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize("badges", [
    {}, [{}], [{"id": "mode", "label": ""}], [{"id": "mode", "label": "x" * 65}],
    [{"id": "mode", "label": "状态", "expires_at": float("nan")}],
    [{"id": "mode", "label": "状态", "expires_at": time.time() + 172800}],
    [{"id": "mode", "label": "状态"}] * 2,
    [{"id": str(i), "label": "状态"} for i in range(9)],
])
async def test_invalid_badges_do_not_replace_existing_state(badges):
    store = RuntimeStatusBadges(Mock())
    store.update("example.adapter", [{"id": "valid", "label": "有效"}])
    with pytest.raises(ValueError):
        store.update("example.adapter", badges)
    assert store.snapshot()["example.adapter"] == [{"id": "valid", "label": "有效", "expires_at": None}]


@pytest.mark.asyncio
async def test_status_capability_is_owned_by_caller_and_only_loaded_plugins_can_publish():
    store = RuntimeStatusBadges(Mock())
    supervisor = SimpleNamespace(get_plugin_load_statuses=lambda: {"example.adapter": "success"}, runtime_status_badges=store)
    manager = SimpleNamespace(_get_supervisor_for_plugin=lambda plugin_id: supervisor)
    await RuntimeComponentCapabilityMixin._cap_runtime_set_status_badges(manager, "example.adapter", "runtime.set_status_badges", {
        "plugin_id": "somebody.else", "badges": [{"id": "mode", "label": "任意适配器模式"}],
    })
    assert set(store.snapshot()) == {"example.adapter"}
    with pytest.raises(ValueError, match="已加载"):
        await RuntimeComponentCapabilityMixin._cap_runtime_set_status_badges(manager, "not.loaded", "runtime.set_status_badges", {})


@pytest.mark.asyncio
async def test_badges_flow_to_installed_list_only_for_running_plugins(tmp_path, monkeypatch):
    manager = PluginRuntimeManager()
    manager._started = True
    supervisor = PluginRunnerSupervisor(plugin_dirs=[])
    manager._third_party_supervisor = supervisor
    supervisor.runtime_status_badges.update("example.adapter", [{"id": "mode", "label": "只读"}])
    manager._plugin_catalog._entries[tmp_path] = PluginCatalogEntry(
        "example.adapter", tmp_path, {"plugin_type": "adapter"}, False, True, "", None,
    )
    manager._plugin_catalog._initialized = True
    monkeypatch.setattr(manager, "get_plugin_load_statuses", lambda: {"example.adapter": "success"})
    result = _collect_installed_plugins(await manager.get_plugin_state_snapshot())
    assert result["plugins"][0]["status_badges"][0]["label"] == "只读"
    monkeypatch.setattr(manager, "get_plugin_load_statuses", lambda: {"example.adapter": "offline"})
    result = _collect_installed_plugins(await manager.get_plugin_state_snapshot())
    assert result["plugins"][0]["status_badges"] == []
    supervisor.runtime_status_badges.clear()
