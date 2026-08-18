"""多模态消息图片数量限制工具。"""

from dataclasses import replace
from typing import List, Sequence, Set, Tuple

from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextContentPart,
    ContextImagePart,
    ContextItem,
    ContextTextPart,
    SystemMessageItem,
    UserMessageItem,
)

IMAGE_LIMIT_PLACEHOLDER = "[图片]"


def limit_latest_images_in_messages(
    messages: Sequence[ContextItem],
    *,
    max_image_num: int,
    placeholder: str = IMAGE_LIMIT_PLACEHOLDER,
) -> List[ContextItem]:
    """限制 prompt 中的图片数量，只保留最新的图片。

    超出数量的旧图片会被替换为文本占位，避免多模态模型收到过多图片。
    """

    normalized_limit = max(0, int(max_image_num))
    image_positions: List[Tuple[int, int]] = []
    for message_index, message in enumerate(messages):
        if not isinstance(message, (SystemMessageItem, UserMessageItem, AssistantMessageItem)):
            continue
        for part_index, part in enumerate(message.parts):
            if isinstance(part, ContextImagePart):
                image_positions.append((message_index, part_index))

    if len(image_positions) <= normalized_limit:
        return list(messages)

    keep_positions: Set[Tuple[int, int]] = set(image_positions[-normalized_limit:]) if normalized_limit > 0 else set()
    limited_messages: List[ContextItem] = []
    for message_index, message in enumerate(messages):
        if not isinstance(message, (SystemMessageItem, UserMessageItem, AssistantMessageItem)):
            limited_messages.append(message)
            continue
        limited_parts: List[ContextContentPart] = []
        for part_index, part in enumerate(message.parts):
            if isinstance(part, ContextImagePart) and (message_index, part_index) not in keep_positions:
                limited_parts.append(ContextTextPart(placeholder))
                continue
            limited_parts.append(part)

        if tuple(limited_parts) == message.parts:
            limited_messages.append(message)
        elif isinstance(message, AssistantMessageItem):
            limited_messages.append(replace(message, parts=tuple(limited_parts), replay=None))
        else:
            limited_messages.append(replace(message, parts=tuple(limited_parts)))

    return limited_messages
