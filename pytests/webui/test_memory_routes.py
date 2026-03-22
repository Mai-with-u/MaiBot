from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.services.memory_service import MemorySearchResult
from src.webui.dependencies import require_auth
from src.webui.routers import memory as memory_router_module
from src.webui.routers.memory import compat_router, router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.dependency_overrides[require_auth] = lambda: "ok"
    app.include_router(router)
    app.include_router(compat_router)
    return TestClient(app)


def test_webui_memory_graph_route(client: TestClient, monkeypatch):
    async def fake_graph_admin(*, action: str, **kwargs):
        assert action == "get_graph"
        return {"success": True, "nodes": [], "edges": [], "total_nodes": 0, "limit": kwargs.get("limit")}

    monkeypatch.setattr(memory_router_module.memory_service, "graph_admin", fake_graph_admin)

    response = client.get("/api/webui/memory/graph", params={"limit": 77})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["limit"] == 77


def test_compat_aggregate_route(client: TestClient, monkeypatch):
    async def fake_search(query: str, **kwargs):
        assert kwargs["mode"] == "aggregate"
        assert kwargs["respect_filter"] is False
        return MemorySearchResult(summary=f"summary:{query}", hits=[])

    monkeypatch.setattr(memory_router_module.memory_service, "search", fake_search)

    response = client.get("/api/query/aggregate", params={"query": "mai"})

    assert response.status_code == 200
    assert response.json() == {"success": True, "summary": "summary:mai", "hits": [], "filtered": False}


def test_auto_save_routes(client: TestClient, monkeypatch):
    async def fake_runtime_admin(*, action: str, **kwargs):
        if action == "get_config":
            return {"success": True, "auto_save": True}
        if action == "set_auto_save":
            return {"success": True, "auto_save": kwargs["enabled"]}
        raise AssertionError(action)

    monkeypatch.setattr(memory_router_module.memory_service, "runtime_admin", fake_runtime_admin)

    get_response = client.get("/api/config/auto_save")
    post_response = client.post("/api/config/auto_save", json={"enabled": False})

    assert get_response.status_code == 200
    assert get_response.json() == {"success": True, "auto_save": True}
    assert post_response.status_code == 200
    assert post_response.json() == {"success": True, "auto_save": False}


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

    async def fake_import_admin(*, action: str, **kwargs):
        assert action == "create_upload"
        staged_files = kwargs["staged_files"]
        assert len(staged_files) == 1
        assert staged_files[0]["filename"] == "demo.txt"
        assert memory_router_module.Path(staged_files[0]["staged_path"]).exists()
        return {"success": True, "task_id": "task-1"}

    monkeypatch.setattr(memory_router_module.memory_service, "import_admin", fake_import_admin)

    response = client.post(
        "/api/import/upload",
        data={"payload_json": "{\"source\": \"upload\"}"},
        files=[("files", ("demo.txt", b"hello world", "text/plain"))],
    )

    assert response.status_code == 200
    assert response.json() == {"success": True, "task_id": "task-1"}
    assert list(tmp_path.iterdir()) == []


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
