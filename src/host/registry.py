"""宿主的 Bot 登记表；不依赖单 Bot 的配置、日志或数据库模块。"""

from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Literal, Optional
from uuid import uuid4

import os
import re
import sqlite3


SCHEMA_VERSION = 1
BOT_ID_PATTERN = re.compile(r"[0-9a-f]{32}")

Initialization = Literal["initializing", "initialized", "failed"]
DesiredState = Literal["running", "stopped"]
RestartPolicy = Literal["on-failure", "never"]

_SCHEMA = """
CREATE TABLE bots (
    bot_id TEXT PRIMARY KEY CHECK (length(bot_id) = 32),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    directory TEXT NOT NULL UNIQUE,
    owned INTEGER NOT NULL CHECK (owned IN (0, 1)),
    created_at TEXT NOT NULL,
    initialization TEXT NOT NULL CHECK (initialization IN ('initializing', 'initialized', 'failed')),
    error TEXT,
    desired_state TEXT NOT NULL CHECK (desired_state IN ('running', 'stopped')),
    restart_policy TEXT NOT NULL CHECK (restart_policy IN ('on-failure', 'never'))
)
"""


@dataclass(frozen=True)
class BotRecord:
    """一个登记的 Bot。owned 为假时目录不归宿主所有：宿主只启动它，永不删除或移动。"""

    bot_id: str
    name: str
    directory: Path
    owned: bool
    created_at: str
    initialization: Initialization
    error: Optional[str]
    desired_state: DesiredState
    restart_policy: RestartPolicy


def validate_name(name: str) -> str:
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise ValueError("Bot 名称应为 1 到 100 个字符")
    return name


def same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(str(first)) == os.path.normcase(str(second))


def path_contains(parent: Path, child: Path) -> bool:
    parent_text, child_text = os.path.normcase(str(parent)), os.path.normcase(str(child))
    return child_text == parent_text or child_text.startswith(parent_text.rstrip(os.sep) + os.sep)


class Registry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "host" / "registry.sqlite3"

    def exists(self) -> bool:
        return self.path.is_file()

    def create(self) -> None:
        """只能创建一次；已存在的注册表（包括空文件或损坏文件）不会被重建。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("xb"):
            pass
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(_SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _connection(self, *, writable: bool = False) -> Iterator[sqlite3.Connection]:
        if not self.exists():
            raise FileNotFoundError(f"宿主工作区未初始化: {self.root}")
        connection = sqlite3.connect(f"{self.path.as_uri()}?mode={'rw' if writable else 'ro'}", uri=True)
        with closing(connection), connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise ValueError(f"不支持的宿主注册表版本: {version}，当前支持 {SCHEMA_VERSION}")
            connection.row_factory = sqlite3.Row
            if writable:
                connection.execute("BEGIN IMMEDIATE")
            yield connection

    def _stored_directory(self, directory: Path) -> str:
        # 工作区内的目录按相对路径保存，整个工作区搬家后登记仍然有效。
        if path_contains(self.root, directory):
            return directory.relative_to(self.root).as_posix() or "."
        return str(directory)

    def _record(self, row: sqlite3.Row) -> BotRecord:
        return BotRecord(
            bot_id=row["bot_id"],
            name=row["name"],
            directory=(self.root / row["directory"]).resolve(),
            owned=bool(row["owned"]),
            created_at=row["created_at"],
            initialization=row["initialization"],
            error=row["error"],
            desired_state=row["desired_state"],
            restart_policy=row["restart_policy"],
        )

    def list_bots(self) -> List[BotRecord]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM bots ORDER BY created_at, bot_id").fetchall()
        return [self._record(row) for row in rows]

    def get_bot(self, bot_id: str) -> BotRecord:
        if BOT_ID_PATTERN.fullmatch(bot_id) is None:
            raise ValueError(f"无效的 Bot ID: {bot_id}")
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM bots WHERE bot_id = ?", (bot_id,)).fetchone()
        if row is None:
            raise KeyError(f"未找到 Bot: {bot_id}")
        return self._record(row)

    def add_bot(
        self, name: str, directory: Path, *, owned: bool, initialization: Initialization,
        bot_id: Optional[str] = None,
    ) -> BotRecord:
        """登记新目录；目录不得与已登记目录相同或互相包含（工作区根目录下的 bots/ 除外）。"""
        name = validate_name(name)
        bot_id = bot_id or uuid4().hex
        directory = directory.resolve()
        bots_root = self.root / "bots"
        for existing in self.list_bots():
            if path_contains(existing.directory, directory) and not path_contains(bots_root, directory):
                raise ValueError(f"目录已属于 Bot {existing.name}: {existing.directory}")
            if path_contains(directory, existing.directory):
                raise ValueError(f"目录包含已登记的 Bot {existing.name}: {existing.directory}")
        with self._connection(writable=True) as connection:
            connection.execute(
                "INSERT INTO bots (bot_id, name, directory, owned, created_at, initialization, desired_state, restart_policy) "
                "VALUES (?, ?, ?, ?, ?, ?, 'running', 'on-failure')",
                (bot_id, name, self._stored_directory(directory), int(owned), datetime.now(timezone.utc).isoformat(), initialization),
            )
        return self.get_bot(bot_id)

    def _update(self, bot_id: str, assignments: str, values: tuple) -> BotRecord:
        with self._connection(writable=True) as connection:
            cursor = connection.execute(f"UPDATE bots SET {assignments} WHERE bot_id = ?", (*values, bot_id))
            if cursor.rowcount != 1:
                raise KeyError(f"未找到 Bot: {bot_id}")
        return self.get_bot(bot_id)

    def set_initialization(self, bot_id: str, state: Initialization, error: Optional[str] = None) -> BotRecord:
        return self._update(bot_id, "initialization = ?, error = ?", (state, error))

    def set_desired_state(self, bot_id: str, state: DesiredState) -> BotRecord:
        return self._update(bot_id, "desired_state = ?", (state,))

    def rename_bot(self, bot_id: str, name: str) -> BotRecord:
        return self._update(bot_id, "name = ?", (validate_name(name),))

    def set_restart_policy(self, bot_id: str, policy: RestartPolicy) -> BotRecord:
        return self._update(bot_id, "restart_policy = ?", (policy,))

    def set_directory(self, bot_id: str, directory: Path, *, owned: bool) -> BotRecord:
        return self._update(bot_id, "directory = ?, owned = ?", (self._stored_directory(directory.resolve()), int(owned)))

    def remove_bot(self, bot_id: str) -> None:
        with self._connection(writable=True) as connection:
            if connection.execute("DELETE FROM bots WHERE bot_id = ?", (bot_id,)).rowcount != 1:
                raise KeyError(f"未找到 Bot: {bot_id}")
