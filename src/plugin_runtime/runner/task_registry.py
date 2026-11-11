"""按插件实例的激活代管理任务，保证清理资源前业务执行已经退出。"""

from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Callable, Coroutine, Dict, Iterable, Iterator, Optional

import asyncio

from src.plugin_runtime.protocol.errors import ErrorCode, RPCError


@dataclass
class _TaskRecord:
    """工厂创建的任务按完整生命周期追踪，已有任务按作用域引用计数。"""

    spawned: bool = False
    scopes: int = 0


@dataclass
class _Generation:
    plugin_id: str
    stopping: bool = False
    closed: bool = False
    tasks: Dict[asyncio.Task[Any], _TaskRecord] = field(default_factory=dict)
    sync_work: Dict[asyncio.Future[Any], str] = field(default_factory=dict)


@dataclass
class _CleanupLease:
    """清理权限只在钩子的作用域内有效，不能被复制的 Context 永久保留。"""

    active: bool = True


@dataclass(frozen=True)
class _Scope:
    generation: _Generation
    cleanup_lease: Optional[_CleanupLease] = None

    @property
    def allows_cleanup(self) -> bool:
        return self.cleanup_lease is not None and self.cleanup_lease.active


class PluginTaskRegistry:
    """追踪插件业务调用及其后代；取消超时后保持停止状态，不允许抢先释放资源。"""

    CANCEL_TIMEOUT_SECONDS = 5.0

    def __init__(self) -> None:
        self._generations: Dict[str, _Generation] = {}
        self._scope: ContextVar[Optional[_Scope]] = ContextVar("plugin_task_scope", default=None)

    def _generation(self, plugin_id: str) -> _Generation:
        return self._generations.setdefault(plugin_id, _Generation(plugin_id))

    def activate(self, plugin_id: str) -> None:
        """开始新实例的一代；旧上下文随后创建的任务仍属于已经关闭的旧代。"""
        previous = self._generations.get(plugin_id)
        if previous is not None:
            if any(not task.done() for task in previous.tasks) or any(
                not future.done() for future in previous.sync_work
            ):
                raise RPCError(ErrorCode.E_SHUTTING_DOWN, f"插件 {plugin_id} 的旧实例仍有任务未退出，不能重新激活")
            previous.stopping = True
            previous.closed = True
        self._generations[plugin_id] = _Generation(plugin_id)

    def is_stopping(self, plugin_id: str) -> bool:
        generation = self._generations.get(plugin_id)
        return generation is not None and generation.stopping

    def begin_stop(self, plugin_ids: Iterable[str]) -> None:
        """无等待地封住整批插件入口，避免卸载依赖时其他目标仍接收新业务。"""
        for plugin_id in plugin_ids:
            self._generation(plugin_id).stopping = True

    @contextmanager
    def task_factory(self) -> Iterator[None]:
        """在 Runner 整个运行期安装工厂，并保留已有工厂及其参数约定。"""
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()

        def create_task(
            target_loop: asyncio.AbstractEventLoop,
            coroutine: Coroutine[Any, Any, Any],
            **kwargs: Any,
        ) -> asyncio.Task[Any]:
            context = kwargs.get("context")
            scope = self._scope.get() if context is None else context.get(self._scope)
            if previous_factory is None:
                task = asyncio.Task(coroutine, loop=target_loop, **kwargs)
            else:
                task = previous_factory(target_loop, coroutine, **kwargs)
            if scope is not None:
                generation = scope.generation
                record = self._record_task(generation, task)
                record.spawned = True
                # call_later 等回调会保留旧 Context，不能让旧实例在重载后重新启动业务。
                if generation.closed and not scope.allows_cleanup and not task.done() and not task.cancelling():
                    task.cancel()
            return task

        loop.set_task_factory(create_task)
        try:
            yield
        finally:
            loop.set_task_factory(previous_factory)

    def _record_task(self, generation: _Generation, task: asyncio.Task[Any]) -> _TaskRecord:
        record = generation.tasks.get(task)
        if record is None:
            record = _TaskRecord()
            generation.tasks[task] = record
            task.add_done_callback(lambda finished: generation.tasks.pop(finished, None))
        return record

    @contextmanager
    def scope(self, plugin_id: str, *, cleanup: bool = False) -> Iterator[None]:
        """给当前调用和之后创建的任务归属；清理作用域可在停止后执行。"""
        inherited = self._scope.get()
        generation = (
            inherited.generation
            if inherited is not None and inherited.generation.plugin_id == plugin_id
            else self._generation(plugin_id)
        )
        if generation.stopping and not cleanup:
            raise RPCError(ErrorCode.E_SHUTTING_DOWN, f"插件 {plugin_id} 正在停止，拒绝新调用")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("插件任务作用域必须运行在 asyncio Task 内")
        record = self._record_task(generation, task)
        record.scopes += 1
        cleanup_lease = _CleanupLease() if cleanup else None
        token = self._scope.set(_Scope(generation, cleanup_lease))
        try:
            yield
        finally:
            if cleanup_lease is not None:
                cleanup_lease.active = False
            self._scope.reset(token)
            record.scopes -= 1
            # 后代任务也可能再进入 scope，退出内层作用域不能提前解除它的任务归属。
            if not record.spawned and record.scopes == 0:
                generation.tasks.pop(task, None)

    async def invoke_sync(self, handler: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """登记真正的线程工作；取消调用方后，卸载仍需等待线程实际退出。"""
        scope = self._scope.get()
        if scope is None:
            raise RuntimeError("同步插件函数必须在插件任务作用域内执行")
        generation = scope.generation
        if generation.stopping and not scope.allows_cleanup:
            raise RPCError(ErrorCode.E_SHUTTING_DOWN, f"插件 {generation.plugin_id} 正在停止，拒绝同步调用")
        loop = asyncio.get_running_loop()
        context = copy_context()
        future = loop.run_in_executor(None, context.run, partial(handler, *args, **kwargs))
        generation.sync_work[future] = f"sync:{generation.plugin_id}"
        detached = False

        def finish(completed: asyncio.Future[Any]) -> None:
            generation.sync_work.pop(completed, None)
            if not completed.cancelled():
                exception = completed.exception()
                if detached and exception is not None:
                    loop.call_exception_handler({
                        "message": f"插件 {generation.plugin_id} 同步调用取消等待后执行失败",
                        "exception": exception,
                        "future": completed,
                    })

        future.add_done_callback(finish)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            detached = True
            raise

    async def cancel_and_wait(self, plugin_id: str) -> None:
        """收完此代的任务和线程；分别在清理钩子前后调用，超时不释放资源。"""
        generation = self._generation(plugin_id)
        generation.stopping = True
        current = asyncio.current_task()
        deadline = asyncio.get_running_loop().time() + self.CANCEL_TIMEOUT_SECONDS
        while True:
            # 保持创建顺序，先取消父调用；它向所等待的子任务传播取消后，不重复 cancel。
            tasks = [
                task for task in list(generation.tasks)
                if task is not current and not task.done()
            ]
            for task in tasks:
                if not task.cancelling():
                    task.cancel()
            futures = [future for future in generation.sync_work if not future.done()]
            pending = [*tasks, *futures]
            if not pending:
                generation.closed = True
                return
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                self._raise_unfinished(generation, tasks, futures)
            _, unfinished = await asyncio.wait(pending, timeout=remaining)
            if unfinished:
                self._raise_unfinished(generation, tasks, futures)
            # 取消期间的 finally 仍可能创建子任务，下一轮也必须收完才能清理插件资源。

    @staticmethod
    def _raise_unfinished(
        generation: _Generation,
        tasks: Iterable[asyncio.Task[Any]],
        futures: Iterable[asyncio.Future[Any]],
    ) -> None:
        names = [task.get_name() for task in tasks if not task.done()]
        names.extend(generation.sync_work[future] for future in futures if not future.done())
        raise RPCError(
            ErrorCode.E_SHUTTING_DOWN,
            f"插件 {generation.plugin_id} 停止未完成，任务仍在运行: {', '.join(names)}；已保留实例和资源",
        )
