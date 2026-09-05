"""锁住 host 侧 ACTION/action → TOOL 映射与 legacy 字段。"""

import pytest

from src.plugin_runtime.capabilities.components import RuntimeComponentCapabilityMixin
from src.plugin_runtime.host.component_registry import (
    ComponentRegistry,
    ComponentTypes,
    ToolEntry,
    normalize_component_type,
)


@pytest.mark.parametrize("raw_type", ["ACTION", "action", "Action"])
def test_normalize_component_type_maps_action_to_tool(raw_type: str) -> None:
    assert normalize_component_type(raw_type) == "TOOL"
    assert RuntimeComponentCapabilityMixin._normalize_component_type(raw_type) == "TOOL"
    assert ComponentRegistry._normalize_component_type(raw_type) == ComponentTypes.TOOL


@pytest.mark.parametrize("raw_type", ["ACTION", "action"])
def test_register_action_stores_tool_with_legacy_fields(raw_type: str) -> None:
    registry = ComponentRegistry()
    registry.register_component(
        name="wave",
        component_type=raw_type,
        plugin_id="demo.plugin",
        metadata={
            "description": "挥手",
            "action_parameters": {"target": "对象"},
        },
    )

    component = registry.get_component("demo.plugin.wave")
    assert isinstance(component, ToolEntry)
    assert component.component_type == ComponentTypes.TOOL
    assert component.legacy_component_type == "ACTION"
    assert component.metadata["legacy_component_type"] == "ACTION"
    assert component.metadata["legacy_action"] is True
    assert component.invoke_method == "plugin.invoke_action"
    assert registry.get_components_by_type("ACTION", enabled_only=False) == [component]
    assert component in registry.get_components_by_type("TOOL", enabled_only=False)


def test_register_native_tool_does_not_set_legacy_action_fields() -> None:
    registry = ComponentRegistry()
    registry.register_component(
        name="search",
        component_type="TOOL",
        plugin_id="demo.plugin",
        metadata={"description": "搜索"},
    )

    component = registry.get_component("demo.plugin.search")
    assert isinstance(component, ToolEntry)
    assert component.component_type == ComponentTypes.TOOL
    assert component.legacy_component_type == ""
    assert "legacy_action" not in component.metadata
    assert component.invoke_method == "plugin.invoke_tool"
    assert registry.get_components_by_type("ACTION", enabled_only=False) == []
    assert registry.get_components_by_type("TOOL", enabled_only=False) == [component]
