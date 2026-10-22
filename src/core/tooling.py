"""统一工具抽象。

该模块定义主程序内部统一使用的工具声明、调用与执行结果模型，
用于收敛插件 Tool、兼容旧 Action、MaiSaka 内置 Tool 与 MCP Tool。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import Any, Dict, List, Literal, Optional, Protocol, Tuple, runtime_checkable
import json
import re

from src.common.logger import get_logger
from src.llm_models.payload_content.tool_option import ToolDefinitionInput

logger = get_logger("core.tooling")


@dataclass(slots=True)
class ToolIcon:
    """统一工具图标信息。"""

    src: str
    mime_type: str = ""
    sizes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ToolAnnotation:
    """统一工具注解信息。"""

    audience: list[str] = field(default_factory=list)
    priority: float | None = None
    title: str = ""
    read_only: bool | None = None
    destructive: bool | None = None
    idempotent: bool | None = None
    open_world: bool | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolContentItem:
    """统一工具内容项。"""

    content_type: Literal["text", "image", "audio", "resource_link", "resource", "binary", "unknown"]
    text: str = ""
    data: str = ""
    mime_type: str = ""
    uri: str = ""
    name: str = ""
    description: str = ""
    annotation: ToolAnnotation | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def build_history_text(self) -> str:
        """生成适合写入历史消息的文本摘要。

        Returns:
            str: 当前内容项对应的历史摘要文本。
        """

        if self.content_type == "text" and self.text.strip():
            return self.text.strip()
        if self.content_type == "image":
            return f"[图片内容 {self.mime_type or 'unknown'}]"
        if self.content_type == "audio":
            return f"[音频内容 {self.mime_type or 'unknown'}]"
        if self.content_type == "resource_link":
            label = self.name or self.uri or "资源链接"
            return f"[资源链接] {label}"
        if self.content_type == "resource":
            if self.text.strip():
                return self.text.strip()
            label = self.name or self.uri or "嵌入资源"
            return f"[嵌入资源] {label}"
        if self.content_type == "binary":
            return f"[二进制内容 {self.mime_type or 'unknown'}]"
        return f"[{self.content_type} 内容]"


@dataclass(init=False, slots=True)
class ToolSpec:
    """统一工具声明。"""

    name: str
    description: str
    title: str = ""
    parameters_schema: Dict[str, Any] | None = None
    output_schema: Dict[str, Any] | None = None
    provider_name: str = ""
    provider_type: str = ""
    enabled: bool = True
    icons: list[ToolIcon] = field(default_factory=list)
    annotation: ToolAnnotation | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        *,
        name: str,
        description: str = "",
        title: str = "",
        parameters_schema: Dict[str, Any] | None = None,
        output_schema: Dict[str, Any] | None = None,
        provider_name: str = "",
        provider_type: str = "",
        enabled: bool = True,
        icons: list[ToolIcon] | None = None,
        annotation: ToolAnnotation | None = None,
        metadata: Dict[str, Any] | None = None,
        brief_description: str = "",
        detailed_description: str = "",
    ) -> None:
        """初始化工具声明。

        brief_description/detailed_description 为旧字段兼容入口：
        - description 为空时使用 brief_description。
        - detailed_description 被弃用，不参与描述构建。
        """

        self.name = name
        self.description = str(description or brief_description or "").strip()
        self.title = title
        self.parameters_schema = parameters_schema
        self.output_schema = output_schema
        self.provider_name = provider_name
        self.provider_type = provider_type
        self.enabled = enabled
        self.icons = list(icons or [])
        self.annotation = annotation
        self.metadata = dict(metadata or {})

    def build_llm_description(self) -> str:
        """构建供 LLM 使用的描述文本。

        Returns:
            str: 合并后的单段工具描述。
        """

        return self.description.strip()

    def to_llm_definition(self) -> ToolDefinitionInput:
        """转换为统一的 LLM 工具定义。

        Returns:
            ToolDefinitionInput: 可直接交给模型层的工具定义。
        """

        definition: Dict[str, Any] = {
            "name": self.name,
            "description": self.build_llm_description(),
        }
        if self.parameters_schema is not None:
            definition["parameters_schema"] = deepcopy(self.parameters_schema)
        return definition


@dataclass(slots=True)
class ToolInvocation:
    """统一工具调用请求。"""

    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    session_id: str = ""
    stream_id: str = ""
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolExecutionContext:
    """统一工具执行上下文。"""

    session_id: str = ""
    stream_id: str = ""
    reasoning: str = ""
    is_group_chat: bool | None = None
    group_id: str = ""
    user_id: str = ""
    platform: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolAvailabilityContext:
    """工具暴露可用性判断上下文。"""

    session_id: str = ""
    stream_id: str = ""
    is_group_chat: bool | None = None
    group_id: str = ""
    user_id: str = ""
    platform: str = ""


@dataclass(slots=True)
class ToolExecutionResult:
    """统一工具执行结果。"""

    tool_name: str
    success: bool
    content: str = ""
    error_message: str = ""
    structured_content: Any = None
    content_items: list[ToolContentItem] = field(default_factory=list)
    post_history_messages: list[Any] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    stop_after_execution: bool = False

    def get_history_content(self) -> str:
        """获取适合写入对话历史的结果文本。

        Returns:
            str: 优先使用文本内容，其次使用错误信息。
        """

        if self.content.strip():
            return self.content.strip()
        if self.content_items:
            parts = [item.build_history_text() for item in self.content_items if item.build_history_text().strip()]
            if parts:
                return "\n".join(parts).strip()
        if self.structured_content is not None:
            if isinstance(self.structured_content, str):
                return self.structured_content.strip()
            try:
                return json.dumps(self.structured_content, ensure_ascii=False)
            except (TypeError, ValueError):
                return str(self.structured_content).strip()
        return self.error_message.strip()


@runtime_checkable
class ToolProvider(Protocol):
    """统一工具提供者协议。"""

    provider_name: str
    provider_type: str

    async def list_tools(
        self,
        context: Optional[ToolAvailabilityContext] = None,
    ) -> list[ToolSpec]:
        """列出当前 Provider 暴露的全部工具。"""
        ...

    async def invoke(
        self,
        invocation: ToolInvocation,
        context: Optional[ToolExecutionContext] = None,
    ) -> ToolExecutionResult:
        """执行指定工具调用。"""
        ...

    async def close(self) -> None:
        """释放 Provider 资源。"""
        ...


class ToolRegistry:
    """统一工具注册表。"""

    def __init__(self) -> None:
        """初始化统一工具注册表。"""

        self._providers: list[ToolProvider] = []

    def register_provider(self, provider: ToolProvider) -> None:
        """注册一个工具提供者。

        Args:
            provider: 待注册的工具提供者。
        """

        self._providers = [item for item in self._providers if item.provider_name != provider.provider_name]
        self._providers.append(provider)

    def unregister_provider(self, provider_name: str) -> None:
        """注销指定名称的工具提供者。

        Args:
            provider_name: 待移除的 Provider 名称。
        """

        self._providers = [item for item in self._providers if item.provider_name != provider_name]

    async def list_tools(
        self,
        context: Optional[ToolAvailabilityContext] = None,
    ) -> list[ToolSpec]:
        """按 Provider 顺序列出工具，重名项添加来源前缀并保留原始入口。

        Returns:
            list[ToolSpec]: 名称唯一的工具列表。
        """

        return [spec for _, spec in (await self._list_routes(context)).values()]

    async def _list_routes(
        self,
        context: Optional[ToolAvailabilityContext] = None,
    ) -> Dict[str, Tuple[ToolProvider, ToolSpec]]:
        """声明与执行共用名称分配规则，不修改 Provider 持有的原始声明。"""
        entries: List[Tuple[ToolProvider, ToolSpec]] = []
        for provider in self._providers:
            entries.extend((provider, spec) for spec in await provider.list_tools(context) if spec.enabled)
        reserved_names = {spec.name for _, spec in entries}
        routes: Dict[str, Tuple[ToolProvider, ToolSpec]] = {}
        for provider, original in entries:
            name = original.name
            spec = deepcopy(original)
            spec.metadata["registry_original_name"] = name
            if name in routes:
                source = str(
                    spec.metadata.get("server_name") or spec.metadata.get("plugin_id") or provider.provider_name
                )
                prefix = f"{provider.provider_type}_{source}" if source != provider.provider_type else source
                candidate = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{prefix}_{name}")
                if len(candidate) > 64 or candidate in routes or candidate in reserved_names:
                    digest = sha256(f"{provider.provider_name}:{source}:{name}".encode()).hexdigest()[:12]
                    candidate = f"{candidate[:51]}_{digest}"
                if candidate in routes or candidate in reserved_names:
                    raise ValueError(f"工具来源重复，无法分配唯一名称：{provider.provider_name}/{source}/{name}")
                spec.name = candidate
            routes[spec.name] = (provider, spec)
        return routes

    async def get_tool_spec(
        self,
        tool_name: str,
        context: Optional[ToolAvailabilityContext] = None,
    ) -> Optional[ToolSpec]:
        """查询指定工具声明。

        Args:
            tool_name: 工具名称。

        Returns:
            Optional[ToolSpec]: 匹配到的工具声明。
        """

        for spec in await self.list_tools(context):
            if spec.name == tool_name:
                return spec
        return None

    async def has_tool(
        self,
        tool_name: str,
        context: Optional[ToolAvailabilityContext] = None,
    ) -> bool:
        """判断指定工具是否存在。

        Args:
            tool_name: 工具名称。

        Returns:
            bool: 是否存在。
        """

        return await self.get_tool_spec(tool_name, context) is not None

    async def get_llm_definitions(
        self,
        context: Optional[ToolAvailabilityContext] = None,
    ) -> list[ToolDefinitionInput]:
        """获取供 LLM 使用的工具定义列表。

        Returns:
            list[ToolDefinitionInput]: 统一工具定义列表。
        """

        return [spec.to_llm_definition() for spec in await self.list_tools(context)]

    async def invoke(
        self,
        invocation: ToolInvocation,
        context: Optional[ToolExecutionContext] = None,
    ) -> ToolExecutionResult:
        """执行一次工具调用。

        Args:
            invocation: 工具调用请求。
            context: 执行上下文。

        Returns:
            ToolExecutionResult: 工具执行结果。
        """

        availability_context: ToolAvailabilityContext | None = None
        if context is not None:
            availability_context = ToolAvailabilityContext(
                session_id=context.session_id,
                stream_id=context.stream_id,
                is_group_chat=context.is_group_chat,
                group_id=context.group_id,
                user_id=context.user_id,
                platform=context.platform,
            )

        route = (await self._list_routes(availability_context)).get(invocation.tool_name)
        if route is not None:
            provider, spec = route
            metadata = dict(invocation.metadata)
            if provider.provider_type == "mcp":
                # 服务器选择来自注册表，不接受调用方伪造的路由元数据。
                metadata["mcp_server_name"] = spec.metadata["server_name"]
            native_invocation = replace(
                invocation,
                tool_name=spec.metadata["registry_original_name"],
                metadata=metadata,
            )
            try:
                result = await provider.invoke(native_invocation, context)
                return replace(result, tool_name=invocation.tool_name)
            except Exception as exc:
                logger.exception(
                    f"工具调用异常: tool={invocation.tool_name} provider={provider.provider_name}",
                )
                error_message = str(exc).strip()
                if error_message:
                    error_message = f"工具 {invocation.tool_name} 调用失败：{exc.__class__.__name__}: {error_message}"
                else:
                    error_message = f"工具 {invocation.tool_name} 调用失败：{exc.__class__.__name__}"
                return ToolExecutionResult(
                    tool_name=invocation.tool_name,
                    success=False,
                    error_message=error_message,
                )

        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=False,
            error_message=f"未找到工具：{invocation.tool_name}",
        )

    async def close(self) -> None:
        """关闭全部 Provider。"""

        for provider in self._providers:
            await provider.close()
