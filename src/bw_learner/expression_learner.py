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
)
from src.bw_learner.jargon_miner import miner_manager
from json_repair import repair_json


# MAX_EXPRESSION_COUNT = 300

logger = get_logger("expressor")


def init_prompt() -> None:
    learn_style_prompt = """{chat_str}
你的名字是{bot_name},现在请你完成两个提取任务
任务1：请从上面这段群聊中用户的语言风格和说话方式
1. 只考虑文字，不要考虑表情包和图片
2. 不要总结SELF的发言
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
            model_set=model_config.model_task_config.utils_small, request_type="expression.summary"
        )
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
        expressions, jargon_entries = self.parse_expression_response(response)
        expressions = self._filter_self_reference_styles(expressions)

        # 检查表达方式数量，如果超过10个则放弃本次表达学习
        if len(expressions) > 10:
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
            logger.info("过滤后没有可用的表达方式（style 与机器人名称重复）")
            return []

        logger.info(f"学习的prompt: {prompt}")
        logger.info(f"学习的expressions: {expressions}")
        logger.info(f"学习的jargon_entries: {jargon_entries}")
        logger.info(f"学习的response: {response}")

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

            # 过滤掉从bot自己发言中提取到的表达方式
            if is_bot_message(current_msg):
                continue

            context = filter_message_content(current_msg.processed_plain_text or "")
            if not context:
                continue

            filtered_expressions.append((situation, style, context))

        learnt_expressions = filtered_expressions

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

    def parse_expression_response(self, response: str) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str]]]:
        """
        解析 LLM 返回的表达风格总结和黑话 JSON，提取两个列表。

        期望的 JSON 结构：
        [
          {"situation": "AAAAA", "style": "BBBBB", "source_id": "3"},  // 表达方式
          {"content": "词条", "source_id": "12"},  // 黑话
          ...
        ]

        Returns:
            Tuple[List[Tuple[str, str, str]], List[Tuple[str, str]]]:
                第一个列表是表达方式 (situation, style, source_id)
                第二个列表是黑话 (content, source_id)
        """
        if not response:
            return [], []

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
        expressions: List[Tuple[str, str, str]] = []  # (situation, style, source_id)
        jargon_entries: List[Tuple[str, str]] = []  # (content, source_id)

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

                        if char == "\\":
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
                            if char == '"':  # 中文左引号 U+201C
                                result.append('\\"')
                            elif char == '"':  # 中文右引号 U+201D
                                result.append('\\"')
                            else:
                                result.append(char)
                        else:
                            # 不在字符串内，直接添加
                            result.append(char)

                        i += 1

                    return "".join(result)

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

            # 检查是否是表达方式条目（有 situation 和 style）
            situation = str(item.get("situation", "")).strip()
            style = str(item.get("style", "")).strip()
            source_id = str(item.get("source_id", "")).strip()

            if situation and style and source_id:
                # 表达方式条目
                expressions.append((situation, style, source_id))
            elif item.get("content"):
                # 黑话条目（有 content 字段）
                content = str(item.get("content", "")).strip()
                source_id = str(item.get("source_id", "")).strip()
                if content and source_id:
                    jargon_entries.append((content, source_id))

        return expressions, jargon_entries

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
