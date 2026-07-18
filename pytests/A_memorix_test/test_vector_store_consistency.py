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
