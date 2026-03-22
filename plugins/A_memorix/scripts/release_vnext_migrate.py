#!/usr/bin/env python3
"""
vNext release migration entrypoint for A_Memorix.

Subcommands:
- preflight: detect legacy config/data/schema risks
- migrate: offline migrate config + vectors + metadata schema + graph edge hash map
- verify: strict post-migration consistency checks
"""

from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import tomlkit


CURRENT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = CURRENT_DIR.parent
PROJECT_ROOT = PLUGIN_ROOT.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A_Memorix vNext release migration tool")
    parser.add_argument(
        "--config",
        default=str(PLUGIN_ROOT / "config.toml"),
        help="config.toml path (default: plugins/A_memorix/config.toml)",
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="optional data dir override; default resolved from config.storage.data_dir",
    )
    parser.add_argument("--json-out", default="", help="optional JSON report output path")

    sub = parser.add_subparsers(dest="command", required=True)

    p_preflight = sub.add_parser("preflight", help="scan legacy risks")
    p_preflight.add_argument("--strict", action="store_true", help="return 1 if any error check exists")

    p_migrate = sub.add_parser("migrate", help="run offline migration")
    p_migrate.add_argument("--dry-run", action="store_true", help="only print planned changes")
    p_migrate.add_argument(
        "--verify-after",
        action="store_true",
        help="run verify automatically after migrate",
    )

    p_verify = sub.add_parser("verify", help="post-migration verification")
    p_verify.add_argument("--strict", action="store_true", help="return 1 if any error check exists")
    return parser


# --help/-h fast path: avoid heavy host/plugin bootstrap
if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    _build_arg_parser().print_help()
    raise SystemExit(0)

try:
    from core.storage import GraphStore, KnowledgeType, MetadataStore, QuantizationType, VectorStore
    from core.storage.metadata_store import SCHEMA_VERSION
except Exception as e:  # pragma: no cover
    print(f"❌ failed to import storage modules: {e}")
    raise SystemExit(2)


@dataclass
class CheckItem:
    code: str
    level: str
    message: str
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "code": self.code,
            "level": self.level,
            "message": self.message,
        }
        if self.details:
            out["details"] = self.details
        return out


def _read_toml(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    return tomlkit.parse(text)


def _write_toml(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(tomlkit.dumps(data), encoding="utf-8")


def _get_nested(obj: Dict[str, Any], keys: Sequence[str], default: Any = None) -> Any:
    cur: Any = obj
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _ensure_table(obj: Dict[str, Any], key: str) -> Dict[str, Any]:
    if key not in obj or not isinstance(obj[key], dict):
        obj[key] = tomlkit.table()
    return obj[key]


def _resolve_data_dir(config_doc: Dict[str, Any], explicit_data_dir: Optional[str]) -> Path:
    if explicit_data_dir:
        return Path(explicit_data_dir).expanduser().resolve()
    raw = str(_get_nested(config_doc, ("storage", "data_dir"), "./data") or "./data").strip()
    if raw.startswith("."):
        return (PLUGIN_ROOT / raw).resolve()
    return Path(raw).expanduser().resolve()


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _collect_hash_alias_conflicts(conn: sqlite3.Connection) -> Dict[str, List[str]]:
    hashes: List[str] = []
    if _sqlite_table_exists(conn, "relations"):
        rows = conn.execute("SELECT hash FROM relations").fetchall()
        hashes.extend(str(r[0]) for r in rows if r and r[0])
    if _sqlite_table_exists(conn, "deleted_relations"):
        rows = conn.execute("SELECT hash FROM deleted_relations").fetchall()
        hashes.extend(str(r[0]) for r in rows if r and r[0])

    alias_map: Dict[str, str] = {}
    conflicts: Dict[str, set[str]] = {}
    for h in hashes:
        if len(h) != 64:
            continue
        alias = h[:32]
        old = alias_map.get(alias)
        if old is None:
            alias_map[alias] = h
            continue
        if old != h:
            conflicts.setdefault(alias, set()).update({old, h})
    return {k: sorted(v) for k, v in conflicts.items()}


def _collect_invalid_knowledge_types(conn: sqlite3.Connection) -> List[str]:
    if not _sqlite_table_exists(conn, "paragraphs"):
        return []

    allowed = {item.value for item in KnowledgeType}
    rows = conn.execute("SELECT DISTINCT knowledge_type FROM paragraphs").fetchall()
    invalid: List[str] = []
    for row in rows:
        raw = row[0]
        value = str(raw).strip().lower() if raw is not None else ""
        if value not in allowed:
            invalid.append(str(raw) if raw is not None else "")
    return sorted(set(invalid))


def _guess_vector_dimension(config_doc: Dict[str, Any], vectors_dir: Path) -> int:
    meta_path = vectors_dir / "vectors_metadata.pkl"
    if meta_path.exists():
        try:
            with open(meta_path, "rb") as f:
                meta = pickle.load(f)
            dim = int(meta.get("dimension", 0))
            if dim > 0:
                return dim
        except Exception:
            pass
    try:
        dim_cfg = int(_get_nested(config_doc, ("embedding", "dimension"), 1024))
        if dim_cfg > 0:
            return dim_cfg
    except Exception:
        pass
    return 1024


def _preflight_impl(config_path: Path, data_dir: Path) -> Dict[str, Any]:
    checks: List[CheckItem] = []
    facts: Dict[str, Any] = {
        "config_path": str(config_path),
        "data_dir": str(data_dir),
    }

    if not config_path.exists():
        checks.append(CheckItem("CFG-00", "error", f"config not found: {config_path}"))
        return {"ok": False, "checks": [c.to_dict() for c in checks], "facts": facts}

    config_doc = _read_toml(config_path)
    tool_mode = str(_get_nested(config_doc, ("routing", "tool_search_mode"), "forward") or "").strip().lower()
    summary_model = _get_nested(config_doc, ("summarization", "model_name"), ["auto"])
    summary_knowledge_type = str(
        _get_nested(config_doc, ("summarization", "default_knowledge_type"), "narrative") or "narrative"
    ).strip().lower()
    quantization = str(_get_nested(config_doc, ("embedding", "quantization_type"), "int8") or "").strip().lower()

    facts["routing.tool_search_mode"] = tool_mode
    facts["summarization.model_name_type"] = type(summary_model).__name__
    facts["summarization.default_knowledge_type"] = summary_knowledge_type
    facts["embedding.quantization_type"] = quantization

    if tool_mode == "legacy":
        checks.append(
            CheckItem(
                "CP-04",
                "error",
                "routing.tool_search_mode=legacy is no longer accepted at runtime",
            )
        )
    elif tool_mode not in {"forward", "disabled"}:
        checks.append(
            CheckItem(
                "CP-04",
                "error",
                f"routing.tool_search_mode invalid value: {tool_mode}",
            )
        )

    if isinstance(summary_model, str):
        checks.append(
            CheckItem(
                "CP-11",
                "error",
                "summarization.model_name must be List[str], string legacy format detected",
            )
        )
    elif not isinstance(summary_model, list) or any(not isinstance(x, str) for x in summary_model):
        checks.append(
            CheckItem(
                "CP-11",
                "error",
                "summarization.model_name must be List[str]",
            )
        )

    if summary_knowledge_type not in {item.value for item in KnowledgeType}:
        checks.append(
            CheckItem(
                "CP-13",
                "error",
                f"invalid summarization.default_knowledge_type: {summary_knowledge_type}",
            )
        )

    if quantization != "int8":
        checks.append(
            CheckItem(
                "UG-07",
                "error",
                "embedding.quantization_type must be int8 in vNext",
            )
        )

    vectors_dir = data_dir / "vectors"
    npy_path = vectors_dir / "vectors.npy"
    bin_path = vectors_dir / "vectors.bin"
    ids_bin_path = vectors_dir / "vectors_ids.bin"
    facts["vectors.npy_exists"] = npy_path.exists()
    facts["vectors.bin_exists"] = bin_path.exists()
    facts["vectors_ids.bin_exists"] = ids_bin_path.exists()

    if npy_path.exists() and not (bin_path.exists() and ids_bin_path.exists()):
        checks.append(
            CheckItem(
                "CP-07",
                "error",
                "legacy vectors.npy detected; offline migrate required",
                {"npy_path": str(npy_path)},
            )
        )

    metadata_db = data_dir / "metadata" / "metadata.db"
    facts["metadata_db_exists"] = metadata_db.exists()
    relation_count = 0
    if metadata_db.exists():
        conn = sqlite3.connect(str(metadata_db))
        try:
            has_schema_table = _sqlite_table_exists(conn, "schema_migrations")
            facts["schema_migrations_exists"] = has_schema_table
            if not has_schema_table:
                checks.append(
                    CheckItem(
                        "CP-08",
                        "error",
                        "schema_migrations table missing (legacy metadata schema)",
                    )
                )
            else:
                row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
                version = int(row[0]) if row and row[0] is not None else 0
                facts["schema_version"] = version
                if version != SCHEMA_VERSION:
                    checks.append(
                        CheckItem(
                            "CP-08",
                            "error",
                            f"schema version mismatch: current={version}, expected={SCHEMA_VERSION}",
                        )
                    )

            if _sqlite_table_exists(conn, "relations"):
                row = conn.execute("SELECT COUNT(*) FROM relations").fetchone()
                relation_count = int(row[0]) if row and row[0] is not None else 0
            facts["relations_count"] = relation_count

            conflicts = _collect_hash_alias_conflicts(conn)
            facts["alias_conflict_count"] = len(conflicts)
            if conflicts:
                checks.append(
                    CheckItem(
                        "CP-05",
                        "error",
                        "32-bit relation hash alias conflict detected",
                        {"aliases": sorted(conflicts.keys())[:20], "total": len(conflicts)},
                    )
                )

            invalid_knowledge_types = _collect_invalid_knowledge_types(conn)
            facts["invalid_knowledge_type_values"] = invalid_knowledge_types
            if invalid_knowledge_types:
                checks.append(
                    CheckItem(
                        "CP-12",
                        "error",
                        "invalid paragraph knowledge_type values detected",
                        {"values": invalid_knowledge_types[:20], "total": len(invalid_knowledge_types)},
                    )
                )
        finally:
            conn.close()
    else:
        checks.append(
            CheckItem(
                "META-00",
                "warning",
                "metadata.db not found, schema checks skipped",
            )
        )

    graph_meta_path = data_dir / "graph" / "graph_metadata.pkl"
    facts["graph_metadata_exists"] = graph_meta_path.exists()
    if relation_count > 0:
        if not graph_meta_path.exists():
            checks.append(
                CheckItem(
                    "CP-06",
                    "error",
                    "relations exist but graph metadata missing",
                )
            )
        else:
            try:
                with open(graph_meta_path, "rb") as f:
                    graph_meta = pickle.load(f)
                edge_hash_map = graph_meta.get("edge_hash_map", {})
                edge_hash_map_size = len(edge_hash_map) if isinstance(edge_hash_map, dict) else 0
                facts["edge_hash_map_size"] = edge_hash_map_size
                if edge_hash_map_size <= 0:
                    checks.append(
                        CheckItem(
                            "CP-06",
                            "error",
                            "edge_hash_map missing/empty while relations exist",
                        )
                    )
            except Exception as e:
                checks.append(
                    CheckItem(
                        "CP-06",
                        "error",
                        f"failed to read graph metadata: {e}",
                    )
                )

    has_error = any(c.level == "error" for c in checks)
    return {
        "ok": not has_error,
        "checks": [c.to_dict() for c in checks],
        "facts": facts,
    }


def _migrate_config(config_doc: Dict[str, Any]) -> Dict[str, Any]:
    changes: Dict[str, Any] = {}

    routing = _ensure_table(config_doc, "routing")
    mode_raw = str(routing.get("tool_search_mode", "forward") or "").strip().lower()
    mode_new = mode_raw
    if mode_raw == "legacy" or mode_raw not in {"forward", "disabled"}:
        mode_new = "forward"
    if mode_new != mode_raw:
        routing["tool_search_mode"] = mode_new
        changes["routing.tool_search_mode"] = {"old": mode_raw, "new": mode_new}

    summary = _ensure_table(config_doc, "summarization")
    summary_model = summary.get("model_name", ["auto"])
    if isinstance(summary_model, str):
        normalized = [summary_model.strip() or "auto"]
        summary["model_name"] = normalized
        changes["summarization.model_name"] = {"old": summary_model, "new": normalized}
    elif not isinstance(summary_model, list):
        normalized = ["auto"]
        summary["model_name"] = normalized
        changes["summarization.model_name"] = {"old": str(type(summary_model)), "new": normalized}
    elif any(not isinstance(x, str) for x in summary_model):
        normalized = [str(x).strip() for x in summary_model if str(x).strip()]
        if not normalized:
            normalized = ["auto"]
        summary["model_name"] = normalized
        changes["summarization.model_name"] = {"old": summary_model, "new": normalized}

    default_knowledge_type = str(summary.get("default_knowledge_type", "narrative") or "").strip().lower()
    allowed_knowledge_types = {item.value for item in KnowledgeType}
    if default_knowledge_type not in allowed_knowledge_types:
        summary["default_knowledge_type"] = "narrative"
        changes["summarization.default_knowledge_type"] = {
            "old": default_knowledge_type,
            "new": "narrative",
        }

    embedding = _ensure_table(config_doc, "embedding")
    quantization = str(embedding.get("quantization_type", "int8") or "").strip().lower()
    if quantization != "int8":
        embedding["quantization_type"] = "int8"
        changes["embedding.quantization_type"] = {"old": quantization, "new": "int8"}

    return changes


def _migrate_impl(config_path: Path, data_dir: Path, dry_run: bool) -> Dict[str, Any]:
    config_doc = _read_toml(config_path)
    result: Dict[str, Any] = {
        "config_path": str(config_path),
        "data_dir": str(data_dir),
        "dry_run": bool(dry_run),
        "steps": {},
    }

    config_changes = _migrate_config(config_doc)
    result["steps"]["config"] = {"changed": bool(config_changes), "changes": config_changes}
    if config_changes and not dry_run:
        _write_toml(config_path, config_doc)

    vectors_dir = data_dir / "vectors"
    vectors_dir.mkdir(parents=True, exist_ok=True)
    npy_path = vectors_dir / "vectors.npy"
    bin_path = vectors_dir / "vectors.bin"
    ids_bin_path = vectors_dir / "vectors_ids.bin"
    if npy_path.exists() and not (bin_path.exists() and ids_bin_path.exists()):
        if dry_run:
            result["steps"]["vector"] = {"migrated": False, "reason": "dry_run"}
        else:
            dim = _guess_vector_dimension(config_doc, vectors_dir)
            store = VectorStore(
                dimension=max(1, int(dim)),
                quantization_type=QuantizationType.INT8,
                data_dir=vectors_dir,
            )
            result["steps"]["vector"] = store.migrate_legacy_npy(vectors_dir)
    else:
        result["steps"]["vector"] = {"migrated": False, "reason": "not_required"}

    metadata_dir = data_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    metadata_db = metadata_dir / "metadata.db"
    triples: List[Tuple[str, str, str, str]] = []
    relation_count = 0

    metadata_result: Dict[str, Any] = {"migrated": False, "reason": "not_required"}
    if metadata_db.exists():
        store = MetadataStore(data_dir=metadata_dir)
        store.connect(enforce_schema=False)
        try:
            if dry_run:
                metadata_result = {"migrated": False, "reason": "dry_run"}
            else:
                metadata_result = store.run_legacy_migration_for_vnext()
            relation_count = int(store.count_relations())
            if relation_count > 0:
                triples = [(str(s), str(p), str(o), str(h)) for s, p, o, h in store.get_all_triples()]
        finally:
            store.close()
    result["steps"]["metadata"] = metadata_result

    graph_dir = data_dir / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    graph_matrix_format = str(_get_nested(config_doc, ("graph", "sparse_matrix_format"), "csr") or "csr")
    graph_store = GraphStore(matrix_format=graph_matrix_format, data_dir=graph_dir)
    graph_step: Dict[str, Any] = {
        "rebuilt": False,
        "mapped_hashes": 0,
        "relation_count": relation_count,
        "topology_rebuilt_from_relations": False,
    }
    if relation_count > 0:
        if dry_run:
            graph_step["reason"] = "dry_run"
        else:
            if graph_store.has_data():
                graph_store.load()

            mapped = graph_store.rebuild_edge_hash_map(triples)

            # 兜底：历史数据里 graph 节点/边与 relations 脱节时，直接从 relations 重建图。
            if mapped <= 0 or not graph_store.has_edge_hash_map():
                nodes = sorted({s for s, _, o, _ in triples} | {o for _, _, o, _ in triples})
                edges = [(s, o) for s, _, o, _ in triples]
                hashes = [h for _, _, _, h in triples]

                graph_store.clear()
                if nodes:
                    graph_store.add_nodes(nodes)
                if edges:
                    mapped = graph_store.add_edges(edges, relation_hashes=hashes)
                else:
                    mapped = 0
                graph_step.update(
                    {
                        "topology_rebuilt_from_relations": True,
                        "rebuilt_nodes": len(nodes),
                        "rebuilt_edges": int(graph_store.num_edges),
                    }
                )

            graph_store.save()
            graph_step.update({"rebuilt": True, "mapped_hashes": int(mapped)})
    else:
        graph_step["reason"] = "no_relations"
    result["steps"]["graph"] = graph_step

    return result


def _verify_impl(config_path: Path, data_dir: Path) -> Dict[str, Any]:
    checks: List[CheckItem] = []
    facts: Dict[str, Any] = {
        "config_path": str(config_path),
        "data_dir": str(data_dir),
    }

    if not config_path.exists():
        checks.append(CheckItem("CFG-00", "error", f"config not found: {config_path}"))
        return {"ok": False, "checks": [c.to_dict() for c in checks], "facts": facts}

    config_doc = _read_toml(config_path)
    mode = str(_get_nested(config_doc, ("routing", "tool_search_mode"), "forward") or "").strip().lower()
    if mode not in {"forward", "disabled"}:
        checks.append(CheckItem("CP-04", "error", f"invalid routing.tool_search_mode: {mode}"))

    summary_model = _get_nested(config_doc, ("summarization", "model_name"), ["auto"])
    if not isinstance(summary_model, list) or any(not isinstance(x, str) for x in summary_model):
        checks.append(CheckItem("CP-11", "error", "summarization.model_name must be List[str]"))
    summary_knowledge_type = str(
        _get_nested(config_doc, ("summarization", "default_knowledge_type"), "narrative") or "narrative"
    ).strip().lower()
    if summary_knowledge_type not in {item.value for item in KnowledgeType}:
        checks.append(
            CheckItem("CP-13", "error", f"invalid summarization.default_knowledge_type: {summary_knowledge_type}")
        )

    quantization = str(_get_nested(config_doc, ("embedding", "quantization_type"), "int8") or "").strip().lower()
    if quantization != "int8":
        checks.append(CheckItem("UG-07", "error", "embedding.quantization_type must be int8"))

    vectors_dir = data_dir / "vectors"
    npy_path = vectors_dir / "vectors.npy"
    bin_path = vectors_dir / "vectors.bin"
    ids_bin_path = vectors_dir / "vectors_ids.bin"
    if npy_path.exists() and not (bin_path.exists() and ids_bin_path.exists()):
        checks.append(CheckItem("CP-07", "error", "legacy vectors.npy still exists without bin migration"))

    metadata_dir = data_dir / "metadata"
    store = MetadataStore(data_dir=metadata_dir)
    try:
        store.connect(enforce_schema=True)
        schema_version = store.get_schema_version()
        facts["schema_version"] = schema_version
        if schema_version != SCHEMA_VERSION:
            checks.append(CheckItem("CP-08", "error", f"schema version mismatch: {schema_version}"))

        relation_count = int(store.count_relations())
        facts["relations_count"] = relation_count

        conflicts = {}
        invalid_knowledge_types: List[str] = []
        db_path = metadata_dir / "metadata.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            try:
                conflicts = _collect_hash_alias_conflicts(conn)
                invalid_knowledge_types = _collect_invalid_knowledge_types(conn)
            finally:
                conn.close()
        if conflicts:
            checks.append(
                CheckItem(
                    "CP-05",
                    "error",
                    "alias conflicts still exist after migration",
                    {"aliases": sorted(conflicts.keys())[:20], "total": len(conflicts)},
                )
            )
        if invalid_knowledge_types:
            checks.append(
                CheckItem(
                    "CP-12",
                    "error",
                    "invalid paragraph knowledge_type values remain after migration",
                    {"values": invalid_knowledge_types[:20], "total": len(invalid_knowledge_types)},
                )
            )

        if relation_count > 0:
            graph_dir = data_dir / "graph"
            if not (graph_dir / "graph_metadata.pkl").exists():
                checks.append(CheckItem("CP-06", "error", "graph metadata missing while relations exist"))
            else:
                matrix_format = str(_get_nested(config_doc, ("graph", "sparse_matrix_format"), "csr") or "csr")
                graph_store = GraphStore(matrix_format=matrix_format, data_dir=graph_dir)
                graph_store.load()
                if not graph_store.has_edge_hash_map():
                    checks.append(CheckItem("CP-06", "error", "edge_hash_map is empty"))
    except Exception as e:
        checks.append(CheckItem("CP-08", "error", f"metadata strict connect failed: {e}"))
    finally:
        try:
            store.close()
        except Exception:
            pass

    has_error = any(c.level == "error" for c in checks)
    return {
        "ok": not has_error,
        "checks": [c.to_dict() for c in checks],
        "facts": facts,
    }


def _print_report(title: str, report: Dict[str, Any]) -> None:
    print(f"=== {title} ===")
    print(f"ok: {bool(report.get('ok', True))}")
    facts = report.get("facts", {})
    if facts:
        print("facts:")
        for k in sorted(facts.keys()):
            print(f"  - {k}: {facts[k]}")
    checks = report.get("checks", [])
    if checks:
        print("checks:")
        for item in checks:
            print(f"  - [{item.get('level')}] {item.get('code')}: {item.get('message')}")
    else:
        print("checks: none")


def _write_json_if_needed(path: str, payload: Dict[str, Any]) -> None:
    if not path:
        return
    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"json_out: {out}")


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    config_path = Path(args.config).expanduser().resolve()
    if not config_path.exists():
        print(f"❌ config not found: {config_path}")
        return 2
    config_doc = _read_toml(config_path)
    data_dir = _resolve_data_dir(config_doc, args.data_dir)

    if args.command == "preflight":
        report = _preflight_impl(config_path, data_dir)
        _print_report("vNext Preflight", report)
        _write_json_if_needed(args.json_out, report)
        has_error = any(item.get("level") == "error" for item in report.get("checks", []))
        if args.strict and has_error:
            return 1
        return 0

    if args.command == "migrate":
        payload = _migrate_impl(config_path, data_dir, dry_run=bool(args.dry_run))
        print("=== vNext Migrate ===")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        verify_report = None
        if args.verify_after and not args.dry_run:
            verify_report = _verify_impl(config_path, data_dir)
            _print_report("vNext Verify (after migrate)", verify_report)
            payload["verify_after"] = verify_report

        _write_json_if_needed(args.json_out, payload)
        if verify_report is not None:
            has_error = any(item.get("level") == "error" for item in verify_report.get("checks", []))
            if has_error:
                return 1
        return 0

    if args.command == "verify":
        report = _verify_impl(config_path, data_dir)
        _print_report("vNext Verify", report)
        _write_json_if_needed(args.json_out, report)
        has_error = any(item.get("level") == "error" for item in report.get("checks", []))
        if args.strict and has_error:
            return 1
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
