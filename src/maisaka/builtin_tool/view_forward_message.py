"""view_forward_message 内置工具。"""

from typing import Optional

from src.common.logger import get_logger
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.maisaka.context.messages import (
    SessionBackedMessage,
    build_full_complex_message_content,
    build_full_complex_message_content_from_sequence,
    contains_complex_message,
)

from .context import BuiltinToolRuntimeContext

logger = get_logger("maisaka_builtin_view_forward_message")


def get_tool_spec() -> ToolSpec:
    """获取 view_forward_message 工具声明。"""

    return ToolSpec(
        name="view_forward_message",
        description="根据 msg_id 查看合并转发消息的完整内容。仅适用于上下文中出现转发消息预览、且需要查看更多转发细节的场景。",
        parameters_schema={
            "type": "object",
            "properties": {
                "msg_id": {
                    "type": "string",
                    "description": "转发消息的 msg_id。",
                },
            },
            "required": ["msg_id"],
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


async def _build_full_content_for_context_message(message: SessionBackedMessage) -> str:
    """优先使用原始消息展开，合成转发消息则直接展开上下文组件。"""

    original_message = getattr(message, "original_message", None)
    if original_message is not None:
        return await build_full_complex_message_content(original_message)
    return build_full_complex_message_content_from_sequence(message.raw_message)


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行 view_forward_message 内置工具。"""

    del context
    target_message_id = str(invocation.arguments.get("msg_id") or "").strip()
    if not target_message_id:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "查看转发消息工具需要提供有效的 `msg_id` 参数。",
        )

    target_context_message = _find_context_message_by_id(tool_ctx, target_message_id)
    target_source_message = None
    if target_context_message is None:
        target_source_message = tool_ctx.runtime.find_source_message_by_id(target_message_id)
    if target_context_message is None and target_source_message is None:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"未找到目标转发消息，msg_id={target_message_id}",
        )

    target_sequence = (
        target_context_message.raw_message if target_context_message is not None else target_source_message.raw_message
    )
    if not contains_complex_message(target_sequence):
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"目标消息不是可展开查看的转发消息，msg_id={target_message_id}",
        )

    logger.info(f"{tool_ctx.runtime.log_prefix} 触发转发消息浏览工具，目标消息编号={target_message_id}")
    try:
        if target_context_message is not None:
            full_content = await _build_full_content_for_context_message(target_context_message)
        else:
            full_content = await build_full_complex_message_content(target_source_message)
    except Exception as exc:
        logger.exception(
            f"{tool_ctx.runtime.log_prefix} 查看转发消息时发生异常: 目标消息编号={target_message_id} 异常={exc}"
        )
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "查看转发消息完整内容时发生异常。",
        )

    if not full_content:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"转发消息内容为空，msg_id={target_message_id}",
        )

    return tool_ctx.build_success_result(
        invocation.tool_name,
        full_content,
        structured_content={
            "msg_id": target_message_id,
            "message_type": "forward",
            "full_content": full_content,
        },
    )
