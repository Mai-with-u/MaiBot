"""WebUI 本地用户表情包管理接口。"""

from pathlib import Path
import mimetypes
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from src.webui.dependencies import require_auth
from src.webui.routers.avatar import WEBUI_USER_ID_PATTERN, detect_supported_image_suffix

router = APIRouter(
    prefix="/user-emojis",
    tags=["user-emojis"],
    dependencies=[Depends(require_auth)],
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
USER_EMOJI_ROOT = (PROJECT_ROOT / "data" / "webui_user_emojis").resolve()
MAX_USER_EMOJI_BYTES = 2 * 1024 * 1024
MAX_USER_EMOJI_COUNT = 100
SUPPORTED_USER_EMOJI_SUFFIXES = {".gif", ".jpg", ".png", ".webp"}
USER_EMOJI_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def _resolve_user_emoji_directory(user_id: str) -> Path:
    normalized_user_id = user_id.strip()
    if not WEBUI_USER_ID_PATTERN.fullmatch(normalized_user_id):
        raise HTTPException(status_code=400, detail="WebUI 用户 ID 不合法")

    directory = (USER_EMOJI_ROOT / normalized_user_id).resolve()
    try:
        directory.relative_to(USER_EMOJI_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="用户表情包目录不合法") from exc
    return directory


def _resolve_user_emoji_path(user_id: str, emoji_id: str) -> Path:
    normalized_emoji_id = emoji_id.strip().lower()
    if not USER_EMOJI_ID_PATTERN.fullmatch(normalized_emoji_id):
        raise HTTPException(status_code=400, detail="表情包 ID 不合法")

    directory = _resolve_user_emoji_directory(user_id)
    for suffix in sorted(SUPPORTED_USER_EMOJI_SUFFIXES):
        candidate = (directory / f"{normalized_emoji_id}{suffix}").resolve()
        try:
            candidate.relative_to(directory)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    raise HTTPException(status_code=404, detail="表情包不存在")


def _build_user_emoji_record(user_id: str, path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "id": path.stem,
        "content_type": mimetypes.guess_type(str(path))[0] or "image/png",
        "content_url": (f"/api/webui/user-emojis/{path.stem}/content?user_id={user_id}&v={stat.st_mtime_ns}"),
        "created_at": stat.st_mtime,
    }


def list_user_emoji_paths(user_id: str) -> list[Path]:
    """按添加时间倒序列出指定 WebUI 用户的表情包。"""

    directory = _resolve_user_emoji_directory(user_id)
    if not directory.is_dir():
        return []
    paths = [
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_USER_EMOJI_SUFFIXES
        and USER_EMOJI_ID_PATTERN.fullmatch(path.stem)
    ]
    return sorted(paths, key=lambda path: path.stat().st_mtime_ns, reverse=True)


def save_user_emoji(user_id: str, content_type: str, image_bytes: bytes) -> Path:
    """校验并保存一个 WebUI 用户表情包。"""

    if not image_bytes:
        raise HTTPException(status_code=400, detail="表情包文件为空")
    if len(image_bytes) > MAX_USER_EMOJI_BYTES:
        raise HTTPException(status_code=413, detail="单个表情包不能超过 2 MB")

    normalized_content_type = content_type.split(";", 1)[0].strip().lower()
    if normalized_content_type and not normalized_content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="表情包文件必须是图片")

    suffix = detect_supported_image_suffix(image_bytes)
    if suffix not in SUPPORTED_USER_EMOJI_SUFFIXES:
        raise HTTPException(status_code=415, detail="仅支持 JPG、PNG、WebP 或 GIF 表情包")

    directory = _resolve_user_emoji_directory(user_id)
    if len(list_user_emoji_paths(user_id)) >= MAX_USER_EMOJI_COUNT:
        raise HTTPException(status_code=409, detail="用户表情包数量已达到 100 个上限")

    directory.mkdir(parents=True, exist_ok=True)
    emoji_path = (directory / f"{uuid.uuid4().hex}{suffix}").resolve()
    try:
        emoji_path.relative_to(directory)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="表情包保存路径不合法") from exc
    emoji_path.write_bytes(image_bytes)
    return emoji_path


@router.get("")
async def get_user_emojis(user_id: str = Query(...)):
    """获取当前 WebUI 本地用户保存的表情包。"""

    return {
        "items": [_build_user_emoji_record(user_id, path) for path in list_user_emoji_paths(user_id)],
        "limit": MAX_USER_EMOJI_COUNT,
    }


@router.post("")
async def add_user_emoji(
    user_id: str = Form(...),
    file: UploadFile = File(...),
):
    """添加一个本地用户表情包。"""

    try:
        image_bytes = await file.read(MAX_USER_EMOJI_BYTES + 1)
    finally:
        await file.close()

    emoji_path = save_user_emoji(user_id, file.content_type or "", image_bytes)
    return {"item": _build_user_emoji_record(user_id.strip(), emoji_path)}


@router.get("/{emoji_id}/content")
async def get_user_emoji_content(
    emoji_id: str,
    user_id: str = Query(...),
):
    """读取一个本地用户表情包文件。"""

    emoji_path = _resolve_user_emoji_path(user_id, emoji_id)
    return FileResponse(
        emoji_path,
        media_type=mimetypes.guess_type(str(emoji_path))[0] or "image/png",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@router.delete("/{emoji_id}")
async def delete_user_emoji(
    emoji_id: str,
    user_id: str = Query(...),
):
    """删除一个本地用户表情包。"""

    emoji_path = _resolve_user_emoji_path(user_id, emoji_id)
    emoji_path.unlink()
    return {"success": True, "id": emoji_id}
