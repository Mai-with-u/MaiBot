from types import SimpleNamespace
from typing import Any, Dict

import pytest

from src.core.tooling import ToolInvocation
from src.maisaka.builtin_tool import query_memory as query_memory_tool
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.services.memory_service import MemoryHit, MemorySearchResult


def _build_tool_ctx(
    *,
    session_id: str = "session-1",
    platform: str = "qq",
    user_id: str = "user-1",
    group_id: str = "",
) -> BuiltinToolRuntimeContext:
    runtime = SimpleNamespace(
        session_id=session_id,
        chat_stream=SimpleNamespace(
            platform=platform,
            user_id=user_id,
            group_id=group_id,
        ),
        log_prefix=f"[{session_id}]",
    )
    return BuiltinToolRuntimeContext(engine=SimpleNamespace(), runtime=runtime)


def _build_invocation(arguments: Dict[str, Any]) -> ToolInvocation:
    return ToolInvocation(
        tool_name="query_memory",
        arguments=dict(arguments),
        call_id="call-query-memory",
    )


@pytest.fixture(autouse=True)
def _patch_maisaka_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        query_memory_tool,
        "global_config",
        SimpleNamespace(
            a_memorix=SimpleNamespace(
                integration=SimpleNamespace(
                    memory_query_default_limit=5,
                )
            )
        ),
    )


@pytest.mark.asyncio
async def test_query_memory_rejects_empty_query_and_time(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(query: str, **kwargs: Any) -> MemorySearchResult:
        _ = query
        _ = kwargs
        raise AssertionError("参数校验失败时不应调用 memory_service.search")

    monkeypatch.setattr(query_memory_tool.memory_service, "search", fake_search)

    result = await query_memory_tool.handle_tool(
        _build_tool_ctx(),
        _build_invocation({"query": "", "time_start": "", "time_end": ""}),
    )

    assert result.success is False
    assert "query_memory 需要提供 query" in result.error_message


@pytest.mark.asyncio
async def test_query_memory_private_chat_auto_sets_person_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    def fake_resolve_person_id_for_memory(
        *,
        person_name: str = "",
        platform: str = "",
        user_id: Any = None,
        strict_known: bool = False,
    ) -> str:
        _ = strict_known
        captured["resolve_args"] = {
            "person_name": person_name,
            "platform": platform,
            "user_id": user_id,
        }
        return "pid-private-auto"

    async def fake_search(query: str, **kwargs: Any) -> MemorySearchResult:
        captured["query"] = query
        captured["search_kwargs"] = dict(kwargs)
        return MemorySearchResult(
            summary="检索摘要",
            hits=[MemoryHit(content="Alice 喜欢咖啡", score=0.91)],
        )

    monkeypatch.setattr(query_memory_tool, "resolve_person_id_for_memory", fake_resolve_person_id_for_memory)
    monkeypatch.setattr(query_memory_tool.memory_service, "search", fake_search)

    result = await query_memory_tool.handle_tool(
        _build_tool_ctx(session_id="private-session", platform="qq", user_id="alice", group_id=""),
        _build_invocation({"query": "Alice 的喜好"}),
    )

    assert result.success is True
    assert captured["query"] == "Alice 的喜好"
    assert captured["resolve_args"] == {
        "person_name": "",
        "platform": "qq",
        "user_id": "alice",
    }
    assert captured["search_kwargs"]["chat_id"] == "private-session"
    assert captured["search_kwargs"]["user_id"] == "alice"
    assert captured["search_kwargs"]["group_id"] == ""
    assert captured["search_kwargs"]["person_id"] == "pid-private-auto"
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["person_id"] == "pid-private-auto"


@pytest.mark.asyncio
async def test_query_memory_group_chat_does_not_attach_default_person_id(monkeypatch: pytest.MonkeyPatch) -> None:
    call_counter = {"resolve": 0}
    captured_kwargs: Dict[str, Any] = {}

    def fake_resolve_person_id_for_memory(
        *,
        person_name: str = "",
        platform: str = "",
        user_id: Any = None,
        strict_known: bool = False,
    ) -> str:
        _ = person_name
        _ = platform
        _ = user_id
        _ = strict_known
        call_counter["resolve"] += 1
        return "unexpected-person-id"

    async def fake_search(query: str, **kwargs: Any) -> MemorySearchResult:
        _ = query
        captured_kwargs.update(kwargs)
        return MemorySearchResult(summary="", hits=[])

    monkeypatch.setattr(query_memory_tool, "resolve_person_id_for_memory", fake_resolve_person_id_for_memory)
    monkeypatch.setattr(query_memory_tool.memory_service, "search", fake_search)

    result = await query_memory_tool.handle_tool(
        _build_tool_ctx(session_id="group-session", platform="qq", user_id="alice", group_id="group-1"),
        _build_invocation({"query": "群聊上下文"}),
    )

    assert result.success is True
    assert call_counter["resolve"] == 0
    assert captured_kwargs["chat_id"] == "group-session"
    assert captured_kwargs["group_id"] == "group-1"
    assert captured_kwargs["person_id"] == ""


@pytest.mark.asyncio
async def test_query_memory_search_failure_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(query: str, **kwargs: Any) -> MemorySearchResult:
        _ = query
        _ = kwargs
        return MemorySearchResult(success=False, error="boom")

    monkeypatch.setattr(query_memory_tool.memory_service, "search", fake_search)
    monkeypatch.setattr(query_memory_tool, "resolve_person_id_for_memory", lambda **kwargs: "")

    result = await query_memory_tool.handle_tool(
        _build_tool_ctx(),
        _build_invocation({"query": "测试失败透传"}),
    )

    assert result.success is False
    assert result.error_message == "boom"
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["success"] is False


@pytest.mark.asyncio
async def test_query_memory_prefers_person_name_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {"resolve_calls": []}

    def fake_resolve_person_id_for_memory(
        *,
        person_name: str = "",
        platform: str = "",
        user_id: Any = None,
        strict_known: bool = False,
    ) -> str:
        _ = strict_known
        captured["resolve_calls"].append(
            {
                "person_name": person_name,
                "platform": platform,
                "user_id": user_id,
            }
        )
        if person_name:
            return "pid-by-name"
        return "pid-private-auto"

    async def fake_search(query: str, **kwargs: Any) -> MemorySearchResult:
        _ = query
        captured["search_kwargs"] = dict(kwargs)
        return MemorySearchResult(summary="", hits=[MemoryHit(content="命中1")])

    monkeypatch.setattr(query_memory_tool, "resolve_person_id_for_memory", fake_resolve_person_id_for_memory)
    monkeypatch.setattr(query_memory_tool.memory_service, "search", fake_search)

    result = await query_memory_tool.handle_tool(
        _build_tool_ctx(session_id="private-session", platform="qq", user_id="alice", group_id=""),
        _build_invocation({"query": "小明资料", "person_name": "小明"}),
    )

    assert result.success is True
    assert captured["resolve_calls"][0] == {
        "person_name": "小明",
        "platform": "qq",
        "user_id": "alice",
    }
    assert captured["search_kwargs"]["person_id"] == "pid-by-name"
    assert result.structured_content["person_name"] == "小明"
    assert result.structured_content["person_id"] == "pid-by-name"


@pytest.mark.asyncio
async def test_query_memory_no_hit_returns_readable_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(query: str, **kwargs: Any) -> MemorySearchResult:
        _ = query
        _ = kwargs
        return MemorySearchResult(summary="", hits=[])

    monkeypatch.setattr(query_memory_tool.memory_service, "search", fake_search)
    monkeypatch.setattr(query_memory_tool, "resolve_person_id_for_memory", lambda **kwargs: "")

    result = await query_memory_tool.handle_tool(
        _build_tool_ctx(),
        _build_invocation({"query": "不存在的记忆"}),
    )

    assert result.success is True
    assert "未找到匹配的长期记忆" in result.content
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["query"] == "不存在的记忆"
