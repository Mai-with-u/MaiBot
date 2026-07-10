from pathlib import Path

from src.A_memorix.core.retrieval.sparse_bm25 import (
    SparseBM25Config,
    SparseBM25Index,
)
from src.A_memorix.core.storage.metadata_store import MetadataStore


def test_sparse_warmup_loads_index_and_runs_probe(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    index = None
    try:
        paragraph_hash = store.add_paragraph("艾宝 喜欢 稀疏检索 warmup 测试", source="test")
        store.add_relation("艾宝", "喜欢", "稀疏检索", source_paragraph=paragraph_hash)

        index = SparseBM25Index(
            metadata_store=store,
            config=SparseBM25Config(enable_ngram_fallback_index=False),
        )

        summary = index.warmup(sample_queries=["艾宝 稀疏检索"], relation_query="艾宝 喜欢")

        assert summary["ok"] is True
        assert summary["loaded"] is True
        assert summary["doc_count"] == 1
        assert summary["tokenized_query_count"] == 1
        assert summary["paragraph_probe_count"] >= 0
        assert summary["relation_probe_count"] >= 0
        assert summary["duration_ms"] >= 0.0
    finally:
        if index is not None:
            index.unload()
        store.close()


def test_sparse_tokenized_shadow_index_search_and_delete(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    index = None
    try:
        paragraph_hash = store.add_paragraph("唐未负责校准温湿度计，并且只喝无糖姜茶。", source="test")
        index = SparseBM25Index(
            metadata_store=store,
            config=SparseBM25Config(
                enable_ngram_fallback_index=False,
                enable_tokenized_shadow_index=True,
            ),
        )

        assert index.warmup(sample_queries=["温湿度计 姜茶"])["ok"] is True
        rows = index.search("温湿度计 姜茶", k=5)

        assert rows
        assert rows[0]["hash"] == paragraph_hash
        assert "无糖姜茶" in rows[0]["content"]

        assert index.delete_paragraph(paragraph_hash) is True
        assert index.search("温湿度计 姜茶", k=5) == []
    finally:
        if index is not None:
            index.unload()
        store.close()


def test_sparse_tokenized_shadow_index_incremental_lifecycle(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    index = None
    try:
        index = SparseBM25Index(
            metadata_store=store,
            config=SparseBM25Config(
                enable_ngram_fallback_index=False,
                enable_tokenized_shadow_index=True,
            ),
        )
        assert index.ensure_loaded()

        paragraph_hash = store.add_paragraph("探针段落 含有 蓝莓曲奇 和 事务一致性", source="test")
        assert index.search("蓝莓曲奇 事务一致性", k=5)[0]["hash"] == paragraph_hash

        assert store.mark_as_deleted([paragraph_hash], "paragraph") == 1
        assert index.search("蓝莓曲奇 事务一致性", k=5) == []

        assert store.revive_if_deleted(paragraph_hashes=[paragraph_hash]) == 1
        assert index.search("蓝莓曲奇 事务一致性", k=5)[0]["hash"] == paragraph_hash

        assert store.physically_delete_paragraphs([paragraph_hash]) == 1
        assert index.search("蓝莓曲奇 事务一致性", k=5) == []
    finally:
        if index is not None:
            index.unload()
        store.close()


def test_tokenized_shadow_index_meta_tracks_incremental_lifecycle(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        assert store.ensure_paragraph_tokenized_fts_schema()
        first_hash = store.add_paragraph("第一条 tokenized meta 段落", source="test")

        conn = store._conn
        assert conn is not None
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM paragraph_tokenized_fts_meta WHERE key='paragraph_count'")
        assert cursor.fetchone()[0] == "1"

        second_hash = store.add_paragraph("第二条 tokenized meta 段落", source="test")
        cursor.execute("SELECT value FROM paragraph_tokenized_fts_meta WHERE key='paragraph_count'")
        assert cursor.fetchone()[0] == "2"

        assert store.mark_as_deleted([first_hash], "paragraph") == 1
        cursor.execute("SELECT value FROM paragraph_tokenized_fts_meta WHERE key='paragraph_count'")
        assert cursor.fetchone()[0] == "1"

        assert store.revive_if_deleted(paragraph_hashes=[first_hash]) == 1
        cursor.execute("SELECT value FROM paragraph_tokenized_fts_meta WHERE key='paragraph_count'")
        assert cursor.fetchone()[0] == "2"

        assert store.physically_delete_paragraphs([first_hash, second_hash]) == 2
        cursor.execute("SELECT value FROM paragraph_tokenized_fts_meta WHERE key='paragraph_count'")
        assert cursor.fetchone()[0] == "0"
    finally:
        store.close()


def test_relation_support_batch_excludes_soft_deleted_paragraphs(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        live_hash = store.add_paragraph("关系支撑 live 段落", source="chat_summary:live")
        deleted_hash = store.add_paragraph("关系支撑 deleted 段落", source="chat_summary:deleted")
        relation_hash = store.add_relation("Alice", "记得", "蓝莓曲奇", source_paragraph=live_hash)
        assert store._conn is not None
        store._conn.execute(
            "INSERT OR IGNORE INTO paragraph_relations (paragraph_hash, relation_hash) VALUES (?, ?)",
            (deleted_hash, relation_hash),
        )
        store._conn.commit()

        assert store.mark_as_deleted([deleted_hash], "paragraph") == 1

        grouped = store.get_paragraphs_by_relation_hashes([relation_hash])

        assert [item["hash"] for item in grouped[relation_hash]] == [live_hash]
    finally:
        store.close()


def test_tokenized_shadow_index_does_not_commit_outer_transaction(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        paragraph_hash = store.add_paragraph("shadow index rollback probe 蓝莓曲奇", source="test")
        assert store.ensure_paragraph_tokenized_fts_schema()
        assert store.ensure_paragraph_tokenized_fts_backfilled()

        conn = store._conn
        assert conn is not None
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        assert conn.in_transaction is True

        assert store.fts_delete_tokenized_paragraph(paragraph_hash, conn=conn) is True
        assert conn.in_transaction is True

        cursor.execute("ROLLBACK")
        assert conn.in_transaction is False
        rows = store.fts_search_tokenized_paragraphs_bm25("蓝莓 曲奇", limit=5)
        assert {row["hash"] for row in rows} == {paragraph_hash}
    finally:
        store.close()


def test_primary_fts_delete_does_not_commit_outer_transaction(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        paragraph_hash = store.add_paragraph("primary fts rollback probe 蓝莓曲奇", source="test")
        assert store.fts_upsert_paragraph(paragraph_hash) is True

        conn = store.get_connection()
        conn.execute("BEGIN IMMEDIATE")
        assert store.fts_delete_paragraph(paragraph_hash, conn=conn) is True
        assert conn.in_transaction is True

        conn.rollback()
        rows = store.fts_search_bm25("蓝莓曲奇", limit=5)
        assert {row["hash"] for row in rows} == {paragraph_hash}
    finally:
        store.close()


def test_sparse_experimental_backend_is_explicitly_not_runtime_ready(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    index = None
    try:
        index = SparseBM25Index(
            metadata_store=store,
            config=SparseBM25Config(backend="tantivy"),
        )

        summary = index.warmup()

        assert summary["ok"] is False
        assert "实验接口" in summary["error"]
        assert index.loaded is False
    finally:
        if index is not None:
            index.unload()
        store.close()
