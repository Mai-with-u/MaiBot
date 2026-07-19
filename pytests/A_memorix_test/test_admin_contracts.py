from __future__ import annotations

from typing import Any

import pytest

from src.A_memorix.core.runtime.admin_contracts import (
    ADMIN_COMPONENT_NAMES,
    AdminContractError,
    dispatch_admin_command,
    parse_admin_command,
)


class _DispatchKernel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def memory_graph_admin(self, *, action: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("memory_graph_admin", action, kwargs)

    async def memory_source_admin(self, *, action: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("memory_source_admin", action, kwargs)

    async def memory_episode_admin(self, *, action: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("memory_episode_admin", action, kwargs)

    async def memory_profile_admin(self, *, action: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("memory_profile_admin", action, kwargs)

    async def memory_feedback_admin(self, *, action: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("memory_feedback_admin", action, kwargs)

    async def memory_runtime_admin(self, *, action: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("memory_runtime_admin", action, kwargs)

    async def memory_import_admin(self, *, action: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("memory_import_admin", action, kwargs)

    async def memory_tuning_admin(self, *, action: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("memory_tuning_admin", action, kwargs)

    async def memory_v5_admin(self, *, action: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("memory_v5_admin", action, kwargs)

    async def memory_delete_admin(self, *, action: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("memory_delete_admin", action, kwargs)

    async def memory_correction_admin(self, *, action: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("memory_correction_admin", action, kwargs)

    async def memory_fuzzy_modify_admin(self, *, action: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("memory_fuzzy_modify_admin", action, kwargs)

    def _record(self, component_name: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((component_name, action, payload))
        return {"success": True, "component_name": component_name, "action": action, "payload": payload}


def test_parse_admin_command_normalizes_action_and_removes_action_from_payload() -> None:
    command = parse_admin_command(" memory_runtime_admin ", {"action": " GET_CONFIG ", "limit": 3})

    assert command.component_name == "memory_runtime_admin"
    assert command.action == "get_config"
    assert command.payload == {"limit": 3}


@pytest.mark.parametrize(
    ("component_name", "payload", "message"),
    [
        ("unknown_admin", {"action": "get"}, "不支持的 A_Memorix admin component"),
        ("memory_runtime_admin", {}, "action 不能为空"),
        ("memory_runtime_admin", {"action": "missing"}, "不支持的 memory_runtime_admin action"),
    ],
)
def test_parse_admin_command_rejects_invalid_contracts(
    component_name: str,
    payload: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(AdminContractError) as exc_info:
        parse_admin_command(component_name, payload)

    assert message in exc_info.value.message
    assert exc_info.value.to_response()["success"] is False


def test_fuzzy_modify_admin_uses_correction_action_set() -> None:
    command = parse_admin_command("memory_fuzzy_modify_admin", {"action": "preview", "plan_id": "fuzzy-1"})

    assert command.component_name == "memory_fuzzy_modify_admin"
    assert command.action == "preview"
    assert command.payload == {"plan_id": "fuzzy-1"}

    with pytest.raises(AdminContractError):
        parse_admin_command("memory_fuzzy_modify_admin", {"action": "correct_evidence"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("component_name", "action"),
    [
        ("memory_graph_admin", "get_graph"),
        ("memory_source_admin", "list"),
        ("memory_episode_admin", "status"),
        ("memory_episode_admin", "process_sources"),
        ("memory_profile_admin", "query"),
        ("memory_feedback_admin", "list"),
        ("memory_runtime_admin", "get_config"),
        ("memory_import_admin", "settings"),
        ("memory_tuning_admin", "settings"),
        ("memory_v5_admin", "status"),
        ("memory_delete_admin", "preview"),
        ("memory_correction_admin", "preview"),
        ("memory_fuzzy_modify_admin", "preview"),
    ],
)
async def test_dispatch_admin_command_uses_explicit_registered_dispatcher(
    component_name: str,
    action: str,
) -> None:
    kernel = _DispatchKernel()
    command = parse_admin_command(component_name, {"action": action, "marker": "contract"})

    result = await dispatch_admin_command(kernel, command)

    assert component_name in ADMIN_COMPONENT_NAMES
    assert result == {
        "success": True,
        "component_name": component_name,
        "action": action,
        "payload": {"marker": "contract"},
    }
    assert kernel.calls == [(component_name, action, {"marker": "contract"})]


@pytest.mark.asyncio
async def test_legacy_plugin_admin_dispatch_uses_contract_before_kernel_start() -> None:
    from src.A_memorix.plugin import AMemorixPlugin

    plugin = object.__new__(AMemorixPlugin)
    kernel = _DispatchKernel()
    started = False

    async def fake_get_kernel() -> _DispatchKernel:
        nonlocal started
        started = True
        return kernel

    plugin._get_kernel = fake_get_kernel  # type: ignore[method-assign]

    invalid = await AMemorixPlugin._dispatch_admin_tool(plugin, "unknown_admin", action="get")
    assert invalid["success"] is False
    assert started is False

    valid = await AMemorixPlugin._dispatch_admin_tool(
        plugin,
        "memory_runtime_admin",
        action="get_config",
        marker="plugin",
    )

    assert started is True
    assert valid == {
        "success": True,
        "component_name": "memory_runtime_admin",
        "action": "get_config",
        "payload": {"marker": "plugin"},
    }
    assert kernel.calls == [("memory_runtime_admin", "get_config", {"marker": "plugin"})]
