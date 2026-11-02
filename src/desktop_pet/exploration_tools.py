"""探索产生的方法以候选图式保存，供主人后续对话检索。"""

from typing import List, Optional
import asyncio
import json

from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec

from .chat_tools import is_owner_chat
from .exploration import exploration_tasks, schema_records


class ExplorationToolProvider:
    provider_name = "desktop_pet_exploration"
    provider_type = "builtin"

    async def list_tools(self, context: Optional[ToolAvailabilityContext] = None) -> List[ToolSpec]:
        if context is None or not is_owner_chat(context.session_id):
            return []
        task_tool = ToolSpec(
            name="desktop_pet_exploration_task", provider_name=self.provider_name, provider_type=self.provider_type,
            description="查询或上报探索任务。list 查看真实任务 ID；get 查看详情；progress 上报已发生的步骤；complete 必须提供实际结果；fail 记录失败原因。仅更新给定 task_id，不把普通聊天误记成探索进度，不把计划当作完成。结果支持 Markdown 标题、列表、代码块与链接，可列出产物路径。",
            parameters_schema={"type": "object", "additionalProperties": False, "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": ["list", "get", "progress", "complete", "fail"]},
                    "task_id": {"type": "string", "description": "除 list 外必填，使用探索提示或 list 中的真实 ID"},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "result": {"type": "string", "maxLength": 30000, "description": "complete 必填，实际结果、证据、限制与产物链接或路径"},
                }},
        )
        return [task_tool, ToolSpec(
            name="desktop_pet_schema", provider_name=self.provider_name, provider_type=self.provider_type,
            description=("查询或保存主人会话的候选图式。相关任务先 list 检索已有方法；save 保存目标、适用条件、"
                         "步骤、成功判据和实际实验结果。evidence 必须如实记录工具结果及失败限制，不能把推测写成验证。"
                         "保存仅表示候选经验，不授予权限；复用时重新检查条件。"),
            parameters_schema={"type": "object", "additionalProperties": False, "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": ["list", "save"]},
                    "title": {"type": "string", "minLength": 1, "maxLength": 100},
                    "workflow": {"type": "string", "minLength": 1, "maxLength": 8000,
                                 "description": "目标、适用条件、参数、步骤、成功判据及失败边界"},
                    "evidence": {"type": "string", "minLength": 1, "maxLength": 8000,
                                 "description": "实际实验的工具调用、结果及未验证部分"},
                }},
        )]

    async def invoke(self, invocation: ToolInvocation,
                     context: Optional[ToolExecutionContext] = None) -> ToolExecutionResult:
        try:
            if context is None or not is_owner_chat(context.session_id):
                raise PermissionError("只有主人内部聊天可以访问探索图式")
            if invocation.tool_name == "desktop_pet_exploration_task":
                args = invocation.arguments
                action = args["action"]
                if action not in {"list", "get", "progress", "complete", "fail"}:
                    raise ValueError("未知探索任务操作")
                result = await asyncio.to_thread(exploration_tasks, context.session_id, action,
                    args.get("task_id", ""), summary=args.get("summary", ""), result=args.get("result", ""))
                if action == "list":
                    result = {"items": [{key: item[key] for key in ("id", "goal", "status", "updated_at")} for item in result]}
                return ToolExecutionResult(tool_name=invocation.tool_name, success=True,
                    content=json.dumps(result, ensure_ascii=False), structured_content=result)
            if invocation.tool_name != "desktop_pet_schema":
                raise ValueError("未知探索工具")
            args = invocation.arguments
            record = None
            if args["action"] == "save":
                record = {}
                for key, limit in (("title", 100), ("workflow", 8000), ("evidence", 8000)):
                    value = args.get(key)
                    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
                        raise ValueError(f"{key} 必须为 1 至 {limit} 字")
                    record[key] = value.strip()
            elif args["action"] != "list":
                raise ValueError("未知图式操作")
            result = {"items": await asyncio.to_thread(schema_records, context.session_id, record)}
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True,
                                       content=json.dumps(result, ensure_ascii=False), structured_content=result)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return ToolExecutionResult(tool_name=invocation.tool_name, success=False, error_message=str(exc))

    async def close(self) -> None:
        pass
