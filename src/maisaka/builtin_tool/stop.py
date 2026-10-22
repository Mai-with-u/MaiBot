"""stop 内置工具。"""

from typing import Optional

from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec

from .context import BuiltinToolRuntimeContext


def get_tool_spec() -> ToolSpec:
    """获取 stop 工具声明。"""

    return ToolSpec(
        name="stop",
        description="结束本轮思考并等待新消息，与不调用任何工具时一致；无需继续行动时调用。",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """复用无工具输出的收尾逻辑，结束当前思考轮。"""

    del context
    tool_ctx.engine._end_planner_no_tool_cycle([], status_line="状态：已结束本轮思考")
    return tool_ctx.build_success_result(
        invocation.tool_name,
        "已结束本轮思考，等待新消息。",
        metadata={"pause_execution": True},
    )
