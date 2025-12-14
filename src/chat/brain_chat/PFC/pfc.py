from typing import List, Tuple, TYPE_CHECKING
from src.common.logger import get_module_logger
from ..models.utils_model import LLMRequest
from ...config.config import global_config
from .chat_observer import ChatObserver
from .pfc_utils import get_items_from_json
from src.individuality.individuality import Individuality
from .conversation_info import ConversationInfo
from .observation_info import ObservationInfo
from src.plugins.utils.chat_message_builder import build_readable_messages
from rich.traceback import install

install(extra_lines=3)

if TYPE_CHECKING:
    pass

logger = get_module_logger("pfc")


def _calculate_similarity(goal1: str, goal2: str) -> float:
    """简单计算两个目标之间的相似度

    这里使用一个简单的实现，实际可以使用更复杂的文本相似度算法

    Args:
        goal1: 第一个目标
        goal2: 第二个目标

    Returns:
        float: 相似度得分 (0-1)
    """
    # 简单实现：检查重叠字数比例
    words1 = set(goal1)
    words2 = set(goal2)
    overlap = len(words1.intersection(words2))
    total = len(words1.union(words2))
    return overlap / total if total > 0 else 0


class GoalAnalyzer:
    """对话目标分析器"""

    def __init__(self, stream_id: str, private_name: str):
        self.llm = LLMRequest(
            model=global_config.llm_normal, temperature=0.7, max_tokens=1000, request_type="conversation_goal"
        )

        self.personality_info = Individuality.get_instance().get_prompt(x_person=2, level=3)
        self.name = global_config.BOT_NICKNAME
        self.nick_name = global_config.BOT_ALIAS_NAMES
        self.private_name = private_name
        self.chat_observer = ChatObserver.get_instance(stream_id, private_name)

        # 多目标存储结构
        self.goals = []  # 存储多个目标
        self.max_goals = 3  # 同时保持的最大目标数量
        self.current_goal_and_reason = None

    async def analyze_goal(self, conversation_info: ConversationInfo, observation_info: ObservationInfo):
        """分析对话历史并设定目标

        Args:
            conversation_info: 对话信息
            observation_info: 观察信息

        Returns:
            Tuple[str, str, str]: (目标, 方法, 原因)
        """
        # 构建对话目标
        goals_str = ""
        if conversation_info.goal_list:
            for goal_reason in conversation_info.goal_list:
                if isinstance(goal_reason, dict):
                    goal = goal_reason.get("goal", "目标内容缺失")
                    reasoning = goal_reason.get("reasoning", "没有明确原因")
                else:
                    goal = str(goal_reason)
                    reasoning = "没有明确原因"

                goal_str = f"目标：{goal}，产生该对话目标的原因：{reasoning}\n"
                goals_str += goal_str
        else:
            goal = "目前没有明确对话目标"
            reasoning = "目前没有明确对话目标，最好思考一个对话目标"
            goals_str = f"目标：{goal}，产生该对话目标的原因：{reasoning}\n"

        # 获取聊天历史记录
        chat_history_text = observation_info.chat_history_str

        if observation_info.new_messages_count > 0:
            new_messages_list = observation_info.unprocessed_messages
            new_messages_str = await build_readable_messages(
                new_messages_list,
                replace_bot_name=True,
                merge_messages=False,
                timestamp_mode="relative",
                read_mark=0.0,
            )
            chat_history_text += f"\n--- 以下是 {observation_info.new_messages_count} 条新消息 ---\n{new_messages_str}"

            # await observation_info.clear_unprocessed_messages()

        persona_text = f"你的名字是{self.name}，{self.personality_info}。"
        # 构建action历史文本
        action_history_list = conversation_info.done_action
        action_history_text = "你之前做的事情是："
        for action in action_history_list:
            action_history_text += f"{action}\n"

        prompt = f"""{persona_text}。现在你在参与一场QQ聊天，请分析以下聊天记录，并根据你的性格特征确定多个明确的对话目标。
这些目标应该反映出对话的不同方面和意图。

{action_history_text}
当前对话目标：
{goals_str}

聊天记录：
{chat_history_text}

请分析当前对话并确定最适合的对话目标。你可以：
1. 保持现有目标不变
2. 修改现有目标
3. 添加新目标
4. 删除不再相关的目标
5. 如果你想结束对话，请设置一个目标，目标goal为"结束对话"，原因reasoning为你希望结束对话

请以JSON数组格式输出当前的所有对话目标，每个目标包含以下字段：
1. goal: 对话目标（简短的一句话）
2. reasoning: 对话原因，为什么设定这个目标（简要解释）

输出格式示例：
[
{{
    "goal": "回答用户关于Python编程的具体问题",
    "reasoning": "用户提出了关于Python的技术问题，需要专业且准确的解答"
}},
{{
    "goal": "回答用户关于python安装的具体问题",
    "reasoning": "用户提出了关于Python的技术问题，需要专业且准确的解答"
}}
]"""

        logger.debug(f"[私聊][{self.private_name}]发送到LLM的提示词: {prompt}")
        try:
            content, _ = await self.llm.generate_response_async(prompt)
            logger.debug(f"[私聊][{self.private_name}]LLM原始返回内容: {content}")
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]分析对话目标时出错: {str(e)}")
            content = ""

        # 使用改进后的get_items_from_json函数处理JSON数组
        success, result = get_items_from_json(
            content,
            self.private_name,
            "goal",
            "reasoning",
            required_types={"goal": str, "reasoning": str},
            allow_array=True,
        )

        if success:
            # 判断结果是单个字典还是字典列表
            if isinstance(result, list):
                # 清空现有目标列表并添加新目标
                conversation_info.goal_list = []
                for item in result:
                    conversation_info.goal_list.append(item)

                # 返回第一个目标作为当前主要目标（如果有）
                if result:
                    first_goal = result[0]
                    return first_goal.get("goal", ""), "", first_goal.get("reasoning", "")
            else:
                # 单个目标的情况
                conversation_info.goal_list.append(result)
                return goal, "", reasoning

        # 如果解析失败，返回默认值
        return "", "", ""

    async def _update_goals(self, new_goal: str, method: str, reasoning: str):
        """更新目标列表

        Args:
            new_goal: 新的目标
            method: 实现目标的方法
            reasoning: 目标的原因
        """
        # 检查新目标是否与现有目标相似
        for i, (existing_goal, _, _) in enumerate(self.goals):
            if _calculate_similarity(new_goal, existing_goal) > 0.7:  # 相似度阈值
                # 更新现有目标
                self.goals[i] = (new_goal, method, reasoning)
                # 将此目标移到列表前面（最主要的位置）
                self.goals.insert(0, self.goals.pop(i))
                return

        # 添加新目标到列表前面
        self.goals.insert(0, (new_goal, method, reasoning))

        # 限制目标数量
        if len(self.goals) > self.max_goals:
            self.goals.pop()  # 移除最老的目标

    async def get_all_goals(self) -> List[Tuple[str, str, str]]:
        """获取所有当前目标

        Returns:
            List[Tuple[str, str, str]]: 目标列表，每项为(目标, 方法, 原因)
        """
        return self.goals.copy()

    async def get_alternative_goals(self) -> List[Tuple[str, str, str]]:
        """获取除了当前主要目标外的其他备选目标

        Returns:
            List[Tuple[str, str, str]]: 备选目标列表
        """
        if len(self.goals) <= 1:
            return []
        return self.goals[1:].copy()

    async def analyze_conversation(self, goal, reasoning):
        messages = self.chat_observer.get_cached_messages()
        chat_history_text = await build_readable_messages(
            messages,
            replace_bot_name=True,
            merge_messages=False,
            timestamp_mode="relative",
            read_mark=0.0,
        )

        persona_text = f"你的名字是{self.name}，{self.personality_info}。"
        # ===> Persona 文本构建结束 <===

        # --- 修改 Prompt 字符串，使用 persona_text ---
        prompt = f"""{persona_text}。现在你在参与一场QQ聊天，
        当前对话目标：{goal}
        产生该对话目标的原因：{reasoning}
        
        请分析以下聊天记录，并根据你的性格特征评估该目标是否已经达到，或者你是否希望停止该次对话。
        聊天记录：
        {chat_history_text}
        请以JSON格式输出，包含以下字段：
        1. goal_achieved: 对话目标是否已经达到（true/false）
        2. stop_conversation: 是否希望停止该次对话（true/false）
        3. reason: 为什么希望停止该次对话（简要解释）   

输出格式示例：
{{
    "goal_achieved": true,
    "stop_conversation": false,
    "reason": "虽然目标已达成，但对话仍然有继续的价值"
}}"""

        try:
            content, _ = await self.llm.generate_response_async(prompt)
            logger.debug(f"[私聊][{self.private_name}]LLM原始返回内容: {content}")

            # 尝试解析JSON
            success, result = get_items_from_json(
                content,
                self.private_name,
                "goal_achieved",
                "stop_conversation",
                "reason",
                required_types={"goal_achieved": bool, "stop_conversation": bool, "reason": str},
            )

            if not success:
                logger.error(f"[私聊][{self.private_name}]无法解析对话分析结果JSON")
                return False, False, "解析结果失败"

            goal_achieved = result["goal_achieved"]
            stop_conversation = result["stop_conversation"]
            reason = result["reason"]

            return goal_achieved, stop_conversation, reason

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]分析对话状态时出错: {str(e)}")
            return False, False, f"分析出错: {str(e)}"


# 先注释掉，万一以后出问题了还能开回来（（（
# class DirectMessageSender:
#     """直接发送消息到平台的发送器"""

#     def __init__(self, private_name: str):
#         self.logger = get_module_logger("direct_sender")
#         self.storage = MessageStorage()
#         self.private_name = private_name

#     async def send_via_ws(self, message: MessageSending) -> None:
#         try:
#             await global_api.send_message(message)
#         except Exception as e:
#             raise ValueError(f"未找到平台：{message.message_info.platform} 的url配置，请检查配置文件") from e

#     async def send_message(
#         self,
#         chat_stream: ChatStream,
#         content: str,
#         reply_to_message: Optional[Message] = None,
#     ) -> None:
#         """直接发送消息到平台

#         Args:
#             chat_stream: 聊天流
#             content: 消息内容
#             reply_to_message: 要回复的消息
#         """
#         # 构建消息对象
#         message_segment = Seg(type="text", data=content)
#         bot_user_info = UserInfo(
#             user_id=global_config.BOT_QQ,
#             user_nickname=global_config.BOT_NICKNAME,
#             platform=chat_stream.platform,
#         )

#         message = MessageSending(
#             message_id=f"dm{round(time.time(), 2)}",
#             chat_stream=chat_stream,
#             bot_user_info=bot_user_info,
#             sender_info=reply_to_message.message_info.user_info if reply_to_message else None,
#             message_segment=message_segment,
#             reply=reply_to_message,
#             is_head=True,
#             is_emoji=False,
#             thinking_start_time=time.time(),
#         )

#         # 处理消息
#         await message.process()

#         _message_json = message.to_dict()

#         # 发送消息
#         try:
#             await self.send_via_ws(message)
#             await self.storage.store_message(message, chat_stream)
#             logger.success(f"[私聊][{self.private_name}]PFC消息已发送: {content}")
#         except Exception as e:
#             logger.error(f"[私聊][{self.private_name}]PFC消息发送失败: {str(e)}")
