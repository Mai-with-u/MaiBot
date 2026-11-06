"""持久登录会话；单次交接票据仍仅在本次宿主运行中有效。"""

from contextlib import closing
from hashlib import sha256
from pathlib import Path
from typing import Dict, NamedTuple, Optional

from fastapi import HTTPException, Response

import hmac
import secrets
import sqlite3
import threading
import time

from .authentication import HostCredentials


_SESSION_SECONDS = 8 * 60 * 60
_TICKET_SECONDS = 60
_MAX_TICKETS = 64
_MAX_SESSIONS = 64


class _Session(NamedTuple):
    expires_at: float
    origin: Optional[str]


class HostSessions:
    """数据库只保存票号摘要与绝对过期时间，认证走内存，不阻塞事件循环。

    摘要绑定当前密码：改密码或临时密码轮换后，即使清理会话前进程退出，旧票号也无法恢复。
    写方法包含同步数据库操作，异步调用方必须使用 asyncio.to_thread。
    """

    def __init__(self, workspace: Path, *, credentials: HostCredentials, secure_cookie: bool = False) -> None:
        suffix = sha256(str(workspace).encode("utf-8")).hexdigest()[:12]
        self.cookie_name = f"maibot_host_session_{suffix}"
        self.selection_cookie = f"maibot_host_selection_{suffix}"
        self.secure_cookie = secure_cookie
        self._credentials = credentials
        self._lock = threading.RLock()
        self._tickets: Dict[str, float] = {}
        self._path = credentials.path.parent / "sessions.sqlite3"
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires_at REAL NOT NULL)")
            if "origin" not in {row[1] for row in db.execute("PRAGMA table_info(sessions)")}:
                db.execute("ALTER TABLE sessions ADD COLUMN origin TEXT")
            # 临时密码每次启动都会轮换，不恢复上一进程的登录。
            if credentials.token_source == "temporary":
                db.execute("DELETE FROM sessions")
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
            self._sessions: Dict[str, _Session] = {
                digest: _Session(expires_at, origin)
                for digest, expires_at, origin in db.execute("SELECT digest, expires_at, origin FROM sessions")
            }

    def _digest(self, session: str) -> str:
        return hmac.new(self._credentials.token.encode("utf-8"), session.encode("utf-8"), sha256).hexdigest()

    @staticmethod
    def _prune(entries: Dict[str, float], now: float) -> None:
        for key, deadline in list(entries.items()):
            if deadline <= now:
                del entries[key]

    def issue_ticket(self) -> str:
        with self._lock:
            self._prune(self._tickets, time.monotonic())
            if len(self._tickets) >= _MAX_TICKETS:
                raise RuntimeError("交接请求过多，请稍后重试")
            ticket = secrets.token_urlsafe(32)
            self._tickets[ticket] = time.monotonic() + _TICKET_SECONDS
            return ticket

    def exchange(self, ticket: str, origin: Optional[str] = None) -> str:
        with self._lock:
            self._prune(self._tickets, time.monotonic())
            if ticket not in self._tickets:
                raise HTTPException(401, "登录链接已失效，请使用 WebUI 密码登录")
            session = self.create_session(origin)
            del self._tickets[ticket]
            return session

    def create_session(self, origin: Optional[str] = None) -> str:
        with self._lock:
            now = time.time()
            self._sessions = {key: record for key, record in self._sessions.items() if record.expires_at > now}
            if len(self._sessions) >= _MAX_SESSIONS:
                raise HTTPException(429, "登录会话过多，请稍后再试")
            session = secrets.token_urlsafe(32)
            digest = self._digest(session)
            expires_at = now + _SESSION_SECONDS
            with closing(sqlite3.connect(self._path)) as db, db:
                db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
                db.execute("INSERT INTO sessions (digest, expires_at, origin) VALUES (?, ?, ?)", (digest, expires_at, origin))
            # 提交成功后才发布内存状态；写失败不能签发无法恢复的 Cookie。
            self._sessions[digest] = _Session(expires_at, origin)
            return session

    def _active_session(self, session: Optional[str]) -> Optional[_Session]:
        record = self._sessions.get(self._digest(session)) if session else None
        return record if record and record.expires_at > time.time() else None

    def login_origin(self, session: Optional[str]) -> Optional[str]:
        record = self._active_session(session)
        return record.origin if record else None

    def set_cookie(self, response: Response, session: str) -> None:
        response.set_cookie(
            self.cookie_name, session, max_age=_SESSION_SECONDS, httponly=True,
            samesite="lax", path="/", secure=self.secure_cookie,
        )

    def clear_all(self) -> None:
        with self._lock:
            with closing(sqlite3.connect(self._path)) as db, db:
                db.execute("DELETE FROM sessions")
            self._sessions.clear()
            self._tickets.clear()

    def valid(self, session: Optional[str]) -> bool:
        # 仅读取已提交的内存快照，不等待持有写锁的数据库操作。
        return self._active_session(session) is not None

    def revoke(self, session: Optional[str]) -> None:
        if session is None:
            return
        with self._lock:
            digest = self._digest(session)
            with closing(sqlite3.connect(self._path)) as db, db:
                db.execute("DELETE FROM sessions WHERE digest = ?", (digest,))
            self._sessions.pop(digest, None)
