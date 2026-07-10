from typing import Optional, Type

import sqlite3

from .sqlite_connection import ManagedSQLiteConnection


class ConnectionTransaction:
    """为外部注入的 SQLite 连接提供统一事务语义。"""

    def __init__(self, connection: sqlite3.Connection, *, immediate: bool = False) -> None:
        if not isinstance(connection, ManagedSQLiteConnection):
            raise TypeError(
                "外部 SQLite 连接不支持受管事务；请注入 ManagedSQLiteConnection"
            )
        self.connection = connection
        self.immediate = immediate
        self._savepoint_name: Optional[str] = None

    def __enter__(self) -> sqlite3.Connection:
        if self.connection.in_transaction:
            self._savepoint_name = f"a_memorix_override_{id(self)}"
            self.connection.execute(f"SAVEPOINT {self._savepoint_name}")
        else:
            self.connection.execute("BEGIN IMMEDIATE" if self.immediate else "BEGIN")
        return self.connection

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: object,
    ) -> bool:
        if self._savepoint_name is not None:
            if exc_type is None:
                self.connection.execute(f"RELEASE SAVEPOINT {self._savepoint_name}")
            else:
                self.connection.execute(f"ROLLBACK TO SAVEPOINT {self._savepoint_name}")
                self.connection.execute(f"RELEASE SAVEPOINT {self._savepoint_name}")
        elif exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        return False
