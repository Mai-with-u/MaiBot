"""插件自报的通用运行标记，随运行实例清理，支持到期通知。"""

from typing import Any, Callable, Dict, List, Optional

import asyncio
import math
import time


class RuntimeStatusBadges:
    """每插件最多八个短文本标记；所有插件共用一个到期计时器。"""

    def __init__(self, on_changed: Callable[[], None]) -> None:
        self._on_changed = on_changed
        self._badges: Dict[str, List[Dict[str, Any]]] = {}
        self._timer: Optional[asyncio.TimerHandle] = None

    def update(self, plugin_id: str, badges: Any) -> None:
        if not isinstance(badges, list) or len(badges) > 8:
            raise ValueError("运行标记必须是列表，最多八个")
        normalized: List[Dict[str, Any]] = []
        identifiers = set()
        now = time.time()
        for badge in badges:
            if not isinstance(badge, dict):
                raise ValueError("运行标记必须是对象")
            identifier, label = badge.get("id"), badge.get("label")
            if (not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 64
                    or not isinstance(label, str) or not label.strip() or len(label) > 64):
                raise ValueError("运行标记的 id 和 label 必须是 1 至 64 字符的文本")
            if identifier in identifiers:
                raise ValueError("运行标记 id 不得重复")
            identifiers.add(identifier)
            expiry = badge.get("expires_at")
            if expiry is not None:
                if (isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                        or not math.isfinite(expiry) or expiry > now + 86400):
                    raise ValueError("运行标记 expires_at 必须是未来一天内的 Unix 秒时间戳")
                if expiry <= now:
                    continue
            normalized.append({"id": identifier, "label": label, "expires_at": expiry})
        previous = self._badges.get(plugin_id, [])
        if normalized:
            self._badges[plugin_id] = normalized
        else:
            self._badges.pop(plugin_id, None)
        if previous != normalized:
            self._schedule_expiry()
            self._on_changed()

    def snapshot(self) -> Dict[str, List[Dict[str, Any]]]:
        now = time.time()
        return {plugin_id: [dict(badge) for badge in badges
                            if badge["expires_at"] is None or badge["expires_at"] > now]
                for plugin_id, badges in self._badges.items()}

    def remove(self, plugin_id: str) -> None:
        if self._badges.pop(plugin_id, None) is not None:
            self._schedule_expiry()
            self._on_changed()

    def clear(self) -> None:
        changed = bool(self._badges)
        self._badges.clear()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if changed:
            self._on_changed()

    def _schedule_expiry(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        expiries = [badge["expires_at"] for badges in self._badges.values() for badge in badges
                    if badge["expires_at"] is not None]
        if expiries:
            self._timer = asyncio.get_running_loop().call_later(
                max(0.0, min(expiries) - time.time()), self._expire
            )

    def _expire(self) -> None:
        self._timer = None
        before = sum(map(len, self._badges.values()))
        self._badges = {plugin_id: badges for plugin_id, badges in self.snapshot().items() if badges}
        self._schedule_expiry()
        if sum(map(len, self._badges.values())) != before:
            self._on_changed()
