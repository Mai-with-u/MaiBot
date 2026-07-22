"""A_Memorix 数据目录的跨进程独占写者锁。"""

from pathlib import Path
from threading import RLock
from typing import BinaryIO, Optional

import json
import os


class RuntimeWriterLock:
    """用操作系统文件锁保证一个数据目录同一时刻只有一个活动 SDK 写者。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle: Optional[BinaryIO] = None
        self._state_lock = RLock()

    @property
    def held(self) -> bool:
        with self._state_lock:
            return self._handle is not None

    def acquire(self) -> None:
        with self._state_lock:
            if self._handle is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch(exist_ok=True)
            handle = self.path.open("r+b", buffering=0)
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b" ")
            handle.seek(0)
            try:
                self._lock_handle(handle)
            except OSError as exc:
                owner = self._read_owner(handle)
                handle.close()
                detail = f"，当前持有者={owner}" if owner else ""
                raise RuntimeError(
                    f"A_Memorix 数据目录已有活动写者: {self.path}{detail}"
                ) from exc

            try:
                owner_payload = json.dumps(
                    {"pid": os.getpid(), "lock_path": str(self.path)},
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
                handle.seek(0)
                handle.write(owner_payload)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                try:
                    self._unlock_handle(handle)
                finally:
                    handle.close()
                raise
            self._handle = handle

    def release(self) -> None:
        with self._state_lock:
            handle = self._handle
            if handle is None:
                return
            self._handle = None
            try:
                self._unlock_handle(handle)
            finally:
                handle.close()

    @staticmethod
    def _read_owner(handle: BinaryIO) -> str:
        try:
            handle.seek(0)
            return handle.read(500).decode("utf-8", errors="replace").strip()
        except OSError:
            return ""

    @staticmethod
    def _lock_handle(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_handle(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
