from types import SimpleNamespace

import pytest

from src.chat.brain_chat.PFC import pfc_KnowledgeFetcher as knowledge_module
from src.services.memory_service import MemoryHit, MemorySearchResult


def test_knowledge_fetcher_resolves_private_memory_context(monkeypatch):
    monkeypatch.setattr(knowledge_module, "LLMRequest", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        knowledge_module,
        "_chat_manager",
        SimpleNamespace(get_session_by_session_id=lambda session_id: SimpleNamespace(platform="qq", user_id="42", group_id="")),
    )
    monkeypatch.setattr(
        knowledge_module,
        "resolve_person_id_for_memory",
        lambda *, person_name, platform, user_id: f"{person_name}:{platform}:{user_id}",
    )

    fetcher = knowledge_module.KnowledgeFetcher(private_name="Alice", stream_id="stream-1")

    assert fetcher._resolve_private_memory_context() == {
        "chat_id": "stream-1",
        "person_id": "Alice:qq:42",
        "user_id": "42",
        "group_id": "",
    }


@pytest.mark.asyncio
async def test_knowledge_fetcher_memory_get_knowledge_uses_memory_service(monkeypatch):
    monkeypatch.setattr(knowledge_module, "LLMRequest", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        knowledge_module,
        "_chat_manager",
        SimpleNamespace(get_session_by_session_id=lambda session_id: SimpleNamespace(platform="qq", user_id="42", group_id="")),
    )
    monkeypatch.setattr(
        knowledge_module,
        "resolve_person_id_for_memory",
        lambda *, person_name, platform, user_id: f"{person_name}:{platform}:{user_id}",
    )

    calls = []

    async def fake_search(query: str, **kwargs):
        calls.append((query, kwargs))
        return MemorySearchResult(summary="", hits=[MemoryHit(content="她喜欢猫", source="person_fact:qq:42")], filtered=False)

    monkeypatch.setattr(knowledge_module.memory_service, "search", fake_search)

    fetcher = knowledge_module.KnowledgeFetcher(private_name="Alice", stream_id="stream-1")
    result = await fetcher._memory_get_knowledge("她喜欢什么")

    assert "1. 她喜欢猫" in result
    assert calls == [
        (
            "她喜欢什么",
            {
                "limit": 5,
                "mode": "search",
                "chat_id": "stream-1",
                "person_id": "Alice:qq:42",
                "user_id": "42",
                "group_id": "",
                "respect_filter": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_knowledge_fetcher_falls_back_to_chat_scope_when_person_scope_misses(monkeypatch):
    monkeypatch.setattr(knowledge_module, "LLMRequest", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        knowledge_module,
        "_chat_manager",
        SimpleNamespace(get_session_by_session_id=lambda session_id: SimpleNamespace(platform="qq", user_id="42", group_id="")),
    )
    monkeypatch.setattr(
        knowledge_module,
        "resolve_person_id_for_memory",
        lambda *, person_name, platform, user_id: "person-1",
    )

    calls = []

    async def fake_search(query: str, **kwargs):
        calls.append((query, kwargs))
        if kwargs.get("person_id"):
            return MemorySearchResult(summary="", hits=[], filtered=False)
        return MemorySearchResult(summary="", hits=[MemoryHit(content="她计划去杭州音乐节", source="chat_summary:stream-1")], filtered=False)

    monkeypatch.setattr(knowledge_module.memory_service, "search", fake_search)

    fetcher = knowledge_module.KnowledgeFetcher(private_name="Alice", stream_id="stream-1")
    result = await fetcher._memory_get_knowledge("Alice 最近在忙什么")

    assert "杭州音乐节" in result
    assert calls == [
        (
            "Alice 最近在忙什么",
            {
                "limit": 5,
                "mode": "search",
                "chat_id": "stream-1",
                "person_id": "person-1",
                "user_id": "42",
                "group_id": "",
                "respect_filter": True,
            },
        ),
        (
            "Alice 最近在忙什么",
            {
                "limit": 5,
                "mode": "search",
                "chat_id": "stream-1",
                "person_id": "",
                "user_id": "42",
                "group_id": "",
                "respect_filter": True,
            },
        ),
    ]
