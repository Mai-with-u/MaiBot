"""插件工具结束 Planner 返回协议测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncio
import pytest

from src.core.tooling import ToolExecutionContext, ToolInvocation
from src.files import service as file_service_module
from src.plugin_runtime.component_query import ComponentQueryService
from src.plugin_runtime.host.component_registry import ComponentRegistry, ComponentTypes, ToolEntry
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError


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


@pytest.fixture
def invocation_service(monkeypatch):
    """使用真实组件查询入口，仅替换 RPC 和文件权限服务。"""
    service = ComponentQueryService()
    registry = ComponentRegistry()
    registry.register_component("test_tool", "TOOL", "test_plugin", {})
    registry.register_component("restricted", "TOOL", "test_plugin", {"platforms": ["telegram"]})
    supervisor = SimpleNamespace(component_registry=registry, invoke_plugin=AsyncMock())
    monkeypatch.setattr(service, "_iter_supervisors", lambda: [supervisor])
    file_service = SimpleNamespace(enabled=False, revoke=Mock())
    monkeypatch.setattr(file_service_module, "get_file_service", lambda: file_service)
    return service, supervisor, file_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, unknown",
    [
        (RuntimeError("副作用后失去回执"), True),
        (RPCError(ErrorCode.E_TIMEOUT), True),
        (RPCError(ErrorCode.E_PLUGIN_CRASHED), True),
        (RPCError(ErrorCode.E_SHUTTING_DOWN), True),
        (RPCError(ErrorCode.E_UNKNOWN), True),
        (RPCError(ErrorCode.E_BAD_PAYLOAD), False),
        (RPCError(ErrorCode.E_PLUGIN_NOT_FOUND), False),
        (RPCError(ErrorCode.E_METHOD_NOT_ALLOWED), False),
        (RPCError(ErrorCode.E_BACK_PRESSURE), False),
    ],
)
async def test_invoke_tool_distinguishes_uncertain_rpc_failure_from_rejection(invocation_service, error, unknown):
    """断连和关停可能中断在途调用；明确的派发前拒绝不造成永久阻塞。"""
    service, supervisor, file_service = invocation_service
    supervisor.invoke_plugin.side_effect = error
    result = await service.invoke_tool_as_tool(ToolInvocation("test_tool"))
    assert result.success is False
    assert result.error_message == str(error)
    assert result.metadata == {"plugin_id": "test_plugin", **({"execution_state": "unknown"} if unknown else {})}
    supervisor.invoke_plugin.assert_awaited_once()
    file_service.revoke.assert_called_once_with("")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload, success, unknown",
    [
        ({"success": False, "result": "处理后异常"}, False, True),
        ({"success": False, "result": str(RPCError(ErrorCode.E_METHOD_NOT_ALLOWED))}, False, True),
        ({}, False, True),
        ({"success": True, "result": {"success": False, "message": "部分完成"}}, False, True),
        ({"success": True, "result": [False, "部分完成"]}, False, True),
        ({"success": True, "result": {"success": True, "stop_after_execution": "true"}}, False, True),
        ({"success": True, "result": {"success": True, "message": "完成"}}, True, False),
        ({"success": True, "result": [True, "完成"]}, True, False),
        ({"success": True, "result": "完成"}, True, False),
    ],
)
async def test_invoke_tool_keeps_uncertain_results_and_success_semantics(invocation_service, payload, success, unknown):
    """传输失败、插件业务失败和解析假阴性均不证明副作用已撤销。"""
    service, supervisor, file_service = invocation_service
    supervisor.invoke_plugin.return_value = SimpleNamespace(payload=payload)
    result = await service.invoke_tool_as_tool(ToolInvocation("test_tool"))
    assert result.success is success
    assert result.metadata == {"plugin_id": "test_plugin", **({"execution_state": "unknown"} if unknown else {})}
    assert result.structured_content == payload.get("result")
    supervisor.invoke_plugin.assert_awaited_once()
    file_service.revoke.assert_called_once_with("")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name, platform", [("missing", "test"), ("restricted", "qq"), ("restricted", ""), ("restricted", None)]
)
async def test_invoke_tool_preflight_failure_does_not_mark_unknown(invocation_service, tool_name, platform):
    """未找到工具和平台拒绝不会派发 RPC，也不能被当成执行未知。"""
    service, supervisor, _ = invocation_service
    context = ToolExecutionContext(platform=platform) if platform is not None else None
    result = await service.invoke_tool_as_tool(ToolInvocation(tool_name), context)
    assert result.success is False
    assert "execution_state" not in result.metadata
    supervisor.invoke_plugin.assert_not_awaited()


@pytest.mark.asyncio
async def test_invoke_tool_propagates_cancellation(invocation_service):
    """取消仍交由 Focus 闭合在途调用，不转成确定失败。"""
    service, supervisor, file_service = invocation_service
    supervisor.invoke_plugin.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await service.invoke_tool_as_tool(ToolInvocation("test_tool"))
    file_service.revoke.assert_called_once_with("")
