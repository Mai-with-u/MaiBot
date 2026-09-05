"""Maisaka 阶段状态展示投影。

阶段账本由 ``src.maisaka.monitor.stage_status`` 维护。本模块只把账本投影成
WebUI / CLI 当前可见字段，运行时写入仍经由此处转发到 monitor。
"""

from typing import Any

from src.maisaka.monitor.stage_status import (
    STAGE_STATUS_ENTRY_KEYS,
    get_stage_status_snapshot as get_monitor_stage_status_snapshot,
    remove_stage_status as remove_monitor_stage_status,
    update_stage_status as update_monitor_stage_status,
)


def update_stage_status(
    *,
    session_id: str,
    session_name: str,
    stage: str,
    detail: str = "",
    round_text: str = "",
    agent_state: str = "",
) -> None:
    """把阶段状态写入 monitor 账本。"""

    update_monitor_stage_status(
        session_id=session_id,
        session_name=session_name,
        stage=stage,
        detail=detail,
        round_text=round_text,
        agent_state=agent_state,
    )


def remove_stage_status(session_id: str) -> None:
    """从 monitor 账本移除一个会话的阶段状态。"""

    remove_monitor_stage_status(session_id)


def get_stage_status_snapshot() -> list[dict[str, Any]]:
    """投影当前阶段状态快照，仅保留 WebUI / CLI 可见字段。"""

    return [_project_stage_status_entry(entry) for entry in get_monitor_stage_status_snapshot()]


def _project_stage_status_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: entry[key] for key in STAGE_STATUS_ENTRY_KEYS}
