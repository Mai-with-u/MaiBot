"""WebUI 通用头像缓存接口。"""

from pathlib import Path
from typing import Literal
from urllib.parse import quote
import asyncio
import mimetypes
import os
import re
import time

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from src.common.paths import get_instance_root
from src.common.runtime_loop import run_on_main_loop
from src.platform_io.avatar import avatar_service, validate_avatar_target
from src.webui.dependencies import require_auth

router = APIRouter(prefix="/avatar", tags=["avatar"], dependencies=[Depends(require_auth)])

# 浏览器直接复用已下载头像的最长时间；超过后带 ETag 校验，头像未变只返回 304。
AVATAR_BROWSER_MAX_AGE = 3 * 60 * 60

PROJECT_ROOT = get_instance_root()
AVATAR_CACHE_ROOT = (PROJECT_ROOT / "data" / "avatar").resolve()
MAX_AVATAR_BYTES = 5 * 1024 * 1024
SUPPORTED_AVATAR_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
WEBUI_AVATAR_PLATFORM = "webui"
WEBUI_USER_ID_PATTERN = re.compile(r"^webui_[A-Za-z0-9_-]+$")
AvatarTargetType = Literal["user", "group"]


def build_webui_avatar_url(platform: str, user_id: str) -> str | None:
    """构造 WebUI 内部头像 URL，不直接暴露外部头像源。"""

    normalized_platform = platform.strip().lower()
    normalized_user_id = user_id.strip()
    if not normalized_platform or not normalized_user_id:
        return None
    return (
        f"/api/webui/avatar?platform={quote(normalized_platform, safe='')}&user_id={quote(normalized_user_id, safe='')}"
    )


def build_webui_group_avatar_url(platform: str, group_id: str) -> str | None:
    """构造 WebUI 内部群头像 URL。"""

    normalized_platform = platform.strip().lower()
    normalized_group_id = group_id.strip()
    if not normalized_platform or not normalized_group_id:
        return None
    return f"/api/webui/avatar?platform={quote(normalized_platform, safe='')}&group_id={quote(normalized_group_id, safe='')}"


def _avatar_cache_key(target_id: str, target_type: AvatarTargetType) -> str:
    if target_type == "group":
        return f"group_{target_id.strip()}"
    return target_id.strip()


def _avatar_cache_path(
    platform: str,
    target_id: str,
    suffix: str = ".jpg",
    target_type: AvatarTargetType = "user",
) -> Path:
    normalized_platform = re.sub(r"[^A-Za-z0-9_-]+", "_", platform.strip().lower()).strip("_")
    normalized_target_id = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        _avatar_cache_key(target_id, target_type),
    ).strip("_")
    if not normalized_platform or not normalized_target_id:
        raise HTTPException(status_code=400, detail="头像参数不合法")
    if suffix.lower() not in SUPPORTED_AVATAR_SUFFIXES:
        suffix = ".jpg"
    return (AVATAR_CACHE_ROOT / normalized_platform / f"{normalized_target_id}{suffix}").resolve()


def _iter_cached_avatar_paths(
    platform: str,
    target_id: str,
    target_type: AvatarTargetType,
) -> list[Path]:
    base_path = _avatar_cache_path(platform, target_id, ".jpg", target_type)
    return [base_path.with_suffix(suffix) for suffix in sorted(SUPPORTED_AVATAR_SUFFIXES)]


def _find_cached_avatar_path(
    platform: str,
    target_id: str,
    target_type: AvatarTargetType,
) -> Path | None:
    for cache_path in _iter_cached_avatar_paths(platform, target_id, target_type):
        try:
            cache_path.relative_to(AVATAR_CACHE_ROOT)
        except ValueError:
            continue
        if cache_path.is_file():
            return cache_path
    return None


def detect_supported_image_suffix(image_bytes: bytes) -> str | None:
    """根据文件签名识别 WebUI 允许处理的图片格式。"""

    if image_bytes.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return ".webp"
    if image_bytes.startswith(b"BM"):
        return ".bmp"
    return None


def save_webui_user_avatar(user_id: str, content_type: str, image_bytes: bytes) -> Path:
    """校验并保存 WebUI 本地用户头像。"""

    normalized_user_id = user_id.strip()
    if not WEBUI_USER_ID_PATTERN.fullmatch(normalized_user_id):
        raise HTTPException(status_code=400, detail="WebUI 用户 ID 不合法")
    if not image_bytes:
        raise HTTPException(status_code=400, detail="头像文件为空")
    if len(image_bytes) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="头像文件不能超过 5 MB")

    normalized_content_type = content_type.split(";", 1)[0].strip().lower()
    if normalized_content_type and not normalized_content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="头像文件必须是图片")

    suffix = detect_supported_image_suffix(image_bytes)
    if suffix is None:
        raise HTTPException(status_code=415, detail="仅支持 JPG、PNG、WebP、GIF 或 BMP 图片")

    cache_path = _avatar_cache_path(WEBUI_AVATAR_PLATFORM, normalized_user_id, suffix)
    try:
        cache_path.relative_to(AVATAR_CACHE_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="头像保存路径不合法") from exc

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    for existing_path in _iter_cached_avatar_paths(WEBUI_AVATAR_PLATFORM, normalized_user_id, "user"):
        if existing_path.is_file():
            existing_path.unlink()
    cache_path.write_bytes(image_bytes)
    return cache_path


@router.put("/webui-user")
async def update_webui_user_avatar(
    user_id: str = Form(...),
    file: UploadFile = File(...),
):
    """上传并持久化当前浏览器生成的 WebUI 本地用户头像。"""

    try:
        image_bytes = await file.read(MAX_AVATAR_BYTES + 1)
    finally:
        await file.close()

    cache_path = await asyncio.to_thread(save_webui_user_avatar, user_id, file.content_type or "", image_bytes)
    modified_at = await asyncio.to_thread(lambda: cache_path.stat().st_mtime_ns)
    return {
        "success": True,
        "avatar_url": (
            f"/api/webui/avatar?platform={WEBUI_AVATAR_PLATFORM}&user_id={quote(user_id.strip())}&v={modified_at}"
        ),
    }


def _etag_matches(if_none_match: str, etag: str) -> bool:
    """判断 If-None-Match 是否命中当前 ETag；条件请求按弱比较处理，忽略 ``W/`` 前缀。"""
    candidates = {candidate.strip().removeprefix("W/") for candidate in if_none_match.split(",")}
    return "*" in candidates or etag in candidates


def _no_avatar_response(status: str, cache_control: str) -> Response:
    """目标没有头像是正常状态而非错误：返回空响应，前端据此显示默认头像，浏览器不会记为请求失败。"""
    return Response(
        status_code=204,
        headers={
            "Cache-Control": cache_control,
            "X-Avatar-Status": status,
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@router.get("")
async def get_webui_avatar(
    platform: str = Query(...),
    user_id: str | None = Query(default=None),
    group_id: str | None = Query(default=None),
    account_id: str = Query(default=""),
    scope: str = Query(default=""),
    force_refresh: bool = Query(default=False),
    if_none_match: str | None = Header(default=None),
):
    """通过统一头像服务读取独立图片；本地上传头像不应用远程过期规则。"""
    if bool(group_id) == bool(user_id):
        raise HTTPException(status_code=400, detail="必须且只能指定 user_id 或 group_id")
    target_id = group_id if group_id else user_id
    target_type = "group" if group_id else "user"
    try:
        normalized_platform, target_id = validate_avatar_target(platform, target_id, target_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if normalized_platform == WEBUI_AVATAR_PLATFORM:
        cache_path = await asyncio.to_thread(_find_cached_avatar_path, normalized_platform, target_id, target_type)
        if cache_path is None:
            return _no_avatar_response("missing", "private, no-cache")
        cache_control = "private, no-cache"
    else:
        try:
            result = await run_on_main_loop(
                avatar_service.get_avatar(
                    normalized_platform,
                    target_id,
                    target_type,
                    account_id=account_id,
                    scope=scope,
                    force_refresh=force_refresh,
                )
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"头像获取失败：{type(exc).__name__}: {exc}") from exc
        if result.status != "available":
            return _no_avatar_response(result.status, "private, max-age=300")
        cache_path = result.path
        # 浏览器缓存不能超过服务端剩余有效期，强制刷新响应不允许复用。
        max_age = max(0, min(AVATAR_BROWSER_MAX_AGE, int(result.expires_at - time.time())))
        cache_control = "private, no-store" if force_refresh else f"private, max-age={max_age}"
    media_type = mimetypes.guess_type(str(cache_path))[0] or "image/jpeg"
    headers = {
        "Cache-Control": cache_control,
        "X-Robots-Tag": "noindex, nofollow",
    }
    # 提前取文件状态，让响应在构造时就带上 ETag，才能与请求头比对。
    stat_result = await asyncio.to_thread(os.stat, cache_path)
    response = FileResponse(cache_path, media_type=media_type, headers=headers, stat_result=stat_result)
    etag = response.headers["etag"]
    if not force_refresh and if_none_match is not None and _etag_matches(if_none_match, etag):
        # 浏览器缓存过期后只需确认头像没变，不必重新下载整张图片。
        return Response(status_code=304, headers={**headers, "ETag": etag})
    return response
