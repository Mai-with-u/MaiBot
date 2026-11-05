"""进程存活期间持有的文件租约；不依据历史 PID 判断或结束进程。"""

from pathlib import Path
from typing import BinaryIO, Optional

import os


class ProcessLease:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: Optional[BinaryIO] = None

    def __enter__(self) -> "ProcessLease":
        handle = self.path.open("a+b")
        try:
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError(f"无法取得运行租约，可能已有进程运行: {self.path}: {exc}") from exc
        self._file = handle
        return self

    def __exit__(self, *_args: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def instance_lock_path(root: Path) -> Path:
    """Bot 实例锁；Worker 存活期间持有，adopt 迁移前也用它确认实例已停止。"""
    directory = root / "temp"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "instance.lock"
