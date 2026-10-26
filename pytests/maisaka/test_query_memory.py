from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.maisaka.builtin_tool import query_memory
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


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved_id", ["target-person", ""])
async def test_explicit_person_uses_alias_mapping_instead_of_private_chat_id(monkeypatch, resolved_id):
    resolve = AsyncMock(return_value={"success": True, "person_id": resolved_id})
    monkeypatch.setattr(query_memory.memory_service, "profile_admin", resolve)
    result = await query_memory._resolve_person_id(
        person_name="另一人的旧昵称",
        platform="qq",
        user_id="current-chat-user",
        group_id="",
    )
    assert result == (resolved_id, "另一人的旧昵称")
    resolve.assert_awaited_once_with(action="resolve_identity", identifier="另一人的旧昵称")


@pytest.mark.asyncio
async def test_ambiguous_person_stops_query_instead_of_removing_person_filter(monkeypatch):
    monkeypatch.setattr(
        query_memory.memory_service, "profile_admin", AsyncMock(return_value={"success": True, "person_id": ""})
    )
    search = AsyncMock()
    monkeypatch.setattr(query_memory.memory_service, "search", search)
    runtime = SimpleNamespace(
        session_id="session", chat_stream=SimpleNamespace(platform="qq", user_id="current-chat-user", group_id="")
    )
    context = SimpleNamespace(runtime=runtime, build_failure_result=lambda name, error: {"error": error})
    invocation = SimpleNamespace(tool_name="query_memory", arguments={"query": "旅行计划", "person_name": "重名用户"})
    result = await query_memory.handle_tool(context, invocation)
    assert "无法唯一确认" in result["error"]
    search.assert_not_awaited()
