from src.chat.message_receive.chat_manager import BotChatSession
from src.maisaka.memory.heuristic_injector import HeuristicMemoryContext, HeuristicMemoryInjector
from src.services.memory_service import MemoryHit


def _context(*, session_id: str = "session-1", active_person_ids: set[str] | None = None) -> HeuristicMemoryContext:
    return HeuristicMemoryContext(
        session=BotChatSession(session_id=session_id, platform="qq", user_id="u1"),
        recent_messages=[],
        active_person_ids=active_person_ids or {"person-1"},
    )


def test_is_hit_allowed_uses_resolved_source_mapping_not_retrieval_channel() -> None:
    context = _context()
    resolved_sources = {
        "h-chat": "chat_summary:session-1",
        "h-person": "person_fact:person-1",
        "h-other": "chat_summary:session-2",
    }

    current_chat_hit = MemoryHit(content="本会话摘要", source="paragraph_search", hash_value="h-chat")
    person_hit = MemoryHit(content="人物事实", source="fusion", hash_value="h-person")
    other_chat_hit = MemoryHit(content="其他会话", source="relation_search", hash_value="h-other")
    unknown_hit = MemoryHit(content="未知来源", source="paragraph_search", hash_value="h-unknown")

    assert HeuristicMemoryInjector._is_hit_allowed(
        current_chat_hit,
        context,
        resolved_source=resolved_sources["h-chat"],
    )
    assert HeuristicMemoryInjector._is_hit_allowed(
        person_hit,
        context,
        resolved_source=resolved_sources["h-person"],
    )
    assert not HeuristicMemoryInjector._is_hit_allowed(
        other_chat_hit,
        context,
        resolved_source=resolved_sources["h-other"],
    )
    assert not HeuristicMemoryInjector._is_hit_allowed(
        unknown_hit,
        context,
        resolved_source=resolved_sources.get("h-unknown", ""),
    )
