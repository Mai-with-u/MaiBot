"""覆盖只改变聊天范围，不删除共享向量、人物或来源内容。"""

import json

import pytest

from src.A_memorix.core.runtime.services.chat_migration_service import ChatMemoryMigration
from src.A_memorix.core.runtime.services.search_hit_processing_service import MemorySearchHitProcessingService
from src.A_memorix.core.storage.metadata_store import MetadataStore


@pytest.fixture
def memory(tmp_path):
    store = MetadataStore(tmp_path)
    store.connect()
    with store.transaction() as conn:
        for key, scopes in (("source", ["A"]), ("target", ["B"]), ("shared", ["B", "C"]), ("global", [])):
            metadata = {"scope_type": "chat" if scopes else "global", "chat_ids": scopes}
            conn.execute(
                "INSERT INTO paragraphs (hash,content,created_at,last_accessed,metadata,source) VALUES (?,?,1,1,?,?)",
                (key, key, json.dumps(metadata), f"chat_summary:{scopes[0]}" if scopes else "web_import:test"),
            )
        conn.execute(
            "INSERT INTO image_assets (asset_id,content_hash,mime_type,storage_key,width,height,byte_size,status,created_at,updated_at) VALUES ('asset','content','image/png','asset.png',1,1,1,'ready',1,1)"
        )
        conn.execute(
            "INSERT INTO image_occurrences (occurrence_id,asset_id,external_ref,source_kind,scope_type,chat_id,status,created_at,updated_at) VALUES ('image','asset','external','message','chat','A','active',1,1)"
        )
    yield store, ChatMemoryMigration(store)
    store.close()


def scope(store, key):
    return MemorySearchHitProcessingService._paragraph_scope_identity(
        store.query("SELECT * FROM paragraphs WHERE hash=?", (key,))[0]
    )


def test_full_overwrite_preserves_shared_and_source_and_restores(memory):
    store, service = memory
    before = store.query("SELECT * FROM paragraphs ORDER BY hash")
    snapshot = service.snapshot("A", "B")
    service.apply("job", "A", "B", snapshot["fingerprint"])
    assert scope(store, "source") == ("chat", {"A", "B"})
    assert scope(store, "target") == ("unknown", set())
    assert scope(store, "shared") == ("chat", {"C"})
    assert scope(store, "global") == ("global", set())
    assert store.query("SELECT chat_id FROM image_occurrences WHERE status='active' ORDER BY chat_id") == [
        {"chat_id": "A"},
        {"chat_id": "B"},
    ]
    service.restore("job")
    assert store.query("SELECT * FROM paragraphs ORDER BY hash") == before
    assert len(store.query("SELECT * FROM image_occurrences")) == 1


def test_empty_source_clears_only_target_scope(memory):
    store, service = memory
    snapshot = service.snapshot("empty", "B")
    service.apply("job", "empty", "B", snapshot["fingerprint"])
    assert scope(store, "source") == ("chat", {"A"})
    assert scope(store, "shared") == ("chat", {"C"})
    assert scope(store, "target") == ("unknown", set())


def test_memory_change_invalidates_preview(memory):
    store, service = memory
    snapshot = service.snapshot("A", "B")
    with store.transaction() as conn:
        conn.execute("UPDATE paragraphs SET content='new' WHERE hash='source'")
    with pytest.raises(ValueError, match="变化"):
        service.apply("job", "A", "B", snapshot["fingerprint"])


def test_restore_refuses_to_erase_later_edits(memory):
    store, service = memory
    snapshot = service.snapshot("A", "B")
    service.apply("job", "A", "B", snapshot["fingerprint"])
    with store.transaction() as conn:
        conn.execute("UPDATE paragraphs SET metadata='{}' WHERE hash='source'")
    with pytest.raises(ValueError, match="被修改"):
        service.restore("job")


def test_facts_episodes_and_image_archive_are_scoped_and_reversible(memory):
    store, service = memory
    for owner in ("A", "B", "C"):
        store.upsert_fact_claim(
            scope_type="chat",
            scope_id=owner,
            fact_key="topic",
            value_text=owner,
            evidence_type="paragraph",
            evidence_id="source" if owner == "A" else "target",
        )
    with store.transaction() as conn:
        for owner in ("A", "B"):
            conn.execute(
                "INSERT INTO episodes (episode_id,source,title,summary,created_at,updated_at) VALUES (?,?,?, ?,1,1)",
                (owner, f"chat_summary:{owner}", owner, owner),
            )
            conn.execute(
                "INSERT INTO episode_paragraphs (episode_id,paragraph_hash) VALUES (?,?)",
                (owner, "source" if owner == "A" else "target"),
            )
        conn.execute("UPDATE image_assets SET status='active'")
        conn.execute(
            "INSERT INTO image_occurrences (occurrence_id,asset_id,external_ref,source_kind,scope_type,chat_id,status,created_at,updated_at) VALUES ('old','asset','old-ref','message','chat','B','active',1,1)"
        )
    tables = ("fact_claims", "fact_evidence", "fact_transitions", "episodes", "episode_paragraphs", "image_occurrences")
    before = {table: store.query(f"SELECT * FROM {table} ORDER BY rowid") for table in tables}
    state = service.snapshot("A", "B")
    service.apply("full", "A", "B", state["fingerprint"])
    assert store.query("SELECT value_text FROM fact_claims WHERE scope_id='B'") == [{"value_text": "A"}]
    assert store.query("SELECT title FROM episodes WHERE source='chat_summary:B'") == [{"title": "A"}]
    visible = store.list_visible_image_occurrences(chat_ids=None)
    assert {row["occurrence_id"] for row in visible} == {"image", "migration_full_image"}
    service.restore("full")
    for table in tables:
        assert sorted(store.query(f"SELECT * FROM {table}"), key=lambda row: json.dumps(row, sort_keys=True)) == sorted(
            before[table], key=lambda row: json.dumps(row, sort_keys=True)
        )
