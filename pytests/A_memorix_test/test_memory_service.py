import pytest

from src.services.memory_service import MemorySearchResult, MemoryService


def test_coerce_write_result_treats_skipped_payload_as_success():
    result = MemoryService._coerce_write_result({"skipped_ids": ["p1"], "detail": "chat_filtered"})

    assert result.success is True
    assert result.stored_ids == []
    assert result.skipped_ids == ["p1"]
    assert result.detail == "chat_filtered"


@pytest.mark.asyncio
async def test_graph_admin_invokes_plugin(monkeypatch):
    service = MemoryService()
    calls = []

    async def fake_invoke(component_name, args=None, **kwargs):
        calls.append((component_name, args, kwargs))
        return {"success": True, "nodes": [], "edges": []}

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    result = await service.graph_admin(action="get_graph", limit=12)

    assert result["success"] is True
    assert calls == [("memory_graph_admin", {"action": "get_graph", "limit": 12}, {"timeout_ms": 30000})]


@pytest.mark.asyncio
async def test_get_recycle_bin_uses_maintain_memory_tool(monkeypatch):
    service = MemoryService()
    calls = []

    async def fake_invoke(component_name, args=None, **kwargs):
        calls.append((component_name, args))
        return {"success": True, "items": [{"hash": "abc"}], "count": 1}

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    result = await service.get_recycle_bin(limit=5)

    assert result == {"success": True, "items": [{"hash": "abc"}], "count": 1}
    assert calls == [("maintain_memory", {"action": "recycle_bin", "limit": 5})]


@pytest.mark.asyncio
async def test_search_respects_filter_by_default(monkeypatch):
    service = MemoryService()
    calls = []

    async def fake_invoke(component_name, args=None, **kwargs):
        calls.append((component_name, args))
        return {"summary": "ok", "hits": [], "filtered": True}

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    result = await service.search(
        "mai",
        chat_id="stream-1",
        person_id="person-1",
        user_id="user-1",
        group_id="",
    )

    assert isinstance(result, MemorySearchResult)
    assert result.filtered is True
    assert calls == [
        (
            "search_memory",
            {
                "query": "mai",
                "limit": 5,
                "mode": "hybrid",
                "chat_id": "stream-1",
                "person_id": "person-1",
                "time_start": None,
                "time_end": None,
                "respect_filter": True,
                "user_id": "user-1",
                "group_id": "",
            },
        )
    ]


@pytest.mark.asyncio
async def test_ingest_summary_can_bypass_filter(monkeypatch):
    service = MemoryService()
    calls = []

    async def fake_invoke(component_name, args=None, **kwargs):
        calls.append((component_name, args))
        return {"success": True, "stored_ids": ["p1"], "detail": ""}

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    result = await service.ingest_summary(
        external_id="chat_history:1",
        chat_id="stream-1",
        text="summary",
        respect_filter=False,
        user_id="user-1",
    )

    assert result.success is True
    assert calls == [
        (
            "ingest_summary",
            {
                "external_id": "chat_history:1",
                "chat_id": "stream-1",
                "text": "summary",
                "participants": [],
                "time_start": None,
                "time_end": None,
                "tags": [],
                "metadata": {},
                "respect_filter": False,
                "user_id": "user-1",
                "group_id": "",
            },
        )
    ]


@pytest.mark.asyncio
async def test_v5_admin_invokes_plugin(monkeypatch):
    service = MemoryService()
    calls = []

    async def fake_invoke(component_name, args=None, **kwargs):
        calls.append((component_name, args, kwargs))
        return {"success": True, "count": 1}

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    result = await service.v5_admin(action="status", target="mai", limit=5)

    assert result["success"] is True
    assert calls == [("memory_v5_admin", {"action": "status", "target": "mai", "limit": 5}, {"timeout_ms": 30000})]


@pytest.mark.asyncio
async def test_delete_admin_uses_long_timeout(monkeypatch):
    service = MemoryService()
    calls = []

    async def fake_invoke(component_name, args=None, **kwargs):
        calls.append((component_name, args, kwargs))
        return {"success": True, "operation_id": "del-1"}

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    result = await service.delete_admin(action="execute", mode="relation", selector={"query": "mai"})

    assert result["success"] is True
    assert calls == [
        (
            "memory_delete_admin",
            {"action": "execute", "mode": "relation", "selector": {"query": "mai"}},
            {"timeout_ms": 120000},
        )
    ]


@pytest.mark.asyncio
async def test_search_returns_empty_when_query_and_time_missing_async():
    service = MemoryService()

    result = await service.search("", time_start=None, time_end=None)

    assert isinstance(result, MemorySearchResult)
    assert result.summary == ""
    assert result.hits == []
    assert result.filtered is False


@pytest.mark.asyncio
async def test_search_accepts_string_time_bounds(monkeypatch):
    service = MemoryService()
    calls = []

    async def fake_invoke(component_name, args=None, **kwargs):
        calls.append((component_name, args))
        return {"summary": "ok", "hits": [], "filtered": False}

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    result = await service.search(
        "广播站",
        mode="time",
        time_start="2026/03/18",
        time_end="2026/03/18 09:30",
    )

    assert isinstance(result, MemorySearchResult)
    assert calls == [
        (
            "search_memory",
            {
                "query": "广播站",
                "limit": 5,
                "mode": "time",
                "chat_id": "",
                "person_id": "",
                "time_start": "2026/03/18",
                "time_end": "2026/03/18 09:30",
                "respect_filter": True,
                "user_id": "",
                "group_id": "",
            },
        )
    ]


def test_coerce_search_result_preserves_aggregate_source_branches():
    result = MemoryService._coerce_search_result(
        {
            "hits": [
                {
                    "content": "广播站值夜班",
                    "type": "paragraph",
                    "metadata": {"event_time_start": 1.0},
                    "source_branches": ["search", "time"],
                    "rank": 1,
                }
            ]
        }
    )

    assert result.hits[0].metadata["source_branches"] == ["search", "time"]
    assert result.hits[0].metadata["rank"] == 1


@pytest.mark.asyncio
async def test_import_admin_uses_long_timeout(monkeypatch):
    service = MemoryService()
    calls = []

    async def fake_invoke(component_name, args=None, **kwargs):
        calls.append((component_name, args, kwargs))
        return {"success": True, "task_id": "import-1"}

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    result = await service.import_admin(action="create_lpmm_openie", alias="lpmm")

    assert result["success"] is True
    assert calls == [
        (
            "memory_import_admin",
            {"action": "create_lpmm_openie", "alias": "lpmm"},
            {"timeout_ms": 120000},
        )
    ]


@pytest.mark.asyncio
async def test_tuning_admin_uses_long_timeout(monkeypatch):
    service = MemoryService()
    calls = []

    async def fake_invoke(component_name, args=None, **kwargs):
        calls.append((component_name, args, kwargs))
        return {"success": True, "task_id": "tuning-1"}

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    result = await service.tuning_admin(action="create_task", payload={"query": "mai"})

    assert result["success"] is True
    assert calls == [
        (
            "memory_tuning_admin",
            {"action": "create_task", "payload": {"query": "mai"}},
            {"timeout_ms": 120000},
        )
    ]
