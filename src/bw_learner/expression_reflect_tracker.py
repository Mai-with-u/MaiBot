import json
import re
import time
from typing import TYPE_CHECKING, Any, Optional

from json_repair import repair_json

from src.chat.utils.chat_message_builder import (
    build_readable_messages,
    get_raw_msg_by_timestamp_with_chat,
)
from src.common.database.database import get_db_session
from src.common.logger import get_logger
from src.config.config import model_config
from src.llm_models.utils_model import LLMRequest
from src.prompt.prompt_manager import prompt_manager

if TYPE_CHECKING:
    from src.common.data_models.expression_data_model import MaiExpression

judge_model = LLMRequest(model_set=model_config.model_task_config.tool_use, request_type="reflect.tracker")

logger = get_logger("reflect_tracker")

class ReflectTracker:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.last_check_msg_count = 0
        self.max_msg_count = 30
        self.max_duration = 15 * 60  # 15 分钟
        self.expression: Optional["MaiExpression"] = None  # 当前正在追踪的表达，由外部设置

        # 运行状态
        self.tracking = False
        self.tracking_start_time: float = 0.0

    def register_expression_and_track(self, expression: "MaiExpression"):
        """注册需要追踪的表达"""
        if self.tracking:
            raise RuntimeError("ReflectTracker is already tracking an expression.")
        self.expression = expression
        self.tracking = True
        self.tracking_start_time = time.time()
    
    def _reset_tracker(self):
        """重置追踪状态"""
        self.expression = None
        self.tracking = False
        self.last_check_msg_count = 0

    # TODO test it
    async def trigger_tracker(self) -> bool:
        """
        触发追踪检查

        Returns:
            return (bool): 如果返回True，表示追踪完成，Tracker运行结束（运行状态置为`False`）；如果返回False，表示继续追踪
        """
        # 对于没有正在追踪的表达，直接返回False
        if not self.tracking or not self.expression:
            return False

        # Type narrowing: expression is guaranteed non-None when tracking
        assert self.expression is not None
        expr = self.expression

        # 检查是否超时（无论是消息数量还是时间）
        if time.time() - self.tracking_start_time > self.max_duration:
            self._reset_tracker()
            return True
        
        # 获取消息列表
        msg_list = get_raw_msg_by_timestamp_with_chat(
            chat_id=self.session_id,
            timestamp_start=self.tracking_start_time,
            timestamp_end=time.time(),
        )

        current_msg_count = len(msg_list)

        # 检查消息数量是否超限
        if current_msg_count > self.max_msg_count:
            logger.info(f"ReflectTracker for expr {expr.item_id} timed out (message count).")
            self._reset_tracker()
            return True

        # 如果没有新消息，跳过本次检查
        if current_msg_count <= self.last_check_msg_count:
            return False

        self.last_check_msg_count = current_msg_count

        # 构建上下文
        context_block = build_readable_messages(
            msg_list,
            replace_bot_name=True,
            timestamp_mode="relative",
            read_mark=0.0,
            show_actions=False,
        )

        # LLM 判断
        try:
            prompt_template = prompt_manager.get_prompt("reflect_judge")
            prompt_template.add_context("situation", str(expr.situation))
            prompt_template.add_context("style", str(expr.style))
            prompt_template.add_context("context_block", context_block)
            prompt = await prompt_manager.render_prompt(prompt_template)

            logger.info(f"ReflectTracker LLM Prompt: {prompt}")

            response, _ = await judge_model.generate_response_async(prompt, temperature=0.1)

            logger.info(f"ReflectTracker LLM Response: {response}")

            # 解析 JSON 响应
            json_pattern = r"```json\s*(.*?)\s*```"
            matches = re.findall(json_pattern, response, re.DOTALL)
            if not matches:
                matches = [response]

            json_obj = json.loads(repair_json(matches[0]))
            judgment = json_obj.get("judgment")

            if judgment == "Approve":
                self._update_expression(checked=True, rejected=False, modified_by="ai")
                logger.info(f"Expression {expr.item_id} approved by operator.")
                self._reset_tracker()
                return True

            elif judgment == "Reject":
                corrected_situation = json_obj.get("corrected_situation")
                corrected_style = json_obj.get("corrected_style")
                has_update = bool(corrected_situation or corrected_style)

                update_kwargs: dict[str, Any] = {"checked": True, "modified_by": "ai"}
                if corrected_situation:
                    update_kwargs["situation"] = corrected_situation
                if corrected_style:
                    update_kwargs["style"] = corrected_style
                if not has_update:
                    update_kwargs["rejected"] = True
                else:
                    update_kwargs["rejected"] = False

                self._update_expression(**update_kwargs)

                if has_update:
                    logger.info(
                        f"Expression {expr.item_id} rejected and updated. "
                        f"New situation: {corrected_situation}, New style: {corrected_style}"
                    )
                else:
                    logger.info(
                        f"Expression {expr.item_id} rejected but no correction provided, marked as rejected."
                    )
                self._reset_tracker()
                return True

            elif judgment == "Ignore":
                logger.info(f"ReflectTracker for expr {expr.item_id} judged as Ignore.")
                return False

        except Exception as e:
            logger.error(f"Error in ReflectTracker check: {e}")
            return False

        return False

    def _update_expression(self, **kwargs: Any) -> None:
        """更新表达并持久化到数据库"""
        if not self.expression:
            return

        # 更新内存中的表达对象
        for key, value in kwargs.items():
            if hasattr(self.expression, key):
                setattr(self.expression, key, value)

        # 持久化到数据库
        try:
            with get_db_session() as session:
                db_expr = self.expression.to_db_instance()
                session.merge(db_expr)
                session.commit()
        except Exception as e:
            logger.error(f"Failed to persist expression update: {e}")