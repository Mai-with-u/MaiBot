"""统一 Agent 工具：普通聊天只传话/查状态，桌面能力只交给当前后端。"""

from typing import Any, Dict, List, Optional

import asyncio
import json
import traceback

from src.computer.actions import action_schemas
from src.core.tooling import ToolAvailabilityContext, ToolContentItem, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec

from . import messaging
from .service import AgentService, agent_service

CHAT_TOOLS = {"agent_talk", "agent_status"}
DESKTOP_TOOLS = {"computer_desktop", "computer_observe", "computer_poll", "computer_cancel"}


def tool_specs() -> List[ToolSpec]:
    definitions = [
        ("agent_talk", "与独立 Agent 双向图文传话。普通聊天 target 留空；Agent 回信填写 agent_chats 返回的真实 session_id。回执表示入队并触发 Planner，不代表已经向 IM 发言。", {
            "text": {"type": "string"}, "target": {"type": "string"},
            "images": {"type": "array", "items": {"type": "string"}, "description": "普通聊天填写当前带图消息 ID 或图片 hash；Agent 填工作目录图片路径。启用桌面时可填 screenshot 或 C:\\MaiWorkspace 内的图片路径。"},
        }, ["text"]),
        ("agent_status", "查看 Agent 后端及运行状态，include_image=true 可读取已启用桌面的最近截图。", {
            "include_image": {"type": "boolean", "default": False},
        }, []),
        ("agent_chats", "列出已加载的真实聊天流及其名称和最近活跃时间。", {}, []),
        ("agent_recent", "读取真实聊天流历史，limit 默认 20，0 为全部。", {
            "target": {"type": "string"}, "limit": {"type": "integer", "minimum": 0, "default": 20},
        }, ["target"]),
        ("computer_desktop", "在 Windows Sandbox 内批量操作并返回截图。先 observe，坐标按最新截图。观察用 computer_observe，查询操作用 computer_poll。"
         "动作：observe；move/click/double_click(x,y,button)；drag(x,y,end_x,end_y,seconds,button)；mouse_down/mouse_up(button)；scroll(delta)；"
         "keys/key_down/key_up(keys 数组，Windows 键名，keys 可加 seconds 持续按住)；type(text)；open(path,arguments 数组，任意沙盒程序或 URL)；"
         "shell(command，沙盒内任意 PowerShell，返回 stdout/stderr/exit_code，工作目录 C:\\MaiWorkspace)；wait(seconds)。"
         "inspect(depth,limit) 读取 UIA；uia(runtime_id,operation,value) 操作控件；browser(method,target,params) 调用沙盒 CDP；"
         "wait_window(title,seconds)、wait_process(pid,seconds) 等待具体条件。"
         "没有探索步数、时长或应用名单限制。timeout 默认60秒，0一直等待；超时返回 running，用 computer_poll 查询，不重复输入。", {
            "actions": {"type": "array", "items": {"oneOf": action_schemas()}, "minItems": 1},
            "timeout": {"type": "number", "minimum": 0},
        }, ["actions"]),
        ("computer_observe", "独立获取当前桌面，长命令执行期间也可观察；返回截图编号、时间、活动窗口及尺寸。", {
            "timeout": {"type": "number", "minimum": 0},
        }, []),
        ("computer_poll", "查询原操作，不重复执行。running 是正常等待，completed/failed/cancelled 是最终结果。", {
            "operation_id": {"type": "string"}, "timeout": {"type": "number", "minimum": 0},
        }, ["operation_id"]),
        ("computer_cancel", "取消指定操作并释放按键，不关闭桌面；已完成的动作不会回滚。running 表示尚未确认取消。", {
            "operation_id": {"type": "string"},
        }, ["operation_id"]),
    ]
    return [ToolSpec(name=name, description=description, provider_name="agent", provider_type="builtin",
                     parameters_schema={"type": "object", "properties": properties, "required": required,
                                        "additionalProperties": False}, metadata={"visibility": "deferred"})
            for name, description, properties, required in definitions]


class AgentToolProvider:
    provider_name = "agent"
    provider_type = "builtin"

    def __init__(self, service: AgentService = agent_service) -> None:
        self.service = service

    async def list_tools(self, context: Optional[ToolAvailabilityContext] = None) -> List[ToolSpec]:
        if not self.service.config.enabled:
            return []
        trusted = context is not None and context.session_id == self.service.agent_id
        tools = []
        for spec in tool_specs():
            if not trusted and spec.name not in CHAT_TOOLS:
                continue
            if spec.name in DESKTOP_TOOLS and not self.service.desktop_enabled:
                continue
            if trusted:
                spec.metadata["visibility"] = "visible"
            tools.append(spec)
        return tools

    async def invoke(self, invocation: ToolInvocation, context: Optional[ToolExecutionContext] = None) -> ToolExecutionResult:
        try:
            if context is None:
                raise PermissionError("Agent 工具需要可信执行上下文。")
            self.service.require_agent()
            name, args = invocation.tool_name, invocation.arguments
            if name not in CHAT_TOOLS:
                self.service.require_owner(context.session_id)
            if name in DESKTOP_TOOLS:
                self.service.require_desktop()
            result: Dict[str, Any]
            if name == "agent_talk":
                result = await messaging.tell(self.service, context.session_id, args["text"], args.get("target", ""), args.get("images", []))
            elif name == "agent_status":
                result = self.service.status()
                if args.get("include_image", False) and self.service.desktop_enabled and self.service.bridge.last_frame is not None:
                    result = {**result, **self.service.bridge.last_frame}
            elif name == "agent_chats":
                result = {"chats": messaging.chats()}
            elif name == "agent_recent":
                result = await messaging.recent(args["target"], args.get("limit", 20))
            elif name == "computer_desktop":
                self.service.activity = {"reason": context.reasoning, "actions": args["actions"]}
                await self.service.persist()
                result = await self.service.desktop(args["actions"], args.get("timeout"))
            elif name == "computer_observe":
                result = await self.service.observe(args.get("timeout"))
            elif name == "computer_poll":
                result = await self.service.bridge.poll(args["operation_id"], args.get("timeout"))
            elif name == "computer_cancel":
                result = await self.service.bridge.cancel(args["operation_id"])
            else:
                raise ValueError(f"未知 Agent 工具：{name}")
            media = [ToolContentItem(content_type="image", data=result["image"], mime_type=result["mime_type"])] if "image" in result else []
            payload = {key: value for key, value in result.items() if key != "image"}
            return ToolExecutionResult(tool_name=name, success=result.get("success", True),
                                       content=await asyncio.to_thread(json.dumps, payload, ensure_ascii=False),
                                       structured_content=payload, content_items=media,
                                       error_message="\n".join(result[key] for key in ("error", "capture_error") if key in result))
        except (OSError, RuntimeError, ValueError, KeyError):
            return ToolExecutionResult(tool_name=invocation.tool_name, success=False, error_message=traceback.format_exc())

    async def close(self) -> None:
        """关闭普通聊天不结束独立 Agent。"""
