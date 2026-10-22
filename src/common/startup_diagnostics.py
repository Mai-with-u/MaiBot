"""启动耗时与卡顿现场诊断；不采集配置、请求内容或栈帧局部变量。"""

from contextlib import contextmanager
from typing import Iterator, Optional

import asyncio
import os
import sys
import threading
import time
import traceback

from .logger import get_logger

logger = get_logger("startup_diagnostics")


@contextmanager
def diagnostic_stage(stage: str) -> Iterator[None]:
    """记录阶段边界，异常继续向调用者传播。"""
    started_at = time.perf_counter()
    identity = f"stage={stage} pid={os.getpid()} thread={threading.current_thread().name}"
    logger.debug(f"诊断阶段开始: {identity}")
    outcome = "failed"
    try:
        yield
        outcome = "completed"
    finally:
        elapsed = time.perf_counter() - started_at
        # 阶段耗时可能包含正常的异步等待；真实循环卡顿由看门狗单独告警。
        log = logger.warning if outcome == "failed" else logger.debug
        log(f"诊断阶段结束: {identity} 耗时={elapsed:.3f}s outcome={outcome}")


@contextmanager
def monitor_startup_loop(
    *, enabled: bool = True, warn_after: float = 3.0, interval: float = 1.0,
) -> Iterator[None]:
    """独立线程在启动主循环失去心跳时采样，退出启动范围即停止。

    与协程看门狗不同，这里能在循环尚未恢复时记录现场。每次连续卡顿最多每五秒
    输出一次；仅记录调用位置，不读取源码行或局部变量。若进程完全无法调度 Python
    线程，采样同样会延迟，不能将没有堆栈视为没有卡顿。
    """
    if not enabled:
        yield
        return

    loop = asyncio.get_running_loop()
    loop_thread_id = threading.get_ident()
    stopped = threading.Event()
    last_heartbeat = time.monotonic()
    heartbeat_handle: Optional[asyncio.TimerHandle] = None

    def heartbeat() -> None:
        nonlocal last_heartbeat, heartbeat_handle
        last_heartbeat = time.monotonic()
        heartbeat_handle = loop.call_later(interval, heartbeat)

    def sample() -> None:
        last_reported_at = float("-inf")
        while not stopped.wait(interval):
            now = time.monotonic()
            lag = now - last_heartbeat
            if lag < warn_after:
                last_reported_at = float("-inf")
                continue
            if now - last_reported_at < 5.0:
                continue
            frame = sys._current_frames().get(loop_thread_id)
            if frame is None:
                continue
            try:
                frames = traceback.StackSummary.extract(traceback.walk_stack(frame), lookup_lines=False)
                stack = "\n".join(
                    f"  {entry.filename}:{entry.lineno} in {entry.name}"
                    for entry in reversed(frames[:40])
                )
            finally:
                del frame
            if stopped.is_set():
                return
            last_reported_at = now
            logger.debug(
                f"启动主循环卡顿现场: loop=main pid={os.getpid()} "
                f"无心跳={lag:.2f}s thread_id={loop_thread_id}\n{stack}"
            )

    # 在进入同步启动代码前布置心跳与采样线程，不依赖主循环先调度监控协程。
    heartbeat()
    thread = threading.Thread(target=sample, name="startup-stack-sampler", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        if heartbeat_handle is not None:
            heartbeat_handle.cancel()
