from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

try:
    from src.A_memorix.core.storage.graph_store import GraphStore
except SystemExit as exc:
    GraphStore = None  # type: ignore[assignment]
    IMPORT_ERROR = f"config initialization exited during import: {exc}"
else:
    IMPORT_ERROR = None


pytestmark = pytest.mark.skipif(IMPORT_ERROR is not None, reason=IMPORT_ERROR or "")


def _build_empty_graph_metadata() -> dict:
    return {
        "nodes": [],
        "node_to_idx": {},
        "node_attrs": {},
        "matrix_format": "csr",
        "total_nodes_added": 0,
        "total_edges_added": 0,
        "total_nodes_deleted": 0,
        "total_edges_deleted": 0,
        "schema_version": 1,
    }


def test_graph_store_clear_save_removes_stale_adjacency(tmp_path: Path) -> None:
    data_dir = tmp_path / "graph_data"
    store = GraphStore(data_dir=data_dir)
    store.add_edges([("Alice", "Bob")], relation_hashes=["rel-1"])
    store.save()

    matrix_path = data_dir / "graph_adjacency.npz"
    assert matrix_path.exists()

    store.clear()
    store.save()

    assert not matrix_path.exists()


def test_graph_store_load_resets_stale_adjacency_when_metadata_is_empty(tmp_path: Path) -> None:
    data_dir = tmp_path / "graph_data"
    store = GraphStore(data_dir=data_dir)
    store.add_edges([("Alice", "Bob")], relation_hashes=["rel-1"])
    store.save()

    pointer = json.loads((data_dir / "graph_snapshot.json").read_text(encoding="utf-8"))
    metadata_path = data_dir / "graph_snapshots" / pointer["generation"] / "graph_metadata.json"
    metadata_path.write_text(json.dumps(_build_empty_graph_metadata()), encoding="utf-8")

    reloaded = GraphStore(data_dir=data_dir)
    reloaded.load()

    assert reloaded.num_nodes == 0
    assert reloaded.num_edges == 0
    assert reloaded.get_nodes() == []


def test_graph_store_load_clears_stale_edge_hash_map_when_metadata_is_empty(tmp_path: Path) -> None:
    data_dir = tmp_path / "graph_data"
    store = GraphStore(data_dir=data_dir)
    store.add_edges([("Alice", "Bob")], relation_hashes=["rel-1"])
    store.save()

    store.clear()
    store.save()

    reloaded = GraphStore(data_dir=data_dir)
    reloaded.load()

    assert reloaded.has_edge_hash_map() is False


def test_graph_store_save_uses_sqlite_edge_map_when_metadata_db_exists(tmp_path: Path) -> None:
    data_dir = tmp_path / "graph"
    db_path = tmp_path / "metadata" / "metadata.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(str(db_path)).close()

    store = GraphStore(data_dir=data_dir)
    store.add_edges([("Alice", "Bob")], relation_hashes=["rel-1"])
    store.save()

    graph_metadata = json.loads((data_dir / "graph_metadata.json").read_text(encoding="utf-8"))
    assert "edge_hash_map" not in graph_metadata

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT src_idx, dst_idx, relation_hash FROM graph_edge_relation_map").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][2] == "rel-1"


def test_graph_store_rejects_relation_hash_length_mismatch_before_mutation(tmp_path: Path) -> None:
    store = GraphStore(data_dir=tmp_path / "graph")

    with pytest.raises(ValueError, match="关系哈希数量不匹配"):
        store.add_edges([("Alice", "Bob"), ("Bob", "Carol")], relation_hashes=["rel-1"])

    assert store.num_nodes == 0
    assert store.num_edges == 0


def test_graph_store_incremental_add_invalidates_transpose_and_saliency(tmp_path: Path) -> None:
    store = GraphStore(data_dir=tmp_path / "graph")
    store.add_edges([("Alice", "Bob")], relation_hashes=["rel-1"])
    store._ensure_adjacency_T()
    store.get_saliency_scores()
    assert store._adjacency_T is not None
    assert store._saliency_cache is not None

    with store.batch_update():
        store.add_edges([("Carol", "Alice")], relation_hashes=["rel-2"])
        assert store._adjacency_dirty is True
        assert store._saliency_cache is None

    assert "Carol" in store.get_in_neighbors("Alice")


def test_graph_store_low_weight_edges_keep_csc_coordinates_aligned(tmp_path: Path) -> None:
    store = GraphStore(matrix_format="csc", data_dir=tmp_path / "graph")
    store.add_edges(
        [("Alice", "Bob"), ("Carol", "Alice")],
        weights=[0.8, 0.05],
        relation_hashes=["rel-1", "rel-2"],
    )

    assert store.get_low_weight_edges(0.1) == [("Carol", "Alice")]


def test_graph_store_failed_mirror_save_does_not_activate_partial_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "graph"
    store = GraphStore(data_dir=data_dir)
    store.add_edges([("Alice", "Bob")], relation_hashes=["rel-1"])
    store.save()
    active_before = json.loads((data_dir / "graph_snapshot.json").read_text(encoding="utf-8"))["generation"]

    store.add_edges([("Bob", "Carol")], relation_hashes=["rel-2"])

    def fail_edge_map(_data_dir: Path) -> bool:
        raise sqlite3.OperationalError("forced edge map failure")

    monkeypatch.setattr(store, "_save_edge_hash_map", fail_edge_map)
    with pytest.raises(sqlite3.OperationalError, match="forced edge map failure"):
        store.save()

    active_after = json.loads((data_dir / "graph_snapshot.json").read_text(encoding="utf-8"))["generation"]
    reloaded = GraphStore(data_dir=data_dir)
    reloaded.load()

    assert active_after == active_before
    assert reloaded.num_edges == 1


def test_graph_store_cleanup_failure_does_not_mask_activated_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "graph"
    store = GraphStore(data_dir=data_dir)
    store.add_edges([("Alice", "Bob")], relation_hashes=["rel-1"])

    def fail_cleanup(_data_dir: Path, _generation: str) -> None:
        raise OSError("forced cleanup failure")

    monkeypatch.setattr(store, "_cleanup_old_snapshots", fail_cleanup)

    store.save()

    reloaded = GraphStore(data_dir=data_dir)
    reloaded.load()
    assert reloaded.num_edges == 1
