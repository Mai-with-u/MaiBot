from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import shutil

import numpy as np
import pytest

from src.A_memorix.core.storage import vector_store as vector_store_module
from src.A_memorix.core.storage.vector_store import HAS_FAISS, VectorStore


pytestmark = pytest.mark.skipif(not HAS_FAISS, reason="Faiss 未安装")


class _SimulatedProcessExit(BaseException):
    pass


def _vector() -> np.ndarray:
    return np.asarray([[1.0, 0.0]], dtype=np.float32)


def _second_vector() -> np.ndarray:
    return np.asarray([[0.0, 1.0]], dtype=np.float32)


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
    store.save()
    original_bin = store._bin_path.read_bytes()
    original_ids = store._ids_bin_path.read_bytes()
    shutil.copy2(store._bin_path, store._bin_backup_path)
    shutil.copy2(store._ids_bin_path, store._ids_backup_path)
    store._bin_path.write_bytes(b"broken")
    store._compaction_journal_path.write_text(
        json.dumps(
            {
                "version": 2,
                "transaction_id": "interrupted-compaction",
                "base_vector_bytes": len(original_bin),
                "base_id_bytes": len(original_ids),
                "target_vector_bytes": 8,
                "target_id_bytes": 16,
            }
        ),
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


def test_runtime_training_threshold_respects_store_minimum(tmp_path: Path) -> None:
    store = VectorStore(dimension=4, data_dir=tmp_path / "vectors")
    store.add(_orthogonal_vectors(), ["vector-1", "vector-2", "vector-3", "vector-4"])

    assert store.needs_training(4) is False

    store.min_train_threshold = 4
    assert store.needs_training(4) is True

    summary = store.warmup_index(force_train=True)

    assert summary["trained"] is True
    assert store.needs_training(4) is False


def test_add_rejects_tombstoned_id_without_mutating_store(tmp_path: Path) -> None:
    store = VectorStore(dimension=2, data_dir=tmp_path / "vectors", buffer_size=1)
    assert store.add(_vector(), ["relation-1"]) == 1
    assert store.delete(["relation-1"]) == 1
    bin_before = store._bin_path.read_bytes()
    ids_before = store._ids_bin_path.read_bytes()
    deleted_before = set(store._deleted_ids)
    known_before = set(store._known_hashes)

    with pytest.raises(ValueError, match="restore"):
        store.add(_second_vector(), ["relation-1"])

    assert store._bin_path.read_bytes() == bin_before
    assert store._ids_bin_path.read_bytes() == ids_before
    assert store._deleted_ids == deleted_before
    assert store._known_hashes == known_before
    assert store._bin_count == 1
    assert store._fallback_index.ntotal == 0
    assert "relation-1" not in store


def test_restore_reuses_persisted_vector_without_duplicate_or_disk_growth(tmp_path: Path) -> None:
    store = VectorStore(dimension=2, data_dir=tmp_path / "vectors", buffer_size=1)
    assert store.add(_vector(), ["relation-1"]) == 1
    assert store.delete(["relation-1"]) == 1
    bin_size_before = store._bin_path.stat().st_size
    ids_size_before = store._ids_bin_path.stat().st_size

    assert store.restore(["relation-1"]) == 1

    assert "relation-1" in store
    assert store._bin_count == 1
    assert store._bin_path.stat().st_size == bin_size_before
    assert store._ids_bin_path.stat().st_size == ids_size_before
    assert store._fallback_index.ntotal == 1
    ids, _scores = store.search(_vector()[0], k=2)
    assert ids == ["relation-1"]
    np.testing.assert_allclose(store.get_vectors(["relation-1"])["relation-1"], _vector()[0])

    # 已恢复 ID 再次执行 restore 是无副作用的幂等操作。
    assert store.restore(["relation-1", "relation-1"]) == 0
    assert store._bin_count == 1
    assert store._fallback_index.ntotal == 1


def test_restored_vector_survives_save_and_reload_without_duplicate(tmp_path: Path) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.add(_vector(), ["relation-1"])
    store.delete(["relation-1"])
    assert store.restore(["relation-1"]) == 1
    store.save()

    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    reloaded.load()
    summary = reloaded.warmup_index(force_train=False)

    assert summary["bin_count"] == 1
    assert summary["fallback_ntotal"] == 1
    assert "relation-1" in reloaded
    ids, _scores = reloaded.search(_vector()[0], k=2)
    assert ids == ["relation-1"]
    np.testing.assert_allclose(reloaded.get_vectors(["relation-1"])["relation-1"], _vector()[0])


def test_restore_returns_missing_after_compaction_and_allows_clean_add(tmp_path: Path) -> None:
    store = VectorStore(dimension=2, data_dir=tmp_path / "vectors", buffer_size=1)
    store.add(_vector(), ["relation-1"])
    store.delete(["relation-1"])
    store.rebuild_index()

    assert store.restore(["relation-1"]) == 0
    assert "relation-1" not in store
    assert store._bin_count == 0

    assert store.add(_second_vector(), ["relation-1"]) == 1
    assert store._bin_count == 1
    assert "relation-1" in store
    ids, _scores = store.search(_second_vector()[0], k=2)
    assert ids == ["relation-1"]


def test_restore_index_failure_keeps_tombstone_and_removes_partial_entry(tmp_path: Path) -> None:
    class FailAfterWriteIndex:
        def __init__(self, delegate: Any) -> None:
            self.delegate = delegate

        @property
        def ntotal(self) -> int:
            return int(self.delegate.ntotal)

        def remove_ids(self, ids: np.ndarray) -> Any:
            return self.delegate.remove_ids(ids)

        def add_with_ids(self, vectors: np.ndarray, ids: np.ndarray) -> None:
            self.delegate.add_with_ids(vectors, ids)
            raise RuntimeError("injected restore failure")

    store = VectorStore(dimension=2, data_dir=tmp_path / "vectors", buffer_size=1)
    store.add(_vector(), ["relation-1"])
    store.delete(["relation-1"])
    store._fallback_index = FailAfterWriteIndex(store._fallback_index)

    with pytest.raises(RuntimeError, match="injected restore failure"):
        store.restore(["relation-1"])

    assert "relation-1" not in store
    assert store.num_vectors == 0
    assert store._generate_id("relation-1") in store._deleted_ids
    assert store._fallback_index.ntotal == 0
    assert store._bin_count == 1


def test_append_pair_second_file_failure_rolls_back_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=16)
    store.add(_vector(), ["vector-1"])
    store.save()
    committed_vector_bytes = store._bin_path.stat().st_size
    committed_id_bytes = store._ids_bin_path.stat().st_size

    original_sync_append = store._sync_append

    def fail_id_append(path: Path, payload: bytes) -> None:
        if path == store._ids_bin_path:
            raise OSError("injected ID append failure")
        original_sync_append(path, payload)

    monkeypatch.setattr(store, "_sync_append", fail_id_append)
    store.add(_second_vector(), ["vector-2"])
    with pytest.raises(OSError, match="injected ID append failure"):
        store.save()

    assert store._bin_path.stat().st_size == committed_vector_bytes
    assert store._ids_bin_path.stat().st_size == committed_id_bytes
    assert store._write_buffer_ids == [store._generate_id("vector-2")]
    assert store._bin_count == 1
    assert not store._append_journal_path.exists()

    monkeypatch.setattr(store, "_sync_append", original_sync_append)
    store.save()

    assert store._vector_pair_matches(store._bin_path, store._ids_bin_path, expected_count=2)
    assert store._bin_path.stat().st_size == committed_vector_bytes + 4
    assert store._ids_bin_path.stat().st_size == committed_id_bytes + 8
    ids, _scores = store.search(_second_vector()[0], k=2)
    assert ids[0] == "vector-2"
    assert store.delete(["vector-2"]) == 1
    assert store.restore(["vector-2"]) == 1
    assert store.search(_second_vector()[0], k=2)[0][0] == "vector-2"


def test_load_rolls_back_process_exit_between_pair_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=16)
    store.add(_vector(), ["vector-1"])
    store.save()
    committed_vector_bytes = store._bin_path.stat().st_size
    committed_id_bytes = store._ids_bin_path.stat().st_size
    original_sync_append = store._sync_append

    def exit_during_id_append(path: Path, payload: bytes) -> None:
        if path == store._ids_bin_path:
            raise _SimulatedProcessExit()
        original_sync_append(path, payload)

    monkeypatch.setattr(store, "_sync_append", exit_during_id_append)
    store.add(_second_vector(), ["vector-2"])
    with pytest.raises(_SimulatedProcessExit):
        store.save()

    assert store._bin_path.stat().st_size == committed_vector_bytes + 4
    assert store._ids_bin_path.stat().st_size == committed_id_bytes
    assert store._append_journal_path.exists()

    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=16)
    reloaded.load()
    reloaded.warmup_index(force_train=False)

    assert reloaded._bin_path.stat().st_size == committed_vector_bytes
    assert reloaded._ids_bin_path.stat().st_size == committed_id_bytes
    assert not reloaded._append_journal_path.exists()
    assert "vector-1" in reloaded
    assert "vector-2" not in reloaded
    assert reloaded.search(_vector()[0], k=2)[0] == ["vector-1"]
    assert reloaded.delete(["vector-1"]) == 1
    assert reloaded.restore(["vector-1"]) == 1


def test_load_rolls_back_complete_pair_when_metadata_was_not_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=16)
    store.add(_vector(), ["vector-1"])
    store.save()
    committed_vector_bytes = store._bin_path.stat().st_size
    committed_id_bytes = store._ids_bin_path.stat().st_size
    original_write_json = vector_store_module._write_json_object

    def exit_before_metadata_commit(path: Path, payload: dict[str, Any]) -> None:
        if path.name == "vectors_metadata.json":
            raise _SimulatedProcessExit()
        original_write_json(path, payload)

    monkeypatch.setattr(vector_store_module, "_write_json_object", exit_before_metadata_commit)
    store.add(_second_vector(), ["vector-2"])
    with pytest.raises(_SimulatedProcessExit):
        store.save()

    assert store._vector_pair_matches(store._bin_path, store._ids_bin_path, expected_count=2)
    assert store._append_journal_path.exists()

    monkeypatch.setattr(vector_store_module, "_write_json_object", original_write_json)
    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=16)
    reloaded.load()
    reloaded.warmup_index(force_train=False)

    assert reloaded._bin_path.stat().st_size == committed_vector_bytes
    assert reloaded._ids_bin_path.stat().st_size == committed_id_bytes
    assert "vector-1" in reloaded
    assert "vector-2" not in reloaded
    assert reloaded.search(_vector()[0], k=2)[0] == ["vector-1"]


def test_load_keeps_pair_when_metadata_commit_precedes_journal_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=16)
    store.add(_vector(), ["vector-1"])
    store.save()
    store.add(_second_vector(), ["vector-2"])

    def exit_before_journal_removal() -> None:
        raise _SimulatedProcessExit()

    monkeypatch.setattr(store, "_finalize_append_commit_unlocked", exit_before_journal_removal)
    with pytest.raises(_SimulatedProcessExit):
        store.save()

    assert store._vector_pair_matches(store._bin_path, store._ids_bin_path, expected_count=2)
    assert store._append_journal_path.exists()

    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=16)
    reloaded.load()
    reloaded.warmup_index(force_train=False)

    assert reloaded._vector_pair_matches(reloaded._bin_path, reloaded._ids_bin_path, expected_count=2)
    assert not reloaded._append_journal_path.exists()
    assert "vector-1" in reloaded
    assert "vector-2" in reloaded
    assert reloaded.search(_second_vector()[0], k=2)[0][0] == "vector-2"


def test_consecutive_flushes_share_transaction_and_second_failure_keeps_first_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    assert store.add(_vector(), ["vector-1"]) == 1
    first_journal = json.loads(store._append_journal_path.read_text(encoding="utf-8"))
    original_sync_append = store._sync_append

    def fail_second_id_append(path: Path, payload: bytes) -> None:
        if path == store._ids_bin_path:
            raise OSError("injected second batch failure")
        original_sync_append(path, payload)

    monkeypatch.setattr(store, "_sync_append", fail_second_id_append)
    with pytest.raises(OSError, match="injected second batch failure"):
        store.add(_second_vector(), ["vector-2"])

    after_failure = json.loads(store._append_journal_path.read_text(encoding="utf-8"))
    assert after_failure["transaction_id"] == first_journal["transaction_id"]
    assert after_failure["target_vector_bytes"] == first_journal["target_vector_bytes"]
    assert after_failure["target_id_bytes"] == first_journal["target_id_bytes"]
    assert store._vector_pair_matches(store._bin_path, store._ids_bin_path, expected_count=1)
    assert store._fallback_index.ntotal == 1
    assert store._write_buffer_ids == [store._generate_id("vector-2")]

    monkeypatch.setattr(store, "_sync_append", original_sync_append)
    store.save()

    assert not store._append_journal_path.exists()
    assert store._vector_pair_matches(store._bin_path, store._ids_bin_path, expected_count=2)
    assert store._fallback_index.ntotal == 2
    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    reloaded.load()
    reloaded.warmup_index(force_train=False)
    assert set(reloaded.search(np.asarray([1.0, 1.0], dtype=np.float32), k=2)[0]) == {
        "vector-1",
        "vector-2",
    }


def test_compaction_commits_pending_append_before_rewriting_pair(tmp_path: Path) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.add(_vector(), ["vector-1"])
    store.add(_second_vector(), ["vector-2"])
    assert store._append_journal_path.exists()
    assert store.delete(["vector-1"]) == 1

    store.rebuild_index()

    assert not store._append_journal_path.exists()
    assert store._vector_pair_matches(store._bin_path, store._ids_bin_path, expected_count=1)
    assert "vector-1" not in store
    assert "vector-2" in store
    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    reloaded.load()
    reloaded.warmup_index(force_train=False)
    assert "vector-1" not in reloaded
    assert "vector-2" in reloaded
    assert reloaded.search(_second_vector()[0], k=2)[0] == ["vector-2"]


@pytest.mark.parametrize(
    ("vector_bytes", "id_bytes"),
    [
        (b"\x00" * 4, b""),
        (b"\x00" * 8, b"\x00" * 8),
        (b"\x00", b"\x00" * 8),
    ],
)
def test_load_rejects_unjournaled_vector_pair_mismatch(
    tmp_path: Path,
    vector_bytes: bytes,
    id_bytes: bytes,
) -> None:
    data_dir = tmp_path / "vectors"
    data_dir.mkdir()
    (data_dir / "vectors.bin").write_bytes(vector_bytes)
    (data_dir / "vectors_ids.bin").write_bytes(id_bytes)
    store = VectorStore(dimension=2, data_dir=data_dir)

    with pytest.raises(RuntimeError, match="不完整记录|记录数不一致"):
        store.load()


def test_load_rejects_unjournaled_missing_pair_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "vectors"
    data_dir.mkdir()
    (data_dir / "vectors.bin").write_bytes(b"\x00" * 4)
    store = VectorStore(dimension=2, data_dir=data_dir)

    with pytest.raises(RuntimeError, match="必须成对存在"):
        store.load()


def test_load_rebuilds_trained_index_when_id_multiset_is_from_wrong_generation(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.min_train_threshold = 1
    store.add(_vector(), ["vector-1"])
    assert store.warmup_index(force_train=True)["trained"] is True
    store.save()

    wrong_index = vector_store_module.faiss.IndexIDMap2(vector_store_module.faiss.IndexFlatIP(2))
    wrong_index.add_with_ids(
        _second_vector(),
        np.asarray([store._generate_id("vector-2")], dtype=np.int64),
    )
    vector_store_module.faiss.write_index(wrong_index, str(data_dir / "vectors.index"))

    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    reloaded.load()

    assert reloaded._faiss_id_multiset(reloaded._index) == {
        reloaded._generate_id("vector-1"): 1,
    }
    assert reloaded.search(_vector()[0], k=2)[0] == ["vector-1"]
    assert "vector-2" not in reloaded


def test_load_rejects_pair_that_exceeds_v2_metadata_commit_length(tmp_path: Path) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.add(_vector(), ["vector-1"])
    store.save()

    with store._bin_path.open("ab") as vector_file:
        vector_file.write(_second_vector().astype(np.float16).tobytes())
    with store._ids_bin_path.open("ab") as id_file:
        id_file.write(np.asarray([store._generate_id("vector-2")], dtype=">i8").tobytes())

    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    with pytest.raises(RuntimeError, match="提交长度与成对文件不一致"):
        reloaded.load()


def test_compaction_switch_without_metadata_commit_rolls_back_whole_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.add(_vector(), ["vector-1"])
    store.add(_second_vector(), ["vector-2"])
    store.save()
    store.delete(["vector-1"])
    original_write_json = vector_store_module._write_json_object

    def exit_after_compaction_switch(path: Path, payload: dict[str, Any]) -> None:
        if path.name == "vectors_metadata.json" and store._compaction_journal_path.exists():
            raise _SimulatedProcessExit()
        original_write_json(path, payload)

    monkeypatch.setattr(vector_store_module, "_write_json_object", exit_after_compaction_switch)
    with pytest.raises(_SimulatedProcessExit):
        store.rebuild_index()

    assert store._vector_pair_matches(store._bin_path, store._ids_bin_path, expected_count=1)
    assert store._compaction_journal_path.exists()
    assert store._bin_backup_path.exists()
    assert store._ids_backup_path.exists()

    monkeypatch.setattr(vector_store_module, "_write_json_object", original_write_json)
    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    reloaded.load()
    reloaded.warmup_index(force_train=False)

    # canonical metadata 未提交，整个 pair 回到压缩前的 2 条；
    # tombstone 已在切换前持久化，所以活动索引仍只有 vector-2。
    assert reloaded._vector_pair_matches(reloaded._bin_path, reloaded._ids_bin_path, expected_count=2)
    assert not reloaded._compaction_journal_path.exists()
    assert "vector-1" not in reloaded
    assert "vector-2" in reloaded
    assert reloaded.search(_second_vector()[0], k=2)[0] == ["vector-2"]


def test_compaction_backup_recovery_is_reentrant_after_first_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.add(_vector(), ["vector-1"])
    store.add(_second_vector(), ["vector-2"])
    store.save()
    store.delete(["vector-1"])
    original_write_json = vector_store_module._write_json_object

    def exit_after_compaction_switch(path: Path, payload: dict[str, Any]) -> None:
        if path.name == "vectors_metadata.json" and store._compaction_journal_path.exists():
            raise _SimulatedProcessExit()
        original_write_json(path, payload)

    monkeypatch.setattr(vector_store_module, "_write_json_object", exit_after_compaction_switch)
    with pytest.raises(_SimulatedProcessExit):
        store.rebuild_index()
    monkeypatch.setattr(vector_store_module, "_write_json_object", original_write_json)

    first_recovery = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)

    def exit_after_first_recovery_replace() -> None:
        os.replace(first_recovery._bin_recovery_tmp_path, first_recovery._bin_path)
        raise _SimulatedProcessExit()

    monkeypatch.setattr(
        first_recovery,
        "_replace_compaction_recovery_pair_unlocked",
        exit_after_first_recovery_replace,
    )
    with pytest.raises(_SimulatedProcessExit):
        first_recovery.load()

    assert first_recovery._compaction_journal_path.exists()
    assert first_recovery._bin_backup_path.exists()
    assert first_recovery._ids_backup_path.exists()
    assert first_recovery._bin_path.stat().st_size // 4 == 2
    assert first_recovery._ids_bin_path.stat().st_size // 8 == 1

    second_recovery = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    second_recovery.load()
    second_recovery.warmup_index(force_train=False)

    assert second_recovery._vector_pair_matches(
        second_recovery._bin_path,
        second_recovery._ids_bin_path,
        expected_count=2,
    )
    assert not second_recovery._compaction_journal_path.exists()
    assert not second_recovery._bin_backup_path.exists()
    assert not second_recovery._ids_backup_path.exists()
    assert second_recovery.search(_second_vector()[0], k=2)[0] == ["vector-2"]


def test_compaction_metadata_commit_before_cleanup_keeps_canonical_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.add(_vector(), ["vector-1"])
    store.add(_second_vector(), ["vector-2"])
    store.save()
    store.delete(["vector-1"])

    def exit_before_compaction_cleanup() -> None:
        raise _SimulatedProcessExit()

    monkeypatch.setattr(
        store,
        "_finalize_compaction_commit_unlocked",
        exit_before_compaction_cleanup,
    )
    with pytest.raises(_SimulatedProcessExit):
        store.rebuild_index()

    assert store._compaction_journal_path.exists()
    assert store._vector_pair_matches(store._bin_path, store._ids_bin_path, expected_count=1)

    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    reloaded.load()

    assert reloaded._vector_pair_matches(reloaded._bin_path, reloaded._ids_bin_path, expected_count=1)
    assert not reloaded._compaction_journal_path.exists()
    assert not reloaded._bin_backup_path.exists()
    assert not reloaded._ids_backup_path.exists()
    assert "vector-1" not in reloaded
    assert "vector-2" in reloaded
    assert reloaded.search(_second_vector()[0], k=2)[0] == ["vector-2"]


def test_compaction_metadata_oserror_allows_same_instance_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.add(_vector(), ["vector-1"])
    store.add(_second_vector(), ["vector-2"])
    store.save()
    assert store.delete(["vector-1"]) == 1
    original_write_json = vector_store_module._write_json_object
    failure_count = 0

    def fail_first_canonical_metadata(path: Path, payload: dict[str, Any]) -> None:
        nonlocal failure_count
        if (
            path.name == "vectors_metadata.json"
            and store._compaction_journal_path.exists()
            and failure_count == 0
        ):
            failure_count += 1
            raise OSError("injected canonical metadata failure")
        original_write_json(path, payload)

    monkeypatch.setattr(vector_store_module, "_write_json_object", fail_first_canonical_metadata)
    with pytest.raises(OSError, match="injected canonical metadata failure"):
        store.rebuild_index()

    # 第一次失败时内存已是压缩后状态，持久化基线仍为 2 行 + tombstone。
    assert store._bin_count == 1
    assert store._known_hashes == {"vector-2"}
    assert store._deleted_ids == set()
    assert store._compaction_journal_path.exists()

    # 同一实例直接重试：先回滚 pair，再从已提交 metadata 恢复
    # known/deleted/bin_count/index，随后重新压缩并提交。
    store.rebuild_index()

    assert failure_count == 1
    assert store._bin_count == 1
    assert store._known_hashes == {"vector-2"}
    assert store._deleted_ids == set()
    assert store._vector_pair_matches(store._bin_path, store._ids_bin_path, expected_count=1)
    assert store.search(_second_vector()[0], k=2)[0] == ["vector-2"]

    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    reloaded.load()

    assert reloaded._bin_count == 1
    assert reloaded._known_hashes == {"vector-2"}
    assert reloaded._deleted_ids == set()
    assert reloaded.search(_second_vector()[0], k=2)[0] == ["vector-2"]


def test_compaction_runtime_reload_failure_keeps_transaction_for_same_instance_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "vectors"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.add(_vector(), ["vector-1"])
    store.add(_second_vector(), ["vector-2"])
    store.save()
    assert store.delete(["vector-1"]) == 1
    original_write_json = vector_store_module._write_json_object
    metadata_failure_count = 0

    def fail_first_canonical_metadata(path: Path, payload: dict[str, Any]) -> None:
        nonlocal metadata_failure_count
        if (
            path.name == "vectors_metadata.json"
            and store._compaction_journal_path.exists()
            and metadata_failure_count == 0
        ):
            metadata_failure_count += 1
            raise OSError("injected canonical metadata failure")
        original_write_json(path, payload)

    monkeypatch.setattr(vector_store_module, "_write_json_object", fail_first_canonical_metadata)
    with pytest.raises(OSError, match="injected canonical metadata failure"):
        store.rebuild_index()

    original_reload_runtime = store._reload_runtime_after_compaction_rollback_unlocked
    reload_failure_count = 0

    def fail_first_runtime_reload() -> None:
        nonlocal reload_failure_count
        if reload_failure_count == 0:
            reload_failure_count += 1
            raise OSError("injected runtime reload failure")
        original_reload_runtime()

    monkeypatch.setattr(
        store,
        "_reload_runtime_after_compaction_rollback_unlocked",
        fail_first_runtime_reload,
    )
    with pytest.raises(OSError, match="injected runtime reload failure"):
        store.rebuild_index()

    # pair 已恢复到基线，但运行时尚未重载；journal 和成对备份不能提前清理。
    assert store._vector_pair_matches(store._bin_path, store._ids_bin_path, expected_count=2)
    assert store._compaction_journal_path.exists()
    assert store._bin_backup_path.exists()
    assert store._ids_backup_path.exists()

    store.rebuild_index()

    assert metadata_failure_count == 1
    assert reload_failure_count == 1
    assert not store._compaction_journal_path.exists()
    assert not store._bin_backup_path.exists()
    assert not store._ids_backup_path.exists()
    assert store._vector_pair_matches(store._bin_path, store._ids_bin_path, expected_count=1)
    assert store._known_hashes == {"vector-2"}
    assert store._deleted_ids == set()
    assert store.search(_second_vector()[0], k=2)[0] == ["vector-2"]

    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    reloaded.load()

    assert reloaded._vector_pair_matches(reloaded._bin_path, reloaded._ids_bin_path, expected_count=1)
    assert reloaded._known_hashes == {"vector-2"}
    assert reloaded._deleted_ids == set()
    assert reloaded.search(_second_vector()[0], k=2)[0] == ["vector-2"]


def test_cleanup_checkpoint_defers_compaction_and_restores_threshold_batch(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "cleanup-checkpoint-threshold"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=2048)
    vector_ids = [f"vector-{index}" for index in range(1002)]
    vectors = np.repeat(_vector(), len(vector_ids), axis=0)
    store.add(vectors, vector_ids)
    store.save()
    baseline_pair_sizes = store._vector_pair_sizes_unlocked()

    checkpoint_token = store.begin_cleanup_checkpoint()
    assert store.delete(vector_ids[:1001]) == 1001
    assert store._cleanup_compaction_deferred is True
    assert store._vector_pair_sizes_unlocked() == baseline_pair_sizes
    with pytest.raises(RuntimeError, match="checkpoint 活动期间禁止手工压缩"):
        store.rebuild_index()

    store.rollback_cleanup_checkpoint(checkpoint_token)

    assert store.num_vectors == len(vector_ids)
    assert store._known_hashes == set(vector_ids)
    assert store._deleted_ids == set()
    assert store._vector_pair_sizes_unlocked() == baseline_pair_sizes
    assert all(vector_id in store for vector_id in vector_ids)


def test_cleanup_checkpoint_commit_validation_failure_can_rollback_and_unlock(
    tmp_path: Path,
) -> None:
    store = VectorStore(dimension=2, data_dir=tmp_path / "checkpoint-commit-validation", buffer_size=1)
    store.add(_vector(), ["vector-1"])
    store.save()
    checkpoint_token = store.begin_cleanup_checkpoint()
    store.delete(["vector-1"])

    with pytest.raises(RuntimeError, match="tombstone 不一致"):
        store.commit_cleanup_checkpoint(checkpoint_token)
    store.rollback_cleanup_checkpoint(checkpoint_token)

    assert "vector-1" in store
    second_checkpoint = store.begin_cleanup_checkpoint()
    store.rollback_cleanup_checkpoint(second_checkpoint)
    assert "vector-1" in store


def test_broken_cleanup_checkpoint_blocks_reads_but_new_instance_can_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "checkpoint-broken-read-guard"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.add(_vector(), ["vector-1"])
    store.save()
    checkpoint_token = store.begin_cleanup_checkpoint()
    store.delete(["vector-1"])

    def fail_rollback_truncate(_path: Path, _size: int) -> None:
        raise OSError("injected checkpoint rollback failure")

    monkeypatch.setattr(store, "_truncate_and_sync", fail_rollback_truncate)
    with pytest.raises(RuntimeError, match="checkpoint 回滚失败"):
        store.rollback_cleanup_checkpoint(checkpoint_token)

    with pytest.raises(RuntimeError, match="禁止继续读写"):
        store.search(_vector()[0], k=1)
    with pytest.raises(RuntimeError, match="禁止继续读写"):
        store.get_vectors(["vector-1"])
    with pytest.raises(RuntimeError, match="禁止继续读写"):
        _ = "vector-1" in store
    with pytest.raises(RuntimeError, match="禁止继续读写"):
        _ = store.num_vectors
    rejected_operations = [
        lambda: store.add(_second_vector(), ["vector-2"]),
        lambda: store.restore(["vector-1"]),
        lambda: store.is_tombstoned("vector-1"),
        lambda: store.delete(["vector-1"]),
        lambda: store.rebuild_index(),
        lambda: store.save(),
        lambda: store.migrate_legacy_npy(),
        lambda: store.load(),
        lambda: store.clear(),
        lambda: store.has_data(),
        lambda: store.needs_training(1),
        lambda: store.warmup_index(force_train=False),
        lambda: list(store.iter_vectors_by_ids(["vector-1"])),
    ]
    for operation in rejected_operations:
        with pytest.raises(RuntimeError, match="禁止继续读写"):
            operation()

    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    reloaded.load()
    reloaded.warmup_index(force_train=False)

    assert "vector-1" in reloaded
    assert reloaded.num_vectors == 1
    assert reloaded.search(_vector()[0], k=1)[0] == ["vector-1"]


def test_clear_removes_committed_state_and_reloads_as_empty(tmp_path: Path) -> None:
    data_dir = tmp_path / "clear-committed-state"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.add(_vector(), ["vector-1"])
    store.save()

    store.clear()

    assert store.has_data() is False
    assert store.num_vectors == 0
    assert not (data_dir / "vectors_metadata.json").exists()
    assert not (data_dir / "vectors.index").exists()
    assert (data_dir / "vectors.bin").stat().st_size == 0
    assert (data_dir / "vectors_ids.bin").stat().st_size == 0

    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    reloaded.load()

    assert reloaded.has_data() is False
    assert reloaded.num_vectors == 0
    assert reloaded.search(_vector()[0], k=1)[0] == []


def test_clear_pair_switch_failure_restores_committed_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "clear-switch-rollback"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.add(_vector(), ["vector-1"])
    store.save()
    original_replace = vector_store_module.os.replace

    def fail_id_pair_switch(source: str | Path, target: str | Path) -> None:
        if Path(source).name == "vectors_ids.bin.tmp":
            raise OSError("injected clear pair switch failure")
        original_replace(source, target)

    monkeypatch.setattr(vector_store_module.os, "replace", fail_id_pair_switch)

    with pytest.raises(OSError, match="injected clear pair switch failure"):
        store.clear()

    assert store.has_data() is True
    assert store.num_vectors == 1
    assert "vector-1" in store
    assert store.search(_vector()[0], k=1)[0] == ["vector-1"]


def test_interrupted_clear_before_metadata_commit_recovers_previous_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "clear-interrupted-before-metadata"
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.add(_vector(), ["vector-1"])
    store.save()
    original_write_json = vector_store_module._write_json_object

    def interrupt_metadata_commit(path: Path, payload: dict[str, Any]) -> None:
        if path.name == "vectors_metadata.json" and store._compaction_journal_path.exists():
            raise _SimulatedProcessExit("injected clear metadata interruption")
        original_write_json(path, payload)

    monkeypatch.setattr(vector_store_module, "_write_json_object", interrupt_metadata_commit)

    with pytest.raises(_SimulatedProcessExit, match="injected clear metadata interruption"):
        store.clear()

    reloaded = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    reloaded.load()

    assert reloaded.has_data() is True
    assert reloaded.num_vectors == 1
    assert "vector-1" in reloaded
    assert reloaded.search(_vector()[0], k=1)[0] == ["vector-1"]


def test_clear_accepts_committed_pair_from_previous_dimension(tmp_path: Path) -> None:
    data_dir = tmp_path / "clear-previous-dimension"
    previous = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    previous.add(_vector(), ["vector-1"])
    previous.save()

    current = VectorStore(dimension=4, data_dir=data_dir, buffer_size=1)
    current.clear()

    assert current.has_data() is False
    assert current.num_vectors == 0
    assert (data_dir / "vectors.bin").stat().st_size == 0
    assert (data_dir / "vectors_ids.bin").stat().st_size == 0

    reloaded = VectorStore(dimension=4, data_dir=data_dir, buffer_size=1)
    reloaded.load()
    assert reloaded.num_vectors == 0
