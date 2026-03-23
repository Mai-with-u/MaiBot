from typing import Dict, Optional, Tuple

from src.chat.message_receive.chat_manager import BotChatSession
from src.chat.message_receive.message import SessionMessage
from src.common.logger import get_logger
from src.core.types import ActionInfo
from src.plugin_runtime.component_query import ActionExecutor, component_query_service

logger = get_logger("action_manager")


class ActionHandle:
    """Action 执行句柄

    不依赖任何插件基类，内部持有 executor (async callable) 和绑定参数。
    brain_chat 调用 ``await handle.execute()`` 即可。
    """

    def __init__(self, executor: ActionExecutor, **kwargs):
        self._executor = executor
        self._kwargs = kwargs

    async def execute(self) -> Tuple[bool, str]:
        return await self._executor(**self._kwargs)


class ActionManager:
    """
    动作管理器，用于管理各种类型的动作

    使用插件运行时统一查询服务的 executor-based 模式。
    """

    def __init__(self):
        """初始化动作管理器"""

        # 当前正在使用的动作集合，默认加载默认动作
        self._using_actions: Dict[str, ActionInfo] = {}

        # 初始化时将默认动作加载到使用中的动作
        self._using_actions = component_query_service.get_default_actions()

    # === 执行Action方法 ===

    def create_action(
        self,
        action_name: str,
        action_data: dict,
        action_reasoning: str,
        cycle_timers: dict,
        thinking_id: str,
        chat_stream: BotChatSession,
        log_prefix: str,
        shutting_down: bool = False,
        action_message: Optional[SessionMessage] = None,
    ) -> Optional[ActionHandle]:
        """
        创建动作执行句柄

        Args:
            action_name: 动作名称
            action_data: 动作数据
            action_reasoning: 执行理由
            cycle_timers: 计时器字典
            thinking_id: 思考ID
            chat_stream: 聊天流
            log_prefix: 日志前缀
            shutting_down: 是否正在关闭
            action_message: 动作消息记录

        Returns:
            Optional[ActionHandle]: 执行句柄，如果动作未注册则返回 None
        """
        try:
            executor = component_query_service.get_action_executor(action_name)
            if not executor:
                logger.warning(f"{log_prefix} 未找到Action组件: {action_name}")
                return None

            info = component_query_service.get_action_info(action_name)
            if not info:
                logger.warning(f"{log_prefix} 未找到Action组件信息: {action_name}")
                return None

            plugin_config = component_query_service.get_plugin_config(info.plugin_name) or {}

            handle = ActionHandle(
                executor,
                action_data=action_data,
                action_reasoning=action_reasoning,
                cycle_timers=cycle_timers,
                thinking_id=thinking_id,
                chat_stream=chat_stream,
                log_prefix=log_prefix,
                shutting_down=shutting_down,
                plugin_config=plugin_config,
                action_message=action_message,
            )

            logger.debug(f"创建Action执行句柄成功: {action_name}")
            return handle

        except Exception as e:
            logger.error(f"创建Action执行句柄失败 {action_name}: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return None

    def get_using_actions(self) -> Dict[str, ActionInfo]:
        """获取当前正在使用的动作集合"""
        return self._using_actions.copy()

    # === Modify相关方法 ===
    def remove_action_from_using(self, action_name: str) -> bool:
        """
        从当前使用的动作集中移除指定动作

        Args:
            action_name: 动作名称

        Returns:
            bool: 移除是否成功
        """
        if action_name not in self._using_actions:
            logger.warning(f"移除失败: 动作 {action_name} 不在当前使用的动作集中")
            return False

        del self._using_actions[action_name]
        logger.debug(f"已从使用集中移除动作 {action_name}")
        return True

    def restore_actions(self) -> None:
        """恢复到默认动作集"""
        actions_to_restore = list(self._using_actions.keys())
        self._using_actions = component_query_service.get_default_actions()
        logger.debug(f"恢复动作集: 从 {actions_to_restore} 恢复到默认动作集 {list(self._using_actions.keys())}")
