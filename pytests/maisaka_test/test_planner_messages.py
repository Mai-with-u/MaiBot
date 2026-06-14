from datetime import datetime

from src.maisaka.context.planner_messages import build_planner_prefix


def test_build_planner_prefix_marks_self_message_when_enabled() -> None:
    prefix = build_planner_prefix(
        timestamp=datetime(2026, 6, 13, 1, 9, 30),
        user_name="呢猫",
        message_id="1316095995",
        is_self_message=True,
    )

    assert 'is_self_message="true"' in prefix


def test_build_planner_prefix_omits_self_message_mark_by_default() -> None:
    prefix = build_planner_prefix(
        timestamp=datetime(2026, 6, 13, 1, 9, 30),
        user_name="Luft",
        message_id="-1470070102",
    )

    assert 'is_self_message="true"' not in prefix
