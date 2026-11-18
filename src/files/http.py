"""文件二进制通道：仅凭 Host 签发的限时授权访问，不依赖 WebUI 登录。"""

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute

import asyncio
import re

from .models import FileAsset
from .service import disk_call, get_file_service
from .store import FileBusyError, FileLimitError


class FileRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                get_file_service().require_enabled()
                return await handler(request)
            except FileNotFoundError as exc:
                raise HTTPException(404, str(exc)) from exc
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except (FileBusyError, FileLimitError) as exc:
                raise HTTPException(409, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except TimeoutError as exc:
                raise HTTPException(504, "文件传输超时") from exc
            except RuntimeError as exc:
                raise HTTPException(502, str(exc)) from exc
        return wrapped


router = APIRouter(prefix="/api/files", tags=["File transfers"], route_class=FileRoute)


async def receive_upload(request: Request, asset: FileAsset):
    service = get_file_service()
    transfer = asset.transfer_id
    try:
        async with asyncio.timeout(service.store.config.transfer_timeout):
            async for chunk in request.stream():
                service.require_enabled()
                await disk_call(service.store.append, asset.file_ref, transfer, chunk)
            return service.store.public(await disk_call(service.store.finish, asset.file_ref, transfer))
    except BaseException as exc:
        await disk_call(service.store.fail, asset.file_ref, transfer, f"上传失败: {type(exc).__name__}")
        raise


@router.put("/transfers/{token}")
async def upload_transfer(token: str, request: Request):
    service = get_file_service()
    claims = service.claims(token, "upload")
    scope = service.claims(claims["scope"], "plugin")
    if scope["plugin_id"] != claims["plugin_id"]:
        raise PermissionError("插件文件凭证不匹配")
    service.revoke(token)
    asset = await disk_call(service.store.get, claims["file_ref"], scope["session_id"])
    return await receive_upload(request, asset)


@router.api_route("/transfers/{token}", methods=["GET", "HEAD"])
def download_transfer(token: str, request: Request):
    service = get_file_service()
    claims = service.claims(token, "download")
    # SDK 数据通道沿用仍有效的工具调用权限，发送给 QQ 的凭证则只授权这一个文件。
    if "scope" in claims:
        service.claims(claims["scope"], "plugin")
    asset = service.store.get(claims["file_ref"], None, admin=True)
    if asset.state != "ready":
        raise FileNotFoundError("附件尚未就绪")
    start, end = 0, asset.size - 1
    range_header = request.headers.get("range") if request.method == "GET" else None
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        if match is None or not any(match.groups()) or asset.size == 0:
            raise HTTPException(416, "无效字节范围", headers={"Content-Range": f"bytes */{asset.size}"})
        left, right = match.groups()
        if left:
            start = int(left)
            end = min(int(right), end) if right else end
        elif int(right) > 0:
            start = max(0, asset.size - int(right))
        else:
            start = asset.size
        if start > end:
            raise HTTPException(416, "字节范围超出文件", headers={"Content-Range": f"bytes */{asset.size}"})
    length = end - start + 1
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(asset.name, safe='')}",
        "Content-Length": str(length), "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer", "Accept-Ranges": "bytes", "ETag": f'"{asset.sha256}"',
    }
    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{asset.size}"
    if request.method == "HEAD":
        return Response(media_type="application/octet-stream", headers=headers)
    if not service.acquire_read():
        raise HTTPException(429, "文件读取并发数已满")

    async def chunks():
        reader = service.store.reader(asset.file_ref)
        opened = False
        try:
            handle = await disk_call(reader.__enter__)
            opened = True
            await disk_call(handle.seek, start)
            remaining = length
            async with asyncio.timeout(service.store.config.transfer_timeout):
                while remaining:
                    service.require_enabled()
                    chunk = await disk_call(handle.read, min(256 * 1024, remaining))
                    if not chunk:
                        raise OSError("附件长度与索引不一致")
                    remaining -= len(chunk)
                    yield chunk
        finally:
            try:
                if opened:
                    await disk_call(reader.__exit__, None, None, None)
            finally:
                service.release_read()

    return StreamingResponse(chunks(), status_code=206 if range_header else 200,
                             media_type="application/octet-stream", headers=headers)
