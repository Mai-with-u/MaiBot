"""为当前真实聊天管理日程、待办和主人桌面提醒。"""

from datetime import datetime
from typing import Any, Dict, Optional

import json

from src.chat.message_receive.chat_manager import chat_manager
from src.common.runtime_loop import run_on_main_loop
from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.desktop_pet.chat_tools import is_owner_chat
from src.desktop_pet.service import desktop_pet_service
from src.services.schedules.service import schedule_service

from .context import BuiltinToolRuntimeContext


def get_tool_spec(context: Optional[ToolAvailabilityContext] = None) -> ToolSpec:
    """按当前聊天的权限提供统一日程工具。"""
    owner = context is not None and is_owner_chat(context.session_id)
    actions = ["add", "list", "update", "cancel", "complete"]
    properties: Dict[str, Any] = {
        "action": {"type": "string", "enum": actions},
        "text": {"type": "string", "minLength": 1, "maxLength": 200, "description": "事项内容，add 必填"},
        "delay_seconds": {"type": "number", "exclusiveMinimum": 0, "description": "多少秒后触发，与 due_at 二选一"},
        "due_at": {"type": ["string", "null"] if owner else "string",
                   "description": "未来 ISO 8601 时间，建议带时区" + ("；null 取消定时" if owner else "")},
        "todo_id": {"type": "string", "minLength": 1, "description": "操作已有事项时填写查询返回的真实 id"},
        "include_completed": {"type": "boolean", "description": "list 时包含已完成或取消的事项，默认 false"},
    }
    description = "创建，查询或者修改日程，日程可以在定时唤起提醒，当需要在特定时间点做事时使用。"
    if owner:
        actions.extend(["delete", "trigger"])
        description += "也可记录无定时待办、设置条件或给主人发桌面提醒。delete 仅用于明确要求永久删除的事项。"
        properties.update({
            "kind": {"type": "string", "enum": ["bot", "user"],
                     "description": "bot 唤醒你执行任务（默认）；user 给主人显示桌面提醒"},
            "condition_type": {"type": "string", "enum": ["natural", "app"],
                               "description": "natural 由你判断条件（默认）；app 由程序检测应用"},
            "condition": {"type": "string", "maxLength": 500,
                          "description": "触发条件；app 填进程名或窗口标题关键字；空字符串取消条件"},
            "revision": {"type": "string", "description": "trigger 必填，使用当前快照或查询返回的版本"},
            "evidence": {"type": "string", "minLength": 1, "maxLength": 1000,
                         "description": "trigger 必填，说明条件成立的实际依据"},
        })
    else:
        description += "新增必须填写未来时间。"
    description += "修改前先查询真实 todo_id；update 省略时间保留计划。创建日程只登记任务。"
    return ToolSpec(
        name="schedule", provider_name="maisaka_builtin", provider_type="builtin",
        description=description,
        parameters_schema={
            "type": "object", "additionalProperties": False, "required": ["action"],
            "properties": properties, "not": {"required": ["delay_seconds", "due_at"]},
        },
    )


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext, invocation: ToolInvocation, context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    session_id = context.session_id if context is not None else tool_ctx.runtime.session_id
    session = chat_manager.get_session_by_session_id(session_id)
    if session is None:
        return tool_ctx.build_failure_result("schedule", "找不到当前真实聊天流，不能创建或管理日程")
    owner = is_owner_chat(session_id)
    args = invocation.arguments
    try:
        action = args["action"]
        actions = {"add", "list", "update", "cancel", "complete"}
        if owner:
            actions.update({"delete", "trigger"})
        if action not in actions:
            raise ValueError("当前聊天不支持此日程操作")
        if not owner and any(key in args for key in ("kind", "condition", "condition_type", "revision", "evidence")):
            raise ValueError("桌面提醒和条件待办仅限主人内部聊天")
        command: Dict[str, Any] = {"action": action}
        if action == "list":
            include_completed = args.get("include_completed", False)
            if not isinstance(include_completed, bool):
                raise ValueError("include_completed 必须为布尔值")
            command["include_completed"] = include_completed
        if action in {"update", "cancel", "complete", "delete", "trigger"}:
            todo_id = args.get("todo_id")
            if not isinstance(todo_id, str) or not todo_id.strip():
                raise ValueError("请先查询并填写真实 todo_id")
            command["todo_id"] = todo_id
        if action in {"add", "update"}:
            if action == "add" or "text" in args:
                text = args.get("text")
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 200:
                    raise ValueError("事项内容必须为 1 至 200 字")
                command["text"] = text.strip()
            if not owner:
                if action == "add" and not any(key in args for key in ("delay_seconds", "due_at")):
                    raise ValueError("新增日程必须填写触发时间")
                if "due_at" in args and not isinstance(args["due_at"], str):
                    raise ValueError("due_at 必须为未来 ISO 8601 时间")
            command["options"] = {
                key: args[key] for key in ("due_at", "delay_seconds", "kind", "condition", "condition_type") if key in args
            }
            if action == "add":
                name = chat_manager.get_session_name(session_id)
                if not name:
                    raise ValueError("无法获取真实聊天名称")
                command["options"].update(
                    kind=args.get("kind", "bot"), session_id=session_id, session_name=name,
                )
        if action == "trigger":
            result = await run_on_main_loop(desktop_pet_service.trigger_condition_todo(
                command["todo_id"], args["revision"], args["evidence"], session_id=session_id,
            ))
        else:
            result = await run_on_main_loop(schedule_service.command(command, session_id=session_id))
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        return tool_ctx.build_failure_result("schedule", str(exc))
    # 返回明确时区的时间，同时保留原始字段供桌宠共用。
    items = result.get("items", [result["item"]] if "item" in result else [])
    for item in items:
        if item["due_at"] is not None:
            item["due_at_local"] = datetime.fromtimestamp(item["due_at"]).astimezone().isoformat()
    return tool_ctx.build_success_result("schedule", json.dumps(result, ensure_ascii=False), structured_content=result)
