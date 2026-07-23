"""MCP 运行时配置转换。

负责将主程序官方配置中的 MCP 配置转换为运行时使用的结构化对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.config.official_configs import MCPConfig, MCPServerItemConfig


@dataclass(slots=True)
class MCPAuthorizationRuntimeConfig:
    """MCP HTTP 认证运行时配置。"""

    mode: Literal["none", "bearer"] = "none"
    bearer_token: str = ""


@dataclass(slots=True)
class MCPRootRuntimeConfig:
    """MCP Root 运行时配置。"""

    uri: str
    name: str = ""


@dataclass(slots=True)
class MCPClientRuntimeConfig:
    """MCP 客户端宿主能力运行时配置。"""

    client_name: str = "MaiBot"
    client_version: str = "1.0.0"
    enable_roots: bool = False
    roots: list[MCPRootRuntimeConfig] = field(default_factory=list)
    enable_sampling: bool = False
    sampling_task_name: str = "planner"
    sampling_include_context_support: bool = False
    sampling_tool_support: bool = False
    enable_elicitation: bool = False
    elicitation_allow_form: bool = True
    elicitation_allow_url: bool = False


@dataclass(slots=True)
class MCPServerRuntimeConfig:
    """单个 MCP 服务器的运行时配置。"""

    name: str
    transport: Literal["stdio", "streamable_http", "sse"] = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    http_timeout_seconds: float = 30.0
    read_timeout_seconds: float = 300.0
    authorization: MCPAuthorizationRuntimeConfig = field(default_factory=MCPAuthorizationRuntimeConfig)

    @property
    def transport_type(self) -> str:
        """返回当前服务器的传输类型。

        Returns:
            str: ``stdio``、``streamable_http``、``sse`` 或 ``unknown``。
        """

        if self.transport == "stdio" and self.command:
            return "stdio"
        if self.transport == "streamable_http" and self.url:
            return "streamable_http"
        if self.transport == "sse" and self.url:
            return "sse"
        return "unknown"

    def build_http_headers(self) -> dict[str, str]:
        """构建远程 HTTP 连接需要附加的请求头。

        Returns:
            dict[str, str]: 归一化后的请求头集合。
        """

        headers = {str(key): str(value) for key, value in self.headers.items()}
        if self.authorization.mode == "bearer" and self.authorization.bearer_token.strip():
            headers["Authorization"] = f"Bearer {self.authorization.bearer_token.strip()}"
        return headers


def build_mcp_client_runtime_config(mcp_config: "MCPConfig") -> MCPClientRuntimeConfig:
    """将官方 MCP 客户端配置转换为运行时结构。

    Args:
        mcp_config: 主程序中的 MCP 官方配置对象。

    Returns:
        MCPClientRuntimeConfig: MCP 客户端宿主能力运行时配置。
    """

    roots = [
        MCPRootRuntimeConfig(
            uri=root.uri.strip(),
            name=root.name.strip(),
        )
        for root in mcp_config.client.roots.items
        if root.enabled and root.uri.strip()
    ]

    return MCPClientRuntimeConfig(
        client_name=mcp_config.client.client_name.strip() or "MaiBot",
        client_version=mcp_config.client.client_version.strip() or "1.0.0",
        enable_roots=mcp_config.client.roots.enable and bool(roots),
        roots=roots,
        enable_sampling=mcp_config.client.sampling.enable,
        sampling_task_name=mcp_config.client.sampling.task_name.strip() or "planner",
        # 当前宿主桥接层尚未实现 includeContext 的上下文收集，不能向服务端声明虚假能力。
        sampling_include_context_support=False,
        sampling_tool_support=mcp_config.client.sampling.tool_support,
        enable_elicitation=mcp_config.client.elicitation.enable,
        elicitation_allow_form=mcp_config.client.elicitation.allow_form,
        elicitation_allow_url=mcp_config.client.elicitation.allow_url,
    )


def build_mcp_server_runtime_configs(mcp_config: "MCPConfig") -> list[MCPServerRuntimeConfig]:
    """将官方 MCP 配置转换为运行时配置列表。

    Args:
        mcp_config: 主程序中的 MCP 官方配置对象。

    Returns:
        list[MCPServerRuntimeConfig]: 启用且配置完整的 MCP 服务器列表。
    """

    if not mcp_config.enable:
        return []

    runtime_configs: list[MCPServerRuntimeConfig] = []
    for server in mcp_config.servers:
        if not server.enabled:
            continue

        runtime_configs.append(build_mcp_server_runtime_config(server))

    return runtime_configs


def build_mcp_server_runtime_config(server: "MCPServerItemConfig") -> MCPServerRuntimeConfig:
    """将单个官方 MCP 服务配置转换为运行时配置。

    Args:
        server: 具备 ``MCPServerItemConfig`` 字段的配置对象。

    Returns:
        MCPServerRuntimeConfig: 可直接交给连接层的运行时配置。
    """

    return MCPServerRuntimeConfig(
        name=server.name.strip(),
        transport=server.transport,
        command=server.command.strip(),
        args=[str(argument) for argument in server.args],
        env={str(key): str(value) for key, value in server.env.items()},
        url=server.url.strip(),
        headers={str(key): str(value) for key, value in server.headers.items()},
        http_timeout_seconds=float(server.http_timeout_seconds),
        read_timeout_seconds=float(server.read_timeout_seconds),
        authorization=MCPAuthorizationRuntimeConfig(
            mode=server.authorization.mode,
            bearer_token=server.authorization.bearer_token.strip(),
        ),
    )
