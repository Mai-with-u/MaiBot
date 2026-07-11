from __future__ import annotations

from pathlib import Path

import json
import pickle
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest

from src.A_memorix.core.storage.format_migration import run_startup_format_migration


def _create_marker(path: str) -> dict:
    Path(path).write_text("pickle payload executed", encoding="utf-8")
    return {"known_hashes": []}


class _MarkerPayload:
    def __init__(self, marker_path: Path) -> None:
        self.marker_path = marker_path

    def __reduce__(self):
        return _create_marker, (str(self.marker_path),)


def _dump_pickle(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(payload, handle)


def _build_legacy_migration_data(data_dir: Path) -> Path:
    db_path = data_dir / "metadata" / "metadata.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE paragraphs (hash TEXT PRIMARY KEY, metadata TEXT)")
        conn.execute(
            "INSERT INTO paragraphs (hash, metadata) VALUES (?, ?)",
            ("paragraph-recover", pickle.dumps({"chat_id": "chat-recover"})),
        )
        conn.commit()
    finally:
        conn.close()

    for relative, item_id in (
        ("vectors", "vec-main"),
        ("vectors/paragraph", "vec-paragraph"),
        ("vectors/graph", "vec-graph"),
    ):
        _dump_pickle(
            data_dir / relative / "vectors_metadata.pkl",
            {"dimension": 8, "ids": [item_id], "known_hashes": [item_id], "vector_norm": "l2"},
        )
    _dump_pickle(data_dir / "graph" / "graph_metadata.pkl", _legacy_graph_payload("rel-recover"))
    return db_path


def _legacy_graph_payload(relation_hash: str) -> dict:
    return {
        "nodes": ["alice", "bob"],
        "node_to_idx": {"alice": 0, "bob": 1},
        "node_attrs": {},
        "matrix_format": "csr",
        "total_nodes_added": 2,
        "total_edges_added": 1,
        "total_nodes_deleted": 0,
        "total_edges_deleted": 0,
        "edge_hash_map": {(0, 1): {relation_hash}},
    }


def _wait_for_marker_or_fail(proc: subprocess.Popen[str], marker_path: Path) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if marker_path.exists():
            return
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=5)
            raise AssertionError(f"迁移子进程提前退出: stdout={stdout} stderr={stderr}")
        time.sleep(0.05)
    proc.kill()
    stdout, stderr = proc.communicate(timeout=10)
    raise AssertionError(f"迁移子进程未进入待 kill 断点: stdout={stdout} stderr={stderr}")


def _run_killed_migration_at_stage(data_dir: Path, marker_path: Path, stage: str) -> None:
    child_code = textwrap.dedent(
        """
        from pathlib import Path

        import sys
        import time

        from src.A_memorix.core.storage import format_migration as fm

        data_dir = Path(sys.argv[1])
        stage = sys.argv[2]
        marker_path = Path(sys.argv[3])

        def mark_and_sleep():
            marker_path.write_text(stage, encoding="utf-8")
            time.sleep(60)

        if stage == "after_sqlite":
            original = fm._migrate_sqlite_metadata

            def wrapped_sqlite(conn):
                result = original(conn)
                mark_and_sleep()
                return result

            fm._migrate_sqlite_metadata = wrapped_sqlite

        elif stage == "after_vector_json":
            original = fm._write_json
            state = {"hit": False}

            def wrapped_write_json(path, payload):
                result = original(path, payload)
                path = Path(path)
                if not state["hit"] and path.name == "vectors_metadata.json" and path.parent.name == "vectors":
                    state["hit"] = True
                    mark_and_sleep()
                return result

            fm._write_json = wrapped_write_json

        elif stage == "after_vector_backup":
            original = fm._migrate_vector_metadata_dir

            def wrapped_vector(vector_dir):
                result = original(vector_dir)
                if Path(vector_dir).name == "vectors":
                    mark_and_sleep()
                return result

            fm._migrate_vector_metadata_dir = wrapped_vector

        elif stage == "after_graph_json":
            original = fm._write_json
            state = {"hit": False}

            def wrapped_write_json(path, payload):
                result = original(path, payload)
                path = Path(path)
                if not state["hit"] and path.name == "graph_metadata.json":
                    state["hit"] = True
                    mark_and_sleep()
                return result

            fm._write_json = wrapped_write_json

        elif stage == "after_graph_backup":
            original = fm._migrate_graph_metadata

            def wrapped_graph(data_dir, conn):
                result = original(data_dir, conn)
                mark_and_sleep()
                return result

            fm._migrate_graph_metadata = wrapped_graph

        elif stage == "after_migration_record":
            original_connect = fm._connect_metadata_db

            class ConnectionProxy:
                def __init__(self, conn):
                    self._conn = conn

                def __getattr__(self, name):
                    return getattr(self._conn, name)

                def execute(self, sql, parameters=()):
                    result = self._conn.execute(sql, parameters)
                    if "storage_format_migrations" in str(sql) and "INSERT OR REPLACE" in str(sql):
                        mark_and_sleep()
                    return result

            def wrapped_connect(db_path):
                return ConnectionProxy(original_connect(db_path))

            fm._connect_metadata_db = wrapped_connect

        else:
            raise AssertionError(f"unknown stage: {stage}")

        fm.run_startup_format_migration(data_dir)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", child_code, str(data_dir), stage, str(marker_path)],
        cwd=Path.cwd(),
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_marker_or_fail(proc, marker_path)
        proc.kill()
        proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)


def _assert_migrated_data_usable(data_dir: Path, db_path: Path) -> None:
    summary = run_startup_format_migration(data_dir)

    assert (data_dir / "vectors" / "vectors_metadata.json").exists()
    assert (data_dir / "vectors" / "paragraph" / "vectors_metadata.json").exists()
    assert (data_dir / "vectors" / "graph" / "vectors_metadata.json").exists()
    assert (data_dir / "graph" / "graph_metadata.json").exists()

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT metadata FROM paragraphs WHERE hash = 'paragraph-recover'").fetchone()
        assert json.loads(row[0])["chat_id"] == "chat-recover"
        edge_rows = conn.execute("SELECT src_idx, dst_idx, relation_hash FROM graph_edge_relation_map").fetchall()
        assert edge_rows == [(0, 1, "rel-recover")]
        migration_row = conn.execute(
            "SELECT summary_json FROM storage_format_migrations WHERE version = ?",
            ("pickle_to_json_v1",),
        ).fetchone()
        assert migration_row is not None
    finally:
        conn.close()

    second = run_startup_format_migration(data_dir)
    assert second["sqlite"]["reason"] == "already_applied"
    assert summary["sqlite"]["updated"] >= 0


def test_startup_format_migration_converts_pickle_storage(tmp_path: Path) -> None:
    data_dir = tmp_path / "a_memorix_data"
    db_path = data_dir / "metadata" / "metadata.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        for table in ("paragraphs", "entities", "relations", "deleted_relations"):
            conn.execute(f"CREATE TABLE {table} (hash TEXT PRIMARY KEY, metadata TEXT)")
            conn.execute(
                f"INSERT INTO {table} (hash, metadata) VALUES (?, ?)",
                (f"{table}-1", pickle.dumps({"chat_id": "chat-1", "table": table})),
            )
        conn.commit()
    finally:
        conn.close()

    _dump_pickle(
        data_dir / "vectors" / "vectors_metadata.pkl",
        {"dimension": 8, "ids": ["p-1"], "known_hashes": ["p-1"]},
    )
    _dump_pickle(
        data_dir / "vectors" / "paragraph" / "vectors_metadata.pkl",
        {"dimension": 8, "ids": ["p-2"], "known_hashes": ["p-2"]},
    )
    _dump_pickle(
        data_dir / "graph" / "graph_metadata.pkl",
        {
            "nodes": ["alice", "bob"],
            "node_to_idx": {"alice": 0, "bob": 1},
            "node_attrs": {},
            "matrix_format": "csr",
            "total_nodes_added": 2,
            "total_edges_added": 1,
            "total_nodes_deleted": 0,
            "total_edges_deleted": 0,
            "edge_hash_map": {(0, 1): {"rel-1"}},
        },
    )

    summary = run_startup_format_migration(data_dir)

    assert summary["sqlite"]["updated"] == 4
    assert (data_dir / "vectors" / "vectors_metadata.json").exists()
    assert (data_dir / "vectors" / "vectors_metadata.pkl.bak").exists()
    assert (data_dir / "vectors" / "paragraph" / "vectors_metadata.json").exists()
    assert (data_dir / "graph" / "graph_metadata.json").exists()
    assert (data_dir / "graph" / "graph_metadata.pkl.bak").exists()

    vector_meta = json.loads((data_dir / "vectors" / "vectors_metadata.json").read_text(encoding="utf-8"))
    graph_meta = json.loads((data_dir / "graph" / "graph_metadata.json").read_text(encoding="utf-8"))
    assert vector_meta["schema_version"] == 1
    assert "edge_hash_map" not in graph_meta

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT metadata FROM paragraphs WHERE hash = 'paragraphs-1'").fetchone()
        assert json.loads(row[0])["chat_id"] == "chat-1"
        edge_rows = conn.execute("SELECT src_idx, dst_idx, relation_hash FROM graph_edge_relation_map").fetchall()
        assert edge_rows == [(0, 1, "rel-1")]
    finally:
        conn.close()


@pytest.mark.parametrize(
    "stage",
    (
        "after_sqlite",
        "after_vector_json",
        "after_vector_backup",
        "after_graph_json",
        "after_graph_backup",
        "after_migration_record",
    ),
)
def test_startup_format_migration_recovers_after_process_kill_matrix(
    tmp_path: Path,
    stage: str,
) -> None:
    data_dir = tmp_path / f"a_memorix_data_{stage}"
    db_path = _build_legacy_migration_data(data_dir)
    marker_path = tmp_path / f"{stage}.marker"

    _run_killed_migration_at_stage(data_dir, marker_path, stage)

    _assert_migrated_data_usable(data_dir, db_path)


def test_startup_format_migration_recovers_after_process_kill_before_commit(tmp_path: Path) -> None:
    data_dir = tmp_path / "a_memorix_data"
    db_path = data_dir / "metadata" / "metadata.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE paragraphs (hash TEXT PRIMARY KEY, metadata TEXT)")
        conn.execute(
            "INSERT INTO paragraphs (hash, metadata) VALUES (?, ?)",
            ("paragraph-kill", pickle.dumps({"chat_id": "chat-kill"})),
        )
        conn.commit()
    finally:
        conn.close()

    _dump_pickle(
        data_dir / "graph" / "graph_metadata.pkl",
        _legacy_graph_payload("rel-kill"),
    )

    marker_path = tmp_path / "graph_migrated_before_commit.marker"
    child_code = textwrap.dedent(
        """
        from pathlib import Path

        import sys
        import time

        from src.A_memorix.core.storage import format_migration as fm

        original_migrate_graph_metadata = fm._migrate_graph_metadata

        def wrapped_migrate_graph_metadata(data_dir, conn):
            result = original_migrate_graph_metadata(data_dir, conn)
            Path(sys.argv[2]).write_text("graph migrated", encoding="utf-8")
            time.sleep(60)
            return result

        fm._migrate_graph_metadata = wrapped_migrate_graph_metadata
        fm.run_startup_format_migration(Path(sys.argv[1]))
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", child_code, str(data_dir), str(marker_path)],
        cwd=Path.cwd(),
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if marker_path.exists():
                break
            if proc.poll() is not None:
                stdout, stderr = proc.communicate(timeout=5)
                raise AssertionError(f"迁移子进程提前退出: stdout={stdout} stderr={stderr}")
            time.sleep(0.05)
        else:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=10)
            raise AssertionError(f"迁移子进程未进入待 kill 断点: stdout={stdout} stderr={stderr}")

        proc.kill()
        proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)

    assert not (data_dir / "graph" / "graph_metadata.pkl").exists()
    assert (data_dir / "graph" / "graph_metadata.pkl.bak").exists()
    assert (data_dir / "graph" / "graph_metadata.json").exists()

    summary = run_startup_format_migration(data_dir)

    assert summary["graph"]["recovered"] is True
    assert summary["graph"]["reason"] == "legacy_backup_recovered"
    assert summary["graph"]["edge_hash_map_rows"] == 1

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT metadata FROM paragraphs WHERE hash = 'paragraph-kill'").fetchone()
        assert json.loads(row[0])["chat_id"] == "chat-kill"
        edge_rows = conn.execute("SELECT src_idx, dst_idx, relation_hash FROM graph_edge_relation_map").fetchall()
        assert edge_rows == [(0, 1, "rel-kill")]
        migration_row = conn.execute(
            "SELECT summary_json FROM storage_format_migrations WHERE version = ?",
            ("pickle_to_json_v1",),
        ).fetchone()
        assert migration_row is not None
        assert json.loads(migration_row[0])["graph"]["reason"] == "legacy_backup_recovered"
    finally:
        conn.close()


def test_startup_format_migration_empty_dir_does_not_create_metadata_db(tmp_path: Path) -> None:
    data_dir = tmp_path / "empty_data"

    summary = run_startup_format_migration(data_dir)

    assert summary["sqlite"]["reason"] == "metadata_db_missing"
    assert not (data_dir / "metadata" / "metadata.db").exists()


def test_startup_format_migration_is_idempotent_after_pickle_backup(tmp_path: Path) -> None:
    data_dir = tmp_path / "a_memorix_data"
    _dump_pickle(
        data_dir / "vectors" / "vectors_metadata.pkl",
        {"dimension": 8, "ids": ["p-1"], "known_hashes": ["p-1"]},
    )

    first = run_startup_format_migration(data_dir)
    second = run_startup_format_migration(data_dir)

    assert first["vectors"][0]["migrated"] is True
    assert second["vectors"][0]["migrated"] is False
    assert second["vectors"][0]["reason"] == "legacy_missing"
    assert (data_dir / "vectors" / "vectors_metadata.json").exists()
    assert (data_dir / "vectors" / "vectors_metadata.pkl.bak").exists()
    assert not list((data_dir / "vectors").glob("vectors_metadata.pkl.bak.*"))


def test_startup_format_migration_skips_sqlite_scan_after_applied(tmp_path: Path) -> None:
    data_dir = tmp_path / "a_memorix_data"
    db_path = data_dir / "metadata" / "metadata.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE paragraphs (hash TEXT PRIMARY KEY, metadata TEXT)")
        conn.execute(
            "INSERT INTO paragraphs (hash, metadata) VALUES (?, ?)",
            ("paragraph-1", pickle.dumps({"chat_id": "chat-1"})),
        )
        conn.commit()
    finally:
        conn.close()

    first = run_startup_format_migration(data_dir)
    second = run_startup_format_migration(data_dir)

    assert first["sqlite"]["updated"] == 1
    assert second["sqlite"]["reason"] == "already_applied"


def test_startup_format_migration_corrupt_vector_pickle_fails_without_backup(tmp_path: Path) -> None:
    data_dir = tmp_path / "a_memorix_data"
    pkl_path = data_dir / "vectors" / "vectors_metadata.pkl"
    pkl_path.parent.mkdir(parents=True, exist_ok=True)
    pkl_path.write_bytes(b"this is not a pickle")

    with pytest.raises(pickle.UnpicklingError):
        run_startup_format_migration(data_dir)

    assert pkl_path.exists()
    assert not (data_dir / "vectors" / "vectors_metadata.json").exists()
    assert not (data_dir / "vectors" / "vectors_metadata.pkl.bak").exists()


def test_startup_format_migration_rejects_global_objects_in_pickle_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "a_memorix_data"
    marker_path = tmp_path / "pickle_file_executed"
    pkl_path = data_dir / "vectors" / "vectors_metadata.pkl"
    pkl_path.parent.mkdir(parents=True, exist_ok=True)
    pkl_path.write_bytes(pickle.dumps(_MarkerPayload(marker_path)))

    with pytest.raises(pickle.UnpicklingError, match="禁止加载全局对象"):
        run_startup_format_migration(data_dir)

    assert not marker_path.exists()
    assert pkl_path.exists()
    assert not (data_dir / "vectors" / "vectors_metadata.json").exists()
    assert not (data_dir / "vectors" / "vectors_metadata.pkl.bak").exists()


def test_startup_format_migration_rejects_global_objects_in_sqlite_blob(tmp_path: Path) -> None:
    data_dir = tmp_path / "a_memorix_data"
    marker_path = tmp_path / "sqlite_blob_executed"
    malicious_payload = pickle.dumps(_MarkerPayload(marker_path))
    db_path = data_dir / "metadata" / "metadata.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE paragraphs (hash TEXT PRIMARY KEY, metadata BLOB)")
        conn.execute(
            "INSERT INTO paragraphs (hash, metadata) VALUES (?, ?)",
            ("malicious-paragraph", malicious_payload),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(pickle.UnpicklingError, match="禁止加载全局对象"):
        run_startup_format_migration(data_dir)

    assert not marker_path.exists()
    conn = sqlite3.connect(str(db_path))
    try:
        stored_payload = conn.execute(
            "SELECT metadata FROM paragraphs WHERE hash = ?",
            ("malicious-paragraph",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert stored_payload == malicious_payload


def test_startup_format_migration_recovers_corrupt_vector_json_from_legacy_pickle(tmp_path: Path) -> None:
    data_dir = tmp_path / "a_memorix_data"
    pkl_path = data_dir / "vectors" / "vectors_metadata.pkl"
    json_path = data_dir / "vectors" / "vectors_metadata.json"
    _dump_pickle(
        pkl_path,
        {"dimension": 8, "ids": ["vec-1"], "known_hashes": ["vec-1"], "vector_norm": "l2"},
    )
    json_path.write_text("{broken json", encoding="utf-8")

    summary = run_startup_format_migration(data_dir)

    assert summary["vectors"][0]["recovered"] is True
    assert summary["vectors"][0]["reason"] == "json_recovered_from_legacy"
    assert json.loads(json_path.read_text(encoding="utf-8"))["known_hashes"] == ["vec-1"]
    assert (data_dir / "vectors" / "vectors_metadata.pkl.bak").exists()


def test_startup_format_migration_recovers_corrupt_vector_json_from_backup(tmp_path: Path) -> None:
    data_dir = tmp_path / "a_memorix_data"
    backup_path = data_dir / "vectors" / "vectors_metadata.pkl.bak"
    json_path = data_dir / "vectors" / "vectors_metadata.json"
    _dump_pickle(
        backup_path,
        {"dimension": 8, "ids": ["vec-bak"], "known_hashes": ["vec-bak"], "vector_norm": "l2"},
    )
    json_path.write_text("[]", encoding="utf-8")

    summary = run_startup_format_migration(data_dir)

    assert summary["vectors"][0]["recovered"] is True
    assert summary["vectors"][0]["reason"] == "legacy_backup_recovered"
    assert json.loads(json_path.read_text(encoding="utf-8"))["known_hashes"] == ["vec-bak"]


def test_startup_format_migration_recovers_incomplete_graph_json_from_backup(tmp_path: Path) -> None:
    data_dir = tmp_path / "a_memorix_data"
    db_path = data_dir / "metadata" / "metadata.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE paragraphs (hash TEXT PRIMARY KEY, metadata TEXT)")
        conn.commit()
    finally:
        conn.close()

    backup_path = data_dir / "graph" / "graph_metadata.pkl.bak"
    json_path = data_dir / "graph" / "graph_metadata.json"
    _dump_pickle(backup_path, _legacy_graph_payload("rel-bak"))
    json_path.write_text(json.dumps({"nodes": ["alice", "bob"]}), encoding="utf-8")

    summary = run_startup_format_migration(data_dir)

    assert summary["graph"]["recovered"] is True
    assert summary["graph"]["reason"] == "legacy_backup_recovered"
    graph_meta = json.loads(json_path.read_text(encoding="utf-8"))
    assert graph_meta["matrix_format"] == "csr"
    assert "edge_hash_map" not in graph_meta

    conn = sqlite3.connect(str(db_path))
    try:
        edge_rows = conn.execute("SELECT src_idx, dst_idx, relation_hash FROM graph_edge_relation_map").fetchall()
        assert edge_rows == [(0, 1, "rel-bak")]
    finally:
        conn.close()


def test_startup_format_migration_corrupt_backup_fails_when_json_unusable(tmp_path: Path) -> None:
    data_dir = tmp_path / "a_memorix_data"
    backup_path = data_dir / "vectors" / "vectors_metadata.pkl.bak"
    json_path = data_dir / "vectors" / "vectors_metadata.json"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_bytes(b"not a pickle")
    json_path.write_text("{broken json", encoding="utf-8")

    with pytest.raises(pickle.UnpicklingError):
        run_startup_format_migration(data_dir)
