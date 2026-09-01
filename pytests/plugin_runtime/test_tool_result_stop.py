"""插件工具结束 Planner 返回协议测试。"""

import pytest

from src.core.tooling import ToolExecutionResult, ToolInvocation
from src.maisaka.utils.tool_record_payload import build_tool_record_payload
from src.plugin_runtime.component_query import ComponentQueryService
from src.plugin_runtime.host.component_registry import ComponentTypes, ToolEntry


def _build_tool_entry() -> ToolEntry:
    """构造最小插件工具条目。"""

    return ToolEntry(
        name="test_tool",
        component_type=ComponentTypes.TOOL.value,
        plugin_id="test_plugin",
        metadata={},
    )


@pytest.mark.parametrize("stop_after_execution", [True, False])
def test_parse_tool_result_maps_stop_after_execution(stop_after_execution: bool) -> None:
    result = ComponentQueryService._parse_tool_invoke_result(
        _build_tool_entry(),
        {
            "success": True,
            "message": "执行完成",
            "stop_after_execution": stop_after_execution,
        },
    )

    assert result.success is True
    assert result.stop_after_execution is stop_after_execution
    assert result.structured_content["stop_after_execution"] is stop_after_execution


def test_parse_tool_result_defaults_to_continue() -> None:
    result = ComponentQueryService._parse_tool_invoke_result(
        _build_tool_entry(),
        {"success": True, "message": "执行完成"},
    )

    assert result.success is True
    assert result.stop_after_execution is False


def test_parse_tool_result_rejects_non_boolean_stop_flag() -> None:
    result = ComponentQueryService._parse_tool_invoke_result(
        _build_tool_entry(),
        {"success": True, "stop_after_execution": "true"},
    )

    assert result.success is False
    assert result.stop_after_execution is False
    assert "必须为布尔值" in result.error_message


def test_tool_record_includes_stop_after_execution() -> None:
    payload = build_tool_record_payload(
        ToolInvocation(tool_name="test_tool", call_id="call-test"),
        ToolExecutionResult(tool_name="test_tool", success=True, stop_after_execution=True),
        None,
    )

    assert payload["stop_after_execution"] is True
