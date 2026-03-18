"""
Hook Dispatch 系统

插件可以注册自己的Hook，当特定函数被调用时，Hook Dispatch系统会将调用转发给插件的Hook处理函数。
每个Hook的参数随Hook点位确定，因此参数是易变的。插件开发者需要根据Hook点位的定义来编写Hook处理函数。
在参数/返回值匹配的情况下允许修改参数/返回值。

HookDispatcher 负责：
1. 按 stage 查询已注册的 hook_handler（通过 ComponentRegistry）
2. 按 priority 排序，区分 blocking 和非 blocking 模式
3. blocking 模式：依次同步调用，支持修改参数/提前终止
4. 非 blocking 模式：异步调用，不阻塞主流程
5. 支持通过 global_config.plugin_runtime.hook_blocking_timeout_sec 设置超时上限
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from src.common.logger import get_logger
from src.config.config import global_config


if TYPE_CHECKING:
    from .supervisor import PluginRunnerSupervisor
    from .component_registry import ComponentRegistry, HookHandlerEntry

logger = get_logger("plugin_runtime.host.hook_dispatcher")


@dataclass
class HookResult:
    """单个 HookHandler 的执行结果"""

    handler_name: str
    success: bool = field(default=True)
    continue_processing: bool = field(default=True)
    modified_kwargs: Optional[Dict[str, Any]] = field(default=None)
    custom_result: Any = field(default=None)


class HookDispatcher:
    """Host-side Hook 分发器

    由业务层调用 hook_dispatch()，
    内部通过 ComponentRegistry 查询 handler，
    再通过提供的 invoke_fn 回调 RPC 到 Runner 执行。
    """

    def __init__(self, component_registry: "ComponentRegistry") -> None:
        """初始化 HookDispatcher

        Args:
            component_registry: ComponentRegistry 实例，用于查询已注册的 hook_handler
        """
        self._component_registry: "ComponentRegistry" = component_registry
        self._background_tasks: Set[asyncio.Task] = set()

    async def stop(self) -> None:
        """停止 HookDispatcher，取消所有未完成的后台任务"""
        for task in self._background_tasks:
            task.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

    async def hook_dispatch(
        self,
        stage: str,
        supervisor: "PluginRunnerSupervisor",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """分发 hook 到所有对应 handler 的便捷方法。

        内置了通过 PluginRunnerSupervisor.invoke_plugin 调用 plugin 的逻辑，
        无需调用方手动构造 invoke_fn 闭包。

        Args:
            stage: hook 名称
            supervisor: PluginRunnerSupervisor 实例，用于调用 invoke_plugin
            **kwargs: 关键字参数，会展开传递给 handler

        Returns:
            modified_kwargs (Dict[str, Any]): 经过所有 handler 修改后的关键字参数
        """
        handler_entries = self._component_registry.get_hook_handlers(stage)
        if not handler_entries:
            return kwargs

        current_kwargs = kwargs.copy()
        blocking_handlers: List["HookHandlerEntry"] = []
        non_blocking_handlers: List["HookHandlerEntry"] = []

        # 分离 blocking 和非 blocking handler
        for entry in handler_entries:
            if entry.blocking:
                blocking_handlers.append(entry)
            else:
                non_blocking_handlers.append(entry)

        # 处理 blocking handlers（同步调用，支持修改参数/提前终止）
        timeout = global_config.plugin_runtime.hook_blocking_timeout_sec or 30.0
        for entry in blocking_handlers:
            hook_args = {"stage": stage, **current_kwargs}
            try:
                # 应用超时控制
                result = await asyncio.wait_for(
                    self._invoke_handler(supervisor, entry, hook_args),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.error(f"Blocking HookHandler {entry.full_name} 执行超时 (>{timeout}秒)，跳过")
                result = HookResult(handler_name=entry.full_name, success=False, continue_processing=True)

            if result:
                if result.modified_kwargs is not None:
                    current_kwargs = result.modified_kwargs
                if not result.continue_processing:
                    logger.info(f"HookHandler {entry.full_name} 终止了后续处理")
                    break

        # 处理 non-blocking handlers（异步调用，不阻塞主流程）
        for entry in non_blocking_handlers:
            async_kwargs = current_kwargs.copy()
            hook_args = {"stage": stage, **async_kwargs}
            task = asyncio.create_task(
                asyncio.wait_for(self._invoke_handler(supervisor, entry, hook_args), timeout=timeout)
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        return current_kwargs

    async def _invoke_handler(
        self,
        supervisor: "PluginRunnerSupervisor",
        handler_entry: "HookHandlerEntry",
        args: Dict[str, Any],
    ) -> Optional[HookResult]:
        """调用单个 handler 并收集结果。

        Args:
            supervisor: PluginRunnerSupervisor 实例
            handler_entry: HookHandlerEntry 实例
            args: 传递给 handler 的参数字典
            stage: hook 名称

        Returns:
            Optional[HookResult]: 执行结果，如果执行失败则返回 None
        """
        try:
            resp_envelope = await supervisor.invoke_plugin(
                "plugin.invoke_hook", handler_entry.plugin_id, handler_entry.name, args
            )
            resp = resp_envelope.payload
            result = HookResult(
                handler_name=handler_entry.full_name,
                success=resp.get("success", True),
                continue_processing=resp.get("continue_processing", True),
                modified_kwargs=resp.get("modified_kwargs"),
                custom_result=resp.get("custom_result"),
            )
        except Exception as e:
            logger.error(f"HookHandler {handler_entry.full_name} 执行失败：{e}", exc_info=True)
            result = HookResult(handler_name=handler_entry.full_name, success=False, continue_processing=True)

        return result
