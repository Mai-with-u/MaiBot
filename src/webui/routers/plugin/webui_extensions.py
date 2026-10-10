"""声明式插件页面及其专用 API 网关。"""

from typing import Any, Dict, Literal, Optional, Tuple

import asyncio
import json

from fastapi import APIRouter, Cookie, File, Form, HTTPException, Request, UploadFile
from fastapi.routing import APIRoute
from pydantic import Field

from src.common.runtime_loop import run_on_main_loop
from src.plugin_runtime.component_query import component_query_service
from src.plugin_runtime.host.supervisor import PluginSupervisor
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.plugin_runtime.webui_schema import Scalar, StrictModel, WebUIExtension
from src.plugin_runtime.upload_store import MAX_BYTES, upload_store

from .support import require_plugin_token

class UploadLimitedRoute(APIRoute):
    """Authenticate and bound multipart bytes before Starlette spools any file."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            if "/uploads/" in request.url.path:
                require_plugin_token(request.cookies.get("maibot_session"))
                params = request.path_params
                await run_on_main_loop(_authorize_upload(params["plugin_id"], params["page_id"], params["name"]))
                limit = MAX_BYTES + 65536
                try:
                    if int(request.headers.get("content-length", "0")) > limit:
                        raise HTTPException(413, "上传请求超过20MiB限制")
                except ValueError as exc:
                    raise HTTPException(400, "无效Content-Length") from exc
                receive = request._receive
                received = 0

                async def bounded_receive():
                    nonlocal received
                    message = await receive()
                    received += len(message.get("body", b""))
                    if received > limit:
                        raise HTTPException(413, "上传请求超过20MiB限制")
                    return message

                request._receive = bounded_receive
            return await original(request)

        return handle


router = APIRouter(route_class=UploadLimitedRoute)
# 只在主循环读写，避免 WebUI 独立事件循环操作 Runner 的 Future 或并发状态。
_inflight: Dict[str, int] = {}


class Invocation(StrictModel):
    args: Dict[str, Scalar] = Field(default_factory=dict, max_length=30)
    confirmed: bool = False


def _extensions() -> Dict[str, Tuple[PluginSupervisor, WebUIExtension]]:
    extensions = {}
    for supervisor in component_query_service._iter_supervisors():
        for plugin_id, extension in supervisor.get_webui_extensions().items():
            if plugin_id in extensions:
                raise HTTPException(status_code=409, detail=f"插件 WebUI 归属冲突: {plugin_id}")
            extensions[plugin_id] = (supervisor, extension)
    return extensions


async def _list_extensions() -> Dict[str, Any]:
    # 主循环只获取注册表快照，批量序列化交给工作线程。
    return await asyncio.to_thread(_serialize_extensions, _extensions())


def _serialize_extensions(extensions: Dict[str, Tuple[PluginSupervisor, WebUIExtension]]) -> Dict[str, Any]:
    return {
        "success": True,
        "capabilities": ["file_upload", "interactive_controls"],
        "extensions": [
            {"plugin_id": plugin_id, **extension.model_dump(mode="json")}
            for plugin_id, (_, extension) in sorted(extensions.items())
        ],
    }


@router.get("/runtime/webui")
async def list_webui_extensions(maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    require_plugin_token(maibot_session)
    return await run_on_main_loop(_list_extensions())


def _validate_result(result: Any) -> None:
    """限制返回 JSON 的深度、元素数量和总量，不向浏览器传递任意对象。"""
    pending = [(result, 0)]
    count = 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if depth > 12 or count > 20000:
            raise ValueError("插件返回数据超过深度或元素数量限制")
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("插件返回数据的键必须为字符串")
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
        elif value is not None and type(value) not in (str, int, float, bool):
            raise ValueError("插件返回数据必须为 JSON")
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 524288:
        raise ValueError("插件返回数据超过 512 KiB")


async def _invoke(
    plugin_id: str, page_id: str, kind: Literal["queries", "actions"], name: str, request: Invocation
) -> Dict[str, Any]:
    target = _extensions().get(plugin_id)
    if target is None:
        raise HTTPException(status_code=404, detail="插件 WebUI 已下线")
    supervisor, extension = target
    page = next((page for page in extension.pages if page.id == page_id), None)
    if page is None:
        raise HTTPException(status_code=404, detail="插件页面不存在")
    binding = (page.queries if kind == "queries" else page.actions).get(name)
    if binding is None:
        raise HTTPException(status_code=403, detail="页面未开放该查询或操作")
    if kind == "actions" and binding.confirmation is not None and not request.confirmed:
        raise HTTPException(status_code=409, detail="该操作需要确认")
    try:
        binding.validate_args(request.args)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    entry = supervisor.api_registry.get_api(plugin_id, binding.api, version=binding.version)
    if entry is None or entry.dynamic:
        raise HTTPException(status_code=404, detail="页面绑定的静态 API 已下线")
    if _inflight.get(plugin_id, 0) >= 2:
        raise HTTPException(status_code=429, detail="该插件已有两个进行中的 WebUI 请求")
    _inflight[plugin_id] = _inflight.get(plugin_id, 0) + 1
    try:
        response = await asyncio.wait_for(
            supervisor.invoke_api(plugin_id, entry.handler_name, args=request.args, timeout_ms=10000),
            timeout=11,
        )
        if response.error and response.error.get("code") == ErrorCode.E_TIMEOUT.value:
            raise TimeoutError
        if response.error or response.payload.get("success") is not True:
            raise HTTPException(status_code=502, detail="插件 API 执行失败，请查看插件日志")
        result = response.payload.get("result")
        try:
            await asyncio.to_thread(_validate_result, result)
        except (ValueError, TypeError, OverflowError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if isinstance(result, dict) and result.get("success") is False:
            raise HTTPException(422, str(result.get("error", "操作失败"))[:1000])
        return {"success": True, "result": result}
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="插件调用超时；操作可能仍在执行，请核实结果后再试") from exc
    except HTTPException:
        raise
    except RPCError as exc:
        if exc.code == ErrorCode.E_TIMEOUT:
            raise HTTPException(status_code=504, detail="插件调用超时；操作可能仍在执行，请核实结果后再试") from exc
        raise HTTPException(status_code=502, detail="插件运行时调用失败，请查看插件日志") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="插件运行时调用失败，请查看插件日志") from exc
    finally:
        remaining = _inflight[plugin_id] - 1
        if remaining:
            _inflight[plugin_id] = remaining
        else:
            _inflight.pop(plugin_id)


async def _authorize_upload(plugin_id: str, page_id: str, name: str) -> None:
    target = _extensions().get(plugin_id)
    if target is None:
        raise HTTPException(404, "插件 WebUI 已下线")
    page = next((p for p in target[1].pages if p.id == page_id), None)
    pending = list(page.content) if page else []
    while pending:
        node = pending.pop()
        if node.type == "upload" and node.action == name:
            binding = page.actions[name]
            entry = target[0].api_registry.get_api(plugin_id, binding.api, version=binding.version)
            if entry is None or entry.dynamic:
                raise HTTPException(404, "上传操作的静态API已下线")
            return
        pending.extend(node.children)
    raise HTTPException(403, "页面未声明该上传操作")


@router.post("/runtime/webui/{plugin_id}/{page_id}/uploads/{name}")
async def upload_webui_file(
    plugin_id: str, page_id: str, name: str,
    file: UploadFile = File(...), args: str = Form("{}"),
    maibot_session: Optional[str] = Cookie(None),
) -> Dict[str, Any]:
    require_plugin_token(maibot_session)
    await run_on_main_loop(_authorize_upload(plugin_id, page_id, name))
    try:
        arguments = json.loads(args)
        request = Invocation(args=arguments)
        raw = await file.read(MAX_BYTES + 1)
        store = await asyncio.to_thread(upload_store)
        token = await asyncio.to_thread(store.stage, plugin_id, raw)
        request.args["upload_id"] = token
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()
    return await run_on_main_loop(_invoke(plugin_id, page_id, "actions", name, request))


@router.post("/runtime/webui/{plugin_id}/{page_id}/{kind}/{name}")
async def invoke_webui_binding(
    plugin_id: str,
    page_id: str,
    kind: Literal["queries", "actions"],
    name: str,
    request: Invocation,
    maibot_session: Optional[str] = Cookie(None),
) -> Dict[str, Any]:
    require_plugin_token(maibot_session)
    return await run_on_main_loop(_invoke(plugin_id, page_id, kind, name, request))
