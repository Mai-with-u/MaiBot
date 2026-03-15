from datetime import datetime
from sqlmodel import select
from typing import TYPE_CHECKING, List, Optional, Tuple

import asyncio
import difflib
import json
import re

from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config, global_config
from src.prompt.prompt_manager import prompt_manager
from src.common.logger import get_logger
from src.common.database.database_model import Expression
from src.common.database.database import get_db_session
from src.common.data_models.expression_data_model import MaiExpression
from src.chat.utils.utils import is_bot_self
from src.common.utils.utils_message import MessageUtils

from .expression_utils import check_expression_suitability, parse_expression_response

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage
    from .jargon_miner import JargonMiner, JargonEntry


logger = get_logger("expressor")

# TODO: 重构完LLM相关内容后，替换成新的模型调用方式
express_learn_model = LLMRequest(model_set=model_config.model_task_config.utils, request_type="expression.learner")
summary_model = LLMRequest(model_set=model_config.model_task_config.tool_use, request_type="expression.summary")
check_model = LLMRequest(model_set=model_config.model_task_config.tool_use, request_type="expression.check")


class ExpressionLearner:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

        # 学习锁，防止并发执行学习任务
        self._learning_lock = asyncio.Lock()

        # 消息缓存
        self._messages_cache: List["SessionMessage"] = []

    def add_messages(self, messages: List["SessionMessage"]) -> None:
        """添加消息到缓存"""
        self._messages_cache.extend(messages)

    def get_cache_size(self) -> int:
        """获取当前消息缓存的大小"""
        return len(self._messages_cache)

    async def learn(self, jargon_miner: Optional["JargonMiner"] = None):
        """学习主流程"""
        if not self._messages_cache:
            logger.debug("没有消息可供学习，跳过学习过程")
            return

        # 构建可读消息
        readable_message, _, _ = await MessageUtils.build_readable_message(
            self._messages_cache,
            anonymize=True,
            show_lineno=True,
            extract_pictures=True,
            replace_bot_name=True,
            target_bot_name="SELF",
        )

        # 准备提示词
        prompt_template = prompt_manager.get_prompt("learn_style")
        prompt_template.add_context("bot_name", global_config.bot.nickname)
        prompt_template.add_context("chat_str", readable_message)
        prompt = await prompt_manager.render_prompt(prompt_template)

        # 调用 LLM 学习表达方式
        try:
            response, _ = await express_learn_model.generate_response_async(prompt, temperature=0.3)
        except Exception as e:
            logger.error(f"学习表达方式失败，模型生成出错：{e}")
            return

        # 解析 LLM 返回的表达方式列表和黑话列表（包含来源行编号）
        expressions: List[Tuple[str, str, str]]
        jargon_entries: List[Tuple[str, str]]  # (content, source_id)
        expressions, jargon_entries = parse_expression_response(response)

        # 从缓存中检查 jargon 是否出现在 messages 中
        if cached_jargon_entries := self._check_cached_jargons_in_messages(jargon_miner):
            # 合并缓存中的 jargon 条目（去重：如果 content 已存在则跳过）
            existing_contents = {content for content, _ in jargon_entries}
            for content, source_id in cached_jargon_entries:
                if content not in existing_contents:
                    jargon_entries.append((content, source_id))
                    existing_contents.add(content)
                    logger.info(f"从缓存中检查到黑话：{content}")

        # 检查表达方式数量，如果超过 20 个则放弃本次表达学习
        if len(expressions) > 20:
            logger.info(f"表达方式提取数量超过 20 个（实际{len(expressions)}个），放弃本次表达学习")
            expressions = []

        # 检查黑话数量，如果超过 30 个则放弃本次黑话学习
        if len(jargon_entries) > 30:
            logger.info(f"黑话提取数量超过 30 个（实际{len(jargon_entries)}个），放弃本次黑话学习")
            jargon_entries = []

        # 处理黑话条目，路由到 jargon_miner（即使没有表达方式也要处理黑话）
        # TODO: 检测是否开启了
        if jargon_entries:
            await self._process_jargon_entries(jargon_entries, jargon_miner)

        # 如果没有表达方式，直接返回
        if not expressions:
            logger.info("解析后没有可用的表达方式")
            return

        logger.info(f"学习的 expressions: {expressions}")
        logger.info(f"学习的 jargon_entries: {jargon_entries}")

        # 过滤表达方式，根据 source_id 溯源并应用各种过滤规则
        learnt_expressions = self._filter_expressions(expressions)

        if not learnt_expressions:
            logger.info("没有学习到表达风格")
            return

        # 展示学到的表达方式
        learnt_expressions_str = "\n".join(f"{situation}->{style}" for situation, style in learnt_expressions)
        logger.info(f"在 {self.session_id} 学习到表达风格:\n{learnt_expressions_str}")

        # 存储到数据库 Expression 表
        for situation, style in learnt_expressions:
            await self._upsert_expression_to_db(situation, style)

    # ====== 黑话相关 ======
    def _check_cached_jargons_in_messages(self, jargon_miner: Optional["JargonMiner"] = None) -> List[Tuple[str, str]]:
        """
        检查缓存中的 jargon 是否出现在 messages 中

        Args:
            jargon_miner: JargonMiner 实例，用于获取缓存的黑话

        Returns:
            List[Tuple[str, str]]: 匹配到的黑话条目列表，每个元素是 (content, source_id)
        """
        if not jargon_miner:
            return []

        # 获取缓存的所有 jargon 实例
        cached_jargons = jargon_miner.get_cached_jargons()
        if not cached_jargons:
            return []

        matched_entries: List[Tuple[str, str]] = []

        for i, msg in enumerate(self._messages_cache):
            # 跳过机器人自己的消息
            if is_bot_self(msg.platform, msg.message_info.user_info.user_id):
                continue

            # 获取消息文本
            msg_text = (msg.processed_plain_text or "").strip()

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
                    # 找到匹配，构建条目（source_id 从 1 开始，因为 build_readable_message 的编号从 1 开始）
                    source_id = str(i + 1)
                    matched_entries.append((jargon_content, source_id))

        return matched_entries

    async def _process_jargon_entries(
        self, jargon_entries: List[Tuple[str, str]], jargon_miner: Optional["JargonMiner"] = None
    ):
        """
        处理从 expression learner 提取的黑话条目，路由到 jargon_miner

        Args:
            jargon_entries: 黑话条目列表，每个元素是 (content, source_id)
            jargon_miner: JargonMiner 实例
        """
        if not jargon_entries or not self._messages_cache:
            return

        if not jargon_miner:
            logger.warning("缺少 JargonMiner 实例，无法处理黑话条目")
            return

        # 构建黑话条目格式
        entries: List["JargonEntry"] = []

        for content, source_id in jargon_entries:
            content = content.strip()
            if not content:
                continue

            # 过滤掉包含 SELF 的黑话，不学习
            if "SELF" in content:
                logger.info(f"跳过包含 SELF 的黑话：{content}")
                continue

            # TODO: 多平台兼容
            # 检查是否包含机器人名称
            bot_nickname = global_config.bot.nickname
            if bot_nickname and bot_nickname in content:
                logger.info(f"跳过包含机器人昵称的黑话：{content}")
                continue

            # 解析 source_id
            if not source_id.isdigit():
                logger.warning(f"黑话条目 source_id 无效：content={content}, source_id={source_id}")
                continue

            # build_readable_message 的编号从 1 开始
            line_index = int(source_id) - 1
            if line_index < 0 or line_index >= len(self._messages_cache):
                logger.warning(f"黑话条目 source_id 超出范围：content={content}, source_id={source_id}")
                continue

            # 检查是否是机器人自己的消息
            target_msg = self._messages_cache[line_index]
            if is_bot_self(target_msg.platform, target_msg.message_info.user_info.user_id):
                logger.info(f"跳过引用机器人自身消息的黑话：content={content}, source_id={source_id}")
                continue

            # 构建上下文段落（取前后各 3 条消息）
            start_idx = max(0, line_index - 3)
            end_idx = min(len(self._messages_cache), line_index + 4)
            context_msgs = self._messages_cache[start_idx:end_idx]

            context_paragraph = "\n".join(
                [f"[{i + 1}] {msg.processed_plain_text or ''}" for i, msg in enumerate(context_msgs)]
            )

            if not context_paragraph:
                logger.warning(f"黑话条目上下文为空：content={content}, source_id={source_id}")
                continue

            entries.append({"content": content, "raw_content": {context_paragraph}})  # type: ignore

        if not entries:
            return

        await jargon_miner.process_extracted_entries(entries)
        logger.info(f"成功处理 {len(entries)} 个黑话条目")

    # ====== 过滤方法 ======
    def _filter_expressions(self, expressions: List[Tuple[str, str, str]]) -> List[Tuple[str, str]]:
        """
        过滤表达方式，移除不符合条件的条目

        Args:
            expressions: 表达方式列表，每个元素是 (situation, style, source_id)

        Returns:
            过滤后的表达方式列表，每个元素是 (situation, style)
        """
        filtered_expressions: List[Tuple[str, str]] = []

        # 准备机器人名称集合（用于过滤 style 与机器人名称重复的表达）
        # TODO: 完善这里的机器人名称检测逻辑（考虑别名、不同平台的名称等）
        banned_names: set[str] = set()
        bot_nickname = global_config.bot.nickname
        if bot_nickname:
            banned_names.add(bot_nickname)
        alias_names = global_config.bot.alias_names or []
        for alias in alias_names:
            if alias_stripped := alias.strip():
                banned_names.add(alias_stripped)
        banned_casefold = {name.casefold() for name in banned_names if name}

        for situation, style, source_id in expressions:
            source_id_str = source_id.strip()
            if not source_id_str.isdigit():
                continue  # 无效的来源行编号，跳过
            line_index = int(source_id_str) - 1  # build_readable_message 的编号从 1 开始
            if line_index < 0 or line_index >= len(self._messages_cache):
                continue  # 超出范围，跳过
            # 当前行的原始消息
            current_msg = self._messages_cache[line_index]
            # 过滤掉从 bot 自己发言中提取到的表达方式
            if is_bot_self(current_msg.platform, current_msg.message_info.user_info.user_id):
                continue
            # 过滤掉无上下文的表达方式
            context = (current_msg.processed_plain_text or "").strip()
            if not context:
                continue
            # 过滤掉包含 SELF 的内容（不学习）
            if "SELF" in situation or "SELF" in style or "SELF" in context:
                logger.info(f"跳过包含 SELF 的表达方式：situation={situation}, style={style}, source_id={source_id}")
                continue
            # 过滤掉 style 与机器人名称/昵称重复的表达
            normalized_style = (style or "").strip()
            if normalized_style and normalized_style.casefold() in banned_casefold:
                logger.debug(
                    f"跳过 style 与机器人名称重复的表达方式：situation={situation}, style={style}, source_id={source_id}"
                )
                continue
            # 过滤掉包含 "[表情" 的内容
            if "[表情包" in situation or "[表情包" in style or "[表情包" in context:
                logger.info(f"跳过包含表情标记的表达方式：situation={situation}, style={style}, source_id={source_id}")
                continue
            # 过滤掉包含 "[图片" 的内容
            if "[图片" in situation or "[图片" in style or "[图片" in context:
                logger.info(f"跳过包含图片标记的表达方式：situation={situation}, style={style}, source_id={source_id}")
                continue

            filtered_expressions.append((situation, style))

        return filtered_expressions

    # ====== DB 操作相关 ======
    async def _upsert_expression_to_db(self, situation: str, style: str):
        expr, similarity = self._find_similar_expression(situation) or (None, 0)
        if expr:
            # 根据相似度决定是否使用 LLM 总结
            # 完全匹配（相似度 == 1.0）时不总结，相似匹配时总结
            use_llm_summary = similarity < 1.0
            await self._update_existing_expression(expr, situation, use_llm_summary=use_llm_summary)
            return
        # 没有找到匹配的记录，创建新记录
        self._create_expression(situation, style)

    def _create_expression(self, situation: str, style: str):
        content_list = [situation]
        try:
            with get_db_session() as db:
                new_expr = Expression(
                    situation=situation,
                    style=style,
                    content_list=json.dumps(content_list),
                    count=1,
                    session_id=self.session_id,
                    last_active_time=datetime.now(),
                )
                db.add(new_expr)
        except Exception as e:
            logger.error(f"创建表达方式失败: {e}")

    async def _update_existing_expression(self, expr: "MaiExpression", situation: str, use_llm_summary: bool = True):
        expr.content.append(situation)
        expr.count += 1
        expr.checked = False  # count 增加时重置 checked 为 False
        expr.last_active_time = datetime.now()

        if use_llm_summary:
            # 相似匹配时，使用 LLM 重新组合 situation
            new_situation = await self._compose_situation_text(expr.content)
            if new_situation:
                expr.situation = new_situation

        try:
            with get_db_session() as session:
                if expr.item_id is None:
                    raise ValueError("表达方式对象缺少 item_id，无法更新数据库记录")
                statement = select(Expression).filter_by(id=expr.item_id).limit(1)
                if db_expr := session.exec(statement).first():
                    db_expr.content_list = json.dumps(expr.content)
                    db_expr.count = expr.count
                    db_expr.checked = expr.checked
                    db_expr.last_active_time = expr.last_active_time
                    db_expr.situation = expr.situation  # 更新 situation
                    session.add(db_expr)
                else:
                    logger.warning(f"表达方式 ID {expr.item_id} 在数据库中未找到，无法更新")
        except Exception as e:
            logger.error(f"更新表达方式失败: {e}")

        # count 增加后，立即进行一次检查
        await self._check_expression(expr)

    # ====== 概括方法 ======
    async def _compose_situation_text(self, content_list: List[str]) -> Optional[str]:
        texts = [c.strip() for c in content_list if c.strip()]
        if not texts:
            return None
        description = "\n".join(f"- {s}" for s in texts[-10:])  # 只取最近10条进行概括
        prompt = (
            "请阅读以下多个聊天情境描述，并将它们概括成一句简短的话，长度不超过20个字，保留共同特点：\n"
            f"{description}\n"
            "只输出概括内容。"
        )
        try:
            summary, _ = await summary_model.generate_response_async(prompt, temperature=0.2)
            if summary := summary.strip():
                return summary
        except Exception as e:
            logger.error(f"使用 LLM 生成表达方式概括失败: {e}")
        return None

    async def _check_expression(self, expr: "MaiExpression"):
        """
        检查表达方式（在 count 增加后调用）

        Args:
            expr (MaiExpression): 要检查的表达方式对象
        """
        if not global_config.expression.expression_self_reflect:
            logger.debug("表达方式自我反思功能未启用，跳过检查")
            return

        suitable, reason, error = await check_expression_suitability(expr.situation, expr.style)
        if error:
            logger.error(f"检查表达方式时发生错误: {error}")
            return
        expr.checked = True
        expr.rejected = not suitable

        try:
            with get_db_session() as session:
                statement = select(Expression).filter_by(id=expr.item_id).limit(1)
                if db_expr := session.exec(statement).first():
                    db_expr.checked = expr.checked
                    db_expr.rejected = expr.rejected
                    session.add(db_expr)
                else:
                    logger.warning(f"表达方式 ID {expr.item_id} 在数据库中未找到，无法更新检查结果")
        except Exception as e:
            logger.error(f"更新表达方式检查结果失败: {e}")

        status = "通过" if suitable else "不通过"
        logger.info(
            f"表达方式检查完成 [ID: {expr.item_id}] - {status} | "
            f"Situation: {expr.situation[:30]}... | "
            f"Style: {expr.style[:30]}... | "
            f"Reason: {reason[:50] if reason else '无'}..."
        )

    def _find_similar_expression(
        self, situation: str, similarity_threshold: float = 0.75
    ) -> Optional[Tuple[MaiExpression, float]]:
        """在数据库中查找相似的表达方式"""
        try:
            with get_db_session() as session:
                statement = select(Expression).filter_by(session_id=self.session_id)
                expressions = session.exec(statement).all()

            best_match: Optional[Expression] = None
            best_similarity = 0.0

            for expr in expressions:
                content_list = json.loads(expr.content_list)
                for situation in content_list:
                    similarity = difflib.SequenceMatcher(None, situation, expr.situation).ratio()
                    if similarity > similarity_threshold and similarity > best_similarity:
                        best_similarity = similarity
                        best_match = expr
            if best_match:
                logger.debug(f"找到相似表达方式情景 [ID: {best_match.id}]，相似度: {best_similarity:.2f}")
                return MaiExpression.from_db_instance(best_match), best_similarity

        except Exception as e:
            logger.error(f"查找相似表达方式失败: {e}")
        return None
