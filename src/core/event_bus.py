"""
核心事件总线

面向最终架构的事件系统：
- 内部 handler 直接注册 async callable
- IPC 插件通过 initialize 注入的 bridge callable 桥接
- 不依赖任何插件基类
"""

from typing import Any, Callable, Coroutine, Dict, List, Optional, Protocol, Tuple

import asyncio
import contextlib

from src.common.logger import get_logger

from .types import EventType, MaiMessages

logger = get_logger("event_bus")

# Handler 签名：接收 MaiMessages，返回 (continue, modified_message)
EventHandler = Callable[[Optional[MaiMessages]], Coroutine[Any, Any, Tuple[bool, Optional[MaiMessages]]]]


class IpcEventBridge(Protocol):
    """IPC 插件运行时事件桥接。由 initialize 注入，core 不硬依赖 plugin_runtime。"""

    async def __call__(
        self,
        event_type_value: str,
        message_dict: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]: ...


class EventBus:
    """核心事件总线

    支持两种 handler：
    - 拦截型（intercept=True）：同步顺序执行，可修改消息、可中断流程
    - 非拦截型（intercept=False）：异步并发执行，fire-and-forget

    handler 是纯 async callable，不需要继承任何基类。
    """

    def __init__(self):
        # event_type -> [(handler, name, weight, intercept)]
        self._handlers: Dict[EventType | str, List[_HandlerEntry]] = {}
        self._running_tasks: Dict[str, List[asyncio.Task]] = {}
        self._ipc_bridge: Optional[IpcEventBridge] = None

        # 预注册所有内置事件类型
        for event in EventType:
            self._handlers[event] = []

    def set_ipc_bridge(self, bridge: Optional[IpcEventBridge]) -> None:
        """注入或清除 IPC 插件运行时事件桥接。"""

        self._ipc_bridge = bridge

    def subscribe(
        self,
        event_type: EventType | str,
        handler: EventHandler,
        name: str,
        weight: int = 0,
        intercept: bool = False,
    ) -> None:
        """注册事件 handler

        Args:
            event_type: 事件类型
            handler: async callable，签名 (Optional[MaiMessages]) -> (bool, Optional[MaiMessages])
            name: handler 标识名
            weight: 权重，越大越先执行
            intercept: 是否为拦截型（同步执行，可中断流程）
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []

        entry = _HandlerEntry(handler=handler, name=name, weight=weight, intercept=intercept)
        self._handlers[event_type].append(entry)
        self._handlers[event_type].sort(key=lambda e: e.weight, reverse=True)
        logger.debug(f"注册事件 handler: {name} -> {event_type} (weight={weight}, intercept={intercept})")

    def unsubscribe(self, event_type: EventType | str, name: str) -> bool:
        """取消注册事件 handler"""
        handlers = self._handlers.get(event_type, [])
        for i, entry in enumerate(handlers):
            if entry.name == name:
                del handlers[i]
                logger.debug(f"取消注册事件 handler: {name} <- {event_type}")
                return True
        return False

    async def emit(
        self,
        event_type: EventType | str,
        message: Optional[MaiMessages] = None,
    ) -> Tuple[bool, Optional[MaiMessages]]:
        """触发事件

        按权重顺序执行所有 handler：
        - 拦截型 handler 同步执行，可修改消息和中断流程
        - 非拦截型 handler 异步 fire-and-forget

        Args:
            event_type: 事件类型
            message: 事件消息（可选）

        Returns:
            (continue_flag, modified_message)
            - continue_flag: False 表示某个拦截型 handler 要求中断
            - modified_message: 被拦截型 handler 修改后的消息
        """
        handlers = self._handlers.get(event_type, [])
        if not handlers:
            return True, None

        continue_flag = True
        current_message = message.deepcopy() if message else None
        intercept_handlers: List[_HandlerEntry] = []
        async_handlers: List[_HandlerEntry] = []

        for entry in handlers:
            if entry.intercept:
                intercept_handlers.append(entry)
            else:
                async_handlers.append(entry)

        for entry in intercept_handlers:
            try:
                should_continue, modified = await entry.handler(current_message)
                if modified is not None:
                    current_message = modified
                if not should_continue:
                    continue_flag = False
                    break
            except Exception as e:
                logger.error(f"拦截型 handler {entry.name} 执行异常: {e}", exc_info=True)

        if continue_flag:
            for entry in async_handlers:
                async_message = current_message.deepcopy() if current_message else None
                self._fire_and_forget(entry, event_type, async_message)

        # 桥接到 IPC 插件运行时
        continue_flag, current_message = await self._bridge_to_ipc_runtime(event_type, continue_flag, current_message)

        return continue_flag, current_message

    async def cancel_handler_tasks(self, handler_name: str) -> None:
        """取消某个 handler 的所有运行中任务"""
        tasks = self._running_tasks.pop(handler_name, [])
        if remaining := [t for t in tasks if not t.done()]:
            for t in remaining:
                t.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)
            logger.info(f"已取消 handler {handler_name} 的 {len(remaining)} 个任务")

    # --- 内部方法 ---

    def _fire_and_forget(
        self,
        entry: "_HandlerEntry",
        event_type: EventType | str,
        message: Optional[MaiMessages],
    ) -> None:
        """创建异步任务执行非拦截型 handler。"""
        try:
            task = asyncio.create_task(entry.handler(message))
            task.set_name(entry.name)
            task.add_done_callback(lambda t: self._task_done_callback(t, entry.name))
            self._running_tasks.setdefault(entry.name, []).append(task)
        except Exception as e:
            logger.error(f"创建 handler 任务 {entry.name} 失败: {e}", exc_info=True)

    def _task_done_callback(self, task: asyncio.Task, handler_name: str) -> None:
        """异步任务完成回调"""
        try:
            if task.cancelled():
                return
            if exc := task.exception():
                logger.error(f"handler {handler_name} 异步任务异常: {exc}")
        except Exception:
            pass
        finally:
            task_list = self._running_tasks.get(handler_name, [])
            with contextlib.suppress(ValueError):
                task_list.remove(task)

    async def _bridge_to_ipc_runtime(
        self,
        event_type: EventType | str,
        continue_flag: bool,
        message: Optional[MaiMessages],
    ) -> Tuple[bool, Optional[MaiMessages]]:
        """将事件桥接到 IPC 插件运行时"""
        if not continue_flag:
            return continue_flag, message

        ipc_bridge = self._ipc_bridge
        if ipc_bridge is None:
            return continue_flag, message

        try:
            event_value = event_type.value if isinstance(event_type, EventType) else str(event_type)
            message_dict = message.to_transport_dict() if message else None
            new_continue, modified_dict = await ipc_bridge(
                event_type_value=event_value,
                message_dict=message_dict,
            )
            if not new_continue:
                continue_flag = False
            if modified_dict is not None and message is not None:
                message = self._apply_ipc_message_update(message, modified_dict)
        except Exception as e:
            logger.warning(f"桥接事件到 IPC 运行时失败: {e}")

        return continue_flag, message

    @staticmethod
    def _apply_ipc_message_update(message: MaiMessages, modified_dict: Dict[str, Any]) -> MaiMessages:
        """将 IPC 返回的消息字典回写到当前 MaiMessages。"""
        return message.apply_transport_update(modified_dict)


class _HandlerEntry:
    """内部 handler 条目"""

    __slots__ = ("handler", "name", "weight", "intercept")

    def __init__(self, handler: EventHandler, name: str, weight: int, intercept: bool):
        self.handler = handler
        self.name = name
        self.weight = weight
        self.intercept = intercept


# 全局单例
event_bus = EventBus()
