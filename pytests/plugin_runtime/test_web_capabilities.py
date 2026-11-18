"""插件联网授权、结构化返回与统一服务错误传播。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import asyncio

import pytest

from src.core.tooling import ToolExecutionResult
from src.plugin_runtime.capabilities import web
from src.plugin_runtime.host.authorization import AuthorizationManager
from src.plugin_runtime.host.capability_service import CapabilityService
from src.plugin_runtime.protocol.envelope import Envelope, MessageType
from src.web_search.config import WebRuntimeConfig
from src.web_search.service import WebSearchService


@pytest.fixture
def bridge(monkeypatch):
    config = WebRuntimeConfig(
        enabled=True, search="shared", fetch="shared", preview_chars=500,
        services={"shared": {"adapter": "stepsearch_mcp", "server_ref": "stepsearch"}},
    )
    mcp = SimpleNamespace(call_tool_invocation=AsyncMock())
    monkeypatch.setattr(web, "get_config", lambda: config)
    monkeypatch.setattr(web, "get_service", lambda: WebSearchService(mcp))
    authorization = AuthorizationManager()
    authorization.register_plugin("test", ["web.search", "web.fetch"])
    capabilities = CapabilityService(authorization)
    capabilities.register_capability("web.search", web.call)
    capabilities.register_capability("web.fetch", web.call)
    return config, mcp, authorization, capabilities


def request(capability, args):
    return Envelope(
        request_id=1, message_type=MessageType.REQUEST, plugin_id="test", method="cap.call",
        payload={"capability": capability, "args": args},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("capability,args,data", [
    ("web.search", {"query": " query ", "max_results": 2}, {
        "results": [{"title": "Title", "url": "https://example.com", "snippet": "Snippet", "time": "2026-10-26"}],
    }),
    ("web.fetch", {"url": "https://example.com"}, {
        "page": {"title": "Title", "url": "https://example.com", "markdown": "正文" * 3000},
    }),
])
async def test_authorized_calls_return_full_results_and_preserve_plugin_identity(bridge, capability, args, data):
    _, mcp, _, capabilities = bridge
    mcp.call_tool_invocation.return_value = ToolExecutionResult(
        tool_name="unused", success=True, structured_content=data,
    )
    response = await capabilities.handle_capability_request(request(capability, args))
    assert response.error is None
    payload = response.payload["result"]["result"]
    assert payload["service"] == "shared"
    assert payload["retrieved_at"].endswith("+00:00")
    assert "result_id" not in payload
    invocation = mcp.call_tool_invocation.await_args.args[0]
    assert invocation.metadata == {"plugin_id": "test", "mcp_server_name": "stepsearch"}
    assert invocation.session_id == ""
    if capability == "web.search":
        assert invocation.arguments == {"query": "query", "n": 2}
        assert payload["query"] == "query"
        assert payload["content_kind"] == "search_results"
        assert payload["results"][0]["published_at"] == "2026-10-26"
    else:
        assert payload["content_kind"] == "page_text"
        assert payload["content"] == data["page"]["markdown"]


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["web.search", "web.fetch"])
async def test_undeclared_capabilities_are_denied_before_network(bridge, capability):
    _, mcp, authorization, capabilities = bridge
    authorization.register_plugin("test", [])
    response = await capabilities.handle_capability_request(request(capability, {}))
    assert response.error["code"] == "E_CAPABILITY_DENIED"
    mcp.call_tool_invocation.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_service_is_not_used(bridge):
    config, mcp, _, capabilities = bridge
    config.enabled = False
    response = await capabilities.handle_capability_request(request("web.search", {"query": "query"}))
    assert "未启用" in response.error["message"]
    mcp.call_tool_invocation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("capability,args", [
    ("web.search", {"query": " "}),
    ("web.search", {"query": "query", "max_results": True}),
    ("web.search", {"query": "query", "max_results": 21}),
    ("web.search", {"query": "query", "api_key": "override"}),
    ("web.fetch", {"url": "file:///local"}),
    ("web.fetch", {"url": "https://example.com", "service": "override"}),
])
async def test_invalid_arguments_fail_before_network(bridge, capability, args):
    _, mcp, _, capabilities = bridge
    response = await capabilities.handle_capability_request(request(capability, args))
    assert response.error["code"] == "E_CAPABILITY_FAILED"
    mcp.call_tool_invocation.assert_not_awaited()


@pytest.mark.asyncio
async def test_upstream_failure_is_not_an_empty_result(bridge):
    _, mcp, _, capabilities = bridge
    mcp.call_tool_invocation.return_value = ToolExecutionResult(
        tool_name="web_search", success=False, error_message="private upstream response",
    )
    response = await capabilities.handle_capability_request(request("web.search", {"query": "query"}))
    assert "StepSearch MCP 调用失败" in response.error["message"]
    assert "private upstream response" not in response.error["message"]


@pytest.mark.asyncio
async def test_timeout_cancels_upstream_call(bridge):
    config, mcp, _, capabilities = bridge
    config.services["shared"].timeout = 0.01
    cancelled = asyncio.Event()

    async def slow(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    mcp.call_tool_invocation.side_effect = slow
    response = await capabilities.handle_capability_request(request("web.search", {"query": "query"}))
    assert "超过" in response.error["message"]
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_oversized_result_is_rejected_without_silent_truncation(bridge):
    _, mcp, _, capabilities = bridge
    mcp.call_tool_invocation.return_value = ToolExecutionResult(
        tool_name="web_fetch", success=True,
        structured_content={"page": {"title": "Title", "url": "https://example.com", "markdown": "x" * 500001}},
    )
    response = await capabilities.handle_capability_request(request("web.fetch", {"url": "https://example.com"}))
    assert "传输限制" in response.error["message"]


def test_runtime_registers_both_web_capabilities():
    from src.plugin_runtime.capabilities.registry import register_capability_impls

    capabilities = CapabilityService(AuthorizationManager())
    register_capability_impls(MagicMock(), SimpleNamespace(capability_service=capabilities))
    assert {"web.search", "web.fetch"} <= set(capabilities.list_capabilities())
