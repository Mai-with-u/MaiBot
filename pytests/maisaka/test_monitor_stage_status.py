"""麦麦观察阶段状态账本与 display 投影测试。"""

from src.maisaka.display.stage_status_board import (
    get_stage_status_snapshot as get_display_stage_status_snapshot,
    remove_stage_status as remove_display_stage_status,
    update_stage_status as update_display_stage_status,
)
from src.maisaka.monitor.stage_status import (
    MaisakaStageStatusStore,
    STAGE_STATUS_ENTRY_KEYS,
    get_stage_status_snapshot,
    update_stage_status,
)
import src.maisaka.monitor.stage_status as stage_status_module


def _use_isolated_store(monkeypatch) -> MaisakaStageStatusStore:
    store = MaisakaStageStatusStore()
    monkeypatch.setattr(stage_status_module, "_stage_store", store)
    return store


def test_display_snapshot_projects_monitor_visible_keys(monkeypatch) -> None:
    _use_isolated_store(monkeypatch)
    update_display_stage_status(
        session_id="session-1",
        session_name="测试群",
        stage="Planner",
        detail="组织上下文并请求模型",
        round_text="R1",
        agent_state="thinking",
    )

    monitor_entries = get_stage_status_snapshot()
    display_entries = get_display_stage_status_snapshot()

    assert len(monitor_entries) == 1
    assert display_entries == monitor_entries
    assert tuple(display_entries[0]) == STAGE_STATUS_ENTRY_KEYS
    assert display_entries[0]["session_id"] == "session-1"
    assert display_entries[0]["session_name"] == "测试群"
    assert display_entries[0]["stage"] == "Planner"
    assert display_entries[0]["detail"] == "组织上下文并请求模型"
    assert display_entries[0]["round_text"] == "R1"
    assert display_entries[0]["agent_state"] == "thinking"


def test_same_stage_keeps_stage_started_at(monkeypatch) -> None:
    _use_isolated_store(monkeypatch)
    update_stage_status(session_id="s", session_name="n", stage="Planner", detail="a")
    first = get_stage_status_snapshot()[0]
    update_stage_status(session_id="s", session_name="n", stage="Planner", detail="b")
    second = get_stage_status_snapshot()[0]

    assert second["stage_started_at"] == first["stage_started_at"]
    assert second["detail"] == "b"
    assert second["updated_at"] >= first["updated_at"]


def test_stage_change_resets_stage_started_at(monkeypatch) -> None:
    _use_isolated_store(monkeypatch)
    update_stage_status(session_id="s", session_name="n", stage="Planner")
    first = get_stage_status_snapshot()[0]
    update_stage_status(session_id="s", session_name="n", stage="Replyer")
    second = get_stage_status_snapshot()[0]

    assert second["stage"] == "Replyer"
    assert second["stage_started_at"] >= first["stage_started_at"]
    assert second["stage_started_at"] == second["updated_at"]


def test_display_remove_clears_monitor_snapshot(monkeypatch) -> None:
    _use_isolated_store(monkeypatch)
    update_display_stage_status(session_id="s", session_name="n", stage="空闲")
    remove_display_stage_status("s")

    assert get_stage_status_snapshot() == []
    assert get_display_stage_status_snapshot() == []


def test_stage_snapshot_event_payload_keys(monkeypatch) -> None:
    _use_isolated_store(monkeypatch)
    update_stage_status(session_id="s", session_name="n", stage="空闲")
    payload = {
        "entries": get_stage_status_snapshot(),
        "timestamp": 1.0,
    }

    assert tuple(payload) == ("entries", "timestamp")
    assert tuple(payload["entries"][0]) == STAGE_STATUS_ENTRY_KEYS
