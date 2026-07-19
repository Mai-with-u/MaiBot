from pathlib import Path

import json
import sqlite3

import pytest

from src.A_memorix.core.storage.metadata_store import MetadataStore


def test_get_all_sources_ignores_soft_deleted_paragraphs(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        live_hash = store.add_paragraph("Alice 喜欢地图", source="live-source")
        deleted_hash = store.add_paragraph("Bob 喜欢咖啡", source="deleted-source")

        assert live_hash
        store.mark_as_deleted([deleted_hash], "paragraph", reason="test_soft_delete")

        sources = store.get_all_sources()
    finally:
        store.close()

    assert [item["source"] for item in sources] == ["live-source"]
    assert sources[0]["count"] == 1


def test_add_paragraph_merges_chat_metadata_when_content_exists(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        original_hash = store.add_paragraph(
            "Alice 持有地图",
            source="web_import:demo.txt",
            metadata={"import_context": {"batch": "plain"}},
        )
        relation_hash = store.add_relation("Alice", "持有", "地图", source_paragraph=original_hash)

        rebound_hash = store.add_paragraph(
            "Alice 持有地图",
            source="web_import:demo.txt",
            metadata={"chat_id": "chat-1", "import_context": {"mode": "bound"}},
        )
        rebound_relation_hash = store.add_relation("Alice", "持有", "地图", source_paragraph=rebound_hash)

        second_rebound_hash = store.add_paragraph(
            "Alice 持有地图",
            source="web_import:demo.txt",
            metadata={"chat_id": "chat-2"},
        )

        paragraph = store.get_paragraph(original_hash)
        relation_paragraphs = store.get_paragraphs_by_relation_hashes([relation_hash])[relation_hash]
    finally:
        store.close()

    assert original_hash == rebound_hash == second_rebound_hash
    assert relation_hash == rebound_relation_hash
    assert paragraph is not None
    assert paragraph["metadata"]["import_context"] == {"batch": "plain", "mode": "bound"}
    assert paragraph["metadata"]["chat_id"] == "chat-2"
    assert paragraph["metadata"]["chat_ids"] == ["chat-1", "chat-2"]
    assert relation_paragraphs[0]["metadata"]["chat_ids"] == ["chat-1", "chat-2"]


def test_add_entities_batch_preserves_counts_links_and_revival(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        paragraph_hash = store.add_paragraph("Alice 与 Bob 参与项目", source="batch-source")
        alice_hash = store.add_entity("Alice", source_paragraph=paragraph_hash)
        connection = store.get_connection()
        connection.execute(
            "UPDATE entities SET is_deleted = 1, deleted_at = 1 WHERE hash = ?",
            (alice_hash,),
        )
        connection.commit()

        hashes = store.add_entities_batch(
            ["Alice", "Alice", "Bob"],
            source_paragraph=paragraph_hash,
        )
        rows = {
            row["name"]: dict(row)
            for row in connection.execute(
                "SELECT hash, name, appearance_count, is_deleted, deleted_at FROM entities"
            ).fetchall()
        }
        mention_counts = {
            row["entity_hash"]: int(row["mention_count"])
            for row in connection.execute(
                "SELECT entity_hash, mention_count FROM paragraph_entities WHERE paragraph_hash = ?",
                (paragraph_hash,),
            ).fetchall()
        }
    finally:
        store.close()

    assert hashes[0] == hashes[1] == alice_hash
    assert rows["Alice"]["appearance_count"] == 3
    assert rows["Alice"]["is_deleted"] == 0
    assert rows["Alice"]["deleted_at"] is None
    assert rows["Bob"]["appearance_count"] == 1
    assert mention_counts[alice_hash] == 3
    assert mention_counts[hashes[2]] == 1


def test_add_relations_batch_is_idempotent_and_preserves_first_metadata(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        paragraph_hash = store.add_paragraph("Alice 持有地图，Bob 居住于广州", source="batch-source")
        existing_hash = store.add_relation(
            "Alice",
            "持有",
            "地图",
            confidence=0.4,
            source_paragraph=paragraph_hash,
            metadata={"origin": "first"},
        )

        hashes = store.add_relations_batch(
            [
                ("Alice", "持有", "地图"),
                ("Alice", "持有", "地图"),
                ("Bob", "居住于", "广州"),
            ],
            confidence=1.0,
            source_paragraph=paragraph_hash,
            metadata={"origin": "batch"},
        )
        connection = store.get_connection()
        existing_row = connection.execute(
            "SELECT confidence, metadata FROM relations WHERE hash = ?",
            (existing_hash,),
        ).fetchone()
        relation_count = int(connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0])
        link_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM paragraph_relations WHERE paragraph_hash = ?",
                (paragraph_hash,),
            ).fetchone()[0]
        )
    finally:
        store.close()

    assert hashes[0] == hashes[1] == existing_hash
    assert hashes[2] != existing_hash
    assert relation_count == 2
    assert link_count == 2
    assert existing_row["confidence"] == 0.4
    assert json.loads(existing_row["metadata"]) == {"origin": "first"}


@pytest.mark.parametrize("target_type", ["entity", "relation"])
def test_batch_metadata_writes_roll_back_invalid_paragraph_links(
    tmp_path: Path,
    target_type: str,
) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            if target_type == "entity":
                store.add_entities_batch(["Alice"], source_paragraph="missing-paragraph")
            else:
                store.add_relations_batch(
                    [("Alice", "持有", "地图")],
                    source_paragraph="missing-paragraph",
                )

        connection = store.get_connection()
        entity_count = int(connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
        relation_count = int(connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0])
        assert connection.in_transaction is False
    finally:
        store.close()

    assert entity_count == 0
    assert relation_count == 0
