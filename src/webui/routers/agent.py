"""本体电脑状态与控制；桌面操作始终投递到 bot 循环。"""

from typing import Any, Dict, List, Literal
import asyncio
import base64

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from src.common.runtime_loop import run_on_main_loop
from src.agent.service import agent_service
from src.webui.dependencies import require_auth

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(require_auth)])


class ComputerControl(BaseModel):
    operation: Literal["pause", "resume", "takeover", "stop", "restart"]


@router.get("")
def status():
    return agent_service.status()


@router.post("/control")
async def control(request: ComputerControl):
    if agent_service.agent is None:
        raise HTTPException(status_code=409, detail="Agent 未启动，请先启用 agent.enabled。")
    return await run_on_main_loop(agent_service.control(request.operation))


@router.get("/frame")
async def frame():
    if agent_service.agent is None or not agent_service.desktop_enabled:
        raise HTTPException(status_code=409, detail="当前 Agent 未启用桌面能力")
    if agent_service.paused and not agent_service.bridge.running:
        raise HTTPException(status_code=409, detail="沙盒已停止，请先恢复电脑。")
    result = await run_on_main_loop(agent_service.bridge.observe(0))
    if not result["success"]:
        raise HTTPException(status_code=409, detail=result)
    return Response(base64.b64decode(result["image"]), media_type=result["mime_type"],
                    headers={"Cache-Control": "no-store", "X-Capture-Id": result["capture_id"]})


class DesktopInput(BaseModel):
    actions: List[Dict[str, Any]]


@router.post("/input")
async def desktop_input(request: DesktopInput):
    async def execute():
        agent_service.require_desktop()
        if not agent_service.takeover:
            raise HTTPException(status_code=409, detail="先接管电脑，再发送人工输入。")
        result = await agent_service.bridge.perform(request.actions, 5)
        return {key: value for key, value in result.items() if key != "image"}
    return await run_on_main_loop(execute())


@router.get("/operations/{operation_id}")
async def poll_operation(operation_id: str):
    async def poll():
        agent_service.require_desktop()
        result = await agent_service.bridge.poll(operation_id, 1)
        return {key: value for key, value in result.items() if key != "image"}
    return await run_on_main_loop(poll())


@router.get("/events")
async def events(after: int = 0):
    if after < 0:
        raise HTTPException(status_code=422, detail="after 不能为负数")
    async def snapshot():
        if agent_service.agent is None:
            return {"events": [], "queued": [], "state": "disabled", "error": agent_service.error,
                    "pending": False, "backend": "acp", "desktop_enabled": agent_service.desktop_enabled}
        agent = agent_service.require_agent()
        # 先取队列，再读已提交记录；输入即使此时被消费，也只会重复而不会漏掉，前端按 ID 合并。
        queued = [{"index": -1, **record} for record in agent.inbox]
        return {"events": await asyncio.to_thread(agent.history.events, after),
                "state": "paused" if agent_service.paused or agent_service.takeover else agent.state,
                "error": agent_service.error, "backend": agent.backend, "desktop_enabled": agent_service.desktop_enabled,
                "pending": agent.pending is not None, "queued": queued}
    return await run_on_main_loop(snapshot())


class AgentMessage(BaseModel):
    text: str


@router.post("/message")
async def message(request: AgentMessage):
    if not request.text.strip():
        raise HTTPException(status_code=422, detail="消息不能为空")
    if agent_service.agent is None:
        raise HTTPException(status_code=409, detail="Agent 未启动")
    async def enqueue():
        agent = agent_service.require_agent()
        return {"id": await agent.enqueue(request.text), "backend": agent.backend}
    return await run_on_main_loop(enqueue())
