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


def test_get_paragraphs_by_hashes_ignores_soft_deleted_paragraphs(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        live_hash = store.add_paragraph("Alice 喜欢地图", source="live-source")
        deleted_hash = store.add_paragraph("Bob 喜欢咖啡", source="deleted-source")
        assert store.mark_as_deleted([deleted_hash], "paragraph", reason="test_soft_delete") == 1

        paragraphs = store.get_paragraphs_by_hashes([live_hash, deleted_hash])
    finally:
        store.close()

    assert set(paragraphs) == {live_hash}


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


def test_get_chat_summary_history_preserves_scope_order_and_filters(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        connection = store.get_connection()
        connection.executemany(
            """
            INSERT INTO paragraphs (hash, content, created_at, metadata, source, is_deleted)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("h1", "直接归属", 100.0, json.dumps({"dedup_phase": "complete"}), "chat_summary:chat-1", 0),
                ("h0", "同时间戳按哈希排序", 100.0, None, "chat_summary:chat-1", 0),
                (
                    "h2",
                    "直接归属且有多条引用",
                    200.0,
                    json.dumps({"dedup_phase": "complete"}),
                    "chat_summary:chat-1",
                    0,
                ),
                ("h3", "跨群复用段落", 150.0, json.dumps({}), "chat_summary:chat-2", 0),
                ("h4", "已软删的直接归属", 120.0, json.dumps({"dedup_phase": "complete"}), "chat_summary:chat-1", 1),
                ("h5", "已软删的跨群引用", 130.0, json.dumps({"dedup_phase": "complete"}), "chat_summary:chat-2", 1),
                ("h6", "去重未完成段落", 140.0, json.dumps({"dedup_phase": "pending"}), "chat_summary:chat-1", 0),
                ("h7", "is_deleted 为空", 160.0, None, "chat_summary:chat-1", None),
                ("h8", "其他群独有摘要", 170.0, None, "chat_summary:chat-2", 0),
                ("h9", "其他 source_type 引用", 180.0, None, "chat_summary:chat-1", 0),
            ],
        )
        connection.executemany(
            """
            INSERT INTO external_memory_refs (external_id, paragraph_hash, source_type, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("ext-1", "h2", "chat_summary", 190.0, json.dumps({"chat_id": "chat-1"})),
                ("ext-2", "h2", "chat_summary", 250.0, json.dumps({"chat_id": "chat-1"})),
                ("ext-3", "h3", "chat_summary", 155.0, json.dumps({"chat_id": "chat-1"})),
                ("ext-4", "h3", "chat_summary", 110.0, json.dumps({"chat_id": "chat-1"})),
                ("ext-5", "h5", "chat_summary", 165.0, json.dumps({"chat_id": "chat-1"})),
                ("ext-6", "h8", "chat_summary", 300.0, json.dumps({"chat_id": "chat-2"})),
                ("ext-7", "h9", "other_source", 999.0, json.dumps({"chat_id": "chat-1"})),
            ],
        )
        connection.commit()

        chat_1_history = store.get_chat_summary_history("chat-1")
        chat_2_history = store.get_chat_summary_history("chat-2")
        empty_history = store.get_chat_summary_history("chat-missing")
    finally:
        store.close()

    assert [item["hash"] for item in chat_1_history] == ["h0", "h1", "h3", "h7", "h9", "h2"]
    assert [item["hash"] for item in chat_2_history] == ["h3", "h8"]
    assert empty_history == []


def test_get_chat_summary_history_uses_indexed_lookup_plan(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        connection = store.get_connection()
        connection.executemany(
            """
            INSERT INTO paragraphs (hash, content, created_at, metadata, source, is_deleted)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            [
                (
                    f"other-{index}",
                    f"其他群摘要 {index}",
                    float(index),
                    None,
                    f"chat_summary:chat-other-{index % 10}",
                )
                for index in range(200)
            ],
        )
        connection.execute(
            """
            INSERT INTO paragraphs (hash, content, created_at, metadata, source, is_deleted)
            VALUES ('target-hash', '目标群摘要', 1000.0, NULL, 'chat_summary:chat-target', 0)
            """
        )
        connection.executemany(
            """
            INSERT INTO external_memory_refs (external_id, paragraph_hash, source_type, created_at, metadata_json)
            VALUES (?, ?, 'chat_summary', ?, ?)
            """,
            [
                (
                    f"ext-other-{index}",
                    f"other-{index}",
                    float(index),
                    json.dumps({"chat_id": f"chat-other-{index % 10}"}),
                )
                for index in range(200)
            ],
        )
        connection.execute(
            """
            INSERT INTO external_memory_refs (external_id, paragraph_hash, source_type, created_at, metadata_json)
            VALUES ('ext-target', 'other-0', 'chat_summary', 1001.0, ?)
            """,
            (json.dumps({"chat_id": "chat-target"}),),
        )
        connection.execute("ANALYZE")
        connection.commit()

        traced_sql: list[str] = []
        connection.set_trace_callback(traced_sql.append)
        try:
            history = store.get_chat_summary_history("chat-target")
        finally:
            connection.set_trace_callback(None)

        summary_queries = [sql for sql in traced_sql if "summary_observed_at" in sql]
        assert len(summary_queries) == 1
        plan_lines = [
            str(row["detail"]) for row in connection.execute(f"EXPLAIN QUERY PLAN {summary_queries[0]}").fetchall()
        ]
    finally:
        store.close()

    assert [item["hash"] for item in history] == ["target-hash", "other-0"]
    plan_text = "\n".join(plan_lines)
    assert "idx_paragraphs_source" in plan_text
    assert "idx_external_memory_refs_chat" in plan_text
    assert "sqlite_autoindex_paragraphs_1" in plan_text
    assert "idx_paragraphs_expiration" not in plan_text
    assert "MULTI-INDEX OR" not in plan_text
    assert "SCAN paragraphs" not in plan_text
    assert "SCAN p" not in plan_text
