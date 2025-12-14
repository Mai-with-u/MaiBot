import json
from typing import Tuple, List, Dict, Any
from src.common.logger import get_module_logger
from ..models.utils_model import LLMRequest
from ...config.config import global_config
from .chat_observer import ChatObserver
from maim_message import UserInfo

logger = get_module_logger("reply_checker")


class ReplyChecker:
    """回复检查器"""

    def __init__(self, stream_id: str, private_name: str):
        self.llm = LLMRequest(
            model=global_config.llm_PFC_reply_checker, temperature=0.50, max_tokens=1000, request_type="reply_check"
        )
        self.name = global_config.BOT_NICKNAME
        self.private_name = private_name
        self.chat_observer = ChatObserver.get_instance(stream_id, private_name)
        self.max_retries = 3  # 最大重试次数

    async def check(
        self, reply: str, goal: str, chat_history: List[Dict[str, Any]], chat_history_text: str, retry_count: int = 0
    ) -> Tuple[bool, str, bool]:
        """检查生成的回复是否合适

        Args:
            reply: 生成的回复
            goal: 对话目标
            chat_history: 对话历史记录
            chat_history_text: 对话历史记录文本
            retry_count: 当前重试次数

        Returns:
            Tuple[bool, str, bool]: (是否合适, 原因, 是否需要重新规划)
        """
        # 不再从 observer 获取，直接使用传入的 chat_history
        # messages = self.chat_observer.get_cached_messages(limit=20)
        try:
            # 筛选出最近由 Bot 自己发送的消息
            bot_messages = []
            for msg in reversed(chat_history):
                user_info = UserInfo.from_dict(msg.get("user_info", {}))
                if str(user_info.user_id) == str(global_config.BOT_QQ):  # 确保比较的是字符串
                    bot_messages.append(msg.get("processed_plain_text", ""))
                if len(bot_messages) >= 2:  # 只和最近的两条比较
                    break
                # 进行比较
            if bot_messages:
                # 可以用简单比较，或者更复杂的相似度库 (如 difflib)
                # 简单比较：是否完全相同
                if reply == bot_messages[0]:  # 和最近一条完全一样
                    logger.warning(
                        f"[私聊][{self.private_name}]ReplyChecker 检测到回复与上一条 Bot 消息完全相同: '{reply}'"
                    )
                    return (
                        False,
                        "被逻辑检查拒绝：回复内容与你上一条发言完全相同，可以选择深入话题或寻找其它话题或等待",
                        True,
                    )  # 不合适，需要返回至决策层
                # 2. 相似度检查 (如果精确匹配未通过)
                import difflib  # 导入 difflib 库

                # 计算编辑距离相似度，ratio() 返回 0 到 1 之间的浮点数
                similarity_ratio = difflib.SequenceMatcher(None, reply, bot_messages[0]).ratio()
                logger.debug(f"[私聊][{self.private_name}]ReplyChecker - 相似度: {similarity_ratio:.2f}")

                # 设置一个相似度阈值
                similarity_threshold = 0.9
                if similarity_ratio > similarity_threshold:
                    logger.warning(
                        f"[私聊][{self.private_name}]ReplyChecker 检测到回复与上一条 Bot 消息高度相似 (相似度 {similarity_ratio:.2f}): '{reply}'"
                    )
                    return (
                        False,
                        f"被逻辑检查拒绝：回复内容与你上一条发言高度相似 (相似度 {similarity_ratio:.2f})，可以选择深入话题或寻找其它话题或等待。",
                        True,
                    )

        except Exception as e:
            import traceback

            logger.error(f"[私聊][{self.private_name}]检查回复时出错: 类型={type(e)}, 值={e}")
            logger.error(f"[私聊][{self.private_name}]{traceback.format_exc()}")  # 打印详细的回溯信息

        prompt = f"""你是一个聊天逻辑检查器，请检查以下回复或消息是否合适：

当前对话目标：{goal}
最新的对话记录：
{chat_history_text}

待检查的消息：
{reply}

请结合聊天记录检查以下几点：
1. 这条消息是否依然符合当前对话目标和实现方式
2. 这条消息是否与最新的对话记录保持一致性
3. 是否存在重复发言，或重复表达同质内容（尤其是只是换一种方式表达了相同的含义）
4. 这条消息是否包含违规内容（例如血腥暴力，政治敏感等）
5. 这条消息是否以发送者的角度发言（不要让发送者自己回复自己的消息）
6. 这条消息是否通俗易懂
7. 这条消息是否有些多余，例如在对方没有回复的情况下，依然连续多次“消息轰炸”（尤其是已经连续发送3条信息的情况，这很可能不合理，需要着重判断）
8. 这条消息是否使用了完全没必要的修辞
9. 这条消息是否逻辑通顺
10. 这条消息是否太过冗长了（通常私聊的每条消息长度在20字以内，除非特殊情况）
11. 在连续多次发送消息的情况下，这条消息是否衔接自然，会不会显得奇怪（例如连续两条消息中部分内容重叠）

请以JSON格式输出，包含以下字段：
1. suitable: 是否合适 (true/false)
2. reason: 原因说明
3. need_replan: 是否需要重新决策 (true/false)，当你认为此时已经不适合发消息，需要规划其它行动时，设为true

输出格式示例：
{{
    "suitable": true,
    "reason": "回复符合要求，虽然有可能略微偏离目标，但是整体内容流畅得体",
    "need_replan": false
}}

注意：请严格按照JSON格式输出，不要包含任何其他内容。"""

        try:
            content, _ = await self.llm.generate_response_async(prompt)
            logger.debug(f"[私聊][{self.private_name}]检查回复的原始返回: {content}")

            # 清理内容，尝试提取JSON部分
            content = content.strip()
            try:
                # 尝试直接解析
                result = json.loads(content)
            except json.JSONDecodeError:
                # 如果直接解析失败，尝试查找和提取JSON部分
                import re

                json_pattern = r"\{[^{}]*\}"
                json_match = re.search(json_pattern, content)
                if json_match:
                    try:
                        result = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        # 如果JSON解析失败，尝试从文本中提取结果
                        is_suitable = "不合适" not in content.lower() and "违规" not in content.lower()
                        reason = content[:100] if content else "无法解析响应"
                        need_replan = "重新规划" in content.lower() or "目标不适合" in content.lower()
                        return is_suitable, reason, need_replan
                else:
                    # 如果找不到JSON，从文本中判断
                    is_suitable = "不合适" not in content.lower() and "违规" not in content.lower()
                    reason = content[:100] if content else "无法解析响应"
                    need_replan = "重新规划" in content.lower() or "目标不适合" in content.lower()
                    return is_suitable, reason, need_replan

            # 验证JSON字段
            suitable = result.get("suitable", None)
            reason = result.get("reason", "未提供原因")
            need_replan = result.get("need_replan", False)

            # 如果suitable字段是字符串，转换为布尔值
            if isinstance(suitable, str):
                suitable = suitable.lower() == "true"

            # 如果suitable字段不存在或不是布尔值，从reason中判断
            if suitable is None:
                suitable = "不合适" not in reason.lower() and "违规" not in reason.lower()

            # 如果不合适且未达到最大重试次数，返回需要重试
            if not suitable and retry_count < self.max_retries:
                return False, reason, False

            # 如果不合适且已达到最大重试次数，返回需要重新规划
            if not suitable and retry_count >= self.max_retries:
                return False, f"多次重试后仍不合适: {reason}", True

            return suitable, reason, need_replan

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]检查回复时出错: {e}")
            # 如果出错且已达到最大重试次数，建议重新规划
            if retry_count >= self.max_retries:
                return False, "多次检查失败，建议重新规划", True
            return False, f"检查过程出错，建议重试: {str(e)}", False
