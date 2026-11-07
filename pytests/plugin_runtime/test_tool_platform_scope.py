from types import SimpleNamespace
from unittest.mock import AsyncMock

from maibot_sdk.components import Action, collect_components
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


@pytest.mark.asyncio
@pytest.mark.parametrize("declaration_style", ["action", "sdk_tool"])
async def test_legacy_action_registers_with_warning_and_invokes_as_tool(monkeypatch, declaration_style):
    if declaration_style == "sdk_tool":
        with pytest.warns(DeprecationWarning, match="Action"):
            class Plugin:
                @Action("greet", action_parameters={"name": "称呼"})
                async def greet(self, **kwargs):
                    return True, kwargs["action_data"]["name"]

        declaration = collect_components(Plugin())[0]
    else:
        declaration = {"type": "ACTION", "name": "greet", "metadata": {"action_parameters": {"name": "称呼"}}}

    registry = ComponentRegistry()
    warnings = []
    monkeypatch.setattr("src.plugin_runtime.host.component_registry.logger.warning", warnings.append)
    registry.register_plugin_components("test", [{**declaration, "component_type": declaration["type"]}])
    registry.register_component("native_tool", "TOOL", "test", {})
    assert len(warnings) == 1
    assert all(text in warnings[0] for text in ("test", "greet", "Action", "Tool"))
    assert [entry.name for entry in registry.get_components_by_type("ACTION")] == ["greet"]

    supervisor = SimpleNamespace(
        component_registry=registry,
        invoke_plugin=AsyncMock(return_value=SimpleNamespace(payload={"success": True, "result": [True, "已问候"]})),
    )
    service = ComponentQueryService()
    monkeypatch.setattr(service, "_iter_supervisors", lambda: [supervisor])
    spec = service.get_llm_available_tool_specs()["greet"]
    assert spec.parameters_schema["properties"]["name"] == {"type": "string", "description": "称呼"}

    context = ToolExecutionContext(stream_id="stream-1", reasoning="打招呼")
    result = await service.invoke_tool_as_tool(ToolInvocation("greet", {"name": "麦麦"}), context)
    assert result.success and result.content == "已问候"
    call = supervisor.invoke_plugin.await_args.kwargs
    assert call["method"] == "plugin.invoke_action"
    assert call["component_name"] == "greet"
    assert call["args"] == {
        "_file_scope": "",
        "name": "麦麦",
        "action_data": {"name": "麦麦"},
        "stream_id": "stream-1",
        "chat_id": "stream-1",
        "reasoning": "打招呼",
    }
    assert len(warnings) == 1


@pytest.mark.parametrize(
    "metadata",
    [{"legacy_action": True}, {"legacy_component_type": "ACTION"}, {"invoke_method": "plugin.invoke_command"}],
)
def test_rejects_invalid_tool_invocation_method(metadata):
    registry = ComponentRegistry()
    with pytest.raises(ComponentRegistrationError):
        registry.register_component("obsolete", "TOOL", "test", metadata)

    assert registry.get_stats()["total"] == 0
