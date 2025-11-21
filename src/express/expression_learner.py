import time
import json
import os
from typing import List, Optional, Tuple
import traceback
from src.common.logger import get_logger
from src.common.database.database_model import Expression
from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config, global_config
from src.chat.utils.chat_message_builder import (
    get_raw_msg_by_timestamp_with_chat_inclusive,
    build_anonymous_messages,
    build_bare_messages,
)
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.message_receive.chat_stream import get_chat_manager
from src.express.express_utils import filter_message_content, calculate_similarity
from json_repair import repair_json


# MAX_EXPRESSION_COUNT = 300

logger = get_logger("expressor")


def init_prompt() -> None:
    learn_style_prompt = """
{chat_str}

请从上面这段群聊中概括除了人名为"SELF"之外的人的语言风格
1. 只考虑文字，不要考虑表情包和图片
2. 不要涉及具体的人名，但是可以涉及具体名词
3. 思考有没有特殊的梗，一并总结成语言风格
4. 例子仅供参考，请严格根据群聊内容总结!!!
注意：总结成如下格式的规律，总结的内容要详细，但具有概括性：
例如：当"AAAAA"时，可以"BBBBB", AAAAA代表某个具体的场景，不超过20个字。BBBBB代表对应的语言风格，特定句式或表达方式，不超过20个字。

例如：
当"对某件事表示十分惊叹"时，使用"我嘞个xxxx"
当"表示讽刺的赞同，不讲道理"时，使用"对对对"
当"想说明某个具体的事实观点，但懒得明说，使用"懂的都懂"
当"当涉及游戏相关时，夸赞，略带戏谑意味"时，使用"这么强！"

请注意：不要总结你自己（SELF）的发言，尽量保证总结内容的逻辑性
现在请你概括
"""
    Prompt(learn_style_prompt, "learn_style_prompt")

    match_expression_context_prompt = """
**聊天内容**
{chat_str}

**从聊天内容总结的表达方式pairs**
{expression_pairs}

请你为上面的每一条表达方式，找到该表达方式的原文句子，并输出匹配结果，expression_pair不能有重复，每个expression_pair仅输出一个最合适的context。
如果找不到原句，就不输出该句的匹配结果。
以json格式输出：
格式如下：
{{
    "expression_pair": "表达方式pair的序号（数字）",
    "context": "与表达方式对应的原文句子的原始内容，不要修改原文句子的内容",
}}，
{{
    "expression_pair": "表达方式pair的序号（数字）",
    "context": "与表达方式对应的原文句子的原始内容，不要修改原文句子的内容",
}}，
...

现在请你输出匹配结果：
"""
    Prompt(match_expression_context_prompt, "match_expression_context_prompt")


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

        # 学习参数
        _, self.enable_learning, self.learning_intensity = global_config.expression.get_expression_config_for_chat(
            self.chat_id
        )
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
        if not self.should_trigger_learning():
            return

        try:
            logger.info(f"在聊天流 {self.chat_name} 学习表达方式")
            # 学习语言风格
            learnt_style = await self.learn_and_store(num=25)

            # 更新学习时间
            self.last_learning_time = time.time()

            if learnt_style:
                logger.info(f"聊天流 {self.chat_name} 表达学习完成")
            else:
                logger.warning(f"聊天流 {self.chat_name} 表达学习未获得有效结果")

        except Exception as e:
            logger.error(f"为聊天流 {self.chat_name} 触发学习失败: {e}")
            traceback.print_exc()
            return

    async def learn_and_store(self, num: int = 10) -> List[Tuple[str, str, str]]:
        """
        学习并存储表达方式
        """
        learnt_expressions = await self.learn_expression(num)

        if learnt_expressions is None:
            logger.info("没有学习到表达风格")
            return []

        # 展示学到的表达方式
        learnt_expressions_str = ""
        for (
            situation,
            style,
            _context,
            _up_content,
        ) in learnt_expressions:
            learnt_expressions_str += f"{situation}->{style}\n"
        logger.info(f"在 {self.chat_name} 学习到表达风格:\n{learnt_expressions_str}")

        current_time = time.time()

        # 存储到数据库 Expression 表
        for (
            situation,
            style,
            context,
            up_content,
        ) in learnt_expressions:
            await self._upsert_expression_record(
                situation=situation,
                style=style,
                context=context,
                up_content=up_content,
                current_time=current_time,
            )

        return learnt_expressions

    async def match_expression_context(
        self, expression_pairs: List[Tuple[str, str]], random_msg_match_str: str
    ) -> List[Tuple[str, str, str]]:
        # 为expression_pairs逐个条目赋予编号，并构建成字符串
        numbered_pairs = []
        for i, (situation, style) in enumerate(expression_pairs, 1):
            numbered_pairs.append(f'{i}. 当"{situation}"时，使用"{style}"')

        expression_pairs_str = "\n".join(numbered_pairs)

        prompt = "match_expression_context_prompt"
        prompt = await global_prompt_manager.format_prompt(
            prompt,
            expression_pairs=expression_pairs_str,
            chat_str=random_msg_match_str,
        )

        response, _ = await self.express_learn_model.generate_response_async(prompt, temperature=0.3)

        # print(f"match_expression_context_prompt: {prompt}")
        # print(f"{response}")

        # 解析JSON响应
        match_responses = []
        try:
            response = response.strip()
            # 检查是否已经是标准JSON数组格式
            if response.startswith("[") and response.endswith("]"):
                match_responses = json.loads(response)
            else:
                # 尝试直接解析多个JSON对象
                try:
                    # 如果是多个JSON对象用逗号分隔，包装成数组
                    if response.startswith("{") and not response.startswith("["):
                        response = "[" + response + "]"
                        match_responses = json.loads(response)
                    else:
                        # 使用repair_json处理响应
                        repaired_content = repair_json(response)

                        # 确保repaired_content是列表格式
                        if isinstance(repaired_content, str):
                            try:
                                parsed_data = json.loads(repaired_content)
                                if isinstance(parsed_data, dict):
                                    # 如果是字典，包装成列表
                                    match_responses = [parsed_data]
                                elif isinstance(parsed_data, list):
                                    match_responses = parsed_data
                                else:
                                    match_responses = []
                            except json.JSONDecodeError:
                                match_responses = []
                        elif isinstance(repaired_content, dict):
                            # 如果是字典，包装成列表
                            match_responses = [repaired_content]
                        elif isinstance(repaired_content, list):
                            match_responses = repaired_content
                        else:
                            match_responses = []
                except json.JSONDecodeError:
                    # 如果还是失败，尝试repair_json
                    repaired_content = repair_json(response)
                    if isinstance(repaired_content, str):
                        parsed_data = json.loads(repaired_content)
                        match_responses = parsed_data if isinstance(parsed_data, list) else [parsed_data]
                    else:
                        match_responses = repaired_content if isinstance(repaired_content, list) else [repaired_content]

        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"解析匹配响应JSON失败: {e}, 响应内容: \n{response}")
            return []

        # 确保 match_responses 是一个列表
        if not isinstance(match_responses, list):
            if isinstance(match_responses, dict):
                match_responses = [match_responses]
            else:
                logger.error(f"match_responses 不是列表或字典类型: {type(match_responses)}, 内容: {match_responses}")
                return []

        matched_expressions = []
        used_pair_indices = set()  # 用于跟踪已经使用的expression_pair索引

        logger.debug(f"match_responses 类型: {type(match_responses)}, 长度: {len(match_responses)}")
        logger.debug(f"match_responses 内容: {match_responses}")

        for match_response in match_responses:
            try:
                # 检查 match_response 的类型
                if not isinstance(match_response, dict):
                    logger.error(f"match_response 不是字典类型: {type(match_response)}, 内容: {match_response}")
                    continue

                # 获取表达方式序号
                if "expression_pair" not in match_response:
                    logger.error(f"match_response 缺少 'expression_pair' 字段: {match_response}")
                    continue

                pair_index = int(match_response["expression_pair"]) - 1  # 转换为0-based索引

                # 检查索引是否有效且未被使用过
                if 0 <= pair_index < len(expression_pairs) and pair_index not in used_pair_indices:
                    situation, style = expression_pairs[pair_index]
                    context = match_response.get("context", "")
                    matched_expressions.append((situation, style, context))
                    used_pair_indices.add(pair_index)  # 标记该索引已使用
                    logger.debug(f"成功匹配表达方式 {pair_index + 1}: {situation} -> {style}")
                elif pair_index in used_pair_indices:
                    logger.debug(f"跳过重复的表达方式 {pair_index + 1}")
            except (ValueError, KeyError, IndexError, TypeError) as e:
                logger.error(f"解析匹配条目失败: {e}, 条目: {match_response}")
                continue

        return matched_expressions

    async def learn_expression(self, num: int = 10) -> Optional[List[Tuple[str, str, str, str]]]:
        """从指定聊天流学习表达方式

        Args:
            num: 学习数量
        """
        current_time = time.time()

        # 获取上次学习之后的消息
        random_msg = get_raw_msg_by_timestamp_with_chat_inclusive(
            chat_id=self.chat_id,
            timestamp_start=self.last_learning_time,
            timestamp_end=current_time,
            limit=num,
        )
        # print(random_msg)
        if not random_msg or random_msg == []:
            return None

        # 学习用
        random_msg_str: str = await build_anonymous_messages(random_msg)
        # 溯源用
        random_msg_match_str: str = await build_bare_messages(random_msg)

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
        expressions: List[Tuple[str, str]] = self.parse_expression_response(response)
        expressions = self._filter_self_reference_styles(expressions)
        if not expressions:
            logger.info("过滤后没有可用的表达方式（style 与机器人名称重复）")
            return None
        # logger.debug(f"学习{type_str}的response: {response}")

        # 对表达方式溯源
        matched_expressions: List[Tuple[str, str, str]] = await self.match_expression_context(
            expressions, random_msg_match_str
        )
        # 为每条消息构建精简文本列表，保留到原消息索引的映射
        bare_lines: List[Tuple[int, str]] = self._build_bare_lines(random_msg)
        # 将 matched_expressions 结合上一句 up_content（若不存在上一句则跳过）
        filtered_with_up: List[Tuple[str, str, str, str]] = []  # (situation, style, context, up_content)
        for situation, style, context in matched_expressions:
            # 在 bare_lines 中找到第一处相似度达到85%的行
            pos = None
            for i, (_, c) in enumerate(bare_lines):
                similarity = calculate_similarity(c, context)
                if similarity >= 0.85:  # 85%相似度阈值
                    pos = i
                    break

            if pos is None or pos == 0:
                # 没有匹配到目标句或没有上一句，跳过该表达
                continue

            # 检查目标句是否为空
            target_content = bare_lines[pos][1]
            if not target_content:
                # 目标句为空，跳过该表达
                continue

            prev_original_idx = bare_lines[pos - 1][0]
            up_content = filter_message_content(random_msg[prev_original_idx].processed_plain_text or "")
            if not up_content:
                # 上一句为空，跳过该表达
                continue
            filtered_with_up.append((situation, style, context, up_content))

        if not filtered_with_up:
            return None

        return filtered_with_up

    def parse_expression_response(self, response: str) -> List[Tuple[str, str, str]]:
        """
        解析LLM返回的表达风格总结，每一行提取"当"和"使用"之间的内容，存储为(situation, style)元组
        """
        expressions: List[Tuple[str, str, str]] = []
        for line in response.splitlines():
            line = line.strip()
            if not line:
                continue
            # 查找"当"和下一个引号
            idx_when = line.find('当"')
            if idx_when == -1:
                continue
            idx_quote1 = idx_when + 1
            idx_quote2 = line.find('"', idx_quote1 + 1)
            if idx_quote2 == -1:
                continue
            situation = line[idx_quote1 + 1 : idx_quote2]
            # 查找"使用"
            idx_use = line.find('使用"', idx_quote2)
            if idx_use == -1:
                continue
            idx_quote3 = idx_use + 2
            idx_quote4 = line.find('"', idx_quote3 + 1)
            if idx_quote4 == -1:
                continue
            style = line[idx_quote3 + 1 : idx_quote4]
            expressions.append((situation, style))
        return expressions

    def _filter_self_reference_styles(self, expressions: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
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

        filtered: List[Tuple[str, str]] = []
        removed_count = 0
        for situation, style in expressions:
            normalized_style = (style or "").strip()
            if normalized_style and normalized_style.casefold() not in banned_casefold:
                filtered.append((situation, style))
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
        up_content: str,
        current_time: float,
    ) -> None:
        expr_obj = Expression.select().where((Expression.chat_id == self.chat_id) & (Expression.style == style)).first()

        if expr_obj:
            await self._update_existing_expression(
                expr_obj=expr_obj,
                situation=situation,
                context=context,
                up_content=up_content,
                current_time=current_time,
            )
            return

        await self._create_expression_record(
            situation=situation,
            style=style,
            context=context,
            up_content=up_content,
            current_time=current_time,
        )

    async def _create_expression_record(
        self,
        situation: str,
        style: str,
        context: str,
        up_content: str,
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
            up_content=up_content,
        )

    async def _update_existing_expression(
        self,
        expr_obj: Expression,
        situation: str,
        context: str,
        up_content: str,
        current_time: float,
    ) -> None:
        content_list = self._parse_content_list(expr_obj.content_list)
        content_list.append(situation)

        expr_obj.content_list = json.dumps(content_list, ensure_ascii=False)
        expr_obj.count = (expr_obj.count or 0) + 1
        expr_obj.last_active_time = current_time
        expr_obj.context = context
        expr_obj.up_content = up_content

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

    def _build_bare_lines(self, messages: List) -> List[Tuple[int, str]]:
        """
        为每条消息构建精简文本列表，保留到原消息索引的映射

        Args:
            messages: 消息列表

        Returns:
            List[Tuple[int, str]]: (original_index, bare_content) 元组列表
        """
        bare_lines: List[Tuple[int, str]] = []

        for idx, msg in enumerate(messages):
            content = msg.processed_plain_text or ""
            content = filter_message_content(content)
            # 即使content为空也要记录，防止错位
            bare_lines.append((idx, content))

        return bare_lines


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
