"""wait 内置工具。"""

from datetime import datetime
from math import ceil
from typing import Optional

from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec

from .context import BuiltinToolRuntimeContext


def get_tool_spec() -> ToolSpec:
    """获取 wait 工具声明。"""

    return ToolSpec(
        name="wait",
        description="等待指定秒数或等到绝对时间；新消息可能提前唤醒。seconds 与 until 必须二选一。",
        parameters_schema={
            "type": "object",
            "properties": {
                "until": {"type": "string", "description": "ISO 8601 绝对时间，建议携带时区偏移，如 2026-11-02T20:30:00+08:00；无时区按服务器本地时间。"},
                "seconds": {
                    "type": "integer",
                    "description": "等待秒数。",
                },
            },
            "oneOf": [{"required": ["seconds"], "not": {"required": ["until"]}},
                      {"required": ["until"], "not": {"required": ["seconds"]}}],
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
    args = invocation.arguments
    if ("seconds" in args) == ("until" in args):
        raise ValueError("wait 的 seconds 和 until 必须二选一")
    if "until" in args:
        deadline = datetime.fromisoformat(args["until"])
        now = datetime.now(deadline.tzinfo)
        wait_seconds = max(0, ceil((deadline - now).total_seconds()))
    else:
        seconds = args["seconds"]
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
            raise ValueError("wait.seconds 必须是非负整数")
        wait_seconds = seconds
    current_count = tool_ctx.runtime._enter_counted_wait_state(
        seconds=wait_seconds,
        tool_call_id=invocation.call_id,
        logical_turn_id=tool_ctx.engine.active_logical_turn_id,
    )
    wait_detail = f"当前对话循环进入等待，最多 {wait_seconds} 秒；私聊新消息经防抖后唤醒，群聊按随等待进度增长的概率唤醒，到期必定继续。"
    return tool_ctx.build_success_result(
        invocation.tool_name,
        f"{wait_detail}连续 wait 次数：{current_count}。",
        metadata={
            "pause_execution": True,
            "consecutive_wait_count": current_count,
        },
    )
