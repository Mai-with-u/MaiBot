"""外接 Agent 传话与状态工具；电脑工具由独立 Provider 提供。"""

from typing import Any, Dict, List, Optional

import asyncio
import json
import traceback

from src.core.local_operator import normalize_operator_permissions
from src.core.tooling import ToolAvailabilityContext, ToolContentItem, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec

from . import messaging
from .service import AgentService, agent_service

CHAT_TOOLS = {"agent_talk", "agent_status"}


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

    def _allowed_chat(self, context: Optional[ToolAvailabilityContext | ToolExecutionContext]) -> bool:
        """判断当前聊天是否有权指挥外接 Agent。"""

        allow_users = normalize_operator_permissions(self.service.config.allow_users)
        allow_groups = normalize_operator_permissions(self.service.config.allow_groups)
        if not allow_users and not allow_groups:
            return True
        if context is None:
            return False
        if context.session_id and context.session_id == self.service.agent_id:
            return True
        platform = (context.platform or "").strip().lower()
        group_id = (context.group_id or "").strip()
        user_id = (context.user_id or "").strip()
        if group_id:
            return f"{platform}:{group_id}" in allow_groups
        return bool(user_id) and f"{platform}:{user_id}" in allow_users

    async def list_tools(self, context: Optional[ToolAvailabilityContext] = None) -> List[ToolSpec]:
        if not self.service.config.enabled:
            return []
        if not self._allowed_chat(context):
            return []
        trusted = context is not None and context.session_id == self.service.agent_id
        tools = []
        for spec in tool_specs():
            if not trusted and spec.name not in CHAT_TOOLS:
                continue
            if trusted:
                spec.metadata["visibility"] = "visible"
            tools.append(spec)
        return tools

    async def invoke(self, invocation: ToolInvocation, context: Optional[ToolExecutionContext] = None) -> ToolExecutionResult:
        try:
            if context is None:
                raise PermissionError("Agent 工具需要可信执行上下文。")
            if not self._allowed_chat(context):
                raise PermissionError("当前聊天没有指挥外接 Agent 的权限。")
            self.service.require_agent()
            name, args = invocation.tool_name, invocation.arguments
            if name not in CHAT_TOOLS:
                self.service.require_owner(context.session_id)
            result: Dict[str, Any]
            if name == "agent_talk":
                result = await messaging.tell(self.service, context.session_id, args["text"], args.get("target", ""), args.get("images", []))
            elif name == "agent_status":
                result = self.service.status()
                if args.get("include_image", False) and self.service.computer.config.enabled and self.service.computer.bridge.last_frame is not None:
                    result = {**result, **self.service.computer.bridge.last_frame}
            elif name == "agent_chats":
                result = {"chats": messaging.chats()}
            elif name == "agent_recent":
                result = await messaging.recent(args["target"], args.get("limit", 20))
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
