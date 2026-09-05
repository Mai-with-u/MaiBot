"""WebUI 删除聊天流时必须 stop 心流 runtime。

仅从注册表 pop 不够：删除路径必须真正 await runtime.stop。
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

chat_streams_routes = pytest.importorskip("src.webui.routers.chat.chat_streams.routes")
heartflow_manager_module = pytest.importorskip("src.chat.heart_flow.heartflow_manager")

HeartflowManager = heartflow_manager_module.HeartflowManager


def _insert_runtime(manager: HeartflowManager, session_id: str) -> MagicMock:
    """向注册表插入带 stop spy 的假心流 runtime。"""
    runtime = MagicMock(name=session_id)
    runtime.stop = AsyncMock()
    manager.heartflow_chat_list[session_id] = runtime
    return runtime


def _scope_result(session_id: str) -> Dict[str, Any]:
    """构造删除聊天流数据库范围的成功返回值。"""
    return {
        "success": True,
        "session_id": session_id,
        "deleted_total": 1,
        "jargons": {"deleted": 0, "unlinked": 0, "removed_refs": 0},
        "items": [],
    }


@pytest.mark.asyncio
async def test_webui_delete_path_stops_heartflow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """WebUI 删除路径必须 await 心流 runtime.stop；仅 pop 不再足够。"""
    session_id = "session-to-delete"
    leftover_id = "session-keep"

    manager = HeartflowManager()
    runtime = _insert_runtime(manager, session_id)
    leftover = _insert_runtime(manager, leftover_id)
    stop_spy = runtime.stop
    fake_sessions = {session_id: object(), leftover_id: object()}

    monkeypatch.setattr(chat_streams_routes, "heartflow_manager", manager)
    monkeypatch.setattr(chat_streams_routes.core_chat_manager, "sessions", fake_sessions)
    monkeypatch.setattr(
        chat_streams_routes,
        "_delete_chat_session_scope",
        lambda _sid: _scope_result(session_id),
    )

    response = await chat_streams_routes.delete_chat_session(session_id)

    assert response == _scope_result(session_id)
    stop_spy.assert_awaited_once()
    leftover.stop.assert_not_called()
    assert manager.get_heartflow_chat(session_id) is None
    assert manager.get_heartflow_chat(leftover_id) is leftover
    assert session_id not in fake_sessions
    assert leftover_id in fake_sessions
