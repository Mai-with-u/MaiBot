import json
from types import SimpleNamespace

import pytest

from src.A_memorix.core.utils.summary_importer import SUMMARY_PROMPT_TEMPLATE, SummaryImporter
from src.services import memory_flow_service as memory_flow_module


def _fake_global_config(**integration_values):
    return SimpleNamespace(
        a_memorix=SimpleNamespace(
            integration=SimpleNamespace(**integration_values),
        )
    )


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


def test_person_fact_resolve_target_person_for_group_without_reply(monkeypatch):
    class FakePerson:
        def __init__(self, person_id: str):
            self.person_id = person_id
            self.is_known = person_id in {"qq:user-1", "qq:user-2"}

    older_user_message = SimpleNamespace(
        platform="qq",
        user_id="user-1",
        message_info=SimpleNamespace(user_info=SimpleNamespace(user_id="user-1")),
    )
    latest_user_message = SimpleNamespace(
        platform="qq",
        user_id="user-2",
        message_info=SimpleNamespace(user_info=SimpleNamespace(user_id="user-2")),
    )

    def fake_find_messages(**kwargs):
        if kwargs.get("session_id") == "session-1":
            return [older_user_message, latest_user_message]
        return []

    service = memory_flow_module.PersonFactWritebackService.__new__(memory_flow_module.PersonFactWritebackService)
    monkeypatch.setattr(memory_flow_module, "find_messages", fake_find_messages)
    monkeypatch.setattr(memory_flow_module, "is_bot_self", lambda platform, user_id: False)
    monkeypatch.setattr(memory_flow_module, "get_person_id", lambda platform, user_id: f"{platform}:{user_id}")
    monkeypatch.setattr(memory_flow_module, "Person", FakePerson)

    message = SimpleNamespace(
        session_id="session-1",
        timestamp=20.0,
        reply_to="",
        session=SimpleNamespace(platform="qq", user_id="bot-1", group_id="group-1", session_id="session-1"),
    )

    person = service._resolve_target_person(message)

    assert person is not None
    assert person.person_id == "qq:user-2"


def test_person_fact_collect_user_evidence_keeps_latest_target_messages_without_reply(monkeypatch):
    class FakePerson:
        person_id = "qq:user-1"

    def make_message(message_id: str, user_id: str, text: str):
        return SimpleNamespace(
            message_id=message_id,
            platform="qq",
            user_id=user_id,
            processed_plain_text=text,
            message_info=SimpleNamespace(user_info=SimpleNamespace(user_id=user_id)),
        )

    messages = [
        make_message("user-1-a", "user-1", "第一条旧证据"),
        make_message("user-2-a", "user-2", "其他人的插话"),
        make_message("user-1-b", "user-1", "第二条证据"),
        make_message("user-1-c", "user-1", "第三条证据"),
        make_message("user-1-d", "user-1", "第四条最新证据"),
    ]

    def fake_find_messages(**kwargs):
        if kwargs.get("session_id") == "session-1":
            return messages
        return []

    service = memory_flow_module.PersonFactWritebackService.__new__(memory_flow_module.PersonFactWritebackService)
    monkeypatch.setattr(memory_flow_module, "find_messages", fake_find_messages)
    monkeypatch.setattr(memory_flow_module, "is_bot_self", lambda platform, user_id: False)
    monkeypatch.setattr(memory_flow_module, "get_person_id", lambda platform, user_id: f"{platform}:{user_id}")

    message = SimpleNamespace(
        session_id="session-1",
        timestamp=20.0,
        reply_to="",
        session=SimpleNamespace(session_id="session-1"),
    )

    evidence = service._collect_user_evidence(message, FakePerson())

    assert [item.message_id for item in evidence.target_messages] == ["user-1-b", "user-1-c", "user-1-d"]
    assert evidence.context_messages == messages


def test_person_fact_reply_evidence_keeps_context_for_short_answer(monkeypatch):
    class FakePerson:
        person_id = "qq:user-1"

    def make_message(
        *,
        message_id: str,
        user_id: str,
        nickname: str,
        text: str,
        timestamp: float,
    ):
        return SimpleNamespace(
            message_id=message_id,
            platform="qq",
            user_id=user_id,
            timestamp=timestamp,
            processed_plain_text=text,
            message_info=SimpleNamespace(
                user_info=SimpleNamespace(
                    user_id=user_id,
                    user_nickname=nickname,
                    user_cardname="",
                ),
            ),
        )

    bot_question = make_message(
        message_id="bot-question",
        user_id="bot-1",
        nickname="麦麦",
        text="你对什么过敏？",
        timestamp=10.0,
    )
    user_answer = make_message(
        message_id="user-answer",
        user_id="user-1",
        nickname="测试用户",
        text="青霉素。",
        timestamp=11.0,
    )

    def fake_find_messages(**kwargs):
        if kwargs.get("message_id") == "user-answer":
            return [user_answer]
        if kwargs.get("session_id") == "session-1":
            assert kwargs.get("filter_bot") is None
            return [bot_question, user_answer]
        return []

    monkeypatch.setattr(memory_flow_module, "find_messages", fake_find_messages)
    monkeypatch.setattr(memory_flow_module, "is_bot_self", lambda platform, user_id: user_id == "bot-1")
    monkeypatch.setattr(memory_flow_module, "get_person_id", lambda platform, user_id: f"{platform}:{user_id}")

    service = memory_flow_module.PersonFactWritebackService.__new__(memory_flow_module.PersonFactWritebackService)
    reply_message = SimpleNamespace(
        session_id="session-1",
        reply_to="user-answer",
        timestamp=12.0,
        session=SimpleNamespace(session_id="session-1"),
    )

    evidence = service._collect_user_evidence(reply_message, FakePerson())
    evidence_text = service._format_user_evidence(evidence)

    assert evidence.target_messages == [user_answer]
    assert evidence.context_messages == [bot_question, user_answer]
    assert "目标用户原始发言" in evidence_text
    assert "青霉素。" in evidence_text
    assert "邻近上下文" in evidence_text
    assert "麦麦: 你对什么过敏？" in evidence_text


@pytest.mark.asyncio
async def test_person_fact_writeback_skips_bot_only_fact_without_user_evidence(monkeypatch):
    stored_facts: list[tuple[str, str, str]] = []

    class FakePerson:
        person_id = "person-1"
        person_name = "测试用户"
        nickname = "测试用户"
        is_known = True

    service = memory_flow_module.PersonFactWritebackService.__new__(memory_flow_module.PersonFactWritebackService)
    service._resolve_target_person = lambda message: FakePerson()

    async def fake_extract_facts(person, reply_text, user_evidence_text):
        del person, reply_text, user_evidence_text
        return ["测试用户喜欢辣椒"]

    async def fake_store_person_memory_from_answer(person_name: str, memory_content: str, chat_id: str, **kwargs):
        del kwargs
        stored_facts.append((person_name, memory_content, chat_id))

    service._extract_facts = fake_extract_facts
    monkeypatch.setattr(memory_flow_module, "store_person_memory_from_answer", fake_store_person_memory_from_answer)
    monkeypatch.setattr(memory_flow_module, "find_messages", lambda **kwargs: [])

    message = SimpleNamespace(
        processed_plain_text="我记得你喜欢辣椒。",
        session_id="session-1",
        reply_to="",
        session=SimpleNamespace(platform="qq", user_id="bot-1", group_id=""),
    )

    await service._handle_message(message)

    assert stored_facts == []


@pytest.mark.asyncio
async def test_person_fact_writeback_uses_resolved_person_id(monkeypatch):
    stored_payloads: list[dict[str, object]] = []

    class FakePerson:
        person_id = "person-target"
        person_name = "重名用户"
        nickname = "重名用户"
        is_known = True

    user_message = SimpleNamespace(
        message_id="user-1",
        platform="qq",
        user_id="10001",
        processed_plain_text="我长期用青轴键盘。",
        message_info=SimpleNamespace(user_info=SimpleNamespace(user_id="10001")),
    )

    service = memory_flow_module.PersonFactWritebackService.__new__(memory_flow_module.PersonFactWritebackService)
    service._resolve_target_person = lambda message: FakePerson()

    async def fake_extract_facts(person, reply_text, user_evidence_text):
        assert person.person_id == "person-target"
        assert "我长期用青轴键盘" in user_evidence_text
        del reply_text
        return ["重名用户长期使用青轴键盘"]

    async def fake_store_person_memory_from_answer(person_name: str, memory_content: str, chat_id: str, **kwargs):
        stored_payloads.append(
            {
                "person_name": person_name,
                "memory_content": memory_content,
                "chat_id": chat_id,
                **kwargs,
            }
        )

    service._extract_facts = fake_extract_facts
    monkeypatch.setattr(memory_flow_module, "store_person_memory_from_answer", fake_store_person_memory_from_answer)
    monkeypatch.setattr(
        memory_flow_module,
        "find_messages",
        lambda **kwargs: [user_message] if kwargs.get("session_id") == "session-1" else [],
    )
    monkeypatch.setattr(memory_flow_module, "is_bot_self", lambda platform, user_id: False)
    monkeypatch.setattr(memory_flow_module, "get_person_id", lambda platform, user_id: "person-target")

    message = SimpleNamespace(
        processed_plain_text="我记住了，你长期用青轴键盘。",
        session_id="session-1",
        reply_to="",
        timestamp=20.0,
        session=SimpleNamespace(platform="qq", user_id="bot-1", group_id="", session_id="session-1"),
    )

    await service._handle_message(message)

    assert len(stored_payloads) == 1
    assert stored_payloads[0]["person_id"] == "person-target"
    assert stored_payloads[0]["evidence_message_ids"] == ["user-1"]


@pytest.mark.asyncio
async def test_chat_summary_writeback_service_triggers_when_threshold_reached(monkeypatch):
    events: list[tuple[str, object]] = []

    monkeypatch.setattr(
        memory_flow_module,
        "global_config",
        _fake_global_config(
            chat_summary_writeback_enabled=True,
            chat_summary_writeback_message_threshold=3,
            chat_summary_writeback_context_length=7,
        ),
    )
    monkeypatch.setattr(memory_flow_module, "count_messages", lambda **kwargs: 5)

    async def fake_ingest_summary(**kwargs):
        events.append(("ingest_summary", kwargs))
        return SimpleNamespace(success=True, detail="ok")

    async def fake_load_last_trigger_message_count(self, *, session_id: str, total_message_count: int) -> int:
        del self, session_id, total_message_count
        return 0

    monkeypatch.setattr(memory_flow_module.memory_service, "ingest_summary", fake_ingest_summary)
    monkeypatch.setattr(
        memory_flow_module.ChatSummaryWritebackService,
        "_load_last_trigger_message_count",
        fake_load_last_trigger_message_count,
    )

    service = memory_flow_module.ChatSummaryWritebackService()
    message = SimpleNamespace(session_id="session-1", session=SimpleNamespace(user_id="user-1", group_id="group-1"))

    await service._handle_message(message)

    assert len(events) == 1
    _, payload = events[0]
    assert payload["external_id"] == "chat_auto_summary:session-1:5"
    assert payload["chat_id"] == "session-1"
    assert payload["text"] == ""
    assert payload["metadata"]["generate_from_chat"] is True
    assert payload["metadata"]["context_length"] == 5
    assert payload["metadata"]["configured_context_length"] == 7
    assert payload["metadata"]["previous_trigger_message_count"] == 0
    assert payload["metadata"]["pending_message_count"] == 5
    assert payload["metadata"]["trigger"] == "message_threshold"
    assert payload["user_id"] == "user-1"
    assert payload["group_id"] == "group-1"


@pytest.mark.asyncio
async def test_chat_summary_writeback_service_skips_when_threshold_not_reached(monkeypatch):
    called = False

    monkeypatch.setattr(
        memory_flow_module,
        "global_config",
        _fake_global_config(
            chat_summary_writeback_enabled=True,
            chat_summary_writeback_message_threshold=6,
            chat_summary_writeback_context_length=9,
        ),
    )
    monkeypatch.setattr(memory_flow_module, "count_messages", lambda **kwargs: 5)

    async def fake_ingest_summary(**kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(success=True, detail="ok")

    async def fake_load_last_trigger_message_count(self, *, session_id: str, total_message_count: int) -> int:
        del self, session_id, total_message_count
        return 0

    monkeypatch.setattr(memory_flow_module.memory_service, "ingest_summary", fake_ingest_summary)
    monkeypatch.setattr(
        memory_flow_module.ChatSummaryWritebackService,
        "_load_last_trigger_message_count",
        fake_load_last_trigger_message_count,
    )

    service = memory_flow_module.ChatSummaryWritebackService()
    message = SimpleNamespace(session_id="session-1", session=SimpleNamespace(user_id="user-1", group_id="group-1"))

    await service._handle_message(message)

    assert called is False


@pytest.mark.asyncio
async def test_chat_summary_writeback_service_restores_previous_trigger_count(monkeypatch):
    events: list[tuple[str, object]] = []

    monkeypatch.setattr(
        memory_flow_module,
        "global_config",
        _fake_global_config(
            chat_summary_writeback_enabled=True,
            chat_summary_writeback_message_threshold=3,
            chat_summary_writeback_context_length=7,
        ),
    )
    monkeypatch.setattr(memory_flow_module, "count_messages", lambda **kwargs: 8)

    async def fake_ingest_summary(**kwargs):
        events.append(("ingest_summary", kwargs))
        return SimpleNamespace(success=True, detail="ok")

    async def fake_load_last_trigger_message_count(self, *, session_id: str, total_message_count: int) -> int:
        del self, session_id, total_message_count
        return 5

    monkeypatch.setattr(memory_flow_module.memory_service, "ingest_summary", fake_ingest_summary)
    monkeypatch.setattr(
        memory_flow_module.ChatSummaryWritebackService,
        "_load_last_trigger_message_count",
        fake_load_last_trigger_message_count,
    )

    service = memory_flow_module.ChatSummaryWritebackService()
    message = SimpleNamespace(session_id="session-1", session=SimpleNamespace(user_id="user-1", group_id="group-1"))

    await service._handle_message(message)

    assert len(events) == 1
    _, payload = events[0]
    assert payload["external_id"] == "chat_auto_summary:session-1:8"
    assert service._states["session-1"].last_trigger_message_count == 8


def test_chat_summary_writeback_effective_context_length_uses_pending_window():
    assert (
        memory_flow_module.ChatSummaryWritebackService._effective_context_length(
            configured_context_length=50,
            pending_message_count=12,
        )
        == 12
    )
    assert (
        memory_flow_module.ChatSummaryWritebackService._effective_context_length(
            configured_context_length=8,
            pending_message_count=12,
        )
        == 8
    )


def test_chat_summary_writeback_counts_until_trigger_message(monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_count_messages(**kwargs):
        calls.append(kwargs)
        return 12

    monkeypatch.setattr(memory_flow_module, "count_messages", fake_count_messages)

    assert (
        memory_flow_module.ChatSummaryWritebackService._count_messages_until_trigger(
            session_id="session-1",
            message_time=123.45,
        )
        == 12
    )
    assert calls == [{"session_id": "session-1", "end_time": 123.45}]


def test_summary_prompt_keeps_static_rules_before_chat_history():
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        bot_name="测试机器人",
        personality_context="你的性格设定是：稳定。",
        previous_summary_context="",
        chat_history="用户：第一条动态消息",
    )

    rules_index = prompt.index("事实筛选规则")
    chat_history_index = prompt.index("聊天记录内容")
    dynamic_history_index = prompt.index("用户：第一条动态消息")

    assert rules_index < chat_history_index < dynamic_history_index


def test_summary_prompt_forbids_repeating_rejected_fact_values():
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        bot_name="测试机器人",
        personality_context="你的性格设定是：稳定。",
        previous_summary_context="",
        chat_history="用户：不是猫毛，是青霉素",
    )

    assert "只输出最终正确事实" in prompt
    assert "不要复述 X 的具体值" in prompt
    assert "不得出现已否定、未确认、传闻、玩笑、注入、机器人误解、旧计划或旧金额中的具体值" in prompt


def test_summary_review_cleaner_does_not_guess_validity_from_keywords():
    dirty_summary = "此前记录林遥对花生过敏，这是测试示例，后来已纠正。"

    assert SummaryImporter._clean_review_summary(dirty_summary) == dirty_summary


@pytest.mark.asyncio
async def test_chat_summary_writeback_service_falls_back_to_current_count_for_legacy_summary(monkeypatch):
    called = False

    monkeypatch.setattr(
        memory_flow_module,
        "global_config",
        _fake_global_config(
            chat_summary_writeback_enabled=True,
            chat_summary_writeback_message_threshold=3,
            chat_summary_writeback_context_length=7,
        ),
    )
    monkeypatch.setattr(memory_flow_module, "count_messages", lambda **kwargs: 5)

    async def fake_ingest_summary(**kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(success=True, detail="ok")

    async def fake_load_last_trigger_message_count(self, *, session_id: str, total_message_count: int) -> int:
        del self, session_id, total_message_count
        return 5

    monkeypatch.setattr(memory_flow_module.memory_service, "ingest_summary", fake_ingest_summary)
    monkeypatch.setattr(
        memory_flow_module.ChatSummaryWritebackService,
        "_load_last_trigger_message_count",
        fake_load_last_trigger_message_count,
    )

    service = memory_flow_module.ChatSummaryWritebackService()
    message = SimpleNamespace(session_id="session-1", session=SimpleNamespace(user_id="user-1", group_id="group-1"))

    await service._handle_message(message)

    assert called is False
    assert service._states["session-1"].last_trigger_message_count == 5


@pytest.mark.asyncio
async def test_chat_summary_writeback_service_loads_trigger_count_from_summary_metadata(monkeypatch):
    class FakeMetadataStore:
        @staticmethod
        def get_paragraphs_by_source(source: str):
            assert source == "chat_summary:session-1"
            return [
                {"created_at": 1.0, "metadata": {"trigger_message_count": 3}},
                {"created_at": 2.0, "metadata": {"trigger_message_count": 6}},
            ]

    class FakeRuntimeManager:
        @staticmethod
        async def _ensure_kernel():
            return SimpleNamespace(metadata_store=FakeMetadataStore())

    monkeypatch.setattr(memory_flow_module.memory_service_module, "a_memorix_host_service", FakeRuntimeManager())

    service = memory_flow_module.ChatSummaryWritebackService()

    restored = await service._load_last_trigger_message_count(session_id="session-1", total_message_count=8)

    assert restored == 6


@pytest.mark.asyncio
async def test_memory_automation_service_auto_starts_and_delegates():
    events: list[tuple[str, str]] = []

    class FakeFactWriteback:
        async def start(self):
            events.append(("start", "fact"))

        async def enqueue(self, message):
            events.append(("sent", message.session_id))

        async def shutdown(self):
            events.append(("shutdown", "fact"))

    class FakeChatSummaryWriteback:
        async def start(self):
            events.append(("start", "summary"))

        async def enqueue(self, message):
            events.append(("summary", message.session_id))

        async def shutdown(self):
            events.append(("shutdown", "summary"))

    service = memory_flow_module.MemoryAutomationService()
    service.fact_writeback = FakeFactWriteback()
    service.chat_summary_writeback = FakeChatSummaryWriteback()

    await service.on_message_sent(SimpleNamespace(session_id="session-1"))
    await service.shutdown()

    assert events == [
        ("start", "fact"),
        ("start", "summary"),
        ("sent", "session-1"),
        ("summary", "session-1"),
        ("shutdown", "summary"),
        ("shutdown", "fact"),
    ]


@pytest.mark.asyncio
async def test_memory_automation_service_on_incoming_message_auto_starts_only():
    events: list[tuple[str, str]] = []

    class FakeFactWriteback:
        async def start(self):
            events.append(("start", "fact"))

        async def enqueue(self, message):
            events.append(("sent", message.session_id))

        async def shutdown(self):
            events.append(("shutdown", "fact"))

    class FakeChatSummaryWriteback:
        async def start(self):
            events.append(("start", "summary"))

        async def enqueue(self, message):
            events.append(("summary", message.session_id))

        async def shutdown(self):
            events.append(("shutdown", "summary"))

    service = memory_flow_module.MemoryAutomationService()
    service.fact_writeback = FakeFactWriteback()
    service.chat_summary_writeback = FakeChatSummaryWriteback()

    await service.on_incoming_message(SimpleNamespace(session_id="session-1"))
    await service.shutdown()

    assert events == [
        ("start", "fact"),
        ("start", "summary"),
        ("shutdown", "summary"),
        ("shutdown", "fact"),
    ]


def test_chat_summary_writeback_metadata_decodes_json_bytes_only():
    metadata = memory_flow_module.ChatSummaryWritebackService._paragraph_metadata(
        {"metadata": json.dumps({"trigger_message_count": 7}).encode("utf-8")}
    )
    invalid = memory_flow_module.ChatSummaryWritebackService._paragraph_metadata({"metadata": b"not-json-or-utf8"})

    assert metadata == {"trigger_message_count": 7}
    assert invalid == {}
