from contextlib import contextmanager
from pathlib import Path
from threading import RLock, Thread, current_thread
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
        self._connections: Dict[Thread, ManagedSQLiteConnection] = {}
        self._lock = RLock()
        self._closed = False

    def connection(self) -> ManagedSQLiteConnection:
        owner_thread = current_thread()
        with self._lock:
            if self._closed:
                raise RuntimeError("SQLite 连接管理器已关闭")
            self._prune_inactive_connections_locked(owner_thread)
            connection = self._connections.get(owner_thread)
            if connection is None:
                connection = self._create_connection()
                self._connections[owner_thread] = connection
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
        except BaseException:
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
        owner_thread = current_thread()
        with self._lock:
            connection = self._connections.pop(owner_thread, None)
        if connection is not None:
            connection.close()

    def close_all(self) -> None:
        """关闭全部连接；调用方应先停止所有使用该管理器的后台任务。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            connections = list(self._connections.values())
            self._connections.clear()
        for connection in connections:
            connection.close()

    def prune_inactive_connections(self) -> int:
        """关闭已结束线程持有的连接，返回本次回收数量。"""
        with self._lock:
            return self._prune_inactive_connections_locked(current_thread())

    def _prune_inactive_connections_locked(self, owner_thread: Thread) -> int:
        inactive_threads = [
            thread for thread in self._connections if thread is not owner_thread and not thread.is_alive()
        ]
        for thread in inactive_threads:
            self._connections.pop(thread).close()
        return len(inactive_threads)

    @property
    def current(self) -> Optional[sqlite3.Connection]:
        with self._lock:
            return self._connections.get(current_thread())

    @property
    def connection_count(self) -> int:
        with self._lock:
            return len(self._connections)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed
