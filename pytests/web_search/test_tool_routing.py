"""重名工具添加来源前缀后仍能调用各自的原始实现。"""

from types import SimpleNamespace

import pytest

from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolRegistry, ToolSpec
from src.mcp_module.config import MCPClientRuntimeConfig
from src.mcp_module.manager import MCPManager
from src.mcp_module.provider import MCPToolProvider
from src.mcp_module.service import MCPService


class Provider:
    def __init__(self, name, kind, specs):
        self.provider_name, self.provider_type, self.specs = name, kind, specs
        self.calls = []

    async def list_tools(self, context=None):
        return self.specs

    async def invoke(self, invocation, context=None):
        self.calls.append((invocation, context))
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, content=self.provider_name)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_collision_keeps_native_tool_and_routes_prefixed_mcp_name():
    registry = ToolRegistry()
    web = Provider("web_search", "web", [ToolSpec(name="web_search")])
    raw_spec = ToolSpec(name="web_search", metadata={"server_name": "stepsearch"})
    mcp = Provider("mcp", "mcp", [raw_spec])
    registry.register_provider(web)
    registry.register_provider(mcp)
    assert [spec.name for spec in await registry.list_tools()] == ["web_search", "mcp_stepsearch_web_search"]
    assert raw_spec.name == "web_search" and "registry_original_name" not in raw_spec.metadata
    context = ToolExecutionContext(session_id="chat-a")
    invocation = ToolInvocation(
        tool_name="mcp_stepsearch_web_search",
        arguments={"query": "test"},
        call_id="c1",
        metadata={"mcp_server_name": "wrong-server"},
    )
    result = await registry.invoke(invocation, context)
    assert result.tool_name == "mcp_stepsearch_web_search" and result.content == "mcp"
    assert invocation.tool_name == "mcp_stepsearch_web_search"
    native, forwarded = mcp.calls[0]
    assert native.tool_name == "web_search" and native.call_id == "c1"
    assert native.metadata["mcp_server_name"] == "stepsearch" and forwarded is context
    assert not web.calls


@pytest.mark.asyncio
async def test_alias_collision_and_long_names_have_stable_bounded_names():
    registry = ToolRegistry()
    registry.register_provider(Provider("web", "web", [ToolSpec(name="search"), ToolSpec(name="mcp_step_search")]))
    registry.register_provider(
        Provider(
            "mcp",
            "mcp",
            [
                ToolSpec(name="search", metadata={"server_name": "step"}),
                ToolSpec(name="search", metadata={"server_name": "长名字" * 60}),
            ],
        )
    )
    first = [spec.name for spec in await registry.list_tools()]
    assert first == [spec.name for spec in await registry.list_tools()]
    assert len(set(first)) == 4 and all(len(name) <= 64 for name in first)


@pytest.mark.asyncio
async def test_multiple_mcp_servers_with_same_tool_are_both_callable():
    from mcp.types import Tool

    manager = MCPManager(MCPClientRuntimeConfig())
    calls = []

    def connection(server):
        async def call_tool(name, arguments):
            calls.append((server, name, arguments))
            return ToolExecutionResult(tool_name=name, success=True, content=server)

        return SimpleNamespace(tools=[Tool(name="web_search", inputSchema={"type": "object"})], call_tool=call_tool)

    for server in ("alpha", "beta"):
        manager._connections[server] = connection(server)
        manager._register_tools(server, manager._connections[server])
    service = MCPService()
    service._manager = manager
    registry = ToolRegistry()
    registry.register_provider(MCPToolProvider(service))
    names = [spec.name for spec in await registry.list_tools()]
    assert names == ["web_search", "mcp_beta_web_search"]
    result = await registry.invoke(ToolInvocation(tool_name="mcp_beta_web_search", arguments={"query": "test"}))
    assert result.success and result.content == "beta"
    assert calls == [("beta", "web_search", {"query": "test"})]
    result = await service.call_tool_invocation(
        ToolInvocation(
            tool_name="web_search",
            metadata={"mcp_server_name": "unknown"},
        )
    )
    assert not result.success and len(calls) == 1
