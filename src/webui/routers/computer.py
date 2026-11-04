"""共享电脑 WebUI；所有沙盒操作投递到 bot 主循环。"""

from typing import Any, Awaitable, Callable, Dict, List, Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
import base64

from src.common.runtime_loop import run_on_main_loop
from src.computer.service import computer_service

router = APIRouter(prefix="/computer", tags=["computer"])


class ComputerControl(BaseModel):
    operation: Literal["pause", "resume", "takeover", "stop", "restart"]


class DesktopInput(BaseModel):
    actions: List[Dict[str, Any]]


@router.get("")
def status():
    return computer_service.status()


async def _execute(operation: Callable[[], Awaitable[Dict[str, Any]]]) -> Dict[str, Any]:
    async def call():
        try:
            return await operation()
        except (RuntimeError, ConnectionError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await run_on_main_loop(call())


@router.post("/control")
async def control(request: ComputerControl):
    return await _execute(lambda: computer_service.control(request.operation))


@router.get("/frame")
async def frame():
    result = await _execute(lambda: computer_service.observe(0))
    if not result["success"]:
        raise HTTPException(status_code=409, detail=result)
    return Response(base64.b64decode(result["image"]), media_type=result["mime_type"],
                    headers={"Cache-Control": "no-store", "X-Capture-Id": result["capture_id"]})


@router.post("/input")
async def desktop_input(request: DesktopInput):
    result = await _execute(lambda: computer_service.desktop(request.actions, 5, manual=True))
    return {key: value for key, value in result.items() if key != "image"}


@router.get("/operations/{operation_id}")
async def poll_operation(operation_id: str):
    return await _execute(lambda: computer_service.poll(operation_id, 1))
