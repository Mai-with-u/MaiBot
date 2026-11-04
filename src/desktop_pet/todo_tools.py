"""主人聊天通过窗口进程管理同一份待办，不在主循环读写文件。"""

from typing import List, Optional
import json

from src.common.runtime_loop import run_on_main_loop
from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec

from .chat_tools import is_owner_chat
from .service import desktop_pet_service


class TodoToolProvider:
    provider_name = "desktop_pet_todos"
    provider_type = "builtin"

    async def list_tools(self, context: Optional[ToolAvailabilityContext] = None) -> List[ToolSpec]:
        if context is None or not is_owner_chat(context.session_id):
            return []
        return [ToolSpec(
            name="desktop_pet_todo", provider_name=self.provider_name, provider_type=self.provider_type,
            description="管理桌宠事项：bot 是到期唤醒自己的任务，user 是给主人的桌面提醒。支持绝对时间 due_at 或相对秒数 delay_seconds，不能同时传；省略时间保留原计划，due_at=null 取消定时。执行失败可查询 error，重新设置时间后重试。list 默认查询未完成，include_completed=true 包含归档；add 新增；update 修改；complete 完成并归档保留；delete 仅在明确要求删除时永久删除。修改、删除前先查询真实 todo_id，不猜 ID。condition_type=app 时程序每5秒检测应用，condition 填进程名或窗口标题关键字，不由模型猜测；natural 时 condition 保存自然语言触发条件，空字符串取消。条件与时间同时存在时，必须到时且条件成立。trigger 仅用于有当前证据的条件待办，必须提供真实 todo_id、revision 和 evidence；已触发不重复执行。桌宠须已启动。",
            parameters_schema={"type": "object", "additionalProperties": False, "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "update", "delete", "complete", "trigger"]},
                    "include_completed": {"type": "boolean", "description": "list 时是否包含已完成的历史事项，默认 false"},
                    "kind": {"type": "string", "enum": ["bot", "user"], "description": "默认 user"},
                    "due_at": {"type": ["string", "null"], "description": "未来 ISO 8601 日期时间，建议含时区；无时区按服务器本地时间；null 取消定时"},
                    "delay_seconds": {"type": "number", "exclusiveMinimum": 0, "description": "从现在起多少秒后触发"},
                    "condition_type": {"type": "string", "enum": ["natural", "app"], "description": "默认 natural 由模型判断；app 由程序匹配进程名或窗口标题，condition 填关键字，如 code.exe 或记事本"},
                    "condition": {"type": "string", "maxLength": 500, "description": "自然语言触发条件，如当桌面出现 VS Code 时；空字符串取消条件"},
                    "revision": {"type": "string", "description": "trigger 必填，来自当前上下文或 list 的真实版本"},
                    "evidence": {"type": "string", "minLength": 1, "maxLength": 1000, "description": "trigger 必填，说明当前条件成立的具体观察，不得凭空推断"},
                    "todo_id": {"type": "string", "description": "update、delete 必填，使用查询结果中的 id"},
                    "text": {"type": "string", "minLength": 1, "maxLength": 200,
                             "description": "add 必填；update 可选，新的事项内容"},
                }},
        )]

    async def invoke(self, invocation: ToolInvocation, context: Optional[ToolExecutionContext] = None) -> ToolExecutionResult:
        try:
            if context is None or not is_owner_chat(context.session_id):
                raise PermissionError("只有主人内部聊天可以管理待办")
            if invocation.tool_name != "desktop_pet_todo":
                raise ValueError("未知待办工具")
            args = invocation.arguments
            action = args["action"]
            if action not in {"list", "add", "update", "delete", "complete", "trigger"}:
                raise ValueError("未知待办操作")
            command = {"type": "todo", "action": action}
            if action == "list":
                include_completed = args.get("include_completed", False)
                if not isinstance(include_completed, bool):
                    raise ValueError("include_completed 必须为布尔值")
                command["include_completed"] = include_completed
            if action == "add" or (action == "update" and "text" in args):
                text = args.get("text")
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 200:
                    raise ValueError("待办内容必须为 1 至 200 字")
                command["text"] = text.strip()
            if action in {"add", "update"}:
                command["options"] = {key: args[key] for key in ("kind", "due_at", "delay_seconds", "condition", "condition_type") if key in args}
            if action in {"update", "delete", "complete", "trigger"}:
                todo_id = args.get("todo_id")
                if not isinstance(todo_id, str) or not todo_id.strip():
                    raise ValueError("请先查询待办并提供 todo_id")
                command["todo_id"] = todo_id
            if action == "trigger":
                result = await run_on_main_loop(desktop_pet_service.trigger_condition_todo(
                    command["todo_id"], args["revision"], args["evidence"],
                ))
            else:
                result = await run_on_main_loop(desktop_pet_service._command(command))
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True,
                content=json.dumps(result, ensure_ascii=False), structured_content=result)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            return ToolExecutionResult(tool_name=invocation.tool_name, success=False, error_message=str(exc))

    async def close(self) -> None:
        pass
