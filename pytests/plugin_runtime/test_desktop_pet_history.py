from unittest.mock import AsyncMock, Mock

import pytest

from src.chat.message_receive.chat_manager import chat_manager
from src.desktop_pet.rendering import render_history_events
from src.desktop_pet.service import DesktopPetService
from src.maisaka.monitor import event_store


@pytest.mark.asyncio
async def test_history_reuses_monitor_cursor_and_only_resolves_owner_sessions(monkeypatch):
    service = DesktopPetService()
    command = AsyncMock()
    monkeypatch.setattr(service, "_command", command)
    resolve = Mock(return_value={"owner-session"})
    monkeypatch.setattr(chat_manager, "resolve_session_ids_by_target", resolve)
    load = Mock(return_value=event_store.MonitorEventPage(
        events=[{"event": "message.sent", "data": {
            "event_id": 25, "message_id": "reply", "content": "历史回复", "media": [],
        }}], oldest_event_id=20, has_more=True,
    ))
    monkeypatch.setattr(event_store, "load_session_event_page", load)

    await service._publish_history({"request_id": "first", "cursors": {}})
    load.assert_called_once_with(session_id="owner-session", before_event_id=0)
    response = command.await_args.args[0]
    assert response["cursors"] == {"owner-session": 20}
    assert response["has_more"]
    assert response["items"] == [{"id": "reply", "who": "mai",
                                  "parts": [{"kind": "text", "text": "历史回复"}]}]

    load.reset_mock()
    load.return_value = event_store.MonitorEventPage(events=[], oldest_event_id=0, has_more=False)
    await service._publish_history({"request_id": "older", "cursors": {"owner-session": 20, "other": 99}})
    load.assert_called_once_with(session_id="owner-session", before_event_id=20)
    assert command.await_args.args[0]["has_more"] is False


@pytest.mark.asyncio
async def test_history_failure_is_reported_without_advancing_cursor(monkeypatch):
    service = DesktopPetService()
    command = AsyncMock()
    monkeypatch.setattr(service, "_command", command)
    monkeypatch.setattr(chat_manager, "resolve_session_ids_by_target", Mock(return_value={"owner-session"}))
    monkeypatch.setattr(event_store, "load_session_event_page", Mock(side_effect=RuntimeError("database unavailable")))
    await service._publish_history({"request_id": "failed", "cursors": {"owner-session": 20}})
    assert command.await_args.args[0] == {
        "type": "history", "request_id": "failed", "error": "database unavailable",
    }


def test_history_renders_messages_and_merges_media_updates():
    events = [
        {"event": "planner.started", "data": {}},
        {"event": "message.ingested", "data": {"message_id": "user", "content": "处理中"}},
        {"event": "message.updated", "data": {"message_id": "user", "content": "图片内容"}},
        {"event": "message.sent", "data": {"message_id": "bot", "content": "收到"}},
    ]
    items = render_history_events(events)
    assert [item["id"] for item in items] == ["user", "bot"]
    assert items[0]["parts"] == [{"kind": "text", "text": "图片内容"}]
    assert [item["who"] for item in items] == ["me", "mai"]
