"""宿主与 Worker 之间的运行契约；不导入配置、日志或数据库。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional
from uuid import uuid4

import json
import os
import re
import threading
import time


RUNTIME_CONTEXT_ENV = "MAIBOT_RUNTIME_CONTEXT"
# Worker 以此退出码请求宿主重新拉起自己。
RESTART_EXIT_CODE = 42
# Worker 自身的关闭预算；宿主在此基础上留出余量后才强制回收进程树。
WORKER_SHUTDOWN_TIMEOUT = 60.0
_BOT_ID = re.compile(r"[0-9a-f]{32}")

Phase = Literal["starting", "migrating", "ready", "stopping"]


@dataclass(frozen=True)
class RuntimeContext:
    """宿主为一次 Worker 运行下发的全部参数。"""

    bot_id: str
    http_endpoint: str
    public_base_url: str
    status_path: Path

    def to_env(self) -> str:
        return json.dumps({
            "bot_id": self.bot_id,
            "http_endpoint": self.http_endpoint,
            "public_base_url": self.public_base_url,
            "status_path": str(self.status_path),
        })

    @classmethod
    def from_env(cls, value: str) -> "RuntimeContext":
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError("运行契约必须是 JSON 对象")
        fields: Dict[str, Any] = {name: data.get(name) for name in ("bot_id", "http_endpoint", "public_base_url", "status_path")}
        for name, item in fields.items():
            if not isinstance(item, str) or not item:
                raise ValueError(f"运行契约缺少 {name}")
        if _BOT_ID.fullmatch(fields["bot_id"]) is None:
            raise ValueError("运行契约的 bot_id 无效")
        if not fields["public_base_url"].startswith(("http://", "https://")):
            raise ValueError("运行契约的 public_base_url 必须是 HTTP(S) 地址")
        return cls(
            bot_id=fields["bot_id"],
            http_endpoint=fields["http_endpoint"],
            public_base_url=fields["public_base_url"].rstrip("/"),
            status_path=Path(fields["status_path"]),
        )


_context: Optional[RuntimeContext] = None
_status_lock = threading.Lock()


def runtime_context() -> RuntimeContext:
    """Worker 只能由宿主启动；缺少契约时立即失败，而不是猜测运行参数。"""
    global _context
    if _context is None:
        value = os.environ.get(RUNTIME_CONTEXT_ENV)
        if value is None:
            raise RuntimeError("MaiBot 只能由宿主启动：请执行 maibot run 或 python bot.py")
        _context = RuntimeContext.from_env(value)
    return _context


def has_runtime_context() -> bool:
    return RUNTIME_CONTEXT_ENV in os.environ


def report_phase(phase: Phase, detail: str = "") -> None:
    """原子写入当前阶段供宿主读取；不在宿主下运行的工具与测试不上报。"""
    if not has_runtime_context():
        return
    path = runtime_context().status_path
    payload = json.dumps({"phase": phase, "detail": detail, "pid": os.getpid(), "at": time.time()}, ensure_ascii=False)
    with _status_lock:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _read_shared(path: Path) -> bytes:
    """Windows 上以共享删除方式打开，读取期间 Worker 的 os.replace 不会失败。"""
    if os.name != "nt":
        return path.read_bytes()
    from ctypes import wintypes

    import ctypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    generic_read, share_all, open_existing = 0x80000000, 0x7, 3
    handle = kernel.CreateFileW(str(path), generic_read, share_all, None, open_existing, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        error = ctypes.get_last_error()
        if error in {2, 3}:
            raise FileNotFoundError(str(path))
        raise ctypes.WinError(error)
    with open(msvcrt.open_osfhandle(handle, os.O_RDONLY), "rb") as stream:
        return stream.read()


def read_phase(path: Path) -> Optional[Dict[str, Any]]:
    """宿主读取 Worker 阶段；文件尚未写入时返回 None。"""
    try:
        data = json.loads(_read_shared(path).decode("utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or data.get("phase") not in {"starting", "migrating", "ready", "stopping"}:
        raise ValueError(f"Worker 状态文件无效: {path}")
    return data
