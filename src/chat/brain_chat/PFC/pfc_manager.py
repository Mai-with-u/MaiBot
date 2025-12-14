import time
from typing import Dict, Optional
from src.common.logger import get_module_logger
from .conversation import Conversation
import traceback

logger = get_module_logger("pfc_manager")


class PFCManager:
    """PFC对话管理器，负责管理所有对话实例"""

    # 单例模式
    _instance = None

    # 会话实例管理
    _instances: Dict[str, Conversation] = {}
    _initializing: Dict[str, bool] = {}

    @classmethod
    def get_instance(cls) -> "PFCManager":
        """获取管理器单例

        Returns:
            PFCManager: 管理器实例
        """
        if cls._instance is None:
            cls._instance = PFCManager()
        return cls._instance

    async def get_or_create_conversation(self, stream_id: str, private_name: str) -> Optional[Conversation]:
        """获取或创建对话实例

        Args:
            stream_id: 聊天流ID
            private_name: 私聊名称

        Returns:
            Optional[Conversation]: 对话实例，创建失败则返回None
        """
        # 检查是否已经有实例
        if stream_id in self._initializing and self._initializing[stream_id]:
            logger.debug(f"[私聊][{private_name}]会话实例正在初始化中: {stream_id}")
            return None

        if stream_id in self._instances and self._instances[stream_id].should_continue:
            logger.debug(f"[私聊][{private_name}]使用现有会话实例: {stream_id}")
            return self._instances[stream_id]
        if stream_id in self._instances:
            instance = self._instances[stream_id]
            if (
                hasattr(instance, "ignore_until_timestamp")
                and instance.ignore_until_timestamp
                and time.time() < instance.ignore_until_timestamp
            ):
                logger.debug(f"[私聊][{private_name}]会话实例当前处于忽略状态: {stream_id}")
                # 返回 None 阻止交互。或者可以返回实例但标记它被忽略了喵？
                # 还是返回 None 吧喵。
                return None

            # 检查 should_continue 状态
            if instance.should_continue:
                logger.debug(f"[私聊][{private_name}]使用现有会话实例: {stream_id}")
                return instance
        # else: 实例存在但不应继续
        try:
            # 创建新实例
            logger.info(f"[私聊][{private_name}]创建新的对话实例: {stream_id}")
            self._initializing[stream_id] = True
            # 创建实例
            conversation_instance = Conversation(stream_id, private_name)
            self._instances[stream_id] = conversation_instance

            # 启动实例初始化
            await self._initialize_conversation(conversation_instance)
        except Exception as e:
            logger.error(f"[私聊][{private_name}]创建会话实例失败: {stream_id}, 错误: {e}")
            return None

        return conversation_instance

    async def _initialize_conversation(self, conversation: Conversation):
        """初始化会话实例

        Args:
            conversation: 要初始化的会话实例
        """
        stream_id = conversation.stream_id
        private_name = conversation.private_name

        try:
            logger.info(f"[私聊][{private_name}]开始初始化会话实例: {stream_id}")
            # 启动初始化流程
            await conversation._initialize()

            # 标记初始化完成
            self._initializing[stream_id] = False

            logger.info(f"[私聊][{private_name}]会话实例 {stream_id} 初始化完成")

        except Exception as e:
            logger.error(f"[私聊][{private_name}]管理器初始化会话实例失败: {stream_id}, 错误: {e}")
            logger.error(f"[私聊][{private_name}]{traceback.format_exc()}")
            # 清理失败的初始化

    async def get_conversation(self, stream_id: str) -> Optional[Conversation]:
        """获取已存在的会话实例

        Args:
            stream_id: 聊天流ID

        Returns:
            Optional[Conversation]: 会话实例，不存在则返回None
        """
        return self._instances.get(stream_id)
