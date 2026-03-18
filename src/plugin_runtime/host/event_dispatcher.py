"""Host-side EventDispatcher

负责:
1. 按事件类型查询已注册的 event_handler（通过 ComponentRegistry）
2. 按 weight 排序，依次通过 RPC 调用 Runner 中的处理器
3. 支持阻塞（intercept_message）和非阻塞分发
4. 事件结果历史记录（有上限）
"""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

import asyncio

from src.common.logger import get_logger


if TYPE_CHECKING:
    from .supervisor import PluginRunnerSupervisor
    from .component_registry import ComponentRegistry, EventHandlerEntry

logger = get_logger("plugin_runtime.host.event_dispatcher")

# invoke_fn 类型: async (plugin_id, component_name, args) -> response_payload dict
InvokeFn = Callable[[str, str, Dict[str, Any]], Awaitable[Dict[str, Any]]]
# 每个事件类型的最大历史记录数量，防止内存无限增长
_MAX_HISTORY_LENGTH = 100


@dataclass
class EventResult:
    """单个 EventHandler 的执行结果"""

    handler_name: str
    success: bool = field(default=True)
    continue_processing: bool = field(default=True)
    modified_message: Optional[Dict[str, Any]] = field(default=None)
    custom_result: Any = field(default=None)


class EventDispatcher:
    """Host-side 事件分发器

    由业务层调用 dispatch_event()，
    内部通过 ComponentRegistry 查询 handler，
    再通过提供的 invoke_fn 回调 RPC 到 Runner 执行。
    """

    def __init__(self, component_registry: "ComponentRegistry") -> None:
        self._component_registry: "ComponentRegistry" = component_registry
        self._result_history: Dict[str, List[EventResult]] = {}
        self._history_enabled: Set[str] = set()
        # 保持 fire-and-forget task 的强引用，防止被 GC 回收
        self._background_tasks: Set[asyncio.Task] = set()

    def enable_history(self, event_type: str) -> None:
        self._history_enabled.add(event_type)
        self._result_history.setdefault(event_type, [])

    def disable_history(self, event_type: str) -> None:
        self._history_enabled.discard(event_type)
        self._result_history.pop(event_type, None)

    def get_history(self, event_type: str) -> List[EventResult]:
        return self._result_history.get(event_type, [])

    def clear_history(self, event_type: str) -> None:
        if event_type in self._result_history:
            self._result_history[event_type] = []

    async def dispatch_event(
        self,
        event_type: str,
        supervisor: "PluginRunnerSupervisor",
        message_dict: Optional[Dict[str, Any]] = None,
        extra_args: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """分发事件到所有对应 handler 的便捷方法。

        内置了通过 PluginSupervisor.invoke_plugin 调用 plugin.emit_event 的逻辑，
        无需调用方手动构造 invoke_fn 闭包。

        Args:
            event_type: 事件类型字符串
            supervisor: PluginSupervisor 实例，用于调用 invoke_plugin
            message: MaiMessages 序列化后的 dict（可选）
            extra_args: 额外参数

        Returns:
            (should_continue, modified_message_dict) (bool, Dict[str, Any] | None): (是否继续后续执行, 可选的修改后的消息字典)
        """
        handler_entries = self._component_registry.get_event_handlers(event_type)
        if not handler_entries:
            return True, None

        should_continue = True
        modified_message: Optional[Dict[str, Any]] = message_dict
        intercept_handlers: List["EventHandlerEntry"] = []
        non_blocking_handlers: List["EventHandlerEntry"] = []

        for entry in handler_entries:
            if entry.intercept_message:
                intercept_handlers.append(entry)
            else:
                non_blocking_handlers.append(entry)

        for entry in intercept_handlers:
            args = {
                "event_type": event_type,
                "message": modified_message,
                **(extra_args or {}),
            }
            result = await self._invoke_handler(supervisor, entry, args, event_type)
            if result and not result.continue_processing:
                should_continue = False
                break
            if result and result.modified_message:
                modified_message = result.modified_message

        if should_continue:
            final_message = modified_message
            for entry in non_blocking_handlers:
                async_message = final_message.copy() if final_message else final_message
                args = {
                    "event_type": event_type,
                    "message": async_message,
                    **(extra_args or {}),
                }
                # 非阻塞：保持实例级强引用，防止 task 被 GC 回收
                task = asyncio.create_task(self._invoke_handler(supervisor, entry, args, event_type))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

        return should_continue, modified_message

    async def _invoke_handler(
        self,
        supervisor: "PluginRunnerSupervisor",
        handler_entry: "EventHandlerEntry",
        args: Dict[str, Any],
        event_type: str,
    ) -> Optional[EventResult]:
        """调用单个 handler 并收集结果。"""
        try:
            resp_envelope = await supervisor.invoke_plugin(
                "plugin.emit_event", handler_entry.plugin_id, handler_entry.name, args
            )
            resp = resp_envelope.payload
            result = EventResult(
                handler_name=handler_entry.full_name,
                success=resp.get("success", True),
                continue_processing=resp.get("continue_processing", True),
                modified_message=resp.get("modified_message"),
                custom_result=resp.get("custom_result"),
            )
        except Exception as e:
            logger.error(f"EventHandler {handler_entry.full_name} 执行失败: {e}", exc_info=True)
            result = EventResult(handler_name=handler_entry.full_name, success=False, continue_processing=True)

        if event_type in self._history_enabled:
            history_list = self._result_history.setdefault(event_type, [])
            history_list.append(result)
            # 自动清理超出限制的旧记录，防止内存无限增长
            if len(history_list) > _MAX_HISTORY_LENGTH:
                # 保留最新的 _MAX_HISTORY_LENGTH 条记录
                self._result_history[event_type] = history_list[-_MAX_HISTORY_LENGTH:]

        return result
