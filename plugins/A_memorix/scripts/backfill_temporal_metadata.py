#!/usr/bin/env python3
"""
回填段落时序字段。

默认策略：
1. 若段落缺失 event_time/event_time_start/event_time_end
2. 且存在 created_at
3. 写入 event_time=created_at, time_granularity=day, time_confidence=0.2
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


CURRENT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = CURRENT_DIR.parent
PROJECT_ROOT = PLUGIN_ROOT.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.A_memorix.core.storage import MetadataStore  # noqa: E402


def backfill(
    data_dir: Path,
    dry_run: bool,
    limit: int,
    no_created_fallback: bool,
) -> int:
    store = MetadataStore(data_dir=data_dir)
    store.connect()
    summary = store.backfill_temporal_metadata_from_created_at(
        limit=limit,
        dry_run=dry_run,
        no_created_fallback=no_created_fallback,
    )
    store.close()
    if dry_run:
        print(f"[dry-run] candidates={summary['candidates']}")
        return int(summary["candidates"])
    if no_created_fallback:
        print(f"skip update (no-created-fallback), candidates={summary['candidates']}")
        return 0
    print(f"updated={summary['updated']}")
    return int(summary["updated"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill temporal metadata for A_Memorix paragraphs")
    parser.add_argument("--data-dir", default=str(PLUGIN_ROOT / "data"), help="数据目录")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写入")
    parser.add_argument("--limit", type=int, default=100000, help="最大处理条数")
    parser.add_argument(
        "--no-created-fallback",
        action="store_true",
        help="不使用 created_at 回填，仅输出候选数量",
    )
    args = parser.parse_args()

    backfill(
        data_dir=Path(args.data_dir),
        dry_run=args.dry_run,
        limit=max(1, int(args.limit)),
        no_created_fallback=args.no_created_fallback,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

