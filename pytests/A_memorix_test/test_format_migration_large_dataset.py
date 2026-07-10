from __future__ import annotations

from pathlib import Path
from typing import Iterable

import json
import os
import pickle
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest

from src.A_memorix.core.storage.format_migration import run_startup_format_migration


pytestmark = pytest.mark.skipif(
    os.getenv("A_MEMORIX_RUN_LARGE_MIGRATION_TEST") != "1",
    reason="设置 A_MEMORIX_RUN_LARGE_MIGRATION_TEST=1 后运行大规模虚拟迁移压测",
)


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    return max(1, int(raw))


def _batched_range(total: int, batch_size: int) -> Iterable[range]:
    for start in range(0, total, batch_size):
        yield range(start, min(total, start + batch_size))


def _build_legacy_metadata_db(data_dir: Path, row_count: int) -> None:
    db_path = data_dir / "metadata" / "metadata.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        for table in ("paragraphs", "entities", "relations", "deleted_relations"):
            conn.execute(f"CREATE TABLE {table} (hash TEXT PRIMARY KEY, metadata TEXT)")
            for batch in _batched_range(row_count, 1000):
                rows = [
                    (
                        f"{table}-{index}",
                        pickle.dumps(
                            {
                                "chat_id": f"chat-{index % 128}",
                                "session_id": f"session-{index % 64}",
                                "stream_id": f"stream-{index % 32}",
                                "sequence": index,
                            }
                        ),
                    )
                    for index in batch
                ]
                conn.executemany(f"INSERT INTO {table} (hash, metadata) VALUES (?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def _build_legacy_vector_metadata(vector_dir: Path, vector_count: int) -> None:
    vector_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "dimension": 8,
        "ids": [f"vec-{index}" for index in range(vector_count)],
        "known_hashes": [f"vec-{index}" for index in range(vector_count)],
        "deleted_ids": [],
        "vector_norm": "l2",
    }
    with (vector_dir / "vectors_metadata.pkl").open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)


def _build_legacy_graph_metadata(data_dir: Path, edge_count: int) -> None:
    graph_dir = data_dir / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    nodes = [f"node-{index}" for index in range(edge_count + 1)]
    payload = {
        "nodes": nodes,
        "node_to_idx": {node: index for index, node in enumerate(nodes)},
        "node_attrs": {},
        "matrix_format": "csr",
        "total_nodes_added": len(nodes),
        "total_edges_added": edge_count,
        "total_nodes_deleted": 0,
        "total_edges_deleted": 0,
        "edge_hash_map": {(index, index + 1): {f"rel-{index}"} for index in range(edge_count)},
    }
    with (graph_dir / "graph_metadata.pkl").open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)


def _wait_for_marker_or_fail(proc: subprocess.Popen[str], marker_path: Path, timeout_seconds: int = 300) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if marker_path.exists():
            return
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=5)
            raise AssertionError(f"大规模迁移子进程提前退出: stdout={stdout} stderr={stderr}")
        time.sleep(0.1)
    proc.kill()
    stdout, stderr = proc.communicate(timeout=10)
    raise AssertionError(f"大规模迁移子进程未进入待 kill 断点: stdout={stdout} stderr={stderr}")


def _run_killed_large_migration_before_commit(data_dir: Path, marker_path: Path) -> float:
    child_code = textwrap.dedent(
        """
        from pathlib import Path

        import sys
        import time

        from src.A_memorix.core.storage import format_migration as fm

        data_dir = Path(sys.argv[1])
        marker_path = Path(sys.argv[2])
        original_connect = fm._connect_metadata_db

        class ConnectionProxy:
            def __init__(self, conn):
                self._conn = conn

            def __getattr__(self, name):
                return getattr(self._conn, name)

            def execute(self, sql, parameters=()):
                result = self._conn.execute(sql, parameters)
                if "storage_format_migrations" in str(sql) and "INSERT OR REPLACE" in str(sql):
                    marker_path.write_text("record-written-before-commit", encoding="utf-8")
                    time.sleep(600)
                return result

        def wrapped_connect(db_path):
            return ConnectionProxy(original_connect(db_path))

        fm._connect_metadata_db = wrapped_connect
        fm.run_startup_format_migration(data_dir)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", child_code, str(data_dir), str(marker_path)],
        cwd=Path.cwd(),
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    started = time.perf_counter()
    try:
        _wait_for_marker_or_fail(proc, marker_path)
        elapsed = time.perf_counter() - started
        proc.kill()
        proc.communicate(timeout=10)
        return elapsed
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)


def test_large_virtual_startup_format_migration_benchmark(tmp_path: Path) -> None:
    row_count = _env_int("A_MEMORIX_LARGE_MIGRATION_ROWS", 50000)
    vector_count = _env_int("A_MEMORIX_LARGE_MIGRATION_VECTORS", row_count)
    edge_count = _env_int("A_MEMORIX_LARGE_MIGRATION_EDGES", row_count)
    data_dir = tmp_path / "a_memorix_large_data"

    build_started = time.perf_counter()
    _build_legacy_metadata_db(data_dir, row_count)
    _build_legacy_vector_metadata(data_dir / "vectors", vector_count)
    _build_legacy_vector_metadata(data_dir / "vectors" / "paragraph", vector_count)
    _build_legacy_vector_metadata(data_dir / "vectors" / "graph", vector_count)
    _build_legacy_graph_metadata(data_dir, edge_count)
    build_elapsed = time.perf_counter() - build_started

    migrate_started = time.perf_counter()
    summary = run_startup_format_migration(data_dir)
    migrate_elapsed = time.perf_counter() - migrate_started

    second_started = time.perf_counter()
    second_summary = run_startup_format_migration(data_dir)
    second_elapsed = time.perf_counter() - second_started

    db_path = data_dir / "metadata" / "metadata.db"
    conn = sqlite3.connect(str(db_path))
    try:
        sample = conn.execute("SELECT metadata FROM paragraphs WHERE hash = 'paragraphs-0'").fetchone()
        edge_rows = conn.execute("SELECT COUNT(*) FROM graph_edge_relation_map").fetchone()
    finally:
        conn.close()

    migrated_rows = row_count * 4
    rows_per_second = migrated_rows / max(migrate_elapsed, 0.001)
    print(
        "\nA_Memorix large migration benchmark: "
        f"rows_per_table={row_count}, migrated_sqlite_rows={migrated_rows}, "
        f"vectors_per_pool={vector_count}, graph_edges={edge_count}, "
        f"build_s={build_elapsed:.3f}, migrate_s={migrate_elapsed:.3f}, "
        f"second_migrate_s={second_elapsed:.3f}, rows_per_s={rows_per_second:.1f}"
    )

    assert summary["sqlite"]["updated"] == migrated_rows
    assert second_summary["sqlite"]["reason"] == "already_applied"
    assert summary["graph"]["edge_hash_map_rows"] == edge_count
    assert all(item["migrated"] is True for item in summary["vectors"])
    assert all(item["reason"] == "legacy_missing" for item in second_summary["vectors"])
    assert json.loads(sample[0])["chat_id"] == "chat-0"
    assert int(edge_rows[0]) == edge_count
    assert not (data_dir / "vectors" / "vectors_metadata.pkl").exists()
    assert not (data_dir / "graph" / "graph_metadata.pkl").exists()


def test_large_virtual_migration_recovers_after_kill_benchmark(tmp_path: Path) -> None:
    row_count = _env_int("A_MEMORIX_LARGE_MIGRATION_ROWS", 50000)
    vector_count = _env_int("A_MEMORIX_LARGE_MIGRATION_VECTORS", row_count)
    edge_count = _env_int("A_MEMORIX_LARGE_MIGRATION_EDGES", row_count)
    data_dir = tmp_path / "a_memorix_large_kill_data"

    build_started = time.perf_counter()
    _build_legacy_metadata_db(data_dir, row_count)
    _build_legacy_vector_metadata(data_dir / "vectors", vector_count)
    _build_legacy_vector_metadata(data_dir / "vectors" / "paragraph", vector_count)
    _build_legacy_vector_metadata(data_dir / "vectors" / "graph", vector_count)
    _build_legacy_graph_metadata(data_dir, edge_count)
    build_elapsed = time.perf_counter() - build_started

    marker_path = tmp_path / "large_migration_record_before_commit.marker"
    killed_migrate_elapsed = _run_killed_large_migration_before_commit(data_dir, marker_path)

    recover_started = time.perf_counter()
    summary = run_startup_format_migration(data_dir)
    recover_elapsed = time.perf_counter() - recover_started

    second_started = time.perf_counter()
    second_summary = run_startup_format_migration(data_dir)
    second_elapsed = time.perf_counter() - second_started

    db_path = data_dir / "metadata" / "metadata.db"
    conn = sqlite3.connect(str(db_path))
    try:
        sample = conn.execute("SELECT metadata FROM paragraphs WHERE hash = 'paragraphs-0'").fetchone()
        edge_rows = conn.execute("SELECT COUNT(*) FROM graph_edge_relation_map").fetchone()
    finally:
        conn.close()

    migrated_rows = row_count * 4
    recover_rows_per_second = migrated_rows / max(recover_elapsed, 0.001)
    print(
        "\nA_Memorix large killed migration recovery benchmark: "
        f"rows_per_table={row_count}, migrated_sqlite_rows={migrated_rows}, "
        f"vectors_per_pool={vector_count}, graph_edges={edge_count}, "
        f"build_s={build_elapsed:.3f}, killed_migrate_s={killed_migrate_elapsed:.3f}, "
        f"recover_s={recover_elapsed:.3f}, second_migrate_s={second_elapsed:.3f}, "
        f"recover_rows_per_s={recover_rows_per_second:.1f}"
    )

    assert summary["sqlite"]["updated"] == migrated_rows
    assert summary["graph"]["recovered"] is True
    assert summary["graph"]["edge_hash_map_rows"] == edge_count
    assert second_summary["sqlite"]["reason"] == "already_applied"
    assert json.loads(sample[0])["chat_id"] == "chat-0"
    assert int(edge_rows[0]) == edge_count
    assert not (data_dir / "vectors" / "vectors_metadata.pkl").exists()
    assert not (data_dir / "graph" / "graph_metadata.pkl").exists()
