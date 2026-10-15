from datetime import datetime
from types import SimpleNamespace

import pytest

from src.maisaka.context import message_id_alias
from src.maisaka.context.message_adapter import format_speaker_content
from src.maisaka.context.message_id_alias import (
    build_alias_map,
    expand_message_id_aliases,
    to_display_message_id,
)
from src.maisaka.context.planner_messages import build_planner_prefix
from src.maisaka.runtime import MaisakaHeartFlowChatting

LONG_ID = "ROBOT1.0_kEiYeq1B.9BpmqCVb4lVwoZqPgq840cIKn7ASxr4e0uhGYLJwpNTsYDChVLbFi1mnTkKVG.xGc1aBOgAsKzG2kg"
OTHER_LONG_ID = "ROBOT1.0_hJfZUlyZtPdrH.wa2V.962yuQQ4grsl97zeFwKRR1pW0lrw-RuXr.Qe-48F2UY7NxPl8KLbsvwQkPnivf"


def test_long_id_becomes_stable_short_alias() -> None:
    alias = to_display_message_id(LONG_ID)

    assert alias.startswith("m")
    assert len(alias) == 7
    assert alias == to_display_message_id(LONG_ID)
    assert alias != to_display_message_id(OTHER_LONG_ID)


def test_short_id_and_alias_are_left_unchanged() -> None:
    assert to_display_message_id("1234567890") == "1234567890"
    alias = to_display_message_id(LONG_ID)
    assert to_display_message_id(alias) == alias


def test_rendered_prefixes_show_alias_instead_of_long_id() -> None:
    alias = to_display_message_id(LONG_ID)
    speaker_text = format_speaker_content("用户", "你好", message_id=LONG_ID)
    planner_prefix = build_planner_prefix(
        timestamp=datetime(2026, 9, 30, 17, 0, 0),
        user_name="用户",
        message_id=LONG_ID,
        quote_ids=[OTHER_LONG_ID],
    )

    assert f"[msg_id:{alias}]" in speaker_text
    assert f'msg_id="{alias}"' in planner_prefix
    assert f'quote="{to_display_message_id(OTHER_LONG_ID)}"' in planner_prefix
    assert LONG_ID not in speaker_text + planner_prefix


def test_expand_restores_nested_tool_arguments() -> None:
    alias_map = build_alias_map([LONG_ID, OTHER_LONG_ID])
    arguments = {
        "msg_id": to_display_message_id(LONG_ID),
        "attach_at": [{"msg_id": to_display_message_id(OTHER_LONG_ID)}],
        "duration": 300,
        "reason": "刷屏",
    }

    assert expand_message_id_aliases(arguments, alias_map) == {
        "msg_id": LONG_ID,
        "attach_at": [{"msg_id": OTHER_LONG_ID}],
        "duration": 300,
        "reason": "刷屏",
    }


def test_alias_collision_prefers_first_given_message(monkeypatch: pytest.MonkeyPatch) -> None:
    # 强制所有长 ID 撞到同一别名：先传入（调用方按新到旧传入，即最新）的消息胜出。
    monkeypatch.setattr(message_id_alias, "to_display_message_id", lambda message_id: "mcollid")

    alias_map = message_id_alias.build_alias_map([LONG_ID, OTHER_LONG_ID])

    assert alias_map == {"mcollid": LONG_ID}


def test_runtime_expands_aliases_from_chat_history() -> None:
    runtime = SimpleNamespace(
        _chat_history=[SimpleNamespace(message_id=OTHER_LONG_ID), SimpleNamespace(message_id=LONG_ID)],
    )
    arguments = {"msg_id": to_display_message_id(LONG_ID), "unknown": "m000000"}

    expanded = MaisakaHeartFlowChatting.expand_message_id_aliases(runtime, arguments)  # type: ignore[arg-type]

    assert expanded == {"msg_id": LONG_ID, "unknown": "m000000"}
