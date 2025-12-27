import json
import time

from typing import List, Dict, Optional, Any, Tuple
from json_repair import repair_json

from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config, model_config
from src.common.logger import get_logger
from src.common.database.database_model import Expression
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.bw_learner.learner_utils import weighted_sample
from src.chat.message_receive.chat_stream import get_chat_manager

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
            model_set=model_config.model_task_config.tool_use, request_type="expression.selector"
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
        """解析'platform:id:type'为chat_id，直接使用 ChatManager 提供的接口"""
        try:
            parts = stream_config_str.split(":")
            if len(parts) != 3:
                return None
            platform = parts[0]
            id_str = parts[1]
            stream_type = parts[2]
            is_group = stream_type == "group"
            # 统一通过 chat_manager 生成 stream_id，避免各处自行实现哈希逻辑
            return get_chat_manager().get_stream_id(platform, str(id_str), is_group=is_group)
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

    def _select_expressions_simple(self, chat_id: str, max_num: int) -> Tuple[List[Dict[str, Any]], List[int]]:
        """
        简单模式：只选择 count > 1 的项目，要求至少有10个才进行选择，随机选5个，不进行LLM选择

        Args:
            chat_id: 聊天流ID
            max_num: 最大选择数量（此参数在此模式下不使用，固定选择5个）

        Returns:
            Tuple[List[Dict[str, Any]], List[int]]: 选中的表达方式列表和ID列表
        """
        try:
            # 支持多chat_id合并抽选
            related_chat_ids = self.get_related_chat_ids(chat_id)

            # 查询所有相关chat_id的表达方式，排除 rejected=1 的，且只选择 count > 1 的
            # 如果 expression_checked_only 为 True，则只选择 checked=True 且 rejected=False 的
            base_conditions = (Expression.chat_id.in_(related_chat_ids)) & (~Expression.rejected) & (Expression.count > 1)
            if global_config.expression.expression_checked_only:
                base_conditions = base_conditions & (Expression.checked)
            style_query = Expression.select().where(base_conditions)

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

            # 要求至少有一定数量的 count > 1 的表达方式才进行“完整简单模式”选择
            min_required = 8
            if len(style_exprs) < min_required:
                # 高 count 样本不足：如果还有候选，就降级为随机选 3 个；如果一个都没有，则直接返回空
                if not style_exprs:
                    logger.info(
                        f"聊天流 {chat_id} 没有满足 count > 1 且未被拒绝的表达方式，简单模式不进行选择"
                    )
                    # 完全没有高 count 样本时，退化为全量随机抽样（不进入LLM流程）
                    fallback_num = min(3, max_num) if max_num > 0 else 3
                    fallback_selected = self._random_expressions(chat_id, fallback_num)
                    if fallback_selected:
                        self.update_expressions_last_active_time(fallback_selected)
                        selected_ids = [expr["id"] for expr in fallback_selected]
                        logger.info(
                            f"聊天流 {chat_id} 使用简单模式降级随机抽选 {len(fallback_selected)} 个表达（无 count>1 样本）"
                        )
                        return fallback_selected, selected_ids
                    return [], []
                logger.info(
                    f"聊天流 {chat_id} count > 1 的表达方式不足 {min_required} 个（实际 {len(style_exprs)} 个），"
                    f"简单模式降级为随机选择 3 个"
                )
                select_count = min(3, len(style_exprs))
            else:
                # 高 count 数量达标时，固定选择 5 个
                select_count = 5
            import random

            selected_style = random.sample(style_exprs, select_count)

            # 更新last_active_time
            if selected_style:
                self.update_expressions_last_active_time(selected_style)

            selected_ids = [expr["id"] for expr in selected_style]
            logger.debug(
                f"think_level=0: 从 {len(style_exprs)} 个 count>1 的表达方式中随机选择了 {len(selected_style)} 个"
            )
            return selected_style, selected_ids

        except Exception as e:
            logger.error(f"简单模式选择表达方式失败: {e}")
            return [], []

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
            # 如果 expression_checked_only 为 True，则只选择 checked=True 且 rejected=False 的
            base_conditions = (Expression.chat_id.in_(related_chat_ids)) & (~Expression.rejected)
            if global_config.expression.expression_checked_only:
                base_conditions = base_conditions & (Expression.checked)
            style_query = Expression.select().where(base_conditions)

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
        think_level: int = 1,
    ) -> Tuple[List[Dict[str, Any]], List[int]]:
        """
        选择适合的表达方式（使用classic模式：随机选择+LLM选择）

        Args:
            chat_id: 聊天流ID
            chat_info: 聊天内容信息
            max_num: 最大选择数量
            target_message: 目标消息内容
            reply_reason: planner给出的回复理由
            think_level: 思考级别，0/1

        Returns:
            Tuple[List[Dict[str, Any]], List[int]]: 选中的表达方式列表和ID列表
        """
        # 检查是否允许在此聊天流中使用表达
        if not self.can_use_expression_for_chat(chat_id):
            logger.debug(f"聊天流 {chat_id} 不允许使用表达，返回空列表")
            return [], []

        # 使用classic模式（随机选择+LLM选择）
        logger.debug(f"使用classic模式为聊天流 {chat_id} 选择表达方式，think_level={think_level}")
        return await self._select_expressions_classic(
            chat_id, chat_info, max_num, target_message, reply_reason, think_level
        )

    async def _select_expressions_classic(
        self,
        chat_id: str,
        chat_info: str,
        max_num: int = 10,
        target_message: Optional[str] = None,
        reply_reason: Optional[str] = None,
        think_level: int = 1,
    ) -> Tuple[List[Dict[str, Any]], List[int]]:
        """
        classic模式：随机选择+LLM选择

        Args:
            chat_id: 聊天流ID
            chat_info: 聊天内容信息
            max_num: 最大选择数量
            target_message: 目标消息内容
            reply_reason: planner给出的回复理由
            think_level: 思考级别，0/1

        Returns:
            Tuple[List[Dict[str, Any]], List[int]]: 选中的表达方式列表和ID列表
        """
        try:
            # think_level == 0: 只选择 count > 1 的项目，随机选10个，不进行LLM选择
            if think_level == 0:
                return self._select_expressions_simple(chat_id, max_num)

            # think_level == 1: 先选高count，再从所有表达方式中随机抽样
            # 1. 获取所有表达方式并分离 count > 1 和 count <= 1 的
            related_chat_ids = self.get_related_chat_ids(chat_id)
            # 如果 expression_checked_only 为 True，则只选择 checked=True 且 rejected=False 的
            base_conditions = (Expression.chat_id.in_(related_chat_ids)) & (~Expression.rejected)
            if global_config.expression.expression_checked_only:
                base_conditions = base_conditions & (Expression.checked)
            style_query = Expression.select().where(base_conditions)

            all_style_exprs = [
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

            # 分离 count > 1 和 count <= 1 的表达方式
            high_count_exprs = [expr for expr in all_style_exprs if (expr.get("count", 1) or 1) > 1]

            # 根据 think_level 设置要求（仅支持 0/1，0 已在上方返回）
            min_high_count = 10
            min_total_count = 10
            select_high_count = 5
            select_random_count = 5

            # 检查数量要求
            # 对于高 count 表达：如果数量不足，不再直接停止，而是仅跳过“高 count 优先选择”
            if len(high_count_exprs) < min_high_count:
                logger.info(
                    f"聊天流 {chat_id} count > 1 的表达方式不足 {min_high_count} 个（实际 {len(high_count_exprs)} 个），"
                    f"将跳过高 count 优先选择，仅从全部表达中随机抽样"
                )
                high_count_valid = False
            else:
                high_count_valid = True

            # 总量不足仍然直接返回，避免样本过少导致选择质量过低
            if len(all_style_exprs) < min_total_count:
                logger.info(
                    f"聊天流 {chat_id} 总表达方式不足 {min_total_count} 个（实际 {len(all_style_exprs)} 个），不进行选择"
                )
                return [], []

            # 先选取高count的表达方式（如果数量达标）
            if high_count_valid:
                selected_high = weighted_sample(high_count_exprs, min(len(high_count_exprs), select_high_count))
            else:
                selected_high = []

            # 然后从所有表达方式中随机抽样（使用加权抽样）
            remaining_num = select_random_count
            selected_random = weighted_sample(all_style_exprs, min(len(all_style_exprs), remaining_num))

            # 合并候选池（去重，避免重复）
            candidate_exprs = selected_high.copy()
            candidate_ids = {expr["id"] for expr in candidate_exprs}
            for expr in selected_random:
                if expr["id"] not in candidate_ids:
                    candidate_exprs.append(expr)
                    candidate_ids.add(expr["id"])

            # 打乱顺序，避免高count的都在前面
            import random

            random.shuffle(candidate_exprs)

            # 2. 构建所有表达方式的索引和情境列表
            all_expressions: List[Dict[str, Any]] = []
            all_situations: List[str] = []

            # 添加style表达方式
            for expr in candidate_exprs:
                expr = expr.copy()
                all_expressions.append(expr)
                all_situations.append(f"{len(all_expressions)}.当 {expr['situation']} 时，使用 {expr['style']}")

            if not all_expressions:
                logger.warning("没有找到可用的表达方式")
                return [], []

            all_situations_str = "\n".join(all_situations)

            if target_message:
                target_message_str = f'，现在你想要对这条消息进行回复："{target_message}"'
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
            # print(content)

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
