"""主人内部聊天的跨会话工具，不依赖独立 Agent 或桌宠窗口是否启动。"""

from typing import Any, Dict, List, Optional
import json

from src.chat.message_receive.chat_manager import chat_manager
from src.config.config import global_config
from src.core.internal_chat import INTERNAL_CHAT_PLATFORM, INTERNAL_CHAT_USER_ID
from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec


def is_owner_chat(session_id: str) -> bool:
    session = chat_manager.get_session_by_session_id(session_id)
    return bool(session is not None
                and session.platform == INTERNAL_CHAT_PLATFORM and not session.group_id
                and session.user_id == INTERNAL_CHAT_USER_ID)


def allowed(session_id: str) -> bool:
    return global_config.desktop_pet.chat_tools_enabled and is_owner_chat(session_id)


class ChatSessionToolProvider:
    provider_name = "chat_sessions"
    provider_type = "builtin"

    async def list_tools(self, context: Optional[ToolAvailabilityContext] = None) -> List[ToolSpec]:
        if context is None or not allowed(context.session_id):
            return []
        definitions = [
            ("list_chat_sessions", "查询可传话的真实聊天流。先按名称查询；重名必须确认，不能猜测 ID。", {
                "keyword": {"type": "string"}, "platform": {"type": "string"},
                "chat_type": {"type": "string", "enum": ["group", "private"]},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            }, []),
            ("handoff_to_chat", "向查询得到的聊天流 Agent 传达用户交代的任务并唤醒。回执只表示入队，不表示已发送消息；不要自行循环转交。", {
                "target_session_id": {"type": "string"}, "instruction": {"type": "string"},
                "image_refs": {"type": "array", "items": {"type": "string"},
                               "description": "当前聊天带图消息 ID 或图片 hash"},
            }, ["target_session_id", "instruction"]),
        ]
        return [ToolSpec(name=name, description=description, provider_name=self.provider_name,
                         provider_type=self.provider_type, parameters_schema={"type": "object",
                         "properties": properties, "required": required, "additionalProperties": False})
                for name, description, properties, required in definitions]

    async def invoke(self, invocation: ToolInvocation, context: Optional[ToolExecutionContext] = None) -> ToolExecutionResult:
        try:
            if context is None or not allowed(context.session_id):
                raise PermissionError("聊天流工具未启用或当前会话无权使用。")
            from src.chat.heart_flow.heartflow_manager import heartflow_manager

            args = invocation.arguments
            result: Dict[str, Any]
            if invocation.tool_name == "list_chat_sessions":
                offset, limit = args.get("offset", 0), args.get("limit", 20)
                if not isinstance(offset, int) or not isinstance(limit, int) or offset < 0 or not 1 <= limit <= 50:
                    raise ValueError("分页参数无效。")
                sessions = []
                for label, session_id in chat_manager.get_named_session_options().items():
                    session = chat_manager.get_session_by_session_id(session_id)
                    if session is None or session_id == context.session_id:
                        continue
                    kind = "group" if session.is_group_session else "private"
                    if (args.get("keyword", "").casefold() not in label.casefold()
                            or args.get("platform", session.platform) != session.platform
                            or args.get("chat_type", kind) != kind):
                        continue
                    if await heartflow_manager._is_session_allowed(session):
                        sessions.append({"session_id": session_id, "name": label, "platform": session.platform,
                                         "chat_type": kind, "target_id": session.group_id if kind == "group" else session.user_id})
                result = {"sessions": sessions[offset:offset + limit], "total": len(sessions)}
            elif invocation.tool_name == "handoff_to_chat":
                target = args["target_session_id"]
                instruction = args["instruction"].strip()
                session = chat_manager.get_session_by_session_id(target)
                if session is None or target == context.session_id:
                    raise ValueError("请选择其他真实聊天流。")
                if not instruction or len(instruction) > 10000:
                    raise ValueError("任务内容应为 1 至 10000 字。")
                if not await heartflow_manager._is_session_allowed(session):
                    raise PermissionError("适配器策略禁止向此聊天流传话。")
                from src.agent.messaging import source_images

                refs = args.get("image_refs", [])
                media = []
                if refs:
                    available = await source_images(context.session_id)
                    for ref in refs:
                        if ref not in available:
                            raise ValueError(f"找不到图片引用：{ref}")
                        media.extend(available[ref])
                runtime = await heartflow_manager.get_or_create_heartflow_chat(target)
                source = chat_manager.get_session_name(context.session_id)
                receipt = runtime.enqueue_internal_task(source_kind="desktop_pet_handoff",
                    text=f"[来自主人聊天 {source} 的内部任务，不是本聊天成员的发言]\n{instruction}",
                    reason="主人跨聊天流传话", images=media)
                result = {**receipt, "target_session_id": target, "name": chat_manager.get_session_name(target),
                          "notice": "任务已入队，尚不代表已经发送消息。"}
            else:
                raise ValueError("未知聊天流工具。")
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True,
                                       content=json.dumps(result, ensure_ascii=False), structured_content=result)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            return ToolExecutionResult(tool_name=invocation.tool_name, success=False, error_message=str(exc))

    async def close(self) -> None:
        pass
