"""宿主保存在线迁移预览；在线校验并调用本体事务合并服务。"""

from pathlib import Path
from typing import Any, Callable, Dict, Set
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

import asyncio
import json

from src.services.person_migration_plan import dumps, loads
from src.services.person_migration_service import _atomic_write, execute, preview

from .maintenance import HostMaintenance


class ConfirmRequest(BaseModel):
    preview_id: str = Field(min_length=32, max_length=32)


class InjectionRequest(BaseModel):
    source: str = Field(min_length=1, max_length=255)
    target: str = Field(min_length=1, max_length=255)
    decisions: Dict[str, str] = Field(default_factory=dict)


def _state_path(root: Path) -> Path:
    return root / "temp/person-migration-preview.json"


def _read(root: Path) -> Dict[str, Any]:
    path = _state_path(root)
    return loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(root: Path, state: Dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, dumps(state))


def _public(state: Dict[str, Any]) -> Dict[str, Any]:
    if not state:
        return {}
    plan = state.get("plan")
    if state.get("status") == "preview" and state.get("kind") == "injection" and (not plan or plan.get("version") != 3):
        return {"kind": "injection", "status": "invalid", "error": "预览已失效，请重新预览"}
    result = {key: value for key, value in state.items() if key != "plan"} | ({
        "conflicts": [{"source_id": plan["identity"].get("source"), "target_id": plan["identity"].get("target"), **item}
                      for item in plan["conflicts"]],
        "decisions": plan["decisions"], "ready": plan["ready"],
        "relations": [{"source": plan["identity"]["source"], "target": plan["identity"]["target"]}],
        "counts": {item["name"]: item["counts"] for item in plan["databases"]},
    } if plan else {})
    return json.loads(dumps(result))


def register_person_migration(app: FastAPI, maintenance: HostMaintenance, require_session: Callable) -> None:
    supervisor = maintenance.supervisor

    async def root_for(bot_id: str) -> Path:
        try:
            bot = await asyncio.to_thread(supervisor.workspace.registry.get_bot, bot_id)
            return bot.directory.resolve()
        except (KeyError, ValueError) as exc:
            raise HTTPException(404, "未找到 Bot") from exc

    prefix = "/api/host/bots/{bot_id}/proxy/api/webui/person-migrations/user-injection"

    @app.get(prefix, dependencies=[Depends(require_session)])
    async def get_preview(bot_id: str) -> Dict[str, Any]:
        root = await root_for(bot_id)
        try:
            return {"preview": await asyncio.to_thread(lambda: _public(_read(root)))}
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    async def operation(bot_id: str, action: str, request: Any) -> Dict[str, Any]:
        if maintenance.busy:
            raise HTTPException(409, "宿主正在执行其他维护操作")
        root = await root_for(bot_id)
        async with maintenance._lock:
            state = await asyncio.to_thread(_read, root)
            if action == "execute" and state.get("kind", "union") != "injection":
                raise HTTPException(410, "Union 身份迁移已停用，请取消旧预览")
            if action != "preview" and (not state or state["preview_id"] != request.preview_id or state["status"] != "preview"):
                raise HTTPException(409, "预览不存在、已执行或已取消，请刷新")
            if action == "preview":
                if state.get("status") == "preview" and state.get("kind", "union") != "injection":
                    raise HTTPException(409, "请先完成或取消旧迁移预览")
                if (request.source != request.source.strip() or request.target != request.target.strip()
                        or request.source == request.target):
                    raise HTTPException(422, "来源和目标须为不同的用户 ID，且不能包含首尾空白")
                try:
                    plan = await asyncio.to_thread(preview, root, "qq", request.source, request.target,
                                                   decisions=request.decisions)
                    state = {"preview_id": uuid4().hex, "status": "preview", "kind": "injection",
                             "plan": plan}
                    await asyncio.to_thread(_save, root, state)
                    return _public(state)
                except (ValueError, RuntimeError, OSError) as exc:
                    raise HTTPException(409, str(exc)) from exc
            if action == "cancel":
                state.update(status="cancelled")
                state.pop("plan", None)
                await asyncio.to_thread(_save, root, state)
                return _public(state)
            if "plan" not in state or not state["plan"]["ready"]:
                raise HTTPException(409, "请先完成预览和冲突选择")
            def run() -> Dict[str, Any]:
                try:
                    state["record"] = str(execute(state["plan"], expected_root=root))
                except ValueError:
                    identity = state["plan"]["identity"]
                    state["plan"] = preview(root, identity["platform"], identity["source"], identity["target"])
                    state["preview_id"] = uuid4().hex
                    _save(root, state)
                    raise
                state["status"] = "completed"
                del state["plan"]
                _save(root, state)
                return _public(state)

            task = asyncio.create_task(asyncio.to_thread(run))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            except (ValueError, RuntimeError, OSError) as exc:
                raise HTTPException(409, str(exc)) from exc

    # 路由任务独立于浏览器连接，客户端离开不会中断数据库事务。
    tasks: Set[asyncio.Task[Dict[str, Any]]] = set()

    async def dispatch(bot_id: str, action: str, request: Any) -> Dict[str, Any]:
        task = asyncio.create_task(operation(bot_id, action, request))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return await asyncio.shield(task)

    @app.post(prefix + "/execute", dependencies=[Depends(require_session)])
    async def execute_route(bot_id: str, request: ConfirmRequest) -> Dict[str, Any]:
        return await dispatch(bot_id, "execute", request)

    @app.post(prefix + "/preview", dependencies=[Depends(require_session)])
    async def injection_route(bot_id: str, request: InjectionRequest) -> Dict[str, Any]:
        return await dispatch(bot_id, "preview", request)

    @app.post(prefix + "/cancel", dependencies=[Depends(require_session)])
    async def cancel_route(bot_id: str, request: ConfirmRequest) -> Dict[str, Any]:
        return await dispatch(bot_id, "cancel", request)
