"""图片发送内置动作。"""

from base64 import b64encode
from typing import Any, Optional

from src.common.data_models.message_component_data_model import ImageComponent, MessageSequence
from src.common.logger import get_logger
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.maisaka.context.messages import SessionBackedMessage
from src.services import send_service

from .context import BuiltinToolRuntimeContext

logger = get_logger("maisaka_builtin_send_image")


def get_tool_spec() -> ToolSpec:
    """获取图片发送工具声明。"""

    return ToolSpec(
        name="send_image",
        description=(
            "将context中的图片展示给用户，给用户发送图片信息时使用。当你需要通过图片进行说明解释时使用。当用户需要你发图片时使用。不是查看图片内容，而是将图片展示给其他用户。"
            "按 msg_id + index 或 工具返回媒体索引 tool_result:<call_id>:<item_index> 发送指定图片"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "msg_id": {
                    "type": "string",
                    "description": "图片所在的消息编号，也可以是工具返回媒体索引 tool_result:<call_id>:<item_index>。",
                    "default": "",
                },
                "media_index": {
                    "type": "string",
                    "description": "工具返回媒体索引，例如 tool_result:call_x:1；与 msg_id 二选一。",
                    "default": "",
                },
                "index": {
                    "type": "integer",
                    "description": "同一消息中的图片序号，从 0 开始。",
                    "default": 0,
                },
            },
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


def _find_context_message_by_id(tool_ctx: BuiltinToolRuntimeContext, message_id: str) -> SessionBackedMessage | None:
    """从 Maisaka 历史里按 message_id 查找上下文消息。"""

    normalized_message_id = str(message_id or "").strip()
    if not normalized_message_id:
        return None

    for history_message in reversed(tool_ctx.runtime._chat_history):
        if str(getattr(history_message, "message_id", "") or "").strip() != normalized_message_id:
            continue
        if isinstance(history_message, SessionBackedMessage):
            return history_message
    return None


def _collect_images_from_sequence(message_sequence: MessageSequence | None) -> list[ImageComponent]:
    """从消息组件序列中收集图片组件。"""

    components = list(getattr(message_sequence, "components", []) or [])
    return [component for component in components if isinstance(component, ImageComponent)]


async def _load_readable_images(
    tool_ctx: BuiltinToolRuntimeContext,
    images: list[ImageComponent],
    source_id: str,
) -> tuple[list[ImageComponent], str | None]:
    """确保图片组件已经加载二进制数据。"""

    if not images:
        return [], f"目标消息中没有可读取的图片：msg_id={source_id}"

    for image in images:
        if image.binary_data:
            continue
        try:
            await image.load_image_binary()
        except Exception as exc:
            logger.warning(f"{tool_ctx.runtime.log_prefix} 加载消息图片失败: msg_id={source_id} error={exc}")

    readable_images = [image for image in images if image.binary_data]
    if not readable_images:
        return [], f"目标消息中的图片数据不可读取：msg_id={source_id}"
    return readable_images, None


async def _collect_message_images(
    tool_ctx: BuiltinToolRuntimeContext, msg_id: str
) -> tuple[list[ImageComponent], str | None]:
    """从 Maisaka 历史消息或工具返回媒体消息里读取图片组件。"""

    target_message_id = str(msg_id or "").strip()
    if not target_message_id:
        return [], "需要提供 msg_id。"

    context_message = _find_context_message_by_id(tool_ctx, target_message_id)
    if context_message is not None:
        images = _collect_images_from_sequence(context_message.raw_message)
        return await _load_readable_images(tool_ctx, images, target_message_id)

    target_message = tool_ctx.runtime.find_source_message_by_id(target_message_id)
    if target_message is None:
        return [], f"没有找到消息：msg_id={target_message_id}"

    images = _collect_images_from_sequence(getattr(target_message, "raw_message", None))
    return await _load_readable_images(tool_ctx, images, target_message_id)


def _normalize_image_index(arguments: dict[str, Any]) -> int:
    """兼容旧工具的 image_index 参数别名。"""

    raw_index = arguments.get("image_index", arguments.get("index", 0))
    try:
        return int(raw_index or 0)
    except (TypeError, ValueError):
        return 0


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行图片发送内置动作。"""

    del context
    arguments = dict(invocation.arguments or {})
    target_message_id = str(arguments.get("media_index") or "").strip() or str(arguments.get("msg_id") or "").strip()
    image_index = _normalize_image_index(arguments)
    structured_content: dict[str, Any] = {
        "success": False,
        "stream_id": tool_ctx.runtime.session_id,
        "msg_id": target_message_id,
        "index": image_index,
    }

    images, error = await _collect_message_images(tool_ctx, target_message_id)
    if error is not None:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            error,
            structured_content=structured_content,
        )

    if image_index < 0 or image_index >= len(images):
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"图片序号超出范围：index={image_index}，该消息共有 {len(images)} 张图片。",
            structured_content=structured_content,
        )

    image_base64 = b64encode(images[image_index].binary_data).decode("utf-8")
    source_label = f"{target_message_id} 的第 {image_index} 张图片"
    success = await send_service.image_to_stream(
        image_base64=image_base64,
        stream_id=tool_ctx.runtime.session_id,
        sync_to_maisaka_history=True,
        maisaka_source_kind="send_image",
    )
    if not success:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"发送上下文图片失败：{source_label}",
            structured_content=structured_content,
        )

    structured_content["success"] = True
    return tool_ctx.build_success_result(
        invocation.tool_name,
        f"已发送上下文图片：{source_label}",
        structured_content=structured_content,
    )
