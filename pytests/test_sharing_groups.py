"""独立相通组：事务迁移、稳定身份、并发编辑与非传递共享。"""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID

from sqlmodel import Session, col, create_engine, select

import pytest

from src.common.database.database_model import ChatSession, Jargon, JargonDeferredLearning, JargonEvidenceLink, JargonLearningRecord, JargonSnapshotMember, Messages
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
    for model in (ChatSession, Messages, Jargon, JargonDeferredLearning, JargonEvidenceLink, JargonLearningRecord, JargonSnapshotMember, SharingGroup, SharingGroupMember, SharingGroupCapability, SharingGroupRule, SharingGroupMigration):
        model.__table__.create(engine)

    @contextmanager
    def session_factory(auto_commit=True):
        with Session(engine, expire_on_commit=False, autoflush=False) as session:
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


def test_jargon_groups_are_exclusive_including_future_chats(store):
    service.save_group(group("AB", ["A", "B"], {"jargon": True}))
    with pytest.raises(ValueError, match="AB"):
        service.save_group(group("BC", ["B", "C"], {"jargon": True}))
    wildcard = service.GroupInput(name="全聊天", color="#39845A", capabilities={"jargon": True},
                                  rules=[service.MemberRule(platform="*", item_id="*", rule_type="all")])
    with pytest.raises(ValueError, match="覆盖所有聊天"):
        service.save_group(wildcard)
    service.save_group(group("表达仍可重叠", ["B", "C"]))


def test_jargon_learning_merge_split_and_departure_preserve_progress(store, monkeypatch):
    from src.maisaka import jargon_context_matcher
    from src.services import jargon_scope_service as scopes
    from src.webui.routers import jargon as api

    _, engine = store
    monkeypatch.setattr(scopes, "get_db_session", service.get_db_session)
    monkeypatch.setattr(api, "get_db_session", service.get_db_session)
    monkeypatch.setattr(api, "list_groups", service.list_groups)
    monkeypatch.setattr(jargon_context_matcher, "get_db_session", service.get_db_session)
    monkeypatch.setattr(api, "require_existing_session_id", lambda value: value)
    with Session(engine) as session:
        for chat in ["A", "B", "C"]:
            session.add(ChatSession(session_id=chat, platform="qq", group_id=chat))
        session.commit()
    a, _, _ = scopes.collect_learning("词", "A", [], {"来自 A"})
    b, _, _ = scopes.collect_learning("词", "B", [], {"来自 B"})
    with Session(engine) as session:
        row = session.get(Jargon, a.id)
        row.meaning, row.count, row.last_inference_count, row.is_jargon = "甲", 12, 8, True
        row = session.get(Jargon, b.id)
        row.meaning, row.count, row.last_inference_count, row.is_jargon = "乙", 7, 4, True
        session.add(Jargon(content="词", meaning="全局释义", is_global=True, is_jargon=True))
        session.commit()
    ab = service.save_group(group("AB", ["A", "B"], {"jargon": True}))
    with Session(engine) as session:
        shared = scopes.scope_row(session, "词", "group", ab["id"])
        assert (shared.meaning, shared.count, shared.last_inference_count) == ("甲", 12, 8)
        assert scopes.scope_row(session, "词", "chat", "B") is None
        assert scopes.scope_row(session, "词", "chat", "A") is None
        common = scopes.scope_row(session, "词", "global", "").id
        assert session.exec(scopes.effective_scope_ids("C", [])).one() == common
        merged = shared.id
        versions = {shared.id: shared.revision}

    with pytest.raises(api.HTTPException) as invalid:
        api.change_jargon_scope(api.JargonScopeChange(action="split", kind="chat", target="B",
                                                      versions=versions, inherit_id=merged))
    assert invalid.value.status_code == 409
    assert set(scopes.learning_material(merged)[0]) == {"来自 A", "来自 B"}
    row, created, _ = scopes.collect_learning("词", "A", [], {"合并后"})
    assert not created and (row.id, row.count, row.last_inference_count, row.meaning) == (merged, 13, 8, "甲")
    with Session(engine) as session:
        saved = session.get(Jargon, merged)
        saved.created_by, saved.allow_update = "MANUAL", False
        session.commit()
    ab["member_ids"] = ["A"]
    service.save_group(service.GroupInput(**ab), ab["id"])
    departed, created, _ = scopes.collect_learning("词", "B", [], {"离组后"})
    assert not created and departed.scope_id == "B"
    assert (departed.meaning, departed.count, departed.last_inference_count, departed.created_by) == ("甲", 14, 8, "MANUAL")
    for chat in ("A", "B"):
        candidate, = jargon_context_matcher._load_scoped_jargon_candidates(chat)
        assert candidate.meaning == "全局释义"
    assert set(scopes.learning_material(departed.id)[0]) == {"来自 A", "来自 B", "合并后", "离组后"}
    assert set(scopes.learning_material(merged)[0]) == {"来自 A", "来自 B", "合并后"}
    with Session(engine) as session:
        assert session.get(Jargon, a.id).meaning == "甲"
        assert session.get(Jargon, b.id).meaning == "乙"
        assert session.exec(scopes.effective_scope_ids("A", [ab["id"]])).one() == common

    from src.webui.routers.chat.routes import _delete_or_unlink_jargons

    with Session(engine) as session:
        result = _delete_or_unlink_jargons(session, "B")
        session.commit()
        assert result["unlinked"] == 2
    assert set(scopes.learning_material(merged)[0]) == {"来自 A", "合并后"}
    with Session(engine) as session:
        assert session.get(Jargon, merged).last_inference_count == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("remove_group", [False, True])
async def test_conflicting_chat_keeps_evidence_until_its_owner_is_known(store, monkeypatch, remove_group):
    from src.common.utils.utils_config import JargonConfigUtils
    from src.learners import jargon_miner
    from src.services import jargon_scope_service as scopes

    _, engine = store
    monkeypatch.setattr(scopes, "get_db_session", service.get_db_session)
    monkeypatch.setattr(jargon_miner, "get_db_session", service.get_db_session)
    monkeypatch.setattr(JargonConfigUtils, "get_jargon_config_for_chat", lambda _: (True, True))
    ab = service.save_group(group("AB", ["A", "B"], {"jargon": True}))
    bc = service.save_group(group("BC", ["C"], {"jargon": True}))
    with Session(engine) as session:
        session.add(SharingGroupMember(group_id=bc["id"], session_id="B", created_at=0))
        for gid, meaning in [(ab["id"], "甲"), (bc["id"], "乙")]:
            session.add(Jargon(content="词", meaning=meaning, scope_kind="group", scope_id=gid,
                               count=6, last_inference_count=4, is_jargon=True))
        session.add(Jargon(content="锁定", meaning="本群定义", scope_kind="chat", scope_id="B", created_by="MANUAL", allow_update=False,
                           count=3, is_jargon=True))
        session.commit()

    # 到下一门槛还差很多次的词，暂存素材最终会落到“最早/最近”采样窗口之间。
    for index in range(12):
        early, _, _ = scopes.collect_learning("长线", "A", [], {f"早期{index}"})
    with Session(engine) as session:
        row = session.get(Jargon, early.id)
        row.count, row.last_inference_count = 26, 25
        session.commit()
    for index, word in enumerate(["词", "词", "锁定", "新词", "长线"]):
        row, _, _ = scopes.collect_learning(word, "B", [[{"platform": "qq", "message_id": str(index)}]], {str(index)})
        assert row is None
    assert scopes.deferred_chats() == ["B"]
    assert not scopes.prepare_deferred_inference("B")
    with Session(engine) as session:
        records = session.exec(select(JargonLearningRecord).where(JargonLearningRecord.session_id == "B")).all()
        assert len(records) == 5
        assert not session.exec(select(JargonEvidenceLink).where(col(JargonEvidenceLink.record_id).in_([r.id for r in records]))).all()
        assert scopes.scope_row(session, "词", "group", ab["id"]).count == 6
        assert scopes.scope_row(session, "词", "group", bc["id"]).count == 6

    if remove_group:
        service.delete_group(bc["id"], bc["revision"])
    else:
        service.save_group(service.GroupInput(**bc), bc["id"])
    service.refresh()
    expected = scopes.prepare_deferred_inference("B")
    assert len(expected) == 1 and scopes.prepare_deferred_inference("B") == expected
    with Session(engine) as session:
        shared = scopes.scope_row(session, "词", "group", ab["id"])
        assert shared.id in expected and shared.count == 8 and shared.last_inference_count == 4 and shared.meaning == "甲"
        assert scopes.scope_row(session, "词", "chat", "B") is None
        assert scopes.scope_row(session, "新词", "group", ab["id"]).count == 1
        assert scopes.scope_row(session, "锁定", "chat", "B") is None
        assert scopes.scope_row(session, "锁定", "group", ab["id"]).meaning == "本群定义"
        assert scopes.scope_row(session, "锁定", "group", ab["id"]).count == 4

    received = []
    async def infer(miner, item):
        received.append((miner.session_id, item.meaning, sorted(scopes.learning_material(item.item_id)[0])))
        item.last_inference_count = item.count
        miner._modify_jargon_entry(item)
    monkeypatch.setattr(jargon_miner.JargonMiner, "infer_meaning", infer)
    await jargon_miner.replay_deferred_learning()
    await jargon_miner.replay_deferred_learning()
    assert received == [("B", "甲", ["0", "1"])]
    assert not scopes.deferred_chats()
    with Session(engine) as session:
        assert len(session.exec(select(JargonEvidenceLink)).all()) == 17
        assert len(session.exec(select(JargonLearningRecord)).all()) == 17
        assert scopes.scope_row(session, "词", "group", ab["id"]).last_inference_count == 8

    for index in range(73):
        later, _, _ = scopes.collect_learning("长线", "A", [], {f"后续{index}"})
    assert later.count == 100
    await jargon_miner.JargonMiner("A", "群 A")._infer_meaning_by_id(later.id)
    assert "4" in received[-1][2]
    assert "早期10" not in received[-1][2]

def test_common_word_shares_each_record_once_even_during_group_conflict(store, monkeypatch):
    from src.services import jargon_scope_service as scopes

    _, engine = store
    monkeypatch.setattr(scopes, "get_db_session", service.get_db_session)
    ab = service.save_group(group("AB", ["A", "B"], {"jargon": True}))
    other = service.save_group(group("C", ["C"], {"jargon": True}))
    with Session(engine) as session:
        session.add(SharingGroupMember(group_id=other["id"], session_id="B", created_at=0))
        session.add(Jargon(content="Bot", meaning="通用释义", scope_kind="global", is_global=True,
                           created_by="MANUAL", count=197, last_inference_count=100, allow_update=False))
        session.commit()

    local, _, common = scopes.collect_learning("bot", "D", [], {"独立聊天"})
    shared, _, _ = scopes.collect_learning("BOT", "A", [], {"共享组"})
    pending, _, common = scopes.collect_learning("Bot", "B", [], {"冲突聊天"})
    assert (local.scope_id, shared.scope_id, pending) == ("D", ab["id"], None)
    assert common.count == 200 and not scopes.should_infer_meaning(common)
    assert set(scopes.learning_material(common.id)[0]) == {"独立聊天", "共享组", "冲突聊天"}
    service.save_group(service.GroupInput(**other), other["id"])
    with Session(engine) as session:
        scopes.assign_deferred_learning(session, {"B"})
        assert scopes.scope_row(session, "Bot", "group", ab["id"]).count == 2
        current = session.get(Jargon, common.id)
        assert current.count == 200
        current.allow_update = True
        assert scopes.should_infer_meaning(current)
        assert len(session.exec(select(JargonLearningRecord)).all()) == 3
        assert len(session.exec(select(JargonEvidenceLink)).all()) == 6


def test_expanding_to_global_absorbs_member_and_unregistered_chat_history(store, monkeypatch):
    from src.services import jargon_scope_service as scopes

    _, engine = store
    monkeypatch.setattr(scopes, "get_db_session", service.get_db_session)
    ab = service.save_group(group("AB", ["A", "B"], {"jargon": True}))
    shared, _, _ = scopes.collect_learning("词", "A", [], {"A 的线索"})
    local, _, _ = scopes.collect_learning("词", "C", [], {"C 的线索"})
    orphan, _, _ = scopes.collect_learning("词", "旧来源", [], {"旧来源线索"})
    with Session(engine) as session:
        winner = session.get(Jargon, local.id)
        winner.allow_update = False
        winner.created_by, winner.meaning, winner.is_jargon = "MANUAL", "C 定义", True
        winner.count, winner.last_inference_count = 12, 8
        session.commit()

    ab["member_ids"] = ["A", "B", "C"]
    abc = service.save_group(service.GroupInput(**ab), ab["id"])
    with Session(engine) as session:
        only = scopes.scope_row(session, "词", "group", ab["id"])
        assert (only.meaning, only.created_by, only.count, only.last_inference_count) == ("C 定义", "MANUAL", 12, 8)
        assert all(session.get(Jargon, row_id).archived for row_id in (shared.id, local.id))
        group_id = only.id
    abc["rules"] = [{"platform": "*", "item_id": "*", "rule_type": "all"}]
    universal = service.save_group(service.GroupInput(**abc), ab["id"])
    with Session(engine) as session:
        assert session.get(Jargon, orphan.id).archived
        assert [row.id for row in session.exec(select(Jargon).where(col(Jargon.archived).is_(False)))] == [group_id]
    row, _, _ = scopes.collect_learning("词", "新来源", [], {"新来源线索"})
    assert (row.id, row.count, row.last_inference_count) == (group_id, 13, 8)
    assert set(scopes.learning_material(group_id)[0]) == {"A 的线索", "C 的线索", "旧来源线索", "新来源线索"}

    narrowed = service.save_group(service.GroupInput(**{**universal, "rules": [], "member_ids": ["A"]}), ab["id"])
    with Session(engine) as session:
        assert scopes.scope_row(session, "词", "chat", "B") is None
        for chat in ["C", "旧来源", "新来源"]:
            private = scopes.scope_row(session, "词", "chat", chat)
            assert (private.meaning, private.count, private.last_inference_count) == ("C 定义", 13, 8)
        assert scopes.scope_row(session, "词", "chat", "A") is None
    restored = service.save_group(service.GroupInput(**{**universal, "revision": narrowed["revision"]}), ab["id"])
    with Session(engine) as session:
        assert scopes.scope_row(session, "词", "chat", "旧来源") is None
    service.delete_group(ab["id"], restored["revision"])
    independent, created, _ = scopes.collect_learning("词", "旧来源", [], {"再学习"})
    assert not created and (independent.meaning, independent.count, independent.last_inference_count) == ("C 定义", 14, 8)
    assert set(scopes.learning_material(independent.id)[0]) == {"A 的线索", "C 的线索", "旧来源线索", "新来源线索", "再学习"}
    first, created, _ = scopes.collect_learning("词", "B", [], {"首次出现"})
    assert created and (first.count, first.meaning) == (1, "")

@pytest.mark.parametrize("change", ["members", "disable", "capability"])
def test_jargon_group_changes_isolate_leaving_and_joining_chats(store, monkeypatch, change):
    from src.services import jargon_scope_service as scopes

    _, engine = store
    monkeypatch.setattr(scopes, "get_db_session", service.get_db_session)
    enabled = {"expression": True, "jargon": True}
    ab = service.save_group(group("原组", ["A", "B"], enabled))
    old, _, _ = scopes.collect_learning("同词", "A", [], {"旧组线索"})
    local, _, _ = scopes.collect_learning("同词", "C", [], {"C 的线索"})
    with Session(engine) as session:
        original = session.get(Jargon, old.id)
        original.meaning, original.count, original.last_inference_count = "旧组释义", 7, 4
        winner = session.get(Jargon, local.id)
        winner.allow_update = False
        winner.meaning, winner.count, winner.last_inference_count, winner.created_by = "C 的释义", 12, 8, "MANUAL"
        session.commit()

    if change == "members":
        # 一次保存内 A 退出、B 留组、C 加入。A 只能继承变更前的词库。
        bc = service.save_group(service.GroupInput(**{**ab, "member_ids": ["B", "C"]}), ab["id"])
    else:
        off = {"enabled": False} if change == "disable" else {"capabilities": {"expression": True}}
        paused = service.save_group(service.GroupInput(**{**ab, **off}), ab["id"])
        assert service.resolve_scope("B", "expression")[0] == ({"B"} if change == "disable" else {"A", "B"})
        bc = service.save_group(service.GroupInput(**{**paused, "enabled": True, "capabilities": enabled,
                                                      "member_ids": ["B", "C"]}), ab["id"])
    with Session(engine) as session:
        left = scopes.scope_row(session, "同词", "chat", "A")
        shared = scopes.scope_row(session, "同词", "group", bc["id"])
        assert (left.meaning, left.count, left.last_inference_count) == ("旧组释义", 7, 4)
        assert (shared.meaning, shared.count, shared.last_inference_count, shared.created_by) == ("C 的释义", 12, 8, "MANUAL")
        assert all(scopes.scope_row(session, "同词", "chat", chat) is None for chat in ["B", "C"])
        assert set(scopes.learning_material(left.id)[0]) == {"旧组线索"}
        assert set(scopes.learning_material(shared.id)[0]) == {"旧组线索", "C 的线索"}

    other = service.save_group(group("另一个组", ["A"], {"jargon": True}))
    row, _, _ = scopes.collect_learning("同词", "A", [], {"转组后"})
    assert (row.scope_id, row.count, row.last_inference_count) == (other["id"], 8, 4)
    assert set(scopes.learning_material(shared.id)[0]) == {"旧组线索", "C 的线索"}
    with Session(engine) as session:
        assert len(session.exec(select(JargonLearningRecord)).all()) == 3


def test_bulk_departure_snapshot_isolated_writes_and_rejoin(store, monkeypatch):
    from src.common.database.jargon_snapshots import read_jargons
    from src.services import jargon_scope_service as scopes
    from src.webui.routers import jargon as api

    _, engine = store
    monkeypatch.setattr(scopes, "get_db_session", service.get_db_session)
    monkeypatch.setattr(api, "get_db_session", service.get_db_session)
    monkeypatch.setattr(api, "get_display_name_for_session_id", lambda chat, _: chat)
    monkeypatch.setattr(api, "list_groups", service.list_groups)
    monkeypatch.setattr(api, "require_existing_session_ids", lambda chats: chats)
    ab = service.save_group(group("AB", ["A", "B"], {"jargon": True}))
    first, _, _ = scopes.collect_learning("词", "A", [], {"来自 A"})
    second, _, _ = scopes.collect_learning("词", "B", [], {"来自 B"})
    with Session(engine) as session:
        row = session.get(Jargon, first.id)
        row.meaning, row.count, row.last_inference_count, row.is_jargon = "旧含义", 12, 8, True
        session.add(Jargon(content="另一个", meaning="已有", scope_kind="group", scope_id=ab["id"]))
        session.commit()
    assert first.id == second.id
    service.delete_group(ab["id"], ab["revision"])
    with Session(engine) as session:
        db = session.connection()
        assert db.exec_driver_sql("SELECT count(*) FROM jargons WHERE scope_kind='snapshot'").scalar_one() == 2
        assert db.exec_driver_sql("SELECT count(*) FROM jargons WHERE scope_kind='chat'").scalar_one() == 0
        assert db.exec_driver_sql("SELECT count(*) FROM jargon_snapshot_members WHERE chat IS NOT NULL").scalar_one() == 2
        a = scopes.scope_row(session, "词", "chat", "A").id
        b = scopes.scope_row(session, "词", "chat", "B").id
        assert a != b and max(a, b) < 0
        assert a in session.exec(scopes.effective_scope_ids("A", [])).all()
        assert b not in session.exec(scopes.effective_scope_ids("A", [])).all()
    assert api.get_jargon_detail(a).data.meaning == "旧含义"
    assert {event["excerpt"] for event in api.jargon_evidence(b, 0)} == {"来自 A", "来自 B"}
    with pytest.raises(api.HTTPException) as conflict:
        api.update_jargon(a, api.JargonUpdateRequest(content="另一个", revision=0))
    assert conflict.value.status_code == 409
    assert api.update_jargon(a, api.JargonUpdateRequest(meaning="手动", created_by="MANUAL", revision=0)).data.id == a
    assert api.get_jargon_detail(b).data.meaning == "旧含义"
    with pytest.raises(api.HTTPException) as stale:
        api.update_jargon(a, api.JargonUpdateRequest(meaning="旧表单", revision=0))
    assert stale.value.status_code == 409
    learned, created, _ = scopes.collect_learning("词", "B", [], {"独立学习"})
    assert not created and (learned.id, learned.count, learned.last_inference_count) == (b, 13, 8)
    assert set(scopes.learning_material(a)[0]) == {"来自 A", "来自 B"}
    assert api.get_jargon_detail(a).data.count == 12

    with Session(engine) as session:
        extra = scopes.scope_row(session, "另一个", "chat", "A").id
    exported = api.export_jargons(api.JargonExportRequest(ids=[extra]))
    assert [item.content for item in exported.jargons] == ["另一个"]
    imported = api.JargonImportRequest(jargons=[api.JargonImportItem(content="另一个", meaning="导入")], target_session_ids=["A"])
    preview = api.preview_jargon_import(imported)
    assert len(preview) == 1 and preview[0].records[0].id == extra
    imported.replacements = {"另一个": preview[0].records}
    assert api.import_jargons(imported).imported_count == 1
    assert api.get_jargon_detail(extra).data.meaning == "导入"
    api.delete_jargon(extra)
    with Session(engine) as session:
        assert scopes.scope_row(session, "另一个", "chat", "A") is None
        assert scopes.scope_row(session, "另一个", "chat", "B") is not None
        rep = read_jargons(representative=True)
        assert session.exec(select(rep.id).where(rep.content == "另一个", col(rep.archived).is_(False))).one() != extra

    regrouped = service.save_group(group("新 AB", ["A", "B"], {"jargon": True}))
    with Session(engine) as session:
        row = scopes.scope_row(session, "词", "group", regrouped["id"])
        shared = row.id
        assert (row.meaning, row.count, row.last_inference_count) == ("手动", 12, 8)
        assert scopes.scope_row(session, "另一个", "group", regrouped["id"]).meaning == "已有"
        assert session.connection().exec_driver_sql("SELECT count(*) FROM jargons WHERE scope_kind='snapshot'").scalar_one() == 0
    assert set(scopes.learning_material(shared)[0]) == {"来自 A", "来自 B", "独立学习"}

def test_delete_global_only_creates_contributed_words_and_rolls_back(store, monkeypatch):
    from src.services import jargon_scope_service as scopes

    _, engine = store
    chats = [f"聊天{i}" for i in range(64)]
    monkeypatch.setattr(scopes, "get_db_session", service.get_db_session)
    monkeypatch.setattr(service, "_rule_ids", lambda _: set(chats))
    group_data = service.GroupInput(name="全局", color="#39845A", capabilities={"jargon": True},
                                   rules=[service.MemberRule(platform="*", item_id="*", rule_type="all")])
    shared = service.save_group(group_data)
    with Session(engine) as session:
        session.add_all([ChatSession(session_id=chat, platform="qq", group_id=chat) for chat in chats])
        for chat in chats:
            session.add(Messages(session_id=chat, platform="qq", message_id=chat, user_id="user",
                                 user_nickname="user", raw_content=b"hello"))
        session.add_all([Jargon(scope_kind="group", scope_id=shared["id"], content=f"词{i}", meaning="释义")
                         for i in range(250)])
        session.commit()

    def interrupt(session, members):
        raise RuntimeError("回滚测试")

    scopes.collect_learning("词1", chats[0], [], {"一号线索"})
    scopes.collect_learning("词2", chats[-1], [], {"二号线索"})
    scopes.collect_learning("词2", chats[0], [], {"共同线索"})

    with monkeypatch.context() as patch:
        patch.setattr(scopes, "assign_deferred_learning", interrupt)
        with pytest.raises(RuntimeError, match="回滚测试"):
            service.delete_group(shared["id"], shared["revision"])
    with Session(engine) as session:
        db = session.connection()
        assert db.exec_driver_sql("SELECT count(*) FROM jargons WHERE archived=0").scalar_one() == 250
        assert db.exec_driver_sql("SELECT count(*) FROM jargon_snapshot_members").scalar_one() == 0
        assert db.exec_driver_sql("SELECT count(*) FROM jargons WHERE scope_kind='snapshot'").scalar_one() == 0

    service.delete_group(shared["id"], shared["revision"])
    with pytest.raises(KeyError):
        service.delete_group(shared["id"], shared["revision"])
    with Session(engine) as session:
        db = session.connection()
        assert db.exec_driver_sql("SELECT count(*) FROM jargons").scalar_one() == 253
        assert db.exec_driver_sql("SELECT count(*) FROM jargon_snapshot_members").scalar_one() == 0
        assert set(db.exec_driver_sql("SELECT content,scope_id FROM jargons WHERE archived=0")) == {
            ("词1", chats[0]), ("词2", chats[0]), ("词2", chats[-1]),
        }
        common = scopes.scope_row(session, "词2", "chat", chats[-1])
        assert set(scopes.learning_material(common.id)[0]) == {"二号线索", "共同线索"}


@pytest.mark.parametrize("turn_off", [{"enabled": False}, {"capabilities": {"expression": True}}])
def test_disable_global_preserves_previous_owners_without_learning_records(store, monkeypatch, turn_off):
    from src.services import jargon_scope_service as scopes

    _, engine = store
    monkeypatch.setattr(scopes, "get_db_session", service.get_db_session)
    with Session(engine) as session:
        for owner in ("A", "B"):
            session.add(Jargon(content="人工词", meaning="自有释义", scope_kind="chat", scope_id=owner, created_by="MANUAL"))
        session.commit()
    global_group = service.save_group(service.GroupInput(
        name="所有聊天", color="#39845A", capabilities={"jargon": True},
        rules=[service.MemberRule(platform="*", item_id="*", rule_type="all")]))
    scopes.collect_learning("新词", "C", [], {"来自 C"})
    service.save_group(service.GroupInput(**{**global_group, **turn_off}), global_group["id"])
    with Session(engine) as session:
        active = session.connection().exec_driver_sql(
            "SELECT content,scope_id FROM jargons WHERE scope_kind='chat' AND archived=0")
        assert set(active) == {("人工词", "A"), ("人工词", "B"), ("新词", "C")}
