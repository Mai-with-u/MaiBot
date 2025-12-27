import random
import time
from typing import List, Dict, TYPE_CHECKING, Tuple

from src.common.logger import get_logger
from src.config.config import global_config
from src.chat.message_receive.chat_stream import get_chat_manager, ChatMessageContext
from src.chat.planner_actions.action_manager import ActionManager
from src.chat.utils.chat_message_builder import get_raw_msg_before_timestamp_with_chat, build_readable_messages
from src.plugin_system.base.component_types import ActionInfo, ActionActivationType
from src.plugin_system.core.global_announcement_manager import global_announcement_manager

if TYPE_CHECKING:
    from src.chat.message_receive.chat_stream import ChatStream

logger = get_logger("action_manager")


class ActionModifier:
    """动作处理器

    用于处理Observation对象和根据激活类型处理actions。
    集成了原有的modify_actions功能和新的激活类型处理功能。
    支持并行判定和智能缓存优化。
    """

    def __init__(self, action_manager: ActionManager, chat_id: str):
        """初始化动作处理器"""
        self.chat_id = chat_id
        self.chat_stream: ChatStream = get_chat_manager().get_stream(self.chat_id)  # type: ignore
        self.log_prefix = f"[{get_chat_manager().get_stream_name(self.chat_id) or self.chat_id}]"

        self.action_manager = action_manager

    async def modify_actions(
        self,
        message_content: str = "",
    ):  # sourcery skip: use-named-expression
        """
        动作修改流程，整合传统观察处理和新的激活类型判定

        这个方法处理完整的动作管理流程：
        1. 基于观察的传统动作修改（循环历史分析、类型匹配等）
        2. 基于激活类型的智能动作判定，最终确定可用动作集

        处理后，ActionManager 将包含最终的可用动作集，供规划器直接使用
        """
        logger.debug(f"{self.log_prefix}开始完整动作修改流程")

        removals_s1: List[Tuple[str, str]] = []
        removals_s2: List[Tuple[str, str]] = []
        # removals_s3: List[Tuple[str, str]] = []

        self.action_manager.restore_actions()
        all_actions = self.action_manager.get_using_actions()

        message_list_before_now_half = get_raw_msg_before_timestamp_with_chat(
            chat_id=self.chat_stream.stream_id,
            timestamp=time.time(),
            limit=min(int(global_config.chat.max_context_size * 0.33), 10),
            filter_intercept_message_level=1,
        )

        chat_content = build_readable_messages(
            message_list_before_now_half,
            replace_bot_name=True,
            timestamp_mode="relative",
            read_mark=0.0,
            show_actions=True,
        )

        if message_content:
            chat_content = chat_content + "\n" + f"现在，最新的消息是：{message_content}"

        # === 第一阶段：去除用户自行禁用的 ===
        disabled_actions = global_announcement_manager.get_disabled_chat_actions(self.chat_id)
        if disabled_actions:
            for disabled_action_name in disabled_actions:
                if disabled_action_name in all_actions:
                    removals_s1.append((disabled_action_name, "用户自行禁用"))
                    self.action_manager.remove_action_from_using(disabled_action_name)
                    logger.debug(f"{self.log_prefix}阶段一移除动作: {disabled_action_name}，原因: 用户自行禁用")

        # === 第二阶段：检查动作的关联类型 ===
        chat_context = self.chat_stream.context
        type_mismatched_actions = self._check_action_associated_types(all_actions, chat_context)

        if type_mismatched_actions:
            removals_s2.extend(type_mismatched_actions)

        # 应用第二阶段的移除
        for action_name, reason in removals_s2:
            self.action_manager.remove_action_from_using(action_name)
            logger.debug(f"{self.log_prefix}阶段二移除动作: {action_name}，原因: {reason}")

        # === 第三阶段：激活类型判定 ===
        # if chat_content is not None:
        # logger.debug(f"{self.log_prefix}开始激活类型判定阶段")

        # 获取当前使用的动作集（经过第一阶段处理）
        # current_using_actions = self.action_manager.get_using_actions()

        # 获取因激活类型判定而需要移除的动作
        # removals_s3 = await self._get_deactivated_actions_by_type(
        # current_using_actions,
        # chat_content,
        # )

        # 应用第三阶段的移除
        # for action_name, reason in removals_s3:
        # self.action_manager.remove_action_from_using(action_name)
        # logger.debug(f"{self.log_prefix}阶段三移除动作: {action_name}，原因: {reason}")

        # === 统一日志记录 ===
        all_removals = removals_s1 + removals_s2
        removals_summary: str = ""
        if all_removals:
            removals_summary = " | ".join([f"{name}({reason})" for name, reason in all_removals])

        available_actions = list(self.action_manager.get_using_actions().keys())
        available_actions_text = "、".join(available_actions) if available_actions else "无"
        logger.debug(f"{self.log_prefix} 当前可用动作: {available_actions_text}||移除: {removals_summary}")

    def _check_action_associated_types(self, all_actions: Dict[str, ActionInfo], chat_context: ChatMessageContext):
        type_mismatched_actions: List[Tuple[str, str]] = []
        for action_name, action_info in all_actions.items():
            if action_info.associated_types and not chat_context.check_types(action_info.associated_types):
                associated_types_str = ", ".join(action_info.associated_types)
                reason = f"适配器不支持（需要: {associated_types_str}）"
                type_mismatched_actions.append((action_name, reason))
                logger.debug(f"{self.log_prefix}决定移除动作: {action_name}，原因: {reason}")
        return type_mismatched_actions

    async def _get_deactivated_actions_by_type(
        self,
        actions_with_info: Dict[str, ActionInfo],
        chat_content: str = "",
    ) -> List[tuple[str, str]]:
        """
        根据激活类型过滤，返回需要停用的动作列表及原因

        Args:
            actions_with_info: 带完整信息的动作字典
            chat_content: 聊天内容

        Returns:
            List[Tuple[str, str]]: 需要停用的 (action_name, reason) 元组列表
        """
        deactivated_actions = []

        actions_to_check = list(actions_with_info.items())
        random.shuffle(actions_to_check)

        for action_name, action_info in actions_to_check:
            activation_type = action_info.activation_type or action_info.focus_activation_type

            if activation_type == ActionActivationType.ALWAYS:
                continue  # 总是激活，无需处理

            elif activation_type == ActionActivationType.RANDOM:
                probability = action_info.random_activation_probability
                if random.random() >= probability:
                    reason = f"RANDOM类型未触发（概率{probability}）"
                    deactivated_actions.append((action_name, reason))
                    logger.debug(f"{self.log_prefix}未激活动作: {action_name}，原因: {reason}")

            elif activation_type == ActionActivationType.KEYWORD:
                if not self._check_keyword_activation(action_name, action_info, chat_content):
                    keywords = action_info.activation_keywords
                    reason = f"关键词未匹配（关键词: {keywords}）"
                    deactivated_actions.append((action_name, reason))
                    logger.debug(f"{self.log_prefix}未激活动作: {action_name}，原因: {reason}")

            elif activation_type == ActionActivationType.NEVER:
                reason = "激活类型为never"
                deactivated_actions.append((action_name, reason))
                logger.debug(f"{self.log_prefix}未激活动作: {action_name}，原因: 激活类型为never")

            else:
                logger.warning(f"{self.log_prefix}未知的激活类型: {activation_type}，跳过处理")

        return deactivated_actions

    def _check_keyword_activation(
        self,
        action_name: str,
        action_info: ActionInfo,
        chat_content: str = "",
    ) -> bool:
        """
        检查是否匹配关键词触发条件

        Args:
            action_name: 动作名称
            action_info: 动作信息
            observed_messages_str: 观察到的聊天消息
            chat_context: 聊天上下文
            extra_context: 额外上下文

        Returns:
            bool: 是否应该激活此action
        """

        activation_keywords = action_info.activation_keywords
        case_sensitive = action_info.keyword_case_sensitive

        if not activation_keywords:
            logger.warning(f"{self.log_prefix}动作 {action_name} 设置为关键词触发但未配置关键词")
            return False

        # 构建检索文本
        search_text = ""
        if chat_content:
            search_text += chat_content
        # if chat_context:
        # search_text += f" {chat_context}"
        # if extra_context:
        # search_text += f" {extra_context}"

        # 如果不区分大小写，转换为小写
        if not case_sensitive:
            search_text = search_text.lower()

        # 检查每个关键词
        matched_keywords = []
        for keyword in activation_keywords:
            check_keyword = keyword if case_sensitive else keyword.lower()
            if check_keyword in search_text:
                matched_keywords.append(keyword)

        if matched_keywords:
            logger.debug(f"{self.log_prefix}动作 {action_name} 匹配到关键词: {matched_keywords}")
            return True
        else:
            logger.debug(f"{self.log_prefix}动作 {action_name} 未匹配到任何关键词: {activation_keywords}")
            return False
