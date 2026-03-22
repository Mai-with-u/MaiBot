#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List


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
    parser = argparse.ArgumentParser(description="迁移 MaiBot person_info.memory_points 到 A_Memorix")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="MaiBot SQLite 路径")
    parser.add_argument("--data-dir", default="./data", help="A_Memorix 数据目录")
    parser.add_argument("--limit", type=int, default=0, help="限制迁移人数，0 表示全部")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入")
    return parser.parse_args()


def _parse_memory_points(raw_value: Any) -> List[Dict[str, Any]]:
    try:
        values = json.loads(raw_value) if raw_value else []
    except Exception:
        values = []
    items: List[Dict[str, Any]] = []
    for index, item in enumerate(values):
        text = str(item or "").strip()
        if not text:
            continue
        parts = text.split(":")
        if len(parts) >= 3:
            category = parts[0].strip()
            content = ":".join(parts[1:-1]).strip()
            weight = parts[-1].strip()
        else:
            category = "其他"
            content = text
            weight = "1.0"
        if content:
            items.append({"index": index, "category": category or "其他", "content": content, "weight": weight or "1.0"})
    return items


async def _main() -> int:
    args = _parse_args()
    db_path = Path(args.db_path).resolve()
    if not db_path.exists():
        print(f"数据库不存在: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT person_id, person_name, user_nickname, memory_points
        FROM person_info
        WHERE memory_points IS NOT NULL AND memory_points != ''
        ORDER BY id ASC
    """
    if int(args.limit or 0) > 0:
        sql += " LIMIT ?"
        rows = conn.execute(sql, (int(args.limit),)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()
    conn.close()

    preview_total = sum(len(_parse_memory_points(row["memory_points"])) for row in rows)
    print(f"person_info 待迁移人物: {len(rows)} 记忆点: {preview_total}")
    if args.dry_run:
        for row in rows[:5]:
            print(f"- person_id={row['person_id']} person_name={row['person_name'] or row['user_nickname']}")
        return 0

    kernel = SDKMemoryKernel(plugin_root=PLUGIN_ROOT, config={"storage": {"data_dir": args.data_dir}})
    await kernel.initialize()
    migrated = 0
    skipped = 0
    for row in rows:
        person_id = str(row["person_id"] or "").strip()
        if not person_id:
            continue
        display_name = str(row["person_name"] or row["user_nickname"] or "").strip()
        for item in _parse_memory_points(row["memory_points"]):
            result: Dict[str, Any] = await kernel.ingest_text(
                external_id=f"person_memory:{person_id}:{item['index']}",
                source_type="person_fact",
                text=f"[{item['category']}] {item['content']}",
                person_ids=[person_id],
                tags=[item["category"]],
                entities=[person_id, display_name] if display_name else [person_id],
                metadata={"category": item["category"], "weight": item["weight"], "display_name": display_name},
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
