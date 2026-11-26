from unittest.mock import AsyncMock, Mock
import json

import pytest

from src.chat.message_receive.chat_manager import chat_manager
from src.core.internal_chat import INTERNAL_CHAT_PLATFORM, INTERNAL_CHAT_USER_ID
from src.desktop_pet.attachments import read_request
from src.desktop_pet.rendering import render_history_events
from src.desktop_pet.remote_server import validate_chat_request, validate_event, validate_hello
from src.desktop_pet.service import DesktopPetService
from src.maisaka.monitor import event_store


def test_remote_history_accepts_cursor_requests():
    validate_event({"type": "history", "request_id": "page-1", "cursors": {}})
    validate_event({"type": "history", "request_id": "page-2", "cursors": {"owner": 25}})


@pytest.mark.parametrize("cursors", [None, [], {"owner": -1}, {"owner": True}, {"owner": "25"},
                                    {str(index): 1 for index in range(101)}])
def test_remote_history_rejects_invalid_cursors(cursors):
    with pytest.raises(ValueError):
        validate_event({"type": "history", "request_id": "page", "cursors": cursors})


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
    resolve.assert_called_once_with(
        platform=INTERNAL_CHAT_PLATFORM, target_id=INTERNAL_CHAT_USER_ID, chat_type="private",
    )
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


def test_legacy_hello_does_not_enable_chat_binding():
    client_id, capabilities = validate_hello({
        "type": "hello", "version": 1, "client_id": "pet-1",
        "capabilities": {"screen_read": False, "skin_assets": True, "chat_binding": True},
    })
    assert client_id == "pet-1"
    assert capabilities == {"screen_read": False, "skin_assets": True, "skin_loading": False}


@pytest.mark.parametrize("target", [{}, {"conversation_id": ""}, {"conversation_id": None}])
def test_chat_protocol_accepts_owner_messages_from_old_and_new_clients(tmp_path, target):
    payload = {"text": " 私聊 ", "images": [], **target}
    request_id = "a" * 32
    path = tmp_path / f"{request_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    expected = {"text": "私聊", "images": []}
    assert validate_chat_request(payload) == expected
    assert read_request(tmp_path, request_id) == expected
    assert not path.exists()


def test_chat_protocol_rejects_stale_external_target_without_rerouting(tmp_path):
    payload = {"text": "旧草稿", "images": [], "conversation_id": "qq-group"}
    request_id = "a" * 32
    path = tmp_path / f"{request_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="已不支持绑定聊天流"):
        validate_chat_request(payload)
    with pytest.raises(ValueError, match="已不支持绑定聊天流"):
        read_request(tmp_path, request_id)
    assert not path.exists()
