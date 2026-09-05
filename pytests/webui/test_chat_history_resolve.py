"""ChatHistoryManager 应按真实聊天流解析 session_id，而不是自行哈希。"""

from types import SimpleNamespace
from typing import Any
import inspect

import pytest

from src.chat.message_receive.chat_manager import chat_manager as core_chat_manager
from src.common.utils.utils_session import SessionUtils
from src.webui.routers.chat.service import ChatHistoryManager, VIRTUAL_GROUP_ID_PREFIX, WEBUI_CHAT_PLATFORM


def _forbid_calculate_session_id(*args: Any, **kwargs: Any) -> str:
    del args, kwargs
    raise AssertionError("ChatHistoryManager 不应调用 SessionUtils.calculate_session_id")


@pytest.fixture
def history_resolve_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """拦截核心聊天流解析，并禁止 ChatHistoryManager 自行计算 session_id。"""

    resolve_calls: list[dict[str, Any]] = []
    find_calls: list[dict[str, Any]] = []
    matched_sessions: list[Any] = []

    def fake_resolve(*, platform: str, target_id: str, chat_type: str) -> list[Any]:
        resolve_calls.append(
            {
                "platform": platform,
                "target_id": target_id,
                "chat_type": chat_type,
            }
        )
        return list(matched_sessions)

    def fake_find_messages(**kwargs: Any) -> list[Any]:
        find_calls.append(kwargs)
        return []

    monkeypatch.setattr(core_chat_manager, "resolve_sessions_by_target", fake_resolve)
    monkeypatch.setattr("src.webui.routers.chat.service.find_messages", fake_find_messages)
    monkeypatch.setattr(SessionUtils, "calculate_session_id", _forbid_calculate_session_id)

    return SimpleNamespace(
        manager=ChatHistoryManager(),
        resolve_calls=resolve_calls,
        find_calls=find_calls,
        matched_sessions=matched_sessions,
    )


def test_chat_history_manager_不调用_calculate_session_id() -> None:
    """ChatHistoryManager 不得自行调用 SessionUtils.calculate_session_id。"""

    source = inspect.getsource(ChatHistoryManager)
    assert "calculate_session_id" not in source


def test_零匹配时返回空且不查询消息(history_resolve_env: SimpleNamespace) -> None:
    """resolve_sessions_by_target 零匹配时，get_history 应返回空列表且不调用 find_messages。"""

    result = history_resolve_env.manager.get_history(
        limit=50,
        group_id="group-1",
        platform="qq",
    )

    assert result == []
    assert history_resolve_env.resolve_calls == [
        {
            "platform": "qq",
            "target_id": "group-1",
            "chat_type": "group",
        }
    ]
    assert history_resolve_env.find_calls == []


def test_单匹配时用该session_id查询消息(history_resolve_env: SimpleNamespace) -> None:
    """唯一匹配时应把该聊天流的 session_id 传给 find_messages。"""

    history_resolve_env.matched_sessions.append(SimpleNamespace(session_id="real-session-id"))

    result = history_resolve_env.manager.get_history(
        limit=10,
        user_id="webui_user_alice",
        platform="webui",
    )

    assert result == []
    assert history_resolve_env.resolve_calls == [
        {
            "platform": "webui",
            "target_id": "webui_user_alice",
            "chat_type": "private",
        }
    ]
    assert len(history_resolve_env.find_calls) == 1
    assert history_resolve_env.find_calls[0]["session_id"] == "real-session-id"
    assert history_resolve_env.find_calls[0]["limit"] == 10


def test_虚拟群聊使用提供的platform而不是webui(history_resolve_env: SimpleNamespace) -> None:
    """虚拟群聊应按调用方提供的平台解析，而不是写死 webui。"""

    group_id = f"{VIRTUAL_GROUP_ID_PREFIX}telegram_1001"
    history_resolve_env.matched_sessions.append(SimpleNamespace(session_id="virtual-group-session"))

    history_resolve_env.manager.get_history(
        limit=20,
        group_id=group_id,
        platform="telegram",
    )

    assert len(history_resolve_env.resolve_calls) == 1
    assert history_resolve_env.resolve_calls[0]["platform"] == "telegram"
    assert history_resolve_env.resolve_calls[0]["platform"] != WEBUI_CHAT_PLATFORM
    assert history_resolve_env.resolve_calls[0]["target_id"] == group_id
    assert history_resolve_env.resolve_calls[0]["chat_type"] == "group"
    assert history_resolve_env.find_calls[0]["session_id"] == "virtual-group-session"
