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


def is_jev_trigger_enabled() -> bool:
    """判断是否启用逐条 Jev 决策触发门。"""

    return get_reply_trigger_mode() == "jev"


def is_jev_batch_trigger_enabled() -> bool:
    """判断是否启用到量 Jev 决策触发门。"""

    return get_reply_trigger_mode() == "jev_batch"


def is_jev_decision_enabled() -> bool:
    """判断当前是否使用 Jev 决策决定是否进入 Planner。"""

    return get_reply_trigger_mode() in ("jev", "jev_batch")


def is_idle_cycle_reason(cycle_end_reason: str) -> bool:
    """判断整轮结束原因是否属于空闲退避。"""

    return str(cycle_end_reason).strip() in IDLE_CYCLE_REASONS
