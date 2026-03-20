"""提供 WebUI 聊天路由使用的消息序列化能力。"""

from typing import Any, Dict, List, Optional

import base64

from src.common.data_models.message_component_data_model import (
    AtComponent,
    DictComponent,
    EmojiComponent,
    ForwardComponent,
    ForwardNodeComponent,
    ImageComponent,
    MessageSequence,
    ReplyComponent,
    StandardMessageComponents,
    TextComponent,
    VoiceComponent,
)


def serialize_message_sequence(message_sequence: MessageSequence) -> List[Dict[str, Any]]:
    """将内部统一消息组件序列转换为 WebUI 富文本消息段。

    Args:
        message_sequence: 内部统一消息组件序列。

    Returns:
        List[Dict[str, Any]]: 可直接广播给 WebUI 前端的消息段列表。
    """
    serialized_segments: List[Dict[str, Any]] = []
    for component in message_sequence.components:
        serialized_segment = serialize_message_component(component)
        if serialized_segment is not None:
            serialized_segments.append(serialized_segment)
    return serialized_segments


def serialize_message_component(component: StandardMessageComponents) -> Optional[Dict[str, Any]]:
    """将单个内部消息组件转换为 WebUI 消息段。

    Args:
        component: 待序列化的内部消息组件。

    Returns:
        Optional[Dict[str, Any]]: 序列化后的 WebUI 消息段；若组件不应展示则返回 ``None``。
    """
    if isinstance(component, TextComponent):
        return {"type": "text", "data": component.text}

    if isinstance(component, ImageComponent):
        return _serialize_binary_component(
            segment_type="image",
            mime_type="image/png",
            binary_data=component.binary_data,
            fallback_text=component.content,
        )

    if isinstance(component, EmojiComponent):
        return _serialize_binary_component(
            segment_type="emoji",
            mime_type="image/gif",
            binary_data=component.binary_data,
            fallback_text=component.content,
        )

    if isinstance(component, VoiceComponent):
        return _serialize_binary_component(
            segment_type="voice",
            mime_type="audio/wav",
            binary_data=component.binary_data,
            fallback_text=component.content,
        )

    if isinstance(component, AtComponent):
        return {
            "type": "at",
            "data": {
                "target_user_id": component.target_user_id,
                "target_user_nickname": component.target_user_nickname,
                "target_user_cardname": component.target_user_cardname,
            },
        }

    if isinstance(component, ReplyComponent):
        return {
            "type": "reply",
            "data": {
                "target_message_id": component.target_message_id,
                "target_message_content": component.target_message_content,
                "target_message_sender_id": component.target_message_sender_id,
                "target_message_sender_nickname": component.target_message_sender_nickname,
                "target_message_sender_cardname": component.target_message_sender_cardname,
            },
        }

    if isinstance(component, ForwardNodeComponent):
        return {
            "type": "forward",
            "data": [_serialize_forward_component(item) for item in component.forward_components],
        }

    if isinstance(component, DictComponent):
        return _serialize_dict_component(component.data)

    return {"type": "unknown", "data": str(component)}


def _serialize_binary_component(
    segment_type: str,
    mime_type: str,
    binary_data: bytes,
    fallback_text: str,
) -> Dict[str, Any]:
    """序列化带二进制负载的消息组件。

    Args:
        segment_type: WebUI 消息段类型。
        mime_type: 对应的数据 MIME 类型。
        binary_data: 组件二进制数据。
        fallback_text: 二进制缺失时可退化展示的文本。

    Returns:
        Dict[str, Any]: 序列化后的 WebUI 消息段。
    """
    if binary_data:
        encoded_payload = base64.b64encode(binary_data).decode()
        return {"type": segment_type, "data": f"data:{mime_type};base64,{encoded_payload}"}

    if fallback_text:
        return {"type": "text", "data": fallback_text}

    return {"type": "unknown", "original_type": segment_type, "data": ""}


def _serialize_forward_component(component: ForwardComponent) -> Dict[str, Any]:
    """序列化单个转发节点。

    Args:
        component: 待序列化的转发节点组件。

    Returns:
        Dict[str, Any]: WebUI 可消费的转发节点字典。
    """
    return {
        "message_id": component.message_id,
        "user_id": component.user_id,
        "user_nickname": component.user_nickname,
        "user_cardname": component.user_cardname,
        "content": serialize_message_sequence(MessageSequence(component.content)),
    }


def _serialize_dict_component(data: Dict[str, Any]) -> Dict[str, Any]:
    """最佳努力地序列化非标准字典组件。

    Args:
        data: 原始字典组件内容。

    Returns:
        Dict[str, Any]: 序列化后的 WebUI 消息段。
    """
    raw_type = str(data.get("type") or "dict").strip()
    raw_payload = data.get("data", data)

    if raw_type in {"text", "image", "emoji", "voice", "video", "file", "music", "face"}:
        return {"type": raw_type, "data": raw_payload}

    if raw_type == "reply":
        return {"type": "reply", "data": raw_payload}

    if raw_type == "forward" and isinstance(raw_payload, list):
        return {"type": "forward", "data": raw_payload}

    return {"type": "unknown", "original_type": raw_type, "data": raw_payload}
