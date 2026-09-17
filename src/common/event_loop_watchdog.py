"""事件循环卡顿看门狗。

WebUI 卡顿的常见原因是事件循环被同步工作阻塞，而事后很难判断是哪个循环被卡住、
卡了多久。这里按固定间隔测量「本应醒来的时间」与「实际醒来的时间」的差值并写入
日志，让卡顿在日志里直接可见，不必等到界面卡死后再挂调试器。
"""

from __future__ import annotations

from typing import Optional

import asyncio

from src.common.logger import get_logger

logger = get_logger("event_loop_watchdog")

DEFAULT_INTERVAL_SECONDS = 1.0
DEFAULT_WARN_THRESHOLD_SECONDS = 0.5


class EventLoopLagWatchdog:
    """周期性测量当前事件循环的延迟，超过阈值时记录警告。"""

    def __init__(
        self,
        loop_name: str,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        warn_threshold_seconds: float = DEFAULT_WARN_THRESHOLD_SECONDS,
    ) -> None:
        self.loop_name = loop_name
        self.interval_seconds = max(0.1, float(interval_seconds))
        self.warn_threshold_seconds = max(0.01, float(warn_threshold_seconds))

    async def run(self) -> None:
        """持续测量循环延迟；循环被阻塞期间本协程不会运行，恢复后只告警一次。"""

        loop = asyncio.get_running_loop()
        while True:
            expected_wakeup = loop.time() + self.interval_seconds
            await asyncio.sleep(self.interval_seconds)

            lag_seconds = loop.time() - expected_wakeup
            if lag_seconds >= self.warn_threshold_seconds:
                logger.warning(
                    f"事件循环卡顿: loop={self.loop_name} 迟到={lag_seconds:.2f}s "
                    f"(告警阈值 {self.warn_threshold_seconds:.2f}s)"
                )


def start_watchdog(loop_name: str) -> Optional["asyncio.Task[None]"]:
    """在**当前事件循环**上启动看门狗，未启用时返回 ``None``。

    必须在该循环内部调用：每个事件循环都需要自己的看门狗，只监测主循环会漏掉
    WebUI 自己那个循环。
    """

    from src.config.config import global_config

    log_config = global_config.log
    if not log_config.event_loop_watchdog_enabled:
        return None

    watchdog = EventLoopLagWatchdog(
        loop_name=loop_name,
        warn_threshold_seconds=log_config.event_loop_watchdog_warn_seconds,
    )
    task = asyncio.create_task(watchdog.run(), name=f"event_loop_watchdog:{loop_name}")
    logger.info(
        f"事件循环看门狗已启动: loop={loop_name} 告警阈值={watchdog.warn_threshold_seconds:.2f}s"
    )
    return task
