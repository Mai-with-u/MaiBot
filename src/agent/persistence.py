"""电脑状态的 JSON 原子提交与 Windows 文件共享语义。"""

from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

import io
import json
import os
import time


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    # 各次提交独占临时文件，避免并发写入互相覆盖或持有对方待替换的文件句柄。
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _replace_json(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_json(source: Path, target: Path) -> None:
    # Windows 的索引/扫描等外部读句柄不受本进程锁控制；只等待原子提交，不重新执行业务动作。
    # 最多等待 0.45 秒，持续占用、权限错误及其他 I/O 错误仍完整抛出。
    for attempt in range(8):
        try:
            _replace_json_once(source, target)
            return
        except OSError as exc:
            if os.name != "nt" or exc.winerror not in {5, 32, 33, 1175} or attempt == 7:
                exc.add_note(f"原子提交状态文件失败: {source} -> {target}; 尝试次数={attempt + 1}")
                raise
            time.sleep(min(0.01 * 2 ** attempt, 0.1))


def _replace_json_once(source: Path, target: Path) -> None:
    if os.name != "nt" or not target.exists():
        source.replace(target)
        return
    from ctypes import wintypes
    import ctypes

    # MoveFileEx（os.replace）即使遇到共享删除的读句柄也会拒绝覆盖；ReplaceFile 支持此场景。
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    replace = kernel.ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID]
    replace.restype = wintypes.BOOL
    if not replace(str(target), str(source), None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def _open_json_reader(path: str, flags: int) -> int:
    if os.name != "nt":
        return os.open(path, flags)
    from ctypes import wintypes
    import ctypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    # FILE_SHARE_DELETE 允许读旧快照的同时原子替换文件；普通 Python open 缺少此标志。
    handle = create(path, 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise


def read_json(path: Path) -> Dict[str, Any]:
    with io.open(path, "r", encoding="utf-8-sig", opener=_open_json_reader) as handle:
        return json.load(handle)


