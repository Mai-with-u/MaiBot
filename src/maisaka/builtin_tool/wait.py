"""wait 内置工具。"""

from typing import Optional

from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec

from .context import BuiltinToolRuntimeContext


def get_tool_spec() -> ToolSpec:
    """获取 wait 工具声明。"""

    return ToolSpec(
        name="wait",
        description="暂停当前对话并固定等待一段时间。",
        parameters_schema={
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "description": "等待秒数。",
                },
            },
            "required": ["seconds"],
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行 wait 内置工具。"""

    del context
    seconds = invocation.arguments.get("seconds", 30)
    try:
        wait_seconds = int(seconds)
    except (TypeError, ValueError):
        wait_seconds = 30
    wait_seconds = max(0, wait_seconds)
    entered, current_count, max_count = tool_ctx.runtime._try_enter_wait_state(
        seconds=wait_seconds,
        tool_call_id=invocation.call_id,
    )
    if not entered:
        return tool_ctx.build_success_result(
            invocation.tool_name,
            f"连续 wait 已达到上限 {max_count} 次，本次不再进入等待；视为多次等待后仍无后续，当前对话进入休息。",
            metadata={
                "pause_execution": True,
                "wait_limit_reached": True,
                "wait_rest": True,
                "consecutive_wait_count": current_count,
                "max_consecutive_wait_count": max_count,
            },
        )

    return tool_ctx.build_success_result(
        invocation.tool_name,
        f"当前对话循环进入等待状态，将固定等待 {wait_seconds} 秒；期间收到的新消息不会提前打断本次等待。"
        f"连续 wait 次数：{current_count}/{max_count}。",
        metadata={
            "pause_execution": True,
            "consecutive_wait_count": current_count,
            "max_consecutive_wait_count": max_count,
        },
    )
