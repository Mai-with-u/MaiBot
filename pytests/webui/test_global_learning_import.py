from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
import pytest

from src.common.database.database_model import ChatSession, Expression, JargonSnapshotMember, Jargon, JargonCreatedBy, JargonEvidenceLink, JargonLearningRecord, Messages, ModifiedBy
from src.common.database.sharing_models import SharingGroup, SharingGroupCapability, SharingGroupMember, SharingGroupRule
from src.webui.routers import expression, jargon


@pytest.fixture
def storage(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine, tables=[ChatSession.__table__, SharingGroup.__table__, SharingGroupCapability.__table__, SharingGroupMember.__table__, SharingGroupRule.__table__, Expression.__table__, Jargon.__table__, JargonSnapshotMember.__table__, JargonLearningRecord.__table__, JargonEvidenceLink.__table__])

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
        assert rows[1].is_global and rows[1].allow_update
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
    assert result.json()["data"]["allow_update"]
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

def test_jargon_verdict_filters_and_counts_match_after_legacy_migration(storage):
    from src.common.database.migrations.v56_to_v57 import migrate_v56_to_v57

    engine, client = storage
    with Session(engine) as session:
        session.add_all([
            Jargon(scope_id="real-chat", content="旧候选", meaning="", is_jargon=False, last_inference_count=5),
            Jargon(scope_id="real-chat", content="旧非黑话", meaning="", is_jargon=False, is_complete=True),
            Jargon(scope_id="real-chat", content="旧空释义", meaning="", is_jargon=True),
            Jargon(scope_id="real-chat", content="已确认", meaning="群内含义", is_jargon=True),
        ])
        session.commit()
    with engine.begin() as connection:
        migrate_v56_to_v57(SimpleNamespace(connection=connection))
    with Session(engine) as session:
        session.add(Jargon(scope_id="real-chat", content="新候选", meaning=""))
        session.add(Jargon(scope_id="real-chat", content="已判非黑话", meaning="", is_jargon=False, is_complete=False))
        session.commit()

    stats = client.get("/jargon/stats/summary").json()["data"]
    for status, verdict, expected in [
        ("pending", None, {"旧候选", "新候选", "旧空释义"}),
        ("confirmed_not_jargon", False, {"旧非黑话", "已判非黑话"}),
        ("confirmed_jargon", True, {"已确认"}),
    ]:
        page = client.get("/jargon/list", params={"jargon_status": status}).json()
        assert page["total"] == stats[status] == len(expected)
        assert {item["content"] for item in page["data"]} == expected
        assert all(item["is_jargon"] is verdict for item in page["data"])


@pytest.mark.parametrize(("action", "expected_meaning"), [
    ("replacements", "搞快点"),
    ("merges", "其他人刚编辑的释义\n\n搞快点"),
])
def test_jargon_import_review_updates_in_place_and_rolls_back_stale_decisions(storage, action, expected_meaning):
    engine, client = storage
    with Session(engine) as session:
        session.add(Jargon(scope_id="real-chat", content="gkd", meaning="原释义", is_global=True, count=37))
        session.commit()

    payload = jargon_payload(is_global=True)
    payload["jargons"].insert(0, {"content": "新词", "meaning": "新释义", "is_jargon": True})
    preview = client.post("/jargon/import/preview", json=payload)
    assert preview.status_code == 200, preview.text
    conflict, = preview.json()
    records = conflict["records"]
    assert conflict["content"] == "gkd"
    assert records[0]["meaning"] == "原释义"
    assert records[0]["is_global"]
    request = {**payload, action: {"gkd": records}}
    with Session(engine) as session:
        existing = session.exec(select(Jargon)).one()
        existing.meaning = "其他人刚编辑的释义"
        session.commit()
    assert client.post("/jargon/import", json=request).status_code == 409
    with Session(engine) as session:
        assert session.exec(select(Jargon)).one().meaning == "其他人刚编辑的释义"

    records[0]["meaning"] = "其他人刚编辑的释义"
    result = client.post("/jargon/import", json=request)
    assert result.status_code == 200, result.text
    assert result.json()["imported_count"] == 2
    with Session(engine) as session:
        row = session.get(Jargon, records[0]["id"])
        assert row.meaning == expected_meaning
        assert row.count == 37
        assert row.is_global and row.created_by == JargonCreatedBy.MANUAL

    skipped = client.post("/jargon/import", json={**jargon_payload(is_global=True), "replacements": {},
                                                  "reviewed_skips": ["gkd"]})
    assert skipped.json()["skipped_count"] == 1


def test_word_list_groups_versions_and_chat_prefers_universal_meaning(storage, monkeypatch):
    engine, client = storage
    monkeypatch.setattr(jargon, "jargon_group_ids", lambda chat: ["AB"] if chat == "B" else [])
    monkeypatch.setattr(jargon, "list_groups", lambda: [{"id": "AB", "name": "共享组"}])
    monkeypatch.setattr(jargon, "build_session_display_name_cache", lambda ids, *args, **kwargs: {i: i for i in ids})
    with Session(engine) as session:
        session.add_all([
            Jargon(content="Bot", meaning="全局", count=5, is_global=True, is_jargon=True),
            Jargon(content="bot", meaning="群内", count=20, scope_kind="group", scope_id="AB", is_jargon=True),
            Jargon(content="BOT", meaning="", scope_id="A", is_jargon=False),
        ])
        session.commit()
    result = client.get("/jargon/list", params={"grouped": True})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["total"] == 1 and len(data["data"][0]["scope_ids"]) == 3
    assert set(data["data"][0]["scope_names"]) == {"通用词库", "共享组", "A"}
    assert data["data"][0]["meaning_count"] == 2
    versions = data["data"][0]["scope_versions"]
    assert {(v["scope_name"], v["meaning"]) for v in versions} == {("通用词库", "全局"), ("共享组", "群内"), ("A", "")}
    assert {v["id"] for v in versions} == set(data["data"][0]["scope_ids"])
    compact = client.get("/jargon/list", params={"grouped": True, "compact": True}).json()
    assert compact["data"] == [{**data["data"][0], "scope_versions": []}]
    assert client.get("/jargon/stats/overview").json() == {"total": 1, "confirmed_jargon": 1, "complete_count": 0}
    with Session(engine) as session:
        session.add(Jargon(content="other", scope_id="C", meaning="另一词", count=10))
        session.commit()
    params = {"grouped": True, "sort_by": "count", "page_size": 1}
    first = client.get("/jargon/list", params=params).json()["data"][0]
    assert first["content"] == "bot" and first["count"] == 20
    assert first["scope_versions"][1]["count"] == 5
    assert client.get("/jargon/list", params={**params, "page": 2}).json()["data"][0]["content"] == "other"
    active = client.get("/jargon/list", params={"grouped": True, "session_id": "A", "jargon_status": "confirmed_jargon"})
    assert active.json()["data"][0]["meaning"] == "全局"
    active = client.get("/jargon/list", params={"grouped": True, "session_id": "B"})
    assert active.json()["data"][0]["scope_name"] == "通用词库"


def test_promotion_preserves_progress_and_rejects_scope_merges(storage, monkeypatch):
    engine, client = storage
    monkeypatch.setattr(jargon, "require_existing_session_id", lambda value: value)
    with Session(engine) as session:
        first = Jargon(content="词", meaning="甲", scope_kind="chat", scope_id="A", is_jargon=True,
                       count=14, last_inference_count=8, created_by=JargonCreatedBy.MANUAL)
        other = Jargon(content="词", meaning="乙", scope_kind="chat", scope_id="B", is_jargon=True)
        event = JargonLearningRecord(content="词", session_id="A", scope_kind="chat", scope_id="A", excerpt="素材")
        session.add_all([first, other, event])
        session.flush()
        first_id, other_id, event_id = first.id, other.id, event.id
        session.add(JargonEvidenceLink(jargon_id=first_id, record_id=event_id))
        session.commit()

    def change(action, kind, target, *ids):
        return client.post("/jargon/library/scope", json={
            "action": action, "kind": kind, "target": target, "inherit_id": ids[0], "versions": dict.fromkeys(ids, 0),
        })

    result = change("move", "global", "", first_id)
    assert result.status_code == 200, result.text
    common_id = result.json()["id"]
    for action in ("move", "split"):
        assert change(action, "chat", "C", common_id).status_code == 409
    assert change("merge", "chat", "B", other_id, common_id).status_code == 409
    assert change("merge", "global", "", common_id, other_id).status_code == 409
    assert change("move", "global", "", other_id).status_code == 409
    with Session(engine) as session:
        common = session.get(Jargon, common_id)
        assert common.scope_kind == "global" and common.is_global and common.allow_update and not common.archived
        assert (common.meaning, common.count, common.last_inference_count, common.created_by) == ("甲", 14, 8, "MANUAL")
        assert session.get(JargonEvidenceLink, (common_id, event_id))
        assert session.get(Jargon, first_id).archived
        assert not session.get(Jargon, other_id).archived


def test_imported_excerpt_attaches_to_the_reviewed_version(storage):
    engine, client = storage
    payload = jargon_payload(is_global=True)
    payload["jargons"][0]["excerpt"] = "导入的文段"
    assert client.post("/jargon/import", json=payload).json()["imported_count"] == 1
    with Session(engine) as session:
        row = session.exec(select(Jargon)).one()
        row.count, row.last_inference_count = 12, 8
        row_id = row.id
        session.commit()
    payload.update(conflict_strategy="overwrite")
    payload["jargons"][0].update(excerpt="另一段用法", meaning="新义")
    assert client.post("/jargon/import", json=payload).json()["imported_count"] == 1
    events = client.get(f"/jargon/library/evidence/{row_id}").json()
    assert [event["excerpt"] for event in events] == ["另一段用法", "导入的文段"]
    with Session(engine) as session:
        row = session.get(Jargon, row_id)
        assert (row.meaning, row.count, row.last_inference_count) == ("新义", 12, 8)


def test_historical_evidence_resolves_messages_in_order_within_source_chat(storage):
    engine, client = storage
    Messages.__table__.create(engine)
    with Session(engine) as session:
        word = Jargon(content="鱼", scope_kind="chat", scope_id="A", meaning="")
        refs = [{"platform": "qq", "message_id": key} for key in ("2", "missing", "1")]
        event = JargonLearningRecord(content="鱼", session_id="A", scope_kind="baseline", scope_id="1",
                                     evidence=json.dumps([refs]))
        session.add_all([word, event])
        session.flush()
        session.add(JargonEvidenceLink(jargon_id=word.id, record_id=event.id))
        for chat, key, text in [("A", "1", "第二句"), ("A", "2", "第一句"), ("B", "2", "其他群的消息")]:
            session.add(Messages(platform="qq", message_id=key, session_id=chat, timestamp=datetime(2026, 1, 1),
                                 user_id="1", user_nickname="小麦", raw_content=b"", processed_plain_text=text))
        word_id = word.id
        session.commit()

    event, = client.get(f"/jargon/library/evidence/{word_id}").json()
    passage, = event["passages"]
    assert [(m["sender"], m["text"]) for m in passage] == [("小麦", "第一句"), ("小麦", "第二句")]
    assert event["unavailable"] == 1


def test_multi_chat_import_contributes_excerpt_once_to_common_term(storage, monkeypatch):
    engine, client = storage
    monkeypatch.setattr(jargon, "require_existing_session_ids", lambda ids: ids)
    common = client.post("/jargon/", json={"content": "gkd", "meaning": "通用释义", "is_global": True}).json()["data"]
    payload = jargon_payload(target_session_ids=["A", "B"])
    payload["jargons"][0]["excerpt"] = "一段共同的导入素材"
    assert client.post("/jargon/import", json=payload).json()["imported_count"] == 1
    assert client.post("/jargon/import", json=payload).json()["skipped_count"] == 1
    with Session(engine) as session:
        saved = session.get(Jargon, common["id"])
        assert saved.count == common["count"] + 1 and saved.meaning == "通用释义"
        event = session.exec(select(JargonLearningRecord)).one()
        assert event.session_id is None
        assert len(session.exec(select(JargonEvidenceLink).where(JargonEvidenceLink.record_id == event.id)).all()) == 3


def test_edit_all_current_scopes_is_atomic_and_preserves_unedited_fields(storage):
    engine, client = storage
    with Session(engine) as session:
        entries = [
            Jargon(content="Bot", scope_kind="chat", scope_id="A", meaning="群内义", is_jargon=True),
            Jargon(content="bot", scope_kind="global", is_global=True, meaning="通用义", is_jargon=True),
            Jargon(content="BOT", scope_kind="chat", scope_id="B", meaning="旧义", archived=True),
            Jargon(content="other", scope_kind="global", is_global=True, meaning="另一词"),
        ]
        session.add_all(entries)
        session.flush()
        local, common, historical, other = [r.id for r in entries]
        session.commit()

    url = f"/jargon/{local}"
    versions = {local: 0, common: 0}
    assert client.patch(url, json={"content": "新词"}).status_code == 409
    assert client.patch(url, json={"all_scope_revisions": versions, "content": "新词"}).status_code == 409
    assert client.patch(url, json={"content": "Bot"}).status_code == 200
    assert client.patch(url, json={"all_scope_revisions": {local: 0}, "meaning": "新义"}).status_code == 409
    result = client.patch(url, json={"all_scope_revisions": versions, "is_jargon": False, "allow_update": False})
    assert result.status_code == 200, result.text
    assert client.patch(url, json={"all_scope_revisions": versions, "meaning": "覆盖"}).status_code == 409
    with Session(engine) as session:
        for ident, meaning in [(local, "群内义"), (common, "通用义")]:
            row = session.get(Jargon, ident)
            assert row.revision == 1 and row.meaning == meaning and row.is_jargon is False and not row.allow_update
        assert session.get(Jargon, historical).revision == session.get(Jargon, other).revision == 0


def test_group_members_share_manual_creation_and_import_conflicts(storage, monkeypatch):
    engine, client = storage
    monkeypatch.setattr(jargon, "require_existing_session_ids", lambda ids: ids)
    with Session(engine) as session:
        session.add(SharingGroup(id="AB", name="AB 组", color="", created_at=0, updated_at=0))
        session.add(SharingGroupCapability(group_id="AB", capability="jargon"))
        for chat in ("A", "B"):
            session.add(SharingGroupMember(group_id="AB", session_id=chat, created_at=0))
        session.commit()

    payload = jargon_payload(target_session_ids=["A", "B"])
    word = payload["jargons"][0]["content"]
    result = client.post("/jargon/", json={"content": word, "meaning": "原释义", "session_ids": ["A", "B"]})
    assert result.status_code == 200, result.text
    assert result.json()["data"]["scope_id"] == "AB"
    conflicts = client.post("/jargon/import/preview", json=payload).json()
    record, = conflicts[0]["records"]
    assert record["group_name"] == "AB 组"
    assert client.post("/jargon/import", json={**payload, "replacements": {word: [record]}}).status_code == 200
    with Session(engine) as session:
        only = session.exec(select(Jargon)).one()
        assert (only.scope_kind, only.scope_id, only.meaning) == ("group", "AB", payload["jargons"][0]["meaning"])
