"""MCP 配置辅助接口。"""

from typing import Any

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from src.config.official_configs import MCPServerItemConfig
from src.mcp_module.config import MCPClientRuntimeConfig, build_mcp_server_runtime_config
from src.mcp_module.connection import MCPConnection
from src.mcp_module.models import build_tool_annotation
from src.mcp_module.service import get_mcp_service
from src.webui.dependencies import require_auth

router = APIRouter(prefix="/mcp", tags=["mcp"], dependencies=[Depends(require_auth)])


class MCPToolPreview(BaseModel):
    """测试连接发现的工具摘要。"""

    name: str
    title: str = ""
    description: str = ""
    read_only: bool | None = None
    destructive: bool | None = None


class MCPConnectionTestResponse(BaseModel):
    """MCP 一次性连接测试结果。"""

    success: bool
    error: str = ""
    protocol_version: str = ""
    tools: list[MCPToolPreview] = Field(default_factory=list)


@router.get("/status")
async def get_mcp_status() -> dict[str, Any]:
    """读取主事件循环维护的 MCP 共享服务状态快照。"""

    return get_mcp_service().get_status_snapshot()


@router.post("/test", response_model=MCPConnectionTestResponse)
async def test_mcp_connection(server: MCPServerItemConfig) -> MCPConnectionTestResponse:
    """使用草稿配置建立一次性连接，发现工具后立即释放资源。"""

    payload = server.model_dump(mode="python")
    payload["enabled"] = True
    try:
        validated_server = MCPServerItemConfig.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"MCP 服务配置无效: {exc}") from exc

    connection = MCPConnection(
        build_mcp_server_runtime_config(validated_server),
        MCPClientRuntimeConfig(),
        discover_extended_features=False,
    )
    timeout_seconds = min(
        30.0,
        max(5.0, float(validated_server.http_timeout_seconds) + 5.0),
    )
    try:
        try:
            connected = await asyncio.wait_for(connection.connect(), timeout=timeout_seconds)
        except TimeoutError:
            return MCPConnectionTestResponse(
                success=False,
                error=f"连接测试超过 {timeout_seconds:g} 秒，已终止",
            )

        if not connected:
            return MCPConnectionTestResponse(
                success=False,
                error=connection.last_error or "连接失败",
            )

        tools: list[MCPToolPreview] = []
        for tool in connection.tools:
            annotation = build_tool_annotation(getattr(tool, "annotations", None))
            tools.append(
                MCPToolPreview(
                    name=str(tool.name),
                    title=str(getattr(tool, "title", "") or (annotation.title if annotation else "")),
                    description=str(getattr(tool, "description", "") or ""),
                    read_only=annotation.read_only if annotation else None,
                    destructive=annotation.destructive if annotation else None,
                )
            )
        return MCPConnectionTestResponse(
            success=True,
            protocol_version=connection.protocol_version,
            tools=tools,
        )
    finally:
        await connection.close()
