from types import SimpleNamespace

import pytest

from src.services import memory_flow_service as memory_flow_module


@pytest.mark.asyncio
async def test_long_term_memory_session_manager_reuses_single_summarizer(monkeypatch):
    starts: list[str] = []
    summarizers: list[object] = []

    class FakeSummarizer:
        def __init__(self, session_id: str):
            self.session_id = session_id
            summarizers.append(self)

        async def start(self):
            starts.append(self.session_id)

        async def stop(self):
            starts.append(f"stop:{self.session_id}")

    monkeypatch.setattr(
        memory_flow_module,
        "global_config",
        SimpleNamespace(memory=SimpleNamespace(long_term_auto_summary_enabled=True)),
    )
    monkeypatch.setattr(memory_flow_module, "ChatHistorySummarizer", FakeSummarizer)

    manager = memory_flow_module.LongTermMemorySessionManager()
    message = SimpleNamespace(session_id="session-1")

    await manager.on_message(message)
    await manager.on_message(message)

    assert len(summarizers) == 1
    assert starts == ["session-1"]


@pytest.mark.asyncio
async def test_long_term_memory_session_manager_shutdown_stops_all(monkeypatch):
    stopped: list[str] = []

    class FakeSummarizer:
        def __init__(self, session_id: str):
            self.session_id = session_id

        async def start(self):
            return None

        async def stop(self):
            stopped.append(self.session_id)

    monkeypatch.setattr(
        memory_flow_module,
        "global_config",
        SimpleNamespace(memory=SimpleNamespace(long_term_auto_summary_enabled=True)),
    )
    monkeypatch.setattr(memory_flow_module, "ChatHistorySummarizer", FakeSummarizer)

    manager = memory_flow_module.LongTermMemorySessionManager()
    await manager.on_message(SimpleNamespace(session_id="session-a"))
    await manager.on_message(SimpleNamespace(session_id="session-b"))
    await manager.shutdown()

    assert stopped == ["session-a", "session-b"]


def test_person_fact_parse_fact_list_deduplicates_and_filters_short_items():
    raw = '["他喜欢猫", "他喜欢猫", "好", "", "他会弹吉他"]'

    result = memory_flow_module.PersonFactWritebackService._parse_fact_list(raw)

    assert result == ["他喜欢猫", "他会弹吉他"]


def test_person_fact_looks_ephemeral_detects_short_chitchat():
    assert memory_flow_module.PersonFactWritebackService._looks_ephemeral("哈哈")
    assert memory_flow_module.PersonFactWritebackService._looks_ephemeral("好的？")
    assert not memory_flow_module.PersonFactWritebackService._looks_ephemeral("她最近在学法语和钢琴")


def test_person_fact_resolve_target_person_for_private_chat(monkeypatch):
    class FakePerson:
        def __init__(self, person_id: str):
            self.person_id = person_id
            self.is_known = True

    service = memory_flow_module.PersonFactWritebackService.__new__(memory_flow_module.PersonFactWritebackService)
    monkeypatch.setattr(memory_flow_module, "is_bot_self", lambda platform, user_id: False)
    monkeypatch.setattr(memory_flow_module, "get_person_id", lambda platform, user_id: f"{platform}:{user_id}")
    monkeypatch.setattr(memory_flow_module, "Person", FakePerson)

    message = SimpleNamespace(session=SimpleNamespace(platform="qq", user_id="123", group_id=""))

    person = service._resolve_target_person(message)

    assert person is not None
    assert person.person_id == "qq:123"


@pytest.mark.asyncio
async def test_memory_automation_service_auto_starts_and_delegates(monkeypatch):
    events: list[tuple[str, str]] = []

    class FakeSessionManager:
        async def on_message(self, message):
            events.append(("incoming", message.session_id))

        async def shutdown(self):
            events.append(("shutdown", "session"))

    class FakeFactWriteback:
        async def start(self):
            events.append(("start", "fact"))

        async def enqueue(self, message):
            events.append(("sent", message.session_id))

        async def shutdown(self):
            events.append(("shutdown", "fact"))

    service = memory_flow_module.MemoryAutomationService()
    service.session_manager = FakeSessionManager()
    service.fact_writeback = FakeFactWriteback()

    await service.on_incoming_message(SimpleNamespace(session_id="session-1"))
    await service.on_message_sent(SimpleNamespace(session_id="session-1"))
    await service.shutdown()

    assert events == [
        ("start", "fact"),
        ("incoming", "session-1"),
        ("sent", "session-1"),
        ("shutdown", "session"),
        ("shutdown", "fact"),
    ]
