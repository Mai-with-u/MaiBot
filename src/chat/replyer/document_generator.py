"""供插件调用的长文 Replyer，复用生成链路并使用独立写作模板。"""

from typing import Any, Dict, List, Optional

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.llm_service_data_models import LLMGenerationOptions
from src.llm_models.payload_content.context_item import ContextItem, ContextItemBuilder, RoleType
from src.maisaka.context.messages import LLMContextMessage

from .maisaka_generator import MaisakaReplyGenerator


class DocumentReplyGenerator(MaisakaReplyGenerator):
    def __init__(self, *, model_name: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.document_model_name = model_name

    def _build_generation_options(self, model_name: Optional[str]) -> LLMGenerationOptions:
        return super()._build_generation_options(model_name or self.document_model_name or None)

    def _uses_retro_prompt(self) -> bool:
        return False

    def _build_request_messages(
        self,
        chat_history: List[LLMContextMessage],
        reply_message: Optional[SessionMessage],
        reply_reason: str,
        expression_habits: str = "",
        reply_requirements: str = "",
        stream_id: Optional[str] = None,
        enable_visual_message: bool = False,
        reply_tool_args: Optional[Dict[str, Any]] = None,
        think_level: int = 1,
    ) -> List[ContextItem]:
        """写作不套用聊天/复古短回复模板，材料和最终输出要求独立传入。"""
        del chat_history, reply_message, stream_id, enable_visual_message, think_level
        reference = self._build_reply_reference_message(
            reply_reason, str((reply_tool_args or {}).get("reply_reference", "")),
        )
        system_prompt = self._load_prompt(
            "maisaka_replyer_document", identity=self._build_personality_prompt(),
            reply_style=self._select_reply_style(), expression_habits=expression_habits,
        )
        return [
            ContextItemBuilder().set_role(RoleType.System).add_text_content(system_prompt).build(),
            ContextItemBuilder().set_role(RoleType.User).add_text_content(reference).build(),
            ContextItemBuilder().set_role(RoleType.User).add_text_content(reply_requirements).build(),
        ]
