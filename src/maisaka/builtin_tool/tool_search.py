"""tool_search 内置工具。"""

from typing import Optional

from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.maisaka.tool_discovery import build_tool_search_result

from .context import BuiltinToolRuntimeContext


def get_tool_spec() -> ToolSpec:
    """获取 tool_search 工具声明。"""

    return ToolSpec(
        name="tool_search",
        description="在 deferred tools 列表中按名称或关键词搜索工具，在搜索结果中加载命中工具的定义和调用指引，供后续轮次调用。",
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
    if not isinstance(raw_limit, int) or isinstance(raw_limit, bool) or raw_limit < 1:
        return tool_ctx.build_failure_result(invocation.tool_name, "tool_search 的 `limit` 必须是正整数。")
    limit = raw_limit

    matched_tool_specs = tool_ctx.runtime.search_deferred_tool_specs(raw_query, limit=limit)
    matched_tool_names = [tool_spec.name for tool_spec in matched_tool_specs]
    newly_discovered_tool_names = tool_ctx.runtime.discover_deferred_tools(matched_tool_names)

    structured_content = build_tool_search_result(raw_query.strip(), matched_tool_specs, newly_discovered_tool_names)
    return tool_ctx.build_success_result(
        invocation.tool_name,
        "",
        structured_content=structured_content,
        metadata={
            "monitor_detail": structured_content,
            "matched_tool_names": matched_tool_names,
            "newly_discovered_tool_names": newly_discovered_tool_names,
        },
    )
