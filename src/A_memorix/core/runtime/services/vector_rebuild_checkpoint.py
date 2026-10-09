from contextlib import closing
from pathlib import Path
from typing import Dict, Sequence, Tuple

import hashlib
import sqlite3

import numpy as np


class EmbeddingConfigurationChanged(RuntimeError):
    """构建期间嵌入配置变化，当前成果不得用于新模型。"""


class VectorRebuildCheckpoint:
    """逐批提交成功向量；模型身份和文本内容共同决定是否可恢复。

    使用独立 SQLite 事务，避免每批重新保存整个索引。调用方须在线程池执行。
    """

    def __init__(self, path: Path, fingerprint: str, dimension: int) -> None:
        self.path = path
        self.fingerprint = fingerprint
        self.dimension = dimension
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS vectors ("
                "model TEXT, pool TEXT, id TEXT, input TEXT, vector BLOB, "
                "PRIMARY KEY (model, pool, id))"
            )

    @staticmethod
    def input_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def read(self, pool: str, items: Sequence[Tuple[str, str]]) -> Dict[str, np.ndarray]:
        vectors = {}
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for item_id, text in items:
                row = connection.execute(
                    "SELECT vector FROM vectors WHERE model=? AND pool=? AND id=? AND input=?",
                    (self.fingerprint, pool, item_id, self.input_hash(text)),
                ).fetchone()
                if row is not None:
                    vector = np.frombuffer(row[0], dtype=np.float32).copy()
                    if vector.size != self.dimension:
                        raise ValueError(f"重建检查点向量维度异常: {item_id}")
                    vectors[item_id] = vector
        return vectors

    def write(self, pool: str, items: Sequence[Tuple[str, str]], vectors: np.ndarray) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executemany(
                "INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?, ?)",
                [
                    (self.fingerprint, pool, item_id, self.input_hash(text), vector.tobytes())
                    for (item_id, text), vector in zip(items, vectors, strict=True)
                ],
            )

    def discard(self) -> None:
        # 只删除本模型的成果，避免模型切换误用或误删其他模型的检查点。
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DELETE FROM vectors WHERE model=?", (self.fingerprint,))
