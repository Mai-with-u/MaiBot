"""从运行上下文解析图片，供内置回复和插件能力共用。"""

from typing import TYPE_CHECKING

from src.common.data_models.message_component_data_model import ImageComponent

from .message_id_alias import build_alias_map, to_display_message_id
from .messages import SessionBackedMessage

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting


async def resolve_context_image(
    runtime: "MaisakaHeartFlowChatting", source_id: str, image_index: int = 0,
) -> ImageComponent:
    """按当前上下文消息编号或工具媒体索引读取图片，保留原始图片序号。"""

    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("图片附件需要提供 msg_id 或 media_index。")
    if type(image_index) is not int or image_index < 0:
        raise ValueError("图片序号必须是非负整数。")
    source_id = source_id.strip()
    # SDK 调用同样接受模型可见的短编号；工具媒体索引仍使用原始值。
    alias_map = build_alias_map(
        message.message_id for message in reversed(runtime._chat_history)
        if isinstance(message, SessionBackedMessage)
    )
    source_id = alias_map.get(source_id, source_id)

    # 工具返回媒体保存在历史中，普通聊天图片也可以从当前源消息读取。
    context_message = next(
        (
            message for message in reversed(runtime._chat_history)
            if isinstance(message, SessionBackedMessage) and message.message_id == source_id
        ),
        None,
    )
    source_message = context_message or runtime.find_source_message_by_id(source_id)
    if source_message is None:
        raise ValueError(f"没有找到消息：msg_id={to_display_message_id(source_id)}")

    # 保留原始图片序号；不能过滤加载失败的图片，否则会发送另一张图。
    images = [component for component in source_message.raw_message.components if isinstance(component, ImageComponent)]
    if image_index >= len(images):
        raise ValueError(f"图片序号超出范围：index={image_index}，该消息共有 {len(images)} 张图片。")
    image = images[image_index]
    if not image.binary_data:
        await image.load_image_binary()
    if not image.binary_data:
        raise ValueError(f"目标图片数据不可读取：msg_id={to_display_message_id(source_id)}，index={image_index}")
    return ImageComponent(binary_hash=image.binary_hash, content=image.content, binary_data=image.binary_data)
