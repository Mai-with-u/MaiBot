from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import asyncio
import sys

import pytest

from src.common.database.database_model import Jargon, JargonCreatedBy, JargonInferenceState, JargonMeaningHistory
from src.config.official_configs import JargonInferenceConfig
from src.webui.routers import jargon, jargon_inference


@pytest.fixture
def inference_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """挂载真实黑话路由，用隔离 SQLite 保留状态与历史，用替身记录任务操作。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(
        engine,
        tables=[
            Jargon.__table__,
            JargonInferenceState.__table__,
            JargonMeaningHistory.__table__,
        ],
    )

    @contextmanager
    def db_session() -> Iterator[Session]:
        """在请求线程与断言之间共享测试数据库，并为每次写操作提供事务边界。"""
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    config = SimpleNamespace(jargon=SimpleNamespace(inference=JargonInferenceConfig()))
    service = SimpleNamespace(
        preview=AsyncMock(return_value={"total": 1, "eligible": 1, "skipped": 0, "reasons": {}}),
        submit=AsyncMock(return_value={"id": "job-a", "status": "queued"}),
        list_jobs=AsyncMock(return_value={"items": [], "total": 0, "page": 1, "page_size": 50}),
        get_job=AsyncMock(return_value={"job": {"id": "job-a"}, "items": [], "total": 0}),
        cancel=AsyncMock(return_value={"id": "job-a", "status": "cancelled"}),
        retry=AsyncMock(return_value={"id": "job-b", "status": "queued"}),
        rescan=AsyncMock(return_value={"id": "job-c", "status": "queued"}),
    )

    async def local_loop(operation):
        return await operation

    monkeypatch.setattr(jargon, "get_db_session", db_session)
    monkeypatch.setattr(jargon_inference, "get_db_session", db_session)
    monkeypatch.setattr(jargon_inference, "global_config", config)
    monkeypatch.setattr(jargon_inference, "get_service", lambda: service)
    monkeypatch.setattr(jargon_inference, "run_on_main_loop", local_loop)
    monkeypatch.setattr(jargon_inference, "_settings_lock", asyncio.Lock())
    monkeypatch.setattr(jargon, "get_display_name_for_session_id", lambda *_: "测试群")
    monkeypatch.setattr(jargon, "build_session_display_name_cache", lambda *_, **__: {"chat-a": "测试群"})
    with db_session() as session:
        session.add(
            Jargon(
                id=1,
                content="测试黑话",
                meaning="新含义",
                is_jargon=True,
                count=7,
                last_inference_count=4,
                session_id_dict='{"chat-a":7}',
                evidence_messages='[[{"message_id":"a","platform":"qq"}]]',
            )
        )
        session.add(JargonInferenceState(jargon_id=1, status="succeeded", last_attempt_at=10, last_success_at=11))
        session.add(
            JargonMeaningHistory(
                id=1,
                jargon_id=1,
                source="manual",
                previous_meaning="旧含义",
                meaning="新含义",
                previous_is_jargon=False,
                is_jargon=True,
            )
        )
    app = FastAPI()
    app.include_router(jargon.router, prefix="/api/webui")
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, session=db_session, service=service, config=config)
    engine.dispose()


def test_static_inference_routes_precede_jargon_id(inference_api):
    response = inference_api.client.get("/api/webui/jargon/inference/settings")
    assert response.status_code == 200
    assert response.json()["thresholds"] == [4, 8, 25, 100]
    assert inference_api.client.get("/api/webui/jargon/inference/jobs").json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 50,
    }


def test_jobs_list_passes_source_pagination_and_activity_filter(inference_api):
    response = inference_api.client.get(
        "/api/webui/jargon/inference/jobs?page=2&page_size=10&source=manual&active_only=true"
    )
    assert response.status_code == 200
    inference_api.service.list_jobs.assert_awaited_once_with(page=2, page_size=10, source="manual", active_only=True)


@pytest.mark.parametrize("query", ["page=0", "page_size=101", "source=unknown"])
def test_jobs_list_rejects_invalid_scope(inference_api, query):
    assert inference_api.client.get(f"/api/webui/jargon/inference/jobs?{query}").status_code == 422
    inference_api.service.list_jobs.assert_not_called()


def test_preview_keeps_explicit_empty_session_scope(inference_api):
    response = inference_api.client.post(
        "/api/webui/jargon/inference/preview",
        json={
            "filters": {"session_ids": [], "search": "测试", "is_global": False},
            "mode": "missing",
            "force": False,
        },
    )
    assert response.status_code == 200
    inference_api.service.preview.assert_awaited_once_with(
        {
            "filters": {"session_ids": [], "search": "测试", "is_global": False},
            "mode": "missing",
            "force": False,
        }
    )


@pytest.mark.parametrize("payload", [{"ids": []}, {"ids": [-1]}, {"mode": "invalid"}, {"filters": {"unrecognized": 1}}])
def test_invalid_selections_never_submit(inference_api, payload):
    response = inference_api.client.post("/api/webui/jargon/inference/jobs", json=payload)
    assert response.status_code == 422
    inference_api.service.submit.assert_not_called()


def test_submit_selected_and_all_filtered(inference_api):
    response = inference_api.client.post("/api/webui/jargon/inference/jobs", json={"ids": [1], "mode": "all"})
    assert response.status_code == 200
    inference_api.service.submit.assert_awaited_once_with(
        {"ids": [1], "filters": {}, "mode": "all", "force": True},
        source="manual",
    )


@pytest.mark.parametrize(
    ("error", "status"),
    [(LookupError("missing"), 404), (RuntimeError("not ready"), 503), (ValueError("bad selection"), 400)],
)
def test_service_errors_keep_http_meaning(inference_api, error, status):
    inference_api.service.get_job.side_effect = error
    response = inference_api.client.get("/api/webui/jargon/inference/jobs/none")
    assert response.status_code == status
    assert response.json()["detail"] == str(error)


def test_job_controls_and_pagination(inference_api):
    client = inference_api.client
    assert client.get("/api/webui/jargon/inference/jobs/job-a?page=2&page_size=10").status_code == 200
    inference_api.service.get_job.assert_awaited_once_with("job-a", 2, 10)
    assert client.get("/api/webui/jargon/inference/jobs/job-a?page=0").status_code == 422
    assert client.post("/api/webui/jargon/inference/jobs/job-a/cancel").json()["status"] == "cancelled"
    assert client.post("/api/webui/jargon/inference/jobs/job-a/retry").json()["id"] == "job-b"
    assert client.post("/api/webui/jargon/inference/rescan").json()["id"] == "job-c"


def test_history_restore_protects_previous_state_without_changing_evidence_or_count(inference_api):
    """历史回退把词条标为人工并锁定，只改含义和分类，不改证据及计数。"""
    response = inference_api.client.post("/api/webui/jargon/inference/history/1/restore")
    assert response.status_code == 200
    with inference_api.session() as session:
        row = session.get(Jargon, 1)
        assert (row.meaning, row.is_jargon, row.created_by) == ("旧含义", False, JargonCreatedBy.MANUAL)
        assert row.count == 7 and row.last_inference_count == 4
        assert row.session_id_dict == '{"chat-a":7}'
        assert row.evidence_messages == '[[{"message_id":"a","platform":"qq"}]]'
        assert session.get(JargonInferenceState, 1).locked
        history = session.exec(select(JargonMeaningHistory).order_by(JargonMeaningHistory.id)).all()
        assert len(history) == 2
        assert (history[-1].source, history[-1].previous_meaning, history[-1].meaning) == (
            "restore",
            "新含义",
            "旧含义",
        )


def test_history_retention_and_missing_ids(inference_api):
    """回退生成的新历史占用保留名额，无效词条或历史 ID 返回 404。"""
    inference_api.config.jargon.inference.history_limit = 1
    client = inference_api.client
    assert client.post("/api/webui/jargon/inference/history/1/restore").status_code == 200
    items = client.get("/api/webui/jargon/inference/history/1").json()["items"]
    assert len(items) == 1 and items[0]["source"] == "restore"
    assert client.post("/api/webui/jargon/inference/history/999/restore").status_code == 404
    assert client.get("/api/webui/jargon/inference/history/999").status_code == 404
    assert client.post("/api/webui/jargon/inference/lock/999", json={"locked": True}).status_code == 404


def test_lock_and_metadata(inference_api):
    """列表和详情应同时读取最新的锁定状态及各自展示的推断时间。"""
    client = inference_api.client
    assert client.post("/api/webui/jargon/inference/lock/1", json={"locked": True}).status_code == 200
    detail = client.get("/api/webui/jargon/1").json()["data"]
    assert detail["inference_status"] == "succeeded"
    assert detail["inference_locked"] and detail["last_success_at"] == 11
    rows = client.get("/api/webui/jargon/list").json()["data"]
    assert rows[0]["inference_locked"] and rows[0]["last_attempt_at"] == 10
    assert client.post("/api/webui/jargon/inference/lock/1", json={"locked": False}).status_code == 200


def test_manual_edit_and_explicit_ai_opt_in(inference_api):
    client = inference_api.client
    assert client.patch("/api/webui/jargon/1", json={"meaning": "人工含义"}).status_code == 200
    with inference_api.session() as session:
        assert session.get(Jargon, 1).created_by == JargonCreatedBy.MANUAL
    assert client.patch("/api/webui/jargon/1", json={"created_by": "AI"}).status_code == 200
    with inference_api.session() as session:
        assert session.get(Jargon, 1).created_by == JargonCreatedBy.AI
    assert client.post("/api/webui/jargon/batch/set-jargon?ids=1&is_jargon=false").status_code == 200
    with inference_api.session() as session:
        assert session.get(Jargon, 1).created_by == JargonCreatedBy.MANUAL


def test_patch_returns_actual_locked_inference_state(inference_api):
    with inference_api.session() as session:
        state = session.get(JargonInferenceState, 1)
        state.locked = True
        state.reason = "已完成的含义推断"
        session.add(state)
    response = inference_api.client.patch("/api/webui/jargon/1", json={"meaning": "人工修订"})
    assert response.status_code == 200
    row = response.json()["data"]
    assert row["meaning"] == "人工修订"
    assert row["created_by"] == "MANUAL"
    assert row["inference_status"] == "succeeded"
    assert row["inference_reason"] == "已完成的含义推断"
    assert row["inference_locked"] is True
    assert (row["last_attempt_at"], row["last_success_at"]) == (10, 11)


@pytest.mark.parametrize("batch", [False, True])
def test_deletion_cleans_state_and_history(inference_api, batch):
    """单条与批量删除均须清除相应推断状态及历史。"""
    client = inference_api.client
    response = (
        client.post("/api/webui/jargon/batch/delete", json={"ids": [1]})
        if batch
        else client.delete("/api/webui/jargon/1")
    )
    assert response.status_code == 200
    with inference_api.session() as session:
        assert session.get(Jargon, 1) is None
        assert session.get(JargonInferenceState, 1) is None
        assert session.exec(select(JargonMeaningHistory)).all() == []


def test_overwrite_import_cleans_prior_inference_metadata(inference_api, monkeypatch):
    """覆盖导入创建人工词条，并清除旧词条留下的状态与历史。"""
    monkeypatch.setattr(jargon, "require_existing_session_ids", lambda session_ids: session_ids)
    response = inference_api.client.post(
        "/api/webui/jargon/import",
        json={
            "target_session_ids": ["chat-a"],
            "conflict_strategy": "overwrite",
            "jargons": [{"content": "测试黑话", "meaning": "导入的人工含义"}],
        },
    )
    assert response.status_code == 200
    with inference_api.session() as session:
        rows = session.exec(select(Jargon)).all()
        assert len(rows) == 1 and rows[0].meaning == "导入的人工含义"
        assert rows[0].created_by == JargonCreatedBy.MANUAL
        assert session.exec(select(JargonInferenceState)).all() == []
        assert session.exec(select(JargonMeaningHistory)).all() == []


def test_settings_save_uses_shared_writer_and_main_loop_reload(inference_api, monkeypatch):
    writes = []

    def save(section, data):
        writes.append((section, data))

    async def reload(**kwargs):
        assert kwargs == {"changed_scopes": ["bot"], "changed_sections": ["jargon"]}
        inference_api.config.jargon.inference = JargonInferenceConfig(**writes[-1][1]["inference"])
        return True

    monkeypatch.setitem(sys.modules, "src.webui.routers.config", SimpleNamespace(update_bot_config_section=save))
    monkeypatch.setattr(jargon_inference, "config_manager", SimpleNamespace(reload_config=reload))
    response = inference_api.client.put("/api/webui/jargon/inference/settings", json={"thresholds": [1, 3, 5]})
    assert response.status_code == 200
    assert response.json()["thresholds"] == [1, 3, 5]
    assert writes[0][0] == "jargon"
    assert not writes[0][1]["inference"]["scheduled_enabled"]
    invalid = inference_api.client.put("/api/webui/jargon/inference/settings", json={"thresholds": [3, 1]})
    assert invalid.status_code == 422
    assert len(writes) == 1


def test_settings_reload_failure_reports_saved_but_not_loaded(inference_api, monkeypatch):
    """配置写入成功而热重载失败时，接口需区分两步的实际结果。"""
    writes = []
    monkeypatch.setitem(
        sys.modules,
        "src.webui.routers.config",
        SimpleNamespace(
            update_bot_config_section=lambda *args: writes.append(args),
        ),
    )
    monkeypatch.setattr(
        jargon_inference, "config_manager", SimpleNamespace(reload_config=AsyncMock(return_value=False))
    )
    response = inference_api.client.put("/api/webui/jargon/inference/settings", json={"thresholds": [1, 4]})
    assert response.status_code == 500
    assert len(writes) == 1
    assert "已保存" in response.json()["detail"] and "热重载失败" in response.json()["detail"]


def test_stale_admin_settings_rejected_before_write(inference_api, monkeypatch):
    writes = []
    expected = inference_api.config.jargon.inference.model_dump()

    def save(section, data):
        writes.append(data["inference"])

    async def reload(**_kwargs):
        inference_api.config.jargon.inference = JargonInferenceConfig(**writes[-1])
        return True

    monkeypatch.setitem(sys.modules, "src.webui.routers.config", SimpleNamespace(update_bot_config_section=save))
    monkeypatch.setattr(jargon_inference, "config_manager", SimpleNamespace(reload_config=reload))
    client = inference_api.client
    first = client.put(
        "/api/webui/jargon/inference/settings",
        json={**expected, "thresholds": [1, 5], "expected_settings": expected},
    )
    assert first.status_code == 200
    assert "expected_settings" not in first.json()
    stale = client.put(
        "/api/webui/jargon/inference/settings",
        json={**expected, "scheduled_enabled": True, "expected_settings": expected},
    )
    assert stale.status_code == 409
    assert len(writes) == 1
    assert "expected_settings" not in writes[0]
    assert inference_api.config.jargon.inference.thresholds == [1, 5]
    assert not inference_api.config.jargon.inference.scheduled_enabled


@pytest.mark.parametrize("optimistic", [False, True])
def test_concurrent_settings_puts_serialize_save_and_reload(inference_api, monkeypatch, optimistic):
    """整个保存和热重载过程串行；携带旧快照的并发请求在写入前被拒绝。"""
    first_save_started, release_first_save, second_save_started = Event(), Event(), Event()
    saved = {}
    operations = []
    expected = {"expected_settings": inference_api.config.jargon.inference.model_dump()} if optimistic else {}

    def save(section, data):
        """阻塞第一次保存，验证第二次请求不会提前修改待重载数据。"""
        threshold = data["inference"]["thresholds"][0]
        operations.append(("save", threshold))
        if threshold == 1:
            first_save_started.set()
            assert release_first_save.wait(5)
        else:
            second_save_started.set()
        saved.update(data["inference"])

    async def reload(**kwargs):
        operations.append(("reload", saved["thresholds"][0]))
        inference_api.config.jargon.inference = JargonInferenceConfig(**saved)
        return True

    monkeypatch.setitem(sys.modules, "src.webui.routers.config", SimpleNamespace(update_bot_config_section=save))
    monkeypatch.setattr(jargon_inference, "config_manager", SimpleNamespace(reload_config=reload))
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            inference_api.client.put,
            "/api/webui/jargon/inference/settings",
            json={"thresholds": [1], **expected},
        )
        assert first_save_started.wait(5)
        second = executor.submit(
            inference_api.client.put,
            "/api/webui/jargon/inference/settings",
            json={"thresholds": [2], **expected},
        )
        try:
            assert not second_save_started.wait(0.1)
        finally:
            release_first_save.set()
        assert first.result(timeout=5).json()["thresholds"] == [1]
        response = second.result(timeout=5)
        if optimistic:
            assert response.status_code == 409
        else:
            assert response.json()["thresholds"] == [2]
    assert operations == (
        [("save", 1), ("reload", 1)]
        if optimistic
        else [
            ("save", 1),
            ("reload", 1),
            ("save", 2),
            ("reload", 2),
        ]
    )
