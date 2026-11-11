"""聊天迁移的真实 SQLite 事务、冲突与幂等性回归。"""

from sqlalchemy import create_engine, text

import pytest

from src.services.chat_migration_store import MigrationStore


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as conn:
        for sql in (
            "CREATE TABLE chat_sessions (id INTEGER PRIMARY KEY, session_id TEXT UNIQUE, platform TEXT, group_id TEXT, user_id TEXT)",
            "CREATE TABLE mai_messages (id INTEGER PRIMARY KEY, session_id TEXT, message_id TEXT, platform TEXT, raw_content BLOB)",
            "CREATE TABLE expressions (id INTEGER PRIMARY KEY, session_id TEXT, situation TEXT, style TEXT, count INTEGER)",
            "CREATE TABLE jargons (id INTEGER PRIMARY KEY, content TEXT, meaning TEXT, session_id_dict TEXT, is_global BOOLEAN)",
            "CREATE TABLE maisaka_monitor_events (event_id INTEGER PRIMARY KEY, session_id TEXT, payload_json TEXT)",
        ):
            conn.execute(text(sql))
        conn.execute(text("INSERT INTO chat_sessions VALUES (1,'A','qq','123',NULL),(2,'B','webui',NULL,'456')"))
        conn.execute(text("INSERT INTO mai_messages VALUES (1,'A','original','qq',X'010203')"))
        conn.execute(text("INSERT INTO expressions VALUES (1,'A','hi','hey',3),(2,'B','hi','hey',7)"))
        conn.execute(
            text("INSERT INTO jargons VALUES (1,'word','source meaning',:a,0),(2,'word','target meaning',:b,0)"),
            {"a": '{"A":2}', "b": '{"B":5}'},
        )
        conn.execute(text("INSERT INTO maisaka_monitor_events VALUES (1,'A','{}'),(2,'A','{}')"))
    service = MigrationStore(engine)
    service.initialize()
    return service


def execute(store, source="A", target="B", choice="both"):
    state = store.snapshot(source, target)
    job = store.create(source, target, {})
    return store.apply(job, state["fingerprint"], {item["id"]: choice for item in state["conflicts"]})


def test_copy_preserves_source_and_message_identity(store):
    execute(store)
    with store.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT session_id,message_id,platform,raw_content FROM mai_messages ORDER BY id")
        ).all()
        assert rows == [("A", "original", "qq", b"\x01\x02\x03"), ("B", "original", "qq", b"\x01\x02\x03")]
        assert conn.execute(text("SELECT count(*) FROM maisaka_monitor_events WHERE session_id='B'")).scalar_one() == 2
        assert conn.execute(text("SELECT count FROM expressions WHERE id=1")).scalar_one() == 3


def test_repeat_and_reverse_do_not_duplicate(store):
    execute(store)
    execute(store)
    execute(store, "B", "A")
    with store.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM mai_messages")).scalar_one() == 2


def test_repeat_uses_record_identity_without_syncing_content_changes(store):
    execute(store)
    with store.engine.begin() as conn:
        conn.execute(text("UPDATE mai_messages SET raw_content=X'FF' WHERE session_id='A'"))
        conn.execute(text("UPDATE expressions SET style='edited source' WHERE session_id='A'"))
        conn.execute(text("UPDATE expressions SET style='edited target' WHERE session_id='B'"))
    state = store.snapshot("A", "B")
    assert state["conflicts"] == []
    execute(store)
    execute(store, "B", "A")
    with store.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM mai_messages")).scalar_one() == 2
        assert (
            conn.execute(text("SELECT raw_content FROM mai_messages WHERE session_id='B'")).scalar_one()
            == b"\x01\x02\x03"
        )


def test_new_record_still_requires_conflict_resolution_after_prior_migration(store):
    execute(store)
    with store.engine.begin() as conn:
        conn.execute(text("INSERT INTO expressions(session_id,situation,style,count) VALUES ('A','hi','hey',1)"))
    state = store.snapshot("A", "B")
    assert len(state["conflicts"]) == 1
    assert state["conflicts"][0]["kind"] == "expressions"
    job = store.create("A", "B", {})
    with pytest.raises(ValueError, match="冲突"):
        store.apply(job, state["fingerprint"], {})


def test_unresolved_conflict_changes_nothing(store):
    state = store.snapshot("A", "B")
    job = store.create("A", "B", {})
    with pytest.raises(ValueError, match="冲突"):
        store.apply(job, state["fingerprint"], {})
    with store.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM mai_messages")).scalar_one() == 1


def test_stale_preview_rejected(store):
    state = store.snapshot("A", "B")
    with store.engine.begin() as conn:
        conn.execute(text("UPDATE expressions SET count=10 WHERE id=2"))
    job = store.create("A", "B", {})
    with pytest.raises(ValueError, match="变化"):
        store.apply(job, state["fingerprint"], {})


def test_unknown_scoped_table_is_not_silently_skipped(store):
    with store.engine.begin() as conn:
        conn.execute(text("CREATE TABLE new_feature (id INTEGER PRIMARY KEY, session_id TEXT)"))
    with pytest.raises(ValueError, match="new_feature"):
        store.snapshot("A", "B")


def test_source_choice_unlinks_target_without_deleting_shared_jargon(store):
    with store.engine.begin() as conn:
        conn.execute(text("UPDATE jargons SET session_id_dict=:v WHERE id=2"), {"v": '{"B":5,"C":8}'})
    execute(store, choice="source")
    with store.engine.connect() as conn:
        assert conn.execute(text("SELECT session_id_dict FROM jargons WHERE id=2")).scalar_one() == '{"C": 8}'
        assert conn.execute(text("SELECT count(*) FROM expressions WHERE session_id='B'")).scalar_one() == 1


@pytest.mark.parametrize("global_scope", [False, True])
def test_jargon_already_in_target_scope_is_neither_conflict_nor_copy(store, global_scope):
    with store.engine.begin() as conn:
        conn.execute(
            text("UPDATE jargons SET session_id_dict=:scopes,is_global=:global_scope WHERE id=1"),
            {"scopes": '{"A":2,"B":5,"C":8}', "global_scope": global_scope},
        )
        before = conn.execute(text("SELECT * FROM jargons ORDER BY id")).all()
    state = store.snapshot("A", "B")
    assert not any(item["kind"] == "jargons" for item in state["conflicts"])
    for choice in ("source", "target", "both"):
        execute(store, choice=choice)
    with store.engine.connect() as conn:
        assert conn.execute(text("SELECT * FROM jargons ORDER BY id")).all() == before
        assert (
            conn.execute(text("SELECT count(*) FROM chat_migration_copies WHERE table_name='jargons'")).scalar_one()
            == 0
        )


def test_global_jargon_without_target_association_is_not_migrated(store):
    with store.engine.begin() as conn:
        conn.execute(text("UPDATE jargons SET is_global=1 WHERE id=1"))
        before = conn.execute(text("SELECT * FROM jargons ORDER BY id")).all()
    execute(store)
    with store.engine.connect() as conn:
        assert conn.execute(text("SELECT * FROM jargons ORDER BY id")).all() == before


def test_same_word_in_distinct_scopes_still_requires_resolution(store):
    state = store.snapshot("A", "B")
    conflict = next(item for item in state["conflicts"] if item["kind"] == "jargons")
    assert conflict["source"]["scope_label"] == "单个聊天流"
    assert conflict["source"]["meaning"] != conflict["target"][0]["meaning"]
    execute(store, choice="both")
    with store.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM jargons")).scalar_one() == 3


def test_aggregate_contributions_are_idempotent_and_transitive(store):
    with store.engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE statistics_usage_hourly (id INTEGER PRIMARY KEY, session_id TEXT, bucket TEXT, request_count INTEGER, UNIQUE(session_id,bucket))"
            )
        )
        conn.execute(text("INSERT INTO statistics_usage_hourly VALUES (1,'A','hour',3),(2,'B','hour',7)"))
    execute(store)
    execute(store)
    with store.engine.connect() as conn:
        assert (
            conn.execute(text("SELECT request_count FROM statistics_usage_hourly WHERE session_id='B'")).scalar_one()
            == 10
        )
    execute(store, "B", "A")
    with store.engine.connect() as conn:
        assert (
            conn.execute(text("SELECT request_count FROM statistics_usage_hourly WHERE session_id='A'")).scalar_one()
            == 10
        )
    with store.engine.begin() as conn:
        conn.execute(text("UPDATE statistics_usage_hourly SET request_count=request_count+2 WHERE session_id='A'"))
    execute(store)
    with store.engine.connect() as conn:
        assert (
            conn.execute(text("SELECT request_count FROM statistics_usage_hourly WHERE session_id='B'")).scalar_one()
            == 12
        )


def test_gate_waits_for_active_work_without_stopping_other_chats():
    import asyncio

    from src.services.chat_migration_gate import ChatMigrationGate

    async def scenario():
        gate = ChatMigrationGate()
        started, finish = asyncio.Event(), asyncio.Event()

        async def receive():
            async with gate.activity("A"):
                started.set()
                await finish.wait()
                async with gate.activity("A"):
                    pass  # 已进入的入站任务可以完成嵌套记忆写入。

        task = asyncio.create_task(receive())
        await started.wait()
        pause = asyncio.create_task(gate.pause({"A", "B"}))
        await asyncio.sleep(0)
        assert not pause.done()
        async with gate.activity("C"):
            pass
        finish.set()
        await task
        await pause
        waiting = asyncio.create_task(receive())
        await asyncio.sleep(0)
        assert not waiting.done()
        gate.resume({"A", "B"})
        await waiting
        assert gate.active == {}

    asyncio.run(scenario())
