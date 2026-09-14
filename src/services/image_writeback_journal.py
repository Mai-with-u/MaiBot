from pathlib import Path
from typing import Any, Dict, List, Optional

import sqlite3
import time


class ImageWritebackJournal:
    """持久化接收侧图片任务；消息内容仍从聊天记录读取，不重复保存图片。"""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS image_writeback_jobs (
                session_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                retry_at REAL NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL,
                PRIMARY KEY (session_id, message_id)
            )
        """)
        self.connection.commit()

    def enqueue(self, session_id: str, message_id: str) -> None:
        if not session_id or not message_id:
            raise ValueError("图片入库任务缺少真实聊天流或消息 ID")
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO image_writeback_jobs(session_id,message_id,updated_at) VALUES (?,?,?)",
                (session_id, message_id, time.time()),
            )

    def next_job(self) -> Optional[Dict[str, Any]]:
        # 成功前始终保持 pending，进程退出不会丢失正在处理的任务。
        row = self.connection.execute(
            "SELECT * FROM image_writeback_jobs WHERE status='pending' AND retry_at<=? "
            "ORDER BY retry_at,updated_at LIMIT 1", (time.time(),),
        ).fetchone()
        return dict(row) if row is not None else None

    def complete(self, session_id: str, message_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE image_writeback_jobs SET status='done',last_error='',updated_at=? "
                "WHERE session_id=? AND message_id=?", (time.time(), session_id, message_id),
            )

    def fail(self, job: Dict[str, Any], error: str, max_attempts: int) -> None:
        attempts = int(job['attempts']) + 1
        now = time.time()
        with self.connection:
            self.connection.execute(
                "UPDATE image_writeback_jobs SET status=?,attempts=?,retry_at=?,last_error=?,updated_at=? "
                "WHERE session_id=? AND message_id=?",
                (
                    'failed' if attempts >= max_attempts else 'pending', attempts,
                    now + min(300, 2 ** min(attempts, 9)), error, now,
                    job['session_id'], job['message_id'],
                ),
            )

    def list_jobs(self, status: str, limit: int, offset: int) -> Dict[str, Any]:
        where = 'WHERE status=?' if status else ''
        parameters = (status,) if status else ()
        total = self.connection.execute(
            f'SELECT COUNT(*) FROM image_writeback_jobs {where}', parameters,
        ).fetchone()[0]
        rows = self.connection.execute(
            f'SELECT * FROM image_writeback_jobs {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?',
            (*parameters, limit, offset),
        ).fetchall()
        items: List[Dict[str, Any]] = [dict(row) for row in rows]
        return {'success': True, 'items': items, 'total': total}

    def retry_failed(self) -> int:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE image_writeback_jobs SET status='pending',attempts=0,retry_at=0,updated_at=? "
                "WHERE status='failed'", (time.time(),),
            )
        return cursor.rowcount

    def close(self) -> None:
        self.connection.close()
