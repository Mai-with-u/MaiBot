"""Maisaka Planner-only 模式策略。"""

IDLE_CYCLE_REASONS = {
    "planner_no_tool_end",
    "planner_wait_rest",
    "tool_pause:wait",
    "tool_stop_after_execution",
}


def is_idle_cycle_reason(cycle_end_reason: str) -> bool:
    """判断整轮结束原因是否属于空闲退避。"""

    return str(cycle_end_reason).strip() in IDLE_CYCLE_REASONS
