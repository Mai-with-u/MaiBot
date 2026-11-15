"""wait 内置工具。"""

from typing import Any, Dict, Optional

from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec

from .context import BuiltinToolRuntimeContext


def resolve_wait_seconds(
    arguments: Dict[str, Any], *, seconds_key: str = "seconds",
) -> Optional[int]:
    """解析可选的等待时间；未指定时间表示结束本轮思考。"""
    if seconds_key in arguments:
        seconds = arguments[seconds_key]
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
            raise ValueError(f"{seconds_key} 必须是非负整数")
        return seconds
    return None


def get_tool_spec() -> ToolSpec:
    """获取 wait 工具声明。"""

    return ToolSpec(
        name="wait",
        description="暂停当前思考，不发送消息。不填 seconds 表示结束本轮，等待新消息；填写 seconds 表示定时等待，到期后即使没有新消息也会继续思考。定时等待可能被新消息提前唤醒。",
        parameters_schema={
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "等待秒数。",
                },
            },
            "additionalProperties": False,
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

    wait_seconds = resolve_wait_seconds(invocation.arguments)
    if context is not None and context.is_focus:
        # Focus 的暂停作用于整个共享组，不改变单个成员的普通循环状态。
        return tool_ctx.build_success_result(
            invocation.tool_name,
            "已结束本轮思考，等待新消息。" if wait_seconds is None else f"已请求整组等待，最多 {wait_seconds} 秒。",
            metadata={"pause_execution": True, "wait_seconds": wait_seconds},
        )
    if wait_seconds is None:
        tool_ctx.engine._end_planner_no_tool_cycle([], status_line="状态：已结束本轮思考")
        return tool_ctx.build_success_result(
            invocation.tool_name, "已结束本轮思考，等待新消息。", metadata={"pause_execution": True},
        )
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
