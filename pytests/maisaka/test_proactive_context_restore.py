"""主动回合用户消息回填的编排逻辑测试。

`restore_proactive_user_context` 复用启动恢复的取数与转换链路，这里只验证
主动场景特有的编排行为：何时回填、去重、插入位置与失败兜底。
"""

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.maisaka.context.messages import SessionBackedMessage
from src.maisaka.runtime import MaisakaHeartFlowChatting


def _session_backed_message(message_id: str, source_kind: str = "user") -> SessionBackedMessage:
    return SessionBackedMessage(
        raw_message=MessageSequence([TextComponent("消息内容")]),
        visible_text=f"19:51:07[msg_id:{message_id}][某人]消息内容",
        timestamp=datetime(2026, 8, 23, 19, 51, 7),
        message_id=message_id,
        source_kind=source_kind,
    )


def _repository_message(message_id: str, *, is_notify: bool = False) -> SimpleNamespace:
    return SimpleNamespace(message_id=message_id, is_notify=is_notify, is_command=False)


def _build_runtime(
    chat_history: list[Any],
    repository_messages: list[Any] | None = None,
    *,
    repository_error: Exception | None = None,
    session_scoped_messages: list[Any] | None = None,
    is_group_chat: bool = True,
    group_id: str = "10086",
    user_id: str = "",
) -> tuple[SimpleNamespace, dict[str, Any]]:
    calls: dict[str, Any] = {"find_messages": []}
    built_sources: list[str] = []

    async def fake_build_history_message(message: Any, *, source_kind: str = "user") -> SessionBackedMessage:
        built_sources.append(source_kind)
        return _session_backed_message(message.message_id, source_kind=source_kind)

    def fake_find_messages(**kwargs: Any) -> list[Any]:
        calls["find_messages"].append(kwargs)
        if repository_error is not None:
            raise repository_error
        # 默认 session_id 查询直接返回 repository_messages；显式传入
        # session_scoped_messages 时模拟会话流分裂（session 查空，回退查询才有历史）。
        if kwargs.get("session_id") is not None:
            if session_scoped_messages is not None:
                return session_scoped_messages
            return repository_messages or []
        return repository_messages or []

    runtime = SimpleNamespace(
        _chat_history=chat_history,
        session_id="session-1",
        log_prefix="[测试会话]",
        _get_context_restore_limit=lambda: 18,
        _reasoning_engine=SimpleNamespace(_build_history_message=fake_build_history_message),
        chat_stream=SimpleNamespace(
            platform="qq",
            is_group_session=is_group_chat,
            group_id=group_id,
            user_id=user_id,
        ),
    )
    runtime.__dict__["_fake_find_messages"] = fake_find_messages
    return runtime, {"calls": calls, "built_sources": built_sources, "fake_find_messages": fake_find_messages}


def _patch_repository(monkeypatch: pytest.MonkeyPatch, runtime: SimpleNamespace, harness: dict[str, Any]) -> None:
    monkeypatch.setattr("src.maisaka.runtime.find_messages", harness["fake_find_messages"])
    monkeypatch.setattr(
        "src.maisaka.runtime.select_messages_after_latest_clear_marker",
        lambda messages: list(messages),
    )


@pytest.mark.asyncio
async def test_returns_zero_without_query_when_history_already_has_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, harness = _build_runtime([_session_backed_message("m-existing")])
    _patch_repository(monkeypatch, runtime, harness)

    restored = await MaisakaHeartFlowChatting.restore_proactive_user_context(runtime)  # type: ignore[arg-type]

    assert restored == 0
    assert harness["calls"]["find_messages"] == []


@pytest.mark.asyncio
async def test_restores_user_messages_before_existing_session_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_message = _session_backed_message("m-bot", source_kind="guided_reply")
    runtime, harness = _build_runtime(
        [bot_message],
        repository_messages=[_repository_message("m-old-1"), _repository_message("m-old-2")],
    )
    _patch_repository(monkeypatch, runtime, harness)

    restored = await MaisakaHeartFlowChatting.restore_proactive_user_context(runtime)  # type: ignore[arg-type]

    assert restored == 2
    assert [message.message_id for message in runtime._chat_history] == ["m-old-1", "m-old-2", "m-bot"]
    assert harness["built_sources"] == ["user", "user"]
    assert harness["calls"]["find_messages"][0]["session_id"] == "session-1"
    assert harness["calls"]["find_messages"][0]["filter_bot"] is True


@pytest.mark.asyncio
async def test_skips_notify_and_already_present_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    existing_bot_message = _session_backed_message("m-dup", source_kind="guided_reply")
    runtime, harness = _build_runtime(
        [existing_bot_message],
        repository_messages=[
            _repository_message("m-dup"),
            _repository_message("m-notify", is_notify=True),
            _repository_message("m-new"),
        ],
    )
    _patch_repository(monkeypatch, runtime, harness)

    restored = await MaisakaHeartFlowChatting.restore_proactive_user_context(runtime)  # type: ignore[arg-type]

    assert restored == 1
    assert [message.message_id for message in runtime._chat_history] == ["m-new", "m-dup"]


@pytest.mark.asyncio
async def test_returns_zero_when_repository_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, harness = _build_runtime([], repository_error=RuntimeError("数据库暂不可用"))
    _patch_repository(monkeypatch, runtime, harness)

    restored = await MaisakaHeartFlowChatting.restore_proactive_user_context(runtime)  # type: ignore[arg-type]

    assert restored == 0
    assert runtime._chat_history == []


@pytest.mark.asyncio
async def test_returns_zero_when_repository_has_no_restorable_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, harness = _build_runtime([], repository_messages=[])
    _patch_repository(monkeypatch, runtime, harness)

    restored = await MaisakaHeartFlowChatting.restore_proactive_user_context(runtime)  # type: ignore[arg-type]

    assert restored == 0
    assert runtime._chat_history == []


@pytest.mark.asyncio
async def test_falls_back_to_group_query_when_session_history_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_message = _session_backed_message("m-bot", source_kind="guided_reply")
    runtime, harness = _build_runtime(
        [bot_message],
        repository_messages=[_repository_message("m-old")],
        session_scoped_messages=[],
        is_group_chat=True,
        group_id="10086",
    )
    _patch_repository(monkeypatch, runtime, harness)

    restored = await MaisakaHeartFlowChatting.restore_proactive_user_context(runtime)  # type: ignore[arg-type]

    assert restored == 1
    assert [message.message_id for message in runtime._chat_history] == ["m-old", "m-bot"]
    queries = harness["calls"]["find_messages"]
    assert len(queries) == 2
    assert queries[0].get("session_id") == "session-1"
    assert queries[1].get("group_id") == "10086"
    assert queries[1].get("platform") == "qq"


@pytest.mark.asyncio
async def test_falls_back_to_user_query_for_private_chat_when_session_history_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, harness = _build_runtime(
        [],
        repository_messages=[_repository_message("m-private")],
        session_scoped_messages=[],
        is_group_chat=False,
        user_id="2933634892",
    )
    _patch_repository(monkeypatch, runtime, harness)

    restored = await MaisakaHeartFlowChatting.restore_proactive_user_context(runtime)  # type: ignore[arg-type]

    assert restored == 1
    assert [message.message_id for message in runtime._chat_history] == ["m-private"]
    queries = harness["calls"]["find_messages"]
    assert len(queries) == 2
    assert queries[1].get("user_id") == "2933634892"
    assert "group_id" not in queries[1]


@pytest.mark.asyncio
async def test_guard_ignores_user_message_without_usable_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 历史 user 消息缺 message_id 时不构成可引用目标，守卫不得短路，仍需回填
    idless_user_message = _session_backed_message("", source_kind="user")
    idless_user_message.message_id = None
    runtime, harness = _build_runtime(
        [idless_user_message],
        repository_messages=[_repository_message("m-restored")],
    )
    _patch_repository(monkeypatch, runtime, harness)

    restored = await MaisakaHeartFlowChatting.restore_proactive_user_context(runtime)  # type: ignore[arg-type]

    assert restored == 1
    assert [message.message_id for message in runtime._chat_history] == ["m-restored", None]
