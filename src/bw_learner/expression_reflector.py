from rich.traceback import install
from sqlmodel import select
from typing import TYPE_CHECKING

import random
import time

from src.common.logger import get_logger
from src.config.config import global_config
from src.common.database.database_model import Expression
from src.common.database.database import get_db_session
from src.common.data_models.expression_data_model import MaiExpression
from src.common.utils.utils_session import SessionUtils
from .expression_reflect_tracker import ReflectTracker

if TYPE_CHECKING:
    from src.config.official_configs import TargetItem

logger = get_logger("expression_reflector")

install(extra_lines=3)

LOG_PREFIX = "[Expression Reflector]"


class ExpressionReflector:
    """表达反思器，管理单个聊天流的表达反思提问，使用每个session_id独立的实例"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.last_ask_time: float = time.time()
        self.reflect_tracker: ReflectTracker = ReflectTracker(session_id)

    async def check_and_ask(self) -> bool:
        """
        检查是否需要提问表达反思，如果需要则提问

        Returns:
            bool: 是否执行了提问
        """
        if not await self.check_need_ask():
            return False

        operator_config = global_config.expression.manual_reflect_operator_id
        if not operator_config:
            logger.debug(f"{LOG_PREFIX} Operator ID 未配置，跳过")
            return False

        if await self.ask_reflection(operator_config):
            self.last_ask_time = time.time()
            return True
        return False

    async def check_need_ask(self) -> bool:
        """
        检查是否需要提问表达反思

        Returns:
            bool: 是否执行了提问
        """
        if not global_config.expression.expression_manual_reflect:
            logger.debug(f"{LOG_PREFIX} 表达反思功能未启用，跳过")
            return False
        logger.debug(f"{LOG_PREFIX} 开始检查是否需要提问 (session_id: {self.session_id})")
        operator_config = global_config.expression.manual_reflect_operator_id
        if not operator_config:
            logger.debug(f"{LOG_PREFIX} Operator ID 未配置，跳过")
            return False

        if allow_reflect_list := global_config.expression.allow_reflect:
            # 转换配置项为session_id列表
            allow_reflect_session_ids = [
                self._parse_config_item_2_session_id(stream_config) for stream_config in allow_reflect_list
            ]
            if self.session_id not in allow_reflect_session_ids:
                logger.info(f"{LOG_PREFIX} 当前聊天流 {self.session_id} 不在允许列表中，跳过")
                return False

        # 检查上一次提问时间
        current_time = time.time()
        time_since_last_ask = current_time - self.last_ask_time

        # 随机选择10-15分钟间隔
        ask_interval = random.uniform(10 * 60, 15 * 60)
        if time_since_last_ask < ask_interval:
            logger.info(
                f"{LOG_PREFIX} 距离上次提问时间 {time_since_last_ask:.2f} 秒，未达到随机间隔 {ask_interval:.2f} 秒，跳过"
            )
            return False

        if self.reflect_tracker.tracking:
            logger.info(f"{LOG_PREFIX} Operator {operator_config} 已有活跃的 Tracker，跳过本次提问")
            return False
        return True

    async def ask_reflection(self, operator_config: "TargetItem") -> bool:
        """执行提问表达反思的操作"""
        # 选取未检查过的表达
        logger.info(f"{LOG_PREFIX} 查询未检查且未拒绝的表达")
        try:
            with get_db_session() as session:
                statement = select(Expression).filter_by(checked=False, rejected=False).limit(50)
                results = session.exec(statement).all()
                if not results:
                    logger.info(f"{LOG_PREFIX} 未找到未检查且未拒绝的表达")
                    return False
                logger.info(f"{LOG_PREFIX} 找到 {len(results)} 个未检查且未拒绝的表达")

        except Exception as selected_expression:
            logger.error(f"{LOG_PREFIX} 查询表达时发生错误: {selected_expression}")
            return False

        # 随机选取一个表达进行提问
        selected_expression = MaiExpression.from_db_instance(random.choice(results))
        item_id = selected_expression.item_id
        situation = selected_expression.situation
        style = selected_expression.style
        logger.info(f"{LOG_PREFIX} 随机选择了表达 ID: {item_id}, Situation: {situation}, Style: {style}")

        ask_text = (
            f"我正在学习新的表达方式，请帮我看看这个是否合适？\n\n"
            f"**学习到的表达信息**\n"
            f"- 情景 (Situation): {situation}\n"
            f"- 风格 (Style): {style}\n"
        )

        # TODO: 在发送相关API重构完成后完成发送给operator的逻辑

        self.reflect_tracker.register_expression_and_track(selected_expression)
        return True

    def _parse_config_item_2_session_id(self, config_item: "TargetItem") -> str:
        if config_item.rule_type == "group":
            return SessionUtils.calculate_session_id(config_item.platform, group_id=str(config_item.item_id))
        else:
            return SessionUtils.calculate_session_id(config_item.platform, user_id=str(config_item.item_id))
