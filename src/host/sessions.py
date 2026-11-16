"""持久登录会话；单次交接票据仍仅在本次宿主运行中有效。"""

from contextlib import closing
from hashlib import sha256
from pathlib import Path
from typing import Dict, NamedTuple, Optional
from uuid import uuid4

from fastapi import HTTPException, Response

import hmac
import secrets
import sqlite3
import threading
import time

from src.common.logger import get_logger

from .authentication import HostCredentials


logger = get_logger("host.sessions")
_COOKIE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_TICKET_SECONDS = 60
_MAX_TICKETS = 64


class _Session(NamedTuple):
    origin: Optional[str]


class _InvalidSessionSchema(ValueError):
    """登录表缺少验证已有会话所需的字段。"""


class HostSessions:
    """数据库只保存票号摘要与登录来源，会话持续有效直到撤销，认证走内存，不阻塞事件循环。

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
        try:
            self._sessions = self._load_sessions()
        except (_InvalidSessionSchema, sqlite3.DatabaseError) as exc:
            if isinstance(exc, sqlite3.DatabaseError) and exc.sqlite_errorcode & 0xff not in {
                sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB,
            }:
                raise
            backup = self._path.with_name(f"{self._path.name}.corrupt-{uuid4().hex}")
            # 宿主持有租约，先移走伴随文件，避免它们被新库接管。
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = self._path.with_name(self._path.name + suffix)
                if sidecar.exists():
                    sidecar.rename(backup.with_name(backup.name + suffix))
            self._path.rename(backup)
            logger.warning(f"登录会话库损坏，已保留原库并重建会话：{backup}", exc_info=True)
            self._sessions = self._load_sessions()

    def _load_sessions(self) -> Dict[str, _Session]:
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, origin TEXT)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
            missing = {"digest"} - columns
            if missing:
                raise _InvalidSessionSchema(f"登录会话表缺少必需列：{', '.join(sorted(missing))}")
            if "origin" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN origin TEXT")
            if "expires_at" in columns:
                # 升级时仅保留尚未到期的旧会话，之后不再按时间失效。
                db.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
                db.execute("ALTER TABLE sessions DROP COLUMN expires_at")
            # 临时密码每次启动都会轮换，不恢复上一进程的登录。
            if self._credentials.token_source == "temporary":
                db.execute("DELETE FROM sessions")
            return {
                digest: _Session(origin)
                for digest, origin in db.execute("SELECT digest, origin FROM sessions")
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
            session = secrets.token_urlsafe(32)
            digest = self._digest(session)
            with closing(sqlite3.connect(self._path)) as db, db:
                db.execute("INSERT INTO sessions (digest, origin) VALUES (?, ?)", (digest, origin))
            # 提交成功后才发布内存状态；写失败不能签发无法恢复的 Cookie。
            self._sessions[digest] = _Session(origin)
            return session

    def _active_session(self, session: Optional[str]) -> Optional[_Session]:
        return self._sessions.get(self._digest(session)) if session else None

    def login_origin(self, session: Optional[str]) -> Optional[str]:
        record = self._active_session(session)
        return record.origin if record else None

    def set_cookie(self, response: Response, session: str) -> None:
        # 沿用旧版的 7 天持久 Cookie，关闭浏览器不会立即退出。
        response.set_cookie(
            self.cookie_name, session, max_age=_COOKIE_MAX_AGE_SECONDS, httponly=True,
            samesite="lax", path="/", secure=self.secure_cookie,
        )

    def clear_all(self) -> None:
        with self._lock:
            with closing(sqlite3.connect(self._path)) as db, db:
                db.execute("DELETE FROM sessions")
            self._sessions.clear()
            self._tickets.clear()

    def update_password(self, session: Optional[str], new_token: str) -> Optional[str]:
        with self._lock:
            record = self._active_session(session)
            if record is None:
                raise HTTPException(401, "登录已失效，请重新登录")
            setting_initial_password = self._credentials.token_source == "temporary"
            self._credentials.update(new_token)
            self.clear_all()
            if setting_initial_password:
                return self.create_session(record.origin)
            return None

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
