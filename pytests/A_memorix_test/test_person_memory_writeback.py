from types import SimpleNamespace

import pytest

from src.person_info import person_info as person_info_module


@pytest.mark.asyncio
async def test_store_person_memory_from_answer_writes_person_fact(monkeypatch):
    calls = []

    class FakePerson:
        def __init__(self, person_id: str):
            self.person_id = person_id
            self.person_name = "Alice"
            self.is_known = True

    async def fake_ingest_text(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(success=True, detail="", stored_ids=["p1"])

    session = SimpleNamespace(platform="qq", user_id="10001", group_id="", session_id="session-1")
    monkeypatch.setattr(person_info_module, "_chat_manager", SimpleNamespace(get_session_by_session_id=lambda chat_id: session))
    monkeypatch.setattr(person_info_module, "get_person_id_by_person_name", lambda person_name: "person-1")
    monkeypatch.setattr(person_info_module, "Person", FakePerson)
    monkeypatch.setattr(person_info_module.memory_service, "ingest_text", fake_ingest_text)

    await person_info_module.store_person_memory_from_answer("Alice", "她喜欢猫和爵士乐", "session-1")

    assert len(calls) == 1
    payload = calls[0]
    assert payload["external_id"].startswith("person_fact:person-1:")
    assert payload["source_type"] == "person_fact"
    assert payload["chat_id"] == "session-1"
    assert payload["person_ids"] == ["person-1"]
    assert payload["participants"] == ["Alice"]
    assert payload["respect_filter"] is True
    assert payload["user_id"] == "10001"
    assert payload["group_id"] == ""
    assert payload["metadata"]["person_id"] == "person-1"


@pytest.mark.asyncio
async def test_store_person_memory_from_answer_skips_unknown_person(monkeypatch):
    calls = []

    class FakePerson:
        def __init__(self, person_id: str):
            self.person_id = person_id
            self.person_name = "Unknown"
            self.is_known = False

    async def fake_ingest_text(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(success=True, detail="", stored_ids=["p1"])

    session = SimpleNamespace(platform="qq", user_id="10001", group_id="", session_id="session-1")
    monkeypatch.setattr(person_info_module, "_chat_manager", SimpleNamespace(get_session_by_session_id=lambda chat_id: session))
    monkeypatch.setattr(person_info_module, "get_person_id_by_person_name", lambda person_name: "person-1")
    monkeypatch.setattr(person_info_module, "Person", FakePerson)
    monkeypatch.setattr(person_info_module.memory_service, "ingest_text", fake_ingest_text)

    await person_info_module.store_person_memory_from_answer("Alice", "她喜欢猫和爵士乐", "session-1")

    assert calls == []


@pytest.mark.asyncio
async def test_store_person_memory_from_answer_skips_empty_content(monkeypatch):
    calls = []

    async def fake_ingest_text(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(success=True, detail="", stored_ids=["p1"])

    monkeypatch.setattr(person_info_module.memory_service, "ingest_text", fake_ingest_text)

    await person_info_module.store_person_memory_from_answer("Alice", "   ", "session-1")

    assert calls == []

