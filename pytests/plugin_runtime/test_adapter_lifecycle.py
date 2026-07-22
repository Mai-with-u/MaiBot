from types import MethodType
from typing import List

import asyncio

from src.plugin_runtime.integration import PluginRuntimeManager
from src.plugin_runtime.protocol.envelope import UnloadPluginsResultPayload


class _FakeSupervisor:
    def __init__(self) -> None:
        self.loaded_plugin_ids = {"adapter.qq", "extension.demo"}

    def get_loaded_plugin_ids_by_type(self, plugin_type: str) -> List[str]:
        assert plugin_type == "adapter"
        return sorted(
            plugin_id
            for plugin_id in self.loaded_plugin_ids
            if plugin_id.startswith("adapter.")
        )

    async def unload_plugins(
        self,
        plugin_ids: List[str],
        reason: str = "manual",
    ) -> UnloadPluginsResultPayload:
        assert reason == "local_operator_offline"
        self.loaded_plugin_ids.difference_update(plugin_ids)
        return UnloadPluginsResultPayload(
            success=True,
            requested_plugin_ids=plugin_ids,
            unloaded_plugins=plugin_ids,
            failed_plugins={},
        )


def _build_runtime_manager(supervisor: _FakeSupervisor) -> PluginRuntimeManager:
    manager = object.__new__(PluginRuntimeManager)
    manager._started = True
    manager._adapter_transition_lock = asyncio.Lock()
    manager._offline_adapter_plugin_ids = set()
    manager._builtin_supervisor = supervisor
    manager._third_party_supervisor = None

    async def reload_plugins(
        runtime_manager: PluginRuntimeManager,
        plugin_ids: List[str],
        reason: str = "manual",
    ) -> bool:
        assert reason == "local_operator_online"
        supervisor.loaded_plugin_ids.update(plugin_ids)
        return True

    manager.reload_plugins_globally = MethodType(reload_plugins, manager)
    return manager


def test_adapter_plugins_can_be_taken_offline_and_restored_exactly() -> None:
    supervisor = _FakeSupervisor()
    manager = _build_runtime_manager(supervisor)

    offline_result = asyncio.run(manager.take_adapters_offline())

    assert offline_result.success is True
    assert offline_result.changed_plugin_ids == ["adapter.qq"]
    assert supervisor.loaded_plugin_ids == {"extension.demo"}

    repeated_result = asyncio.run(manager.take_adapters_offline())

    assert repeated_result.success is True
    assert repeated_result.changed_plugin_ids == []
    assert repeated_result.pending_plugin_ids == ["adapter.qq"]

    online_result = asyncio.run(manager.bring_adapters_online())

    assert online_result.success is True
    assert online_result.changed_plugin_ids == ["adapter.qq"]
    assert supervisor.loaded_plugin_ids == {"adapter.qq", "extension.demo"}
    assert manager._offline_adapter_plugin_ids == set()
