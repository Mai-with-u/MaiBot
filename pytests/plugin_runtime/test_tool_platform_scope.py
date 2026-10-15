from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolInvocation
from src.plugin_runtime.component_query import ComponentQueryService
from src.plugin_runtime.host.component_registry import ComponentRegistrationError, ComponentRegistry


def registry(platforms):
    result = ComponentRegistry()
    result.register_component("restricted", "TOOL", "test", {"platforms": platforms})
    result.register_component("unlimited", "TOOL", "test", {})
    return result


@pytest.mark.parametrize("declaration", ["telegram", ["telegram", "qq"], None, []])
@pytest.mark.parametrize("platform", ["telegram", "qq", "webui", ""])
def test_tool_listing_filters_platform(declaration, platform):
    declared = {declaration} if isinstance(declaration, str) else set(declaration or [])
    names = {entry.name for entry in registry(declaration).get_components_by_type("TOOL", platform=platform)}
    assert "unlimited" in names
    assert ("restricted" in names) == (not declared or platform in declared)


@pytest.mark.parametrize("invalid", [1, True, {}, "", " ", ["telegram", None], [""], [2]])
def test_invalid_declarations_fail_registration(invalid):
    with pytest.raises(ComponentRegistrationError, match="platforms"):
        registry(invalid)


def test_platform_normalization_and_inventory():
    entries = registry([" Telegram ", "TELEGRAM", "qq"]).get_components_by_type("TOOL")
    restricted = next(entry for entry in entries if entry.name == "restricted")
    assert restricted.platforms == {"telegram", "qq"}
    assert registry("telegram").get_components_by_type("TOOL", platform=" TELEGRAM ")


def test_platform_scope_combines_with_chat_and_session_scope():
    result = ComponentRegistry()
    result.register_component(
        "group_tool", "TOOL", "test", {"platforms": ["telegram", "qq"]},
        chat_scope="group", allowed_session=["session-1"],
    )
    for platform, group, session, expected in [
        ("telegram", True, "session-1", True),
        ("qq", True, "session-1", True),
        ("webui", True, "session-1", False),
        ("telegram", False, "session-1", False),
        ("telegram", True, "session-2", False),
    ]:
        assert bool(result.get_components_by_type(
            "TOOL", platform=platform, is_group_chat=group, session_id=session
        )) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", [None, "", "qq", "telegram"])
async def test_query_and_execution_enforce_scope(monkeypatch, platform):
    service = ComponentQueryService()
    supervisor = SimpleNamespace(
        component_registry=registry("telegram"),
        invoke_plugin=AsyncMock(return_value=SimpleNamespace(payload={"success": True, "result": {"success": True}})),
    )
    monkeypatch.setattr(service, "_iter_supervisors", lambda: [supervisor])
    availability = ToolAvailabilityContext(platform=platform) if platform is not None else None
    specs = service.get_llm_available_tool_specs(context=availability)
    assert ("restricted" in specs) == (platform in {None, "telegram"})
    context = ToolExecutionContext(platform=platform) if platform is not None else None
    result = await service.invoke_tool_as_tool(ToolInvocation("restricted"), context)
    assert result.success == (platform == "telegram")
    assert supervisor.invoke_plugin.await_count == (1 if platform == "telegram" else 0)
