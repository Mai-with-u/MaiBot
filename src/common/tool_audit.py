from __future__ import annotations

import hashlib
import itertools
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from src.common.paths import get_instance_root


def sha256_canonical(value: Any) -> str:
    """对可 JSON 序列化的值做规范化哈希（键排序、紧凑分隔符、UTF-8）。"""

    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    """返回带本地时区偏移的 ISO 时间戳，精确到毫秒。"""

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


class ToolAuditWriter:
    """append-only 工具审计；逐行 JSON + 进程内 seq + sha256 链。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self._last_hash_value = self._read_last_hash()

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        *,
        action: str,
        tool: str,
        provider_type: str,
        platform: str,
        session_id: str,
        user_id: str,
        call_id: str = "",
        args: Dict[str, Any],
        reason: str = "",
        decided_by: str = "",
    ) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "seq": next(self._seq),
                "ts": _now_iso(),
                "call_id": call_id or f"gen-{uuid4().hex}",
                "action": action,
                "tool": tool,
                "provider_type": provider_type,
                "platform": platform,
                "session_id": session_id,
                "user_id": user_id,
                "args_hash": sha256_canonical(args),
                "reason": reason,
                "decided_by": decided_by,
                "prev_hash": self._last_hash_value,
            }
            entry["entry_hash"] = sha256_canonical(entry)
            self._last_hash_value = entry["entry_hash"]
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _read_last_hash(self) -> str:
        """读取末行的 entry_hash；空文件或损坏时返回空串。"""

        if not self._path.exists():
            return ""
        last_line = ""
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last_line = line
        if not last_line:
            return ""
        try:
            return str(json.loads(last_line).get("entry_hash", ""))
        except json.JSONDecodeError:
            return ""


_audit_writer: ToolAuditWriter | None = None


def configure_tool_audit(path: str) -> ToolAuditWriter:
    """按配置初始化全局审计写入器；相对路径以实例根目录为基准。"""

    global _audit_writer
    target = Path(path)
    if not target.is_absolute():
        target = get_instance_root() / target
    if _audit_writer is None or _audit_writer.path != target:
        _audit_writer = ToolAuditWriter(target)
    return _audit_writer


def get_tool_audit_writer() -> ToolAuditWriter | None:
    """返回已配置的审计写入器；未开启审计时返回 None。"""

    return _audit_writer
