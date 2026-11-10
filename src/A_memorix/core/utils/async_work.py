"""让持锁的后台同步工作在协程取消前完成收尾。"""

from typing import Any, Callable, Optional, TypeVar

import asyncio

_Result = TypeVar("_Result")


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
