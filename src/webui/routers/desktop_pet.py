"""桌宠皮肤管理：读写与校验在工作线程进行，预览不修改当前皮肤。"""

from zipfile import BadZipFile
from fastapi import APIRouter, HTTPException, Request
import asyncio

from src.common.paths import get_instance_root
from src.desktop_pet.thinking_caption import fast_model_configured
from src.desktop_pet.skins import MAX_BYTES, find_skin, install_zip, list_skins

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
