"""从原始用户历史重建已有聊天的高频词；默认预览，--apply 备份后写入。

旧词库没有逐消息来源，不能按关键词精确扣除通知贡献。重建会重新统计
已有词库聊天的全部历史正文，次数不再表示原来的裁切批次采样次数。
"""

# ruff: noqa: E402

from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

import json
import sqlite3
import sys

import msgpack

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.common.data_models.message_component_data_model import TextComponent
from src.services.bot_account_service import get_all_bot_account_pairs
from src.services.message_word_frequency_service import (
    _build_message_word_frequency_terms,
    _extract_text_from_maisaka_components,
)


def build_rebuilt_rows(
    connection: sqlite3.Connection,
    bot_accounts: Set[Tuple[str, str]],
    generated_at: str,
) -> Tuple[List[Tuple[Any, ...]], Dict[str, Any]]:
    """在只读快照中统计真实正文，保留每个聊天前 1000 条。"""
    chat_ids = [row[0] for row in connection.execute("SELECT DISTINCT chat_id FROM high_frequency_terms")]
    rows: List[Tuple[Any, ...]] = []
    report: Dict[str, Any] = {
        "chats": len(chat_ids),
        "user_messages": 0,
        "excluded_notifications": 0,
        "excluded_self_messages": 0,
        "chats_detail": [],
    }
    for chat_id in chat_ids:

        def texts(chat_id: str = chat_id) -> Iterable[str]:
            # 读取原始 msgpack 的顶层 text，忽略媒体说明、引用及转发节点。
            # 按平台消息 ID 去重，与在线批次提取采用相同的消息身份。
            seen_messages: Set[Tuple[str, str]] = set()
            query = (
                "SELECT platform,user_id,is_notify,message_id,raw_content FROM mai_messages "
                "WHERE session_id=? ORDER BY id"
            )
            for platform, user_id, is_notify, message_id, raw_content in connection.execute(query, (chat_id,)):
                if is_notify:
                    report["excluded_notifications"] += 1
                    continue
                if (platform.strip().lower(), user_id.strip()) in bot_accounts:
                    report["excluded_self_messages"] += 1
                    continue
                message_key = (platform, message_id)
                if message_id and message_key in seen_messages:
                    continue
                if message_id:
                    seen_messages.add(message_key)
                components = [
                    TextComponent(item["data"]) for item in msgpack.unpackb(raw_content) if item["type"] == "text"
                ]
                text = _extract_text_from_maisaka_components(components)
                if text:
                    report["user_messages"] += 1
                    yield text

        terms = _build_message_word_frequency_terms(texts(), limit=1000, min_count=2)
        occurrence_total = sum(item.count for item in terms)
        message_total = sum(item.message_count for item in terms)
        for rank, item in enumerate(terms, 1):
            rows.append(
                (
                    chat_id,
                    item.term,
                    rank,
                    item.count,
                    item.message_count,
                    item.count / occurrence_total,
                    item.message_count / message_total,
                    generated_at,
                    generated_at,
                )
            )
        report["chats_detail"].append(
            {"chat_id": chat_id, "terms": len(terms), "top": [(item.term, item.count) for item in terms[:8]]}
        )
        print(f"Rebuilt {len(report['chats_detail'])}/{len(chat_ids)} chats", flush=True)
    report["rebuilt_terms"] = len(rows)
    return rows, report


def apply_rebuilt_rows(
    connection: sqlite3.Connection,
    rows: List[Tuple[Any, ...]],
    expected_rows: List[Tuple[Any, ...]],
    backup_path: Path,
) -> None:
    """词库发生并发变化时中止；备份成功后在短事务内替换词库。"""
    connection.execute("BEGIN IMMEDIATE")
    try:
        current_rows = connection.execute("SELECT * FROM high_frequency_terms ORDER BY id").fetchall()
        if current_rows != expected_rows:
            raise RuntimeError("重建期间词库发生变化，请停止旧版学习进程后重试；没有改动词库")
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='high_frequency_terms'"
        ).fetchone()[0]
        # 独立 SQLite 文件只备份词库，不复制数 GB 的运行数据库。
        with sqlite3.connect(backup_path) as backup:
            backup.execute(schema)
            placeholders = ",".join("?" for _ in connection.execute("PRAGMA table_info(high_frequency_terms)"))
            backup.executemany(f"INSERT INTO high_frequency_terms VALUES ({placeholders})", current_rows)
        connection.execute("DELETE FROM high_frequency_terms")
        connection.executemany(
            "INSERT INTO high_frequency_terms "
            "(chat_id,term,rank,occurrence_count,message_count,frequency,message_frequency,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="备份旧词库并写入重建结果")
    args = parser.parse_args()
    db_path = PROJECT_ROOT / "data" / "MaiBot.db"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    generated_at = datetime.now().isoformat(sep=" ")
    accounts = get_all_bot_account_pairs()
    with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as source:
        source.execute("BEGIN")
        expected_rows = source.execute("SELECT * FROM high_frequency_terms ORDER BY id").fetchall()
        rows, report = build_rebuilt_rows(source, accounts, generated_at)
    report["previous_terms"] = len(expected_rows)
    report["applied"] = False
    output_dir = PROJECT_ROOT / "data" / "high_frequency_repairs"
    output_dir.mkdir(exist_ok=True)
    if args.apply:
        backup_path = output_dir / f"before_{stamp}.sqlite3"
        with sqlite3.connect(db_path, timeout=10) as target:
            apply_rebuilt_rows(target, rows, expected_rows, backup_path)
        report.update(applied=True, backup=str(backup_path))
    report_path = output_dir / f"report_{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "chats_detail"}, ensure_ascii=True))
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
