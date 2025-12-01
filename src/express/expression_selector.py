import json
import time
import hashlib

from typing import List, Dict, Optional, Any, Tuple
from json_repair import repair_json

from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config, model_config
from src.common.logger import get_logger
from src.common.database.database_model import Expression
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.express.express_utils import weighted_sample

logger = get_logger("expression_selector")


def init_prompt():
    expression_evaluation_prompt = """{chat_observe_info}

你的名字是{bot_name}{target_message}
{reply_reason_block}

以下是可选的表达情境：
{all_situations}

请你分析聊天内容的语境、情绪、话题类型，从上述情境中选择最适合当前聊天情境的，最多{max_num}个情境。
考虑因素包括：
1.聊天的情绪氛围（轻松、严肃、幽默等）
2.话题类型（日常、技术、游戏、情感等）
3.情境与当前语境的匹配度
{target_message_extra_block}

请以JSON格式输出，只需要输出选中的情境编号：
例如：
{{
    "selected_situations": [2, 3, 5, 7, 19]
}}

请严格按照JSON格式输出，不要包含其他内容：
"""
    Prompt(expression_evaluation_prompt, "expression_evaluation_prompt")


class ExpressionSelector:
    def __init__(self):
        self.llm_model = LLMRequest(
            model_set=model_config.model_task_config.utils_small, request_type="expression.selector"
        )

    def can_use_expression_for_chat(self, chat_id: str) -> bool:
        """
        检查指定聊天流是否允许使用表达

        Args:
            chat_id: 聊天流ID

        Returns:
            bool: 是否允许使用表达
        """
        try:
            use_expression, _, _ = global_config.expression.get_expression_config_for_chat(chat_id)
            return use_expression
        except Exception as e:
            logger.error(f"检查表达使用权限失败: {e}")
            return False

    @staticmethod
    def _parse_stream_config_to_chat_id(stream_config_str: str) -> Optional[str]:
        """解析'platform:id:type'为chat_id（与get_stream_id一致）"""
        try:
            parts = stream_config_str.split(":")
            if len(parts) != 3:
                return None
            platform = parts[0]
            id_str = parts[1]
            stream_type = parts[2]
            is_group = stream_type == "group"
            if is_group:
                components = [platform, str(id_str)]
            else:
                components = [platform, str(id_str), "private"]
            key = "_".join(components)
            return hashlib.md5(key.encode()).hexdigest()
        except Exception:
            return None

    def get_related_chat_ids(self, chat_id: str) -> List[str]:
        """根据expression_groups配置，获取与当前chat_id相关的所有chat_id（包括自身）"""
        groups = global_config.expression.expression_groups

        # 检查是否存在全局共享组（包含"*"的组）
        global_group_exists = any("*" in group for group in groups)

        if global_group_exists:
            # 如果存在全局共享组，则返回所有可用的chat_id
            all_chat_ids = set()
            for group in groups:
                for stream_config_str in group:
                    if chat_id_candidate := self._parse_stream_config_to_chat_id(stream_config_str):
                        all_chat_ids.add(chat_id_candidate)
            return list(all_chat_ids) if all_chat_ids else [chat_id]

        # 否则使用现有的组逻辑
        for group in groups:
            group_chat_ids = []
            for stream_config_str in group:
                if chat_id_candidate := self._parse_stream_config_to_chat_id(stream_config_str):
                    group_chat_ids.append(chat_id_candidate)
            if chat_id in group_chat_ids:
                return group_chat_ids
        return [chat_id]

    def _random_expressions(self, chat_id: str, total_num: int) -> List[Dict[str, Any]]:
        """
        随机选择表达方式

        Args:
            chat_id: 聊天室ID
            total_num: 需要选择的数量

        Returns:
            List[Dict[str, Any]]: 随机选择的表达方式列表
        """
        try:
            # 支持多chat_id合并抽选
            related_chat_ids = self.get_related_chat_ids(chat_id)

            # 优化：一次性查询所有相关chat_id的表达方式，排除 rejected=1 的表达
            style_query = Expression.select().where(
                (Expression.chat_id.in_(related_chat_ids)) & (~Expression.rejected)
            )

            style_exprs = [
                {
                    "id": expr.id,
                    "situation": expr.situation,
                    "style": expr.style,
                    "last_active_time": expr.last_active_time,
                    "source_id": expr.chat_id,
                    "create_date": expr.create_date if expr.create_date is not None else expr.last_active_time,
                    "count": expr.count if getattr(expr, "count", None) is not None else 1,
                    "checked": expr.checked if getattr(expr, "checked", None) is not None else False,
                }
                for expr in style_query
            ]

            # 随机抽样
            if style_exprs:
                selected_style = weighted_sample(style_exprs, total_num)
            else:
                selected_style = []

            return selected_style

        except Exception as e:
            logger.error(f"随机选择表达方式失败: {e}")
            return []

    async def select_suitable_expressions(
        self,
        chat_id: str,
        chat_info: str,
        max_num: int = 10,
        target_message: Optional[str] = None,
        reply_reason: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[int]]:
        """
        选择适合的表达方式（使用classic模式：随机选择+LLM选择）

        Args:
            chat_id: 聊天流ID
            chat_info: 聊天内容信息
            max_num: 最大选择数量
            target_message: 目标消息内容
            reply_reason: planner给出的回复理由

        Returns:
            Tuple[List[Dict[str, Any]], List[int]]: 选中的表达方式列表和ID列表
        """
        # 检查是否允许在此聊天流中使用表达
        if not self.can_use_expression_for_chat(chat_id):
            logger.debug(f"聊天流 {chat_id} 不允许使用表达，返回空列表")
            return [], []

        # 使用classic模式（随机选择+LLM选择）
        logger.debug(f"使用classic模式为聊天流 {chat_id} 选择表达方式")
        return await self._select_expressions_classic(chat_id, chat_info, max_num, target_message, reply_reason)

    async def _select_expressions_classic(
        self,
        chat_id: str,
        chat_info: str,
        max_num: int = 10,
        target_message: Optional[str] = None,
        reply_reason: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[int]]:
        """
        classic模式：随机选择+LLM选择

        Args:
            chat_id: 聊天流ID
            chat_info: 聊天内容信息
            max_num: 最大选择数量
            target_message: 目标消息内容
            reply_reason: planner给出的回复理由

        Returns:
            Tuple[List[Dict[str, Any]], List[int]]: 选中的表达方式列表和ID列表
        """
        try:
            # 1. 使用随机抽样选择表达方式
            style_exprs = self._random_expressions(chat_id, 20)

            if len(style_exprs) < 10:
                logger.info(f"聊天流 {chat_id} 表达方式正在积累中")
                return [], []

            # 2. 构建所有表达方式的索引和情境列表
            all_expressions: List[Dict[str, Any]] = []
            all_situations: List[str] = []

            # 添加style表达方式
            for expr in style_exprs:
                expr = expr.copy()
                all_expressions.append(expr)
                all_situations.append(f"{len(all_expressions)}.当 {expr['situation']} 时，使用 {expr['style']}")

            if not all_expressions:
                logger.warning("没有找到可用的表达方式")
                return [], []

            all_situations_str = "\n".join(all_situations)

            if target_message:
                target_message_str = f"，现在你想要对这条消息进行回复：“{target_message}”"
                target_message_extra_block = "4.考虑你要回复的目标消息"
            else:
                target_message_str = ""
                target_message_extra_block = ""

            chat_context = f"以下是正在进行的聊天内容：{chat_info}"

            # 构建reply_reason块
            if reply_reason:
                reply_reason_block = f"你的回复理由是：{reply_reason}"
                chat_context = ""
            else:
                reply_reason_block = ""

            # 3. 构建prompt（只包含情境，不包含完整的表达方式）
            prompt = (await global_prompt_manager.get_prompt_async("expression_evaluation_prompt")).format(
                bot_name=global_config.bot.nickname,
                chat_observe_info=chat_context,
                all_situations=all_situations_str,
                max_num=max_num,
                target_message=target_message_str,
                target_message_extra_block=target_message_extra_block,
                reply_reason_block=reply_reason_block,
            )

            # 4. 调用LLM
            content, (reasoning_content, model_name, _) = await self.llm_model.generate_response_async(prompt=prompt)

            # print(prompt)

            if not content:
                logger.warning("LLM返回空结果")
                return [], []

            # 5. 解析结果
            result = repair_json(content)
            if isinstance(result, str):
                result = json.loads(result)

            if not isinstance(result, dict) or "selected_situations" not in result:
                logger.error("LLM返回格式错误")
                logger.info(f"LLM返回结果: \n{content}")
                return [], []

            selected_indices = result["selected_situations"]

            # 根据索引获取完整的表达方式
            valid_expressions: List[Dict[str, Any]] = []
            selected_ids = []
            for idx in selected_indices:
                if isinstance(idx, int) and 1 <= idx <= len(all_expressions):
                    expression = all_expressions[idx - 1]  # 索引从1开始
                    selected_ids.append(expression["id"])
                    valid_expressions.append(expression)

            # 对选中的所有表达方式，更新last_active_time
            if valid_expressions:
                self.update_expressions_last_active_time(valid_expressions)

            logger.debug(f"从{len(all_expressions)}个情境中选择了{len(valid_expressions)}个")
            return valid_expressions, selected_ids

        except Exception as e:
            logger.error(f"classic模式处理表达方式选择时出错: {e}")
            return [], []

    def update_expressions_last_active_time(self, expressions_to_update: List[Dict[str, Any]]):
        """对一批表达方式更新last_active_time"""
        if not expressions_to_update:
            return
        updates_by_key = {}
        for expr in expressions_to_update:
            source_id: str = expr.get("source_id")  # type: ignore
            situation: str = expr.get("situation")  # type: ignore
            style: str = expr.get("style")  # type: ignore
            if not source_id or not situation or not style:
                logger.warning(f"表达方式缺少必要字段，无法更新: {expr}")
                continue
            key = (source_id, situation, style)
            if key not in updates_by_key:
                updates_by_key[key] = expr
        for chat_id, situation, style in updates_by_key:
            query = Expression.select().where(
                (Expression.chat_id == chat_id) & (Expression.situation == situation) & (Expression.style == style)
            )
            if query.exists():
                expr_obj = query.get()
                expr_obj.last_active_time = time.time()
                expr_obj.save()
                logger.debug("表达方式激活: 更新last_active_time in db")


init_prompt()

try:
    expression_selector = ExpressionSelector()
except Exception as e:
    logger.error(f"ExpressionSelector初始化失败: {e}")
