"""桌宠皮肤管理与首页卡片：读写与校验在工作线程进行，预览不修改当前皮肤。"""

from typing import Any, Dict
from zipfile import BadZipFile
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import asyncio

from src.common.paths import get_instance_root
from src.desktop_pet.remote_server import on_main_loop
from src.desktop_pet.service import desktop_pet_service
from src.desktop_pet.thinking_caption import fast_model_configured
from src.desktop_pet.skins import MAX_BYTES, find_skin, install_zip, list_skins
from src.desktop_pet.todos import TodoStore

router = APIRouter(prefix="/desktop-pet", tags=["desktop-pet"])


@router.get("/skins")
def skins():
    return list_skins(get_instance_root())


@router.get("/skins/{skin_id}")
def skin(skin_id: str):
    try:
        return find_skin(get_instance_root(), skin_id)
    except (BadZipFile, ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/skins")
async def install(request: Request):
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BYTES:
            raise HTTPException(413, "皮肤包不能超过 32 MiB")
        chunks.append(chunk)
    try:
        return await asyncio.to_thread(install_zip, get_instance_root(), b"".join(chunks))
    except (BadZipFile, ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/capabilities")
def capabilities():
    return {"fast_model_configured": fast_model_configured()}


# ─── 首页桌宠卡片 ─────────────────────────────────────────────

TODO_PREVIEW_LIMIT = 20
CHAT_MAX_LENGTH = 2000


class VisibilityBody(BaseModel):
    visible: bool


class ChatBody(BaseModel):
    text: str


def _todo_summary() -> Dict[str, Any]:
    """读取未完成的待办，只读不写；本机窗口和远端客户端共用同一份待办文件。"""
    path = get_instance_root() / "data" / "plugins" / "sengokucola.desktop-pet" / "todos.json"
    if not path.exists():
        return {"pending": 0, "items": []}
    items = [item for item in TodoStore(path).list() if item["status"] != "completed"]
    preview = [{"id": item["id"], "text": item["text"], "due_at": item.get("due_at")} for item in items]
    return {"pending": len(items), "items": preview[:TODO_PREVIEW_LIMIT]}


@router.get("/status")
async def status():
    state = desktop_pet_service.status()
    try:
        todos = await asyncio.to_thread(_todo_summary)
    except (OSError, ValueError, KeyError, TypeError):
        # 待办文件读不出来时卡片照常显示状态，只是没有待办数
        todos = {"pending": 0, "items": []}
    return {**state, "todos": todos}


@router.post("/visibility")
async def visibility(body: VisibilityBody):
    state = desktop_pet_service.status()
    if not state["enabled"]:
        raise HTTPException(409, "桌宠没有开启")
    if not state["ready"]:
        raise HTTPException(409, "桌宠窗口还没有就绪")
    try:
        return await on_main_loop(lambda: desktop_pet_service.control("show" if body.visible else "hide"))
    except (RuntimeError, ConnectionError, TimeoutError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/chat")
async def chat(body: ChatBody):
    """首页卡片给桌宠说话：和桌宠窗口里输入一样投递到主人聊天，回复会在桌宠和网页同步显示。"""
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "消息不能为空")
    if len(text) > CHAT_MAX_LENGTH:
        raise HTTPException(422, f"消息不能超过 {CHAT_MAX_LENGTH} 字")
    if not desktop_pet_service.status()["enabled"]:
        raise HTTPException(409, "桌宠没有开启")
    from src.webui.routers.chat.service import relay_plugin_chat_message

    message_id = await relay_plugin_chat_message("builtin.desktop_pet", text)
    return {"message_id": message_id}
