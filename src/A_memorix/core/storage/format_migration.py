"""A_Memorix storage format migration helpers.

启动阶段使用本模块读取本地历史 pickle 数据，并转换为运行时安全格式。
运行时代码不应直接反序列化 pickle。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterable, List, Optional, Tuple

import json
import pickle
import sqlite3
import time

from src.common.logger import get_logger

from ..utils.io import atomic_write

logger = get_logger("A_Memorix.FormatMigration")

FORMAT_MIGRATION_VERSION = "pickle_to_json_v1"


class _LegacyDataUnpickler(pickle.Unpickler):
    """仅加载旧存储中的基础数据结构，禁止解析任何全局对象。"""

    def find_class(self, module: str, name: str) -> Any:
        raise pickle.UnpicklingError(f"旧存储 pickle 禁止加载全局对象: {module}.{name}")


def _load_legacy_pickle(handle: BinaryIO) -> Any:
    return _LegacyDataUnpickler(handle).load()


def _loads_legacy_pickle(payload: bytes) -> Any:
    return _load_legacy_pickle(BytesIO(payload))


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    with atomic_write(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")


def _read_json_dict(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise TypeError(f"JSON 元数据必须是对象: {path}")
    return payload


def _backup_legacy_file(path: Path) -> Path:
    backup_path = path.with_suffix(path.suffix + ".bak")
    if backup_path.exists():
        backup_path = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
    path.replace(backup_path)
    return backup_path


def _legacy_backup_candidates(path: Path) -> List[Path]:
    candidates = []
    direct_backup = path.with_suffix(path.suffix + ".bak")
    if direct_backup.exists():
        candidates.append(direct_backup)
    candidates.extend(path.parent.glob(path.name + ".bak.*"))
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)


def _load_pickle_dict(path: Path, label: str) -> Dict[str, Any]:
    with path.open("rb") as handle:
        payload = _load_legacy_pickle(handle)
    if not isinstance(payload, dict):
        raise TypeError(f"{label}必须是 dict: {path}")
    return dict(payload)


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _sqlite_column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _sqlite_table_exists(conn, table):
        return False
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(row[1]) == column for row in rows)


def _ensure_migration_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS storage_format_migrations (
            version TEXT PRIMARY KEY,
            applied_at REAL NOT NULL,
            summary_json TEXT NOT NULL
        )
        """
    )


def _migration_record_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM storage_format_migrations WHERE version = ? LIMIT 1",
        (FORMAT_MIGRATION_VERSION,),
    ).fetchone()
    return row is not None


def _legacy_pickle_paths(data_dir: Path) -> List[Path]:
    return [
        data_dir / "vectors" / "vectors_metadata.pkl",
        data_dir / "vectors" / "paragraph" / "vectors_metadata.pkl",
        data_dir / "vectors" / "graph" / "vectors_metadata.pkl",
        data_dir / "graph" / "graph_metadata.pkl",
    ]


def _legacy_pickle_exists(data_dir: Path) -> bool:
    return any(path.exists() for path in _legacy_pickle_paths(data_dir))


def ensure_graph_edge_map_table(conn: sqlite3.Connection) -> None:
    """确保图边到关系哈希的映射表存在。"""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS graph_edge_relation_map (
            src_idx INTEGER NOT NULL,
            dst_idx INTEGER NOT NULL,
            relation_hash TEXT NOT NULL,
            PRIMARY KEY (src_idx, dst_idx, relation_hash)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_graph_edge_relation_hash
        ON graph_edge_relation_map(relation_hash)
        """
    )


def _metadata_db_path(data_dir: Path) -> Path:
    return data_dir / "metadata" / "metadata.db"


def _connect_metadata_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _metadata_to_json_text(raw: Any) -> Tuple[Optional[str], bool]:
    if raw is None:
        return None, False
    if isinstance(raw, dict):
        return _json_dumps(raw), True

    decoded: Any
    if isinstance(raw, bytes):
        if not raw:
            return "", False
        try:
            decoded = json.loads(raw.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise TypeError("metadata JSON 必须是对象")
            return _json_dumps(decoded), True
        except Exception as json_exc:
            decoded = _loads_legacy_pickle(raw)
            if not isinstance(decoded, dict):
                raise TypeError("pickle metadata 必须解码为 dict") from json_exc
            return _json_dumps(decoded), True

    if isinstance(raw, str):
        if raw == "":
            return "", False
        try:
            decoded = json.loads(raw)
        except Exception as exc:
            raise ValueError("metadata 文本既不是合法 JSON，也不是可迁移的 pickle bytes") from exc
        if not isinstance(decoded, dict):
            raise TypeError("metadata JSON 必须是对象")
        normalized = _json_dumps(decoded)
        return normalized, normalized != raw

    raise TypeError(f"不支持的 metadata 类型: {type(raw).__name__}")


def _migrate_sqlite_metadata(conn: sqlite3.Connection) -> Dict[str, Any]:
    result: Dict[str, Any] = {"tables": {}, "updated": 0}
    tables = ("paragraphs", "entities", "relations", "deleted_relations")
    for table in tables:
        if not _sqlite_column_exists(conn, table, "metadata"):
            continue
        rows = conn.execute(f"SELECT rowid, metadata FROM {table} WHERE metadata IS NOT NULL").fetchall()
        table_updated = 0
        for row in rows:
            converted, changed = _metadata_to_json_text(row["metadata"])
            if not changed:
                continue
            conn.execute(
                f"UPDATE {table} SET metadata = ? WHERE rowid = ?",
                (converted, row["rowid"]),
            )
            table_updated += 1
        result["tables"][table] = {"scanned": len(rows), "updated": table_updated}
        result["updated"] += table_updated
    return result


def _prepare_vector_metadata_payload(payload: Dict[str, Any], path: Path) -> Dict[str, Any]:
    if "known_hashes" not in payload and "ids" not in payload:
        raise ValueError(f"向量元数据缺少 known_hashes 或 ids: {path}")
    metadata = dict(payload)
    metadata["schema_version"] = int(metadata.get("schema_version") or 1)
    return metadata


def _load_vector_metadata_from_pickle(path: Path) -> Dict[str, Any]:
    return _prepare_vector_metadata_payload(_load_pickle_dict(path, "向量元数据"), path)


def _validate_vector_metadata_json(path: Path) -> None:
    _prepare_vector_metadata_payload(_read_json_dict(path), path)


def _recover_vector_metadata_from_backup(vector_dir: Path, json_path: Path, backups: List[Path]) -> Dict[str, Any]:
    if not backups:
        raise FileNotFoundError(f"向量元数据 JSON 损坏且没有可恢复备份: {json_path}")
    payload = _load_vector_metadata_from_pickle(backups[0])
    _write_json(json_path, payload)
    return {
        "migrated": False,
        "recovered": True,
        "reason": "legacy_backup_recovered",
        "path": str(vector_dir),
        "backup": str(backups[0]),
    }


def _migrate_vector_metadata_dir(vector_dir: Path) -> Dict[str, Any]:
    pkl_path = vector_dir / "vectors_metadata.pkl"
    json_path = vector_dir / "vectors_metadata.json"
    if not pkl_path.exists():
        backups = _legacy_backup_candidates(pkl_path)
        if json_path.exists():
            try:
                _validate_vector_metadata_json(json_path)
            except Exception:
                return _recover_vector_metadata_from_backup(vector_dir, json_path, backups)
        elif backups:
            return _recover_vector_metadata_from_backup(vector_dir, json_path, backups)
        return {"migrated": False, "reason": "legacy_missing", "path": str(vector_dir)}

    payload = _load_vector_metadata_from_pickle(pkl_path)
    if json_path.exists():
        reason = "json_exists"
        try:
            _validate_vector_metadata_json(json_path)
        except Exception:
            _write_json(json_path, payload)
            reason = "json_recovered_from_legacy"
        backup = _backup_legacy_file(pkl_path)
        return {
            "migrated": False,
            "recovered": reason == "json_recovered_from_legacy",
            "reason": reason,
            "path": str(vector_dir),
            "backup": str(backup),
        }

    _write_json(json_path, payload)
    backup = _backup_legacy_file(pkl_path)
    return {"migrated": True, "path": str(vector_dir), "backup": str(backup)}


def _normalize_edge_key(key: Any) -> Optional[Tuple[int, int]]:
    if isinstance(key, (list, tuple)) and len(key) == 2:
        return int(key[0]), int(key[1])
    if isinstance(key, str):
        normalized = key.strip().replace("(", "").replace(")", "")
        for separator in (",", ":", "|"):
            if separator in normalized:
                left, right = normalized.split(separator, 1)
                return int(left.strip()), int(right.strip())
    return None


def _edge_map_rows(edge_hash_map: Any) -> List[Tuple[int, int, str]]:
    rows: List[Tuple[int, int, str]] = []
    if not isinstance(edge_hash_map, dict):
        return rows
    seen = set()
    for raw_key, raw_hashes in edge_hash_map.items():
        key = _normalize_edge_key(raw_key)
        if key is None:
            continue
        if isinstance(raw_hashes, (list, tuple, set)):
            hashes = raw_hashes
        else:
            hashes = [raw_hashes]
        for raw_hash in hashes:
            relation_hash = str(raw_hash or "").strip()
            if not relation_hash:
                continue
            item = (int(key[0]), int(key[1]), relation_hash)
            if item in seen:
                continue
            seen.add(item)
            rows.append(item)
    return rows


def _replace_graph_edge_map(conn: sqlite3.Connection, rows: Iterable[Tuple[int, int, str]]) -> int:
    ensure_graph_edge_map_table(conn)
    conn.execute("DELETE FROM graph_edge_relation_map")
    normalized = list(rows)
    if normalized:
        conn.executemany(
            """
            INSERT OR IGNORE INTO graph_edge_relation_map (src_idx, dst_idx, relation_hash)
            VALUES (?, ?, ?)
            """,
            normalized,
        )
    return len(normalized)


def _graph_edge_map_count(conn: sqlite3.Connection) -> int:
    if not _sqlite_table_exists(conn, "graph_edge_relation_map"):
        return 0
    row = conn.execute("SELECT COUNT(*) FROM graph_edge_relation_map").fetchone()
    return int(row[0] or 0) if row else 0


def _json_edge_hash_map(edge_hash_map: Any) -> Dict[str, List[str]]:
    payload: Dict[str, List[str]] = {}
    for src_idx, dst_idx, relation_hash in _edge_map_rows(edge_hash_map):
        payload.setdefault(f"{src_idx},{dst_idx}", []).append(relation_hash)
    return payload


def _prepare_graph_metadata_payload(
    payload: Dict[str, Any],
    path: Path,
    conn: Optional[sqlite3.Connection],
) -> Tuple[Dict[str, Any], int]:
    nodes = payload.get("nodes")
    node_to_idx = payload.get("node_to_idx")
    if not isinstance(nodes, list):
        raise ValueError(f"图元数据缺少 nodes 列表: {path}")
    if not isinstance(node_to_idx, dict):
        raise ValueError(f"图元数据缺少 node_to_idx 对象: {path}")

    edge_rows = _edge_map_rows(payload.get("edge_hash_map", {}))
    metadata = dict(payload)
    metadata.setdefault("node_attrs", {})
    metadata.setdefault("matrix_format", "csr")
    metadata.setdefault("total_nodes_added", len(nodes))
    metadata.setdefault("total_edges_added", len(edge_rows))
    metadata.setdefault("total_nodes_deleted", 0)
    metadata.setdefault("total_edges_deleted", 0)
    if conn is not None:
        edge_count = _replace_graph_edge_map(conn, edge_rows)
        metadata.pop("edge_hash_map", None)
    else:
        edge_count = len(edge_rows)
        metadata["edge_hash_map"] = _json_edge_hash_map(payload.get("edge_hash_map", {}))
    metadata["schema_version"] = int(metadata.get("schema_version") or 1)
    return metadata, edge_count


def _load_graph_metadata_from_pickle(
    path: Path,
    conn: Optional[sqlite3.Connection],
) -> Tuple[Dict[str, Any], int]:
    return _prepare_graph_metadata_payload(_load_pickle_dict(path, "图元数据"), path, conn)


def _validate_graph_metadata_json(path: Path) -> None:
    required_keys = (
        "nodes",
        "node_to_idx",
        "node_attrs",
        "matrix_format",
        "total_nodes_added",
        "total_edges_added",
        "total_nodes_deleted",
        "total_edges_deleted",
    )
    payload = _read_json_dict(path)
    missing = [key for key in required_keys if key not in payload]
    if missing:
        raise ValueError(f"图元数据缺少字段: {', '.join(missing)}")
    _prepare_graph_metadata_payload(payload, path, None)


def _recover_graph_metadata_from_backup(
    json_path: Path,
    backup_path: Path,
    conn: Optional[sqlite3.Connection],
) -> Dict[str, Any]:
    metadata, edge_count = _load_graph_metadata_from_pickle(backup_path, conn)
    _write_json(json_path, metadata)
    return {
        "migrated": False,
        "recovered": True,
        "reason": "legacy_backup_recovered",
        "backup": str(backup_path),
        "edge_hash_map_rows": edge_count,
    }


def _migrate_graph_metadata(data_dir: Path, conn: Optional[sqlite3.Connection]) -> Dict[str, Any]:
    graph_dir = data_dir / "graph"
    pkl_path = graph_dir / "graph_metadata.pkl"
    json_path = graph_dir / "graph_metadata.json"
    if not pkl_path.exists():
        backup_candidates = _legacy_backup_candidates(pkl_path)
        needs_recovery = False
        if json_path.exists():
            try:
                _validate_graph_metadata_json(json_path)
            except Exception:
                needs_recovery = True
        else:
            needs_recovery = bool(backup_candidates)
        if conn is not None and json_path.exists() and backup_candidates and _graph_edge_map_count(conn) == 0:
            needs_recovery = True
        if needs_recovery and backup_candidates:
            return _recover_graph_metadata_from_backup(json_path, backup_candidates[0], conn)
        if needs_recovery:
            raise FileNotFoundError(f"图元数据 JSON 损坏且没有可恢复备份: {json_path}")
        return {"migrated": False, "reason": "legacy_missing"}

    metadata, edge_count = _load_graph_metadata_from_pickle(pkl_path, conn)
    reason = "ok"
    if json_path.exists():
        try:
            _validate_graph_metadata_json(json_path)
        except Exception:
            _write_json(json_path, metadata)
            reason = "json_recovered_from_legacy"
    else:
        _write_json(json_path, metadata)
    backup = _backup_legacy_file(pkl_path)
    result = {
        "migrated": True,
        "backup": str(backup),
        "edge_hash_map_rows": edge_count,
    }
    if reason != "ok":
        result["recovered"] = True
        result["reason"] = reason
    return result


def run_startup_format_migration(data_dir: Path) -> Dict[str, Any]:
    """同步执行启动格式迁移，失败时抛出异常并阻止 A_Memorix 初始化。"""

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    summary: Dict[str, Any] = {
        "version": FORMAT_MIGRATION_VERSION,
        "data_dir": str(data_dir),
        "started_at": started_at,
        "sqlite": {},
        "vectors": [],
        "graph": {},
    }

    db_path = _metadata_db_path(data_dir)
    conn: Optional[sqlite3.Connection] = _connect_metadata_db(db_path) if db_path.exists() else None
    try:
        if conn is not None:
            _ensure_migration_table(conn)
            if _migration_record_exists(conn) and not _legacy_pickle_exists(data_dir):
                summary["sqlite"] = {"updated": 0, "reason": "already_applied"}
                summary["vectors"] = [
                    {"migrated": False, "reason": "legacy_missing", "path": str(path.parent)}
                    for path in _legacy_pickle_paths(data_dir)[:3]
                ]
                summary["graph"] = {"migrated": False, "reason": "legacy_missing"}
                conn.commit()
                summary["finished_at"] = time.time()
                logger.info(
                    "A_Memorix 存储格式迁移快速跳过: "
                    f"duration={summary['finished_at'] - started_at:.2f}s"
                )
                return summary
            summary["sqlite"] = _migrate_sqlite_metadata(conn)
        else:
            summary["sqlite"] = {"updated": 0, "reason": "metadata_db_missing"}
        for relative in ("vectors", "vectors/paragraph", "vectors/graph"):
            summary["vectors"].append(_migrate_vector_metadata_dir(data_dir / relative))
        summary["graph"] = _migrate_graph_metadata(data_dir, conn)
        if conn is not None:
            conn.execute(
                """
                INSERT OR REPLACE INTO storage_format_migrations (version, applied_at, summary_json)
                VALUES (?, ?, ?)
                """,
                (FORMAT_MIGRATION_VERSION, time.time(), _json_dumps(summary)),
            )
            conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()

    summary["finished_at"] = time.time()
    logger.info(
        "A_Memorix 存储格式迁移检查完成: "
        f"sqlite_updated={summary.get('sqlite', {}).get('updated', 0)}, "
        f"duration={summary['finished_at'] - started_at:.2f}s"
    )
    return summary
