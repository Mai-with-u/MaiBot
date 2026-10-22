"""关停步骤计时及独立线程现场采样，不读取源码行或局部变量。"""

from contextlib import contextmanager
from typing import Iterator

import asyncio
import sys
import threading
import time
import traceback

from .logger import get_logger

logger = get_logger("shutdown_diagnostics")


@contextmanager
def shutdown_step(step_name: str, *, warn_after: float = 3.0, interval: float = 1.0) -> Iterator[None]:
    """慢步骤每五秒采样线程栈；心跳区分异步等待与事件循环阻塞。"""
    loop = asyncio.get_running_loop()
    started = last_heartbeat = time.monotonic()
    stopped = threading.Event()
    handle = None

    def heartbeat() -> None:
        nonlocal last_heartbeat, handle
        last_heartbeat = time.monotonic()
        handle = loop.call_later(interval, heartbeat)

    def task_stacks() -> None:
        if stopped.is_set():
            return
        for task in asyncio.all_tasks(loop):
            frames = task.get_stack(limit=8)
            stack = "\n".join(
                f"  {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}" for frame in frames
            )
            logger.warning(f"关闭任务现场: step={step_name} task={task.get_name()}\n{stack}")

    def sample() -> None:
        next_report = started + warn_after
        while not stopped.wait(interval):
            now = time.monotonic()
            if now < next_report:
                continue
            next_report = now + 5.0
            names = {thread.ident: thread.name for thread in threading.enumerate()}
            # 主循环卡住时仍可捕获同步调用；工作线程栈用于定位 to_thread 等待。
            frames = sys._current_frames()
            try:
                stacks = []
                for ident, frame in frames.items():
                    if ident == threading.get_ident():
                        continue
                    summary = traceback.StackSummary.extract(traceback.walk_stack(frame), lookup_lines=False)
                    stack = "\n".join(
                        f"  {entry.filename}:{entry.lineno} in {entry.name}" for entry in reversed(summary[:40])
                    )
                    stacks.append(f"thread={names.get(ident, str(ident))}\n{stack}")
            finally:
                frames.clear()
                del frame
            logger.warning(
                f"关闭步骤等待现场: step={step_name} 耗时={now - started:.3f}s "
                f"主循环无心跳={now - last_heartbeat:.3f}s\n" + "\n".join(stacks)
            )
            # 循环正常时补充协程等待位置；阻塞时上面的线程栈已记录现场。
            loop.call_soon_threadsafe(task_stacks)

    logger.info(f"关闭步骤开始: step={step_name}")
    heartbeat()
    thread = threading.Thread(target=sample, name="shutdown-stack-sampler", daemon=True)
    thread.start()
    outcome = "failed"
    try:
        yield
        outcome = "completed"
    finally:
        stopped.set()
        if handle is not None:
            handle.cancel()
        logger.info(f"关闭步骤结束: step={step_name} 耗时={time.monotonic() - started:.3f}s outcome={outcome}")
