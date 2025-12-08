import time
import json
import os
import re
import asyncio
from typing import List, Optional, Tuple
import traceback
from src.common.logger import get_logger
from src.common.database.database_model import Expression
from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config, global_config
from src.chat.utils.chat_message_builder import (
    get_raw_msg_by_timestamp_with_chat_inclusive,
    build_anonymous_messages,
)
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.message_receive.chat_stream import get_chat_manager
from src.express.express_utils import filter_message_content
from json_repair import repair_json


# MAX_EXPRESSION_COUNT = 300

logger = get_logger("expressor")


def init_prompt() -> None:
    if global_config.expression.content_filtration:
        learn_style_prompt = """{chat_str}

请从上面这段群聊中概括除了人名为"SELF"之外的人的语言风格。
每一行消息前面的方括号中的数字（如 [1]、[2]）是该行消息的唯一编号，请在输出中引用这些编号来标注“表达方式的来源行”。
1. 只考虑文字，不要考虑表情包和图片
2. 不要涉及具体的名称、事件以及话题内容，但是可以涉及具体名词
3. 思考有没有特殊的梗，一并总结成语言风格
4. 必须符合"{global_config.expression.filtration_prompt}"的要求
5. 例子仅供参考，请严格根据群聊内容总结!!!
注意：总结成如下格式的规律，总结的内容要详细，但具有概括性：
例如：当"AAAAA"时，可以"BBBBB", AAAAA代表某个具体的场景，不超过20个字。BBBBB代表对应的语言风格，特定句式或表达方式，不超过20个字。

请严格以 JSON 数组的形式输出结果，每个元素为一个对象，结构如下（注意字段名）：
[
  {{"situation": "AAAAA", "style": "BBBBB", "source_id": "3"}},
  {{"situation": "CCCC", "style": "DDDD", "source_id": "7"}}
  {{"situation": "对某件事表示十分惊叹", "style": "使用 我嘞个xxxx", "source_id": "[消息编号]"}},
  {{"situation": "表示讽刺的赞同，不讲道理", "style": "对对对", "source_id": "[消息编号]"}},
  {{"situation": "当涉及游戏相关时，夸赞，略带戏谑意味", "style": "使用 这么强！", "source_id": "[消息编号]"}},
]

请注意：
- 不要总结你自己（SELF）的发言，尽量保证总结内容的逻辑性
- 请只针对最重要的若干条表达方式进行总结，避免输出太多重复或相似的条目

其中：
- situation：表示“在什么情境下”的简短概括（不超过20个字）
- style：表示对应的语言风格或常用表达（不超过20个字）
- source_id：该表达方式对应的“来源行编号”，即上方聊天记录中方括号里的数字（例如 [3]），请只输出数字本身，不要包含方括号

现在请你输出 JSON：
"""
    else:
        learn_style_prompt = """{chat_str}

请从上面这段群聊中概括除了人名为"SELF"之外的人的语言风格。
每一行消息前面的方括号中的数字（如 [1]、[2]）是该行消息的唯一编号，请在输出中引用这些编号来标注“表达方式的来源行”。
1. 只考虑文字，不要考虑表情包和图片
2. 不要涉及具体的名称、事件以及话题内容，但是可以涉及具体名词
3. 思考有没有特殊的梗，一并总结成语言风格
4. 例子仅供参考，请严格根据群聊内容总结!!!
注意：总结成如下格式的规律，总结的内容要详细，但具有概括性：
例如：当"AAAAA"时，可以"BBBBB", AAAAA代表某个具体的场景，不超过20个字。BBBBB代表对应的语言风格，特定句式或表达方式，不超过20个字。

请严格以 JSON 数组的形式输出结果，每个元素为一个对象，结构如下（注意字段名）：
[
  {{"situation": "AAAAA", "style": "BBBBB", "source_id": "3"}},
  {{"situation": "CCCC", "style": "DDDD", "source_id": "7"}}
  {{"situation": "对某件事表示十分惊叹", "style": "使用 我嘞个xxxx", "source_id": "[消息编号]"}},
  {{"situation": "表示讽刺的赞同，不讲道理", "style": "对对对", "source_id": "[消息编号]"}},
  {{"situation": "当涉及游戏相关时，夸赞，略带戏谑意味", "style": "使用 这么强！", "source_id": "[消息编号]"}},
]

请注意：
- 不要总结你自己（SELF）的发言，尽量保证总结内容的逻辑性
- 请只针对最重要的若干条表达方式进行总结，避免输出太多重复或相似的条目

其中：
- situation：表示“在什么情境下”的简短概括（不超过20个字）
- style：表示对应的语言风格或常用表达（不超过20个字）
- source_id：该表达方式对应的“来源行编号”，即上方聊天记录中方括号里的数字（例如 [3]），请只输出数字本身，不要包含方括号

现在请你输出 JSON：
"""
    Prompt(learn_style_prompt, "learn_style_prompt")




class ExpressionLearner:
    def __init__(self, chat_id: str) -> None:
        self.express_learn_model: LLMRequest = LLMRequest(
            model_set=model_config.model_task_config.utils, request_type="expression.learner"
        )
        self.summary_model: LLMRequest = LLMRequest(
            model_set=model_config.model_task_config.utils_small, request_type="expression.summary"
        )
        self.embedding_model: LLMRequest = LLMRequest(
            model_set=model_config.model_task_config.embedding, request_type="expression.embedding"
        )
        self.chat_id = chat_id
        self.chat_stream = get_chat_manager().get_stream(chat_id)
        self.chat_name = get_chat_manager().get_stream_name(chat_id) or chat_id

        # 维护每个chat的上次学习时间
        self.last_learning_time: float = time.time()

        # 学习锁，防止并发执行学习任务
        self._learning_lock = asyncio.Lock()

        # 学习参数
        _, self.enable_learning, self.learning_intensity = global_config.expression.get_expression_config_for_chat(
            self.chat_id
        )
        # 防止除以零：如果学习强度为0或负数，使用最小值0.0001
        if self.learning_intensity <= 0:
            logger.warning(f"学习强度为 {self.learning_intensity}，已自动调整为 0.0001 以避免除以零错误")
            self.learning_intensity = 0.0000001
        self.min_messages_for_learning = 15 / self.learning_intensity  # 触发学习所需的最少消息数
        self.min_learning_interval = 120 / self.learning_intensity

    def should_trigger_learning(self) -> bool:
        """
        检查是否应该触发学习

        Args:
            chat_id: 聊天流ID

        Returns:
            bool: 是否应该触发学习
        """
        # 检查是否允许学习
        if not self.enable_learning:
            return False

        # 检查时间间隔
        time_diff = time.time() - self.last_learning_time
        if time_diff < self.min_learning_interval:
            return False

        # 检查消息数量（只检查指定聊天流的消息）
        recent_messages = get_raw_msg_by_timestamp_with_chat_inclusive(
            chat_id=self.chat_id,
            timestamp_start=self.last_learning_time,
            timestamp_end=time.time(),
        )

        if not recent_messages or len(recent_messages) < self.min_messages_for_learning:
            return False

        return True

    async def trigger_learning_for_chat(self):
        """
        为指定聊天流触发学习

        Args:
            chat_id: 聊天流ID

        Returns:
            bool: 是否成功触发学习
        """
        # 使用异步锁防止并发执行
        async with self._learning_lock:
            # 在锁内检查，避免并发触发
            # 如果锁被持有，其他协程会等待，但等待期间条件可能已变化，所以需要再次检查
            if not self.should_trigger_learning():
                return

            # 保存学习开始前的时间戳，用于获取消息范围
            learning_start_timestamp = time.time()
            previous_learning_time = self.last_learning_time
            
            # 立即更新学习时间，防止并发触发
            self.last_learning_time = learning_start_timestamp

            try:
                logger.info(f"在聊天流 {self.chat_name} 学习表达方式")
                # 学习语言风格，传递学习开始前的时间戳
                learnt_style = await self.learn_and_store(num=25, timestamp_start=previous_learning_time)

                if learnt_style:
                    logger.info(f"聊天流 {self.chat_name} 表达学习完成")
                else:
                    logger.warning(f"聊天流 {self.chat_name} 表达学习未获得有效结果")

            except Exception as e:
                logger.error(f"为聊天流 {self.chat_name} 触发学习失败: {e}")
                traceback.print_exc()
                # 即使失败也保持时间戳更新，避免频繁重试
                return

    async def learn_and_store(self, num: int = 10, timestamp_start: Optional[float] = None) -> List[Tuple[str, str, str]]:
        """
        学习并存储表达方式
        
        Args:
            num: 学习数量
            timestamp_start: 学习开始的时间戳，如果为None则使用self.last_learning_time
        """
        learnt_expressions = await self.learn_expression(num, timestamp_start=timestamp_start)

        if learnt_expressions is None:
            logger.info("没有学习到表达风格")
            return []

        # 展示学到的表达方式
        learnt_expressions_str = ""
        for (
            situation,
            style,
            _context,
        ) in learnt_expressions:
            learnt_expressions_str += f"{situation}->{style}\n"
        logger.info(f"在 {self.chat_name} 学习到表达风格:\n{learnt_expressions_str}")

        current_time = time.time()

        # 存储到数据库 Expression 表
        for (
            situation,
            style,
            context,
        ) in learnt_expressions:
            await self._upsert_expression_record(
                situation=situation,
                style=style,
                context=context,
                current_time=current_time,
            )

        return learnt_expressions

    async def learn_expression(self, num: int = 10, timestamp_start: Optional[float] = None) -> Optional[List[Tuple[str, str, str]]]:
        """从指定聊天流学习表达方式

        Args:
            num: 学习数量
            timestamp_start: 学习开始的时间戳，如果为None则使用self.last_learning_time
        """
        current_time = time.time()
        
        # 使用传入的时间戳，如果没有则使用self.last_learning_time
        start_timestamp = timestamp_start if timestamp_start is not None else self.last_learning_time

        # 获取上次学习之后的消息
        random_msg = get_raw_msg_by_timestamp_with_chat_inclusive(
            chat_id=self.chat_id,
            timestamp_start=start_timestamp,
            timestamp_end=current_time,
            limit=num,
        )
        # print(random_msg)
        if not random_msg or random_msg == []:
            return None

        # 学习用（开启行编号，便于溯源）
        random_msg_str: str = await build_anonymous_messages(random_msg, show_ids=True)

        prompt: str = await global_prompt_manager.format_prompt(
            "learn_style_prompt",
            chat_str=random_msg_str,
        )

        # print(f"random_msg_str:{random_msg_str}")
        # logger.info(f"学习{type_str}的prompt: {prompt}")

        try:
            response, _ = await self.express_learn_model.generate_response_async(prompt, temperature=0.3)
        except Exception as e:
            logger.error(f"学习表达方式失败,模型生成出错: {e}")
            return None

        # 解析 LLM 返回的表达方式列表（包含来源行编号）
        expressions: List[Tuple[str, str, str]] = self.parse_expression_response(response)
        expressions = self._filter_self_reference_styles(expressions)
        if not expressions:
            logger.info("过滤后没有可用的表达方式（style 与机器人名称重复）")
            return None
        # logger.debug(f"学习{type_str}的response: {response}")

        # 直接根据 source_id 在 random_msg 中溯源，获取 context
        filtered_expressions: List[Tuple[str, str, str]] = []  # (situation, style, context)

        for situation, style, source_id in expressions:
            source_id_str = (source_id or "").strip()
            if not source_id_str.isdigit():
                # 无效的来源行编号，跳过
                continue

            line_index = int(source_id_str) - 1  # build_anonymous_messages 的编号从 1 开始
            if line_index < 0 or line_index >= len(random_msg):
                # 超出范围，跳过
                continue

            # 当前行的原始内容
            current_msg = random_msg[line_index]
            context = filter_message_content(current_msg.processed_plain_text or "")
            if not context:
                continue

            filtered_expressions.append((situation, style, context))

        if not filtered_expressions:
            return None

        return filtered_expressions

    def parse_expression_response(self, response: str) -> List[Tuple[str, str, str]]:
        """
        解析 LLM 返回的表达风格总结 JSON，提取 (situation, style, source_id) 元组列表。

        期望的 JSON 结构：
        [
          {"situation": "AAAAA", "style": "BBBBB", "source_id": "3"},
          ...
        ]
        """
        if not response:
            return []

        raw = response.strip()

        # 尝试提取 ```json 代码块
        json_block_pattern = r"```json\s*(.*?)\s*```"
        match = re.search(json_block_pattern, raw, re.DOTALL)
        if match:
            raw = match.group(1).strip()
        else:
            # 去掉可能存在的通用 ``` 包裹
            raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
            raw = raw.strip()

        parsed = None
        expressions: List[Tuple[str, str, str]] = []

        try:
            # 优先尝试直接解析
            if raw.startswith("[") and raw.endswith("]"):
                parsed = json.loads(raw)
            else:
                repaired = repair_json(raw)
                if isinstance(repaired, str):
                    parsed = json.loads(repaired)
                else:
                    parsed = repaired
        except Exception as parse_error:
            # 如果解析失败，尝试修复中文引号问题
            # 使用状态机方法，在 JSON 字符串值内部将中文引号替换为转义的英文引号
            try:
                def fix_chinese_quotes_in_json(text):
                    """使用状态机修复 JSON 字符串值中的中文引号"""
                    result = []
                    i = 0
                    in_string = False
                    escape_next = False
                    
                    while i < len(text):
                        char = text[i]
                        
                        if escape_next:
                            # 当前字符是转义字符后的字符，直接添加
                            result.append(char)
                            escape_next = False
                            i += 1
                            continue
                        
                        if char == '\\':
                            # 转义字符
                            result.append(char)
                            escape_next = True
                            i += 1
                            continue
                        
                        if char == '"' and not escape_next:
                            # 遇到英文引号，切换字符串状态
                            in_string = not in_string
                            result.append(char)
                            i += 1
                            continue
                        
                        if in_string:
                            # 在字符串值内部，将中文引号替换为转义的英文引号
                            if char == '"':  # 中文左引号
                                result.append('\\"')
                            elif char == '"':  # 中文右引号
                                result.append('\\"')
                            else:
                                result.append(char)
                        else:
                            # 不在字符串内，直接添加
                            result.append(char)
                        
                        i += 1
                    
                    return ''.join(result)
                
                fixed_raw = fix_chinese_quotes_in_json(raw)
                
                # 再次尝试解析
                if fixed_raw.startswith("[") and fixed_raw.endswith("]"):
                    parsed = json.loads(fixed_raw)
                else:
                    repaired = repair_json(fixed_raw)
                    if isinstance(repaired, str):
                        parsed = json.loads(repaired)
                    else:
                        parsed = repaired
            except Exception as fix_error:
                logger.error(f"解析表达风格 JSON 失败，初始错误: {type(parse_error).__name__}: {str(parse_error)}")
                logger.error(f"修复中文引号后仍失败，错误: {type(fix_error).__name__}: {str(fix_error)}")
                logger.error(f"解析表达风格 JSON 失败，原始响应：{response}")
                logger.error(f"处理后的 JSON 字符串（前500字符）：{raw[:500]}")
                return []

        if isinstance(parsed, dict):
            parsed_list = [parsed]
        elif isinstance(parsed, list):
            parsed_list = parsed
        else:
            logger.error(f"表达风格解析结果类型异常: {type(parsed)}, 内容: {parsed}")
            return []

        for item in parsed_list:
            if not isinstance(item, dict):
                continue
            situation = str(item.get("situation", "")).strip()
            style = str(item.get("style", "")).strip()
            source_id = str(item.get("source_id", "")).strip()
            if not situation or not style or not source_id:
                # 三个字段必须同时存在
                continue
            expressions.append((situation, style, source_id))

        return expressions

    def _filter_self_reference_styles(self, expressions: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
        """
        过滤掉style与机器人名称/昵称重复的表达
        """
        banned_names = set()
        bot_nickname = (global_config.bot.nickname or "").strip()
        if bot_nickname:
            banned_names.add(bot_nickname)

        alias_names = global_config.bot.alias_names or []
        for alias in alias_names:
            alias = alias.strip()
            if alias:
                banned_names.add(alias)

        banned_casefold = {name.casefold() for name in banned_names if name}

        filtered: List[Tuple[str, str, str]] = []
        removed_count = 0
        for situation, style, source_id in expressions:
            normalized_style = (style or "").strip()
            if normalized_style and normalized_style.casefold() not in banned_casefold:
                filtered.append((situation, style, source_id))
            else:
                removed_count += 1

        if removed_count:
            logger.debug(f"已过滤 {removed_count} 条style与机器人名称重复的表达方式")

        return filtered

    async def _upsert_expression_record(
        self,
        situation: str,
        style: str,
        context: str,
        current_time: float,
    ) -> None:
        expr_obj = Expression.select().where((Expression.chat_id == self.chat_id) & (Expression.style == style)).first()

        if expr_obj:
            await self._update_existing_expression(
                expr_obj=expr_obj,
                situation=situation,
                context=context,
                current_time=current_time,
            )
            return

        await self._create_expression_record(
            situation=situation,
            style=style,
            context=context,
            current_time=current_time,
        )

    async def _create_expression_record(
        self,
        situation: str,
        style: str,
        context: str,
        current_time: float,
    ) -> None:
        content_list = [situation]
        formatted_situation = await self._compose_situation_text(content_list, 1, situation)

        Expression.create(
            situation=formatted_situation,
            style=style,
            content_list=json.dumps(content_list, ensure_ascii=False),
            count=1,
            last_active_time=current_time,
            chat_id=self.chat_id,
            create_date=current_time,
            context=context,
        )

    async def _update_existing_expression(
        self,
        expr_obj: Expression,
        situation: str,
        context: str,
        current_time: float,
    ) -> None:
        content_list = self._parse_content_list(expr_obj.content_list)
        content_list.append(situation)

        expr_obj.content_list = json.dumps(content_list, ensure_ascii=False)
        expr_obj.count = (expr_obj.count or 0) + 1
        expr_obj.last_active_time = current_time
        expr_obj.context = context

        new_situation = await self._compose_situation_text(
            content_list=content_list,
            count=expr_obj.count,
            fallback=expr_obj.situation,
        )
        expr_obj.situation = new_situation

        expr_obj.save()

    def _parse_content_list(self, stored_list: Optional[str]) -> List[str]:
        if not stored_list:
            return []
        try:
            data = json.loads(stored_list)
        except json.JSONDecodeError:
            return []
        return [str(item) for item in data if isinstance(item, str)] if isinstance(data, list) else []

    async def _compose_situation_text(self, content_list: List[str], count: int, fallback: str = "") -> str:
        sanitized = [c.strip() for c in content_list if c.strip()]
        summary = await self._summarize_situations(sanitized)
        if summary:
            return summary
        return "/".join(sanitized) if sanitized else fallback

    async def _summarize_situations(self, situations: List[str]) -> Optional[str]:
        if not situations:
            return None

        prompt = (
            "请阅读以下多个聊天情境描述，并将它们概括成一句简短的话，"
            "长度不超过20个字，保留共同特点：\n"
            f"{chr(10).join(f'- {s}' for s in situations[-10:])}\n只输出概括内容。"
        )

        try:
            summary, _ = await self.summary_model.generate_response_async(prompt, temperature=0.2)
            summary = summary.strip()
            if summary:
                return summary
        except Exception as e:
            logger.error(f"概括表达情境失败: {e}")
        return None

init_prompt()


class ExpressionLearnerManager:
    def __init__(self):
        self.expression_learners = {}

        self._ensure_expression_directories()

    def get_expression_learner(self, chat_id: str) -> ExpressionLearner:
        if chat_id not in self.expression_learners:
            self.expression_learners[chat_id] = ExpressionLearner(chat_id)
        return self.expression_learners[chat_id]

    def _ensure_expression_directories(self):
        """
        确保表达方式相关的目录结构存在
        """
        base_dir = os.path.join("data", "expression")
        directories_to_create = [
            base_dir,
            os.path.join(base_dir, "learnt_style"),
            os.path.join(base_dir, "learnt_grammar"),
        ]

        for directory in directories_to_create:
            try:
                os.makedirs(directory, exist_ok=True)
                logger.debug(f"确保目录存在: {directory}")
            except Exception as e:
                logger.error(f"创建目录失败 {directory}: {e}")


expression_learner_manager = ExpressionLearnerManager()
