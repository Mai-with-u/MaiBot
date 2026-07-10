from contextlib import contextmanager
from pathlib import Path
from threading import RLock, get_ident
from typing import Dict, Iterator, Optional, Set

import sqlite3


class ManagedSQLiteConnection(sqlite3.Connection):
    """在显式事务中延迟业务方法自行触发的提交和回滚。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._managed_transaction_depth = 0
        self._managed_rollback_requests: Set[int] = set()
        self._savepoint_counter = 0

    def commit(self) -> None:
        if self._managed_transaction_depth > 0:
            return
        super().commit()

    def rollback(self) -> None:
        if self._managed_transaction_depth > 0:
            self._managed_rollback_requests.add(self._managed_transaction_depth)
            return
        super().rollback()

    def next_savepoint_name(self) -> str:
        self._savepoint_counter += 1
        return f"a_memorix_tx_{self._savepoint_counter}"

    def begin_managed_scope(self) -> int:
        self._managed_transaction_depth += 1
        return self._managed_transaction_depth

    def end_managed_scope(self, scope_depth: int) -> bool:
        rollback_requested = scope_depth in self._managed_rollback_requests
        self._managed_rollback_requests.discard(scope_depth)
        self._managed_transaction_depth -= 1
        return rollback_requested

    def force_commit(self) -> None:
        super().commit()

    def force_rollback(self) -> None:
        super().rollback()


class SQLiteConnectionManager:
    """按线程管理 SQLite 连接，并统一事务提交与回滚边界。"""

    def __init__(self, db_path: Path, *, timeout: float = 30.0) -> None:
        self.db_path = db_path
        self.timeout = timeout
        self._connections: Dict[int, ManagedSQLiteConnection] = {}
        self._lock = RLock()

    def connection(self) -> ManagedSQLiteConnection:
        thread_id = get_ident()
        with self._lock:
            connection = self._connections.get(thread_id)
            if connection is None:
                connection = self._create_connection()
                self._connections[thread_id] = connection
            return connection

    def _create_connection(self) -> ManagedSQLiteConnection:
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=self.timeout,
            check_same_thread=False,
            factory=ManagedSQLiteConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA cache_size=-64000")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connection()
        savepoint_name: Optional[str] = None
        if connection.in_transaction:
            savepoint_name = connection.next_savepoint_name()
            connection.execute(f"SAVEPOINT {savepoint_name}")
        else:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        scope_depth = connection.begin_managed_scope()
        try:
            yield connection
        except Exception:
            self._rollback_scope(connection, savepoint_name, scope_depth)
            raise
        else:
            rollback_requested = connection.end_managed_scope(scope_depth)
            if rollback_requested:
                self._rollback_scope_without_depth_change(connection, savepoint_name)
                raise RuntimeError("事务中的操作请求了回滚")
            if savepoint_name is not None:
                connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")
            else:
                connection.force_commit()

    @staticmethod
    def _rollback_scope(
        connection: ManagedSQLiteConnection,
        savepoint_name: Optional[str],
        scope_depth: int,
    ) -> None:
        connection.end_managed_scope(scope_depth)
        SQLiteConnectionManager._rollback_scope_without_depth_change(connection, savepoint_name)

    @staticmethod
    def _rollback_scope_without_depth_change(
        connection: ManagedSQLiteConnection,
        savepoint_name: Optional[str],
    ) -> None:
        if savepoint_name is not None:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        else:
            connection.force_rollback()

    def close_current(self) -> None:
        thread_id = get_ident()
        with self._lock:
            connection = self._connections.pop(thread_id, None)
        if connection is not None:
            connection.close()

    def close_all(self) -> None:
        with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
        for connection in connections:
            connection.close()

    @property
    def current(self) -> Optional[sqlite3.Connection]:
        with self._lock:
            return self._connections.get(get_ident())
