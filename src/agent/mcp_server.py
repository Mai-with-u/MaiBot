"""向外部 Agent 提供麦麦工具；临时 HTTP 端口仅监听本机并使用独立令牌。"""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

from mcp import types
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

import asyncio
import secrets
import socket
import uvicorn

from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolInvocation

if TYPE_CHECKING:
    from .acp_agent import AcpAgent


class AgentMCPServer:
    def __init__(self, agent: "AcpAgent") -> None:
        self.agent = agent
        self.token = secrets.token_urlsafe(32)
        self.url = ""
        self.server: Optional[uvicorn.Server] = None
        self.task: Optional[asyncio.Task[None]] = None

    async def start(self) -> Dict[str, Any]:
        from .provider import AgentToolProvider

        provider = AgentToolProvider(self.agent.service)
        server = Server("maibot-agent")

        @server.list_tools()
        async def list_tools() -> List[types.Tool]:
            specs = await provider.list_tools(ToolAvailabilityContext(session_id=self.agent.service.agent_id))
            return [types.Tool(name=spec.name, description=spec.description, inputSchema=spec.parameters_schema) for spec in specs]

        @server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
            result = await provider.invoke(ToolInvocation(name, arguments), ToolExecutionContext(session_id=self.agent.service.agent_id))
            content = [types.TextContent(type="text", text=result.get_history_content())]
            content.extend(types.ImageContent(type="image", data=item.data, mimeType=item.mime_type)
                           for item in result.content_items if item.content_type == "image")
            return types.CallToolResult(content=content, isError=not result.success)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/mcp"
        manager = StreamableHTTPSessionManager(server, json_response=True, stateless=True, security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=[f"127.0.0.1:{port}"], allowed_origins=[],
        ))
        endpoint = StreamableHTTPASGIApp(manager)
        token = self.token

        class AuthorizedEndpoint:
            async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                headers = dict(scope["headers"])
                if not secrets.compare_digest(headers.get(b"authorization", b""), ("Bearer " + token).encode()):
                    await Response(status_code=401)(scope, receive, send)
                    return
                if scope["method"] == "GET":
                    # 无状态工具入口只做请求/响应，不建立额外的服务器 SSE 通知流。
                    await Response(status_code=405, headers={"Allow": "POST"})(scope, receive, send)
                    return
                await endpoint(scope, receive, send)

        @asynccontextmanager
        async def lifespan(_app: Starlette) -> AsyncIterator[None]:
            async with manager.run():
                yield

        app = Starlette(routes=[Route("/mcp", AuthorizedEndpoint(), methods=["GET", "POST", "DELETE"])], lifespan=lifespan)
        self.server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, log_level="warning", ws="none"))
        self.task = asyncio.create_task(self.server.serve(sockets=[sock]), name="agent.mcp")
        try:
            async def ready():
                while not self.server.started:
                    if self.task.done():
                        await self.task
                        raise RuntimeError("外接 Agent MCP 服务未能启动")
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(ready(), self.agent.config.acp_timeout)
        except BaseException:
            await self.close()
            sock.close()
            raise
        return {"type": "http", "name": "maibot-agent", "url": self.url,
                "headers": [{"name": "Authorization", "value": "Bearer " + self.token}]}

    async def close(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self.task), min(self.agent.config.acp_timeout, 1))
            except asyncio.TimeoutError:
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
            self.task = None
