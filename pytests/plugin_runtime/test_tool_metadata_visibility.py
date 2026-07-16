"""覆盖插件工具 metadata 嵌套导致 core_tool 失效的问题。"""

from src.plugin_runtime.component_query import ComponentQueryService
from src.plugin_runtime.host.component_registry import _flatten_extension_metadata


class _FakeToolEntry:
    def __init__(self, metadata: dict) -> None:
        self.metadata = metadata


def test_flatten_extension_metadata_promotes_nested_core_tool() -> None:
    flattened = _flatten_extension_metadata(
        {
            "description": "demo",
            "handler_name": "demo_tool",
            "metadata": {"core_tool": True},
        }
    )
    assert flattened["core_tool"] is True
    assert flattened["description"] == "demo"
    assert "metadata" not in flattened


def test_flatten_extension_metadata_keeps_top_level_priority() -> None:
    flattened = _flatten_extension_metadata(
        {
            "core_tool": False,
            "metadata": {"core_tool": True},
        }
    )
    assert flattened["core_tool"] is False


def test_get_tool_visibility_reads_nested_core_tool() -> None:
    entry = _FakeToolEntry({"metadata": {"core_tool": True}})
    assert ComponentQueryService._get_tool_visibility(entry) == "visible"


def test_get_tool_visibility_reads_top_level_core_tool() -> None:
    entry = _FakeToolEntry({"core_tool": True})
    assert ComponentQueryService._get_tool_visibility(entry) == "visible"


def test_get_tool_visibility_defaults_to_deferred() -> None:
    entry = _FakeToolEntry({})
    assert ComponentQueryService._get_tool_visibility(entry) == "deferred"
