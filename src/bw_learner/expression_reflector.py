import random
import time
from typing import Optional, Dict

from src.common.logger import get_logger
from src.common.database.database_model import Expression
from src.config.config import global_config
from src.chat.message_receive.chat_stream import get_chat_manager
from src.plugin_system.apis import send_api

logger = get_logger("expression_reflector")


class ExpressionReflector:
    """表达反思器，管理单个聊天流的表达反思提问"""

    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.last_ask_time: float = 0.0

    async def check_and_ask(self) -> bool:
        """
        检查是否需要提问表达反思，如果需要则提问

        Returns:
            bool: 是否执行了提问
        """
        try:
            logger.debug(f"[Expression Reflection] 开始检查是否需要提问 (stream_id: {self.chat_id})")

            if not global_config.expression.expression_self_reflect:
                logger.debug("[Expression Reflection] 表达反思功能未启用，跳过")
                return False

            operator_config = global_config.expression.manual_reflect_operator_id
            if not operator_config:
                logger.debug("[Expression Reflection] Operator ID 未配置，跳过")
                return False

            # 检查是否在允许列表中
            allow_reflect = global_config.expression.allow_reflect
            if allow_reflect:
                # 将 allow_reflect 中的 platform:id:type 格式转换为 chat_id 列表
                allow_reflect_chat_ids = []
                for stream_config in allow_reflect:
                    parsed_chat_id = global_config.expression._parse_stream_config_to_chat_id(stream_config)
                    if parsed_chat_id:
                        allow_reflect_chat_ids.append(parsed_chat_id)
                    else:
                        logger.warning(f"[Expression Reflection] 无法解析 allow_reflect 配置项: {stream_config}")

                if self.chat_id not in allow_reflect_chat_ids:
                    logger.info(f"[Expression Reflection] 当前聊天流 {self.chat_id} 不在允许列表中，跳过")
                    return False

            # 检查上一次提问时间
            current_time = time.time()
            time_since_last_ask = current_time - self.last_ask_time

            # 5-10分钟间隔，随机选择
            min_interval = 10 * 60  # 5分钟
            max_interval = 15 * 60  # 10分钟
            interval = random.uniform(min_interval, max_interval)

            logger.info(
                f"[Expression Reflection] 上次提问时间: {self.last_ask_time:.2f}, 当前时间: {current_time:.2f}, 已过时间: {time_since_last_ask:.2f}秒 ({time_since_last_ask / 60:.2f}分钟), 需要间隔: {interval:.2f}秒 ({interval / 60:.2f}分钟)"
            )

            if time_since_last_ask < interval:
                remaining_time = interval - time_since_last_ask
                logger.info(
                    f"[Expression Reflection] 距离上次提问时间不足，还需等待 {remaining_time:.2f}秒 ({remaining_time / 60:.2f}分钟)，跳过"
                )
                return False

            # 检查是否已经有针对该 Operator 的 Tracker 在运行
            logger.info(f"[Expression Reflection] 检查 Operator {operator_config} 是否已有活跃的 Tracker")
            if await _check_tracker_exists(operator_config):
                logger.info(f"[Expression Reflection] Operator {operator_config} 已有活跃的 Tracker，跳过本次提问")
                return False

            # 获取未检查的表达
            try:
                logger.info("[Expression Reflection] 查询未检查且未拒绝的表达")
                expressions = Expression.select().where((~Expression.checked) & (~Expression.rejected)).limit(50)

                expr_list = list(expressions)
                logger.info(f"[Expression Reflection] 找到 {len(expr_list)} 个候选表达")

                if not expr_list:
                    logger.info("[Expression Reflection] 没有可用的表达，跳过")
                    return False

                target_expr: Expression = random.choice(expr_list)
                logger.info(
                    f"[Expression Reflection] 随机选择了表达 ID: {target_expr.id}, Situation: {target_expr.situation}, Style: {target_expr.style}"
                )

                # 生成询问文本
                ask_text = _generate_ask_text(target_expr)
                if not ask_text:
                    logger.warning("[Expression Reflection] 生成询问文本失败，跳过")
                    return False

                logger.info(f"[Expression Reflection] 准备向 Operator {operator_config} 发送提问")
                # 发送给 Operator
                await _send_to_operator(operator_config, ask_text, target_expr)

                # 更新上一次提问时间
                self.last_ask_time = current_time
                logger.info(f"[Expression Reflection] 提问成功，已更新上次提问时间为 {current_time:.2f}")

                return True

            except Exception as e:
                logger.error(f"[Expression Reflection] 检查或提问过程中出错: {e}")
                import traceback

                logger.error(traceback.format_exc())
                return False
        except Exception as e:
            logger.error(f"[Expression Reflection] 检查或提问过程中出错: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return False


class ExpressionReflectorManager:
    """表达反思管理器，管理多个聊天流的表达反思实例"""

    def __init__(self):
        self.reflectors: Dict[str, ExpressionReflector] = {}

    def get_or_create_reflector(self, chat_id: str) -> ExpressionReflector:
        """获取或创建指定聊天流的表达反思实例"""
        if chat_id not in self.reflectors:
            self.reflectors[chat_id] = ExpressionReflector(chat_id)
        return self.reflectors[chat_id]


# 创建全局实例
expression_reflector_manager = ExpressionReflectorManager()


async def _check_tracker_exists(operator_config: str) -> bool:
    """检查指定 Operator 是否已有活跃的 Tracker"""
    from src.bw_learner.reflect_tracker import reflect_tracker_manager

    chat_manager = get_chat_manager()
    chat_stream = None

    # 尝试解析配置字符串 "platform:id:type"
    parts = operator_config.split(":")
    if len(parts) == 3:
        platform = parts[0]
        id_str = parts[1]
        stream_type = parts[2]

        user_info = None
        group_info = None

        from maim_message import UserInfo, GroupInfo

        if stream_type == "group":
            group_info = GroupInfo(group_id=id_str, platform=platform)
            user_info = UserInfo(user_id="system", user_nickname="System", platform=platform)
        elif stream_type == "private":
            user_info = UserInfo(user_id=id_str, platform=platform, user_nickname="Operator")
        else:
            return False

        if user_info:
            try:
                chat_stream = await chat_manager.get_or_create_stream(platform, user_info, group_info)
            except Exception as e:
                logger.error(f"Failed to get or create chat stream for checking tracker: {e}")
                return False
    else:
        chat_stream = chat_manager.get_stream(operator_config)

    if not chat_stream:
        return False

    return reflect_tracker_manager.get_tracker(chat_stream.stream_id) is not None


def _generate_ask_text(expr: Expression) -> Optional[str]:
    try:
        ask_text = (
            f"我正在学习新的表达方式，请帮我看看这个是否合适？\n\n"
            f"**学习到的表达信息**\n"
            f"- 情景 (Situation): {expr.situation}\n"
            f"- 风格 (Style): {expr.style}\n"
        )
        return ask_text
    except Exception as e:
        logger.error(f"Failed to generate ask text: {e}")
        return None


async def _send_to_operator(operator_config: str, text: str, expr: Expression):
    chat_manager = get_chat_manager()
    chat_stream = None

    # 尝试解析配置字符串 "platform:id:type"
    parts = operator_config.split(":")
    if len(parts) == 3:
        platform = parts[0]
        id_str = parts[1]
        stream_type = parts[2]

        user_info = None
        group_info = None

        from maim_message import UserInfo, GroupInfo

        if stream_type == "group":
            group_info = GroupInfo(group_id=id_str, platform=platform)
            user_info = UserInfo(user_id="system", user_nickname="System", platform=platform)
        elif stream_type == "private":
            user_info = UserInfo(user_id=id_str, platform=platform, user_nickname="Operator")
        else:
            logger.warning(f"Unknown stream type in operator config: {stream_type}")
            return

        if user_info:
            try:
                chat_stream = await chat_manager.get_or_create_stream(platform, user_info, group_info)
            except Exception as e:
                logger.error(f"Failed to get or create chat stream for operator {operator_config}: {e}")
                return
    else:
        chat_stream = chat_manager.get_stream(operator_config)

    if not chat_stream:
        logger.warning(f"Could not find or create chat stream for operator: {operator_config}")
        return

    stream_id = chat_stream.stream_id

    # 注册 Tracker
    from src.bw_learner.reflect_tracker import ReflectTracker, reflect_tracker_manager

    tracker = ReflectTracker(chat_stream=chat_stream, expression=expr, created_time=time.time())
    reflect_tracker_manager.add_tracker(stream_id, tracker)

    # 发送消息
    await send_api.text_to_stream(text=text, stream_id=stream_id, typing=True)
    logger.info(f"Sent expression reflect query to operator {operator_config} for expr {expr.id}")
