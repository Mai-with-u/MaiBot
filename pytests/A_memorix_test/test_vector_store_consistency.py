from __future__ import annotations

from pathlib import Path
import json
import shutil

import numpy as np
import pytest

from src.A_memorix.core.storage.vector_store import HAS_FAISS, VectorStore


pytestmark = pytest.mark.skipif(not HAS_FAISS, reason="Faiss 未安装")


def _vector() -> np.ndarray:
    return np.asarray([[1.0, 0.0]], dtype=np.float32)


def _orthogonal_vectors() -> np.ndarray:
    return np.eye(4, dtype=np.float32)


def _assert_unique_search_results(store: VectorStore, *, expected_count: int) -> None:
    ids, _scores = store.search(_orthogonal_vectors()[0], k=expected_count)

    assert len(ids) == expected_count
    assert len(set(ids)) == expected_count


def test_vector_id_map_cache_detects_equal_size_membership_change(tmp_path: Path) -> None:
    store = VectorStore(dimension=2, data_dir=tmp_path / "vectors")
    store._known_hashes = {"old"}
    store._invalidate_id_map()
    old_map = dict(store._int_to_str_map)

    store._known_hashes = {"new"}
    store._invalidate_id_map()

    assert old_map != store._int_to_str_map
    assert set(store._int_to_str_map.values()) == {"new"}


def test_vector_compaction_removes_deleted_hash_and_allows_readd(tmp_path: Path) -> None:
    store = VectorStore(dimension=2, data_dir=tmp_path / "vectors", buffer_size=1)
    assert store.add(_vector(), ["relation-1"]) == 1
    assert store.delete(["relation-1"]) == 1

    store.rebuild_index()

    assert "relation-1" not in store
    assert store.add(_vector(), ["relation-1"]) == 1


def test_vector_compaction_journal_restores_consistent_backup(tmp_path: Path) -> None:
    store = VectorStore(dimension=2, data_dir=tmp_path / "vectors", buffer_size=1)
    store.add(_vector(), ["relation-1"])
    original_bin = store._bin_path.read_bytes()
    original_ids = store._ids_bin_path.read_bytes()
    shutil.copy2(store._bin_path, store._bin_backup_path)
    shutil.copy2(store._ids_bin_path, store._ids_backup_path)
    store._bin_path.write_bytes(b"broken")
    store._compaction_journal_path.write_text(
        json.dumps({"expected_count": 2}),
        encoding="utf-8",
    )

    store._recover_interrupted_compaction_unlocked()

    assert store._bin_path.read_bytes() == original_bin
    assert store._ids_bin_path.read_bytes() == original_ids
    assert not store._compaction_journal_path.exists()


def test_untrained_search_flushes_each_vector_to_fallback_once(tmp_path: Path) -> None:
    store = VectorStore(dimension=4, data_dir=tmp_path / "vectors")
    ids = ["vector-1", "vector-2", "vector-3", "vector-4"]

    assert store.add(_orthogonal_vectors(), ids) == 4
    assert store._fallback_index.ntotal == 0

    _assert_unique_search_results(store, expected_count=4)

    assert store._fallback_index.ntotal == 4
    assert store._bin_count == 4


def test_untrained_save_and_repeated_search_do_not_grow_fallback(tmp_path: Path) -> None:
    store = VectorStore(dimension=4, data_dir=tmp_path / "vectors")
    ids = ["vector-1", "vector-2", "vector-3", "vector-4"]
    store.add(_orthogonal_vectors(), ids)

    store.save()
    assert store._fallback_index.ntotal == 4
    assert store._bin_count == 4

    store.save()
    _assert_unique_search_results(store, expected_count=4)

    assert store._fallback_index.ntotal == 4
    assert store._bin_count == 4


def test_buffer_threshold_and_search_do_not_duplicate_untrained_vector(tmp_path: Path) -> None:
    store = VectorStore(dimension=2, data_dir=tmp_path / "vectors", buffer_size=1)

    assert store.add(_vector(), ["vector-1"]) == 1
    assert store._fallback_index.ntotal == 1

    ids, _scores = store.search(_vector()[0], k=1)

    assert ids == ["vector-1"]
    assert store._fallback_index.ntotal == 1
    assert store._bin_count == 1


def test_training_transition_keeps_one_index_entry_per_vector(tmp_path: Path) -> None:
    store = VectorStore(dimension=4, data_dir=tmp_path / "vectors")
    store.min_train_threshold = 4
    ids = ["vector-1", "vector-2", "vector-3", "vector-4"]
    store.add(_orthogonal_vectors(), ids)

    summary = store.warmup_index(force_train=True)

    assert summary["ok"] is True
    assert summary["trained"] is True
    assert summary["index_ntotal"] == 4
    assert summary["fallback_ntotal"] == 0
    _assert_unique_search_results(store, expected_count=4)
    assert store._index.ntotal == 4
