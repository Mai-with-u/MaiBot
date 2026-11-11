"""按实际运行区间累计插件使用时长；持久化文件不保存跨进程的计时起点。"""

from pathlib import Path
from typing import Callable, Dict, Iterable

import asyncio
import json
import math
import time

from src.common.atomic_file import quarantine_file, write_private
from src.common.logger import get_logger

logger = get_logger("plugin_runtime.usage_tracker")


class PluginUsageTracker:
    """内存状态只在主循环访问，文件读写在线程池串行执行。"""

    def __init__(self, path: Path, clock: Callable[[], float] = time.monotonic) -> None:
        self._path = path
        self._clock = clock
        self._totals: Dict[str, float] = {}
        self._active_since: Dict[str, float] = {}
        self._save_lock = asyncio.Lock()

    def _read_totals(self) -> Dict[str, float]:
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except FileNotFoundError:
            return {}
        if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("usage_seconds"), dict):
            raise ValueError(f"插件使用时长记录格式错误: {self._path}")
        totals: Dict[str, float] = {}
        for plugin_id, seconds in value["usage_seconds"].items():
            if (
                not isinstance(plugin_id, str) or not plugin_id.strip()
                or isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds) or seconds < 0
            ):
                raise ValueError(f"插件 {plugin_id!r} 的累计使用时长无效: {self._path}")
            totals[plugin_id] = float(seconds)
        return totals

    async def load(self) -> None:
        self._totals = await asyncio.to_thread(self._load_totals)
        self._active_since.clear()

    def _load_totals(self) -> Dict[str, float]:
        try:
            return self._read_totals()
        except (ValueError, OverflowError):
            backup = quarantine_file(self._path)
            logger.warning(f"插件累计使用时长记录损坏，已保留原文件并重新计时：{backup}", exc_info=True)
            return {}

    def reconcile(self, active_plugin_ids: Iterable[str]) -> None:
        """只结算离开运行集合的插件；重复通知不会重置仍在运行的起点。"""
        now = self._clock()
        active = set(active_plugin_ids)
        for plugin_id in self._active_since.keys() - active:
            self._totals[plugin_id] = self._totals.get(plugin_id, 0.0) + now - self._active_since.pop(plugin_id)
        for plugin_id in active - self._active_since.keys():
            self._active_since[plugin_id] = now

    def snapshot(self) -> Dict[str, float]:
        now = self._clock()
        totals = dict(self._totals)
        for plugin_id, started_at in self._active_since.items():
            totals[plugin_id] = totals.get(plugin_id, 0.0) + now - started_at
        return totals

    def _write_totals(self, totals: Dict[str, float]) -> None:
        content = json.dumps({"version": 1, "usage_seconds": totals}, ensure_ascii=False, allow_nan=False)
        write_private(self._path, content + "\n")

    async def save(self) -> None:
        async with self._save_lock:
            write_task = asyncio.create_task(asyncio.to_thread(self._write_totals, self.snapshot()))
            try:
                await asyncio.shield(write_task)
            except asyncio.CancelledError:
                # 线程中的原子替换不能取消；完成后才释放锁，避免旧快照覆盖停止时的新快照。
                await write_task
                raise
