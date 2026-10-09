from collections import deque
from threading import Lock
from typing import Deque, Dict, Tuple

import asyncio
import time

from src.common.logger import get_logger

logger = get_logger("embedding_rate_limit")


class EmbeddingRateLimit:
    """同一供应商和模型共享发送节奏，允许主循环与 WebUI 循环同时使用。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._times: Deque[float] = deque(maxlen=32)
        self.interval = 0.0
        self.halvings = 0

    async def wait(self) -> Tuple[int, float]:
        started = time.monotonic()
        while True:
            # 不预订未来时隙：醒来后重新检查，避免排队请求沿用减速前的间隔。
            with self._lock:
                now = time.monotonic()
                delay = self._times[-1] + self.interval - now if self._times and self.interval else 0.0
                if delay <= 0:
                    self._times.append(now)
                    return self.halvings, now - started
            await asyncio.sleep(delay)

    def reduce(self, generation: int) -> None:
        with self._lock:
            # 同一代并发请求只减速一次；两次以后保持 1/4 的估算发送速度。
            if generation != self.halvings or self.halvings >= 2:
                return
            interval = self.interval or (time.monotonic() - self._times[0]) / len(self._times)
            self.interval = max(interval, 0.001) * 2
            self.halvings += 1
            interval, halvings = self.interval, self.halvings
        logger.warning(f"Embedding HTTP 429，发送速度减半 ({halvings}/2): 间隔={interval:.3f}s")


_limits: Dict[Tuple[str, str, str], EmbeddingRateLimit] = {}
_limits_lock = Lock()


def get_embedding_rate_limit(provider: str, base_url: str, model: str) -> EmbeddingRateLimit:
    key = (provider, base_url.rstrip("/"), model)
    with _limits_lock:
        if key not in _limits:
            _limits[key] = EmbeddingRateLimit()
        return _limits[key]
