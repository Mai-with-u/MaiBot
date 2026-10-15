"""追加到 Planner 历史的固定表情候选快照。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict

from src.llm_models.payload_content.context_item import ContextItem, ContextTextPart, UserMessageItem

from .messages import LLMContextMessage


@dataclass(slots=True)
class EmojiCandidateMessage(LLMContextMessage):
    """保存原始拼图和编号映射，后续请求直接复用，避免重绘历史破坏缓存。"""

    item: UserMessageItem
    emoji_hashes: Dict[int, str]
    visible_text: str
    timestamp: datetime

    @property
    def role(self) -> str:
        return self.item.role.value

    @property
    def processed_plain_text(self) -> str:
        return self.visible_text

    @property
    def source(self) -> str:
        return "emoji_candidates"

    def to_context_item(self, enable_visual_message: bool = True) -> ContextItem:
        if not enable_visual_message:
            return UserMessageItem(meta=self.item.meta, parts=(ContextTextPart(self.visible_text),))
        return self.item
