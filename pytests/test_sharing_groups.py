"""独立相通组：事务迁移、稳定身份、并发编辑与非传递共享。"""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID

from sqlmodel import Session, create_engine, select

import pytest

from src.common.database.sharing_models import (
    SharingGroup,
    SharingGroupCapability,
    SharingGroupMember,
    SharingGroupMigration,
    SharingGroupRule,
)
from src.services import sharing_group_service as service


@pytest.fixture
def store(monkeypatch, tmp_path):
    from src.chat.message_receive.chat_manager import chat_manager
    from src.config import config

    engine = create_engine(f"sqlite:///{tmp_path / 'sharing.db'}")
    for model in (SharingGroup, SharingGroupMember, SharingGroupCapability, SharingGroupRule, SharingGroupMigration):
        model.__table__.create(engine)

    @contextmanager
    def session_factory(auto_commit=True):
        with Session(engine, expire_on_commit=False) as session:
            yield session
            if auto_commit:
                session.commit()

    monkeypatch.setattr(service, "get_db_session", session_factory)
    monkeypatch.setattr(service, "_snapshot", None)
    monkeypatch.setattr(service, "_rule_ids", lambda rule: {"A", "B"} if rule["item_id"] == "*" else {rule["item_id"]})
    monkeypatch.setattr(chat_manager, "sessions", {})
    monkeypatch.setattr(
        chat_manager,
        "get_existing_session_by_session_id",
        lambda session_id: object() if session_id in {"A", "B", "C"} else None,
    )
    settings = SimpleNamespace(
        expression=SimpleNamespace(expression_groups=[]),
        jargon=SimpleNamespace(jargon_groups=[]),
        a_memorix=SimpleNamespace(shared_memory_groups=[], global_memory_sharing_enabled=False),
    )
    monkeypatch.setattr(config, "global_config", settings)
    return settings, engine


def group(name, members, capabilities=None):
    return service.GroupInput(
        name=name, color="#39845A", member_ids=members, capabilities=capabilities or {"expression": True}
    )


def test_identity_revision_and_non_transitive_scope(store):
    first = service.save_group(group("一组", ["A", "B"]))
    second = service.save_group(group("二组", ["B", "C"]))
    UUID(first["id"])
    assert service.resolve_scope("A", "expression") == ({"A", "B"}, False)
    assert service.resolve_scope("B", "expression") == ({"A", "B", "C"}, False)
    assert service.resolve_scope("C", "expression") == ({"B", "C"}, False)
    assert service.resolve_scope("A", "memory") == ({"A"}, False)
    updated = service.save_group(
        service.GroupInput(**{**first, "name": "改名", "note": "备注", "color": "#B97132"}), first["id"]
    )
    assert updated["id"] == first["id"] and updated["revision"] == 2
    with pytest.raises(ValueError, match="已被修改"):
        service.save_group(service.GroupInput(**first), first["id"])
    service.delete_group(second["id"], second["revision"])
    assert service.resolve_scope("C", "expression") == ({"C"}, False)
    updated["enabled"] = False
    service.save_group(service.GroupInput(**updated), updated["id"])
    assert service.resolve_scope("A", "expression") == ({"A"}, False)


def test_migration_once_and_no_resurrection(store):
    from src.config.official_configs import ChatStreamGroup, TargetItem

    settings, engine = store
    settings.expression.expression_groups = [
        ChatStreamGroup(targets=[TargetItem(platform="qq", item_id="A", rule_type="group")])
    ]
    settings.a_memorix.global_memory_sharing_enabled = True
    groups = service.list_groups()
    assert len(groups) == 2
    assert service.resolve_scope("C", "memory")[1]
    for item in groups:
        service.delete_group(item["id"], item["revision"])
    service._snapshot = None
    assert service.list_groups() == []
    with Session(engine) as session:
        assert session.get(SharingGroupMigration, "config-v1-complete") is not None
        assert len(session.exec(select(SharingGroupMigration)).all()) == 3


def test_migration_rolls_back_on_failure(store, monkeypatch):
    from src.config.official_configs import ChatStreamGroup, TargetItem

    settings, engine = store
    settings.expression.expression_groups = [ChatStreamGroup(targets=[TargetItem(platform="qq", item_id="A")])]
    original = service.uuid4
    calls = 0

    def broken_uuid():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("interrupted")
        return original()

    monkeypatch.setattr(service, "uuid4", broken_uuid)
    with pytest.raises(RuntimeError, match="interrupted"):
        service.initialize()
    with Session(engine) as session:
        assert session.exec(select(SharingGroup)).all() == []
        assert session.get(SharingGroupMigration, "config-v1-complete") is None
    monkeypatch.setattr(service, "uuid4", original)
    assert len(service.list_groups()) == 1


def test_capability_and_unknown_member_validation(store):
    with pytest.raises(ValueError, match="未知"):
        group("组", [], {"invented": True})
    with pytest.raises(ValueError, match="不存在"):
        service.save_group(group("组", ["missing"]))
    assert service.list_groups() == []
    item = service.save_group(group("统一成员", ["A", "B"], {"expression": True, "jargon": True, "memory": True}))
    for capability in service.CAPABILITIES:
        assert service.resolve_scope("A", capability) == ({"A", "B"}, False)
    item["capabilities"]["memory"] = False
    service.save_group(service.GroupInput(**item), item["id"])
    assert service.resolve_scope("A", "memory") == ({"A"}, False)


def test_api_revision_conflict_and_missing_group(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.webui.routers.sharing_groups import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.post("/sharing-groups", json=group("组", ["A"]).model_dump())
    assert response.status_code == 200
    created = response.json()
    url = f"/sharing-groups/{created['id']}"
    assert client.put(url, json={**created, "name": "改名"}).status_code == 200
    assert client.put(url, json=created).status_code == 409
    assert client.delete(url, params={"revision": 1}).status_code == 409
    assert client.delete(url, params={"revision": 2}).status_code == 200
    assert client.put(url, json=created).status_code == 404


def test_dynamic_rule_uses_new_real_sessions_without_transitive_expansion(store, monkeypatch):
    from src.chat.message_receive.chat_manager import chat_manager

    item = service.save_group(
        service.GroupInput(
            name="动态组",
            color="#39845A",
            rules=[service.MemberRule(platform="qq", item_id="*", rule_type="private")],
            capabilities={"memory": True},
        )
    )
    monkeypatch.setattr(
        chat_manager,
        "sessions",
        {"new": SimpleNamespace(session_id="new", platform="qq", is_group_session=False, user_id="123", group_id=None)},
    )
    assert "new" in service.resolve_scope("A", "memory")[0]
    assert not service.resolve_scope("A", "memory")[1]
    assert item["id"] in [group["id"] for group in service.list_groups()]


def test_database_migration_creates_all_sharing_tables():
    from sqlalchemy import inspect
    from src.common.database.migrations.v48_to_v49 import migrate_v48_to_v49

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        migrate_v48_to_v49(SimpleNamespace(connection=connection))
        migrate_v48_to_v49(SimpleNamespace(connection=connection))
    assert set(inspect(engine).get_table_names()) == {
        "sharing_groups",
        "sharing_group_members",
        "sharing_group_capabilities",
        "sharing_group_rules",
        "sharing_group_migrations",
    }
