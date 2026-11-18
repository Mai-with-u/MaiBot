"""让持锁的后台同步工作在协程取消前完成收尾。"""

from typing import Any, Callable, Iterable, Optional, TypeVar

import asyncio

from src.common.logger import get_logger

_Result = TypeVar("_Result")
TASK_STOP_TIMEOUT_SECONDS = 30.0
logger = get_logger("A_Memorix.AsyncWork")


async def cancel_and_wait_for_tasks(tasks: Iterable[asyncio.Task], *, operation: str) -> None:
    """有界等待取消收尾；超时不取消底层线程，也不允许调用方释放存储。"""
    workers = set(tasks)
    if not workers:
        return
    for task in workers:
        if not task.done():
            task.cancel()
    done, pending = await asyncio.wait(workers, timeout=TASK_STOP_TIMEOUT_SECONDS)
    for task in done:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(f"{operation}: task={task.get_name()} 收尾失败", exc_info=error)
    if pending:
        names = ", ".join(sorted(task.get_name() for task in pending))
        message = f"{operation} 超过 {TASK_STOP_TIMEOUT_SECONDS:g}s，任务仍未退出: {names}；底层存储与锁保持占用，请待写入结束后重试关闭"
        logger.error(message)
        raise RuntimeError(message)


async def cancellation_safe_to_thread(function: Callable[..., _Result], *args: Any, **kwargs: Any) -> _Result:
    """等待已启动的线程结束后再传播取消，调用方在整个等待期间继续持锁。

    shield 只保护工作任务；循环还要处理外层被重复取消的情况。线程异常保持原样，
    如果同时发生取消，则作为取消异常的原因保留，避免未取出的后台任务异常。
    """
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancellation: Optional[asyncio.CancelledError] = None
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError as exc:
            if worker.cancelled():
                raise
            if cancellation is None:
                cancellation = exc
        except Exception as exc:
            if cancellation is not None:
                raise cancellation from exc
            raise
    if cancellation is not None:
        raise cancellation
    return result
