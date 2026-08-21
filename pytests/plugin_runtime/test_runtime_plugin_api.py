from typing import Any, Dict, List, Optional

import pytest

from src.plugin_runtime.host.api_registry import APIRegistry
from src.plugin_runtime.integration import PluginRuntimeManager


class _FakeSupervisor:
    def __init__(self, registry: APIRegistry) -> None:
        self.api_registry = registry
        self._registered_plugins = {"example.first": object()}
        self.invocations: List[Dict[str, Any]] = []

    def get_loaded_plugin_ids(self) -> List[str]:
        return ["example.first"]

    async def invoke_api(
        self,
        plugin_id: str,
        component_name: str,
        args: Optional[Dict[str, Any]] = None,
        timeout_ms: int = 30000,
    ) -> Dict[str, Any]:
        self.invocations.append(
            {
                "plugin_id": plugin_id,
                "component_name": component_name,
                "args": args,
                "timeout_ms": timeout_ms,
            }
        )
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_invoke_api_routes_to_the_plugin_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = PluginRuntimeManager()
    registry = APIRegistry()
    registry.register_api("get_status", "example.first", {"enabled": True})
    supervisor = _FakeSupervisor(registry)

    monkeypatch.setattr(manager, "_builtin_supervisor", supervisor)

    result = await manager.invoke_api(
        "example.first",
        "get_status",
        {"verbose": True},
        1500,
    )

    assert result == {"status": "ok"}
    assert supervisor.invocations == [
        {
            "plugin_id": "example.first",
            "component_name": "get_status",
            "args": {"verbose": True},
            "timeout_ms": 1500,
        }
    ]
    assert manager.get_plugin_api("example.first", "get_status") is not None


def test_get_plugin_api_hides_disabled_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = PluginRuntimeManager()
    registry = APIRegistry()
    registry.register_api("get_status", "example.first", {"enabled": False})
    supervisor = _FakeSupervisor(registry)

    monkeypatch.setattr(manager, "_builtin_supervisor", supervisor)

    assert manager.get_plugin_api("example.first", "get_status") is None
    assert manager.get_plugin_api("example.first", "get_status", enabled_only=False) is not None
