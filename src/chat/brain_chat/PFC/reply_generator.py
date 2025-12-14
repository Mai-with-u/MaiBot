from typing import Tuple, List, Dict, Any
from src.common.logger import get_module_logger
from ..models.utils_model import LLMRequest
from ...config.config import global_config
from .chat_observer import ChatObserver
from .reply_checker import ReplyChecker
from src.individuality.individuality import Individuality
from .observation_info import ObservationInfo
from .conversation_info import ConversationInfo
from src.plugins.utils.chat_message_builder import build_readable_messages

logger = get_module_logger("reply_generator")

# --- 定义 Prompt 模板 ---

# Prompt for direct_reply (首次回复)
PROMPT_DIRECT_REPLY = """{persona_text}。现在你在参与一场QQ私聊，请根据以下信息生成一条回复：

当前对话目标：{goals_str}

{knowledge_info_str}

最近的聊天记录：
{chat_history_text}


请根据上述信息，结合聊天记录，回复对方。该回复应该：
1. 符合对话目标，以"你"的角度发言（不要自己与自己对话！）
2. 符合你的性格特征和身份细节
3. 通俗易懂，自然流畅，像正常聊天一样，简短（通常20字以内，除非特殊情况）
4. 可以适当利用相关知识，但不要生硬引用
5. 自然、得体，结合聊天记录逻辑合理，且没有重复表达同质内容

请注意把握聊天内容，不要回复的太有条理，可以有个性。请分清"你"和对方说的话，不要把"你"说的话当做对方说的话，这是你自己说的话。
可以回复得自然随意自然一些，就像真人一样，注意把握聊天内容，整体风格可以平和、简短，不要刻意突出自身学科背景，不要说你说过的话，可以简短，多简短都可以，但是避免冗长。
请你注意不要输出多余内容(包括前后缀，冒号和引号，括号，表情等)，只输出回复内容。
不要输出多余内容(包括前后缀，冒号和引号，括号，表情包，at或 @等 )。

请直接输出回复内容，不需要任何额外格式。"""

# Prompt for send_new_message (追问/补充)
PROMPT_SEND_NEW_MESSAGE = """{persona_text}。现在你在参与一场QQ私聊，**刚刚你已经发送了一条或多条消息**，现在请根据以下信息再发一条新消息： 

当前对话目标：{goals_str}

{knowledge_info_str}

最近的聊天记录：
{chat_history_text}


请根据上述信息，结合聊天记录，继续发一条新消息（例如对之前消息的补充，深入话题，或追问等等）。该消息应该： 
1. 符合对话目标，以"你"的角度发言（不要自己与自己对话！）
2. 符合你的性格特征和身份细节
3. 通俗易懂，自然流畅，像正常聊天一样，简短（通常20字以内，除非特殊情况）
4. 可以适当利用相关知识，但不要生硬引用
5. 跟之前你发的消息自然的衔接，逻辑合理，且没有重复表达同质内容或部分重叠内容

请注意把握聊天内容，不用太有条理，可以有个性。请分清"你"和对方说的话，不要把"你"说的话当做对方说的话，这是你自己说的话。
这条消息可以自然随意自然一些，就像真人一样，注意把握聊天内容，整体风格可以平和、简短，不要刻意突出自身学科背景，不要说你说过的话，可以简短，多简短都可以，但是避免冗长。
请你注意不要输出多余内容(包括前后缀，冒号和引号，括号，表情等)，只输出消息内容。
不要输出多余内容(包括前后缀，冒号和引号，括号，表情包，at或 @等 )。

请直接输出回复内容，不需要任何额外格式。"""

# Prompt for say_goodbye (告别语生成)
PROMPT_FAREWELL = """{persona_text}。你在参与一场 QQ 私聊，现在对话似乎已经结束，你决定再发一条最后的消息来圆满结束。

最近的聊天记录：
{chat_history_text}

请根据上述信息，结合聊天记录，构思一条**简短、自然、符合你人设**的最后的消息。
这条消息应该：
1. 从你自己的角度发言。
2. 符合你的性格特征和身份细节。
3. 通俗易懂，自然流畅，通常很简短。
4. 自然地为这场对话画上句号，避免开启新话题或显得冗长、刻意。

请像真人一样随意自然，**简洁是关键**。
不要输出多余内容（包括前后缀、冒号、引号、括号、表情包、at或@等）。

请直接输出最终的告别消息内容，不需要任何额外格式。"""


class ReplyGenerator:
    """回复生成器"""

    def __init__(self, stream_id: str, private_name: str):
        self.llm = LLMRequest(
            model=global_config.llm_PFC_chat,
            temperature=global_config.llm_PFC_chat["temp"],
            max_tokens=300,
            request_type="reply_generation",
        )
        self.personality_info = Individuality.get_instance().get_prompt(x_person=2, level=3)
        self.name = global_config.BOT_NICKNAME
        self.private_name = private_name
        self.chat_observer = ChatObserver.get_instance(stream_id, private_name)
        self.reply_checker = ReplyChecker(stream_id, private_name)

    # 修改 generate 方法签名，增加 action_type 参数
    async def generate(
        self, observation_info: ObservationInfo, conversation_info: ConversationInfo, action_type: str
    ) -> str:
        """生成回复

        Args:
            observation_info: 观察信息
            conversation_info: 对话信息
            action_type: 当前执行的动作类型 ('direct_reply' 或 'send_new_message')

        Returns:
            str: 生成的回复
        """
        # 构建提示词
        logger.debug(
            f"[私聊][{self.private_name}]开始生成回复 (动作类型: {action_type})：当前目标: {conversation_info.goal_list}"
        )

        # --- 构建通用 Prompt 参数 ---
        # (这部分逻辑基本不变)

        # 构建对话目标 (goals_str)
        goals_str = ""
        if conversation_info.goal_list:
            for goal_reason in conversation_info.goal_list:
                if isinstance(goal_reason, dict):
                    goal = goal_reason.get("goal", "目标内容缺失")
                    reasoning = goal_reason.get("reasoning", "没有明确原因")
                else:
                    goal = str(goal_reason)
                    reasoning = "没有明确原因"

                goal = str(goal) if goal is not None else "目标内容缺失"
                reasoning = str(reasoning) if reasoning is not None else "没有明确原因"
                goals_str += f"- 目标：{goal}\n  原因：{reasoning}\n"
        else:
            goals_str = "- 目前没有明确对话目标\n"  # 简化无目标情况

        # --- 新增：构建知识信息字符串 ---
        knowledge_info_str = "【供参考的相关知识和记忆】\n"  # 稍微改下标题，表明是供参考
        try:
            # 检查 conversation_info 是否有 knowledge_list 并且不为空
            if hasattr(conversation_info, "knowledge_list") and conversation_info.knowledge_list:
                # 最多只显示最近的 5 条知识
                recent_knowledge = conversation_info.knowledge_list[-5:]
                for i, knowledge_item in enumerate(recent_knowledge):
                    if isinstance(knowledge_item, dict):
                        query = knowledge_item.get("query", "未知查询")
                        knowledge = knowledge_item.get("knowledge", "无知识内容")
                        source = knowledge_item.get("source", "未知来源")
                        # 只取知识内容的前 2000 个字
                        knowledge_snippet = knowledge[:2000] + "..." if len(knowledge) > 2000 else knowledge
                        knowledge_info_str += (
                            f"{i + 1}. 关于 '{query}' (来源: {source}): {knowledge_snippet}\n"  # 格式微调，更简洁
                        )
                    else:
                        knowledge_info_str += f"{i + 1}. 发现一条格式不正确的知识记录。\n"

                if not recent_knowledge:
                    knowledge_info_str += "- 暂无。\n"  # 更简洁的提示

            else:
                knowledge_info_str += "- 暂无。\n"
        except AttributeError:
            logger.warning(f"[私聊][{self.private_name}]ConversationInfo 对象可能缺少 knowledge_list 属性。")
            knowledge_info_str += "- 获取知识列表时出错。\n"
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]构建知识信息字符串时出错: {e}")
            knowledge_info_str += "- 处理知识列表时出错。\n"

        # 获取聊天历史记录 (chat_history_text)
        chat_history_text = observation_info.chat_history_str
        if observation_info.new_messages_count > 0 and observation_info.unprocessed_messages:
            new_messages_list = observation_info.unprocessed_messages
            new_messages_str = await build_readable_messages(
                new_messages_list,
                replace_bot_name=True,
                merge_messages=False,
                timestamp_mode="relative",
                read_mark=0.0,
            )
            chat_history_text += f"\n--- 以下是 {observation_info.new_messages_count} 条新消息 ---\n{new_messages_str}"
        elif not chat_history_text:
            chat_history_text = "还没有聊天记录。"

        # 构建 Persona 文本 (persona_text)
        persona_text = f"你的名字是{self.name}，{self.personality_info}。"

        # --- 选择 Prompt ---
        if action_type == "send_new_message":
            prompt_template = PROMPT_SEND_NEW_MESSAGE
            logger.info(f"[私聊][{self.private_name}]使用 PROMPT_SEND_NEW_MESSAGE (追问生成)")
        elif action_type == "say_goodbye":  # 处理告别动作
            prompt_template = PROMPT_FAREWELL
            logger.info(f"[私聊][{self.private_name}]使用 PROMPT_FAREWELL (告别语生成)")
        else:  # 默认使用 direct_reply 的 prompt (包括 'direct_reply' 或其他未明确处理的类型)
            prompt_template = PROMPT_DIRECT_REPLY
            logger.info(f"[私聊][{self.private_name}]使用 PROMPT_DIRECT_REPLY (首次/非连续回复生成)")

        # --- 格式化最终的 Prompt ---
        prompt = prompt_template.format(
            persona_text=persona_text,
            goals_str=goals_str,
            chat_history_text=chat_history_text,
            knowledge_info_str=knowledge_info_str,
        )

        # --- 调用 LLM 生成 ---
        logger.debug(f"[私聊][{self.private_name}]发送到LLM的生成提示词:\n------\n{prompt}\n------")
        try:
            content, _ = await self.llm.generate_response_async(prompt)
            logger.debug(f"[私聊][{self.private_name}]生成的回复: {content}")
            # 移除旧的检查新消息逻辑，这应该由 conversation 控制流处理
            return content

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]生成回复时出错: {e}")
            return "抱歉，我现在有点混乱，让我重新思考一下..."

    # check_reply 方法保持不变
    async def check_reply(
        self, reply: str, goal: str, chat_history: List[Dict[str, Any]], chat_history_str: str, retry_count: int = 0
    ) -> Tuple[bool, str, bool]:
        """检查回复是否合适
        (此方法逻辑保持不变)
        """
        return await self.reply_checker.check(reply, goal, chat_history, chat_history_str, retry_count)
