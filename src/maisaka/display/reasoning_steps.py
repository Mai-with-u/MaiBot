"""推理轮次关联：上下文随异步任务/线程传播，磁盘索引由预览写线程维护。"""

from contextlib import closing
from contextvars import ContextVar
from pathlib import Path
from typing import List, Optional, Tuple
import sqlite3

reasoning_trace_id: ContextVar[Optional[str]] = ContextVar("reasoning_trace_id", default=None)
INDEX_NAME = ".reasoning-steps.sqlite3"


def register_reasoning_step(root: Path, path: Path, trace_id: Optional[str]) -> None:
    if not trace_id:
        return
    stage, session, filename = path.relative_to(root).parts
    with closing(sqlite3.connect(root / INDEX_NAME)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS steps ("
            "stage TEXT, session TEXT, stem TEXT, trace_id TEXT NOT NULL, PRIMARY KEY(stage, session, stem))"
        )
        connection.execute("CREATE INDEX IF NOT EXISTS steps_trace ON steps(trace_id, session)")
        connection.execute(
            "INSERT OR REPLACE INTO steps VALUES (?, ?, ?, ?)",
            (stage, session, Path(filename).stem, trace_id),
        )


def remove_reasoning_step(root: Path, path: Path) -> None:
    index = root / INDEX_NAME
    if not index.is_file() or path.suffix != ".json":
        return
    parts = path.relative_to(root).parts
    if len(parts) != 3:
        return
    with closing(sqlite3.connect(index)) as connection, connection:
        connection.execute("DELETE FROM steps WHERE stage=? AND session=? AND stem=?",
                           (parts[0], parts[1], path.stem))


def find_reasoning_steps(root: Path, trace_id: str, session: str) -> List[Tuple[str, str, str]]:
    index = root / INDEX_NAME
    if not index.is_file():
        return []
    with closing(sqlite3.connect(f"{index.as_uri()}?mode=ro", uri=True)) as connection:
        return connection.execute(
            "SELECT stage, session, stem FROM steps WHERE trace_id=? AND session=? ORDER BY stem, stage",
            (trace_id, session),
        ).fetchall()
