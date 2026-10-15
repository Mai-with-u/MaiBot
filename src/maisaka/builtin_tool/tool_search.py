"""tool_search 内置工具。"""

from typing import Any, Dict, List, Optional

from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec

from .context import BuiltinToolRuntimeContext


def get_tool_spec() -> ToolSpec:
    """获取 tool_search 工具声明。"""

    return ToolSpec(
        name="tool_search",
        description="在 deferred tools 列表中按名称或关键词搜索工具，并将命中的工具加入后续轮次的可用工具列表。",
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要搜索的工具名、前缀或关键词。",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回多少个工具。",
                    "minimum": 1,
                },
            },
            "required": ["query"],
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行 tool_search 内置工具。"""

    del context
    raw_query = invocation.arguments.get("query")
    if not isinstance(raw_query, str) or not raw_query.strip():
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "tool_search 需要提供非空的 `query` 字符串参数。",
        )

    raw_limit = invocation.arguments.get("limit", 5)
    try:
        limit = max(1, int(raw_limit))
    except (TypeError, ValueError):
        limit = 5

    matched_tool_specs = tool_ctx.runtime.search_deferred_tool_specs(raw_query, limit=limit)
    matched_tool_names = [tool_spec.name for tool_spec in matched_tool_specs]
    newly_discovered_tool_names = tool_ctx.runtime.discover_deferred_tools(matched_tool_names)

    structured_content: Dict[str, Any] = {
        "query": raw_query.strip(),
        "matched_tool_names": matched_tool_names,
        "newly_discovered_tool_names": newly_discovered_tool_names,
    }

    if not matched_tool_names:
        return tool_ctx.build_success_result(
            invocation.tool_name,
            "未找到匹配的 deferred tools，请尝试更完整的工具名、前缀或其他关键词。",
            structured_content=structured_content,
        )

    newly_discovered_tool_name_set = set(newly_discovered_tool_names)
    content_lines: List[str] = [
        f"已找到 {len(matched_tool_names)} 个 deferred tools，它们会在后续轮次中加入可用工具列表：",
        *[
            f"- {tool_name}{'（本次新发现）' if tool_name in newly_discovered_tool_name_set else '（此前已发现）'}"
            for tool_name in matched_tool_names
        ],
    ]

    return tool_ctx.build_success_result(
        invocation.tool_name,
        "\n".join(content_lines),
        structured_content=structured_content,
        metadata={
            "monitor_detail": {**structured_content, "content": "\n".join(content_lines)},
            "matched_tool_names": matched_tool_names,
            "newly_discovered_tool_names": newly_discovered_tool_names,
        },
    )
