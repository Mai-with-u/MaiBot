from collections import deque
from threading import Lock
from typing import Deque, Dict, Tuple

import asyncio
import time

from src.common.logger import get_logger

logger = get_logger("embedding_rate_limit")

_SAMPLE_WINDOW = 60.0
_MAX_INTERVAL = 5.0
_RECOVERY_SECONDS = 300.0


class EmbeddingRateLimit:
    """同一供应商和模型共享发送节奏，允许主循环与 WebUI 循环同时使用。"""

    def __init__(self) -> None:
        """Initialize an unthrottled limiter with state shared across event loops."""
        self._lock = Lock()
        self._times: Deque[float] = deque(maxlen=32)
        self.interval = 0.0
        self.halvings = 0
        self._generation = 0
        self._last_429 = 0.0

    async def wait(self) -> Tuple[int, float]:
        """Wait for a send slot, returning its generation and total wait in seconds.

        Recover after the cooldown and recheck the shared interval after each sleep.
        """
        started = time.monotonic()
        while True:
            # 不预订未来时隙：醒来后重新检查，避免排队请求沿用减速前的间隔。
            with self._lock:
                now = time.monotonic()
                if self.halvings and now - self._last_429 >= _RECOVERY_SECONDS:
                    self.interval = 0.0
                    self.halvings = 0
                    self._times.clear()
                    # 世代单调递增，防止恢复前的迟到 429 再次减速。
                    self._generation += 1
                delay = self._times[-1] + self.interval - now if self._times and self.interval else 0.0
                if delay <= 0:
                    self._times.append(now)
                    return self._generation, now - started
            await asyncio.sleep(delay)

    def reduce(self, generation: int) -> None:
        """Apply a 429 using the generation returned by ``wait()``.

        Ignore stale generations; current errors refresh cooldown even at the backoff cap.
        """
        with self._lock:
            # 同一代并发请求只减速一次；持续收到 429 时延后恢复。
            if generation != self._generation:
                return
            now = time.monotonic()
            self._last_429 = now
            if self.halvings >= 2:
                return
            recent = [sent for sent in self._times if now - sent <= _SAMPLE_WINDOW]
            interval = self.interval or ((now - recent[0]) / len(recent) if len(recent) > 1 else 0.5)
            self.interval = min(max(interval, 0.001) * 2, _MAX_INTERVAL)
            self.halvings += 1
            self._generation += 1
            interval, halvings = self.interval, self.halvings
        logger.warning(f"Embedding HTTP 429，发送减速 ({halvings}/2): 间隔={interval:.3f}s")


_limits: Dict[Tuple[str, str, str], EmbeddingRateLimit] = {}
_limits_lock = Lock()


def get_embedding_rate_limit(provider: str, base_url: str, model: str) -> EmbeddingRateLimit:
    """Return the shared limiter for a provider, normalized endpoint, and model."""
    key = (provider, base_url.rstrip("/"), model)
    with _limits_lock:
        if key not in _limits:
            _limits[key] = EmbeddingRateLimit()
        return _limits[key]
