from types import SimpleNamespace

import pytest

from src.maisaka.memory.heuristic_injector import HeuristicMemoryContext, HeuristicMemoryInjector
from src.services.memory_service import MemoryHit, MemoryService


@pytest.mark.asyncio
async def test_preview_paragraph_sources_calls_delete_admin_preview_paragraph(monkeypatch):
    """具名段落来源预览必须走启发式相同的 delete_admin action=preview mode=paragraph。"""

    service = MemoryService()
    calls = []

    async def fake_delete_admin(*, action: str, **kwargs):
        calls.append({"action": action, **kwargs})
        return {
            "success": True,
            "items": [
                {
                    "item_type": "paragraph",
                    "item_hash": "para-hash-1",
                    "source": "chat_summary:session-1",
                }
            ],
        }

    monkeypatch.setattr(service, "delete_admin", fake_delete_admin)

    hashes = ["para-hash-1"]
    result = await service.preview_paragraph_sources(paragraph_hashes=hashes)

    assert calls == [
        {
            "action": "preview",
            "mode": "paragraph",
            "selector": {"hashes": hashes},
            "timeout_ms": 10000,
        }
    ]
    assert result == {"para-hash-1": "chat_summary:session-1"}


def test_is_hit_allowed_does_not_treat_paragraph_search_channel_as_storage_source(monkeypatch):
    """启发式 _is_hit_allowed 作为纯函数时，未解析来源的 paragraph_search 通道不是存储来源。"""

    monkeypatch.setattr(
        HeuristicMemoryInjector,
        "_is_chat_memory_allowed",
        staticmethod(lambda source_session_id, current_session: True),
    )

    context = HeuristicMemoryContext(
        session=SimpleNamespace(session_id="session-1", is_group_session=False),
        recent_messages=[],
        active_person_ids=set(),
    )
    hit = MemoryHit(
        content="广播站值夜班",
        score=0.9,
        hit_type="paragraph",
        source="paragraph_search",
        hash_value="para-hash-1",
        metadata={},
    )

    allowed = HeuristicMemoryInjector._is_hit_allowed(hit, context, resolved_source="")

    assert allowed is False
