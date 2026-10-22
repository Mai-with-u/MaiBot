"""通过已有 MCPService 调用 StepSearch，按服务器名明确路由。"""

from dataclasses import replace
from typing import Any, Dict, Optional, Protocol
import json

from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation

from ..config import WebServiceConfig
from ..models import PageResult, SearchHit, SearchResult, WebSearchError


class MCPCaller(Protocol):
    async def call_tool_invocation(
        self,
        invocation: ToolInvocation,
        context: Optional[ToolExecutionContext] = None,
    ) -> ToolExecutionResult: ...


class StepSearchAdapter:
    def __init__(
        self,
        config: WebServiceConfig,
        mcp: MCPCaller,
        invocation: ToolInvocation,
        context: Optional[ToolExecutionContext],
    ) -> None:
        self.config = config
        self.mcp = mcp
        self.invocation = invocation
        self.context = context

    async def call(self, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        result = await self.mcp.call_tool_invocation(
            replace(
                self.invocation,
                tool_name=tool,
                arguments=arguments,
                metadata={**self.invocation.metadata, "mcp_server_name": self.config.server_ref},
            ),
            self.context,
        )
        if not result.success:
            raise WebSearchError("StepSearch MCP 调用失败，请检查该 MCP 连接的状态和日志")
        data = result.structured_content
        if data is None:
            # 较旧的 MCP 传输仅返回同一 JSON 的文本表示，不解析模型生成的文本。
            data = json.loads(result.get_history_content())
        if not isinstance(data, dict):
            raise WebSearchError("StepSearch MCP 返回格式错误，缺少结构化对象")
        if data.get("code", 0) != 0:
            raise WebSearchError("StepSearch MCP 返回业务错误")
        return data

    async def search(self, query: str, max_results: int) -> SearchResult:
        arguments: Dict[str, Any] = {"query": query, "n": max_results}
        if self.config.category:
            arguments["category"] = self.config.category
        data = await self.call("web_search", arguments)
        return SearchResult(
            query=query,
            results=[
                SearchHit(
                    title=item["title"],
                    url=item["url"],
                    snippet=item["snippet"],
                    published_at=item.get("time"),
                )
                for item in data["results"][:max_results]
            ],
        )

    async def fetch(self, url: str) -> PageResult:
        data = await self.call("web_fetch", {"url": url})
        page = data["page"]
        return PageResult(url=page["url"], title=page["title"], content=page["markdown"])
