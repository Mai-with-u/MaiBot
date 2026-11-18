"""Maisaka Planner-only 模式策略。"""

from typing import List

from src.common.prompt_i18n import load_prompt


IDLE_CYCLE_REASONS = {
    "planner_no_tool_end",
    "tool_pause:wait",
    "tool_stop_after_execution",
}


def is_idle_cycle_reason(cycle_end_reason: str) -> bool:
    """判断整轮结束原因是否属于空闲退避。"""

    return str(cycle_end_reason).strip() in IDLE_CYCLE_REASONS


def build_consecutive_wait_reminders(consecutive_wait_count: int) -> List[str]:
    """连续等待三次后，在每轮请求尾部提醒，直到其他操作重置计数。"""

    if consecutive_wait_count < 3:
        return []
    return [load_prompt("maisaka_wait_reminder").strip()]
