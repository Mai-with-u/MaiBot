#!/usr/bin/env python3
"""
A_Memorix 一致性审计脚本。

输出内容：
1. paragraph/entity/relation 向量覆盖率
2. relation vector_state 分布
3. 孤儿向量数量（向量存在但 metadata 不存在）
4. 状态与向量文件不一致统计
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path
from typing import Any, Dict, Set


CURRENT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = CURRENT_DIR.parent
PROJECT_ROOT = PLUGIN_ROOT.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="审计 A_Memorix 向量一致性")
    parser.add_argument(
        "--data-dir",
        default=str(PLUGIN_ROOT / "data"),
        help="A_Memorix 数据目录（默认: plugins/A_memorix/data）",
    )
    parser.add_argument("--json-out", default="", help="可选：输出 JSON 文件路径")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="若发现一致性异常则返回非 0 退出码",
    )
    return parser


# --help/-h fast path: avoid heavy host/plugin bootstrap
if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    _build_arg_parser().print_help()
    sys.exit(0)

try:
    from core.storage.vector_store import VectorStore
    from core.storage.metadata_store import MetadataStore
    from core.storage import QuantizationType
except Exception as e:  # pragma: no cover
    print(f"❌ 导入核心模块失败: {e}")
    sys.exit(1)


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _load_vector_store(data_dir: Path) -> VectorStore:
    meta_path = data_dir / "vectors" / "vectors_metadata.pkl"
    if not meta_path.exists():
        raise FileNotFoundError(f"未找到向量元数据文件: {meta_path}")

    with open(meta_path, "rb") as f:
        meta = pickle.load(f)
    dimension = int(meta.get("dimension", 1024))

    store = VectorStore(
        dimension=max(1, dimension),
        quantization_type=QuantizationType.INT8,
        data_dir=data_dir / "vectors",
    )
    if store.has_data():
        store.load()
    return store


def _load_metadata_store(data_dir: Path) -> MetadataStore:
    store = MetadataStore(data_dir=data_dir / "metadata")
    store.connect()
    return store


def _hash_set(metadata_store: MetadataStore, table: str) -> Set[str]:
    return {str(h) for h in metadata_store.list_hashes(table)}


def _relation_state_stats(metadata_store: MetadataStore) -> Dict[str, int]:
    return metadata_store.count_relations_by_vector_state()


def run_audit(data_dir: Path) -> Dict[str, Any]:
    vector_store = _load_vector_store(data_dir)
    metadata_store = _load_metadata_store(data_dir)
    try:
        paragraph_hashes = _hash_set(metadata_store, "paragraphs")
        entity_hashes = _hash_set(metadata_store, "entities")
        relation_hashes = _hash_set(metadata_store, "relations")

        known_hashes = set(getattr(vector_store, "_known_hashes", set()))
        live_vector_hashes = {h for h in known_hashes if h in vector_store}

        para_vector_hits = len(paragraph_hashes & live_vector_hashes)
        ent_vector_hits = len(entity_hashes & live_vector_hashes)
        rel_vector_hits = len(relation_hashes & live_vector_hashes)

        orphan_vector_hashes = sorted(
            live_vector_hashes - paragraph_hashes - entity_hashes - relation_hashes
        )

        relation_rows = metadata_store.get_relations()
        ready_but_missing = 0
        not_ready_but_present = 0
        for row in relation_rows:
            h = str(row.get("hash") or "")
            state = str(row.get("vector_state") or "none").lower()
            in_vector = h in live_vector_hashes
            if state == "ready" and not in_vector:
                ready_but_missing += 1
            if state != "ready" and in_vector:
                not_ready_but_present += 1

        relation_states = _relation_state_stats(metadata_store)
        rel_total = max(0, int(relation_states.get("total", len(relation_hashes))))
        ready_count = max(0, int(relation_states.get("ready", 0)))

        result = {
            "counts": {
                "paragraphs": len(paragraph_hashes),
                "entities": len(entity_hashes),
                "relations": len(relation_hashes),
                "vectors_live": len(live_vector_hashes),
            },
            "coverage": {
                "paragraph_vector_coverage": _safe_ratio(para_vector_hits, len(paragraph_hashes)),
                "entity_vector_coverage": _safe_ratio(ent_vector_hits, len(entity_hashes)),
                "relation_vector_coverage": _safe_ratio(rel_vector_hits, len(relation_hashes)),
                "relation_ready_coverage": _safe_ratio(ready_count, rel_total),
            },
            "relation_states": relation_states,
            "orphans": {
                "vector_only_count": len(orphan_vector_hashes),
                "vector_only_sample": orphan_vector_hashes[:30],
            },
            "consistency_checks": {
                "ready_but_missing_vector": ready_but_missing,
                "not_ready_but_vector_present": not_ready_but_present,
            },
        }
        return result
    finally:
        metadata_store.close()


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.exists():
        print(f"❌ 数据目录不存在: {data_dir}")
        return 2

    try:
        result = run_audit(data_dir)
    except Exception as e:
        print(f"❌ 审计失败: {e}")
        return 2

    print("=== A_Memorix Vector Consistency Audit ===")
    print(f"data_dir: {data_dir}")
    print(f"paragraphs: {result['counts']['paragraphs']}")
    print(f"entities: {result['counts']['entities']}")
    print(f"relations: {result['counts']['relations']}")
    print(f"vectors_live: {result['counts']['vectors_live']}")
    print(
        "coverage: "
        f"paragraph={result['coverage']['paragraph_vector_coverage']:.3f}, "
        f"entity={result['coverage']['entity_vector_coverage']:.3f}, "
        f"relation={result['coverage']['relation_vector_coverage']:.3f}, "
        f"relation_ready={result['coverage']['relation_ready_coverage']:.3f}"
    )
    print(f"relation_states: {result['relation_states']}")
    print(
        "consistency_checks: "
        f"ready_but_missing_vector={result['consistency_checks']['ready_but_missing_vector']}, "
        f"not_ready_but_vector_present={result['consistency_checks']['not_ready_but_vector_present']}"
    )
    print(f"orphan_vectors: {result['orphans']['vector_only_count']}")

    if args.json_out:
        out_path = Path(args.json_out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"json_out: {out_path}")

    has_anomaly = (
        result["orphans"]["vector_only_count"] > 0
        or result["consistency_checks"]["ready_but_missing_vector"] > 0
    )
    if args.strict and has_anomaly:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
