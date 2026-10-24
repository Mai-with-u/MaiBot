"""query_image_memory 内置工具。"""

from __future__ import annotations

from typing import Any, Optional, Sequence, Tuple

from src.A_memorix.core.image.component_paths import build_chat_external_ref, iter_message_image_components
from src.common.message_repository import find_messages
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.maisaka.context.message_id_alias import to_display_message_id
from src.services.memory_service import memory_service

from .context import BuiltinToolRuntimeContext

_DEFAULT_LIMIT = 8
_MAX_LIMIT = 20


def get_tool_spec(*, enabled: bool = True) -> ToolSpec:
    """获取 query_image_memory 工具声明。"""

    return ToolSpec(
        name="query_image_memory",
        description="查询当前聊天中某张图片的记忆：过去曾出现过的同图、相似图及其关联认知。",
        parameters_schema={
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "包含目标图片的消息 msg_id。"},
                "image_index": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "该消息中的第几张图片，从 0 开始按出现顺序计数；消息只有一张图片时可省略。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_LIMIT,
                    "default": _DEFAULT_LIMIT,
                },
            },
            "required": ["message_id"],
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
        enabled=enabled,
    )


def resolve_message_image(components: Sequence[Any], image_index: int) -> Tuple[str, Any]:
    """按模型可见的图片序号解析消息内图片，返回持久化组件路径与图片组件。

    component_path 是服务端按组件树生成的下标，文本、回复等组件也会占位，模型无从得知；
    因此工具只暴露「消息内第几张图片」，在这里换算成持久化的组件路径。
    """

    image_components = list(iter_message_image_components(components))
    if not image_components:
        raise ValueError("该消息没有图片，无法查询图片记忆；请改用包含图片的消息 msg_id。")
    if image_index < 0 or image_index >= len(image_components):
        raise ValueError(
            f"图片序号超出范围：image_index={image_index}，该消息共有 {len(image_components)} 张图片"
            "（image_index 从 0 开始）。"
        )
    return image_components[image_index]


def _parse_image_index(raw_value: Any) -> int:
    """解析工具参数中的图片序号，只接受非负整数或整数字符串。

    int() 会把 1.9 截断成 1、把 True 折叠成 1，这类输入会静默查到另一张图，
    因此布尔值和浮点数必须在转换前拒绝。
    """

    error_message = f"`image_index` 必须是从 0 开始的整数，当前值为 {raw_value!r}。"
    if raw_value is None or raw_value == "":
        return 0
    if isinstance(raw_value, bool) or isinstance(raw_value, float):
        raise ValueError(error_message)
    try:
        image_index = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(error_message) from exc
    if image_index < 0:
        raise ValueError(error_message)
    return image_index


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行 query_image_memory 内置工具。"""

    del context
    runtime = tool_ctx.runtime
    session_id = str(runtime.session_id or "").strip()
    requested_message_id = str(invocation.arguments.get("message_id") or "").strip()
    if not requested_message_id:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "查询图片记忆工具需要提供有效的 `message_id` 参数。",
        )

    try:
        image_index = _parse_image_index(invocation.arguments.get("image_index"))
    except ValueError as exc:
        return tool_ctx.build_failure_result(invocation.tool_name, str(exc))
    try:
        limit = max(1, min(_MAX_LIMIT, int(invocation.arguments.get("limit") or _DEFAULT_LIMIT)))
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT

    message = next(iter(find_messages(session_id=session_id, message_id=requested_message_id, limit=1)), None)
    if message is None:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"当前聊天中没有找到这条消息：msg_id={to_display_message_id(requested_message_id)}。",
        )
    try:
        component_path, component = resolve_message_image(message.raw_message.components, image_index)
    except ValueError as exc:
        return tool_ctx.build_failure_result(invocation.tool_name, str(exc))

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
