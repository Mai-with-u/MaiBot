"""为当前真实聊天创建、查看和管理持久化单次日程。"""

from datetime import datetime
from typing import Any, Dict, Optional

import json

from src.chat.message_receive.chat_manager import chat_manager
from src.common.runtime_loop import run_on_main_loop
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.services.schedules.service import schedule_service

from .context import BuiltinToolRuntimeContext


def get_tool_spec() -> ToolSpec:
    return ToolSpec(
        name="schedule", provider_name="maisaka_builtin", provider_type="builtin",
        description="创建，查询或者修改日程，日程可以在定时唤起提醒，当需要在特定时间点做事时使用。"
        "新增必须填写 text 和未来时间：delay_seconds 或 ISO 8601 due_at 二选一。"
        "修改前先查询真实 todo_id；update 省略时间保留计划。创建日程只登记任务。",
        parameters_schema={
            "type": "object", "additionalProperties": False, "required": ["action"],
            "properties": {
                "action": {"type": "string", "enum": ["add", "list", "update", "cancel", "complete"]},
                "text": {"type": "string", "minLength": 1, "maxLength": 200, "description": "到期要执行的任务"},
                "delay_seconds": {"type": "number", "exclusiveMinimum": 0, "description": "从现在起多少秒后唤醒"},
                "due_at": {"type": "string", "description": "未来 ISO 8601 时间，建议携带时区，如 2026-11-14T09:00:00+08:00"},
                "todo_id": {"type": "string", "minLength": 1, "description": "查询返回的真实日程 id"},
                "include_completed": {"type": "boolean", "description": "list 时包含已完成或取消的历史，默认 false"},
            },
            "not": {"required": ["delay_seconds", "due_at"]},
        },
    )


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext, invocation: ToolInvocation, context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    session_id = context.session_id if context is not None else tool_ctx.runtime.session_id
    session = chat_manager.get_session_by_session_id(session_id)
    if session is None:
        return tool_ctx.build_failure_result("schedule", "找不到当前真实聊天流，不能创建或管理日程")
    args = invocation.arguments
    action = args["action"]
    if action not in {"add", "list", "update", "cancel", "complete"}:
        return tool_ctx.build_failure_result("schedule", "未知日程操作")
    command: Dict[str, Any] = {"action": action}
    if action == "list":
        command["include_completed"] = args.get("include_completed", False)
    if action in {"update", "cancel", "complete"}:
        if not args.get("todo_id"):
            return tool_ctx.build_failure_result("schedule", "请先查询并填写真实 todo_id")
        command["todo_id"] = args["todo_id"]
    if action in {"add", "update"}:
        if "delay_seconds" in args and "due_at" in args:
            return tool_ctx.build_failure_result("schedule", "delay_seconds 和 due_at 不能同时填写")
        if action == "add" and ("text" not in args or not any(k in args for k in ("delay_seconds", "due_at"))):
            return tool_ctx.build_failure_result("schedule", "新增日程必须填写任务内容和触发时间")
        if "due_at" in args and not isinstance(args["due_at"], str):
            return tool_ctx.build_failure_result("schedule", "due_at 必须为未来 ISO 8601 时间")
        if "text" in args:
            command["text"] = args["text"]
        command["options"] = {key: args[key] for key in ("due_at", "delay_seconds") if key in args}
        if action == "add":
            name = chat_manager.get_session_name(session_id)
            if not name:
                return tool_ctx.build_failure_result("schedule", "无法获取真实聊天名称")
            command["options"].update(kind="bot", session_id=session_id, session_name=name)
    try:
        result = await run_on_main_loop(schedule_service.command(command, session_id=session_id))
    except (ValueError, RuntimeError, OSError) as exc:
        return tool_ctx.build_failure_result("schedule", str(exc))
    # 返回明确时区的时间，同时保留原始字段供桌宠共用。
    items = result.get("items", [result["item"]] if "item" in result else [])
    for item in items:
        if item["due_at"] is not None:
            item["due_at_local"] = datetime.fromtimestamp(item["due_at"]).astimezone().isoformat()
    return tool_ctx.build_success_result("schedule", json.dumps(result, ensure_ascii=False), structured_content=result)
