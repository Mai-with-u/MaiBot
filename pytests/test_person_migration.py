from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock

from sqlmodel import Session, SQLModel, create_engine

import json
import asyncio
import sqlite3

import msgpack
import pytest

from src.common.database.database_model import ChatSession, Messages, PersonInfo
from src.common.process_lease import ProcessLease, instance_lock_path
from src.common.utils.utils_session import SessionUtils
from src.person_info.identity import (
    load_aliases, normalize_message_identity, person_id_for, resolve_person_id,
    resolve_private_transport_user, resolve_user_id,
)
from src.services import person_migration_service as service
from src.services.person_migration_plan import DatabasePlan, dumps, fingerprint, loads


def session_id(user: str, account: str = "bot") -> str:
    return SessionUtils.calculate_session_id("qq", user_id=user, account_id=account)


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    engine = create_engine(f"sqlite:///{tmp_path / 'data' / 'MaiBot.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        for user in ("old", "new"):
            db.add(PersonInfo(
                person_id=person_id_for("qq", user), platform="qq", user_id=user,
                user_nickname=f"昵称-{user}", person_name=f"名字-{user}", know_counts=2,
                group_cardname=json.dumps([{"group_id": "group", "group_cardname": f"名片-{user}"}]),
            ))
            db.add(ChatSession(session_id=session_id(user), platform="qq", account_id="bot", user_id=user))
        db.add(ChatSession(session_id=session_id("old", "bot2"), platform="qq", account_id="bot2", user_id="old"))
        for user, platform, stream in (("old", "qq", session_id("old")), ("new", "qq", session_id("new")),
                                       ("someone", "qq", "group-session"), ("old", "other", "other-session")):
            db.add(Messages(
                message_id=f"{platform}-{user}", timestamp=datetime(2026, 11, 16), platform=platform,
                account_id="bot", user_id=user, user_nickname=user, session_id=stream,
                raw_content=msgpack.packb([
                    {"type": "text", "data": "old 是原始正文"},
                    {"type": "at", "data": {"target_user_id": "old"}},
                    {"type": "reply", "data": "original-message", "target_message_sender_id": "old"},
                    {"type": "forward", "data": [{"user_id": "old", "content": [{"type": "text", "data": "old"}]}]},
                ], use_bin_type=True),
                additional_config=json.dumps({"qq_official_user_openid": "old", "user_id": user}),
            ))
        db.commit()
    engine.dispose()
    yield tmp_path


def ready_plan(root: Path, **kwargs: Any) -> Dict[str, Any]:
    result = service.preview(root, "qq", "old", "new", **kwargs)
    decisions = {item["id"]: "source" for item in result["conflicts"]}
    return service.preview(root, "qq", "old", "new", decisions=decisions, **kwargs)


def rows(root: Path, sql: str) -> List[Dict[str, Any]]:
    with sqlite3.connect(root / "data" / "MaiBot.db") as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql)]


def test_preview_conflicts_and_complete_merge(instance: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unresolved = service.preview(instance, "qq", "old", "new")
    assert not unresolved["ready"]
    assert any(item["field"] == "user_nickname" for item in unresolved["conflicts"])
    with pytest.raises(ValueError, match="确认"):
        service.execute(unresolved, expected_root=instance)
    plan = ready_plan(instance)
    assert plan["ready"]
    assert loads(dumps(plan)) == plan
    job = service.execute(plan, expected_root=instance)
    assert loads((job / "state.json").read_text(encoding="utf-8"))["status"] == "completed"
    people = rows(instance, "SELECT * FROM person_info")
    assert len(people) == 1
    assert people[0]["user_id"] == "new"
    assert people[0]["person_id"] == person_id_for("qq", "new")
    assert people[0]["user_nickname"] == "昵称-old"
    assert people[0]["know_counts"] == 4
    messages = {row["message_id"]: row for row in rows(instance, "SELECT * FROM mai_messages")}
    assert len(messages) == 4
    assert messages["qq-old"]["user_id"] == "old"
    assert messages["qq-old"]["session_id"] == session_id("old")
    for message in messages.values():
        assert msgpack.unpackb(message["raw_content"], raw=False)[1]["data"]["target_user_id"] == "old"
    assert {row["session_id"] for row in rows(instance, "SELECT * FROM chat_sessions")} == {
        session_id("old"), session_id("old", "bot2"), session_id("new"),
    }
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(instance))
    assert resolve_user_id("qq", "old") == "new"
    assert resolve_user_id("other", "old") == "old"
    assert resolve_person_id(person_id_for("qq", "old")) == person_id_for("qq", "new")
    assert resolve_private_transport_user("qq", "new", "bot", "") == "new"
    with pytest.raises(ValueError, match="已迁移"):
        service.preview(instance, "qq", "old", "new")


def test_online_execute_includes_writes_since_preview(instance: Path) -> None:
    with ProcessLease(instance_lock_path(instance)):
        plan = ready_plan(instance)
        with sqlite3.connect(instance / "data/MaiBot.db") as conn:
            conn.execute("UPDATE person_info SET know_counts=know_counts+1")
        service.execute(plan, expected_root=instance)
    assert rows(instance, "SELECT know_counts FROM person_info")[0]["know_counts"] == 6


def test_extension_failure_restores_all_databases(instance: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_db = instance / "data" / "plugin.db"
    with sqlite3.connect(plugin_db) as conn:
        conn.execute("CREATE TABLE scores(user_id TEXT PRIMARY KEY, score INTEGER)")
        conn.execute("INSERT INTO scores VALUES ('old', 10)")

    def build(plan: DatabasePlan, context: Dict[str, Any]) -> None:
        plan.transform("scores", lambda row: {**row, "user_id": context["user_map"].get(row["user_id"], row["user_id"])})

    extension = service.MigrationExtension("test-plugin", plugin_db, build)
    plan = ready_plan(instance, extensions=[extension])
    with sqlite3.connect(instance / "data" / "MaiBot.db") as conn:
        before = fingerprint(conn)
    actual_apply = service._apply_transaction_rows

    def fail(conn: sqlite3.Connection, schema: str, database: Dict[str, Any]) -> None:
        if database["name"] == "test-plugin":
            raise RuntimeError("模拟扩展写入失败")
        actual_apply(conn, schema, database)

    monkeypatch.setattr(service, "_apply_transaction_rows", fail)
    with pytest.raises(RuntimeError, match="模拟"):
        service.execute(plan, expected_root=instance)
    with sqlite3.connect(instance / "data" / "MaiBot.db") as conn:
        assert fingerprint(conn) == before
    assert not (instance / "data" / "person_identity_aliases.json").exists()


def test_recover_completed_commit_record(instance: Path) -> None:
    job = service.execute(ready_plan(instance), expected_root=instance)
    state = loads((job / "state.json").read_text(encoding="utf-8"))
    state["status"] = "applying"
    (job / "state.json").write_text(dumps(state), encoding="utf-8")
    with sqlite3.connect(instance / "data/MaiBot.db") as conn:
        conn.execute("UPDATE person_info SET know_counts=99")
    service.recover(job, expected_root=instance)
    assert loads((job / "state.json").read_text(encoding="utf-8"))["status"] == "completed"
    assert rows(instance, "SELECT know_counts FROM person_info")[0]["know_counts"] == 99


def test_unrelated_tables_are_outside_profile_migration(instance: Path) -> None:
    with sqlite3.connect(instance / "data" / "MaiBot.db") as conn:
        conn.execute("CREATE TABLE new_person_resource(id INTEGER PRIMARY KEY, person_id TEXT)")
    plan = ready_plan(instance)
    assert {change["table"] for database in plan["databases"] for change in database["changes"]} == {"person_info"}


def test_union_target_creation_and_transitive_aliases(instance: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(instance))
    ready_plan(instance)
    # 预览不得在当前进程悄悄改变别名解析结果。
    assert resolve_user_id("qq", "old") == "old"
    service.execute(ready_plan(instance), expected_root=instance)
    plan = service.preview(instance, "qq", "new", "UNION_ID")
    assert plan["ready"]
    service.execute(plan, expected_root=instance)
    assert resolve_user_id("qq", "old") == "UNION_ID"
    assert resolve_user_id("qq", "new") == "UNION_ID"
    assert resolve_private_transport_user("qq", "UNION_ID", "bot", "") == "UNION_ID"
    assert {row["user_id"] for row in rows(instance, "SELECT * FROM person_info")} == {"UNION_ID"}
    assert {row["session_id"] for row in rows(instance, "SELECT * FROM chat_sessions")} == {
        session_id("old"), session_id("old", "bot2"), session_id("new"),
    }


def test_plan_edits_require_regeneration(instance: Path) -> None:
    plan = ready_plan(instance)
    plan["identity"]["target"] = "different-target"
    with pytest.raises(ValueError, match="计划已被修改"):
        service.execute(plan, expected_root=instance)


@pytest.mark.asyncio
async def test_runtime_normalizes_references_without_losing_media(instance: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.chat.message_receive.bot import ChatBot
    from src.common.data_models.message_component_data_model import AtComponent, ForwardComponent, ForwardNodeComponent, ReplyComponent
    from src.services.send_service import _inherit_platform_io_route_metadata

    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(instance))
    service.execute(ready_plan(instance), expected_root=instance)
    at = AtComponent("old")
    reply = ReplyComponent("original-id", target_message_sender_id="old")
    node = ForwardComponent("昵称", "forward-id", [AtComponent("old")], user_id="old")
    media = SimpleNamespace(binary_data=b"unchanged")
    message = SimpleNamespace(
        platform="qq", message_info=SimpleNamespace(user_info=SimpleNamespace(user_id="old"), additional_config={}),
        raw_message=SimpleNamespace(components=[at, reply, ForwardNodeComponent([node]), media]),
    )
    normalize_message_identity(message)
    assert message.message_info.user_info.user_id == "new"

    assert at.target_user_id == reply.target_message_sender_id == node.user_id == "new"
    assert node.content[0].target_user_id == "new"
    assert message.raw_message.components[-1] is media
    assert media.binary_data == b"unchanged"
    stream = SimpleNamespace(platform="qq", user_id="new", account_id="bot", scope=None, group_id=None, context=None)
    assert _inherit_platform_io_route_metadata(stream)["platform_io_target_user_id"] == "new"
    message.account_id = "bot"
    message.scope = None
    message.message_info.group_info = None
    message.message_info.user_info.user_id = "old"
    receiver = SimpleNamespace(_receive_message=AsyncMock())
    await ChatBot._receive_with_migration_gate(receiver, message)
    receiver._receive_message.assert_awaited_once_with(message)
    assert message.message_info.user_info.user_id == "new"


@pytest.mark.asyncio
async def test_host_manual_preview_cancel_execute_and_duplicate(instance: Path) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.host.person_migration import register_person_migration

    runtime = SimpleNamespace(lock=asyncio.Lock())
    supervisor = SimpleNamespace(
        workspace=SimpleNamespace(registry=SimpleNamespace(get_bot=lambda _: SimpleNamespace(directory=instance))),
        status=AsyncMock(return_value={"desired_state": "running"}),
        stop=AsyncMock(return_value={"status": "stopped"}), start=AsyncMock(),
        _runtime=AsyncMock(return_value=runtime),
    )
    maintenance = SimpleNamespace(supervisor=supervisor, busy=False, _lock=asyncio.Lock())
    app = FastAPI()

    async def authenticated() -> None:
        pass

    register_person_migration(app, maintenance, authenticated)
    url = "/api/host/bots/bot/proxy/api/webui/person-migrations/user-injection"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        initial = await client.get(url)
        assert initial.status_code == 200
        assert supervisor.stop.await_count == 0
        response = await client.post(url + "/preview", json={"source": "old", "target": "new"})
        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["status"] == "preview" and not plan["ready"]
        assert "plan" not in plan
        supervisor.start.assert_not_awaited()
        premature = await client.post(url + "/execute", json={"preview_id": plan["preview_id"]})
        assert premature.status_code == 409
        cancelled = await client.post(url + "/cancel", json={"preview_id": plan["preview_id"]})
        assert cancelled.status_code == 200
        supervisor.start.assert_not_awaited()
        supervisor.stop.assert_not_awaited()
        assert len(rows(instance, "SELECT * FROM person_info")) == 2
        response = await client.post(url + "/preview", json={"source": "old", "target": "new"})
        plan = response.json()
        response = await client.post(url + "/preview", json={"source": "old", "target": "new",
                                     "decisions": {item["id"]: "target" for item in plan["conflicts"]}})
        assert response.status_code == 200, response.text
        ready = response.json()
        assert ready["ready"]
        completed = await client.post(url + "/execute", json={"preview_id": ready["preview_id"]})
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        supervisor.start.assert_not_awaited()
        duplicate = await client.post(url + "/execute", json={"preview_id": ready["preview_id"]})
        assert duplicate.status_code == 409
        invalid = await client.post(url + "/preview", json={"source": "new", "target": "new"})
        assert invalid.status_code == 422
        injected = await client.post(url + "/preview", json={"source": "new", "target": "destination"})
        assert injected.status_code == 200, injected.text
        injection = injected.json()
        assert injection["kind"] == "injection"
        assert injection["relations"] == [{"source": "new", "target": "destination"}]
        assert injection["ready"]
        assert rows(instance, "SELECT user_id FROM person_info")[0]["user_id"] == "new"
        executed = await client.post(url + "/execute", json={"preview_id": injection["preview_id"]})
        assert executed.status_code == 200, executed.text
        assert rows(instance, "SELECT user_id FROM person_info")[0]["user_id"] == "destination"
        supervisor.start.assert_not_awaited()



def test_qq_mapping_progress_tracks_core_migration(instance: Path) -> None:
    from src.plugins.built_in.qq_official_adapter.mapping_migration import migration_candidates

    mappings = [{"id": "old", "openid": "new", "source": "manual", "conflict": False}]
    assert migration_candidates(instance, mappings)[0]["status"] == "pending"
    service.execute(ready_plan(instance), expected_root=instance)
    result = migration_candidates(instance, mappings)[0]
    assert result["status"] == "migrated"
    assert result["target"] == "new"


def test_online_preview_does_not_require_stopping_instance(instance: Path) -> None:
    with ProcessLease(instance_lock_path(instance)):
        plan = service.preview(instance, "qq", "old", "new")
    assert plan["identity"]["source"] == "old"
    assert len(rows(instance, "SELECT * FROM person_info")) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unrelated", "source"])
async def test_online_execute_rechecks_affected_data(instance: Path, change: str) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from src.host.person_migration import register_person_migration

    with sqlite3.connect(instance / "data/MaiBot.db") as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO unrelated VALUES (1, 'before')")
    supervisor = SimpleNamespace(
        workspace=SimpleNamespace(registry=SimpleNamespace(get_bot=lambda _: SimpleNamespace(directory=instance))),
        status=AsyncMock(return_value={"desired_state": "running"}),
        stop=AsyncMock(return_value={"status": "stopped"}), start=AsyncMock(),
        _runtime=AsyncMock(return_value=SimpleNamespace(lock=asyncio.Lock())),
    )
    app = FastAPI()
    register_person_migration(app, SimpleNamespace(supervisor=supervisor, busy=False, _lock=asyncio.Lock()), lambda: None)
    url = "/api/host/bots/bot/proxy/api/webui/person-migrations/user-injection"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(url + "/preview", json={"source": "old", "target": "new"})
        choices = {item["id"]: "target" for item in response.json()["conflicts"]}
        response = await client.post(url + "/preview", json={"source": "old", "target": "new", "decisions": choices})
        assert response.status_code == 200, response.text
        supervisor.stop.assert_not_awaited()
        with sqlite3.connect(instance / "data/MaiBot.db") as conn:
            if change == "unrelated":
                conn.execute("UPDATE unrelated SET value='after'")
            else:
                conn.execute("UPDATE person_info SET user_nickname='changed' WHERE user_id='old'")
        result = await client.post(url + "/execute", json={"preview_id": response.json()["preview_id"]})
        supervisor.stop.assert_not_awaited()
        supervisor.start.assert_not_awaited()
        if change == "unrelated":
            assert result.status_code == 200, result.text
            assert result.json()["status"] == "completed"
        else:
            assert result.status_code == 409, result.text
            assert len(rows(instance, "SELECT * FROM person_info")) == 2
            fresh = (await client.get(url)).json()["preview"]
            assert fresh["preview_id"] != response.json()["preview_id"]
            assert fresh["decisions"] == {}
            assert "was_running" not in fresh


def test_database_plan_reads_a_stable_wal_snapshot(instance: Path) -> None:
    path = instance / "data/MaiBot.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    plan = DatabasePlan("main", path)
    try:
        original = next(row for row in plan.rows("person_info") if row["user_id"] == "old")
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE person_info SET user_nickname='after snapshot' WHERE user_id='old'")
        current = next(row for row in plan.rows("person_info") if row["user_id"] == "old")
        assert current == original
    finally:
        plan.close()


def test_missing_preview_plan_is_invalid_not_zero_conflicts() -> None:
    from src.host.person_migration import _public

    result = _public({"kind": "injection", "status": "preview", "preview_id": "a" * 32, "was_running": True})
    assert result["status"] == "invalid"
    assert result["error"] == "预览已失效，请重新预览"
    assert "conflicts" not in result


def test_public_preview_preserves_saved_plan(instance: Path) -> None:
    from src.host.person_migration import _public, _read, _save

    state = {"kind": "injection", "status": "preview", "preview_id": "a" * 32,
             "plan": service.preview(instance, "qq", "old", "destination")}
    first = _public(state)
    _save(instance, state)
    second = _public(_read(instance))
    assert first == second
    assert second["ready"] is True
    assert second["conflicts"] == []
    assert second["relations"] == [{"source": "old", "target": "destination"}]
    assert "main" in second["counts"]


def test_memory_queue_only_blocks_unfinished_related_writes(tmp_path: Path) -> None:
    queue = tmp_path / "startup_write_queue.jsonl"
    done = tmp_path / "startup_write_queue.done.jsonl"
    failed = tmp_path / "startup_write_queue.failed.jsonl"
    queue.write_text("\n".join(json.dumps(item) for item in [
        {"record_id": "completed", "payload": {"user_id": "old"}},
        {"record_id": "unrelated", "payload": {"chat_id": "other-group", "image_base64": "old"}},
        {"record_id": "related", "payload": {"chat_id": "private-old"}},
    ]), encoding="utf-8")
    done.write_text(json.dumps({"record_id": "completed"}), encoding="utf-8")
    failed.write_text(json.dumps({"record_id": "related", "error": "图片记忆未启用"}), encoding="utf-8")
    with pytest.raises(ValueError, match="图片记忆未启用"):
        service._check_pending_memory_writes(tmp_path, ["old", "private-old"])
    done.write_text('\n'.join(json.dumps({"record_id": key}) for key in ["completed", "related"]), encoding="utf-8")
    service._check_pending_memory_writes(tmp_path, ["old", "private-old"])


def test_filtered_preview_matches_full_scan(instance: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    optimized = ready_plan(instance)
    transform = DatabasePlan.transform

    def full_transform(self: DatabasePlan, table: str, callback: Any, **kwargs: Any) -> None:
        kwargs.pop("where", None)
        kwargs.pop("parameters", None)
        transform(self, table, callback, **kwargs)

    monkeypatch.setattr(DatabasePlan, "transform", full_transform)
    assert ready_plan(instance) == optimized


def test_current_memory_moves_without_rewriting_history(instance: Path) -> None:
    from src.A_memorix.core.storage.metadata_store import MetadataStore

    directory = instance / "data/a-memorix"
    store = MetadataStore(directory / "metadata")
    store.connect()
    source, target = person_id_for("qq", "old"), person_id_for("qq", "new")
    store.observe_person_identity(person_id=source, platform="qq", user_id="old", aliases=["旧称呼"], preferred_name="旧称呼")
    claim = store.upsert_fact_claim(scope_type="person", scope_id=source, fact_key="偏好", value_text="咖啡",
                                   cardinality="single", evidence_type="message", evidence_id="original-message")["claim_id"]
    with store.transaction() as conn:
        for version in (1, 2):
            conn.execute("INSERT INTO person_profile_snapshots(person_id,profile_version,profile_text,updated_at,expires_at,fact_claim_ids_json) VALUES (?,?,?,1,9999999999,?)",
                         (source, version, f"画像-{version}", json.dumps([claim])))
    store.close()
    database = directory / "metadata/metadata.db"
    with sqlite3.connect(database) as conn:
        old = conn.execute("SELECT * FROM person_profile_snapshots WHERE profile_version=1").fetchall()
        transitions = conn.execute("SELECT * FROM fact_transitions").fetchall()
        evidence = conn.execute("SELECT * FROM fact_evidence WHERE claim_id=?", (claim,)).fetchall()
    messages = rows(instance, "SELECT * FROM mai_messages")
    sessions = rows(instance, "SELECT * FROM chat_sessions")
    plan = ready_plan(instance)
    assert {c["table"] for c in plan["databases"][0]["changes"]} == {"person_info"}
    service.execute(plan, expected_root=instance)
    assert rows(instance, "SELECT * FROM mai_messages") == messages
    assert rows(instance, "SELECT * FROM chat_sessions") == sessions
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT * FROM person_profile_snapshots WHERE person_id=? AND profile_version=1", (source,)).fetchall() == old
        assert conn.execute("SELECT * FROM fact_transitions").fetchall() == transitions
        assert conn.execute("SELECT * FROM fact_evidence WHERE claim_id=?", (claim,)).fetchall() == evidence
        assert conn.execute("SELECT COUNT(*) FROM fact_claims WHERE scope_id=?", (target,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM person_profile_snapshots WHERE person_id=?", (target,)).fetchone()[0] == 1
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
