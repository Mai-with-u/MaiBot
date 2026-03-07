"""Host-side EventDispatcher

负责:
1. 按事件类型查询已注册的 event_handler（通过 ComponentRegistry）
2. 按 weight 排序，依次通过 RPC 调用 Runner 中的处理器
3. 支持阻塞（intercept_message）和非阻塞分发
4. 事件结果历史记录
"""

from typing import Any, Awaitable, Callable

import asyncio

from src.common.logger import get_logger
from src.plugin_runtime.host.component_registry import ComponentRegistry, RegisteredComponent

logger = get_logger("plugin_runtime.host.event_dispatcher")

# invoke_fn 类型: async (plugin_id, component_name, args) -> response_payload dict
InvokeFn = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]


class EventResult:
    """单个 EventHandler 的执行结果"""
    __slots__ = ("handler_name", "success", "continue_processing", "modified_message", "custom_result")

    def __init__(
        self,
        handler_name: str,
        success: bool = True,
        continue_processing: bool = True,
        modified_message: dict[str, Any] | None = None,
        custom_result: Any = None,
    ):
        self.handler_name = handler_name
        self.success = success
        self.continue_processing = continue_processing
        self.modified_message = modified_message
        self.custom_result = custom_result


class EventDispatcher:
    """Host-side 事件分发器

    由业务层调用 dispatch_event()，
    内部通过 ComponentRegistry 查询 handler，
    再通过提供的 invoke_fn 回调 RPC 到 Runner 执行。
    """

    def __init__(self, registry: ComponentRegistry) -> None:
        self._registry: ComponentRegistry = registry
        self._result_history: dict[str, list[EventResult]] = {}
        self._history_enabled: set[str] = set()

    def enable_history(self, event_type: str) -> None:
        self._history_enabled.add(event_type)
        self._result_history.setdefault(event_type, [])

    def get_history(self, event_type: str) -> list[EventResult]:
        return self._result_history.get(event_type, [])

    def clear_history(self, event_type: str) -> None:
        if event_type in self._result_history:
            self._result_history[event_type] = []

    async def dispatch_event(
        self,
        event_type: str,
        invoke_fn: InvokeFn,
        message: dict[str, Any] | None = None,
        extra_args: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        """分发事件到所有对应 handler。

        Args:
            event_type: 事件类型字符串
            invoke_fn: 异步回调，签名 (plugin_id, component_name, args) -> response_payload dict
            message: MaiMessages 序列化后的 dict（可选）
            extra_args: 额外参数

        Returns:
            (should_continue, modified_message_dict)
        """
        handlers = self._registry.get_event_handlers(event_type)
        if not handlers:
            return True, None

        should_continue = True
        modified_message: dict[str, Any] | None = None
        fire_and_forget_tasks: list[asyncio.Task] = []

        for handler in handlers:
            intercept = handler.metadata.get("intercept_message", False)
            args = {
                "event_type": event_type,
                "message": modified_message or message,
                **(extra_args or {}),
            }

            if intercept:
                # 阻塞执行
                result = await self._invoke_handler(invoke_fn, handler, args, event_type)
                if result and not result.continue_processing:
                    should_continue = False
                if result and result.modified_message:
                    modified_message = result.modified_message
            else:
                # 非阻塞
                task = asyncio.create_task(
                    self._invoke_handler(invoke_fn, handler, args, event_type)
                )
                fire_and_forget_tasks.append(task)

        # 不等待 fire-and-forget 任务（但不丢弃引用以防 GC）
        if fire_and_forget_tasks:
            for t in fire_and_forget_tasks:
                t.add_done_callback(lambda _t: None)

        return should_continue, modified_message

    async def _invoke_handler(
        self,
        invoke_fn: InvokeFn,
        handler: RegisteredComponent,
        args: dict[str, Any],
        event_type: str,
    ) -> EventResult | None:
        """调用单个 handler 并收集结果。"""
        try:
            resp = await invoke_fn(handler.plugin_id, handler.name, args)
            result = EventResult(
                handler_name=handler.full_name,
                success=resp.get("success", True),
                continue_processing=resp.get("continue_processing", True),
                modified_message=resp.get("modified_message"),
                custom_result=resp.get("custom_result"),
            )
        except Exception as e:
            logger.error(f"EventHandler {handler.full_name} 执行失败: {e}", exc_info=True)
            result = EventResult(
                handler_name=handler.full_name,
                success=False,
                continue_processing=True,
            )

        if event_type in self._history_enabled:
            self._result_history.setdefault(event_type, []).append(result)

        return result
