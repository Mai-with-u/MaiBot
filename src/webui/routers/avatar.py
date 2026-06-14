"""WebUI 通用头像缓存接口。"""

from pathlib import Path
from urllib.request import Request, urlopen
import mimetypes
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from src.webui.dependencies import require_auth

router = APIRouter(prefix="/avatar", tags=["avatar"], dependencies=[Depends(require_auth)])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
AVATAR_CACHE_ROOT = (PROJECT_ROOT / "data" / "avatar").resolve()
MAX_AVATAR_BYTES = 5 * 1024 * 1024
QQ_AVATAR_URL_TEMPLATE = "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
SUPPORTED_AVATAR_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
AVATAR_USER_AGENT = "MaiBot-WebUI-Avatar/1.0"
QQ_COMPATIBLE_PLATFORMS = {"qq", "qqguild", "napcat"}


def build_webui_avatar_url(platform: str, user_id: str) -> str | None:
    """构造 WebUI 内部头像 URL，不直接暴露外部头像源。"""

    normalized_platform = platform.strip().lower()
    normalized_user_id = user_id.strip()
    if not normalized_platform or not normalized_user_id:
        return None
    if normalized_platform not in QQ_COMPATIBLE_PLATFORMS:
        return None
    if not normalized_user_id.isdigit():
        return None
    return f"/api/webui/avatar?platform={normalized_platform}&user_id={normalized_user_id}"


def _avatar_cache_path(platform: str, user_id: str, suffix: str = ".jpg") -> Path:
    normalized_platform = re.sub(r"[^A-Za-z0-9_-]+", "_", platform.strip().lower()).strip("_")
    normalized_user_id = re.sub(r"[^A-Za-z0-9_-]+", "_", user_id.strip()).strip("_")
    if not normalized_platform or not normalized_user_id:
        raise HTTPException(status_code=400, detail="头像参数不合法")
    if suffix.lower() not in SUPPORTED_AVATAR_SUFFIXES:
        suffix = ".jpg"
    return (AVATAR_CACHE_ROOT / normalized_platform / f"{normalized_user_id}{suffix}").resolve()


def _iter_cached_avatar_paths(platform: str, user_id: str) -> list[Path]:
    base_path = _avatar_cache_path(platform, user_id, ".jpg")
    return [base_path.with_suffix(suffix) for suffix in sorted(SUPPORTED_AVATAR_SUFFIXES)]


def _find_cached_avatar_path(platform: str, user_id: str) -> Path | None:
    for cache_path in _iter_cached_avatar_paths(platform, user_id):
        try:
            cache_path.relative_to(AVATAR_CACHE_ROOT)
        except ValueError:
            continue
        if cache_path.is_file():
            return cache_path
    return None


def _guess_avatar_suffix(content_type: str, image_bytes: bytes) -> str:
    normalized_content_type = content_type.split(";", 1)[0].strip().lower()
    suffix_by_content_type = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
    }
    if normalized_content_type in suffix_by_content_type:
        return suffix_by_content_type[normalized_content_type]
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if image_bytes.startswith(b"GIF8"):
        return ".gif"
    if image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:16]:
        return ".webp"
    if image_bytes.startswith(b"BM"):
        return ".bmp"
    return ".jpg"


def _download_qq_avatar_to_cache(platform: str, user_id: str) -> Path:
    normalized_user_id = user_id.strip()
    if not normalized_user_id.isdigit():
        raise HTTPException(status_code=404, detail="当前平台用户没有可用头像")

    request = Request(
        QQ_AVATAR_URL_TEMPLATE.format(user_id=normalized_user_id),
        headers={"User-Agent": AVATAR_USER_AGENT},
    )
    try:
        with urlopen(request, timeout=10) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if content_type and not content_type.startswith("image/"):
                raise HTTPException(status_code=502, detail="头像接口返回内容不是图片")
            image_bytes = response.read(MAX_AVATAR_BYTES + 1)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"头像下载失败：{type(exc).__name__}: {exc}") from exc

    if not image_bytes or len(image_bytes) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=502, detail="头像下载失败：图片为空或超过大小限制")

    suffix = _guess_avatar_suffix(content_type, image_bytes)
    cache_path = _avatar_cache_path(platform, normalized_user_id, suffix)
    try:
        cache_path.relative_to(AVATAR_CACHE_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="头像缓存路径不合法") from exc

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(image_bytes)
    return cache_path


def resolve_avatar_cache_file(platform: str, user_id: str) -> Path:
    """读取头像缓存；不存在时按平台规则下载。"""

    normalized_platform = platform.strip().lower()
    cached_path = _find_cached_avatar_path(normalized_platform, user_id)
    if cached_path is not None:
        return cached_path

    if normalized_platform in QQ_COMPATIBLE_PLATFORMS:
        return _download_qq_avatar_to_cache(normalized_platform, user_id)

    raise HTTPException(status_code=404, detail="当前平台用户没有可用头像")


@router.get("")
async def get_webui_avatar(platform: str = Query(...), user_id: str = Query(...)):
    """读取或下载并缓存 WebUI 展示用头像。"""

    cache_path = resolve_avatar_cache_file(platform, user_id)
    media_type = mimetypes.guess_type(str(cache_path))[0] or "image/jpeg"
    return FileResponse(
        cache_path,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )
