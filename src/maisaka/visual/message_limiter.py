"""多模态消息图片数量限制工具。"""

from typing import List, Sequence, Set, Tuple

from src.llm_models.payload_content.message import ImageMessagePart, Message, MessagePart, TextMessagePart

IMAGE_LIMIT_PLACEHOLDER = "[图片]"


def limit_latest_images_in_messages(
    messages: Sequence[Message],
    *,
    max_image_num: int,
    placeholder: str = IMAGE_LIMIT_PLACEHOLDER,
) -> List[Message]:
    """限制 prompt 中的图片数量，只保留最新的图片。

    超出数量的旧图片会被替换为文本占位，避免多模态模型收到过多图片。
    """

    normalized_limit = max(0, int(max_image_num))
    image_positions: List[Tuple[int, int]] = []
    for message_index, message in enumerate(messages):
        for part_index, part in enumerate(message.parts):
            if isinstance(part, ImageMessagePart):
                image_positions.append((message_index, part_index))

    if len(image_positions) <= normalized_limit:
        return list(messages)

    keep_positions: Set[Tuple[int, int]] = set(image_positions[-normalized_limit:]) if normalized_limit > 0 else set()
    limited_messages: List[Message] = []
    for message_index, message in enumerate(messages):
        limited_parts: List[MessagePart] = []
        for part_index, part in enumerate(message.parts):
            if isinstance(part, ImageMessagePart) and (message_index, part_index) not in keep_positions:
                limited_parts.append(TextMessagePart(placeholder))
                continue
            limited_parts.append(part)

        limited_messages.append(
            Message(
                role=message.role,
                parts=limited_parts,
                tool_call_id=message.tool_call_id,
                tool_name=message.tool_name,
                tool_calls=message.tool_calls,
                provider_state=message.provider_state if limited_parts == message.parts else None,
            )
        )

    return limited_messages
