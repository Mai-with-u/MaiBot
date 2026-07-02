from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import json
import pickle
import pytest

from src.services.memory_service import MemorySearchResult
from src.webui.dependencies import require_auth
from src.webui.routers import memory as memory_router_module
from src.webui.routers.memory import compat_router
from src.webui.routes import router as main_router


class _FakeDbContext:
    def __init__(self, db_session):
        self.db_session = db_session

    def __enter__(self):
        return self.db_session

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeMemoryMetadataStore:
    def __init__(self):
        self.paragraph_rows = [
            {
                "hash": "p-source",
                "content": "来源命中的聊天摘要段落",
                "created_at": 100.0,
                "updated_at": 105.0,
                "metadata": {},
                "source": "chat_summary:chat-1",
                "is_deleted": 0,
                "deleted_at": None,
            },
            {
                "hash": "p-meta",
                "content": "metadata 命中的聊天历史段落",
                "created_at": 110.0,
                "updated_at": 110.0,
                "metadata": {"chat_id": "chat-1"},
                "source": "external",
                "is_deleted": 0,
                "deleted_at": None,
            },
            {
                "hash": "p-other",
                "content": "其他聊天流段落",
                "created_at": 120.0,
                "updated_at": 120.0,
                "metadata": {"chat_id": "chat-2"},
                "source": "chat_summary:chat-2",
                "is_deleted": 0,
                "deleted_at": None,
            },
        ]
        self.episode_rows = [
            {
                "episode_id": "ep-1",
                "source": "chat_summary:chat-1",
                "title": "聊天摘要 Episode",
                "summary": "Episode 摘要",
                "paragraph_count": 2,
                "created_at": 130.0,
                "updated_at": 130.0,
                "event_time_start": 100.0,
                "event_time_end": 130.0,
            }
        ]
        self.feedback_rows = [
            {
                "id": 7,
                "query_tool_id": "tool-7",
                "session_id": "chat-1",
                "query_timestamp": 140.0,
                "status": "applied",
                "decision_json": '{"profile_person_ids":["person-1"]}',
                "query_snapshot_json": "{}",
                "rollback_plan_json": "{}",
                "rollback_result_json": "{}",
                "created_at": 139.0,
                "updated_at": 145.0,
                "rolled_back_at": 150.0,
                "rollback_reason": "测试回滚",
            }
        ]
        self.delete_rows = [
            {
                "operation_id": "op-1",
                "mode": "source",
                "selector": '{"sources":["chat_summary:chat-1"]}',
                "reason": "清理来源",
                "requested_by": "tester",
                "status": "restored",
                "created_at": 160.0,
                "restored_at": 170.0,
                "summary_json": '{"sources":["chat_summary:chat-1"]}',
            }
        ]
        self.delete_item_rows = [
            {
                "operation_id": "op-1",
                "item_type": "paragraph",
                "item_hash": "p-source",
                "item_key": "chat_summary:chat-1",
                "payload_json": '{"source":"chat_summary:chat-1","paragraph_hash":"p-source"}',
                "created_at": 160.0,
            }
        ]

    def query(self, sql: str, params=None):
        if "FROM paragraphs" in sql and "WHERE hash = ?" in sql:
            wanted_hash = params[0]
            return [row for row in self.paragraph_rows if row["hash"] == wanted_hash]
        if "FROM paragraphs" in sql and "ORDER BY COALESCE(updated_at" in sql:
            return list(self.paragraph_rows)
        if "FROM episodes" in sql:
            return list(self.episode_rows)
        if "FROM memory_feedback_tasks" in sql:
            return list(self.feedback_rows)
        if "FROM delete_operations" in sql:
            return list(self.delete_rows)
        if "FROM delete_operation_items" in sql:
            return list(self.delete_item_rows)
        if "person_profile" in sql or "FROM relations" in sql:
            return []
        return []


class _CountingTimelineMetadataStore(_FakeMemoryMetadataStore):
    def __init__(self):
        super().__init__()
        self.delete_item_query_count = 0
        self.profile_paragraph_query_count = 0
        self.paragraph_rows.append(
            {
                "hash": "p-zero",
                "content": "零时间戳段落",
                "created_at": 0.0,
                "updated_at": 0.0,
                "metadata": json.dumps({"chat_id": "chat-1"}).encode("utf-8"),
                "source": "external",
                "is_deleted": 0,
                "deleted_at": None,
            }
        )
        self.paragraph_rows.append(
            {
                "hash": "p-pickle",
                "content": "pickle metadata 不应在请求路径反序列化",
                "created_at": 180.0,
                "updated_at": 180.0,
                "metadata": pickle.dumps({"chat_id": "chat-1"}),
                "source": "external",
                "is_deleted": 0,
                "deleted_at": None,
            }
        )
        self.delete_rows.append(
            {
                "operation_id": "op-2",
                "mode": "source",
                "selector": '{"sources":["chat_summary:chat-1"]}',
                "reason": "第二次清理",
                "requested_by": "tester",
                "status": "done",
                "created_at": 175.0,
                "restored_at": None,
                "summary_json": '{"sources":["chat_summary:chat-1"]}',
            }
        )

    def query(self, sql: str, params=None):
        if "FROM delete_operation_items" in sql:
            self.delete_item_query_count += 1
        if "FROM paragraph_entities" in sql and "JOIN paragraphs" in sql:
            self.profile_paragraph_query_count += 1
        return super().query(sql, params)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.dependency_overrides[require_auth] = lambda: "ok"
    app.include_router(main_router)
    app.include_router(compat_router)
    return TestClient(app)


def test_webui_memory_graph_route(client: TestClient, monkeypatch):
    async def fake_graph_admin(*, action: str, **kwargs):
        assert action == "get_graph"
        return {
            "success": True,
            "nodes": [],
            "edges": [
                {
                    "source": "alice",
                    "target": "map",
                    "weight": 1.5,
                    "relation_hashes": ["rel-1"],
                    "predicates": ["持有"],
                    "relation_count": 1,
                    "evidence_count": 2,
                    "label": "持有",
                }
            ],
            "total_nodes": 0,
            "limit": kwargs.get("limit"),
        }

    monkeypatch.setattr(memory_router_module.memory_service, "graph_admin", fake_graph_admin)

    response = client.get("/api/webui/memory/graph", params={"limit": 77})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["limit"] == 77
    assert response.json()["edges"][0]["predicates"] == ["持有"]
    assert response.json()["edges"][0]["relation_count"] == 1
    assert response.json()["edges"][0]["evidence_count"] == 2


def test_webui_memory_graph_search_route(client: TestClient, monkeypatch):
    async def fake_graph_admin(*, action: str, **kwargs):
        assert action == "search"
        assert kwargs["query"] == "Alice"
        assert kwargs["limit"] == 33
        return {
            "success": True,
            "query": kwargs["query"],
            "limit": kwargs["limit"],
            "count": 1,
            "items": [
                {
                    "type": "entity",
                    "title": "Alice",
                    "matched_field": "name",
                    "matched_value": "Alice",
                    "entity_name": "Alice",
                    "entity_hash": "entity-1",
                    "appearance_count": 3,
                }
            ],
        }

    monkeypatch.setattr(memory_router_module.memory_service, "graph_admin", fake_graph_admin)

    response = client.get("/api/webui/memory/graph/search", params={"query": "Alice", "limit": 33})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["query"] == "Alice"
    assert response.json()["limit"] == 33
    assert response.json()["items"][0]["type"] == "entity"


@pytest.mark.parametrize(
    "params",
    [
        {"query": "", "limit": 50},
        {"query": "Alice", "limit": 0},
        {"query": "Alice", "limit": 201},
    ],
)
def test_webui_memory_graph_search_route_validation(client: TestClient, params):
    response = client.get("/api/webui/memory/graph/search", params=params)

    assert response.status_code == 422


def test_webui_memory_graph_node_detail_route(client: TestClient, monkeypatch):
    async def fake_graph_admin(*, action: str, **kwargs):
        assert action == "node_detail"
        assert kwargs["node_id"] == "Alice"
        return {
            "success": True,
            "node": {"id": "Alice", "type": "entity", "content": "Alice", "appearance_count": 3},
            "relations": [{"hash": "rel-1", "subject": "Alice", "predicate": "持有", "object": "Map", "text": "Alice 持有 Map", "confidence": 0.9, "paragraph_count": 1, "paragraph_hashes": ["p-1"], "source_paragraph": "p-1"}],
            "paragraphs": [{"hash": "p-1", "content": "Alice 拿着地图。", "preview": "Alice 拿着地图。", "source": "demo", "entity_count": 2, "relation_count": 1, "entities": ["Alice", "Map"], "relations": ["Alice 持有 Map"]}],
            "evidence_graph": {
                "nodes": [{"id": "entity:Alice", "type": "entity", "content": "Alice"}],
                "edges": [],
                "focus_entities": ["Alice"],
            },
        }

    monkeypatch.setattr(memory_router_module.memory_service, "graph_admin", fake_graph_admin)

    response = client.get("/api/webui/memory/graph/node-detail", params={"node_id": "Alice"})

    assert response.status_code == 200
    assert response.json()["node"]["id"] == "Alice"
    assert response.json()["relations"][0]["predicate"] == "持有"
    assert response.json()["evidence_graph"]["focus_entities"] == ["Alice"]


def test_webui_memory_graph_node_detail_route_returns_404(client: TestClient, monkeypatch):
    async def fake_graph_admin(*, action: str, **kwargs):
        assert action == "node_detail"
        return {"success": False, "error": "未找到节点: Missing"}

    monkeypatch.setattr(memory_router_module.memory_service, "graph_admin", fake_graph_admin)

    response = client.get("/api/webui/memory/graph/node-detail", params={"node_id": "Missing"})

    assert response.status_code == 404
    assert response.json()["detail"] == "未找到节点: Missing"


def test_webui_memory_graph_edge_detail_route(client: TestClient, monkeypatch):
    async def fake_graph_admin(*, action: str, **kwargs):
        assert action == "edge_detail"
        assert kwargs["source"] == "Alice"
        assert kwargs["target"] == "Map"
        return {
            "success": True,
            "edge": {
                "source": "Alice",
                "target": "Map",
                "weight": 1.5,
                "relation_hashes": ["rel-1"],
                "predicates": ["持有"],
                "relation_count": 1,
                "evidence_count": 1,
                "label": "持有",
            },
            "relations": [{"hash": "rel-1", "subject": "Alice", "predicate": "持有", "object": "Map", "text": "Alice 持有 Map", "confidence": 0.9, "paragraph_count": 1, "paragraph_hashes": ["p-1"], "source_paragraph": "p-1"}],
            "paragraphs": [{"hash": "p-1", "content": "Alice 拿着地图。", "preview": "Alice 拿着地图。", "source": "demo", "entity_count": 2, "relation_count": 1, "entities": ["Alice", "Map"], "relations": ["Alice 持有 Map"]}],
            "evidence_graph": {
                "nodes": [{"id": "relation:rel-1", "type": "relation", "content": "Alice 持有 Map"}],
                "edges": [{"source": "paragraph:p-1", "target": "relation:rel-1", "kind": "supports", "label": "支撑", "weight": 1.0}],
                "focus_entities": ["Alice", "Map"],
            },
        }

    monkeypatch.setattr(memory_router_module.memory_service, "graph_admin", fake_graph_admin)

    response = client.get("/api/webui/memory/graph/edge-detail", params={"source": "Alice", "target": "Map"})

    assert response.status_code == 200
    assert response.json()["edge"]["predicates"] == ["持有"]
    assert response.json()["paragraphs"][0]["source"] == "demo"
    assert response.json()["evidence_graph"]["edges"][0]["kind"] == "supports"


def test_webui_memory_graph_edge_detail_route_returns_404(client: TestClient, monkeypatch):
    async def fake_graph_admin(*, action: str, **kwargs):
        assert action == "edge_detail"
        return {"success": False, "error": "未找到边: Alice -> Missing"}

    monkeypatch.setattr(memory_router_module.memory_service, "graph_admin", fake_graph_admin)

    response = client.get("/api/webui/memory/graph/edge-detail", params={"source": "Alice", "target": "Missing"})

    assert response.status_code == 404
    assert response.json()["detail"] == "未找到边: Alice -> Missing"


def test_webui_memory_profile_query_resolves_platform_user_id(client: TestClient, monkeypatch):
    def fake_resolve_person_id_for_memory(**kwargs):
        assert kwargs == {"platform": "qq", "user_id": "12345", "strict_known": False}
        return "resolved-person-id"

    async def fake_profile_admin(*, action: str, **kwargs):
        assert action == "query"
        assert kwargs["person_id"] == "resolved-person-id"
        assert kwargs["person_keyword"] == "Alice"
        assert kwargs["limit"] == 9
        assert kwargs["force_refresh"] is True
        return {"success": True, "person_id": kwargs["person_id"], "profile_text": "profile"}

    monkeypatch.setattr(memory_router_module, "resolve_person_id_for_memory", fake_resolve_person_id_for_memory)
    monkeypatch.setattr(memory_router_module.memory_service, "profile_admin", fake_profile_admin)

    response = client.get(
        "/api/webui/memory/profiles/query",
        params={
            "platform": "qq",
            "user_id": "12345",
            "person_keyword": "Alice",
            "limit": 9,
            "force_refresh": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["person_id"] == "resolved-person-id"


def test_webui_memory_profile_evidence_route(client: TestClient, monkeypatch):
    async def fake_profile_admin(*, action: str, **kwargs):
        assert action == "evidence"
        assert kwargs["person_id"] == "person-1"
        assert kwargs["limit"] == 7
        assert kwargs["force_refresh"] is True
        return {
            "success": True,
            "person_id": "person-1",
            "evidence": [{"evidence_type": "person_fact", "hash": "p-1"}],
        }

    monkeypatch.setattr(memory_router_module.memory_service, "profile_admin", fake_profile_admin)

    response = client.get(
        "/api/webui/memory/profiles/person-1/evidence",
        params={"limit": 7, "force_refresh": True},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["evidence"][0]["hash"] == "p-1"


def test_webui_memory_profile_evidence_correct_route(client: TestClient, monkeypatch):
    async def fake_profile_admin(*, action: str, **kwargs):
        assert action == "correct_evidence"
        assert kwargs["person_id"] == "person-1"
        assert kwargs["evidence_type"] == "relation"
        assert kwargs["hash"] == "rel-1"
        assert kwargs["requested_by"] == "tester"
        assert kwargs["reason"] == "wrong_relation"
        assert kwargs["refresh"] is True
        assert kwargs["limit"] == 6
        return {"success": True, "operation_id": "delete-1"}

    monkeypatch.setattr(memory_router_module.memory_service, "profile_admin", fake_profile_admin)

    response = client.post(
        "/api/webui/memory/profiles/person-1/evidence/correct",
        json={
            "evidence_type": "relation",
            "hash": "rel-1",
            "requested_by": "tester",
            "reason": "wrong_relation",
            "refresh": True,
            "limit": 6,
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["operation_id"] == "delete-1"


def test_webui_memory_profile_query_prefers_explicit_person_id(client: TestClient, monkeypatch):
    def fake_resolve_person_id_for_memory(**kwargs):
        raise AssertionError(f"不应解析平台账号: {kwargs}")

    async def fake_profile_admin(*, action: str, **kwargs):
        assert action == "query"
        assert kwargs["person_id"] == "explicit-person-id"
        return {"success": True, "person_id": kwargs["person_id"]}

    monkeypatch.setattr(memory_router_module, "resolve_person_id_for_memory", fake_resolve_person_id_for_memory)
    monkeypatch.setattr(memory_router_module.memory_service, "profile_admin", fake_profile_admin)

    response = client.get(
        "/api/webui/memory/profiles/query",
        params={"person_id": "explicit-person-id", "platform": "qq", "user_id": "12345"},
    )

    assert response.status_code == 200
    assert response.json()["person_id"] == "explicit-person-id"


def test_webui_memory_profile_list_enriches_person_name(client: TestClient, monkeypatch):
    async def fake_profile_admin(*, action: str, **kwargs):
        assert action == "list"
        assert kwargs["limit"] == 7
        return {
            "success": True,
            "items": [
                {"person_id": "person-1", "profile_text": "profile-1"},
                {"person_id": "person-2", "profile_text": "profile-2"},
            ],
        }

    monkeypatch.setattr(memory_router_module.memory_service, "profile_admin", fake_profile_admin)
    monkeypatch.setattr(
        memory_router_module,
        "_get_person_name_for_person_id",
        lambda person_id: {"person-1": "Alice"}.get(person_id, ""),
    )

    response = client.get("/api/webui/memory/profiles", params={"limit": 7})

    assert response.status_code == 200
    assert response.json()["items"][0]["person_name"] == "Alice"
    assert response.json()["items"][1]["person_name"] == ""


def test_webui_memory_profile_search_resolves_platform_user_id(client: TestClient, monkeypatch):
    def fake_resolve_person_id_for_memory(**kwargs):
        assert kwargs == {"platform": "qq", "user_id": "12345", "strict_known": False}
        return "resolved-person-id"

    async def fake_profile_list(limit: int):
        assert limit == 200
        return {
            "success": True,
            "items": [
                {"person_id": "resolved-person-id", "person_name": "Alice", "profile_text": "喜欢咖啡"},
                {"person_id": "other-person-id", "person_name": "Bob", "profile_text": "喜欢茶"},
            ],
        }

    monkeypatch.setattr(memory_router_module, "resolve_person_id_for_memory", fake_resolve_person_id_for_memory)
    monkeypatch.setattr(memory_router_module, "_profile_list", fake_profile_list)

    response = client.get(
        "/api/webui/memory/profiles/search",
        params={"platform": "qq", "user_id": "12345", "limit": 50},
    )

    assert response.status_code == 200
    assert response.json()["items"] == [
        {"person_id": "resolved-person-id", "person_name": "Alice", "profile_text": "喜欢咖啡"}
    ]


def test_webui_memory_profile_search_filters_keyword(client: TestClient, monkeypatch):
    async def fake_profile_list(limit: int):
        assert limit == 200
        return {
            "success": True,
            "items": [
                {"person_id": "person-1", "person_name": "Alice", "profile_text": "喜欢咖啡"},
                {"person_id": "person-2", "person_name": "Bob", "profile_text": "喜欢茶"},
            ],
        }

    monkeypatch.setattr(memory_router_module, "_profile_list", fake_profile_list)

    response = client.get("/api/webui/memory/profiles/search", params={"person_keyword": "咖啡", "limit": 50})

    assert response.status_code == 200
    assert response.json()["items"] == [
        {"person_id": "person-1", "person_name": "Alice", "profile_text": "喜欢咖啡"}
    ]


def test_webui_memory_episode_list_resolves_platform_user_id(client: TestClient, monkeypatch):
    def fake_resolve_person_id_for_memory(**kwargs):
        assert kwargs == {"platform": "qq", "user_id": "12345", "strict_known": False}
        return "resolved-person-id"

    async def fake_episode_admin(*, action: str, **kwargs):
        assert action == "list"
        assert kwargs == {
            "query": "咖啡",
            "limit": 9,
            "source": "chat_summary:demo",
            "person_id": "resolved-person-id",
            "time_start": 100.0,
            "time_end": 200.0,
        }
        return {
            "success": True,
            "items": [{"episode_id": "ep-1", "person_id": "resolved-person-id", "summary": "喝咖啡"}],
            "count": 1,
        }

    monkeypatch.setattr(memory_router_module, "resolve_person_id_for_memory", fake_resolve_person_id_for_memory)
    monkeypatch.setattr(memory_router_module.memory_service, "episode_admin", fake_episode_admin)
    monkeypatch.setattr(memory_router_module, "_get_person_name_for_person_id", lambda person_id: "测试人物")

    response = client.get(
        "/api/webui/memory/episodes",
        params={
            "query": "咖啡",
            "limit": 9,
            "source": "chat_summary:demo",
            "platform": "qq",
            "user_id": "12345",
            "time_start": 100,
            "time_end": 200,
        },
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["person_name"] == "测试人物"


def test_webui_memory_episode_list_prefers_explicit_person_id(client: TestClient, monkeypatch):
    def fake_resolve_person_id_for_memory(**kwargs):
        raise AssertionError(f"不应解析平台账号: {kwargs}")

    async def fake_episode_admin(*, action: str, **kwargs):
        assert action == "list"
        assert kwargs["person_id"] == "explicit-person-id"
        return {"success": True, "items": []}

    monkeypatch.setattr(memory_router_module, "resolve_person_id_for_memory", fake_resolve_person_id_for_memory)
    monkeypatch.setattr(memory_router_module.memory_service, "episode_admin", fake_episode_admin)

    response = client.get(
        "/api/webui/memory/episodes",
        params={"person_id": "explicit-person-id", "platform": "qq", "user_id": "12345"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_webui_memory_timeline_returns_chat_scoped_events(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        memory_router_module,
        "_find_real_chat_session",
        lambda chat_id: SimpleNamespace(
            session_id=chat_id,
            platform="qq",
            group_id="100",
            user_id=None,
            group_name="测试群",
            user_cardname=None,
            user_nickname=None,
        )
        if chat_id == "chat-1"
        else None,
    )
    monkeypatch.setattr(memory_router_module, "_get_memory_metadata_store", lambda: _FakeMemoryMetadataStore())
    monkeypatch.setattr(memory_router_module, "_prefetch_latest_messages_by_session", lambda db_session, session_ids: {})
    monkeypatch.setattr(memory_router_module._chat_manager, "get_session_name", lambda chat_id: "测试群")

    response = client.get(
        "/api/webui/memory/timeline",
        params={"chat_id": "chat-1", "time_start": 90, "time_end": 180, "limit": 50},
    )

    assert response.status_code == 200
    payload = response.json()
    event_types = {item["event_type"] for item in payload["items"]}
    assert payload["success"] is True
    assert payload["chat"]["chat_id"] == "chat-1"
    assert "paragraph_created" in event_types
    assert "episode_created" in event_types
    assert "feedback_correction_applied" in event_types
    assert "feedback_correction_rollback" in event_types
    assert "delete_executed" in event_types
    assert "delete_restored" in event_types
    assert any(item["key_id"] == "p-meta" and item["attribution"] == "metadata.chat_id" for item in payload["items"])
    assert any(item["key_id"] == "p-source" and item["attribution"] == "source" for item in payload["items"])
    paragraph_created = next(
        item for item in payload["items"]
        if item["event_type"] == "paragraph_created" and item["key_id"] == "p-meta"
    )
    assert paragraph_created["jump_target"] == {
        "tab": "graph",
        "params": {"paragraph_hash": "p-meta"},
    }
    delete_executed = next(item for item in payload["items"] if item["event_type"] == "delete_executed")
    assert delete_executed["jump_target"] == {
        "tab": "delete",
        "params": {"operation_id": "op-1"},
    }
    assert all(item["jump_target"]["tab"] in {"graph", "delete", "episodes", "feedback", "profiles"} for item in payload["items"])
    assert payload["range"]["min_time"] == 100.0
    assert payload["range"]["max_time"] == 170.0


def test_memory_metadata_matches_chat_ids_list() -> None:
    assert memory_router_module._metadata_matches_chat({"chat_ids": ["chat-1"]}, "chat-1") is True
    assert (
        memory_router_module._metadata_matches_chat(
            {"source_context": {"chat_ids": ["chat-2"], "chat_id": "chat-3"}},
            "chat-2",
        )
        is True
    )
    assert memory_router_module._metadata_matches_chat({"chat_ids": ["chat-1"]}, "chat-2") is False


def test_webui_memory_timeline_filters_types_and_limit(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        memory_router_module,
        "_find_real_chat_session",
        lambda chat_id: SimpleNamespace(
            session_id=chat_id,
            platform="qq",
            group_id="100",
            user_id=None,
            group_name="测试群",
            user_cardname=None,
            user_nickname=None,
        ),
    )
    monkeypatch.setattr(memory_router_module, "_get_memory_metadata_store", lambda: _FakeMemoryMetadataStore())
    monkeypatch.setattr(memory_router_module, "_prefetch_latest_messages_by_session", lambda db_session, session_ids: {})

    response = client.get(
        "/api/webui/memory/timeline",
        params={"chat_id": "chat-1", "types": "episode", "limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["category"] == "episode"
    assert payload["items"][0]["jump_target"]["params"]["episode_id"] == "ep-1"


def test_webui_memory_timeline_deleted_paragraph_prefers_delete_operation(client: TestClient, monkeypatch):
    store = _FakeMemoryMetadataStore()
    store.paragraph_rows = [
        {
            "hash": "p-deleted",
            "content": "已经删除的段落",
            "created_at": 80.0,
            "updated_at": 80.0,
            "metadata": {"chat_id": "chat-1"},
            "source": "external",
            "is_deleted": 1,
            "deleted_at": 165.0,
        }
    ]
    store.delete_rows = []
    store.delete_item_rows = [
        {
            "operation_id": "op-paragraph-delete",
            "item_type": "paragraph",
            "item_hash": "p-deleted",
            "item_key": "p-deleted",
            "payload_json": '{"paragraph_hash":"p-deleted"}',
            "created_at": 165.0,
        }
    ]
    monkeypatch.setattr(
        memory_router_module,
        "_find_real_chat_session",
        lambda chat_id: SimpleNamespace(
            session_id=chat_id,
            platform="qq",
            group_id="100",
            user_id=None,
            group_name="测试群",
            user_cardname=None,
            user_nickname=None,
        ),
    )
    monkeypatch.setattr(memory_router_module, "_get_memory_metadata_store", lambda: store)
    monkeypatch.setattr(memory_router_module, "_prefetch_latest_messages_by_session", lambda db_session, session_ids: {})

    response = client.get(
        "/api/webui/memory/timeline",
        params={"chat_id": "chat-1", "time_start": 90, "time_end": 180, "limit": 20},
    )

    assert response.status_code == 200
    paragraph_deleted = next(item for item in response.json()["items"] if item["event_type"] == "paragraph_deleted")
    assert paragraph_deleted["jump_target"] == {
        "tab": "delete",
        "params": {"operation_id": "op-paragraph-delete"},
    }


def test_webui_memory_timeline_uses_latest_message_snapshot(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        memory_router_module,
        "_find_real_chat_session",
        lambda chat_id: SimpleNamespace(
            session_id=chat_id,
            platform="qq",
            group_id=None,
            user_id="user-1",
            group_name=None,
            user_cardname=None,
            user_nickname=None,
        ),
    )
    monkeypatch.setattr(memory_router_module, "_get_memory_metadata_store", lambda: _FakeMemoryMetadataStore())
    monkeypatch.setattr(
        memory_router_module,
        "_prefetch_latest_messages_by_session",
        lambda db_session, session_ids: {
            "chat-1": {
                "group_id": None,
                "group_name": None,
                "user_id": "user-1",
                "user_cardname": "测试名片",
                "user_nickname": "测试昵称",
            }
        },
    )
    monkeypatch.setattr(memory_router_module._chat_manager, "get_session_name", lambda chat_id: "")

    response = client.get("/api/webui/memory/timeline", params={"chat_id": "chat-1", "limit": 1})

    assert response.status_code == 200
    assert response.json()["chat"]["chat_name"] == "测试名片的私聊"


def test_webui_memory_timeline_rejects_unknown_chat(client: TestClient, monkeypatch):
    def fake_find_real_chat_session(chat_id: str):
        assert chat_id == "missing-chat"
        return None

    monkeypatch.setattr(memory_router_module, "_find_real_chat_session", fake_find_real_chat_session)
    monkeypatch.setattr(memory_router_module, "_get_memory_metadata_store", lambda: _FakeMemoryMetadataStore())

    response = client.get("/api/webui/memory/timeline", params={"chat_id": "missing-chat"})

    assert response.status_code == 400
    assert response.json()["detail"] == "聊天流不存在: missing-chat"


def test_webui_memory_timeline_handles_json_bytes_zero_timestamp_and_batches_items(
    client: TestClient,
    monkeypatch,
):
    store = _CountingTimelineMetadataStore()
    monkeypatch.setattr(
        memory_router_module,
        "_find_real_chat_session",
        lambda chat_id: SimpleNamespace(
            session_id=chat_id,
            platform="qq",
            group_id="100",
            user_id=None,
            group_name="测试群",
            user_cardname=None,
            user_nickname=None,
        ),
    )
    monkeypatch.setattr(memory_router_module, "_get_memory_metadata_store", lambda: store)
    monkeypatch.setattr(memory_router_module, "_prefetch_latest_messages_by_session", lambda db_session, session_ids: {})

    response = client.get("/api/webui/memory/timeline", params={"chat_id": "chat-1", "limit": 50})

    assert response.status_code == 200
    payload = response.json()
    paragraph_ids = {
        item["key_id"]
        for item in payload["items"]
        if item["event_type"] == "paragraph_created"
    }
    assert "p-zero" in paragraph_ids
    assert "p-pickle" not in paragraph_ids
    assert store.delete_item_query_count == 2


def test_compat_aggregate_route(client: TestClient, monkeypatch):
    async def fake_search(query: str, **kwargs):
        assert kwargs["mode"] == "aggregate"
        assert kwargs["respect_filter"] is False
        return MemorySearchResult(summary=f"summary:{query}", hits=[])

    monkeypatch.setattr(memory_router_module.memory_service, "search", fake_search)

    response = client.get("/api/query/aggregate", params={"query": "mai"})

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "summary": "summary:mai",
        "hits": [],
        "filtered": False,
        "error": "",
    }


def test_auto_save_routes(client: TestClient, monkeypatch):
    async def fake_runtime_admin(*, action: str, **kwargs):
        if action == "get_config":
            return {
                "success": True,
                "auto_save": True,
                "config": {"integration": {"fuzzy_modify_candidate_limit": 33}},
            }
        if action == "set_auto_save":
            return {"success": True, "auto_save": kwargs["enabled"]}
        raise AssertionError(action)

    monkeypatch.setattr(memory_router_module.memory_service, "runtime_admin", fake_runtime_admin)

    get_response = client.get("/api/config/auto_save")
    post_response = client.post("/api/config/auto_save", json={"enabled": False})
    runtime_response = client.get("/api/webui/memory/runtime/config")

    assert get_response.status_code == 200
    assert get_response.json() == {"success": True, "auto_save": True}
    assert post_response.status_code == 200
    assert post_response.json() == {"success": True, "auto_save": False}
    assert runtime_response.status_code == 200
    assert runtime_response.json()["fuzzy_modify_candidate_limit"] == 33


def test_memory_config_routes(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        memory_router_module.a_memorix_host_service,
        "get_config_schema",
        lambda: {"layout": {"type": "tabs"}, "sections": {"plugin": {"fields": {}}}},
    )
    monkeypatch.setattr(
        memory_router_module.a_memorix_host_service,
        "get_config_path",
        lambda: memory_router_module.Path("/tmp/config/bot_config.toml"),
    )
    monkeypatch.setattr(
        memory_router_module.a_memorix_host_service,
        "get_config",
        lambda: {"plugin": {"enabled": True}},
    )
    monkeypatch.setattr(
        memory_router_module.a_memorix_host_service,
        "get_raw_config",
        lambda: "[plugin]\nenabled = true\n",
    )
    monkeypatch.setattr(
        memory_router_module.a_memorix_host_service,
        "get_raw_config_with_meta",
        lambda: {
            "config": "[plugin]\nenabled = true\n",
            "exists": True,
            "using_default": False,
        },
    )

    schema_response = client.get("/api/webui/memory/config/schema")
    config_response = client.get("/api/webui/memory/config")
    raw_response = client.get("/api/webui/memory/config/raw")
    expected_path = memory_router_module.Path("/tmp/config/bot_config.toml").as_posix()

    assert schema_response.status_code == 200
    assert memory_router_module.Path(schema_response.json()["path"]).as_posix() == expected_path
    assert schema_response.json()["schema"]["layout"]["type"] == "tabs"

    assert config_response.status_code == 200
    assert config_response.json()["success"] is True
    assert config_response.json()["config"] == {"plugin": {"enabled": True}}
    assert memory_router_module.Path(config_response.json()["path"]).as_posix() == expected_path

    assert raw_response.status_code == 200
    assert raw_response.json()["success"] is True
    assert raw_response.json()["config"] == "[plugin]\nenabled = true\n"
    assert memory_router_module.Path(raw_response.json()["path"]).as_posix() == expected_path


def test_memory_config_raw_returns_default_template_when_file_missing(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        memory_router_module.a_memorix_host_service,
        "get_config_path",
        lambda: memory_router_module.Path("/tmp/config/bot_config.toml"),
    )
    monkeypatch.setattr(
        memory_router_module.a_memorix_host_service,
        "get_raw_config_with_meta",
        lambda: {
            "config": "[plugin]\nenabled = true\n",
            "exists": False,
            "using_default": True,
        },
    )

    response = client.get("/api/webui/memory/config/raw")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["config"] == "[plugin]\nenabled = true\n"
    assert response.json()["exists"] is False
    assert response.json()["using_default"] is True


def test_memory_config_update_routes(client: TestClient, monkeypatch):
    async def fake_update_config(config):
        assert config == {"plugin": {"enabled": False}}
        return {"success": True, "config_path": "config/bot_config.toml"}

    async def fake_update_raw(raw_config):
        assert raw_config == "[plugin]\nenabled = false\n"
        return {"success": True, "config_path": "config/bot_config.toml"}

    monkeypatch.setattr(memory_router_module.a_memorix_host_service, "update_config", fake_update_config)
    monkeypatch.setattr(memory_router_module.a_memorix_host_service, "update_raw_config", fake_update_raw)

    config_response = client.put("/api/webui/memory/config", json={"config": {"plugin": {"enabled": False}}})
    raw_response = client.put("/api/webui/memory/config/raw", json={"config": "[plugin]\nenabled = false\n"})

    assert config_response.status_code == 200
    assert config_response.json() == {"success": True, "config_path": "config/bot_config.toml"}

    assert raw_response.status_code == 200
    assert raw_response.json() == {"success": True, "config_path": "config/bot_config.toml"}


def test_memory_config_raw_rejects_invalid_toml(client: TestClient):
    response = client.put("/api/webui/memory/config/raw", json={"config": "[plugin\nenabled = true"})

    assert response.status_code == 400
    assert "TOML 格式错误" in response.json()["detail"]


def test_recycle_bin_route(client: TestClient, monkeypatch):
    async def fake_get_recycle_bin(*, limit: int):
        return {"success": True, "items": [{"hash": "deadbeef"}], "count": 1, "limit": limit}

    monkeypatch.setattr(memory_router_module.memory_service, "get_recycle_bin", fake_get_recycle_bin)

    response = client.get("/api/memory/recycle_bin", params={"limit": 10})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["count"] == 1
    assert response.json()["limit"] == 10


def test_import_guide_route(client: TestClient, monkeypatch):
    async def fake_import_admin(*, action: str, **kwargs):
        assert kwargs == {}
        if action == "get_guide":
            return {"success": True}
        if action == "get_settings":
            return {"success": True, "settings": {"path_aliases": {"raw": "/tmp/raw"}}}
        raise AssertionError(action)

    monkeypatch.setattr(memory_router_module.memory_service, "import_admin", fake_import_admin)

    response = client.get("/api/webui/memory/import/guide")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["source"] == "local"
    assert "长期记忆导入说明" in response.json()["content"]


def test_import_upload_route(client: TestClient, monkeypatch, tmp_path):
    monkeypatch.setattr(memory_router_module, "STAGING_ROOT", tmp_path)
    monkeypatch.setattr(
        memory_router_module._chat_manager,
        "get_existing_session_by_session_id",
        lambda chat_id: SimpleNamespace(session_id=chat_id) if chat_id == "session-1" else None,
    )

    async def fake_import_admin(*, action: str, **kwargs):
        assert action == "create_upload"
        assert kwargs["chat_id"] == "session-1"
        staged_files = kwargs["staged_files"]
        assert len(staged_files) == 1
        assert staged_files[0]["filename"] == "demo.txt"
        assert memory_router_module.Path(staged_files[0]["staged_path"]).exists()
        return {"success": True, "task_id": "task-1"}

    monkeypatch.setattr(memory_router_module.memory_service, "import_admin", fake_import_admin)

    response = client.post(
        "/api/import/upload",
        data={"payload_json": "{\"source\": \"upload\", \"chat_id\": \"session-1\"}"},
        files=[("files", ("demo.txt", b"hello world", "text/plain"))],
    )

    assert response.status_code == 200
    assert response.json() == {"success": True, "task_id": "task-1"}
    assert list(tmp_path.iterdir()) == []


def test_import_upload_route_rejects_unknown_chat_id(client: TestClient, monkeypatch, tmp_path):
    monkeypatch.setattr(memory_router_module, "STAGING_ROOT", tmp_path)
    monkeypatch.setattr(memory_router_module._chat_manager, "get_existing_session_by_session_id", lambda chat_id: None)
    monkeypatch.setattr(
        memory_router_module,
        "get_db_session",
        lambda: _FakeDbContext(SimpleNamespace(exec=lambda statement: SimpleNamespace(first=lambda: None))),
    )

    response = client.post(
        "/api/import/upload",
        data={"payload_json": "{\"chat_id\": \"missing-session\"}"},
        files=[("files", ("demo.txt", b"hello world", "text/plain"))],
    )

    assert response.status_code == 400
    assert "聊天流不存在" in response.json()["detail"]
    assert list(tmp_path.iterdir()) == []


def test_import_chat_targets_route(client: TestClient, monkeypatch):
    chat_session = SimpleNamespace(
        session_id="session-1",
        platform="qq",
        group_id="10001",
        group_name="测试群",
        user_id="20002",
        account_id="bot-1",
        scope="default",
        user_nickname=None,
        user_cardname=None,
        last_active_timestamp=None,
    )
    monkeypatch.setattr(memory_router_module._chat_manager, "get_session_name", lambda chat_id: "")
    monkeypatch.setattr(
        memory_router_module,
        "get_db_session",
        lambda: _FakeDbContext(
            SimpleNamespace(exec=lambda statement: SimpleNamespace(all=lambda: [chat_session], first=lambda: None))
        ),
    )

    response = client.get("/api/webui/memory/import/chat-targets")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"][0]["chat_id"] == "session-1"
    assert response.json()["data"][0]["chat_name"] == "测试群"
    assert response.json()["data"][0]["platform"] == "qq"
    assert response.json()["data"][0]["group_id"] == "10001"
    assert response.json()["data"][0]["user_id"] == "20002"
    assert response.json()["data"][0]["account_id"] == "bot-1"
    assert response.json()["data"][0]["scope"] == "default"


def test_v5_status_route(client: TestClient, monkeypatch):
    async def fake_v5_admin(*, action: str, **kwargs):
        assert action == "status"
        assert kwargs["target"] == "mai"
        return {"success": True, "active_count": 1, "inactive_count": 2, "deleted_count": 3}

    monkeypatch.setattr(memory_router_module.memory_service, "v5_admin", fake_v5_admin)

    response = client.get("/api/webui/memory/v5/status", params={"target": "mai"})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["deleted_count"] == 3


def test_delete_preview_route(client: TestClient, monkeypatch):
    async def fake_delete_admin(*, action: str, **kwargs):
        assert action == "preview"
        assert kwargs["mode"] == "paragraph"
        assert kwargs["selector"] == {"query": "demo"}
        return {"success": True, "counts": {"paragraphs": 1}, "dry_run": True}

    monkeypatch.setattr(memory_router_module.memory_service, "delete_admin", fake_delete_admin)

    response = client.post(
        "/api/webui/memory/delete/preview",
        json={"mode": "paragraph", "selector": {"query": "demo"}},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True, "counts": {"paragraphs": 1}, "dry_run": True}


def test_delete_preview_route_supports_mixed_mode(client: TestClient, monkeypatch):
    async def fake_delete_admin(*, action: str, **kwargs):
        assert action == "preview"
        assert kwargs["mode"] == "mixed"
        assert kwargs["selector"] == {
            "entity_hashes": ["entity-1"],
            "paragraph_hashes": ["p-1"],
            "relation_hashes": ["rel-1"],
            "sources": ["demo"],
        }
        return {"success": True, "mode": "mixed", "counts": {"entities": 1, "paragraphs": 1, "relations": 1, "sources": 1}}

    monkeypatch.setattr(memory_router_module.memory_service, "delete_admin", fake_delete_admin)

    response = client.post(
        "/api/webui/memory/delete/preview",
        json={
            "mode": "mixed",
            "selector": {
                "entity_hashes": ["entity-1"],
                "paragraph_hashes": ["p-1"],
                "relation_hashes": ["rel-1"],
                "sources": ["demo"],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "mixed"
    assert response.json()["counts"]["entities"] == 1


def test_delete_execute_route_supports_mixed_mode(client: TestClient, monkeypatch):
    async def fake_delete_admin(*, action: str, **kwargs):
        assert action == "execute"
        assert kwargs["mode"] == "mixed"
        assert kwargs["selector"] == {
            "entity_hashes": ["entity-1"],
            "paragraph_hashes": ["p-1"],
            "relation_hashes": ["rel-1"],
            "sources": ["demo"],
        }
        assert kwargs["reason"] == "knowledge_graph_delete_entity"
        assert kwargs["requested_by"] == "knowledge_graph"
        return {
            "success": True,
            "mode": "mixed",
            "operation_id": "op-mixed-1",
            "deleted_count": 4,
            "deleted_entity_count": 1,
            "deleted_relation_count": 1,
            "deleted_paragraph_count": 1,
            "deleted_source_count": 1,
            "counts": {"entities": 1, "paragraphs": 1, "relations": 1, "sources": 1},
        }

    monkeypatch.setattr(memory_router_module.memory_service, "delete_admin", fake_delete_admin)

    response = client.post(
        "/api/webui/memory/delete/execute",
        json={
            "mode": "mixed",
            "selector": {
                "entity_hashes": ["entity-1"],
                "paragraph_hashes": ["p-1"],
                "relation_hashes": ["rel-1"],
                "sources": ["demo"],
            },
            "reason": "knowledge_graph_delete_entity",
            "requested_by": "knowledge_graph",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["mode"] == "mixed"
    assert response.json()["operation_id"] == "op-mixed-1"


def test_episode_process_pending_route(client: TestClient, monkeypatch):
    async def fake_episode_admin(*, action: str, **kwargs):
        assert action == "process_pending"
        assert kwargs == {"limit": 7, "max_retry": 4}
        return {"success": True, "processed": 3}

    monkeypatch.setattr(memory_router_module.memory_service, "episode_admin", fake_episode_admin)

    response = client.post("/api/webui/memory/episodes/process-pending", json={"limit": 7, "max_retry": 4})

    assert response.status_code == 200
    assert response.json() == {"success": True, "processed": 3}


def test_import_list_route_includes_settings(client: TestClient, monkeypatch):
    calls = []

    async def fake_import_admin(*, action: str, **kwargs):
        calls.append((action, kwargs))
        if action == "list":
            return {"success": True, "items": [{"task_id": "task-1"}]}
        if action == "get_settings":
            return {"success": True, "settings": {"path_aliases": {"lpmm": "/tmp/lpmm"}}}
        raise AssertionError(action)

    monkeypatch.setattr(memory_router_module.memory_service, "import_admin", fake_import_admin)

    response = client.get("/api/webui/memory/import/tasks", params={"limit": 9})

    assert response.status_code == 200
    assert response.json()["items"] == [{"task_id": "task-1"}]
    assert response.json()["settings"] == {"path_aliases": {"lpmm": "/tmp/lpmm"}}
    assert calls == [("list", {"limit": 9}), ("get_settings", {})]


def test_tuning_profile_route_backfills_settings(client: TestClient, monkeypatch):
    calls = []

    async def fake_tuning_admin(*, action: str, **kwargs):
        calls.append((action, kwargs))
        if action == "get_profile":
            return {"success": True, "profile": {"retrieval": {"top_k": 8}}}
        if action == "get_settings":
            return {"success": True, "settings": {"profiles": ["default"]}}
        raise AssertionError(action)

    monkeypatch.setattr(memory_router_module.memory_service, "tuning_admin", fake_tuning_admin)

    response = client.get("/api/webui/memory/retrieval_tuning/profile")

    assert response.status_code == 200
    assert response.json()["profile"] == {"retrieval": {"top_k": 8}}
    assert response.json()["settings"] == {"profiles": ["default"]}
    assert calls == [("get_profile", {}), ("get_settings", {})]


def test_tuning_report_route_flattens_report_payload(client: TestClient, monkeypatch):
    async def fake_tuning_admin(*, action: str, **kwargs):
        assert action == "get_report"
        assert kwargs == {"task_id": "task-1", "format": "json"}
        return {
            "success": True,
            "report": {"format": "json", "content": "{\"ok\": true}", "path": "/tmp/report.json"},
        }

    monkeypatch.setattr(memory_router_module.memory_service, "tuning_admin", fake_tuning_admin)

    response = client.get("/api/webui/memory/retrieval_tuning/tasks/task-1/report", params={"format": "json"})

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "format": "json",
        "content": "{\"ok\": true}",
        "path": "/tmp/report.json",
        "error": "",
    }


def test_tuning_apply_best_defaults_to_runtime_only(client: TestClient, monkeypatch):
    calls = []

    async def fake_tuning_admin(*, action: str, **kwargs):
        calls.append((action, kwargs))
        return {"success": True, "applied": {"retrieval": {"top_k_final": 8}}, "runtime_rebuilt": True}

    monkeypatch.setattr(memory_router_module.memory_service, "tuning_admin", fake_tuning_admin)

    response = client.post("/api/webui/memory/retrieval_tuning/tasks/task-1/apply-best")

    assert response.status_code == 200
    assert response.json()["persisted"] is False
    assert calls == [("apply_best", {"task_id": "task-1", "validate": True})]


def test_tuning_apply_best_persists_when_requested(client: TestClient, monkeypatch):
    calls = []

    async def fake_tuning_admin(*, action: str, **kwargs):
        calls.append((action, kwargs))
        return {"success": True, "applied": {"retrieval": {"top_k_final": 8}}, "runtime_rebuilt": True}

    async def fake_runtime_admin(*, action: str, **kwargs):
        calls.append((action, kwargs))
        return {"success": True, "config": {"retrieval": {"top_k_final": 8}}}

    async def fake_update_config(config):
        calls.append(("update_config", config))
        return {"success": True, "path": "bot_config.toml"}

    monkeypatch.setattr(memory_router_module.memory_service, "tuning_admin", fake_tuning_admin)
    monkeypatch.setattr(memory_router_module.memory_service, "runtime_admin", fake_runtime_admin)
    monkeypatch.setattr(memory_router_module.a_memorix_host_service, "update_config", fake_update_config)

    response = client.post(
        "/api/webui/memory/retrieval_tuning/tasks/task-1/apply-best",
        json={"persist": True, "validate": False},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["persisted"] is True
    assert payload["persist_result"] == {"success": True, "path": "bot_config.toml"}
    assert calls == [
        ("apply_best", {"task_id": "task-1", "validate": False}),
        ("get_config", {}),
        ("update_config", {"retrieval": {"top_k_final": 8}}),
    ]


def test_tuning_apply_best_reports_persist_error(client: TestClient, monkeypatch):
    async def fake_tuning_admin(*, action: str, **kwargs):
        return {"success": True, "applied": {"retrieval": {"top_k_final": 8}}, "runtime_rebuilt": True}

    async def fake_runtime_admin(*, action: str, **kwargs):
        return {"success": True, "config": {"retrieval": {"top_k_final": 8}}}

    async def fake_update_config(config):
        raise OSError("write denied")

    monkeypatch.setattr(memory_router_module.memory_service, "tuning_admin", fake_tuning_admin)
    monkeypatch.setattr(memory_router_module.memory_service, "runtime_admin", fake_runtime_admin)
    monkeypatch.setattr(memory_router_module.a_memorix_host_service, "update_config", fake_update_config)

    response = client.post(
        "/api/webui/memory/retrieval_tuning/tasks/task-1/apply-best",
        json={"persist": True},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["persisted"] is False
    assert payload["persist_error"] == "persist_failed: write denied"


def test_delete_execute_route(client: TestClient, monkeypatch):
    async def fake_delete_admin(*, action: str, **kwargs):
        assert action == "execute"
        assert kwargs["mode"] == "source"
        assert kwargs["selector"] == {"source": "chat_summary:stream-1"}
        assert kwargs["reason"] == "cleanup"
        assert kwargs["requested_by"] == "tester"
        return {"success": True, "operation_id": "del-1"}

    monkeypatch.setattr(memory_router_module.memory_service, "delete_admin", fake_delete_admin)

    response = client.post(
        "/api/webui/memory/delete/execute",
        json={
            "mode": "source",
            "selector": {"source": "chat_summary:stream-1"},
            "reason": "cleanup",
            "requested_by": "tester",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"success": True, "operation_id": "del-1"}


def test_sources_route(client: TestClient, monkeypatch):
    async def fake_source_admin(*, action: str, **kwargs):
        assert action == "list"
        assert kwargs == {}
        return {"success": True, "items": [{"source": "demo", "paragraph_count": 2}], "count": 1}

    monkeypatch.setattr(memory_router_module.memory_service, "source_admin", fake_source_admin)

    response = client.get("/api/webui/memory/sources")

    assert response.status_code == 200
    assert response.json()["items"] == [{"source": "demo", "paragraph_count": 2}]


def test_delete_operation_routes(client: TestClient, monkeypatch):
    async def fake_delete_admin(*, action: str, **kwargs):
        if action == "list_operations":
            assert kwargs == {"limit": 5, "mode": "paragraph"}
            return {"success": True, "items": [{"operation_id": "del-1"}], "count": 1}
        if action == "get_operation":
            assert kwargs == {"operation_id": "del-1"}
            return {"success": True, "operation": {"operation_id": "del-1", "mode": "paragraph"}}
        raise AssertionError(action)

    monkeypatch.setattr(memory_router_module.memory_service, "delete_admin", fake_delete_admin)

    list_response = client.get("/api/webui/memory/delete/operations", params={"limit": 5, "mode": "paragraph"})
    get_response = client.get("/api/webui/memory/delete/operations/del-1")

    assert list_response.status_code == 200
    assert list_response.json()["count"] == 1
    assert get_response.status_code == 200
    assert get_response.json()["operation"]["operation_id"] == "del-1"


def test_memory_correction_routes(client: TestClient, monkeypatch):
    calls = []

    async def fake_memory_correction_admin(*, action: str, **kwargs):
        calls.append((action, kwargs))
        if action == "preview":
            assert kwargs == {
                "request_text": "把小明喜欢蓝色修正为喜欢绿色",
                "scope": "person_profile",
                "person_id": "person-1",
                "person_keyword": "",
                "chat_id": "",
                "limit": 5,
                "requested_by": "tester",
                "reason": "manual correction",
            }
            return {"success": True, "plan": {"plan_id": "corr-1", "status": "pending"}}
        if action == "execute":
            assert kwargs == {
                "plan_id": "corr-1",
                "confirmed": True,
                "requested_by": "tester",
                "reason": "confirmed",
            }
            return {"success": True, "plan": {"plan_id": "corr-1", "status": "executed"}}
        if action == "list":
            assert kwargs == {"limit": 7, "status": "pending", "scope": "person_profile"}
            return {"success": True, "items": [{"plan_id": "corr-1"}], "count": 1}
        if action == "get":
            assert kwargs == {"plan_id": "corr-1"}
            return {"success": True, "plan": {"plan_id": "corr-1"}}
        if action == "rollback":
            assert kwargs == {"plan_id": "corr-1", "requested_by": "tester", "reason": "undo"}
            return {"success": True, "rollback_result": {"restored": 1}}
        raise AssertionError(action)

    monkeypatch.setattr(memory_router_module.memory_service, "memory_correction_admin", fake_memory_correction_admin)

    preview_response = client.post(
        "/api/webui/memory/corrections/preview",
        json={
            "request_text": "把小明喜欢蓝色修正为喜欢绿色",
            "scope": "person_profile",
            "person_id": "person-1",
            "limit": 5,
            "requested_by": "tester",
            "reason": "manual correction",
        },
    )
    execute_response = client.post(
        "/api/webui/memory/corrections/execute",
        json={"plan_id": "corr-1", "confirmed": True, "requested_by": "tester", "reason": "confirmed"},
    )
    list_response = client.get(
        "/api/webui/memory/corrections/plans",
        params={"limit": 7, "status": "pending", "scope": "person_profile"},
    )
    get_response = client.get("/api/webui/memory/corrections/plans/corr-1")
    rollback_response = client.post(
        "/api/webui/memory/corrections/plans/corr-1/rollback",
        json={"requested_by": "tester", "reason": "undo"},
    )

    assert preview_response.status_code == 200
    assert preview_response.json()["plan"]["status"] == "pending"
    assert execute_response.status_code == 200
    assert execute_response.json()["plan"]["status"] == "executed"
    assert list_response.status_code == 200
    assert list_response.json()["count"] == 1
    assert get_response.status_code == 200
    assert get_response.json()["plan"]["plan_id"] == "corr-1"
    assert rollback_response.status_code == 200
    assert rollback_response.json()["rollback_result"]["restored"] == 1
    assert [action for action, _ in calls] == ["preview", "execute", "list", "get", "rollback"]


def test_memory_correction_preview_resolves_fuzzy_chat_id(client: TestClient, monkeypatch):
    chat_session = SimpleNamespace(
        session_id="session-1",
        platform="qq",
        group_id="10001",
        group_name="测试群",
        user_id=None,
        user_cardname=None,
        user_nickname=None,
        account_id="bot-1",
        scope="group",
        last_active_timestamp=None,
        created_timestamp=None,
    )
    message = SimpleNamespace(
        session_id="session-1",
        group_id="10001",
        group_name="测试群",
        user_id=None,
        user_cardname=None,
        user_nickname=None,
    )
    class _FakeExecResult:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

        def first(self):
            return None

    class _FakeSession:
        def __init__(self):
            self.exec_count = 0

        def exec(self, statement):
            self.exec_count += 1
            if self.exec_count == 1:
                return _FakeExecResult([chat_session])
            return _FakeExecResult([message])

    async def fake_memory_correction_admin(*, action: str, **kwargs):
        assert action == "preview"
        assert kwargs["chat_id"] == "session-1"
        return {"success": True, "plan": {"plan_id": "corr-1"}}

    monkeypatch.setattr(memory_router_module, "_find_real_chat_session", lambda chat_id: None)
    monkeypatch.setattr(memory_router_module._chat_manager, "get_session_name", lambda chat_id: "测试群")
    monkeypatch.setattr(memory_router_module, "get_db_session", lambda: _FakeDbContext(_FakeSession()))
    monkeypatch.setattr(memory_router_module.memory_service, "memory_correction_admin", fake_memory_correction_admin)

    response = client.post(
        "/api/webui/memory/corrections/preview",
        json={
            "request_text": "只检索测试群里的旧记忆",
            "scope": "memory",
            "chat_id": "测试群",
        },
    )

    assert response.status_code == 200
    assert response.json()["plan"]["plan_id"] == "corr-1"


def test_fuzzy_modify_routes_keep_memory_correction_compatibility(client: TestClient, monkeypatch):
    calls = []

    async def fake_memory_correction_admin(*, action: str, **kwargs):
        calls.append((action, kwargs))
        return {"success": True, "action": action}

    monkeypatch.setattr(memory_router_module.memory_service, "memory_correction_admin", fake_memory_correction_admin)

    response = client.post(
        "/api/webui/memory/fuzzy-modify/preview",
        json={"request_text": "旧接口兼容测试", "scope": "person_profile", "person_id": "person-1"},
    )
    execute_response = client.post(
        "/api/webui/memory/fuzzy-modify/execute",
        json={"plan_id": "corr-1", "confirmed": True},
    )
    list_response = client.get("/api/webui/memory/fuzzy-modify/plans", params={"limit": 3, "status": "pending"})
    get_response = client.get("/api/webui/memory/fuzzy-modify/plans/corr-1")
    rollback_response = client.post("/api/webui/memory/fuzzy-modify/plans/corr-1/rollback", json={})

    assert response.status_code == 200
    assert response.json()["action"] == "preview"
    assert execute_response.status_code == 200
    assert list_response.status_code == 200
    assert get_response.status_code == 200
    assert rollback_response.status_code == 200
    assert [action for action, _ in calls] == ["preview", "execute", "list", "get", "rollback"]
    assert calls[0][1]["request_text"] == "旧接口兼容测试"
    assert calls[1][1]["plan_id"] == "corr-1"
    assert calls[2][1]["limit"] == 3
    assert calls[3][1]["plan_id"] == "corr-1"
    assert calls[4][1]["plan_id"] == "corr-1"


def test_memory_correction_preview_allows_configured_default_limit(client: TestClient, monkeypatch):
    calls = []

    async def fake_memory_correction_admin(*, action: str, **kwargs):
        calls.append((action, kwargs))
        return {"success": True, "action": action}

    monkeypatch.setattr(memory_router_module.memory_service, "memory_correction_admin", fake_memory_correction_admin)

    response = client.post(
        "/api/webui/memory/corrections/preview",
        json={"request_text": "按配置默认候选上限", "scope": "person_profile", "person_id": "person-1"},
    )

    assert response.status_code == 200
    assert calls == [
        (
            "preview",
            {
                "request_text": "按配置默认候选上限",
                "scope": "person_profile",
                "person_id": "person-1",
                "person_keyword": "",
                "chat_id": "",
                "limit": None,
                "requested_by": "webui",
                "reason": "",
            },
        )
    ]


def test_feedback_correction_routes(client: TestClient, monkeypatch):
    async def fake_feedback_admin(*, action: str, **kwargs):
        if action == "list":
            assert kwargs == {
                "limit": 7,
                "statuses": ["applied"],
                "rollback_statuses": ["none"],
                "query": "green",
            }
            return {"success": True, "items": [{"task_id": 11, "query_text": "what color"}], "count": 1}
        if action == "get":
            assert kwargs == {"task_id": 11}
            return {"success": True, "task": {"task_id": 11, "query_text": "what color", "action_logs": []}}
        if action == "rollback":
            assert kwargs == {"task_id": 11, "requested_by": "tester", "reason": "manual revert"}
            return {"success": True, "result": {"restored_relation_hashes": ["rel-1"]}}
        raise AssertionError(action)

    monkeypatch.setattr(memory_router_module.memory_service, "feedback_admin", fake_feedback_admin)

    list_response = client.get(
        "/api/webui/memory/feedback-corrections",
        params={"limit": 7, "status": "applied", "rollback_status": "none", "query": "green"},
    )
    get_response = client.get("/api/webui/memory/feedback-corrections/11")
    rollback_response = client.post(
        "/api/webui/memory/feedback-corrections/11/rollback",
        json={"requested_by": "tester", "reason": "manual revert"},
    )

    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["task_id"] == 11
    assert get_response.status_code == 200
    assert get_response.json()["task"]["task_id"] == 11
    assert rollback_response.status_code == 200
    assert rollback_response.json()["result"]["restored_relation_hashes"] == ["rel-1"]
