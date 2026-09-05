from unittest.mock import AsyncMock, MagicMock

import pytest

from src.chat.heart_flow.heartflow_manager import HeartflowManager


def _insert_chat(manager: HeartflowManager, session_id: str) -> MagicMock:
    """向注册表插入带 stop spy 的假心流实例。"""
    chat = MagicMock(name=session_id)
    chat.stop = AsyncMock()
    manager.heartflow_chat_list[session_id] = chat
    return chat


def test_get_heartflow_chat_returns_existing_or_none_without_creating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已存在则原样返回，缺失则返回 None，且不会创建新实例。"""
    manager = HeartflowManager()
    existing = _insert_chat(manager, "session-existing")
    create_mock = AsyncMock()
    constructed = MagicMock(side_effect=AssertionError("get_heartflow_chat 不应创建心流"))
    monkeypatch.setattr(manager, "get_or_create_heartflow_chat", create_mock)
    monkeypatch.setattr(
        "src.chat.heart_flow.heartflow_manager.MaisakaHeartFlowChatting",
        constructed,
    )

    assert manager.get_heartflow_chat("session-existing") is existing
    assert manager.get_heartflow_chat("session-missing") is None
    assert list(manager.iter_heartflow_chats()) == [existing]
    create_mock.assert_not_called()
    constructed.assert_not_called()
    existing.stop.assert_not_called()


def test_iter_heartflow_chats_matches_inserted_values() -> None:
    """iter_heartflow_chats 应按插入顺序返回已注册的心流实例。"""
    manager = HeartflowManager()
    first = _insert_chat(manager, "session-a")
    second = _insert_chat(manager, "session-b")

    assert list(manager.iter_heartflow_chats()) == [first, second]


def test_pop_heartflow_chat_removes_without_calling_stop() -> None:
    """pop_heartflow_chat 只移除注册项，不调用 stop。"""
    manager = HeartflowManager()
    chat = _insert_chat(manager, "session-a")
    leftover = _insert_chat(manager, "session-b")
    stop_spy = chat.stop

    popped = manager.pop_heartflow_chat("session-a")

    assert popped is chat
    stop_spy.assert_not_called()
    assert manager.get_heartflow_chat("session-a") is None
    assert manager.get_heartflow_chat("session-b") is leftover
    assert list(manager.iter_heartflow_chats()) == [leftover]
    assert manager.pop_heartflow_chat("session-missing") is None


@pytest.mark.asyncio
async def test_clear_chat_history_context_still_stops() -> None:
    """清空历史上下文时仍应停止并移除对应心流实例。"""
    manager = HeartflowManager()
    chat = _insert_chat(manager, "session-a")
    leftover = _insert_chat(manager, "session-b")
    stop_spy = chat.stop

    cleared = await manager.clear_chat_history_context("session-a")

    assert cleared is True
    stop_spy.assert_awaited_once()
    assert manager.get_heartflow_chat("session-a") is None
    assert manager.get_heartflow_chat("session-b") is leftover
    leftover.stop.assert_not_called()


@pytest.mark.asyncio
async def test_stop_heartflow_chat_stops_and_removes() -> None:
    """stop_heartflow_chat 应停止并移除对应心流实例，缺失时为 no-op。"""
    manager = HeartflowManager()
    chat = _insert_chat(manager, "session-a")
    leftover = _insert_chat(manager, "session-b")
    stop_spy = chat.stop

    await manager.stop_heartflow_chat("session-a")

    stop_spy.assert_awaited_once()
    assert manager.get_heartflow_chat("session-a") is None
    assert manager.get_heartflow_chat("session-b") is leftover
    leftover.stop.assert_not_called()

    await manager.stop_heartflow_chat("session-missing")
    leftover.stop.assert_not_called()
    assert manager.get_heartflow_chat("session-b") is leftover


def test_heartflow_chat_list_property_still_works_if_present() -> None:
    """若仍暴露 heartflow_chat_list，应与 get/iter/pop 看到同一份注册表。"""
    manager = HeartflowManager()
    if not hasattr(manager, "heartflow_chat_list"):
        pytest.skip("heartflow_chat_list 不存在")

    chat = _insert_chat(manager, "session-a")
    chat_list = manager.heartflow_chat_list

    assert chat_list["session-a"] is chat
    assert chat_list.get("session-a") is chat
    assert manager.get_heartflow_chat("session-a") is chat
    assert list(chat_list.values()) == [chat]
    assert list(manager.iter_heartflow_chats()) == [chat]

    popped = manager.pop_heartflow_chat("session-a")

    assert popped is chat
    assert "session-a" not in chat_list
    assert manager.get_heartflow_chat("session-a") is None
    assert list(manager.iter_heartflow_chats()) == []
