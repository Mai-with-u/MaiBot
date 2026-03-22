"""
内存监控模块

提供内存使用监控和预警功能。
"""

import gc
import threading
import time
from typing import Callable, Optional

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

from src.common.logger import get_logger

logger = get_logger("A_Memorix.MemoryMonitor")


class MemoryMonitor:
    """
    内存监控器

    功能：
    - 实时监控内存使用
    - 超过阈值时触发警告
    - 支持自动垃圾回收
    """

    def __init__(
        self,
        max_memory_mb: int,
        warning_threshold: float = 0.9,
        check_interval: float = 10.0,
        enable_auto_gc: bool = True,
    ):
        """
        初始化内存监控器

        Args:
            max_memory_mb: 最大内存限制（MB）
            warning_threshold: 警告阈值（0-1之间，默认0.9表示90%）
            check_interval: 检查间隔（秒）
            enable_auto_gc: 是否启用自动垃圾回收
        """
        self.max_memory_mb = max_memory_mb
        self.warning_threshold = warning_threshold
        self.check_interval = check_interval
        self.enable_auto_gc = enable_auto_gc

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._callbacks: list[Callable[[float, float], None]] = []

    def start(self):
        """启动监控"""
        if self._running:
            logger.warning("内存监控已在运行")
            return

        if not HAS_PSUTIL:
            logger.warning("psutil 未安装，内存监控功能不可用")
            return

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info(f"内存监控已启动 (限制: {self.max_memory_mb}MB)")

    def stop(self):
        """停止监控"""
        if not self._running:
            return

        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("内存监控已停止")

    def register_callback(self, callback: Callable[[float, float], None]):
        """
        注册内存超限回调函数

        Args:
            callback: 回调函数，接收 (当前使用MB, 限制MB) 参数
        """
        self._callbacks.append(callback)

    def get_current_memory_mb(self) -> float:
        """
        获取当前进程内存使用量（MB）

        Returns:
            内存使用量（MB）
        """
        if not HAS_PSUTIL:
            # 降级方案：使用内置函数
            import sys
            return sys.getsizeof(gc.get_objects()) / 1024 / 1024

        process = psutil.Process()
        return process.memory_info().rss / 1024 / 1024

    def get_memory_usage_ratio(self) -> float:
        """
        获取内存使用率

        Returns:
            使用率（0-1之间）
        """
        current = self.get_current_memory_mb()
        return current / self.max_memory_mb if self.max_memory_mb > 0 else 0

    def _monitor_loop(self):
        """监控循环"""
        while self._running:
            try:
                current_mb = self.get_current_memory_mb()
                ratio = current_mb / self.max_memory_mb if self.max_memory_mb > 0 else 0

                # 检查是否超过阈值
                if ratio >= self.warning_threshold:
                    logger.warning(
                        f"内存使用率过高: {current_mb:.1f}MB / {self.max_memory_mb}MB "
                        f"({ratio*100:.1f}%)"
                    )

                    # 触发回调
                    for callback in self._callbacks:
                        try:
                            callback(current_mb, self.max_memory_mb)
                        except Exception as e:
                            logger.error(f"内存回调执行失败: {e}")

                    # 自动垃圾回收
                    if self.enable_auto_gc:
                        before = self.get_current_memory_mb()
                        gc.collect()
                        after = self.get_current_memory_mb()
                        freed = before - after
                        if freed > 1:  # 释放超过1MB才记录
                            logger.info(f"垃圾回收释放: {freed:.1f}MB")

                # 定期垃圾回收（即使未超限）
                elif self.enable_auto_gc and int(time.time()) % 60 == 0:
                    gc.collect()

            except Exception as e:
                logger.error(f"内存监控出错: {e}")

            # 等待下次检查
            time.sleep(self.check_interval)

    def __enter__(self):
        """上下文管理器入口"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.stop()


def get_memory_info() -> dict:
    """
    获取系统内存信息

    Returns:
        内存信息字典
    """
    if not HAS_PSUTIL:
        return {"error": "psutil 未安装"}

    try:
        mem = psutil.virtual_memory()
        process = psutil.Process()

        return {
            "system_total_gb": mem.total / 1024 / 1024 / 1024,
            "system_available_gb": mem.available / 1024 / 1024 / 1024,
            "system_usage_percent": mem.percent,
            "process_mb": process.memory_info().rss / 1024 / 1024,
            "process_percent": (process.memory_info().rss / mem.total) * 100,
        }
    except Exception as e:
        return {"error": str(e)}
