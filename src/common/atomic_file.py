"""同目录写入并刷盘后，原子替换私有文件。"""

from pathlib import Path
from uuid import uuid4

import os
import time


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def sync_directory(path: Path) -> None:
    """在 POSIX 提交目录项，保证文件替换与事务日志的落盘顺序。"""
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def replace_file(source: Path, target: Path) -> None:
    for attempt in range(8):
        try:
            _replace_once(source, target)
            return
        except OSError as exc:
            # Windows 扫描器和普通读句柄可能短暂阻止替换，只重试这一次文件提交。
            if os.name != "nt" or exc.winerror not in {5, 32, 33, 1175} or attempt == 7:
                exc.add_note(f"原子替换文件失败: {source} -> {target}; 尝试次数={attempt + 1}")
                raise
            time.sleep(min(0.01 * 2 ** attempt, 0.1))


def _replace_once(source: Path, target: Path) -> None:
    if os.name != "nt" or not target.exists():
        source.replace(target)
        return
    from ctypes import wintypes

    import ctypes

    # MoveFileEx 即使遇到共享删除的读句柄也会拒绝覆盖；ReplaceFile 支持此场景。
    replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID,
    ]
    replace.restype = wintypes.BOOL
    if not replace(str(target), str(source), None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def quarantine_file(path: Path) -> Path:
    """保留损坏文件，并将正式路径释放给下一次写入。"""
    backup = path.with_name(f"{path.name}.corrupt-{uuid4().hex}")
    path.rename(backup)
    sync_directory(path.parent)
    return backup
