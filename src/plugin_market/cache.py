"""跨主循环和 WebUI 线程共享缓存，合并同一资源的并发请求。"""

from concurrent.futures import Future
from copy import deepcopy
from threading import RLock
from typing import Any, Callable, Dict, OrderedDict, Tuple
import time


class MarketCache:
    def __init__(self, max_entries: int = 256) -> None:
        self._entries: OrderedDict[str, Tuple[float, Any]] = OrderedDict()
        self._pending: Dict[str, Future[Tuple[float, Any]]] = {}
        self._lock = RLock()
        self._max_entries = max_entries

    def get(
        self, key: str, loader: Callable[[], Any], ttl: int, force_refresh: bool = False,
    ) -> Tuple[Any, Dict[str, Any]]:
        """在线程池中调用；等待并发请求的 Future 时不持锁，也不占事件循环。"""
        with self._lock:
            entry = self._entries.get(key)
            hit = not force_refresh and entry is not None and time.monotonic() - entry[0] < ttl
            if hit:
                self._entries.move_to_end(key)
            pending = self._pending.get(key)
            owner = not hit and pending is None
            if owner:
                pending = Future()
                self._pending[key] = pending

        if not hit:
            assert pending is not None
            if owner:
                try:
                    value = loader()
                    # TTL 从拉取完成时计算，慢请求不消耗缓存有效期。
                    entry = (time.monotonic(), value)
                    with self._lock:
                        if self._pending.get(key) is pending:
                            self._entries[key] = entry
                            self._entries.move_to_end(key)
                            while len(self._entries) > self._max_entries:
                                self._entries.popitem(last=False)
                        pending.set_result(entry)
                except BaseException as exc:
                    pending.set_exception(exc)
                    raise
                finally:
                    with self._lock:
                        if self._pending.get(key) is pending:
                            del self._pending[key]
            else:
                entry = pending.result()

        assert entry is not None
        remaining = max(0, ttl - (time.monotonic() - entry[0]))
        # 返回副本，筛选、裁剪等调用方操作不会污染其他消费者的缓存。
        return deepcopy(entry[1]), {
            "hit": hit, "shared_request": not hit and not owner,
            "ttl_seconds": ttl, "remaining_seconds": int(remaining),
        }

    def invalidate(self, prefix: str) -> None:
        """写入市场互动后失效快照；旧的在途请求也不能回填失效缓存。"""
        with self._lock:
            for entries in (self._entries, self._pending):
                for key in list(entries):
                    if key.startswith(prefix):
                        del entries[key]


market_cache = MarketCache()
