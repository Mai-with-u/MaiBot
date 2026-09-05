from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from src.common.database.database_model import ChatSession
from src.webui.routers.chat.chat_streams import routes as chat_streams_routes
from src.webui.routers.chat.local_chat import routes as local_chat_routes


class _DetachedGuardPerson:
    def __init__(self) -> None:
        self.closed = False
        self.person_id = "person-1"
        self.user_id = "user-1"
        self.person_name = "Test User"
        self.user_nickname = "Test Nickname"
        self.is_known = True
        self.platform = "qq"

    def __getattribute__(self, name: str) -> Any:
        if name not in {"closed", "__dict__", "__class__", "__getattribute__"}:
            if object.__getattribute__(self, "closed"):
                raise RuntimeError("person attribute accessed after session closed")
        return object.__getattribute__(self, name)


class _FakeExecResult:
    def __init__(self, person: _DetachedGuardPerson) -> None:
        self.person = person

    def all(self) -> list[_DetachedGuardPerson]:
        return [self.person]


class _FakeSession:
    def __init__(self, person: _DetachedGuardPerson) -> None:
        self.person = person

    def exec(self, statement: Any) -> _FakeExecResult:
        del statement
        return _FakeExecResult(self.person)


@pytest.mark.asyncio
async def test_get_persons_by_platform_serializes_before_session_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    person = _DetachedGuardPerson()

    @contextmanager
    def fake_get_db_session() -> Iterator[_FakeSession]:
        try:
            yield _FakeSession(person)
        finally:
            person.closed = True

    monkeypatch.setattr(local_chat_routes, "get_db_session", fake_get_db_session)

    response = await local_chat_routes.get_persons_by_platform(platform="qq", limit=50)

    assert response == {
        "success": True,
        "persons": [
            {
                "person_id": "person-1",
                "user_id": "user-1",
                "person_name": "Test User",
                "nickname": "Test Nickname",
                "is_known": True,
                "platform": "qq",
                "display_name": "Test User",
            }
        ],
        "total": 1,
    }


def test_group_display_name_ignores_private_latest_message_identity() -> None:
    chat_session = ChatSession(
        session_id="group-session",
        platform="qq",
        group_id="571780722",
        group_name="麦麦脑电图｜技术交流群｜部署/配置",
    )
    latest_message = SimpleNamespace(
        group_id=None,
        group_name=None,
        user_id="2814567326",
        user_nickname="麦麦",
        user_cardname=None,
    )

    assert chat_streams_routes._get_chat_display_name(chat_session, latest_message) == "麦麦脑电图｜技术交流群｜部署/配置"


@pytest.mark.asyncio
async def test_delete_chat_session_stops_heartflow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """删除聊天流成功后应 stop 心流 runtime，并 pop 运行期 session。"""

    session_id = "session-to-delete"
    fake_sessions = {session_id: object()}
    stop_heartflow = AsyncMock()
    delete_result = {
        "success": True,
        "session_id": session_id,
        "deleted_total": 1,
        "jargons": {"deleted": 0, "unlinked": 0, "removed_refs": 0},
        "items": [],
    }

    monkeypatch.setattr(chat_streams_routes.core_chat_manager, "sessions", fake_sessions)
    monkeypatch.setattr(chat_streams_routes.heartflow_manager, "stop_heartflow_chat", stop_heartflow)
    monkeypatch.setattr(chat_streams_routes, "_delete_chat_session_scope", lambda _sid: delete_result)

    response = await chat_streams_routes.delete_chat_session(session_id)

    assert response == delete_result
    assert session_id not in fake_sessions
    stop_heartflow.assert_awaited_once_with(session_id)


@pytest.mark.asyncio
async def test_delete_chat_session_skips_runtime_release_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """聊天流不存在时不应 stop 心流或 pop session。"""

    session_id = "missing-session"
    fake_sessions = {session_id: object()}
    stop_heartflow = AsyncMock()

    def raise_not_found(_session_id: str) -> dict:
        raise HTTPException(status_code=404, detail=f"聊天流不存在: {_session_id}")

    monkeypatch.setattr(chat_streams_routes.core_chat_manager, "sessions", fake_sessions)
    monkeypatch.setattr(chat_streams_routes.heartflow_manager, "stop_heartflow_chat", stop_heartflow)
    monkeypatch.setattr(chat_streams_routes, "_delete_chat_session_scope", raise_not_found)

    with pytest.raises(HTTPException) as exc_info:
        await chat_streams_routes.delete_chat_session(session_id)

    assert exc_info.value.status_code == 404
    assert session_id in fake_sessions
    stop_heartflow.assert_not_called()
