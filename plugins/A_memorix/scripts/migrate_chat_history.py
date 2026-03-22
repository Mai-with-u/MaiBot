#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


CURRENT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = CURRENT_DIR.parent
WORKSPACE_ROOT = PLUGIN_ROOT.parent
MAIBOT_ROOT = WORKSPACE_ROOT / "MaiBot"
DEFAULT_DB_PATH = MAIBOT_ROOT / "data" / "MaiBot.db"

if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
if str(MAIBOT_ROOT) not in sys.path:
    sys.path.insert(0, str(MAIBOT_ROOT))

from A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="迁移 MaiBot chat_history 到 A_Memorix")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="MaiBot SQLite 路径")
    parser.add_argument("--data-dir", default="./data", help="A_Memorix 数据目录")
    parser.add_argument("--limit", type=int, default=0, help="限制迁移条数，0 表示全部")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入")
    return parser.parse_args()


def _to_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


async def _main() -> int:
    args = _parse_args()
    db_path = Path(args.db_path).resolve()
    if not db_path.exists():
        print(f"数据库不存在: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT id, session_id, start_timestamp, end_timestamp, participants, theme, keywords, summary
        FROM chat_history
        ORDER BY id ASC
    """
    if int(args.limit or 0) > 0:
        sql += " LIMIT ?"
        rows = conn.execute(sql, (int(args.limit),)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()
    conn.close()

    print(f"chat_history 待处理: {len(rows)}")
    if args.dry_run:
        for row in rows[:5]:
            print(f"- id={row['id']} session={row['session_id']} theme={row['theme']}")
        return 0

    kernel = SDKMemoryKernel(plugin_root=PLUGIN_ROOT, config={"storage": {"data_dir": args.data_dir}})
    await kernel.initialize()
    migrated = 0
    skipped = 0
    for row in rows:
        participants = json.loads(row["participants"]) if row["participants"] else []
        keywords = json.loads(row["keywords"]) if row["keywords"] else []
        theme = str(row["theme"] or "").strip()
        summary = str(row["summary"] or "").strip()
        text = f"主题：{theme}\n概括：{summary}".strip()
        result: Dict[str, Any] = await kernel.ingest_summary(
            external_id=f"chat_history:{row['id']}",
            chat_id=str(row["session_id"] or ""),
            text=text,
            participants=participants,
            time_start=_to_timestamp(row["start_timestamp"]),
            time_end=_to_timestamp(row["end_timestamp"]),
            tags=keywords,
            metadata={"theme": theme, "source_row_id": int(row["id"])},
        )
        if result.get("stored_ids"):
            migrated += 1
        else:
            skipped += 1

    print(f"迁移完成: migrated={migrated} skipped={skipped}")
    print(json.dumps(kernel.memory_stats(), ensure_ascii=False))
    kernel.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
