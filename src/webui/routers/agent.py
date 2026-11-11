"""外接 Agent 状态、消息和队列控制。"""

from typing import Literal
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.common.runtime_loop import run_on_main_loop
from src.agent.service import agent_service

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentControl(BaseModel):
    operation: Literal["pause", "resume"]


@router.get("")
def status():
    return agent_service.status()


@router.post("/control")
async def control(request: AgentControl):
    if agent_service.agent is None:
        raise HTTPException(status_code=409, detail="Agent 未启动，请先启用 agent.enabled。")
    return await run_on_main_loop(agent_service.control(request.operation))


@router.get("/events")
async def events(after: int = 0):
    if after < 0:
        raise HTTPException(status_code=422, detail="after 不能为负数")
    async def snapshot():
        if agent_service.agent is None:
            return {"events": [], "queued": [], "state": "disabled", "error": agent_service.error,
                    "pending": False, "backend": "acp"}
        agent = agent_service.require_agent()
        # 先取队列，再读已提交记录；输入即使此时被消费，也只会重复而不会漏掉，前端按 ID 合并。
        queued = [{"index": -1, **record} for record in agent.inbox]
        return {"events": await asyncio.to_thread(agent.history.events, after),
                "state": "paused" if agent_service.paused else agent.state,
                "error": agent_service.error, "backend": agent.backend,
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
