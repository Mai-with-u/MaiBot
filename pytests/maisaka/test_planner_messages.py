from datetime import datetime

from src.maisaka.context.planner_messages import build_planner_prefix


def test_build_planner_prefix_marks_self_message() -> None:
    prefix = build_planner_prefix(
        timestamp=datetime(2026, 8, 20, 1, 9, 30),
        user_name="麦麦",
        message_id="self-message",
        is_self_message=True,
    )

    assert 'is_self_message="true"' in prefix


def test_build_planner_prefix_does_not_mark_user_message() -> None:
    prefix = build_planner_prefix(
        timestamp=datetime(2026, 8, 20, 1, 9, 30),
        user_name="用户",
        message_id="user-message",
    )

    assert 'is_self_message="true"' not in prefix
