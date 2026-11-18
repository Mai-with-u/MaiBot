from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
import pytest

from src.common.database.database_model import Expression, Jargon, JargonCreatedBy, ModifiedBy
from src.webui.routers import expression, jargon


@pytest.fixture
def storage(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine, tables=[Expression.__table__, Jargon.__table__])

    @contextmanager
    def db_session(*_args, **_kwargs):
        with Session(engine) as session:
            yield session
            session.commit()

    monkeypatch.setattr(expression, "get_db_session", db_session)
    monkeypatch.setattr(jargon, "get_db_session", db_session)
    monkeypatch.setattr(expression, "get_visible_expression_chat_ids", lambda *_args, **_kwargs: {"real-chat"})
    monkeypatch.setattr(expression._chat_manager, "get_existing_session_by_session_id",
                        lambda value: SimpleNamespace(session_id=value) if value == "real-chat" else None)
    monkeypatch.setattr(expression, "get_chat_name", lambda *_args: "测试群")
    monkeypatch.setattr(jargon, "get_display_name_for_session_id", lambda *_args: "测试群")
    app = FastAPI()
    app.include_router(expression.router)
    app.include_router(jargon.router)
    with TestClient(app) as client:
        yield engine, client
    engine.dispose()


def test_global_expression_is_one_null_scoped_record_visible_in_all_and_stats(storage):
    engine, client = storage
    payload = {"is_global": True, "expressions": [{"situation": "赞同", "style": "确实", "checked": True, "modified_by": "user"}]}
    assert client.post("/expression/import", json=payload).json()["imported_count"] == 1
    assert client.post("/expression/import", json=payload).json()["skipped_count"] == 1
    with Session(engine) as session:
        rows = session.exec(select(Expression)).all()
        assert len(rows) == 1
        assert rows[0].session_id is None
        assert rows[0].content_list == "[]"
    response = client.get("/expression/list").json()
    assert response["total"] == 1
    assert response["data"][0]["chat_name"] == "全局（所有聊天）"
    assert client.get("/expression/stats/summary").json()["data"]["total"] == 1
    assert client.get("/expression/list", params={"chat_id": "real-chat"}).json()["total"] == 0


def test_global_expressions_are_visible_without_any_existing_chat(storage, monkeypatch):
    _, client = storage
    monkeypatch.setattr(expression, "get_visible_expression_chat_ids", lambda *_args, **_kwargs: set())
    client.post("/expression/import", json={"is_global": True, "expressions": [{"situation": "情境", "style": "表达"}]})
    assert client.get("/expression/list").json()["total"] == 1


def jargon_payload(**scope):
    return {"jargons": [{"content": "gkd", "meaning": "搞快点", "is_jargon": True}], **scope}


def test_global_jargon_is_not_blocked_by_local_term_and_does_not_overwrite_it(storage):
    engine, client = storage
    with Session(engine) as session:
        session.add(Jargon(content="gkd", meaning="局部含义", session_id_dict='{"real-chat":1}',
                           is_global=False, created_by=JargonCreatedBy.MANUAL))
        session.commit()
    payload = jargon_payload(is_global=True)
    assert client.post("/jargon/import", json=payload).json()["imported_count"] == 1
    assert client.post("/jargon/import", json=payload).json()["skipped_count"] == 1
    with Session(engine) as session:
        rows = session.exec(select(Jargon).order_by(Jargon.id)).all()
        assert len(rows) == 2
        assert rows[0].meaning == "局部含义"
        assert rows[1].is_global
        assert rows[1].session_id_dict == "{}"


@pytest.mark.parametrize("resource", ["expression", "jargon"])
def test_import_rejects_missing_or_mixed_targets_and_unknown_sessions(storage, resource):
    _, client = storage
    payload = {"expressions": [{"situation": "情境", "style": "表达"}]} if resource == "expression" else jargon_payload()
    field = "chat_id" if resource == "expression" else "target_session_ids"
    assert client.post(f"/{resource}/import", json=payload).status_code == 400
    for chat in ("real-chat", "unknown-chat"):
        target = chat if resource == "expression" else [chat]
        assert client.post(f"/{resource}/import", json={**payload, field: target, "is_global": True}).status_code == 400
    target = "unknown-chat" if resource == "expression" else ["unknown-chat"]
    assert client.post(f"/{resource}/import", json={**payload, field: target}).status_code == 400


def test_local_import_still_uses_existing_chat_scope(storage):
    engine, client = storage
    result = client.post("/expression/import", json={"chat_id": "real-chat", "expressions": [{"situation": "情境", "style": "表达"}]})
    assert result.json()["imported_count"] == 1
    assert client.post("/jargon/import", json=jargon_payload(target_session_ids=["real-chat"])).json()["imported_count"] == 1
    with Session(engine) as session:
        assert session.exec(select(Expression)).one().session_id == "real-chat"
        row = session.exec(select(Jargon)).one()
        assert not row.is_global
        assert "real-chat" in row.session_id_dict


def test_manual_global_expression_needs_no_chat_and_is_available_when_only_checked(storage):
    engine, client = storage
    result = client.post("/expression/", json={"situation": "手工情境", "style": "手工表达", "is_global": True})
    assert result.status_code == 200, result.text
    assert result.json()["data"]["chat_name"] == "全局（所有聊天）"
    with Session(engine) as session:
        row = session.exec(select(Expression)).one()
        assert row.session_id is None
        assert row.checked
        assert row.modified_by == ModifiedBy.USER
    assert client.get("/expression/list").json()["total"] == 1


def test_manual_global_jargon_needs_no_chat_and_preserves_same_local_term(storage):
    engine, client = storage
    with Session(engine) as session:
        session.add(Jargon(content="gkd", meaning="局部含义", session_id_dict='{"real-chat":1}',
                           is_global=False, created_by=JargonCreatedBy.MANUAL))
        session.commit()
    payload = {"content": "gkd", "meaning": "搞快点", "is_global": True, "session_ids": []}
    result = client.post("/jargon/", json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["data"]["session_ids"] == []
    assert client.post("/jargon/", json=payload).status_code == 400
    with Session(engine) as session:
        rows = session.exec(select(Jargon).order_by(Jargon.id)).all()
        assert len(rows) == 2
        assert rows[0].meaning == "局部含义"
        assert rows[1].is_global
        assert rows[1].session_id_dict == "{}"


@pytest.mark.parametrize("resource", ["expression", "jargon"])
def test_manual_local_create_still_validates_existing_chat(storage, resource):
    engine, client = storage
    payload = {"situation": "手工情境", "style": "手工表达"} if resource == "expression" else {
        "content": "gkd", "meaning": "搞快点",
    }
    field = "chat_id" if resource == "expression" else "session_ids"
    assert client.post(f"/{resource}/", json=payload).status_code == 400
    unknown = "unknown-chat" if resource == "expression" else ["unknown-chat"]
    assert client.post(f"/{resource}/", json={**payload, field: unknown}).status_code == 400
    existing = "real-chat" if resource == "expression" else ["real-chat"]
    result = client.post(f"/{resource}/", json={**payload, field: existing})
    assert result.status_code == 200, result.text
    with Session(engine) as session:
        if resource == "expression":
            assert session.exec(select(Expression)).one().session_id == "real-chat"
        else:
            row = session.exec(select(Jargon)).one()
            assert not row.is_global
            assert "real-chat" in row.session_id_dict
