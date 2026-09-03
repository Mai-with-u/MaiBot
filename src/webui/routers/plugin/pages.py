"""插件 WebUI 页面清单和静态资源路由。"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from src.common.logger import get_logger
from src.plugin_runtime.integration import get_plugin_runtime_manager
from src.plugin_runtime.protocol.envelope import Envelope
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.webui.dependencies import require_auth
from src.webui.routers.plugin.support import validate_plugin_id
from src.webui.services.plugin_page_registry import PluginPageRecord, discover_plugin_pages

router = APIRouter(tags=["插件页面"])
logger = get_logger("webui.plugin_pages")

_ALLOWED_ASSET_MEDIA_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".mjs": "application/javascript",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
_MAX_PAGE_API_BODY_BYTES = 64 * 1024
_MIN_PAGE_API_TIMEOUT_MS = 1_000
_MAX_PAGE_API_TIMEOUT_MS = 30_000
_OPERATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def discover_runtime_plugin_pages() -> Tuple[List[PluginPageRecord], List[str]]:
    """根据 Runner 当前成功加载的插件目录发现页面。"""
    loaded_plugin_paths = get_plugin_runtime_manager().get_loaded_plugin_paths()
    loaded_plugin_ids = {plugin_id for plugin_id, _plugin_path in loaded_plugin_paths}
    return discover_plugin_pages(
        (plugin_path for _plugin_id, plugin_path in loaded_plugin_paths),
        loaded_plugin_ids,
    )


def _get_page_records() -> Tuple[List[PluginPageRecord], List[str]]:
    """获取当前页面记录及警告，独立函数便于测试和后续缓存接入。"""
    return discover_runtime_plugin_pages()


def _find_plugin_page(plugin_id: str) -> PluginPageRecord:
    """从当前页面记录中查找插件，用于解析其资源根目录。"""
    normalized_plugin_id = validate_plugin_id(plugin_id)
    pages, _warnings = _get_page_records()
    for page in pages:
        if page.plugin_id == normalized_plugin_id:
            return page
    raise HTTPException(status_code=404, detail="插件页面不存在")


def _find_plugin_page_record(plugin_id: str, page_id: str) -> PluginPageRecord:
    """按插件和页面 ID 查找页面记录，避免跨页面读取 API 白名单。"""
    normalized_plugin_id = validate_plugin_id(plugin_id)
    normalized_page_id = str(page_id or "").strip()
    pages, _warnings = _get_page_records()
    for page in pages:
        if page.plugin_id == normalized_plugin_id and page.page_id == normalized_page_id:
            return page
    raise HTTPException(status_code=404, detail="插件页面不存在")


async def _read_page_api_payload(request: Request) -> Dict[str, Any]:
    """读取并限制页面 API 的 JSON 请求体大小。"""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_PAGE_API_BODY_BYTES:
                raise HTTPException(status_code=413, detail="插件页面 API 请求体过大")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="插件页面 API 请求体长度非法") from exc

    raw_body = await request.body()
    if len(raw_body) > _MAX_PAGE_API_BODY_BYTES:
        raise HTTPException(status_code=413, detail="插件页面 API 请求体过大")
    if not raw_body:
        return {}

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="插件页面 API 请求体必须是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="插件页面 API 请求体必须是 JSON 对象")
    return payload


def _extract_plugin_result(raw_result: Any) -> Any:
    """将 Runner Envelope 或测试边界结果转换为页面 API 数据。"""
    if isinstance(raw_result, Envelope):
        if raw_result.error:
            raise RPCError(
                ErrorCode.E_UNKNOWN,
                str(raw_result.error.get("message") or "插件页面 API 调用失败"),
                raw_result.error.get("details") if isinstance(raw_result.error.get("details"), dict) else {},
            )
        payload = raw_result.payload
        if payload.get("success") is False:
            raise RPCError(ErrorCode.E_UNKNOWN, "插件页面 API 调用失败")
        return payload.get("result")
    return raw_result


def _validate_json_result(result: Any) -> Any:
    """确认插件结果可安全编码为 JSON，避免 FastAPI 在响应阶段抛出异常。"""
    try:
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=502, detail="插件页面 API 返回了不可序列化的数据") from exc
    return result


def _resolve_asset_path(plugin_path: Path, asset_path: str) -> Path:
    """解析插件资源路径并限制在插件的 WebUI 构建目录内。"""
    if (
        not asset_path
        or "\x00" in asset_path
        or asset_path.startswith(("/", "\\"))
        or any(part == ".." for part in Path(asset_path).parts)
    ):
        raise HTTPException(status_code=400, detail="插件资源路径包含非法字符")

    plugin_root = plugin_path.resolve()
    asset_root = (plugin_root / "webui" / "dist").resolve()
    try:
        asset_root.relative_to(plugin_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="插件资源目录超出允许范围") from exc

    check_path = plugin_root
    for part in ("webui", "dist", *Path(asset_path).parts):
        check_path = check_path / part
        if check_path.exists() and check_path.is_symlink():
            raise HTTPException(status_code=400, detail="插件资源路径包含符号链接")

    try:
        resolved_candidate_path = (plugin_root / asset_path).resolve()
        resolved_candidate_path.relative_to(asset_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="插件资源路径超出允许范围") from exc

    if not resolved_candidate_path.exists() or not resolved_candidate_path.is_file():
        raise HTTPException(status_code=404, detail="插件资源不存在")
    if resolved_candidate_path.suffix.lower() not in _ALLOWED_ASSET_MEDIA_TYPES:
        raise HTTPException(status_code=403, detail="插件资源类型不受支持")
    return resolved_candidate_path


@router.get("/pages", dependencies=[Depends(require_auth)])
async def list_plugin_pages() -> Dict[str, Any]:
    """返回当前已加载插件声明的 WebUI 页面及发现警告。"""
    pages, warnings = _get_page_records()

    return {
        "success": True,
        "pages": [page.to_response_dict() for page in pages],
        "warnings": warnings,
    }


@router.post(
    "/{plugin_id}/pages/{page_id}/api/{operation}",
    dependencies=[Depends(require_auth)],
)
async def invoke_plugin_page_api(
    plugin_id: str,
    page_id: str,
    operation: str,
    request: Request,
    debug: bool = Query(default=False, description="返回可用于查询链路日志的 request_id"),
) -> Dict[str, Any]:
    """代理页面声明的插件 API，并把 Runner 错误转换为稳定的 HTTP 响应。"""
    request_id = uuid4().hex
    if not _OPERATION_PATTERN.fullmatch(operation):
        raise HTTPException(status_code=404, detail="插件页面 API 操作不存在")

    page = _find_plugin_page_record(plugin_id, page_id)
    component_name = page.api.get(operation)
    if not component_name:
        raise HTTPException(status_code=404, detail="插件页面 API 操作不存在")

    runtime = get_plugin_runtime_manager()
    api_entry = runtime.get_plugin_api(page.plugin_id, component_name, enabled_only=False)
    if api_entry is None:
        raise HTTPException(status_code=404, detail="插件页面 API 未注册")
    if runtime.get_plugin_api(page.plugin_id, component_name) is None:
        raise HTTPException(status_code=403, detail="插件页面 API 已禁用")

    payload = await _read_page_api_payload(request)
    timeout_ms = max(
        _MIN_PAGE_API_TIMEOUT_MS,
        min(_MAX_PAGE_API_TIMEOUT_MS, int(api_entry.timeout_ms)),
    )
    try:
        raw_result = await runtime.invoke_api(
            page.plugin_id,
            component_name,
            payload,
            timeout_ms,
        )
        result = _validate_json_result(_extract_plugin_result(raw_result))
    except HTTPException:
        raise
    except RPCError as exc:
        if exc.code == ErrorCode.E_TIMEOUT:
            raise HTTPException(status_code=504, detail="插件页面 API 调用超时") from exc
        logger.exception("插件页面 API RPC 调用失败 [request_id=%s]: %s", request_id, exc)
        raise HTTPException(status_code=502, detail="插件页面 API 调用失败") from exc
    except Exception as exc:
        logger.exception("插件页面 API 处理失败 [request_id=%s]: %s", request_id, exc)
        raise HTTPException(status_code=502, detail="插件页面 API 调用失败") from exc

    response: Dict[str, Any] = {"success": True, "data": result}
    if debug:
        response["request_id"] = request_id
    logger.debug(
        "插件页面 API 调用完成 [request_id=%s] plugin=%s page=%s operation=%s debug=%s",
        request_id,
        page.plugin_id,
        page.page_id,
        operation,
        debug,
    )
    return response


@router.get("/{plugin_id}/assets/{asset_path:path}", dependencies=[Depends(require_auth)])
async def serve_plugin_asset(plugin_id: str, asset_path: str) -> FileResponse:
    """返回页面声明插件的 WebUI 构建资源。"""
    page = _find_plugin_page(plugin_id)
    resolved_asset_path = _resolve_asset_path(page.plugin_path, asset_path)
    media_type = _ALLOWED_ASSET_MEDIA_TYPES[resolved_asset_path.suffix.lower()]
    response = FileResponse(resolved_asset_path, media_type=media_type)
    response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return response
