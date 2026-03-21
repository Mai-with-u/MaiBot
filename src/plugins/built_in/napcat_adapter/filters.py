"""NapCat 入站消息过滤。"""

from typing import Any, Set

from napcat_adapter.config import NapCatChatConfig


class NapCatChatFilter:
    """NapCat 聊天名单过滤器。"""

    def __init__(self, logger: Any) -> None:
        """初始化聊天名单过滤器。

        Args:
            logger: 插件日志对象。
        """
        self._logger = logger

    def is_inbound_chat_allowed(
        self,
        sender_user_id: str,
        group_id: str,
        chat_config: NapCatChatConfig,
    ) -> bool:
        """检查入站消息是否通过聊天名单过滤。

        Args:
            sender_user_id: 发送者用户 ID。
            group_id: 群聊 ID；私聊时为空字符串。
            chat_config: 当前生效的聊天配置。

        Returns:
            bool: 若消息允许继续进入 Host，则返回 ``True``。
        """
        if sender_user_id in chat_config.ban_user_id:
            self._logger.warning(f"NapCat 用户 {sender_user_id} 在全局禁止名单中，消息被丢弃")
            return False

        if group_id:
            if not self._is_id_allowed_by_list_policy(group_id, chat_config.group_list_type, chat_config.group_list):
                self._logger.warning(f"NapCat 群聊 {group_id} 未通过聊天名单过滤，消息被丢弃")
                return False
            return True

        if not self._is_id_allowed_by_list_policy(
            sender_user_id,
            chat_config.private_list_type,
            chat_config.private_list,
        ):
            self._logger.warning(f"NapCat 私聊用户 {sender_user_id} 未通过聊天名单过滤，消息被丢弃")
            return False
        return True

    @staticmethod
    def _is_id_allowed_by_list_policy(target_id: str, list_type: str, configured_ids: Set[str]) -> bool:
        """根据白名单或黑名单规则判断目标 ID 是否允许通过。

        Args:
            target_id: 待检查的目标 ID。
            list_type: 名单模式，仅支持 ``whitelist`` 或 ``blacklist``。
            configured_ids: 配置中的 ID 集合。

        Returns:
            bool: 若目标 ID 允许通过，则返回 ``True``。
        """
        if list_type == "whitelist":
            return target_id in configured_ids
        return target_id not in configured_ids
