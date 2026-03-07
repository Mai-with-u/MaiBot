from typing import List, Dict

from src.common.logger import get_logger

logger = get_logger("global_announcement_manager")


class GlobalAnnouncementManager:
    def __init__(self) -> None:
        # 用户禁用的动作，chat_id -> [action_name]
        self._user_disabled_actions: Dict[str, List[str]] = {}
        # 用户禁用的命令，chat_id -> [command_name]
        self._user_disabled_commands: Dict[str, List[str]] = {}
        # 用户禁用的事件处理器，chat_id -> [handler_name]
        self._user_disabled_event_handlers: Dict[str, List[str]] = {}
        # 用户禁用的工具，chat_id -> [tool_name]
        self._user_disabled_tools: Dict[str, List[str]] = {}

    def disable_specific_chat_action(self, chat_id: str, action_name: str) -> bool:
        """禁用特定聊天的某个动作"""
        if chat_id not in self._user_disabled_actions:
            self._user_disabled_actions[chat_id] = []
        if action_name in self._user_disabled_actions[chat_id]:
            logger.warning(f"动作 {action_name} 已经被禁用")
            return False
        self._user_disabled_actions[chat_id].append(action_name)
        return True

    def enable_specific_chat_action(self, chat_id: str, action_name: str) -> bool:
        """启用特定聊天的某个动作"""
        if chat_id in self._user_disabled_actions:
            try:
                self._user_disabled_actions[chat_id].remove(action_name)
                return True
            except ValueError:
                logger.warning(f"动作 {action_name} 不在禁用列表中")
                return False
        return False

    def disable_specific_chat_command(self, chat_id: str, command_name: str) -> bool:
        """禁用特定聊天的某个命令"""
        if chat_id not in self._user_disabled_commands:
            self._user_disabled_commands[chat_id] = []
        if command_name in self._user_disabled_commands[chat_id]:
            logger.warning(f"命令 {command_name} 已经被禁用")
            return False
        self._user_disabled_commands[chat_id].append(command_name)
        return True

    def enable_specific_chat_command(self, chat_id: str, command_name: str) -> bool:
        """启用特定聊天的某个命令"""
        if chat_id in self._user_disabled_commands:
            try:
                self._user_disabled_commands[chat_id].remove(command_name)
                return True
            except ValueError:
                logger.warning(f"命令 {command_name} 不在禁用列表中")
                return False
        return False

    def disable_specific_chat_event_handler(self, chat_id: str, handler_name: str) -> bool:
        """禁用特定聊天的某个事件处理器"""
        if chat_id not in self._user_disabled_event_handlers:
            self._user_disabled_event_handlers[chat_id] = []
        if handler_name in self._user_disabled_event_handlers[chat_id]:
            logger.warning(f"事件处理器 {handler_name} 已经被禁用")
            return False
        self._user_disabled_event_handlers[chat_id].append(handler_name)
        return True

    def enable_specific_chat_event_handler(self, chat_id: str, handler_name: str) -> bool:
        """启用特定聊天的某个事件处理器"""
        if chat_id in self._user_disabled_event_handlers:
            try:
                self._user_disabled_event_handlers[chat_id].remove(handler_name)
                return True
            except ValueError:
                logger.warning(f"事件处理器 {handler_name} 不在禁用列表中")
                return False
        return False

    def disable_specific_chat_tool(self, chat_id: str, tool_name: str) -> bool:
        """禁用特定聊天的某个工具"""
        if chat_id not in self._user_disabled_tools:
            self._user_disabled_tools[chat_id] = []
        if tool_name in self._user_disabled_tools[chat_id]:
            logger.warning(f"工具 {tool_name} 已经被禁用")
            return False
        self._user_disabled_tools[chat_id].append(tool_name)
        return True

    def enable_specific_chat_tool(self, chat_id: str, tool_name: str) -> bool:
        """启用特定聊天的某个工具"""
        if chat_id in self._user_disabled_tools:
            try:
                self._user_disabled_tools[chat_id].remove(tool_name)
                return True
            except ValueError:
                logger.warning(f"工具 {tool_name} 不在禁用列表中")
                return False
        return False

    def get_disabled_chat_actions(self, chat_id: str) -> List[str]:
        """获取特定聊天禁用的所有动作"""
        return self._user_disabled_actions.get(chat_id, []).copy()

    def get_disabled_chat_commands(self, chat_id: str) -> List[str]:
        """获取特定聊天禁用的所有命令"""
        return self._user_disabled_commands.get(chat_id, []).copy()

    def get_disabled_chat_event_handlers(self, chat_id: str) -> List[str]:
        """获取特定聊天禁用的所有事件处理器"""
        return self._user_disabled_event_handlers.get(chat_id, []).copy()

    def get_disabled_chat_tools(self, chat_id: str) -> List[str]:
        """获取特定聊天禁用的所有工具"""
        return self._user_disabled_tools.get(chat_id, []).copy()


global_announcement_manager = GlobalAnnouncementManager()
