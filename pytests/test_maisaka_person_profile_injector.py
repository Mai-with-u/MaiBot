from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import GroupInfo, MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import AtComponent, MessageSequence, ReplyComponent, TextComponent
from src.maisaka import person_profile_injector
from src.maisaka import reasoning_engine as reasoning_engine_module
from src.maisaka.reasoning_engine import MaisakaReasoningEngine


def _build_config(
    *,
    enable_injection: bool = True,
    max_profiles: int = 3,
    bot_user_id: str = "bot",
) -> SimpleNamespace:
    return SimpleNamespace(
        a_memorix=SimpleNamespace(
            integration=SimpleNamespace(
                enable_person_profile_injection=enable_injection,
                person_profile_injection_max_profiles=max_profiles,
            )
        ),
        bot=SimpleNamespace(qq_account=bot_user_id),
    )


def _build_message(
    *,
    message_id: str,
    user_id: str,
    nickname: str,
    cardname: str = "",
    group_id: str = "",
    components: list[Any] | None = None,
) -> SessionMessage:
    message = SessionMessage(message_id=message_id, timestamp=datetime(2026, 1, 1), platform="qq")
    group_info = GroupInfo(group_id=group_id, group_name="测试群") if group_id else None
    message.message_info = MessageInfo(
        user_info=UserInfo(
            user_id=user_id,
            user_nickname=nickname,
            user_cardname=cardname or None,
        ),
        group_info=group_info,
    )
    message.raw_message = MessageSequence(components or [TextComponent("hello")])
    message.session_id = group_id or user_id
    message.processed_plain_text = "hello"
    return message


def _patch_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_resolve_person_id_for_memory(
        *,
        person_name: str = "",
        platform: str = "",
        user_id: Any = None,
        strict_known: bool = False,
    ) -> str:
        _ = platform
        _ = strict_known
        key = str(user_id or person_name or "").strip()
        return f"pid-{key}" if key else ""

    monkeypatch.setattr(person_profile_injector, "resolve_person_id_for_memory", fake_resolve_person_id_for_memory)


def test_collect_private_chat_only_uses_current_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(person_profile_injector, "global_config", _build_config())
    _patch_resolver(monkeypatch)

    anchor = _build_message(message_id="m2", user_id="alice", nickname="Alice")
    pending = [
        _build_message(message_id="m1", user_id="bob", nickname="Bob"),
        anchor,
    ]

    candidates = person_profile_injector.collect_person_profile_candidates(
        anchor,
        pending,
        max_profiles=3,
    )

    assert [(item.person_id, item.user_id, item.source) for item in candidates] == [
        ("pid-alice", "alice", "private_current_user")
    ]


def test_collect_group_chat_current_object_priority_and_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(person_profile_injector, "global_config", _build_config())
    _patch_resolver(monkeypatch)

    anchor = _build_message(
        message_id="m4",
        user_id="alice",
        nickname="Alice",
        group_id="group-1",
        components=[
            TextComponent("ping"),
            AtComponent("bob", target_user_nickname="Bob"),
            AtComponent("bob", target_user_nickname="Bob"),
            ReplyComponent(
                target_message_id="m3",
                target_message_sender_id="carol",
                target_message_sender_nickname="Carol",
            ),
        ],
    )
    pending = [
        _build_message(message_id="m1", user_id="dave", nickname="Dave", group_id="group-1"),
        anchor,
    ]

    candidates = person_profile_injector.collect_person_profile_candidates(
        anchor,
        pending,
        max_profiles=3,
    )

    assert [item.person_id for item in candidates] == ["pid-alice", "pid-bob", "pid-carol"]
    assert [item.source for item in candidates] == ["recent_speaker", "at_user", "reply_sender"]


@pytest.mark.asyncio
async def test_injection_disabled_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(person_profile_injector, "global_config", _build_config(enable_injection=False))
    _patch_resolver(monkeypatch)

    anchor = _build_message(message_id="m1", user_id="alice", nickname="Alice")

    result = await person_profile_injector.build_person_profile_injection_messages(
        anchor_message=anchor,
        pending_messages=[anchor],
    )

    assert result == []


@pytest.mark.asyncio
async def test_injection_builds_internal_reference_and_skips_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(person_profile_injector, "global_config", _build_config(max_profiles=2))
    _patch_resolver(monkeypatch)
    calls: list[Dict[str, Any]] = []

    async def fake_profile_admin(*, action: str, **kwargs: Any) -> Dict[str, Any]:
        calls.append({"action": action, **kwargs})
        person_id = str(kwargs.get("person_id") or "")
        if person_id == "pid-bob":
            return {"success": True, "person_id": person_id, "profile_text": ""}
        return {
            "success": True,
            "person_id": person_id,
            "person_name": "Alice",
            "profile_text": "Alice 喜欢咖啡，最近在聊模型记忆。",
            "evidence": [{"content": "不应注入 evidence"}],
        }

    monkeypatch.setattr(person_profile_injector.memory_service, "profile_admin", fake_profile_admin)

    anchor = _build_message(
        message_id="m2",
        user_id="alice",
        nickname="Alice",
        group_id="group-1",
        components=[AtComponent("bob", target_user_nickname="Bob")],
    )

    result = await person_profile_injector.build_person_profile_injection_messages(
        anchor_message=anchor,
        pending_messages=[anchor],
    )

    assert len(result) == 1
    assert "【人物画像-内部参考】" in result[0]
    assert "仅供内部推理，不要向用户逐字复述" in result[0]
    assert "Alice 喜欢咖啡" in result[0]
    assert "不应注入 evidence" not in result[0]
    assert [call["person_id"] for call in calls] == ["pid-alice", "pid-bob"]


@pytest.mark.asyncio
async def test_injection_empty_or_failed_profile_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(person_profile_injector, "global_config", _build_config())
    _patch_resolver(monkeypatch)

    async def fake_profile_admin(*, action: str, **kwargs: Any) -> Dict[str, Any]:
        _ = action
        _ = kwargs
        return {"success": False, "error": "boom"}

    monkeypatch.setattr(person_profile_injector.memory_service, "profile_admin", fake_profile_admin)

    anchor = _build_message(message_id="m1", user_id="alice", nickname="Alice")

    result = await person_profile_injector.build_person_profile_injection_messages(
        anchor_message=anchor,
        pending_messages=[anchor],
    )

    assert result == []


@pytest.mark.asyncio
async def test_reasoning_engine_injected_messages_keep_deferred_reminder(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_build_person_profile_injection_messages(**kwargs: Any) -> list[str]:
        _ = kwargs
        return ["profile-reference"]

    monkeypatch.setattr(
        reasoning_engine_module,
        "build_person_profile_injection_messages",
        fake_build_person_profile_injection_messages,
    )
    engine = MaisakaReasoningEngine(runtime=SimpleNamespace(log_prefix="[test]"))
    anchor = _build_message(message_id="m1", user_id="alice", nickname="Alice")

    result = await engine._build_planner_injected_user_messages(
        anchor_message=anchor,
        source_messages=[anchor],
        deferred_tools_reminder="deferred-tools",
    )

    assert result == ["deferred-tools", "profile-reference"]


@pytest.mark.asyncio
async def test_reasoning_engine_injected_messages_forwards_source_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    async def fake_build_person_profile_injection_messages(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        reasoning_engine_module,
        "build_person_profile_injection_messages",
        fake_build_person_profile_injection_messages,
    )
    engine = MaisakaReasoningEngine(runtime=SimpleNamespace(log_prefix="[test]"))
    old_message = _build_message(message_id="m1", user_id="alice", nickname="Alice")
    new_message = _build_message(message_id="m2", user_id="bob", nickname="Bob")

    result = await engine._build_planner_injected_user_messages(
        anchor_message=new_message,
        source_messages=[old_message, new_message],
        deferred_tools_reminder="",
    )

    assert result == []
    assert captured["anchor_message"] is new_message
    assert captured["pending_messages"] == [old_message, new_message]
