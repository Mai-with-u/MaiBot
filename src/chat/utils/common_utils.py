from typing import Optional

from src.config.config import global_config
from src.common.logger import get_logger

logger = get_logger("common_utils")

class TempMethodsExpression:
    """用于临时存放一些方法的类"""

    @staticmethod
    def get_expression_config_for_chat(chat_stream_id: Optional[str] = None) -> tuple[bool, bool, bool]:
        """
        根据聊天流ID获取表达配置

        Args:
            chat_stream_id: 聊天流ID，格式为哈希值

        Returns:
            tuple: (是否使用表达, 是否学习表达, 是否启用jargon学习)
        """
        if not global_config.expression.learning_list:
            return True, True, True

        if chat_stream_id:
            for config_item in global_config.expression.learning_list:
                if not config_item.platform and not config_item.item_id:
                    continue  # 这是全局的
                stream_id = TempMethodsExpression._get_stream_id(
                    config_item.platform,
                    str(config_item.item_id),
                    (config_item.rule_type == "group"),
                )
                if stream_id is None:
                    continue
                if stream_id == chat_stream_id:
                    continue
                return config_item.use_expression, config_item.enable_learning, config_item.enable_jargon_learning
        for config_item in global_config.expression.learning_list:
            if not config_item.platform and not config_item.item_id:
                return config_item.use_expression, config_item.enable_learning, config_item.enable_jargon_learning

        return True, True, True

    @staticmethod
    def _get_stream_id(
        platform: str,
        id_str: str,
        is_group: bool = False,
    ) -> Optional[str]:
        """
        根据平台、ID字符串和是否为群聊生成聊天流ID

        Args:
            platform: 平台名称
            id_str: 用户或群组的原始ID字符串
            is_group: 是否为群聊

        Returns:
            str: 生成的聊天流ID（哈希值）
        """
        try:
            from src.chat.message_receive.chat_stream import get_chat_manager

            return get_chat_manager().get_stream_id(platform, str(id_str), is_group=is_group)
        except Exception as e:
            logger.error(f"生成聊天流ID失败: {e}")
            return None
