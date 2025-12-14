import time
from typing import Optional, Dict, TYPE_CHECKING
from src.common.logger import get_logger
from src.common.database.database_model import Expression
from src.llm_models.utils_model import LLMRequest
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.config.config import model_config
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.utils.chat_message_builder import (
    get_raw_msg_by_timestamp_with_chat,
    build_readable_messages,
)

if TYPE_CHECKING:
    pass

logger = get_logger("reflect_tracker")


class ReflectTracker:
    def __init__(self, chat_stream: ChatStream, expression: Expression, created_time: float):
        self.chat_stream = chat_stream
        self.expression = expression
        self.created_time = created_time
        # self.message_count = 0  # Replaced by checking message list length
        self.last_check_msg_count = 0
        self.max_message_count = 30
        self.max_duration = 15 * 60  # 15 minutes

        # LLM for judging response
        self.judge_model = LLMRequest(model_set=model_config.model_task_config.utils, request_type="reflect.tracker")

        self._init_prompts()

    def _init_prompts(self):
        judge_prompt = """
你是一个表达反思助手。Bot之前询问了表达方式是否合适。
你需要根据提供的上下文对话，判断是否对该表达方式做出了肯定或否定的评价。

**询问内容**
情景: {situation}
风格: {style}

**上下文对话**
{context_block}

**判断要求**
1. 判断对话中是否包含对上述询问的回答。
2. 如果是，判断是肯定（Approve）还是否定（Reject），或者是提供了修改意见。
3. 如果不是回答，或者是无关内容，请返回 "Ignore"。
4. 如果是否定并提供了修改意见，请提取修正后的情景和风格。

请输出JSON格式：
```json
{{
    "judgment": "Approve" | "Reject" | "Ignore",
    "corrected_situation": "...", // 如果有修改意见，提取修正后的情景，否则留空
    "corrected_style": "..." // 如果有修改意见，提取修正后的风格，否则留空
}}
```
"""
        Prompt(judge_prompt, "reflect_judge_prompt")

    async def trigger_tracker(self) -> bool:
        """
        触发追踪检查
        Returns: True if resolved (should destroy tracker), False otherwise
        """
        # Check timeout
        if time.time() - self.created_time > self.max_duration:
            logger.info(f"ReflectTracker for expr {self.expression.id} timed out (duration).")
            return True

        # Fetch messages since creation
        msg_list = get_raw_msg_by_timestamp_with_chat(
            chat_id=self.chat_stream.stream_id,
            timestamp_start=self.created_time,
            timestamp_end=time.time(),
        )

        current_msg_count = len(msg_list)

        # Check message limit
        if current_msg_count > self.max_message_count:
            logger.info(f"ReflectTracker for expr {self.expression.id} timed out (message count).")
            return True

        # If no new messages since last check, skip
        if current_msg_count <= self.last_check_msg_count:
            return False

        self.last_check_msg_count = current_msg_count

        # Build context block
        # Use simple readable format
        context_block = build_readable_messages(
            msg_list,
            replace_bot_name=True,
            timestamp_mode="relative",
            read_mark=0.0,
            show_actions=False,
        )

        # LLM Judge
        try:
            prompt = await global_prompt_manager.format_prompt(
                "reflect_judge_prompt",
                situation=self.expression.situation,
                style=self.expression.style,
                context_block=context_block,
            )

            logger.info(f"ReflectTracker LLM Prompt: {prompt}")

            response, _ = await self.judge_model.generate_response_async(prompt, temperature=0.1)

            logger.info(f"ReflectTracker LLM Response: {response}")

            # Parse JSON
            import json
            import re
            from json_repair import repair_json

            json_pattern = r"```json\s*(.*?)\s*```"
            matches = re.findall(json_pattern, response, re.DOTALL)
            if not matches:
                # Try to parse raw response if no code block
                matches = [response]

            json_obj = json.loads(repair_json(matches[0]))

            judgment = json_obj.get("judgment")

            if judgment == "Approve":
                self.expression.checked = True
                self.expression.rejected = False
                self.expression.save()
                logger.info(f"Expression {self.expression.id} approved by operator.")
                return True

            elif judgment == "Reject":
                self.expression.checked = True
                corrected_situation = json_obj.get("corrected_situation")
                corrected_style = json_obj.get("corrected_style")

                # 检查是否有更新
                has_update = bool(corrected_situation or corrected_style)

                if corrected_situation:
                    self.expression.situation = corrected_situation
                if corrected_style:
                    self.expression.style = corrected_style

                # 如果拒绝但未更新，标记为 rejected=1
                if not has_update:
                    self.expression.rejected = True
                else:
                    self.expression.rejected = False

                self.expression.save()

                if has_update:
                    logger.info(
                        f"Expression {self.expression.id} rejected and updated by operator. New situation: {corrected_situation}, New style: {corrected_style}"
                    )
                else:
                    logger.info(
                        f"Expression {self.expression.id} rejected but no correction provided, marked as rejected=1."
                    )
                return True

            elif judgment == "Ignore":
                logger.info(f"ReflectTracker for expr {self.expression.id} judged as Ignore.")
                return False

        except Exception as e:
            logger.error(f"Error in ReflectTracker check: {e}")
            return False

        return False


# Global manager for trackers
class ReflectTrackerManager:
    def __init__(self):
        self.trackers: Dict[str, ReflectTracker] = {}  # chat_id -> tracker

    def add_tracker(self, chat_id: str, tracker: ReflectTracker):
        self.trackers[chat_id] = tracker

    def get_tracker(self, chat_id: str) -> Optional[ReflectTracker]:
        return self.trackers.get(chat_id)

    def remove_tracker(self, chat_id: str):
        if chat_id in self.trackers:
            del self.trackers[chat_id]


reflect_tracker_manager = ReflectTrackerManager()
