from types import SimpleNamespace

import pytest

from src.services.memory_service import MemoryMetadataUnavailableError, MemorySearchResult, MemoryService


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
    assert calls == [("memory_graph_admin", {"action": "get_graph", "limit": 12}, {})]


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
                "mode": "search",
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
    assert calls == [("memory_v5_admin", {"action": "status", "target": "mai", "limit": 5}, {})]


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


@pytest.mark.asyncio
async def test_preview_paragraph_sources_uses_delete_admin_preview(monkeypatch):
    service = MemoryService()
    calls = []

    async def fake_invoke(component_name, args=None, **kwargs):
        calls.append((component_name, args, kwargs))
        return {
            "success": True,
            "items": [
                {"item_type": "paragraph", "item_hash": "h1", "source": "chat_summary:s1"},
                {"item_type": "entity", "item_hash": "h2", "source": "ignored"},
                {"item_type": "paragraph", "item_hash": "h3", "source": "person_fact:p1"},
                {"item_type": "paragraph", "item_hash": "", "source": "chat_summary:s2"},
                "not-a-dict",
            ],
        }

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    result = await service.preview_paragraph_sources(["h1", "h3"])

    assert result == {"h1": "chat_summary:s1", "h3": "person_fact:p1"}
    assert calls == [
        (
            "memory_delete_admin",
            {
                "action": "preview",
                "mode": "paragraph",
                "selector": {"hashes": ["h1", "h3"]},
            },
            {"timeout_ms": 10000},
        )
    ]


@pytest.mark.asyncio
async def test_preview_paragraph_sources_returns_empty_on_failure(monkeypatch):
    service = MemoryService()

    async def fake_invoke(component_name, args=None, **kwargs):
        raise RuntimeError("preview failed")

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    assert await service.preview_paragraph_sources(["h1"]) == {}


@pytest.mark.asyncio
async def test_preview_paragraph_sources_returns_empty_when_unsuccessful(monkeypatch):
    service = MemoryService()

    async def fake_invoke(component_name, args=None, **kwargs):
        return {
            "success": False,
            "items": [{"item_type": "paragraph", "item_hash": "h1", "source": "chat_summary:s1"}],
        }

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    assert await service.preview_paragraph_sources(["h1"]) == {}


@pytest.mark.asyncio
async def test_preview_paragraph_sources_skips_invoke_for_empty_hashes(monkeypatch):
    service = MemoryService()
    calls = []

    async def fake_invoke(component_name, args=None, **kwargs):
        calls.append((component_name, args, kwargs))
        return {"success": True, "items": []}

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    assert await service.preview_paragraph_sources([]) == {}
    assert calls == []


@pytest.mark.asyncio
async def test_memory_correction_admin_uses_new_component_and_keeps_legacy_alias(monkeypatch):
    service = MemoryService()
    calls = []

    async def fake_invoke(component_name, args=None, **kwargs):
        calls.append((component_name, args, kwargs))
        return {"success": True, "plan_id": args["plan_id"]}

    monkeypatch.setattr(service, "_invoke", fake_invoke)

    result = await service.memory_correction_admin(action="get", plan_id="corr-1")
    legacy_result = await service.fuzzy_modify_admin(action="get", plan_id="corr-2")

    assert result == {"success": True, "plan_id": "corr-1"}
    assert legacy_result == {"success": True, "plan_id": "corr-2"}
    assert calls == [
        (
            "memory_correction_admin",
            {"action": "get", "plan_id": "corr-1"},
            {"timeout_ms": 120000},
        ),
        (
            "memory_correction_admin",
            {"action": "get", "plan_id": "corr-2"},
            {"timeout_ms": 120000},
        ),
    ]


def test_query_memory_rows_uses_runtime_kernel_and_keeps_list_shaping(monkeypatch):
    service = MemoryService()
    captured = []

    class FakeStore:
        @staticmethod
        def query(sql, params):
            captured.append((sql, params))
            return [{"hash": "h1"}]

    monkeypatch.setattr(
        "src.services.memory_service.get_runtime_kernel",
        lambda: SimpleNamespace(metadata_store=FakeStore()),
    )

    result = service.query_memory_rows("SELECT hash FROM paragraphs", ("p1",))

    assert result == [{"hash": "h1"}]
    assert captured == [("SELECT hash FROM paragraphs", ("p1",))]


def test_query_memory_rows_returns_empty_when_store_missing(monkeypatch):
    service = MemoryService()
    monkeypatch.setattr("src.services.memory_service.get_runtime_kernel", lambda: None)

    assert service.query_memory_rows("SELECT 1") == []


def test_query_memory_records_dict_shapes_rows_and_raises_when_unavailable(monkeypatch):
    service = MemoryService()
    captured = []

    class FakeRow(dict):
        pass

    class FakeStore:
        @staticmethod
        def query(sql, params):
            captured.append((sql, params))
            return [FakeRow(hash="h1")]

    monkeypatch.setattr(
        "src.services.memory_service.get_runtime_kernel",
        lambda: SimpleNamespace(metadata_store=FakeStore()),
    )

    result = service.query_memory_records("SELECT * FROM paragraphs WHERE hash = ?", ("h1",))

    assert result == [{"hash": "h1"}]
    assert all(isinstance(item, dict) for item in result)
    assert captured == [("SELECT * FROM paragraphs WHERE hash = ?", ("h1",))]

    monkeypatch.setattr("src.services.memory_service.get_runtime_kernel", lambda: None)
    with pytest.raises(MemoryMetadataUnavailableError, match="长期记忆 metadata 数据库尚未就绪"):
        service.query_memory_records("SELECT 1")


def test_search_paragraph_records_uses_original_sql(monkeypatch):
    service = MemoryService()
    captured = []

    class FakeStore:
        @staticmethod
        def query(sql, params):
            captured.append((sql, params))
            return [{"hash": "h1", "content": "咖啡"}]

    monkeypatch.setattr(
        "src.services.memory_service.get_runtime_kernel",
        lambda: SimpleNamespace(metadata_store=FakeStore()),
    )

    rows = service.search_paragraph_records(
        include_inactive=False,
        keyword="咖啡",
        pattern="%咖啡%",
        limit=20,
    )

    assert rows == [{"hash": "h1", "content": "咖啡"}]
    assert len(captured) == 1
    sql, params = captured[0]
    assert "FROM paragraphs" in sql
    assert "LOWER(COALESCE(content, '')) LIKE ? ESCAPE '\\'" in sql
    assert params == (0, "咖啡", "%咖啡%", "%咖啡%", "%咖啡%", 20)


@pytest.mark.asyncio
async def test_get_paragraphs_by_source_uses_host_ensure_kernel(monkeypatch):
    service = MemoryService()
    calls = []

    class FakeStore:
        @staticmethod
        def get_paragraphs_by_source(source: str):
            calls.append(source)
            return [{"hash": "p1", "metadata": {"trigger_message_count": 6}}]

    class FakeHost:
        @staticmethod
        async def _ensure_kernel():
            return SimpleNamespace(metadata_store=FakeStore())

    monkeypatch.setattr("src.services.memory_service.a_memorix_host_service", FakeHost())

    result = await service.get_paragraphs_by_source("chat_summary:session-1")

    assert result == [{"hash": "p1", "metadata": {"trigger_message_count": 6}}]
    assert calls == ["chat_summary:session-1"]


@pytest.mark.asyncio
async def test_get_paragraphs_by_source_returns_empty_when_ensure_kernel_missing(monkeypatch):
    service = MemoryService()

    class FakeHost:
        pass

    monkeypatch.setattr("src.services.memory_service.a_memorix_host_service", FakeHost())

    assert await service.get_paragraphs_by_source("chat_summary:session-1") == []
