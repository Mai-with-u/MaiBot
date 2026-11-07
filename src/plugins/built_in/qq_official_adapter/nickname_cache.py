"""官方单聊事件通常不带昵称：缓存群消息里出现过的 QQ 昵称，供单聊按 OpenID 补全。

QQ 开放平台的 ``C2C_MESSAGE_CREATE`` 事件里 ``author.username`` 一般是空串，
适配器只能把 OpenID 当昵称。同一个人在群里发言时，``author.username`` 是真实的
QQ 昵称，而 OpenID 在同一应用内跨场景一致，因此可以按「应用 + OpenID」把群消息里
学到的昵称缓存下来，单聊缺昵称时回填。

缓存只保存全局 QQ 昵称，不保存群名片，避免把某个群里的备注带到别的会话；文件写入
采用「临时文件 + 替换」，进程崩溃不会留下半个 JSON。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

CACHE_VERSION = 1
DEFAULT_TTL_DAYS = 30
DEFAULT_MAX_ENTRIES = 5000
MAX_NICKNAME_LENGTH = 64
# 过期清理是 O(n) 的，按小时执行即可；单条查询另有 O(1) 的到期判断。
PRUNE_INTERVAL_SEC = 3600.0
# 昵称没变时只在时间戳超过一天未落盘时才回写，避免每条群消息都写文件。
PERSIST_REFRESH_SEC = 86400.0

# 昵称里混入的零宽/双向控制字符（从群名片复制时尤其常见），保留会让日志与上下文出现“看不见的字符”。
_INVISIBLE_CHARS = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_WHITESPACE = re.compile(r"\s+")
_OPENID_LIKE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def sanitize_nickname(value: Any) -> str:
    """去掉零宽字符与多余空白并截断；非字符串或清洗后为空时返回空串。"""
    if not isinstance(value, str):
        return ""
    text = _INVISIBLE_CHARS.sub("", value)
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:MAX_NICKNAME_LENGTH]


def is_placeholder_nickname(nickname: Any, *identifiers: Any) -> bool:
    """判断昵称是否只是某个 ID 的占位。

    解码器在官方事件缺昵称时会直接把 OpenID 当昵称；配置了统一 ID 映射时，还会退化成
    映射后的 QQ 号。这类值既不该写进缓存，也不该被当作“已有昵称”。``identifiers``
    传入该用户可能被当成昵称的所有 ID（OpenID、映射后的 user_id）。
    """
    name = sanitize_nickname(nickname)
    if not name:
        return True
    for identifier in identifiers:
        normalized = str(identifier or "").strip()
        if normalized and name == normalized:
            return True
    # 官方 OpenID 是 32 位十六进制；昵称恰好全是十六进制的情况极少，按占位处理更安全。
    return bool(_OPENID_LIKE.fullmatch(name))


def cache_key(app_id: str, openid: str) -> str:
    """缓存键：OpenID 按应用隔离，换机器人后同一个人的 OpenID 会变。"""
    return f"{str(app_id or '').strip()}:{str(openid or '').strip()}"


class NicknameCache:
    """按「应用 + OpenID」缓存 QQ 昵称，落盘为单个 JSON 文件。"""

    def __init__(
        self,
        path: Path,
        *,
        ttl_days: int = DEFAULT_TTL_DAYS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path)
        self._ttl_seconds = max(1, int(ttl_days)) * 86400
        self._max_entries = max(1, int(max_entries))
        self._clock = clock
        self._lock = threading.RLock()
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._last_prune = 0.0

    # -- 读写 ---------------------------------------------------------------

    def load(self) -> int:
        """读取缓存文件，返回有效条目数；文件缺失或损坏时按空缓存处理。"""
        with self._lock:
            self._entries = {}
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return 0
            except (OSError, ValueError):
                # 缓存损坏不该阻断适配器启动，下一次写入会覆盖它。
                return 0
            entries = raw.get("entries") if isinstance(raw, Mapping) else None
            if isinstance(entries, Mapping):
                for key, row in entries.items():
                    entry = self._normalize_entry(row)
                    if entry is not None:
                        self._entries[str(key)] = entry
            self.prune()
            return len(self._entries)

    def save(self) -> None:
        """原子写入缓存文件；目录不存在时自动创建。"""
        with self._lock:
            payload = {
                "version": CACHE_VERSION,
                "entries": {key: dict(row) for key, row in self._entries.items()},
            }
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            temp = self._path.with_name(self._path.name + ".tmp")
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(text, encoding="utf-8")
            os.replace(temp, self._path)

    def remember(
        self,
        app_id: str,
        openid: str,
        nickname: Any,
        *,
        group_openid: str = "",
        now: Optional[float] = None,
    ) -> bool:
        """记录群消息里的昵称。

        返回 ``True`` 表示需要落盘：昵称新增/改写，或同一昵称已超过一天没有持久化。
        其余情况只刷新内存里的时间戳，避免每条群消息都写文件。
        """
        name = sanitize_nickname(nickname)
        normalized_openid = str(openid or "").strip()
        if not name or not normalized_openid:
            return False
        if is_placeholder_nickname(name, normalized_openid):
            return False
        moment = self._clock() if now is None else float(now)
        key = cache_key(app_id, normalized_openid)
        with self._lock:
            self._maybe_prune(moment)
            entry = self._entries.get(key)
            if entry is not None and entry["nickname"] == name:
                refresh_required = moment - float(entry["updated_at"]) >= PERSIST_REFRESH_SEC
                entry["updated_at"] = moment
                entry["hits"] = int(entry.get("hits") or 0) + 1
                return refresh_required
            self._entries[key] = {
                "nickname": name,
                "updated_at": moment,
                "source_group": str(group_openid or "")[:64],
                "hits": 1,
            }
            if len(self._entries) > self._max_entries:
                self.prune(moment)
            return True

    def lookup(self, app_id: str, openid: str, *, now: Optional[float] = None) -> str:
        """取缓存的昵称；未命中或已过期时返回空串。"""
        normalized_openid = str(openid or "").strip()
        if not normalized_openid:
            return ""
        moment = self._clock() if now is None else float(now)
        key = cache_key(app_id, normalized_openid)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return ""
            # 单条查询直接判到期，不依赖周期清理，避免“已过期但仍命中”。
            if moment - float(entry["updated_at"]) > self._ttl_seconds:
                del self._entries[key]
                return ""
            entry["hits"] = int(entry.get("hits") or 0) + 1
            return str(entry["nickname"])

    # -- 维护 ---------------------------------------------------------------

    def _maybe_prune(self, moment: float) -> None:
        if moment - self._last_prune < PRUNE_INTERVAL_SEC:
            return
        self._last_prune = moment
        self.prune(moment)

    def prune(self, now: Optional[float] = None) -> int:
        """删除过期条目，并在超出上限时淘汰最久未更新的记录。"""
        moment = self._clock() if now is None else float(now)
        with self._lock:
            expired = [
                key for key, entry in self._entries.items() if moment - float(entry["updated_at"]) > self._ttl_seconds
            ]
            for key in expired:
                del self._entries[key]
            overflow = len(self._entries) - self._max_entries
            if overflow > 0:
                for key, _ in sorted(self._entries.items(), key=lambda item: float(item[1]["updated_at"]))[:overflow]:
                    del self._entries[key]
            return len(expired) + max(overflow, 0)

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """返回当前条目副本，供测试与诊断使用。"""
        with self._lock:
            return {key: dict(row) for key, row in self._entries.items()}

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @staticmethod
    def _normalize_entry(row: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(row, Mapping):
            return None
        nickname = sanitize_nickname(row.get("nickname"))
        if not nickname:
            return None
        try:
            updated_at = float(row.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            updated_at = 0.0
        return {
            "nickname": nickname,
            "updated_at": updated_at,
            "source_group": str(row.get("source_group") or "")[:64],
            "hits": int(row.get("hits") or 0),
        }
