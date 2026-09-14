"""query_image_memory 内置工具。"""

from __future__ import annotations

from typing import Optional

from src.A_memorix.core.image.component_paths import build_chat_external_ref, iter_message_image_components
from src.common.message_repository import find_messages
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.services.memory_service import memory_service

from .context import BuiltinToolRuntimeContext


def get_tool_spec(*, enabled: bool = True) -> ToolSpec:
    return ToolSpec(
        name="query_image_memory",
        description="查询当前聊天中某张图片过去出现过的同图、相似图及其关联记忆。",
        parameters_schema={
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "包含目标图片的消息 ID；留空时使用最近图片。"},
                "component_path": {"type": "string", "description": "消息内图片组件路径；留空时使用第一张图片。"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
            },
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
        enabled=enabled,
    )


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    del context
    runtime = tool_ctx.runtime
    session_id = str(runtime.session_id or "").strip()
    requested_message_id = str(invocation.arguments.get("message_id") or "").strip()
    requested_path = str(invocation.arguments.get("component_path") or "").strip()
    try:
        limit = max(1, min(20, int(invocation.arguments.get("limit") or 8)))
    except (TypeError, ValueError):
        limit = 8

    messages = (
        find_messages(session_id=session_id, message_id=requested_message_id, limit=1)
        if requested_message_id
        else find_messages(session_id=session_id, limit=30, limit_mode="latest")
    )
    selected = None
    for message in reversed(messages):
        for component_path, component in iter_message_image_components(message.raw_message.components):
            if requested_path and component_path != requested_path:
                continue
            selected = (message, component_path, component)
            break
        if selected is not None:
            break
    if selected is None:
        return tool_ctx.build_failure_result(invocation.tool_name, "没有找到指定图片。")

    message, component_path, component = selected
    content_hash = str(component.binary_hash or "").strip()
    external_ref = build_chat_external_ref(
        chat_id=session_id,
        message_id=str(message.message_id),
        component_path=component_path,
    )
    result = await memory_service.image_memory(
        action="search",
        content_hash=content_hash,
        current_external_ref=external_ref,
        chat_id=session_id,
        session_id=session_id,
        candidate_limit=limit,
    )
    if not result.get("success", True) and result.get("error"):
        return tool_ctx.build_failure_result(invocation.tool_name, str(result["error"]), structured_content=result)

    lines: list[str] = []
    for index, hit in enumerate(result.get("hits") or [], start=1):
        kind = "同一图片" if hit.get("match_kind") == "exact_hash" else "相似图片"
        lines.append(f"{index}. {kind}，相似度 {float(hit.get('similarity') or 0.0):.3f}")
        for observation in hit.get("observations") or []:
            text = str(observation.get("text") or "").strip()
            if text:
                lines.append(f"   图片认知：{text}")
        for memory in hit.get("related_memories") or []:
            text = str(memory.get("content") or "").strip()
            if text:
                lines.append(f"   关联记忆：{text}")
    content = "\n".join(lines) if lines else "没有找到同图或达到阈值的相似图片记录。"
    return tool_ctx.build_success_result(invocation.tool_name, content, structured_content=result)
