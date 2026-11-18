"""持久化轮询游标、失败更新、贴纸引用与话题路由，所有 SQLite 操作在线程中完成。"""

from pathlib import Path
from typing import Any, Dict, Optional

import asyncio
import json
import sqlite3
import time


class AdapterStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS cursors (account TEXT PRIMARY KEY, offset INTEGER NOT NULL, updated_at REAL NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS stickers (account TEXT, hash TEXT, file_id TEXT NOT NULL, PRIMARY KEY(account, hash))"
            )

            db.execute(
                "CREATE TABLE IF NOT EXISTS message_groups (account TEXT, message_id TEXT, group_id TEXT NOT NULL, group_name TEXT NOT NULL, PRIMARY KEY(account, message_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS topics (account TEXT, chat_id TEXT, thread_id INTEGER, name TEXT NOT NULL, PRIMARY KEY(account, chat_id, thread_id))"
            )

            db.execute(
                "CREATE TABLE IF NOT EXISTS failed_updates (account TEXT, update_id INTEGER, payload TEXT NOT NULL, error TEXT NOT NULL, traceback TEXT NOT NULL, failed_at REAL NOT NULL, PRIMARY KEY(account, update_id))"
            )

    async def save_failed_update(
        self, account: str, update: Dict[str, Any], error: str, traceback_text: str
    ) -> None:
        await asyncio.to_thread(self._save_failed_update, account, update, error, traceback_text)

    def _save_failed_update(
        self, account: str, update: Dict[str, Any], error: str, traceback_text: str
    ) -> None:
        # 先保留原始更新再确认游标，二者原子提交；数据库失败时仍保留 Telegram 未确认更新。
        with sqlite3.connect(self.path) as db:
            now = time.time()
            db.execute(
                "INSERT OR REPLACE INTO failed_updates VALUES (?, ?, ?, ?, ?, ?)",
                (account, update["update_id"], json.dumps(update, ensure_ascii=False), error, traceback_text, now),
            )
            db.execute("INSERT OR REPLACE INTO cursors VALUES (?, ?, ?)", (account, update["update_id"] + 1, now))

    async def failed_update(self, account: str, update_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._failed_update, account, update_id)

    def _failed_update(self, account: str, update_id: int) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT payload, error, traceback, failed_at FROM failed_updates WHERE account = ? AND update_id = ?",
                (account, update_id),
            ).fetchone()
        return {"update": json.loads(row[0]), "error": row[1], "traceback": row[2], "failed_at": row[3]} if row else None

    async def save_message_group(self, account: str, message_id: str, group: Dict[str, str]) -> None:
        await asyncio.to_thread(self._save_message_group, account, message_id, group)

    def _save_message_group(self, account: str, message_id: str, group: Dict[str, str]) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO message_groups VALUES (?, ?, ?, ?)",
                       (account, message_id, group["group_id"], group["group_name"]))

    async def message_group(self, account: str, message_id: str) -> Optional[Dict[str, str]]:
        return await asyncio.to_thread(self._message_group, account, message_id)

    def _message_group(self, account: str, message_id: str) -> Optional[Dict[str, str]]:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT group_id, group_name FROM message_groups WHERE account = ? AND message_id = ?",
                             (account, message_id)).fetchone()
        return {"group_id": row[0], "group_name": row[1]} if row else None

    async def topic_name(self, account: str, chat_id: str, thread_id: int, name: str = "") -> str:
        return await asyncio.to_thread(self._topic_name, account, chat_id, thread_id, name)

    def _topic_name(self, account: str, chat_id: str, thread_id: int, name: str) -> str:
        with sqlite3.connect(self.path) as db:
            if name:
                db.execute("INSERT OR REPLACE INTO topics VALUES (?, ?, ?, ?)", (account, chat_id, thread_id, name))
            row = db.execute("SELECT name FROM topics WHERE account = ? AND chat_id = ? AND thread_id = ?",
                             (account, chat_id, thread_id)).fetchone()
        return row[0] if row else ""

    async def offset(self, account: str) -> int:
        return await asyncio.to_thread(self._offset, account)

    def _offset(self, account: str) -> int:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT offset, updated_at FROM cursors WHERE account = ?", (account,)).fetchone()
        # Telegram 在一周没有更新后会随机选择新的 update_id；旧高位 offset 不能继续使用。
        return row[0] if row and time.time() - row[1] < 7 * 86400 else 0

    async def save_offset(self, account: str, offset: int) -> None:
        await asyncio.to_thread(self._save_offset, account, offset)

    def _save_offset(self, account: str, offset: int) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO cursors VALUES (?, ?, ?)", (account, offset, time.time()))

    async def sticker(self, account: str, binary_hash: str) -> Optional[str]:
        return await asyncio.to_thread(self._sticker, account, binary_hash)

    def _sticker(self, account: str, binary_hash: str) -> Optional[str]:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT file_id FROM stickers WHERE account = ? AND hash = ?", (account, binary_hash)
            ).fetchone()
        return row[0] if row else None

    async def save_sticker(self, account: str, binary_hash: str, file_id: str) -> None:
        await asyncio.to_thread(self._save_sticker, account, binary_hash, file_id)

    def _save_sticker(self, account: str, binary_hash: str, file_id: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO stickers VALUES (?, ?, ?)", (account, binary_hash, file_id))
