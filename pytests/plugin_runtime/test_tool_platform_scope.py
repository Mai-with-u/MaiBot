from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from maibot_sdk.components import Action, ReplyExtension, Tool, collect_components
import pytest

from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolInvocation
from src.plugin_runtime.component_query import ComponentQueryService
from src.plugin_runtime.host.component_registry import ComponentRegistrationError, ComponentRegistry
from src.plugin_runtime.protocol.envelope import ComponentDeclaration, RegisterPluginPayload
from src.plugin_runtime.runner.plugin_loader import PluginMeta
from src.plugin_runtime.runner.runner_main import PluginRunner


def registry(platforms):
    result = ComponentRegistry()
    result.register_component(
        "restricted", "TOOL", "test", {}, platforms=ComponentDeclaration.normalize_platforms(platforms),
    )
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
    with pytest.raises(ValueError, match="platforms"):
        ComponentDeclaration(name="invalid", component_type="TOOL", plugin_id="test", platforms=invalid)


def test_platform_normalization_and_inventory():
    entries = registry([" Telegram ", "TELEGRAM", "qq"]).get_components_by_type("TOOL")
    restricted = next(entry for entry in entries if entry.name == "restricted")
    assert restricted.platforms == {"telegram", "qq"}
    assert registry("telegram").get_components_by_type("TOOL", platform=" TELEGRAM ")


def test_platform_scope_combines_with_chat_and_session_scope():
    result = ComponentRegistry()
    result.register_component(
        "group_tool", "TOOL", "test", {},
        chat_scope="group", allowed_session=["session-1"], platforms=["telegram", "qq"],
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


def _plugin_meta(instance, tmp_path):
    manifest = SimpleNamespace(
        name="Test", version="1.0.0", plugin_type="extension", capabilities=[], plugin_dependency_ids=[],
        llm_provider_client_types=[],
    )
    return PluginMeta("test.scope", "test.scope", str(tmp_path), instance, manifest)


@pytest.mark.asyncio
@pytest.mark.parametrize("declaration_style", ["sdk", "legacy_metadata", "top_level"])
async def test_tool_scopes_survive_registration_and_control_invocation(monkeypatch, tmp_path, declaration_style):
    class Plugin:
        def get_components(self):
            if declaration_style == "sdk":
                return collect_components(self)
            scope = {"platforms": " QQ ", "chat_scope": " Group ", "allowed_session": [" session-1 "]}
            if declaration_style == "legacy_metadata":
                return [{"name": "group_tool", "type": "TOOL", "metadata": scope}]
            return [{
                "name": "group_tool", "type": "TOOL", **scope,
                "metadata": {"platforms": ["telegram"], "chat_scope": "private", "allowed_session": ["session-2"]},
            }]

        @Tool(
            "group_tool", platforms="qq", chat_scope="group", allowed_session=["session-1"],
            parameters={"message_id": {"type": "string"}},
        )
        async def group_tool(self):
            return {"success": True}

    meta = _plugin_meta(Plugin(), tmp_path)
    runner = PluginRunner("unused", "test", [])
    payload = await runner._build_plugin_registration_payload(meta)
    payload = RegisterPluginPayload.model_validate_json(payload.model_dump_json())
    component = payload.components[0]
    assert component.platforms == ["qq"]
    assert component.chat_scope == "group"
    assert component.allowed_session == ["session-1"]
    assert {"platforms", "chat_scope", "allowed_session"}.isdisjoint(component.metadata)
    registered = ComponentRegistry()
    registered.register_plugin_components(meta.plugin_id, payload.model_dump()["components"])
    service = ComponentQueryService()
    supervisor = SimpleNamespace(
        component_registry=registered,
        invoke_plugin=AsyncMock(return_value=SimpleNamespace(payload={"success": True, "result": {"success": True}})),
    )
    monkeypatch.setattr(service, "_iter_supervisors", lambda: [supervisor])
    monkeypatch.setattr("src.files.service.get_file_service", lambda: SimpleNamespace(enabled=False, revoke=Mock()))

    for platform, is_group_chat, session_id, expected in [
        ("qq", False, "session-1", False),
        ("qq", None, "session-1", False),
        ("qq", True, "session-2", False),
        ("telegram", True, "session-1", False),
        ("qq", True, "session-1", True),
    ]:
        availability = ToolAvailabilityContext(platform=platform, is_group_chat=is_group_chat, session_id=session_id)
        specs = service.get_llm_available_tool_specs(availability)
        assert ("group_tool" in specs) == expected
        if expected and declaration_style == "sdk":
            assert set(specs["group_tool"].parameters_schema["properties"]) == {"message_id"}
        context = ToolExecutionContext(platform=platform, is_group_chat=is_group_chat, session_id=session_id)
        result = await service.invoke_tool_as_tool(ToolInvocation("group_tool"), context)
        assert result.success == expected
    supervisor.invoke_plugin.assert_awaited_once()


@pytest.mark.asyncio
async def test_reply_extension_scopes_survive_runner_registration(tmp_path):
    class Plugin:
        def get_components(self):
            return collect_components(self)

        @ReplyExtension("voice", platforms="qq", chat_scope="private", allowed_session=["session-1"])
        async def voice(self, **kwargs):
            return {}

    meta = _plugin_meta(Plugin(), tmp_path)
    payload = await PluginRunner("unused", "test", [])._build_plugin_registration_payload(meta)
    registered = ComponentRegistry()
    registered.register_plugin_components(meta.plugin_id, payload.model_dump()["components"])

    for platform, group, session, expected in [
        ("qq", False, "session-1", True),
        ("telegram", False, "session-1", False),
        ("qq", True, "session-1", False),
        ("qq", False, "session-2", False),
    ]:
        assert bool(registered.get_components_by_type(
            "REPLY_EXTENSION", platform=platform, is_group_chat=group, session_id=session,
        )) == expected


@pytest.mark.asyncio
async def test_explicit_unrestricted_scope_takes_precedence_over_legacy_metadata(tmp_path):
    declaration = {
        "name": "tool", "type": "TOOL",
        "platforms": [], "chat_scope": "all", "allowed_session": [],
        "metadata": {"platforms": ["qq"], "chat_scope": "group", "allowed_session": ["session-1"]},
    }
    meta = _plugin_meta(SimpleNamespace(get_components=lambda: [declaration]), tmp_path)
    payload = await PluginRunner("unused", "test", [])._build_plugin_registration_payload(meta)
    component = payload.components[0]
    assert (component.platforms, component.chat_scope, component.allowed_session) == ([], "all", [])


@pytest.mark.asyncio
@pytest.mark.parametrize("field,invalid", [
    ("platforms", [""]),
    ("chat_scope", "direct"),
    ("allowed_session", [123]),
])
@pytest.mark.parametrize("legacy", [False, True])
async def test_runner_rejects_invalid_scope(tmp_path, field, invalid, legacy):
    declaration = {
        "name": "tool", "type": "TOOL",
        "metadata": {"platforms": ["qq"], "chat_scope": "group", "allowed_session": ["session-1"]},
    }
    (declaration["metadata"] if legacy else declaration)[field] = invalid
    meta = _plugin_meta(SimpleNamespace(get_components=lambda: [declaration]), tmp_path)
    with pytest.raises(ValueError, match=field):
        await PluginRunner("unused", "test", [])._build_plugin_registration_payload(meta)


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
