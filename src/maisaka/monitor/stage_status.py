"""麦麦观察阶段状态账本。"""

from typing import Any

import asyncio
import threading
import time

# WebUI / CLI 当前可见的阶段字段；snapshot 与广播都必须保持这些 key。
STAGE_STATUS_ENTRY_KEYS = (
    "session_id",
    "session_name",
    "stage",
    "detail",
    "round_text",
    "agent_state",
    "stage_started_at",
    "updated_at",
    "timestamp",
)


class MaisakaStageStatusStore:
    """维护各聊天流的当前阶段状态。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}

    def update(
        self,
        *,
        session_id: str,
        session_name: str,
        stage: str,
        detail: str = "",
        round_text: str = "",
        agent_state: str = "",
    ) -> dict[str, Any]:
        """写入一个会话的阶段状态，并返回可广播的载荷。"""

        now = time.time()
        with self._lock:
            current = self._entries.get(session_id, {})
            previous_stage = str(current.get("stage") or "").strip()
            stage_started_at = float(current.get("stage_started_at") or now)
            if previous_stage != stage:
                stage_started_at = now

            payload = {
                "session_id": session_id,
                "session_name": session_name,
                "stage": stage,
                "detail": detail,
                "round_text": round_text,
                "agent_state": agent_state,
                "stage_started_at": stage_started_at,
                "updated_at": now,
                "timestamp": now,
            }
            self._entries[session_id] = payload
            return dict(payload)

    def remove(self, session_id: str) -> dict[str, Any] | None:
        """移除一个会话的阶段状态。"""

        with self._lock:
            removed = self._entries.pop(session_id, None)
        if removed is None:
            return None
        return dict(removed)

    def snapshot(self) -> list[dict[str, Any]]:
        """返回当前所有聊天流的阶段状态快照。"""

        with self._lock:
            return [_copy_stage_status_entry(entry) for entry in self._entries.values()]


def _copy_stage_status_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: entry[key] for key in STAGE_STATUS_ENTRY_KEYS}


_stage_store = MaisakaStageStatusStore()


def update_stage_status(
    *,
    session_id: str,
    session_name: str,
    stage: str,
    detail: str = "",
    round_text: str = "",
    agent_state: str = "",
) -> dict[str, Any]:
    """更新阶段状态账本，并调度 WebSocket 广播。"""

    payload = _stage_store.update(
        session_id=session_id,
        session_name=session_name,
        stage=stage,
        detail=detail,
        round_text=round_text,
        agent_state=agent_state,
    )
    _schedule_stage_status_event(payload)
    return payload


def remove_stage_status(session_id: str) -> None:
    """从阶段状态账本移除一个会话，并调度移除广播。"""

    removed = _stage_store.remove(session_id)
    _schedule_stage_removed_event(session_id, removed)


def get_stage_status_snapshot() -> list[dict[str, Any]]:
    """获取当前阶段状态快照。"""

    return _stage_store.snapshot()


def _schedule_stage_status_event(payload: dict[str, Any]) -> None:
    try:
        from src.maisaka.monitor.events import emit_stage_status

        asyncio.get_running_loop().create_task(
            emit_stage_status(
                session_id=payload["session_id"],
                session_name=payload["session_name"],
                stage=payload["stage"],
                detail=payload["detail"],
                round_text=payload["round_text"],
                agent_state=payload["agent_state"],
                stage_started_at=payload["stage_started_at"],
                updated_at=payload["updated_at"],
                timestamp=payload["timestamp"],
            )
        )
    except RuntimeError:
        return


def _schedule_stage_removed_event(session_id: str, removed: dict[str, Any] | None) -> None:
    try:
        from src.maisaka.monitor.events import emit_stage_removed

        asyncio.get_running_loop().create_task(
            emit_stage_removed(
                session_id=session_id,
                session_name=str((removed or {}).get("session_name") or ""),
            )
        )
    except RuntimeError:
        return
