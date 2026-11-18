"""独立 Agent 与 IM 共用的适配层：真实会话查询、传话和图片引用。"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

import asyncio
import json
import time

from src.chat.message_receive.chat_manager import BotChatSession, chat_manager
from src.common.data_models.message_component_data_model import ImageComponent
from src.common.prompt_i18n import load_prompt

if TYPE_CHECKING:
    from .service import AgentService


def resolve_chat(session_id: str) -> BotChatSession:
    session = chat_manager.sessions.get(session_id)
    if session is None:
        raise ValueError("找不到真实聊天流，请使用聊天列表工具查询。")
    return session


def chats() -> List[Dict[str, Any]]:
    return [{"session_id": session.session_id, "name": chat_manager.get_session_name(session.session_id),
             "platform": session.platform, "last_active": str(session.last_active_timestamp)}
            for session in sorted(chat_manager.sessions.values(),
                                  key=lambda item: (item.last_active_timestamp is not None, item.last_active_timestamp), reverse=True)]


async def recent(target: str, limit: int) -> Dict[str, Any]:
    resolve_chat(target)
    if limit < 0:
        raise ValueError("limit 不能为负数，0 表示全部。")
    def query() -> str:
        from src.services.message_service import build_readable_messages, get_messages_by_time_in_chat
        return build_readable_messages(get_messages_by_time_in_chat(target, 0, time.time(), limit=limit))
    return {"name": chat_manager.get_session_name(target), "messages": await asyncio.to_thread(query)}


async def source_images(caller: str) -> Dict[str, List[ImageComponent]]:
    from src.chat.heart_flow.heartflow_manager import heartflow_manager
    from src.maisaka.context.message_id_alias import to_display_message_id
    from src.maisaka.context.messages import ContentContextMessage

    runtime = await heartflow_manager.get_or_create_heartflow_chat(caller)
    available: Dict[str, List[ImageComponent]] = {}
    for message in runtime._chat_history:
        if not isinstance(message, ContentContextMessage):
            continue
        media = runtime._collect_sent_image_components(message.media_sequence.components)
        if media and message.message_id:
            available[message.message_id] = media
            available[to_display_message_id(message.message_id)] = media
        for image in media:
            available[image.binary_hash] = [image]
    return available


async def tell(
    service: "AgentService", caller: str, text: str, target: str = "",
    images: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from .images import resolve_images

    agent = service.require_agent()
    if not text.strip() and not images:
        raise ValueError("传话内容不能为空。")
    from_agent = caller == service.agent_id
    if from_agent:
        resolve_chat(target)
    else:
        resolve_chat(caller)
        if target and target != service.agent_id:
            raise PermissionError("普通聊天只能向指定 Agent 传话。")
        target = service.agent_id
    media, attachments = await resolve_images(service, caller, images if images is not None else [])
    relay_id = uuid4().hex
    name = service.name if from_agent else chat_manager.get_session_name(caller)
    relay_text = f"[传话 {relay_id}，来自 {name}，回复目标 {caller}]\n{text}\n"
    if attachments:
        relay_text += "附图：" + json.dumps(attachments, ensure_ascii=False) + "\n"
    relay_text += f"这是传话内容，可通过 {service.relay_tool} 回复；按需要回复，不必礼貌性来回传话。"
    if from_agent:
        from src.chat.heart_flow.heartflow_manager import heartflow_manager

        runtime = await heartflow_manager.get_or_create_heartflow_chat(target)
        receipt = runtime.enqueue_internal_task(source_kind=service.relay_kind, text=relay_text, reason=service.name + "传话", images=media)
    else:
        receipt = {"task_id": await agent.enqueue(relay_text, media, reply_to=caller), "queued": True,
                   "paused": service.paused}
        if service.paused:
            receipt["error"] = service.error
            receipt["notice"] = "Agent 已暂停，消息仅入队；请在 WebUI 恢复后执行。"
    return {"delivered": True, "relay_id": relay_id, "target": target,
            "name": chat_manager.get_session_name(target) if from_agent else service.name,
            "images": attachments, **receipt}


async def report_failure(service: "AgentService", target: str, task_ids: List[str], error: str) -> None:
    """将失败事实回报给原聊天的 Planner，不冒充外部 Agent 的回复。"""
    from src.chat.heart_flow.heartflow_manager import heartflow_manager

    resolve_chat(target)
    message = await asyncio.to_thread(
        load_prompt, "agent_acp_failure", agent_name=service.name,
        task_ids=", ".join(task_ids), error=error,
    )
    runtime = await heartflow_manager.get_or_create_heartflow_chat(target)
    runtime.enqueue_internal_task(
        source_kind=service.relay_kind,
        text=message,
        reason=service.name + "执行失败",
    )
