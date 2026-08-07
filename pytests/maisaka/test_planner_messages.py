"""Planner 消息构造工具测试。"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.maisaka.context.planner_messages import (
    build_planner_prefix,
    build_planner_user_prefix_from_session_message,
)


def _make_prefix(
    *,
    is_at: bool = False,
    is_mentioned: bool = False,
) -> str:
    return build_planner_prefix(
        timestamp=datetime(2026, 8, 5, 12, 0, 0),
        user_name="test_user",
        is_at=is_at,
        is_mentioned=is_mentioned,
        include_message_id=False,
    )


@pytest.mark.parametrize(
    ("is_at", "is_mentioned", "expect_at", "expect_mentioned"),
    [
        (False, False, False, False),
        (True, False, True, False),
        (False, True, False, True),
        (True, True, True, True),
    ],
)
def test_build_planner_prefix_is_at_mentioned(
    is_at: bool,
    is_mentioned: bool,
    expect_at: bool,
    expect_mentioned: bool,
) -> None:
    prefix = _make_prefix(is_at=is_at, is_mentioned=is_mentioned)

    assert (expect_at and 'is_at="true"' in prefix) or (not expect_at and 'is_at="true"' not in prefix)
    assert (expect_mentioned and 'is_mentioned="true"' in prefix) or (
        not expect_mentioned and 'is_mentioned="true"' not in prefix
    )


def test_build_planner_user_prefix_from_session_message_passthrough() -> None:
    """验证 build_planner_user_prefix_from_session_message 正确透传 is_at/is_mentioned。"""

    def _make_msg(is_at: bool, is_mentioned: bool) -> SimpleNamespace:
        return SimpleNamespace(
            timestamp=datetime(2026, 8, 5, 12, 0, 0),
            message_id="msg_001",
            session_id="session_001",
            is_notify=False,
            is_at=is_at,
            is_mentioned=is_mentioned,
            message_info=SimpleNamespace(
                user_info=SimpleNamespace(
                    user_id="user_001",
                    user_nickname="test_user",
                    user_cardname="",
                ),
            ),
            raw_message=SimpleNamespace(components=[]),
        )

    msg_both = _make_msg(is_at=True, is_mentioned=True)
    prefix = build_planner_user_prefix_from_session_message(msg_both)
    assert 'is_at="true"' in prefix
    assert 'is_mentioned="true"' in prefix

    msg_neither = _make_msg(is_at=False, is_mentioned=False)
    prefix = build_planner_user_prefix_from_session_message(msg_neither)
    assert 'is_at="true"' not in prefix
    assert 'is_mentioned="true"' not in prefix

    msg_at_only = _make_msg(is_at=True, is_mentioned=False)
    prefix = build_planner_user_prefix_from_session_message(msg_at_only)
    assert 'is_at="true"' in prefix
    assert 'is_mentioned="true"' not in prefix

    msg_mentioned_only = _make_msg(is_at=False, is_mentioned=True)
    prefix = build_planner_user_prefix_from_session_message(msg_mentioned_only)
    assert 'is_at="true"' not in prefix
    assert 'is_mentioned="true"' in prefix