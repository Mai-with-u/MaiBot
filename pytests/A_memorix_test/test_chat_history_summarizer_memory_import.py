from types import SimpleNamespace

import pytest

from src.memory_system import chat_history_summarizer as summarizer_module


def _build_summarizer() -> summarizer_module.ChatHistorySummarizer:
    summarizer = summarizer_module.ChatHistorySummarizer.__new__(summarizer_module.ChatHistorySummarizer)
    summarizer.session_id = "session-1"
    summarizer.log_prefix = "[session-1]"
    return summarizer


@pytest.mark.asyncio
async def test_import_to_long_term_memory_uses_summary_payload(monkeypatch):
    calls = []
    summarizer = _build_summarizer()

    async def fake_ingest_summary(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(success=True, detail="", stored_ids=["p1"])

    monkeypatch.setattr(
        summarizer_module,
        "_chat_manager",
        SimpleNamespace(get_session_by_session_id=lambda session_id: SimpleNamespace(user_id="user-1", group_id="")),
    )
    monkeypatch.setattr(summarizer_module, "global_config", SimpleNamespace(memory=SimpleNamespace(chat_history_topic_check_message_threshold=8)))
    monkeypatch.setattr("src.services.memory_service.memory_service.ingest_summary", fake_ingest_summary)

    await summarizer._import_to_long_term_memory(
        record_id=1,
        theme="旅行计划",
        summary="我们讨论了春游安排",
        participants=["Alice", "Bob"],
        start_time=1.0,
        end_time=2.0,
        original_text="long text",
    )

    assert len(calls) == 1
    payload = calls[0]
    assert payload["external_id"] == "chat_history:1"
    assert payload["chat_id"] == "session-1"
    assert payload["participants"] == ["Alice", "Bob"]
    assert payload["respect_filter"] is True
    assert payload["user_id"] == "user-1"
    assert payload["group_id"] == ""
    assert "主题：旅行计划" in payload["text"]
    assert "概括：我们讨论了春游安排" in payload["text"]


@pytest.mark.asyncio
async def test_import_to_long_term_memory_falls_back_when_content_empty(monkeypatch):
    summarizer = _build_summarizer()
    fallback_calls = []

    async def fake_fallback(**kwargs):
        fallback_calls.append(kwargs)

    summarizer._fallback_import_to_long_term_memory = fake_fallback
    monkeypatch.setattr(
        summarizer_module,
        "_chat_manager",
        SimpleNamespace(get_session_by_session_id=lambda session_id: SimpleNamespace(user_id="user-1", group_id="")),
    )

    await summarizer._import_to_long_term_memory(
        record_id=2,
        theme="",
        summary="",
        participants=[],
        start_time=10.0,
        end_time=20.0,
        original_text="raw chat",
    )

    assert len(fallback_calls) == 1
    assert fallback_calls[0]["record_id"] == 2
    assert fallback_calls[0]["original_text"] == "raw chat"


@pytest.mark.asyncio
async def test_import_to_long_term_memory_falls_back_when_ingest_fails(monkeypatch):
    summarizer = _build_summarizer()
    fallback_calls = []

    async def fake_ingest_summary(**kwargs):
        return SimpleNamespace(success=False, detail="boom", stored_ids=[])

    async def fake_fallback(**kwargs):
        fallback_calls.append(kwargs)

    summarizer._fallback_import_to_long_term_memory = fake_fallback
    monkeypatch.setattr(
        summarizer_module,
        "_chat_manager",
        SimpleNamespace(get_session_by_session_id=lambda session_id: SimpleNamespace(user_id="user-1", group_id="group-1")),
    )
    monkeypatch.setattr("src.services.memory_service.memory_service.ingest_summary", fake_ingest_summary)

    await summarizer._import_to_long_term_memory(
        record_id=3,
        theme="电影",
        summary="聊了电影推荐",
        participants=["Alice"],
        start_time=3.0,
        end_time=4.0,
        original_text="raw",
    )

    assert len(fallback_calls) == 1
    assert fallback_calls[0]["theme"] == "电影"


@pytest.mark.asyncio
async def test_fallback_import_to_long_term_memory_sets_generate_from_chat(monkeypatch):
    calls = []
    summarizer = _build_summarizer()

    async def fake_ingest_summary(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(success=True, detail="chat_filtered", stored_ids=[])

    monkeypatch.setattr(
        summarizer_module,
        "_chat_manager",
        SimpleNamespace(get_session_by_session_id=lambda session_id: SimpleNamespace(user_id="user-2", group_id="group-2")),
    )
    monkeypatch.setattr(summarizer_module, "global_config", SimpleNamespace(memory=SimpleNamespace(chat_history_topic_check_message_threshold=12)))
    monkeypatch.setattr("src.services.memory_service.memory_service.ingest_summary", fake_ingest_summary)

    await summarizer._fallback_import_to_long_term_memory(
        record_id=4,
        theme="工作",
        participants=["Alice"],
        start_time=5.0,
        end_time=6.0,
        original_text="a" * 128,
    )

    assert len(calls) == 1
    metadata = calls[0]["metadata"]
    assert metadata["generate_from_chat"] is True
    assert metadata["context_length"] == 12
    assert calls[0]["respect_filter"] is True

