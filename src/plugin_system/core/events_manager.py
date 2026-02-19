import asyncio
import contextlib
from typing import List, Dict, Optional, Type, Tuple, TYPE_CHECKING

from src.chat.message_receive.message import MessageRecv, MessageSending
from src.chat.message_receive.chat_stream import get_chat_manager
from src.common.logger import get_logger
from src.plugin_system.base.component_types import EventType, EventHandlerInfo, MaiMessages, CustomEventHandlerResult
from src.plugin_system.base.base_events_handler import BaseEventHandler
from .global_announcement_manager import global_announcement_manager

if TYPE_CHECKING:
    from src.common.data_models.llm_data_model import LLMGenerationDataModel

logger = get_logger("events_manager")


class EventsManager:
    def __init__(self):
        # 有权重的 events 订阅者注册表
        self._events_subscribers: Dict[EventType | str, List[BaseEventHandler]] = {}
        self._handler_mapping: Dict[str, Type[BaseEventHandler]] = {}  # 事件处理器映射表
        self._handler_tasks: Dict[str, List[asyncio.Task]] = {}  # 事件处理器正在处理的任务
        self._events_result_history: Dict[EventType | str, List[CustomEventHandlerResult]] = {}  # 事件的结果历史记录
        self._history_enable_map: Dict[EventType | str, bool] = {}  # 是否启用历史记录的映射表，同时作为events注册表

        # 事件注册（同时作为注册样例）
        for event in EventType:
            self.register_event(event, enable_history_result=False)

    def register_event(self, event_type: EventType | str, enable_history_result: bool = False):
        if event_type in self._events_subscribers:
            raise ValueError(f"事件类型 {event_type} 已存在")
        self._events_subscribers[event_type] = []
        self._history_enable_map[event_type] = enable_history_result
        if enable_history_result:
            self._events_result_history[event_type] = []

    def register_event_subscriber(self, handler_info: EventHandlerInfo, handler_class: Type[BaseEventHandler]) -> bool:
        """注册事件处理器

        Args:
            handler_info (EventHandlerInfo): 事件处理器信息
            handler_class (Type[BaseEventHandler]): 事件处理器类

        Returns:
            bool: 是否注册成功
        """
        if not issubclass(handler_class, BaseEventHandler):
            logger.error(f"类 {handler_class.__name__} 不是 BaseEventHandler 的子类")
            return False

        handler_name = handler_info.name

        if handler_name in self._handler_mapping:
            logger.warning(f"事件处理器 {handler_name} 已存在，跳过注册")
            return False

        if handler_info.event_type not in self._history_enable_map:
            logger.error(f"事件类型 {handler_info.event_type} 未注册，无法为其注册处理器 {handler_name}")
            return False

        self._handler_mapping[handler_name] = handler_class
        return self._insert_event_handler(handler_class, handler_info)

    async def handle_mai_events(
        self,
        event_type: EventType,
        message: Optional[MessageRecv | MessageSending] = None,
        llm_prompt: Optional[str] = None,
        llm_response: Optional["LLMGenerationDataModel"] = None,
        stream_id: Optional[str] = None,
        action_usage: Optional[List[str]] = None,
    ) -> Tuple[bool, Optional[MaiMessages]]:
        """
        处理所有事件，根据事件类型分发给订阅的处理器。
        """
        from src.plugin_system.core import component_registry

        continue_flag = True

        # 1. 准备消息
        transformed_message = self._prepare_message(
            event_type, message, llm_prompt, llm_response, stream_id, action_usage
        )
        if transformed_message:
            transformed_message = transformed_message.deepcopy()

        # 2. 获取并遍历处理器
        handlers = self._events_subscribers.get(event_type, [])
        if not handlers:
            return True, None

        current_stream_id = transformed_message.stream_id if transformed_message else None
        modified_message: Optional[MaiMessages] = None
        for handler in handlers:
            # 3. 前置检查和配置加载
            if (
                current_stream_id
                and handler.handler_name
                in global_announcement_manager.get_disabled_chat_event_handlers(current_stream_id)
            ):
                continue

            # 统一加载插件配置
            plugin_config = component_registry.get_plugin_config(handler.plugin_name) or {}
            handler.set_plugin_config(plugin_config)

            # 4. 根据类型分发任务
            if (
                handler.intercept_message or event_type == EventType.ON_STOP
            ):  # 让ON_STOP的所有事件处理器都发挥作用，防止还没执行即被取消
                # 阻塞执行，并更新 continue_flag
                should_continue, modified_message = await self._dispatch_intercepting_handler_task(
                    handler, event_type, modified_message or transformed_message
                )
                continue_flag = continue_flag and should_continue
            else:
                # 异步执行，不阻塞
                self._dispatch_handler_task(handler, event_type, transformed_message)

        return continue_flag, modified_message

    async def cancel_handler_tasks(self, handler_name: str) -> None:
        tasks_to_be_cancelled = self._handler_tasks.get(handler_name, [])
        if remaining_tasks := [task for task in tasks_to_be_cancelled if not task.done()]:
            for task in remaining_tasks:
                task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(*remaining_tasks, return_exceptions=True), timeout=5)
                logger.info(f"已取消事件处理器 {handler_name} 的所有任务")
            except asyncio.TimeoutError:
                logger.warning(f"取消事件处理器 {handler_name} 的任务超时，开始强制取消")
            except Exception as e:
                logger.error(f"取消事件处理器 {handler_name} 的任务时发生异常: {e}")
        if handler_name in self._handler_tasks:
            del self._handler_tasks[handler_name]

    async def unregister_event_subscriber(self, handler_name: str) -> bool:
        """取消注册事件处理器"""
        if handler_name not in self._handler_mapping:
            logger.warning(f"事件处理器 {handler_name} 不存在，无法取消注册")
            return False

        await self.cancel_handler_tasks(handler_name)

        handler_class = self._handler_mapping.pop(handler_name)
        if not self._remove_event_handler_instance(handler_class):
            return False

        logger.info(f"事件处理器 {handler_name} 已成功取消注册")
        return True

    async def get_event_result_history(self, event_type: EventType | str) -> List[CustomEventHandlerResult]:
        """获取事件的结果历史记录"""
        if event_type == EventType.UNKNOWN:
            raise ValueError("未知事件类型")
        if event_type not in self._history_enable_map:
            raise ValueError(f"事件类型 {event_type} 未注册")
        if not self._history_enable_map[event_type]:
            raise ValueError(f"事件类型 {event_type} 的历史记录未启用")

        return self._events_result_history[event_type]

    async def clear_event_result_history(self, event_type: EventType | str) -> None:
        """清空事件的结果历史记录"""
        if event_type == EventType.UNKNOWN:
            raise ValueError("未知事件类型")
        if event_type not in self._history_enable_map:
            raise ValueError(f"事件类型 {event_type} 未注册")
        if not self._history_enable_map[event_type]:
            raise ValueError(f"事件类型 {event_type} 的历史记录未启用")

        self._events_result_history[event_type] = []

    def _insert_event_handler(self, handler_class: Type[BaseEventHandler], handler_info: EventHandlerInfo) -> bool:
        """插入事件处理器到对应的事件类型列表中并设置其插件配置"""
        if handler_class.event_type == EventType.UNKNOWN:
            logger.error(f"事件处理器 {handler_class.__name__} 的事件类型未知，无法注册")
            return False
        if handler_class.event_type not in self._events_subscribers:
            self._events_subscribers[handler_class.event_type] = []
        handler_instance = handler_class()
        handler_instance.set_plugin_name(handler_info.plugin_name or "unknown")
        self._events_subscribers[handler_class.event_type].append(handler_instance)
        self._events_subscribers[handler_class.event_type].sort(key=lambda x: x.weight, reverse=True)

        return True

    def _remove_event_handler_instance(self, handler_class: Type[BaseEventHandler]) -> bool:
        """从事件类型列表中移除事件处理器"""
        display_handler_name = handler_class.handler_name or handler_class.__name__
        if handler_class.event_type == EventType.UNKNOWN:
            logger.warning(f"事件处理器 {display_handler_name} 的事件类型未知，不存在于处理器列表中")
            return False

        handlers = self._events_subscribers[handler_class.event_type]
        for i, handler in enumerate(handlers):
            if isinstance(handler, handler_class):
                del handlers[i]
                logger.debug(f"事件处理器 {display_handler_name} 已移除")
                return True

        logger.warning(f"未找到事件处理器 {display_handler_name}，无法移除")
        return False

    def _transform_event_message(
        self,
        message: MessageRecv | MessageSending,
        llm_prompt: Optional[str] = None,
        llm_response: Optional["LLMGenerationDataModel"] = None,
    ) -> MaiMessages:
        """转换事件消息格式"""
        # 直接赋值部分内容
        transformed_message = MaiMessages(
            llm_prompt=llm_prompt,
            llm_response_content=llm_response.content if llm_response else None,
            llm_response_reasoning=llm_response.reasoning if llm_response else None,
            llm_response_model=llm_response.model if llm_response else None,
            llm_response_tool_call=llm_response.tool_calls if llm_response else None,
            raw_message=message.raw_message,
            additional_data=message.message_info.additional_config or {},
        )

        # 消息段处理
        if message.message_segment.type == "seglist":
            transformed_message.message_segments = list(message.message_segment.data)  # type: ignore
        else:
            transformed_message.message_segments = [message.message_segment]

        # stream_id 处理
        if hasattr(message, "chat_stream") and message.chat_stream:
            transformed_message.stream_id = message.chat_stream.stream_id

        # 处理后文本
        transformed_message.plain_text = message.processed_plain_text

        # 基本信息
        if hasattr(message, "message_info") and message.message_info:
            if message.message_info.platform:
                transformed_message.message_base_info["platform"] = message.message_info.platform
            if message.message_info.group_info:
                transformed_message.is_group_message = True
                transformed_message.message_base_info.update(
                    {
                        "group_id": message.message_info.group_info.group_id,
                        "group_name": message.message_info.group_info.group_name,
                    }
                )
            if message.message_info.user_info:
                if not transformed_message.is_group_message:
                    transformed_message.is_private_message = True
                transformed_message.message_base_info.update(
                    {
                        "user_id": message.message_info.user_info.user_id,
                        "user_cardname": message.message_info.user_info.user_cardname,  # 用户群昵称
                        "user_nickname": message.message_info.user_info.user_nickname,  # 用户昵称（用户名）
                    }
                )

        return transformed_message

    def _build_message_from_stream(
        self, stream_id: str, llm_prompt: Optional[str] = None, llm_response: Optional["LLMGenerationDataModel"] = None
    ) -> MaiMessages:
        """从流ID构建消息"""
        chat_stream = get_chat_manager().get_stream(stream_id)
        assert chat_stream, f"未找到流ID为 {stream_id} 的聊天流"
        message = chat_stream.context.get_last_message()
        return self._transform_event_message(message, llm_prompt, llm_response)

    def _transform_event_without_message(
        self,
        stream_id: str,
        llm_prompt: Optional[str] = None,
        llm_response: Optional["LLMGenerationDataModel"] = None,
        action_usage: Optional[List[str]] = None,
    ) -> MaiMessages:
        """没有message对象时进行转换"""
        chat_stream = get_chat_manager().get_stream(stream_id)
        assert chat_stream, f"未找到流ID为 {stream_id} 的聊天流"
        return MaiMessages(
            stream_id=stream_id,
            llm_prompt=llm_prompt,
            llm_response_content=(llm_response.content if llm_response else None),
            llm_response_reasoning=(llm_response.reasoning if llm_response else None),
            llm_response_model=(llm_response.model if llm_response else None),
            llm_response_tool_call=(llm_response.tool_calls if llm_response else None),
            is_group_message=(not (not chat_stream.group_info)),
            is_private_message=(not chat_stream.group_info),
            action_usage=action_usage,
            additional_data={"response_is_processed": True},
        )

    def _prepare_message(
        self,
        event_type: EventType,
        message: Optional[MessageRecv | MessageSending] = None,
        llm_prompt: Optional[str] = None,
        llm_response: Optional["LLMGenerationDataModel"] = None,
        stream_id: Optional[str] = None,
        action_usage: Optional[List[str]] = None,
    ) -> Optional[MaiMessages]:
        """根据事件类型和输入，准备和转换消息对象。"""
        if message:
            return self._transform_event_message(message, llm_prompt, llm_response)

        if event_type not in [EventType.ON_START, EventType.ON_STOP]:
            assert stream_id, "如果没有消息，必须为非启动/关闭事件提供流ID"
            if event_type in [EventType.ON_MESSAGE, EventType.ON_PLAN, EventType.POST_LLM, EventType.AFTER_LLM]:
                return self._build_message_from_stream(stream_id, llm_prompt, llm_response)
            else:
                return self._transform_event_without_message(stream_id, llm_prompt, llm_response, action_usage)

        return None  # ON_START, ON_STOP事件没有消息体

    def _dispatch_handler_task(
        self, handler: BaseEventHandler, event_type: EventType | str, message: Optional[MaiMessages] = None
    ):
        """分发一个非阻塞（异步）的事件处理任务。"""
        if event_type == EventType.UNKNOWN:
            raise ValueError("未知事件类型")
        try:
            task = asyncio.create_task(handler.execute(message))

            task_name = f"{handler.plugin_name}-{handler.handler_name}"
            task.set_name(task_name)
            handler_name = handler.handler_name
            task.add_done_callback(lambda t: self._task_done_callback(t, event_type, handler_name))

            self._handler_tasks.setdefault(handler_name, []).append(task)
        except Exception as e:
            logger.error(f"创建事件处理器任务 {handler.handler_name} 时发生异常: {e}", exc_info=True)

    async def _dispatch_intercepting_handler_task(
        self, handler: BaseEventHandler, event_type: EventType | str, message: Optional[MaiMessages] = None
    ) -> Tuple[bool, Optional[MaiMessages]]:
        """分发并等待一个阻塞（同步）的事件处理器，返回是否应继续处理。"""
        if event_type == EventType.UNKNOWN:
            raise ValueError("未知事件类型")
        if event_type not in self._history_enable_map:
            raise ValueError(f"事件类型 {event_type} 未注册")
        try:
            result = await handler.execute(message)

            expected_fields = ["success", "continue_processing", "return_message", "custom_result", "modified_message"]

            if not isinstance(result, tuple) or len(result) != 5:
                if isinstance(result, tuple):
                    annotated = ", ".join(f"{name}={val!r}" for name, val in zip(expected_fields, result, strict=False))
                    actual_desc = f"{len(result)} 个元素 ({annotated})"
                else:
                    actual_desc = f"非 tuple 类型: {type(result)}"

                logger.error(
                    f"[{self.__class__.__name__}] EventHandler {handler.handler_name} 返回值不符合预期:\n"
                    f"  模块来源: {handler.__class__.__module__}.{handler.__class__.__name__}\n"
                    f"  期望: 5 个元素 ({', '.join(expected_fields)})\n"
                    f"  实际: {actual_desc}"
                )
                return True, None

            success, continue_processing, return_message, custom_result, modified_message = result

            if not success:
                logger.error(f"EventHandler {handler.handler_name} 执行失败: {return_message}")
            else:
                logger.debug(f"EventHandler {handler.handler_name} 执行成功: {return_message}")

            if self._history_enable_map[event_type] and custom_result:
                self._events_result_history[event_type].append(custom_result)

            return continue_processing, modified_message

        except KeyError:
            logger.error(f"事件 {event_type} 注册的历史记录启用情况与实际不符合")
            return True, None
        except Exception as e:
            logger.error(f"EventHandler {handler.handler_name} 发生异常: {e}", exc_info=True)
            return True, None  # 发生异常时默认不中断其他处理

    def _task_done_callback(
        self,
        task: asyncio.Task[Tuple[bool, bool, str | None, CustomEventHandlerResult | None, MaiMessages | None]],
        event_type: EventType | str,
        handler_name: str,
    ):
        """任务完成回调"""
        task_name = task.get_name() or "Unknown Task"
        if event_type == EventType.UNKNOWN:
            raise ValueError("未知事件类型")
        if event_type not in self._history_enable_map:
            raise ValueError(f"事件类型 {event_type} 未注册")
        try:
            success, _, result, custom_result, _ = task.result()  # 忽略是否继续的标志和消息的修改，因为消息本身未被拦截
            if success:
                logger.debug(f"事件处理任务 {task_name} 已成功完成: {result}")
            else:
                logger.error(f"事件处理任务 {task_name} 执行失败: {result}")

            if self._history_enable_map[event_type] and custom_result:
                self._events_result_history[event_type].append(custom_result)
        except asyncio.CancelledError:
            pass
        except KeyError:
            logger.error(f"事件 {event_type} 注册的历史记录启用情况与实际不符合")
        except Exception as e:
            logger.error(f"事件处理任务 {task_name} 发生异常: {e}")
        finally:
            with contextlib.suppress(ValueError, KeyError):
                tasks = self._handler_tasks[handler_name]
                tasks.remove(task)
                if not tasks:
                    del self._handler_tasks[handler_name]


events_manager = EventsManager()
