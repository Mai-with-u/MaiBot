"""提供 Platform IO 的轻量入站消息去重能力。

当前实现基于 ``dict + heapq``：
- ``dict`` 保存去重键到过期时间的映射
- ``heapq`` 维护按过期时间排序的小顶堆

这样就不需要在每次检查时全表扫描，而是通过懒清理逐步弹出已经过期
或已经失效的堆节点。
"""

from typing import Dict, List, Tuple

import heapq
import time


class MessageDeduplicator:
    """使用基于 TTL 的内存缓存进行入站消息去重。

    主要用于解决同一条外部消息被重复送入 Core 的问题，例如双路径并存、
    适配器重试、重连或重复回调等场景。Broker 可以借助这个组件在进入
    Core 前先拦住重复投递，避免重复处理、重复回复和重复入库。

    当前实现使用 ``dict + heapq`` 维护过期时间：
    - ``dict`` 负责 ``O(1)`` 级别的去重键查找
    - ``heapq`` 负责按过期时间顺序做懒清理

    这比“每次调用都全表扫描过期项”的实现更适合高吞吐消息场景。

    Notes:
        复杂度说明如下，设 ``n`` 为当前缓存中的有效去重键数量：

        - 单次 ``mark_seen()`` 在常见路径下的时间复杂度接近 ``O(log n)``
        - 从长期摊还角度看，``mark_seen()`` 的时间复杂度也接近 ``O(log n)``
        - 如果某次调用恰好触发一批过期键的集中清理，则该次调用的最坏时间复杂度
          可达到 ``O(k log n)``，其中 ``k`` 为本次被弹出或清理的键数量
        - 空间复杂度为 ``O(n)``
    """

    def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 10000) -> None:
        """初始化去重器。

        Args:
            ttl_seconds: 每个去重键在缓存中的保留时长，单位为秒。
            max_entries: 缓存允许保留的最大有效键数量，超出后会触发
                机会性淘汰。

        Raises:
            ValueError: 当 ``ttl_seconds`` 或 ``max_entries`` 非正数时抛出。
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须大于 0")
        if max_entries <= 0:
            raise ValueError("max_entries 必须大于 0")

        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._expire_heap: List[Tuple[float, str]] = []
        self._seen: Dict[str, float] = {}

    def mark_seen(self, dedupe_key: str) -> bool:
        """标记一条去重键已经出现过。

        Args:
            dedupe_key: 能稳定标识一条外部入站消息的去重键。

        Returns:
            bool: 若该键在当前 TTL 窗口内首次出现则返回 ``True``，
            否则返回 ``False``。

        Notes:
            方法会先基于小顶堆做一次懒清理，再判断当前键是否仍在有效期内。
            如果缓存已达到上限，则会优先淘汰“最早过期的仍然有效的键”。

            复杂度方面，常见路径下该方法接近 ``O(log n)``；如果恰好需要
            集中清理一批过期键，则单次调用最坏可达到 ``O(k log n)``。
        """
        now = time.monotonic()
        self._purge_expired(now)

        expires_at = self._seen.get(dedupe_key)
        if expires_at is not None and expires_at > now:
            return False

        if len(self._seen) >= self._max_entries:
            self._evict_earliest_live()

        expires_at = now + self._ttl_seconds
        self._seen[dedupe_key] = expires_at
        heapq.heappush(self._expire_heap, (expires_at, dedupe_key))
        return True

    def clear(self) -> None:
        """清空全部去重缓存。"""
        self._expire_heap.clear()
        self._seen.clear()

    def _purge_expired(self, now: float) -> None:
        """从缓存中清理已经过期的去重键。

        Args:
            now: 当前单调时钟时间戳。

        Notes:
            堆中可能存在旧版本节点。例如同一个 ``dedupe_key`` 被重新写入后，
            旧的过期时间节点仍会留在堆里。这里会通过和 ``dict`` 中当前值比对，
            跳过这类失效节点。
        """
        while self._expire_heap and self._expire_heap[0][0] <= now:
            expires_at, dedupe_key = heapq.heappop(self._expire_heap)
            current_expires_at = self._seen.get(dedupe_key)
            if current_expires_at is None:
                continue
            if current_expires_at != expires_at:
                continue
            self._seen.pop(dedupe_key, None)

    def _evict_earliest_live(self) -> None:
        """当缓存达到容量上限时，淘汰一条最早过期的有效键。

        Notes:
            堆顶可能是已经过期或已失效的旧节点，因此这里同样需要循环弹出，
            直到找到一条当前仍然在 ``dict`` 中生效的键。
        """
        while self._expire_heap:
            expires_at, dedupe_key = heapq.heappop(self._expire_heap)
            current_expires_at = self._seen.get(dedupe_key)
            if current_expires_at is None:
                continue
            if current_expires_at != expires_at:
                continue
            self._seen.pop(dedupe_key, None)
            return
