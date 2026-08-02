"""MCP 结构化模型与转换工具。

负责在 MCP SDK 原始对象与主程序内部数据模型之间进行转换，
避免连接层和管理器层直接操作大量弱类型字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from src.core.tooling import ToolAnnotation, ToolContentItem, ToolIcon


def _dump_model_metadata(raw_value: Any) -> dict[str, Any]:
    """提取任意 MCP 模型对象中的元数据字典。

    Args:
        raw_value: MCP SDK 返回的原始对象。

    Returns:
        dict[str, Any]: 归一化后的元数据字典。
    """

    metadata = getattr(raw_value, "meta", None)
    if isinstance(metadata, dict):
        return dict(metadata)
    return {}


def build_tool_icon(raw_icon: Any) -> ToolIcon:
    """将 MCP 图标对象转换为统一图标模型。

    Args:
        raw_icon: MCP SDK 返回的图标对象。

    Returns:
        ToolIcon: 统一图标模型。
    """

    sizes_value = getattr(raw_icon, "sizes", None)
    sizes = [str(item) for item in sizes_value] if isinstance(sizes_value, list) else []
    return ToolIcon(
        src=str(getattr(raw_icon, "src", "") or ""),
        mime_type=str(getattr(raw_icon, "mimeType", "") or ""),
        sizes=sizes,
    )


def build_tool_annotation(raw_annotation: Any) -> Optional[ToolAnnotation]:
    """将 MCP 注解对象转换为统一注解模型。

    Args:
        raw_annotation: MCP SDK 返回的注解对象。

    Returns:
        Optional[ToolAnnotation]: 统一注解模型；无有效内容时返回 ``None``。
    """

    if raw_annotation is None:
        return None

    audience_value = getattr(raw_annotation, "audience", None)
    audience = [str(item) for item in audience_value] if isinstance(audience_value, list) else []
    priority_value = getattr(raw_annotation, "priority", None)
    priority = float(priority_value) if isinstance(priority_value, int | float) else None
    title = str(getattr(raw_annotation, "title", "") or "").strip()
    read_only = getattr(raw_annotation, "readOnlyHint", None)
    destructive = getattr(raw_annotation, "destructiveHint", None)
    idempotent = getattr(raw_annotation, "idempotentHint", None)
    open_world = getattr(raw_annotation, "openWorldHint", None)
    metadata = _dump_model_metadata(raw_annotation)

    if (
        not audience
        and priority is None
        and not title
        and read_only is None
        and destructive is None
        and idempotent is None
        and open_world is None
        and not metadata
    ):
        return None

    return ToolAnnotation(
        audience=audience,
        priority=priority,
        title=title,
        read_only=read_only if isinstance(read_only, bool) else None,
        destructive=destructive if isinstance(destructive, bool) else None,
        idempotent=idempotent if isinstance(idempotent, bool) else None,
        open_world=open_world if isinstance(open_world, bool) else None,
        metadata=metadata,
    )


def build_tool_content_item(raw_content: Any) -> ToolContentItem:
    """将 MCP 内容块转换为统一工具内容项。

    Args:
        raw_content: MCP SDK 返回的内容块对象。

    Returns:
        ToolContentItem: 统一工具内容项。
    """

    content_type = str(getattr(raw_content, "type", "") or "").strip().lower()
    annotation = build_tool_annotation(getattr(raw_content, "annotations", None))
    metadata = _dump_model_metadata(raw_content)

    if content_type == "text" or hasattr(raw_content, "text"):
        return ToolContentItem(
            content_type="text",
            text=str(getattr(raw_content, "text", "") or ""),
            annotation=annotation,
            metadata=metadata,
        )

    if content_type == "image":
        return ToolContentItem(
            content_type="image",
            data=str(getattr(raw_content, "data", "") or ""),
            mime_type=str(getattr(raw_content, "mimeType", "") or ""),
            annotation=annotation,
            metadata=metadata,
        )

    if content_type == "audio":
        return ToolContentItem(
            content_type="audio",
            data=str(getattr(raw_content, "data", "") or ""),
            mime_type=str(getattr(raw_content, "mimeType", "") or ""),
            annotation=annotation,
            metadata=metadata,
        )

    if content_type == "resource_link":
        return ToolContentItem(
            content_type="resource_link",
            uri=str(getattr(raw_content, "uri", "") or ""),
            name=str(getattr(raw_content, "name", "") or ""),
            description=str(getattr(raw_content, "description", "") or ""),
            mime_type=str(getattr(raw_content, "mimeType", "") or ""),
            annotation=annotation,
            metadata=metadata,
        )

    if content_type == "resource" or hasattr(raw_content, "resource"):
        resource = getattr(raw_content, "resource", None)
        resource_metadata = metadata | _dump_model_metadata(resource)
        return ToolContentItem(
            content_type="resource",
            text=str(getattr(resource, "text", "") or ""),
            data=str(getattr(resource, "blob", "") or ""),
            mime_type=str(getattr(resource, "mimeType", "") or ""),
            uri=str(getattr(resource, "uri", "") or ""),
            name=str(getattr(resource, "name", "") or ""),
            annotation=annotation,
            metadata=resource_metadata,
        )

    if hasattr(raw_content, "data"):
        return ToolContentItem(
            content_type="binary",
            data=str(getattr(raw_content, "data", "") or ""),
            mime_type=str(getattr(raw_content, "mimeType", "") or ""),
            annotation=annotation,
            metadata=metadata,
        )

    return ToolContentItem(
        content_type="unknown",
        text=str(raw_content),
        annotation=annotation,
        metadata=metadata,
    )


def build_tool_content_items(raw_contents: list[Any] | None) -> list[ToolContentItem]:
    """批量转换 MCP 内容块列表。

    Args:
        raw_contents: MCP SDK 返回的内容块列表。

    Returns:
        list[ToolContentItem]: 转换后的统一内容项列表。
    """

    if not raw_contents:
        return []
    return [build_tool_content_item(item) for item in raw_contents]


@dataclass(slots=True)
class MCPPromptArgumentSpec:
    """MCP Prompt 参数声明。"""

    name: str
    description: str = ""
    required: bool = False


@dataclass(slots=True)
class MCPPromptSpec:
    """MCP Prompt 声明。"""

    name: str
    server_name: str
    title: str = ""
    description: str = ""
    arguments: list[MCPPromptArgumentSpec] = field(default_factory=list)
    icons: list[ToolIcon] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MCPPromptMessage:
    """MCP Prompt 消息。"""

    role: str
    content: ToolContentItem


@dataclass(slots=True)
class MCPPromptResult:
    """MCP Prompt 获取结果。"""

    prompt_name: str
    server_name: str
    description: str = ""
    messages: list[MCPPromptMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MCPResourceSpec:
    """MCP Resource 声明。"""

    uri: str
    server_name: str
    name: str
    title: str = ""
    description: str = ""
    mime_type: str = ""
    size: int | None = None
    icons: list[ToolIcon] = field(default_factory=list)
    annotation: ToolAnnotation | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MCPResourceTemplateSpec:
    """MCP Resource Template 声明。"""

    uri_template: str
    server_name: str
    name: str
    title: str = ""
    description: str = ""
    mime_type: str = ""
    icons: list[ToolIcon] = field(default_factory=list)
    annotation: ToolAnnotation | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MCPResourceReadResult:
    """MCP Resource 读取结果。"""

    uri: str
    server_name: str
    contents: list[ToolContentItem] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def build_prompt_argument_spec(raw_argument: Any) -> MCPPromptArgumentSpec:
    """将 MCP Prompt 参数对象转换为统一结构。

    Args:
        raw_argument: MCP SDK 返回的 Prompt 参数对象。

    Returns:
        MCPPromptArgumentSpec: 统一 Prompt 参数结构。
    """

    return MCPPromptArgumentSpec(
        name=str(getattr(raw_argument, "name", "") or ""),
        description=str(getattr(raw_argument, "description", "") or ""),
        required=bool(getattr(raw_argument, "required", False)),
    )


def build_prompt_spec(raw_prompt: Any, server_name: str) -> MCPPromptSpec:
    """将 MCP Prompt 定义转换为统一结构。

    Args:
        raw_prompt: MCP SDK 返回的 Prompt 对象。
        server_name: Prompt 所属的服务器名称。

    Returns:
        MCPPromptSpec: 统一 Prompt 定义。
    """

    raw_arguments = getattr(raw_prompt, "arguments", None)
    raw_icons = getattr(raw_prompt, "icons", None)
    return MCPPromptSpec(
        name=str(getattr(raw_prompt, "name", "") or ""),
        server_name=server_name,
        title=str(getattr(raw_prompt, "title", "") or ""),
        description=str(getattr(raw_prompt, "description", "") or ""),
        arguments=[build_prompt_argument_spec(item) for item in raw_arguments] if isinstance(raw_arguments, list) else [],
        icons=[build_tool_icon(item) for item in raw_icons] if isinstance(raw_icons, list) else [],
        metadata=_dump_model_metadata(raw_prompt),
    )


def build_prompt_result(raw_result: Any, prompt_name: str, server_name: str) -> MCPPromptResult:
    """将 MCP Prompt 获取结果转换为统一结构。

    Args:
        raw_result: MCP SDK 返回的 Prompt 结果对象。
        prompt_name: Prompt 名称。
        server_name: Prompt 所属服务器名称。

    Returns:
        MCPPromptResult: 统一 Prompt 获取结果。
    """

    messages: list[MCPPromptMessage] = []
    raw_messages = getattr(raw_result, "messages", None)
    if isinstance(raw_messages, list):
        for raw_message in raw_messages:
            messages.append(
                MCPPromptMessage(
                    role=str(getattr(raw_message, "role", "") or ""),
                    content=build_tool_content_item(getattr(raw_message, "content", None)),
                )
            )

    return MCPPromptResult(
        prompt_name=prompt_name,
        server_name=server_name,
        description=str(getattr(raw_result, "description", "") or ""),
        messages=messages,
        metadata=_dump_model_metadata(raw_result),
    )


def build_resource_spec(raw_resource: Any, server_name: str) -> MCPResourceSpec:
    """将 MCP Resource 定义转换为统一结构。

    Args:
        raw_resource: MCP SDK 返回的 Resource 对象。
        server_name: Resource 所属服务器名称。

    Returns:
        MCPResourceSpec: 统一 Resource 定义。
    """

    raw_icons = getattr(raw_resource, "icons", None)
    size_value = getattr(raw_resource, "size", None)
    size = int(size_value) if isinstance(size_value, int | float) else None
    return MCPResourceSpec(
        uri=str(getattr(raw_resource, "uri", "") or ""),
        server_name=server_name,
        name=str(getattr(raw_resource, "name", "") or ""),
        title=str(getattr(raw_resource, "title", "") or ""),
        description=str(getattr(raw_resource, "description", "") or ""),
        mime_type=str(getattr(raw_resource, "mimeType", "") or ""),
        size=size,
        icons=[build_tool_icon(item) for item in raw_icons] if isinstance(raw_icons, list) else [],
        annotation=build_tool_annotation(getattr(raw_resource, "annotations", None)),
        metadata=_dump_model_metadata(raw_resource),
    )


def build_resource_template_spec(raw_template: Any, server_name: str) -> MCPResourceTemplateSpec:
    """将 MCP Resource Template 定义转换为统一结构。

    Args:
        raw_template: MCP SDK 返回的 ResourceTemplate 对象。
        server_name: 模板所属服务器名称。

    Returns:
        MCPResourceTemplateSpec: 统一模板定义。
    """

    raw_icons = getattr(raw_template, "icons", None)
    return MCPResourceTemplateSpec(
        uri_template=str(getattr(raw_template, "uriTemplate", "") or ""),
        server_name=server_name,
        name=str(getattr(raw_template, "name", "") or ""),
        title=str(getattr(raw_template, "title", "") or ""),
        description=str(getattr(raw_template, "description", "") or ""),
        mime_type=str(getattr(raw_template, "mimeType", "") or ""),
        icons=[build_tool_icon(item) for item in raw_icons] if isinstance(raw_icons, list) else [],
        annotation=build_tool_annotation(getattr(raw_template, "annotations", None)),
        metadata=_dump_model_metadata(raw_template),
    )


def build_resource_read_result(raw_result: Any, uri: str, server_name: str) -> MCPResourceReadResult:
    """将 MCP Resource 读取结果转换为统一结构。

    Args:
        raw_result: MCP SDK 返回的读取结果对象。
        uri: 被读取的资源 URI。
        server_name: 资源所属服务器名称。

    Returns:
        MCPResourceReadResult: 统一资源读取结果。
    """

    contents: list[ToolContentItem] = []
    raw_contents = getattr(raw_result, "contents", None)
    if isinstance(raw_contents, list):
        for raw_content in raw_contents:
            metadata = _dump_model_metadata(raw_content)
            contents.append(
                ToolContentItem(
                    content_type="resource",
                    text=str(getattr(raw_content, "text", "") or ""),
                    data=str(getattr(raw_content, "blob", "") or ""),
                    mime_type=str(getattr(raw_content, "mimeType", "") or ""),
                    uri=str(getattr(raw_content, "uri", "") or uri),
                    annotation=None,
                    metadata=metadata,
                )
            )

    return MCPResourceReadResult(
        uri=uri,
        server_name=server_name,
        contents=contents,
        metadata=_dump_model_metadata(raw_result),
    )
