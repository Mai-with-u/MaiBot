"""stop 内置工具。"""

from typing import Optional

from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec

from .context import BuiltinToolRuntimeContext


def get_tool_spec() -> ToolSpec:
    """获取 stop 工具声明。"""

    return ToolSpec(
        name="stop",
        description="不发送回复，结束本轮思考并等待新消息；无需继续行动时单独调用。",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """结束当前思考轮，Focus 共享组由调度器根据暂停信号收尾。"""

    if context is None or not context.is_focus:
        tool_ctx.engine._end_planner_no_tool_cycle([], status_line="状态：已结束本轮思考")
    return tool_ctx.build_success_result(
        invocation.tool_name,
        "已结束本轮思考，等待新消息。",
        metadata={"pause_execution": True},
    )
