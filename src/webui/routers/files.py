"""需要 WebUI 管理员登录的文件管理入口。"""

from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from src.files.service import disk_call, get_file_service
from src.files.http import FileRoute, receive_upload


router = APIRouter(prefix="/files", tags=["Files"], route_class=FileRoute)


@router.get("")
def list_files(session_id: Optional[str] = None, offset: int = 0, limit: int = 50):
    from src.chat.message_receive.chat_manager import chat_manager

    service = get_file_service()
    items = service.store.list(session_id, admin=True, offset=offset, limit=limit)
    for item in items:
        item["chats"] = [{"session_id": sid, "name": chat_manager.get_session_name(sid) or "已移除的聊天"}
                         for sid in item["session_ids"]]
    return {"items": items, "usage": service.store.usage()}


@router.get("/chats")
def file_chats():
    from src.chat.message_receive.chat_manager import chat_manager

    return [{"session_id": session.session_id, "name": chat_manager.get_session_name(session.session_id) or "未命名聊天"}
            for session in list(chat_manager.sessions.values())]


@router.post("/uploads")
async def upload(request: Request, session_id: str, name: str):
    service = get_file_service()
    service.require_session(session_id)
    asset = await disk_call(service.store.begin_upload, session_id, name, request.headers.get("content-type", "application/octet-stream"))
    return await receive_upload(request, asset)


@router.post("/chat-uploads")
async def chat_upload(request: Request, session_id: str, name: str):
    from src.webui.routers.chat.service import chat_manager, ensure_file_session

    connection = chat_manager.active_connections.get(session_id)
    if connection is None:
        raise ValueError("聊天连接已失效，请重新连接")
    session = await ensure_file_session(connection.user_id, connection.user_name, connection.virtual_config)
    return await upload(request, session.session_id, name)


@router.post("/{file_ref}/download")
async def prepare_download(file_ref: str):
    service = get_file_service()
    await service.ensure(file_ref, None, admin=True)
    url = await disk_call(service.download_url, file_ref, None, admin=True)
    # 浏览器使用当前站点，反向代理部署不依赖适配器配置的外部地址。
    return {"url": "/api/files/transfers/" + url.rsplit("/", 1)[-1]}


@router.get("/{file_ref}/text")
async def read_text(file_ref: str, offset: int = 0, limit: int = 12000, encoding: str = "utf-8-sig"):
    service = get_file_service()
    await service.ensure(file_ref, None, admin=True)
    return await disk_call(service.read_text, file_ref, None, admin=True, offset=offset, limit=limit, encoding=encoding)


class SendFileRequest(BaseModel):
    session_id: str


@router.post("/{file_ref}/send")
async def send_file(file_ref: str, body: SendFileRequest):
    return await get_file_service().send(file_ref, body.session_id, admin=True)


@router.delete("/{file_ref}")
def delete_file(file_ref: str, cache_only: bool = False):
    get_file_service().store.remove(file_ref, cache_only=cache_only)
    return {"success": True}
