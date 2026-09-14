"""Maisaka Planner-only 模式策略。"""

from src.config.config import global_config

IDLE_CYCLE_REASONS = {
    "planner_no_tool_end",
    "planner_wait_rest",
    "tool_pause:wait",
    "tool_stop_after_execution",
}


def get_reply_trigger_mode() -> str:
    """读取当前回复触发模式。"""

    return global_config.chat.reply_timing.reply_trigger_mode


def is_reply_necessity_trigger_enabled() -> bool:
    """判断是否启用回复必要性触发门。"""

    return get_reply_trigger_mode() == "reply_necessity"


def is_idle_cycle_reason(cycle_end_reason: str) -> bool:
    """判断整轮结束原因是否属于空闲退避。"""

    return str(cycle_end_reason).strip() in IDLE_CYCLE_REASONS
