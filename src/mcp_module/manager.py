"""
MaiSaka - MCP 管理器
管理所有 MCP 服务器连接，提供统一的工具、Prompt 与 Resource 访问入口。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

import asyncio

from src.cli.console import console
from src.core.tooling import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)

from .config import (
    MCPClientRuntimeConfig,
    MCPServerRuntimeConfig,
    build_mcp_client_runtime_config,
    build_mcp_server_runtime_configs,
)
from .connection import MCPConnection, MCP_AVAILABLE
from .hooks import MCPHostCallbacks
from .models import (
    MCPPromptResult,
    MCPPromptSpec,
    MCPResourceReadResult,
    MCPResourceSpec,
    MCPResourceTemplateSpec,
    build_prompt_spec,
    build_resource_spec,
    build_resource_template_spec,
    build_tool_annotation,
    build_tool_icon,
)

if TYPE_CHECKING:
    from src.config.official_configs import MCPConfig

# 内置工具名称集合 —— MCP 工具不允许与这些名称冲突
BUILTIN_TOOL_NAMES = frozenset(
    {
        "reply",
        "no_action",
        "stop",
        "create_table",
        "list_tables",
        "view_table",
    }
)


class MCPManager:
    """MCP 服务器连接管理器。"""

    def __init__(
        self,
        client_config: MCPClientRuntimeConfig,
        host_callbacks: Optional[MCPHostCallbacks] = None,
        *,
        discover_extended_features: bool = True,
    ) -> None:
        """初始化 MCP 管理器。

        Args:
            client_config: MCP 客户端宿主能力运行时配置。
            host_callbacks: 宿主侧能力回调集合。
            discover_extended_features: 是否发现当前尚未接入主流程的 Prompt 和 Resource。
        """

        self._client_config = client_config
        self._host_callbacks = host_callbacks or MCPHostCallbacks()
        self._discover_extended_features = discover_extended_features
        self._connections: dict[str, MCPConnection] = {}
        self._connection_errors: dict[str, str] = {}
        self._server_configs: dict[str, MCPServerRuntimeConfig] = {}
        self._tool_to_server: dict[str, str] = {}
        self._prompt_to_server: dict[str, str] = {}
        self._resource_to_server: dict[str, str] = {}
        self._resource_template_to_server: dict[str, str] = {}

    @classmethod
    async def from_app_config(
        cls,
        mcp_config: "MCPConfig",
        host_callbacks: Optional[MCPHostCallbacks] = None,
        *,
        discover_extended_features: bool = True,
        allow_empty: bool = False,
    ) -> Optional["MCPManager"]:
        """从官方配置创建并初始化 `MCPManager`。

        Args:
            mcp_config: 主程序中的 MCP 配置对象。
            host_callbacks: 宿主侧能力回调集合。
            discover_extended_features: 是否发现 Prompt 和 Resource。
            allow_empty: 全部连接失败时是否仍返回包含诊断信息的管理器。

        Returns:
            Optional[MCPManager]: 初始化完成的管理器；无可用配置或全部连接失败时返回 ``None``。
        """

        configs = build_mcp_server_runtime_configs(mcp_config)
        if not configs:
            return None

        manager = cls(
            client_config=build_mcp_client_runtime_config(mcp_config),
            host_callbacks=host_callbacks,
            discover_extended_features=discover_extended_features,
        )
        if not MCP_AVAILABLE:
            console.print("[warning]⚠️ 发现 MCP 配置但未安装 mcp SDK，请运行: pip install mcp[/warning]")
            if allow_empty:
                manager._server_configs = {config.name: config for config in configs}
                manager._connection_errors = {config.name: "未安装 mcp SDK" for config in configs}
                return manager
            return None

        await manager._connect_all(configs)

        if not manager._connections:
            console.print("[warning]⚠️ 所有 MCP 服务器连接失败[/warning]")
            return manager if allow_empty else None

        return manager

    async def _connect_all(self, configs: list[MCPServerRuntimeConfig]) -> None:
        """连接全部已配置的 MCP 服务器。

        Args:
            configs: 服务器运行时配置列表。

        Returns:
            None
        """

        self._server_configs = {config.name: config for config in configs}

        async def connect_one(config: MCPServerRuntimeConfig) -> tuple[MCPServerRuntimeConfig, MCPConnection, bool]:
            """并行建立一个服务连接，但保持后续注册顺序稳定。"""

            connection = MCPConnection(
                config,
                self._client_config,
                self._host_callbacks,
                discover_extended_features=self._discover_extended_features,
            )
            connect_timeout_seconds = (
                min(60.0, max(5.0, config.http_timeout_seconds + 5.0))
                if config.transport != "stdio"
                else 30.0
            )
            try:
                success = await asyncio.wait_for(
                    connection.connect(),
                    timeout=connect_timeout_seconds,
                )
            except TimeoutError:
                connection.last_error = f"连接超过 {connect_timeout_seconds:g} 秒"
                await connection.close()
                success = False
            return config, connection, success

        connection_results = await asyncio.gather(*(connect_one(config) for config in configs))
        for config, connection, success in connection_results:
            if not success:
                self._connection_errors[config.name] = connection.last_error or "连接失败"
                continue

            self._connections[config.name] = connection
            registered_tool_count = self._register_tools(config.name, connection)
            registered_prompt_count = self._register_prompts(config.name, connection)
            registered_resource_count = self._register_resources(config.name, connection)
            registered_template_count = self._register_resource_templates(config.name, connection)
            console.print(
                "[success]✓ MCP 服务器 "
                f"'{config.name}' 已连接[/success] "
                f"[muted](工具 {registered_tool_count} / Prompt {registered_prompt_count} / "
                f"资源 {registered_resource_count} / 模板 {registered_template_count})[/muted]"
            )

    def get_status_snapshot(self) -> dict[str, Any]:
        """返回可跨线程读取的纯数据连接状态快照。"""

        servers: list[dict[str, Any]] = []
        for server_name, config in self._server_configs.items():
            connection = self._connections.get(server_name)
            servers.append(
                {
                    "name": server_name,
                    "transport": config.transport,
                    "connected": connection is not None and connection.session is not None,
                    "protocol_version": connection.protocol_version if connection is not None else "",
                    "tool_count": len(connection.tools) if connection is not None else 0,
                    "error": self._connection_errors.get(server_name, ""),
                }
            )
        return {
            "initialized": True,
            "server_count": len(self._connections),
            "tool_count": len(self._tool_to_server),
            "servers": servers,
        }

    def _register_tools(self, server_name: str, connection: MCPConnection) -> int:
        """注册单个服务器暴露的 MCP 工具。

        Args:
            server_name: 服务器名称。
            connection: 对应连接对象。

        Returns:
            int: 成功注册的工具数量。
        """

        registered_count = 0
        for tool in connection.tools:
            tool_name = str(tool.name)

            if tool_name in BUILTIN_TOOL_NAMES:
                console.print(
                    f"[warning]⚠️ MCP 工具 '{tool_name}' (来自 {server_name}) 与内置工具冲突，已跳过[/warning]"
                )
                continue

            if tool_name in self._tool_to_server:
                existing_server = self._tool_to_server[tool_name]
                console.print(
                    f"[warning]⚠️ MCP 工具 '{tool_name}' (来自 {server_name}) 与 {existing_server} 冲突，已跳过[/warning]"
                )
                continue

            self._tool_to_server[tool_name] = server_name
            registered_count += 1
        return registered_count

    def _register_prompts(self, server_name: str, connection: MCPConnection) -> int:
        """注册单个服务器暴露的 MCP Prompt。

        Args:
            server_name: 服务器名称。
            connection: 对应连接对象。

        Returns:
            int: 成功注册的 Prompt 数量。
        """

        registered_count = 0
        for prompt in connection.prompts:
            prompt_name = str(prompt.name)
            if prompt_name in self._prompt_to_server:
                existing_server = self._prompt_to_server[prompt_name]
                console.print(
                    f"[warning]⚠️ MCP Prompt '{prompt_name}' (来自 {server_name}) 与 {existing_server} 冲突，已跳过[/warning]"
                )
                continue
            self._prompt_to_server[prompt_name] = server_name
            registered_count += 1
        return registered_count

    def _register_resources(self, server_name: str, connection: MCPConnection) -> int:
        """注册单个服务器暴露的 MCP Resource。

        Args:
            server_name: 服务器名称。
            connection: 对应连接对象。

        Returns:
            int: 成功注册的 Resource 数量。
        """

        registered_count = 0
        for resource in connection.resources:
            resource_uri = str(resource.uri)
            if resource_uri in self._resource_to_server:
                existing_server = self._resource_to_server[resource_uri]
                console.print(
                    f"[warning]⚠️ MCP Resource '{resource_uri}' (来自 {server_name}) 与 {existing_server} 冲突，已跳过[/warning]"
                )
                continue
            self._resource_to_server[resource_uri] = server_name
            registered_count += 1
        return registered_count

    def _register_resource_templates(self, server_name: str, connection: MCPConnection) -> int:
        """注册单个服务器暴露的 MCP Resource Template。

        Args:
            server_name: 服务器名称。
            connection: 对应连接对象。

        Returns:
            int: 成功注册的模板数量。
        """

        registered_count = 0
        for resource_template in connection.resource_templates:
            uri_template = str(resource_template.uriTemplate)
            if uri_template in self._resource_template_to_server:
                existing_server = self._resource_template_to_server[uri_template]
                console.print(
                    "[warning]⚠️ MCP Resource Template "
                    f"'{uri_template}' (来自 {server_name}) 与 {existing_server} 冲突，已跳过[/warning]"
                )
                continue
            self._resource_template_to_server[uri_template] = server_name
            registered_count += 1
        return registered_count

    def _build_tool_parameters_schema(self, tool: Any) -> dict[str, Any] | None:
        """构造单个 MCP 工具的参数 Schema。

        Args:
            tool: MCP SDK 返回的原始工具对象。

        Returns:
            dict[str, Any] | None: 参数 Schema。
        """

        parameters_schema = (
            dict(tool.inputSchema)
            if hasattr(tool, "inputSchema") and tool.inputSchema
            else {"type": "object", "properties": {}}
        )
        parameters_schema.pop("$schema", None)
        return parameters_schema

    def _build_tool_output_schema(self, tool: Any) -> dict[str, Any] | None:
        """构造单个 MCP 工具的输出 Schema。

        Args:
            tool: MCP SDK 返回的原始工具对象。

        Returns:
            dict[str, Any] | None: 输出 Schema。
        """

        output_schema = dict(tool.outputSchema) if hasattr(tool, "outputSchema") and tool.outputSchema else None
        if isinstance(output_schema, dict):
            output_schema.pop("$schema", None)
        return output_schema

    def get_tool_specs(self) -> list[ToolSpec]:
        """获取全部已注册 MCP 工具的统一声明。

        Returns:
            list[ToolSpec]: MCP 工具声明列表。
        """

        tool_specs: list[ToolSpec] = []
        for server_name, connection in self._connections.items():
            for tool in connection.tools:
                if self._tool_to_server.get(tool.name) != server_name:
                    continue

                parameters_schema = self._build_tool_parameters_schema(tool)
                output_schema = self._build_tool_output_schema(tool)
                description = str(tool.description or f"来自 {server_name} 的 MCP 工具").strip()
                tool_specs.append(
                    ToolSpec(
                        name=str(tool.name),
                        title=str(getattr(tool, "title", "") or ""),
                        description=description,
                        parameters_schema=parameters_schema,
                        output_schema=output_schema,
                        provider_name="mcp",
                        provider_type="mcp",
                        icons=[build_tool_icon(item) for item in getattr(tool, "icons", []) or []],
                        annotation=build_tool_annotation(getattr(tool, "annotations", None)),
                        metadata={"server_name": server_name} | (getattr(tool, "meta", {}) or {}),
                    )
                )
        return tool_specs

    def get_prompt_specs(self) -> list[MCPPromptSpec]:
        """获取全部已注册 MCP Prompt 声明。

        Returns:
            list[MCPPromptSpec]: Prompt 声明列表。
        """

        prompt_specs: list[MCPPromptSpec] = []
        for server_name, connection in self._connections.items():
            for prompt in connection.prompts:
                if self._prompt_to_server.get(prompt.name) != server_name:
                    continue
                prompt_specs.append(build_prompt_spec(prompt, server_name))
        return prompt_specs

    def get_resource_specs(self) -> list[MCPResourceSpec]:
        """获取全部已注册 MCP Resource 声明。

        Returns:
            list[MCPResourceSpec]: Resource 声明列表。
        """

        resource_specs: list[MCPResourceSpec] = []
        for server_name, connection in self._connections.items():
            for resource in connection.resources:
                if self._resource_to_server.get(resource.uri) != server_name:
                    continue
                resource_specs.append(build_resource_spec(resource, server_name))
        return resource_specs

    def get_resource_template_specs(self) -> list[MCPResourceTemplateSpec]:
        """获取全部已注册 MCP Resource Template 声明。

        Returns:
            list[MCPResourceTemplateSpec]: Resource Template 声明列表。
        """

        resource_template_specs: list[MCPResourceTemplateSpec] = []
        for server_name, connection in self._connections.items():
            for resource_template in connection.resource_templates:
                if self._resource_template_to_server.get(resource_template.uriTemplate) != server_name:
                    continue
                resource_template_specs.append(build_resource_template_spec(resource_template, server_name))
        return resource_template_specs

    def get_openai_tools(self) -> list[dict[str, Any]]:
        """获取兼容旧模型层的 MCP 工具定义。

        Returns:
            list[dict[str, Any]]: OpenAI function tool 格式列表。
        """

        return [
            {
                "type": "function",
                "function": {
                    "name": tool_spec.name,
                    "description": tool_spec.build_llm_description(),
                    "parameters": tool_spec.parameters_schema or {"type": "object", "properties": {}},
                },
            }
            for tool_spec in self.get_tool_specs()
        ]

    def is_mcp_tool(self, tool_name: str) -> bool:
        """判断给定名称是否为已注册 MCP 工具。

        Args:
            tool_name: 工具名称。

        Returns:
            bool: 是否存在。
        """

        return tool_name in self._tool_to_server

    def is_mcp_prompt(self, prompt_name: str) -> bool:
        """判断给定名称是否为已注册 MCP Prompt。

        Args:
            prompt_name: Prompt 名称。

        Returns:
            bool: 是否存在。
        """

        return prompt_name in self._prompt_to_server

    def is_mcp_resource(self, uri: str) -> bool:
        """判断给定 URI 是否为已注册 MCP Resource。

        Args:
            uri: 资源 URI。

        Returns:
            bool: 是否存在。
        """

        return uri in self._resource_to_server

    async def call_tool_invocation(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """执行统一的 MCP 工具调用。

        Args:
            invocation: 统一工具调用请求。

        Returns:
            ToolExecutionResult: 统一工具执行结果。
        """

        tool_name = invocation.tool_name
        server_name = self._tool_to_server.get(tool_name)
        if not server_name or server_name not in self._connections:
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_message=f"MCP 工具 '{tool_name}' 未找到",
            )

        connection = self._connections[server_name]
        return await connection.call_tool(tool_name, invocation.arguments)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """兼容旧接口，返回 MCP 工具的文本结果。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数。

        Returns:
            str: 工具结果文本。
        """

        result = await self.call_tool_invocation(
            ToolInvocation(
                tool_name=tool_name,
                arguments=arguments,
            )
        )
        return result.get_history_content()

    async def get_prompt(
        self,
        prompt_name: str,
        arguments: Optional[dict[str, str]] = None,
    ) -> MCPPromptResult:
        """读取指定 Prompt 的内容。

        Args:
            prompt_name: Prompt 名称。
            arguments: Prompt 参数字典。

        Returns:
            MCPPromptResult: Prompt 获取结果。
        """

        server_name = self._prompt_to_server.get(prompt_name)
        if not server_name or server_name not in self._connections:
            raise KeyError(f"MCP Prompt '{prompt_name}' 未找到")

        connection = self._connections[server_name]
        return await connection.get_prompt(prompt_name, arguments=arguments)

    async def read_resource(self, uri: str) -> MCPResourceReadResult:
        """读取指定 Resource 的内容。

        Args:
            uri: 资源 URI。

        Returns:
            MCPResourceReadResult: 资源读取结果。
        """

        server_name = self._resource_to_server.get(uri)
        if not server_name or server_name not in self._connections:
            raise KeyError(f"MCP Resource '{uri}' 未找到")

        connection = self._connections[server_name]
        return await connection.read_resource(uri)

    def get_tool_summary(self) -> str:
        """获取所有已注册 MCP 工具的摘要信息。

        Returns:
            str: 工具摘要文本。
        """

        parts: list[str] = []
        for server_name, connection in self._connections.items():
            tool_names = [
                str(tool.name)
                for tool in connection.tools
                if self._tool_to_server.get(tool.name) == server_name
            ]
            if tool_names:
                parts.append(f"  • {server_name}: {', '.join(tool_names)}")
        return "\n".join(parts)

    def get_feature_summary(self) -> str:
        """获取所有服务器能力的总体摘要。

        Returns:
            str: 多行摘要文本。
        """

        parts: list[str] = []
        for server_name, connection in self._connections.items():
            tool_count = sum(1 for tool in connection.tools if self._tool_to_server.get(tool.name) == server_name)
            prompt_count = sum(
                1 for prompt in connection.prompts if self._prompt_to_server.get(prompt.name) == server_name
            )
            resource_count = sum(
                1 for resource in connection.resources if self._resource_to_server.get(resource.uri) == server_name
            )
            template_count = sum(
                1
                for resource_template in connection.resource_templates
                if self._resource_template_to_server.get(resource_template.uriTemplate) == server_name
            )
            parts.append(
                f"  • {server_name}: 工具 {tool_count} / Prompt {prompt_count} / "
                f"资源 {resource_count} / 模板 {template_count}"
            )
        return "\n".join(parts)

    @property
    def server_count(self) -> int:
        """返回已连接 MCP 服务器数量。

        Returns:
            int: 服务器数量。
        """

        return len(self._connections)

    @property
    def tool_count(self) -> int:
        """返回已注册 MCP 工具总数。

        Returns:
            int: 工具数量。
        """

        return len(self._tool_to_server)

    @property
    def prompt_count(self) -> int:
        """返回已注册 MCP Prompt 总数。

        Returns:
            int: Prompt 数量。
        """

        return len(self._prompt_to_server)

    @property
    def resource_count(self) -> int:
        """返回已注册 MCP Resource 总数。

        Returns:
            int: Resource 数量。
        """

        return len(self._resource_to_server)

    async def close(self) -> None:
        """关闭所有 MCP 服务器连接。"""

        close_results = await asyncio.gather(
            *(connection.close() for connection in self._connections.values()),
            return_exceptions=True,
        )
        for close_result in close_results:
            if isinstance(close_result, Exception):
                console.print(f"[warning]⚠️ MCP 连接关闭失败: {close_result}[/warning]")
        self._connections.clear()
        self._connection_errors.clear()
        self._server_configs.clear()
        self._tool_to_server.clear()
        self._prompt_to_server.clear()
        self._resource_to_server.clear()
        self._resource_template_to_server.clear()
