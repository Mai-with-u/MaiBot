#!/usr/bin/env python3
"""
关系向量一次性回填脚本（灰度/离线执行）。

用途：
1. 对 relations 中 vector_state in (none, failed, pending) 的记录补齐向量。
2. 支持并发控制，降低总耗时。
3. 可作为灰度阶段验证工具，与 audit_vector_consistency.py 配合使用。
4. 可选自动纳入“ready 但向量缺失”的漂移记录进行修复。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import tomlkit


CURRENT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = CURRENT_DIR.parent
PROJECT_ROOT = PLUGIN_ROOT.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="关系向量一次性回填")
    parser.add_argument(
        "--config",
        default=str(PLUGIN_ROOT / "config.toml"),
        help="配置文件路径（默认 plugins/A_memorix/config.toml）",
    )
    parser.add_argument(
        "--data-dir",
        default=str(PLUGIN_ROOT / "data"),
        help="数据目录（默认 plugins/A_memorix/data）",
    )
    parser.add_argument(
        "--states",
        default="none,failed,pending",
        help="待处理状态列表，逗号分隔",
    )
    parser.add_argument("--limit", type=int, default=50000, help="最大处理数量")
    parser.add_argument("--concurrency", type=int, default=8, help="并发数")
    parser.add_argument("--max-retry", type=int, default=None, help="最大重试次数过滤")
    parser.add_argument(
        "--include-ready-missing",
        action="store_true",
        help="额外纳入 vector_state=ready 但向量缺失的关系",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅统计候选，不写入")
    return parser


# --help/-h fast path: avoid heavy host/plugin bootstrap
if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    _build_arg_parser().print_help()
    raise SystemExit(0)

from core.storage import (
    VectorStore,
    GraphStore,
    MetadataStore,
    QuantizationType,
    SparseMatrixFormat,
)
from core.embedding import create_embedding_api_adapter
from core.utils.relation_write_service import RelationWriteService


def _load_config(config_path: Path) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        raw = tomlkit.load(f)
    return dict(raw) if isinstance(raw, dict) else {}


def _build_vector_store(data_dir: Path, emb_cfg: Dict[str, Any]) -> VectorStore:
    q_type = str(emb_cfg.get("quantization_type", "int8")).lower()
    if q_type != "int8":
        raise ValueError(
            "embedding.quantization_type 在 vNext 仅允许 int8(SQ8)。"
            " 请先执行 scripts/release_vnext_migrate.py migrate。"
        )
    dim = int(emb_cfg.get("dimension", 1024))
    store = VectorStore(
        dimension=max(1, dim),
        quantization_type=QuantizationType.INT8,
        data_dir=data_dir / "vectors",
    )
    if store.has_data():
        store.load()
    return store


def _build_graph_store(data_dir: Path, graph_cfg: Dict[str, Any]) -> GraphStore:
    fmt = str(graph_cfg.get("sparse_matrix_format", "csr")).lower()
    fmt_map = {
        "csr": SparseMatrixFormat.CSR,
        "csc": SparseMatrixFormat.CSC,
    }
    store = GraphStore(
        matrix_format=fmt_map.get(fmt, SparseMatrixFormat.CSR),
        data_dir=data_dir / "graph",
    )
    if store.has_data():
        store.load()
    return store


def _build_metadata_store(data_dir: Path) -> MetadataStore:
    store = MetadataStore(data_dir=data_dir / "metadata")
    store.connect()
    return store


def _build_embedding_manager(emb_cfg: Dict[str, Any]):
    retry_cfg = emb_cfg.get("retry", {})
    if not isinstance(retry_cfg, dict):
        retry_cfg = {}
    return create_embedding_api_adapter(
        batch_size=int(emb_cfg.get("batch_size", 32)),
        max_concurrent=int(emb_cfg.get("max_concurrent", 5)),
        default_dimension=int(emb_cfg.get("dimension", 1024)),
        model_name=str(emb_cfg.get("model_name", "auto")),
        retry_config=retry_cfg,
    )


async def _process_rows(
    service: RelationWriteService,
    rows: List[Dict[str, Any]],
    concurrency: int,
) -> Dict[str, int]:
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))
    stat = {"success": 0, "failed": 0, "skipped": 0}

    async def _worker(row: Dict[str, Any]) -> None:
        async with semaphore:
            result = await service.ensure_relation_vector(
                hash_value=str(row["hash"]),
                subject=str(row.get("subject", "")),
                predicate=str(row.get("predicate", "")),
                obj=str(row.get("object", "")),
            )
            if result.vector_state == "ready":
                if result.vector_written:
                    stat["success"] += 1
                else:
                    stat["skipped"] += 1
            else:
                stat["failed"] += 1

    await asyncio.gather(*[_worker(row) for row in rows])
    return stat


async def main_async(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"❌ 配置文件不存在: {config_path}")
        return 2

    cfg = _load_config(config_path)
    emb_cfg = cfg.get("embedding", {}) if isinstance(cfg, dict) else {}
    graph_cfg = cfg.get("graph", {}) if isinstance(cfg, dict) else {}
    retrieval_cfg = cfg.get("retrieval", {}) if isinstance(cfg, dict) else {}
    rv_cfg = retrieval_cfg.get("relation_vectorization", {}) if isinstance(retrieval_cfg, dict) else {}
    if not isinstance(emb_cfg, dict):
        emb_cfg = {}
    if not isinstance(graph_cfg, dict):
        graph_cfg = {}
    if not isinstance(rv_cfg, dict):
        rv_cfg = {}

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.exists():
        print(f"❌ 数据目录不存在: {data_dir}")
        return 2

    print(f"data_dir: {data_dir}")
    print(f"config: {config_path}")

    vector_store = _build_vector_store(data_dir, emb_cfg)
    graph_store = _build_graph_store(data_dir, graph_cfg)
    metadata_store = _build_metadata_store(data_dir)
    embedding_manager = _build_embedding_manager(emb_cfg)
    service = RelationWriteService(
        metadata_store=metadata_store,
        graph_store=graph_store,
        vector_store=vector_store,
        embedding_manager=embedding_manager,
    )

    try:
        states = [s.strip() for s in str(args.states).split(",") if s.strip()]
        if not states:
            states = ["none", "failed", "pending"]
        max_retry = int(args.max_retry) if args.max_retry is not None else int(rv_cfg.get("max_retry", 3))
        limit = int(args.limit)

        rows = metadata_store.list_relations_by_vector_state(
            states=states,
            limit=max(1, limit),
            max_retry=max(1, max_retry),
        )
        added_ready_missing = 0
        if args.include_ready_missing:
            ready_rows = metadata_store.list_relations_by_vector_state(
                states=["ready"],
                limit=max(1, limit),
                max_retry=max(1, max_retry),
            )
            ready_missing_rows = [
                row for row in ready_rows if str(row.get("hash", "")) not in vector_store
            ]
            added_ready_missing = len(ready_missing_rows)
            if ready_missing_rows:
                dedup: Dict[str, Dict[str, Any]] = {}
                for row in rows:
                    dedup[str(row.get("hash", ""))] = row
                for row in ready_missing_rows:
                    dedup.setdefault(str(row.get("hash", "")), row)
                rows = list(dedup.values())[: max(1, limit)]
        print(f"candidates: {len(rows)} (states={states}, max_retry={max_retry})")
        if args.include_ready_missing:
            print(f"ready_missing_candidates_added: {added_ready_missing}")
        if not rows:
            return 0

        if args.dry_run:
            print("dry_run=true，未执行写入。")
            return 0

        started = time.time()
        stat = await _process_rows(
            service=service,
            rows=rows,
            concurrency=int(args.concurrency),
        )
        elapsed = (time.time() - started) * 1000.0

        vector_store.save()
        graph_store.save()
        state_stats = metadata_store.count_relations_by_vector_state()
        output = {
            "processed": len(rows),
            "success": int(stat["success"]),
            "failed": int(stat["failed"]),
            "skipped": int(stat["skipped"]),
            "elapsed_ms": elapsed,
            "state_stats": state_stats,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if stat["failed"] == 0 else 1
    finally:
        metadata_store.close()


def parse_args() -> argparse.Namespace:
    return _build_arg_parser().parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(asyncio.run(main_async(arguments)))
