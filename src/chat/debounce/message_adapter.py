"""从原始消息组件读取类型和文字长度，避免把识图描述当作用户文字。"""

from typing import Tuple

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.message_component_data_model import TextComponent


def get_message_shape(message: SessionMessage) -> Tuple[int, int]:
    components = message.raw_message.components
    names = {component.format_name for component in components}
    text = "".join(component.text for component in components if isinstance(component, TextComponent)).strip()
    kind = (
        1
        if "image" in names
        else 2
        if "emoji" in names
        else 3
        if names & {"voice", "forward", "forward_node", "file", "video"}
        else 0
    )
    return kind, len(text)
