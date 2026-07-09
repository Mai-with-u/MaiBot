from __future__ import annotations

from typing import Any

from src.config.config import global_config


def _integration_config() -> Any:
    return global_config.a_memorix.integration


def feedback_signal_tokens() -> tuple[str, ...]:
    return (
        "不对",
        "错了",
        "你记错",
        "记错了",
        "不是",
        "并不是",
        "纠正",
        "更正",
        "改成",
        "应该是",
        "实际是",
        "说反了",
    )


def feedback_contains_signal(text: str) -> bool:
    content = str(text or "").strip().lower()
    if not content:
        return False
    return any(token in content for token in feedback_signal_tokens())


def feedback_noise(text: str) -> bool:
    content = str(text or "").strip()
    if not content:
        return True
    if feedback_contains_signal(content):
        return False
    if len(content) <= 2:
        return True
    markers = (
        "哈哈",
        "好的",
        "收到",
        "谢谢",
        "嗯嗯",
        "晚安",
        "早安",
        "拜拜",
        "在吗",
    )
    return len(content) <= 8 and any(marker in content for marker in markers)


def feedback_cfg_enabled() -> bool:
    return bool(getattr(_integration_config(), "feedback_correction_enabled", False))


def feedback_cfg_window_hours() -> float:
    return max(0.1, float(getattr(_integration_config(), "feedback_correction_window_hours", 12.0) or 12.0))


def feedback_cfg_check_interval_seconds() -> float:
    minutes = max(1, int(getattr(_integration_config(), "feedback_correction_check_interval_minutes", 30) or 30))
    return float(minutes) * 60.0


def feedback_cfg_batch_size() -> int:
    return max(1, int(getattr(_integration_config(), "feedback_correction_batch_size", 20) or 20))


def feedback_cfg_auto_apply_threshold() -> float:
    value = float(getattr(_integration_config(), "feedback_correction_auto_apply_threshold", 0.85) or 0.85)
    return min(1.0, max(0.0, value))


def feedback_cfg_max_messages() -> int:
    return max(1, int(getattr(_integration_config(), "feedback_correction_max_feedback_messages", 30) or 30))


def feedback_cfg_prefilter_enabled() -> bool:
    return bool(getattr(_integration_config(), "feedback_correction_prefilter_enabled", True))


def feedback_cfg_paragraph_mark_enabled() -> bool:
    return bool(getattr(_integration_config(), "feedback_correction_paragraph_mark_enabled", True))


def feedback_cfg_paragraph_hard_filter_enabled() -> bool:
    return bool(getattr(_integration_config(), "feedback_correction_paragraph_hard_filter_enabled", True))


def feedback_cfg_profile_refresh_enabled() -> bool:
    return bool(getattr(_integration_config(), "feedback_correction_profile_refresh_enabled", True))


def feedback_cfg_profile_force_refresh_on_read() -> bool:
    return bool(getattr(_integration_config(), "feedback_correction_profile_force_refresh_on_read", True))


def feedback_cfg_episode_rebuild_enabled() -> bool:
    return bool(getattr(_integration_config(), "feedback_correction_episode_rebuild_enabled", True))


def feedback_cfg_episode_query_block_enabled() -> bool:
    return bool(getattr(_integration_config(), "feedback_correction_episode_query_block_enabled", True))


def feedback_cfg_reconcile_interval_seconds() -> float:
    minutes = max(1, int(getattr(_integration_config(), "feedback_correction_reconcile_interval_minutes", 5) or 5))
    return float(minutes) * 60.0


def feedback_cfg_reconcile_batch_size() -> int:
    return max(1, int(getattr(_integration_config(), "feedback_correction_reconcile_batch_size", 20) or 20))


def feedback_cfg_window_label() -> str:
    hours = feedback_cfg_window_hours()
    if abs(hours - round(hours)) < 1e-9:
        return f"{int(round(hours))}h"
    return f"{hours:.2f}h"


def fuzzy_modify_cfg_enabled() -> bool:
    return bool(getattr(_integration_config(), "fuzzy_modify_enabled", True))


def fuzzy_modify_cfg_auto_execute_enabled() -> bool:
    return bool(getattr(_integration_config(), "fuzzy_modify_auto_execute_enabled", False))


def fuzzy_modify_cfg_confirm_threshold() -> float:
    return float(getattr(_integration_config(), "fuzzy_modify_confirm_threshold", 0.85) or 0.85)


def fuzzy_modify_cfg_candidate_limit() -> int:
    return max(1, int(getattr(_integration_config(), "fuzzy_modify_candidate_limit", 20) or 20))


def fuzzy_modify_cfg_max_targets() -> int:
    return max(1, int(getattr(_integration_config(), "fuzzy_modify_max_targets", 5) or 5))


def fuzzy_modify_cfg_allow_global_scope() -> bool:
    return bool(getattr(_integration_config(), "fuzzy_modify_allow_global_scope", False))
