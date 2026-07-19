from pathlib import Path

from src.A_memorix.core.retrieval.sparse_bm25 import SparseBM25Config, SparseBM25Index
from src.A_memorix.core.storage.metadata_store import MetadataStore


def _new_store(tmp_path: Path) -> MetadataStore:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    store.ensure_paragraph_ngram_schema()
    return store


def _ngram_meta_count(store: MetadataStore) -> int:
    row = store.query(
        "SELECT value FROM paragraph_ngram_meta WHERE key='paragraph_count'",
    )
    return int(row[0]["value"]) if row else 0


def test_ngram_incremental_add_soft_delete_and_revive(tmp_path: Path) -> None:
    store = _new_store(tmp_path)
    try:
        first_hash = store.add_paragraph("艾宝 喜欢 三文鱼", source="test")
        assert store.ensure_paragraph_ngram_backfilled(n=2)
        assert _ngram_meta_count(store) == 1

        second_hash = store.add_paragraph("三文鱼 k77 新话题", source="test")
        assert _ngram_meta_count(store) == 2
        rows = store.ngram_search_paragraphs(["三文"], limit=10)
        assert {row["hash"] for row in rows} == {first_hash, second_hash}

        assert store.mark_as_deleted([second_hash], "paragraph", reason="test_soft_delete") == 1
        assert _ngram_meta_count(store) == 1
        rows = store.ngram_search_paragraphs(["k7"], limit=10)
        assert second_hash not in {row["hash"] for row in rows}

        assert store.revive_if_deleted(paragraph_hashes=[second_hash]) == 1
        assert _ngram_meta_count(store) == 2
        rows = store.ngram_search_paragraphs(["k7"], limit=10)
        assert second_hash in {row["hash"] for row in rows}
    finally:
        store.close()


def test_ngram_incremental_physical_delete_paths(tmp_path: Path) -> None:
    store = _new_store(tmp_path)
    try:
        direct_hash = store.add_paragraph("直接删除 测试文本", source="test")
        atomic_hash = store.add_paragraph("原子删除 测试文本", source="test")
        batch_hash = store.add_paragraph("批量删除 测试文本", source="test")
        soft_then_physical_hash = store.add_paragraph("软删后物理删除 测试文本", source="test")
        assert store.ensure_paragraph_ngram_backfilled(n=2)
        assert _ngram_meta_count(store) == 4

        assert store.physically_delete_paragraphs([direct_hash]) == 1
        assert _ngram_meta_count(store) == 3
        assert direct_hash not in {row["hash"] for row in store.ngram_search_paragraphs(["直接"], limit=10)}

        assert store.physically_delete_paragraphs([atomic_hash]) == 1
        assert _ngram_meta_count(store) == 2
        assert atomic_hash not in {row["hash"] for row in store.ngram_search_paragraphs(["原子"], limit=10)}

        assert store.physically_delete_paragraphs([batch_hash]) == 1
        assert _ngram_meta_count(store) == 1
        assert batch_hash not in {row["hash"] for row in store.ngram_search_paragraphs(["批量"], limit=10)}

        assert (
            store.mark_as_deleted(
                [soft_then_physical_hash],
                "paragraph",
                reason="test_soft_delete",
            )
            == 1
        )
        assert _ngram_meta_count(store) == 0
        assert store.physically_delete_paragraphs([soft_then_physical_hash]) == 1
        assert _ngram_meta_count(store) == 0
        assert soft_then_physical_hash not in {row["hash"] for row in store.ngram_search_paragraphs(["软删"], limit=10)}
    finally:
        store.close()


def test_sparse_index_does_not_backfill_ngram_on_read_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _new_store(tmp_path)
    index = None
    try:
        store.add_paragraph("没有预热的 ngram fallback 文本", source="test")

        def fail_backfill(*args, **kwargs):
            raise AssertionError("read path must not call full ngram backfill")

        monkeypatch.setattr(store, "ensure_paragraph_ngram_backfilled", fail_backfill)
        index = SparseBM25Index(
            metadata_store=store,
            config=SparseBM25Config(enable_like_fallback=False),
        )

        assert index.ensure_loaded()
        assert index.search("完全不存在的检索词", k=3) == []
    finally:
        if index is not None:
            index.unload()
        store.close()


def test_ngram_ready_returns_false_for_corrupt_paragraph_count(tmp_path: Path) -> None:
    store = _new_store(tmp_path)
    try:
        store.add_paragraph("损坏元数据 容错 文本", source="test")
        assert store.ensure_paragraph_ngram_backfilled(n=2)
        store.query(
            "UPDATE paragraph_ngram_meta SET value = ? WHERE key = 'paragraph_count'",
            ("not-a-number",),
        )

        assert store.is_paragraph_ngram_ready(n=2) is False
    finally:
        store.close()
