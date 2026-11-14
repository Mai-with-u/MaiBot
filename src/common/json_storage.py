"""运行状态的 JSON 原子提交与 Windows 文件共享语义。"""

from pathlib import Path
from typing import Any, Dict

import io
import json
import os

from src.common.atomic_file import write_private


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    write_private(path, json.dumps(payload, ensure_ascii=False))


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


