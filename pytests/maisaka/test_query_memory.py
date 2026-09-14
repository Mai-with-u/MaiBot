import pytest

from src.maisaka.builtin_tool.query_memory import _normalize_optional_time


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("2026-09-01", "2026/09/01"),
        ("2026-09-01 12:30", "2026/09/01 12:30"),
        ("2026/09/01", "2026/09/01"),
        ("2026/09/01 12:30", "2026/09/01 12:30"),
    ],
)
def test_normalize_optional_time_accepts_slash_and_hyphen_formats(raw_value: str, expected: str) -> None:
    assert _normalize_optional_time(raw_value) == expected


def test_normalize_optional_time_does_not_relax_unsupported_formats() -> None:
    assert _normalize_optional_time("2026-09-01T12:30:00") == "2026-09-01T12:30:00"
