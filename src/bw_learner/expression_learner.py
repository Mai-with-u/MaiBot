import time
import json
import os
import re
import asyncio
from typing import List, Optional, Tuple, Any, Dict
from src.common.logger import get_logger
from src.common.database.database_model import Expression
from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config, global_config
from src.chat.utils.chat_message_builder import (
    build_anonymous_messages,
)
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.chat.message_receive.chat_stream import get_chat_manager
from src.bw_learner.learner_utils import (
    filter_message_content,
    is_bot_message,
    build_context_paragraph,
    contains_bot_self_name,
    calculate_similarity,
    parse_expression_response,
)
from src.bw_learner.jargon_miner import miner_manager
from src.bw_learner.expression_auto_check_task import (
    single_expression_check,
)


# MAX_EXPRESSION_COUNT = 300

logger = get_logger("expressor")


def init_prompt() -> None:
    learn_style_prompt = """{chat_str}
你的名字是{bot_name},现在请你完成两个提取任务
任务1：请从上面这段群聊中用户的语言风格和说话方式
1. 只考虑文字，不要考虑表情包和图片
2. 不要总结SELF的发言，因为这是你自己的发言，不要重复学习你自己的发言
3. 不要涉及具体的人名，也不要涉及具体名词
4. 思考有没有特殊的梗，一并总结成语言风格
5. 例子仅供参考，请严格根据群聊内容总结!!!
注意：总结成如下格式的规律，总结的内容要详细，但具有概括性：
例如：当"AAAAA"时，可以"BBBBB", AAAAA代表某个场景，不超过20个字。BBBBB代表对应的语言风格，特定句式或表达方式，不超过20个字。
表达方式在3-5个左右，不要超过10个


任务2：请从上面这段聊天内容中提取"可能是黑话"的候选项（黑话/俚语/网络缩写/口头禅）。
- 必须为对话中真实出现过的短词或短语
- 必须是你无法理解含义的词语，没有明确含义的词语，请不要选择有明确含义，或者含义清晰的词语
- 排除：人名、@、表情包/图片中的内容、纯标点、常规功能词（如的、了、呢、啊等）
- 每个词条长度建议 2-8 个字符（不强制），尽量短小
- 请你提取出可能的黑话，最多30个黑话，请尽量提取所有

黑话必须为以下几种类型：
- 由字母构成的，汉语拼音首字母的简写词，例如：nb、yyds、xswl
- 英文词语的缩写，用英文字母概括一个词汇或含义，例如：CPU、GPU、API
- 中文词语的缩写，用几个汉字概括一个词汇或含义，例如：社死、内卷

输出要求：
将表达方式，语言风格和黑话以 JSON 数组输出，每个元素为一个对象，结构如下（注意字段名）：
注意请不要输出重复内容，请对表达方式和黑话进行去重。

[
  {{"situation": "AAAAA", "style": "BBBBB", "source_id": "3"}},
  {{"situation": "CCCC", "style": "DDDD", "source_id": "7"}}
  {{"situation": "对某件事表示十分惊叹", "style": "使用 我嘞个xxxx", "source_id": "[消息编号]"}},
  {{"situation": "表示讽刺的赞同，不讲道理", "style": "对对对", "source_id": "[消息编号]"}},
  {{"situation": "当涉及游戏相关时，夸赞，略带戏谑意味", "style": "使用 这么强！", "source_id": "[消息编号]"}},
  {{"content": "词条", "source_id": "12"}},
  {{"content": "词条2", "source_id": "5"}}
]

其中：
表达方式条目：
- situation：表示“在什么情境下”的简短概括（不超过20个字）
- style：表示对应的语言风格或常用表达（不超过20个字）
- source_id：该表达方式对应的“来源行编号”，即上方聊天记录中方括号里的数字（例如 [3]），请只输出数字本身，不要包含方括号
黑话jargon条目：
- content:表示黑话的内容
- source_id：该黑话对应的“来源行编号”，即上方聊天记录中方括号里的数字（例如 [3]），请只输出数字本身，不要包含方括号

现在请你输出 JSON：
"""
    Prompt(learn_style_prompt, "learn_style_prompt")


class ExpressionLearner:
    def __init__(self, chat_id: str) -> None:
        self.express_learn_model: LLMRequest = LLMRequest(
            model_set=model_config.model_task_config.utils, request_type="expression.learner"
        )
        self.summary_model: LLMRequest = LLMRequest(
            model_set=model_config.model_task_config.tool_use, request_type="expression.summary"
        )
        self.check_model: Optional[LLMRequest] = None  # 检查用的 LLM 实例，延迟初始化
        self.chat_id = chat_id
        self.chat_stream = get_chat_manager().get_stream(chat_id)
        self.chat_name = get_chat_manager().get_stream_name(chat_id) or chat_id

        # 学习锁，防止并发执行学习任务
        self._learning_lock = asyncio.Lock()

    async def learn_and_store(
        self,
        messages: List[Any],
    ) -> List[Tuple[str, str, str]]:
        """
        学习并存储表达方式

        Args:
            messages: 外部传入的消息列表（必需）
            num: 学习数量
            timestamp_start: 学习开始的时间戳，如果为None则使用self.last_learning_time
        """
        if not messages:
            return None

        random_msg = messages

        # 学习用（开启行编号，便于溯源）
        random_msg_str: str = await build_anonymous_messages(random_msg, show_ids=True)

        prompt: str = await global_prompt_manager.format_prompt(
            "learn_style_prompt",
            bot_name=global_config.bot.nickname,
            chat_str=random_msg_str,
        )

        # print(f"random_msg_str:{random_msg_str}")
        # logger.info(f"学习{type_str}的prompt: {prompt}")

        try:
            response, _ = await self.express_learn_model.generate_response_async(prompt, temperature=0.3)
        except Exception as e:
            logger.error(f"学习表达方式失败,模型生成出错: {e}")
            return None

        # 解析 LLM 返回的表达方式列表和黑话列表（包含来源行编号）
        expressions: List[Tuple[str, str, str]]
        jargon_entries: List[Tuple[str, str]]  # (content, source_id)
        expressions, jargon_entries = parse_expression_response(response)

        # 从缓存中检查 jargon 是否出现在 messages 中
        cached_jargon_entries = self._check_cached_jargons_in_messages(random_msg)
        if cached_jargon_entries:
            # 合并缓存中的 jargon 条目（去重：如果 content 已存在则跳过）
            existing_contents = {content for content, _ in jargon_entries}
            for content, source_id in cached_jargon_entries:
                if content not in existing_contents:
                    jargon_entries.append((content, source_id))
                    existing_contents.add(content)
                    logger.info(f"从缓存中检查到黑话: {content}")

        # 检查表达方式数量，如果超过10个则放弃本次表达学习
        if len(expressions) > 20:
            logger.info(f"表达方式提取数量超过10个（实际{len(expressions)}个），放弃本次表达学习")
            expressions = []

        # 检查黑话数量，如果超过30个则放弃本次黑话学习
        if len(jargon_entries) > 30:
            logger.info(f"黑话提取数量超过30个（实际{len(jargon_entries)}个），放弃本次黑话学习")
            jargon_entries = []

        # 处理黑话条目，路由到 jargon_miner（即使没有表达方式也要处理黑话）
        if jargon_entries:
            await self._process_jargon_entries(jargon_entries, random_msg)

        # 如果没有表达方式，直接返回
        if not expressions:
            logger.info("解析后没有可用的表达方式")
            return []

        logger.info(f"学习的prompt: {prompt}")
        logger.info(f"学习的expressions: {expressions}")
        logger.info(f"学习的jargon_entries: {jargon_entries}")
        logger.info(f"学习的response: {response}")

        # 过滤表达方式，根据 source_id 溯源并应用各种过滤规则
        learnt_expressions = self._filter_expressions(expressions, random_msg)

        if learnt_expressions is None:
            logger.info("没有学习到表达风格")
            return []

        # 展示学到的表达方式
        learnt_expressions_str = ""
        for (situation,style) in learnt_expressions:
            learnt_expressions_str += f"{situation}->{style}\n"
        logger.info(f"在 {self.chat_name} 学习到表达风格:\n{learnt_expressions_str}")

        current_time = time.time()

        # 存储到数据库 Expression 表
        for (situation,style) in learnt_expressions:
            await self._upsert_expression_record(
                situation=situation,
                style=style,
                current_time=current_time,
            )

        return learnt_expressions

    def _filter_expressions(
        self,
        expressions: List[Tuple[str, str, str]],
        messages: List[Any],
    ) -> List[Tuple[str, str, str]]:
        """
        过滤表达方式，移除不符合条件的条目
        
        Args:
            expressions: 表达方式列表，每个元素是 (situation, style, source_id)
            messages: 原始消息列表，用于溯源和验证
            
        Returns:
            过滤后的表达方式列表，每个元素是 (situation, style, context)
        """
        filtered_expressions: List[Tuple[str, str, str]] = []  # (situation, style, context)

        # 准备机器人名称集合（用于过滤 style 与机器人名称重复的表达）
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

        for situation, style, source_id in expressions:
            source_id_str = (source_id or "").strip()
            if not source_id_str.isdigit():
                # 无效的来源行编号，跳过
                continue

            line_index = int(source_id_str) - 1  # build_anonymous_messages 的编号从 1 开始
            if line_index < 0 or line_index >= len(messages):
                # 超出范围，跳过
                continue

            # 当前行的原始内容
            current_msg = messages[line_index]

            # 过滤掉从bot自己发言中提取到的表达方式
            if is_bot_message(current_msg):
                continue

            context = filter_message_content(current_msg.processed_plain_text or "")
            if not context:
                continue

            # 过滤掉包含 SELF 的内容（不学习）
            if "SELF" in (situation or "") or "SELF" in (style or "") or "SELF" in context:
                logger.info(
                    f"跳过包含 SELF 的表达方式: situation={situation}, style={style}, source_id={source_id}"
                )
                continue

            # 过滤掉 style 与机器人名称/昵称重复的表达
            normalized_style = (style or "").strip()
            if normalized_style and normalized_style.casefold() in banned_casefold:
                logger.debug(
                    f"跳过 style 与机器人名称重复的表达方式: situation={situation}, style={style}, source_id={source_id}"
                )
                continue

            # 过滤掉包含 "表情：" 或 "表情:" 的内容
            if "表情：" in (situation or "") or "表情:" in (situation or "") or \
               "表情：" in (style or "") or "表情:" in (style or "") or \
               "表情：" in context or "表情:" in context:
                logger.info(
                    f"跳过包含表情标记的表达方式: situation={situation}, style={style}, source_id={source_id}"
                )
                continue

            # 过滤掉包含 "[图片" 的内容
            if "[图片" in (situation or "") or "[图片" in (style or "") or "[图片" in context:
                logger.info(
                    f"跳过包含图片标记的表达方式: situation={situation}, style={style}, source_id={source_id}"
                )
                continue

            filtered_expressions.append((situation, style))

        return filtered_expressions

    async def _upsert_expression_record(
        self,
        situation: str,
        style: str,
        current_time: float,
    ) -> None:
        # 检查是否有相似的 situation（相似度 >= 0.75，检查 content_list）
        # 完全匹配（相似度 == 1.0）和相似匹配（相似度 >= 0.75）统一处理
        expr_obj, similarity = await self._find_similar_situation_expression(situation, similarity_threshold=0.75)

        if expr_obj:
            # 根据相似度决定是否使用 LLM 总结
            # 完全匹配（相似度 == 1.0）时不总结，相似匹配时总结
            use_llm_summary = similarity < 1.0
            await self._update_existing_expression(
                expr_obj=expr_obj,
                situation=situation,
                current_time=current_time,
                use_llm_summary=use_llm_summary,
            )
            return

        # 没有找到匹配的记录，创建新记录
        await self._create_expression_record(
            situation=situation,
            style=style,
            current_time=current_time,
        )

    async def _create_expression_record(
        self,
        situation: str,
        style: str,
        current_time: float,
    ) -> None:
        content_list = [situation]
        # 创建新记录时，直接使用原始的 situation，不进行总结
        formatted_situation = situation

        Expression.create(
            situation=formatted_situation,
            style=style,
            content_list=json.dumps(content_list, ensure_ascii=False),
            count=1,
            last_active_time=current_time,
            chat_id=self.chat_id,
            create_date=current_time,
        )

    async def _update_existing_expression(
        self,
        expr_obj: Expression,
        situation: str,
        current_time: float,
        use_llm_summary: bool = True,
    ) -> None:
        """
        更新现有 Expression 记录（situation 完全匹配或相似的情况）
        将新的 situation 添加到 content_list，不合并 style
        
        Args:
            use_llm_summary: 是否使用 LLM 进行总结，完全匹配时为 False，相似匹配时为 True
        """
        # 更新 content_list（添加新的 situation）
        content_list = self._parse_content_list(expr_obj.content_list)
        content_list.append(situation)
        expr_obj.content_list = json.dumps(content_list, ensure_ascii=False)

        # 更新其他字段
        expr_obj.count = (expr_obj.count or 0) + 1
        expr_obj.checked = False  # count 增加时重置 checked 为 False
        expr_obj.last_active_time = current_time

        if use_llm_summary:
            # 相似匹配时，使用 LLM 重新组合 situation
            new_situation = await self._compose_situation_text(
                content_list=content_list,
                fallback=expr_obj.situation,
            )
            expr_obj.situation = new_situation

        expr_obj.save()

        # count 增加后，立即进行一次检查
        await self._check_expression_immediately(expr_obj)

    def _parse_content_list(self, stored_list: Optional[str]) -> List[str]:
        if not stored_list:
            return []
        try:
            data = json.loads(stored_list)
        except json.JSONDecodeError:
            return []
        return [str(item) for item in data if isinstance(item, str)] if isinstance(data, list) else []

    async def _find_similar_situation_expression(self, situation: str, similarity_threshold: float = 0.75) -> Tuple[Optional[Expression], float]:
        """
        查找具有相似 situation 的 Expression 记录
        检查 content_list 中的每一项
        
        Args:
            situation: 要查找的 situation
            similarity_threshold: 相似度阈值，默认 0.75
            
        Returns:
            Tuple[Optional[Expression], float]: 
                - 找到的最相似的 Expression 对象，如果没有找到则返回 None
                - 相似度值（如果找到匹配，范围在 similarity_threshold 到 1.0 之间）
        """
        # 查询同一 chat_id 的所有记录
        all_expressions = Expression.select().where(Expression.chat_id == self.chat_id)
        
        best_match = None
        best_similarity = 0.0
        
        for expr in all_expressions:
            # 检查 content_list 中的每一项
            content_list = self._parse_content_list(expr.content_list)
            for existing_situation in content_list:
                similarity = calculate_similarity(situation, existing_situation)
                if similarity >= similarity_threshold and similarity > best_similarity:
                    best_similarity = similarity
                    best_match = expr
        
        if best_match:
            logger.debug(f"找到相似的 situation: 相似度={best_similarity:.3f}, 现有='{best_match.situation}', 新='{situation}'")
        
        return best_match, best_similarity

    async def _compose_situation_text(self, content_list: List[str], fallback: str = "") -> str:
        sanitized = [c.strip() for c in content_list if c.strip()]
        if not sanitized:
            return fallback

        prompt = (
            "请阅读以下多个聊天情境描述，并将它们概括成一句简短的话，"
            "长度不超过20个字，保留共同特点：\n"
            f"{chr(10).join(f'- {s}' for s in sanitized[-10:])}\n只输出概括内容。"
        )

        try:
            summary, _ = await self.summary_model.generate_response_async(prompt, temperature=0.2)
            summary = summary.strip()
            if summary:
                return summary
        except Exception as e:
            logger.error(f"概括表达情境失败: {e}")
        return "/".join(sanitized) if sanitized else fallback

    async def _init_check_model(self) -> None:
        """初始化检查用的 LLM 实例"""
        if self.check_model is None:
            try:
                self.check_model = LLMRequest(
                    model_set=model_config.model_task_config.tool_use,
                    request_type="expression.check"
                )
                logger.debug("检查用 LLM 实例初始化成功")
            except Exception as e:
                logger.error(f"创建检查用 LLM 实例失败: {e}")

    async def _check_expression_immediately(self, expr_obj: Expression) -> None:
        """
        立即检查表达方式（在 count 增加后调用）
        
        Args:
            expr_obj: 要检查的表达方式对象
        """
        try:
            # 检查是否启用自动检查
            if not global_config.expression.expression_self_reflect:
                logger.debug("表达方式自动检查未启用，跳过立即检查")
                return

            # 初始化检查用的 LLM
            await self._init_check_model()
            if self.check_model is None:
                logger.warning("检查用 LLM 实例初始化失败，跳过立即检查")
                return

            # 执行 LLM 评估
            suitable, reason, error = await single_expression_check(
                expr_obj.situation,
                expr_obj.style
            )

            # 更新数据库
            expr_obj.checked = True
            expr_obj.rejected = not suitable  # 通过则 rejected=False，不通过则 rejected=True
            expr_obj.save()

            status = "通过" if suitable else "不通过"
            logger.info(
                f"表达方式立即检查完成 [ID: {expr_obj.id}] - {status} | "
                f"Situation: {expr_obj.situation[:30]}... | "
                f"Style: {expr_obj.style[:30]}... | "
                f"Reason: {reason[:50] if reason else '无'}..."
            )

            if error:
                logger.warning(f"表达方式立即检查时出现错误 [ID: {expr_obj.id}]: {error}")

        except Exception as e:
            logger.error(f"立即检查表达方式失败 [ID: {expr_obj.id}]: {e}", exc_info=True)
            # 检查失败时，保持 checked=False，等待后续自动检查任务处理

    def _check_cached_jargons_in_messages(self, messages: List[Any]) -> List[Tuple[str, str]]:
        """
        检查缓存中的 jargon 是否出现在 messages 中
        
        Args:
            messages: 消息列表
            
        Returns:
            List[Tuple[str, str]]: 匹配到的黑话条目列表，每个元素是 (content, source_id)
        """
        if not messages:
            return []
        
        # 获取 jargon_miner 实例
        jargon_miner = miner_manager.get_miner(self.chat_id)
        
        # 获取缓存中的所有 jargon
        cached_jargons = jargon_miner.get_cached_jargons()
        if not cached_jargons:
            return []
        
        matched_entries: List[Tuple[str, str]] = []
        
        # 遍历 messages，检查缓存中的 jargon 是否出现
        for i, msg in enumerate(messages):
            # 跳过机器人自己的消息
            if is_bot_message(msg):
                continue
            
            # 获取消息文本
            msg_text = (
                getattr(msg, "processed_plain_text", None) or 
                ""
            ).strip()
            
            if not msg_text:
                continue
            
            # 检查每个缓存中的 jargon 是否出现在消息文本中
            for jargon in cached_jargons:
                if not jargon or not jargon.strip():
                    continue
                
                jargon_content = jargon.strip()
                
                # 使用正则匹配，考虑单词边界（类似 jargon_explainer 中的逻辑）
                pattern = re.escape(jargon_content)
                # 对于中文，使用更宽松的匹配；对于英文/数字，使用单词边界
                if re.search(r"[\u4e00-\u9fff]", jargon_content):
                    # 包含中文，使用更宽松的匹配
                    search_pattern = pattern
                else:
                    # 纯英文/数字，使用单词边界
                    search_pattern = r"\b" + pattern + r"\b"
                
                if re.search(search_pattern, msg_text, re.IGNORECASE):
                    # 找到匹配，构建条目（source_id 从 1 开始，因为 build_anonymous_messages 的编号从 1 开始）
                    source_id = str(i + 1)
                    matched_entries.append((jargon_content, source_id))
        
        return matched_entries

    async def _process_jargon_entries(self, jargon_entries: List[Tuple[str, str]], messages: List[Any]) -> None:
        """
        处理从 expression learner 提取的黑话条目，路由到 jargon_miner

        Args:
            jargon_entries: 黑话条目列表，每个元素是 (content, source_id)
            messages: 消息列表，用于构建上下文
        """
        if not jargon_entries or not messages:
            return

        # 获取 jargon_miner 实例
        jargon_miner = miner_manager.get_miner(self.chat_id)

        # 构建黑话条目格式，与 jargon_miner.run_once 中的格式一致
        entries: List[Dict[str, List[str]]] = []

        for content, source_id in jargon_entries:
            content = content.strip()
            if not content:
                continue

            # 过滤掉包含 SELF 的黑话，不学习
            if "SELF" in content:
                logger.info(f"跳过包含 SELF 的黑话: {content}")
                continue

            # 检查是否包含机器人名称
            if contains_bot_self_name(content):
                logger.info(f"跳过包含机器人昵称/别名的黑话: {content}")
                continue

            # 解析 source_id
            source_id_str = (source_id or "").strip()
            if not source_id_str.isdigit():
                logger.warning(f"黑话条目 source_id 无效: content={content}, source_id={source_id_str}")
                continue

            # build_anonymous_messages 的编号从 1 开始
            line_index = int(source_id_str) - 1
            if line_index < 0 or line_index >= len(messages):
                logger.warning(f"黑话条目 source_id 超出范围: content={content}, source_id={source_id_str}")
                continue

            # 检查是否是机器人自己的消息
            target_msg = messages[line_index]
            if is_bot_message(target_msg):
                logger.info(f"跳过引用机器人自身消息的黑话: content={content}, source_id={source_id_str}")
                continue

            # 构建上下文段落
            context_paragraph = build_context_paragraph(messages, line_index)
            if not context_paragraph:
                logger.warning(f"黑话条目上下文为空: content={content}, source_id={source_id_str}")
                continue

            entries.append({"content": content, "raw_content": [context_paragraph]})

        if not entries:
            return

        # 调用 jargon_miner 处理这些条目
        await jargon_miner.process_extracted_entries(entries)


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
