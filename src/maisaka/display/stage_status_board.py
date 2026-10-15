"""Maisaka 阶段状态广播。"""

from __future__ import annotations

from typing import Any

import asyncio
import threading
import time


class MaisakaStageStatusBoard:
    """维护 Maisaka 阶段状态，并推送给 WebUI 麦麦观察。"""

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
        wait_until: float | None = None,
    ) -> None:
        """更新一个会话的阶段状态。"""

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
                "wait_until": wait_until,
                "stage_started_at": stage_started_at,
                "updated_at": now,
                "timestamp": now,
            }
            self._entries[session_id] = payload

        self._schedule_stage_status_event(payload)

    def remove(self, session_id: str) -> None:
        """移除一个会话的阶段状态。"""

        with self._lock:
            removed = self._entries.pop(session_id, None)

        self._schedule_stage_removed_event(session_id, removed)

    def snapshot(self) -> list[dict[str, Any]]:
        """返回当前所有聊天流的阶段状态快照。"""

        with self._lock:
            return [dict(entry) for entry in self._entries.values()]

    @staticmethod
    def _schedule_stage_status_event(payload: dict[str, Any]) -> None:
        try:
            from src.maisaka.monitor.events import emit_stage_status

            asyncio.get_running_loop().create_task(emit_stage_status(**payload))
        except RuntimeError:
            return

    @staticmethod
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


_stage_board = MaisakaStageStatusBoard()


def update_stage_status(
    *,
    session_id: str,
    session_name: str,
    stage: str,
    detail: str = "",
    round_text: str = "",
    agent_state: str = "",
    wait_until: float | None = None,
) -> None:
    """更新 WebUI 麦麦观察中的阶段状态。"""

    _stage_board.update(
        session_id=session_id,
        session_name=session_name,
        stage=stage,
        detail=detail,
        round_text=round_text,
        agent_state=agent_state,
        wait_until=wait_until,
    )


def remove_stage_status(session_id: str) -> None:
    """移除 WebUI 麦麦观察中的阶段状态。"""

    _stage_board.remove(session_id)


def get_stage_status_snapshot() -> list[dict[str, Any]]:
    """获取当前阶段状态快照。"""

    return _stage_board.snapshot()
