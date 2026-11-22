"""插件媒体目录与按哈希读取能力。"""

from base64 import b64encode
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Tuple

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.sql.expression import Select

import asyncio

from src.common.database.database import get_db_session
from src.common.database.database_model import Images, ImageType
from src.common.paths import get_instance_root
from src.common.utils.image_path import resolve_stored_image_path
from src.plugin_runtime.transport.base import MAX_FRAME_SIZE

# 按字符限制描述预览；即使每字符占四字节，100 条记录也能放入一个 RPC 帧。
_DESCRIPTION_LIMIT = 4096
# 为返回的描述、情绪标签和 RPC 信封预留空间。
_MAX_IMAGE_BYTES = (MAX_FRAME_SIZE - 65536) // 4 * 3


def _query(kind: ImageType) -> Select[Tuple[int, str, str, str]]:
    statement = select(
        Images.id, Images.image_hash, func.substr(Images.description, 1, _DESCRIPTION_LIMIT).label("description"),
        Images.full_path,
    ).where(col(Images.image_type) == kind, col(Images.no_file_flag).is_(False))
    if kind == ImageType.EMOJI:
        statement = statement.where(col(Images.is_registered).is_(True), col(Images.is_banned).is_(False))
    return statement


def _relative_path(kind: ImageType, stored_path: str) -> str:
    root = get_instance_root()
    path = Path(stored_path.replace("\\", "/"))
    relative = path.relative_to(root) if path.is_absolute() else path
    directory = Path("data", "emoji" if kind == ImageType.EMOJI else "images")
    if ".." in relative.parts or not relative.is_relative_to(directory):
        raise ValueError("文件不在对应的媒体目录内")
    return relative.as_posix()


def _metadata(kind: ImageType, image_hash: str, description: str, stored_path: str) -> Dict[str, str]:
    item = {"hash": image_hash, "description": description, "relative_path": _relative_path(kind, stored_path)}
    if kind == ImageType.EMOJI:
        item["emotion"] = description
    return item


def _list(kind: ImageType, cursor: int, limit: int) -> Dict[str, Any]:
    if type(cursor) is not int or not 0 <= cursor <= 2**63 - 1:
        raise ValueError("cursor 必须是非负的 SQLite 整数")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit 必须是 1 到 100 的整数")
    statement = _query(kind).where(col(Images.id) > cursor).order_by(col(Images.id)).limit(limit + 1)
    with get_db_session() as session:
        rows = session.exec(statement).all()
    items = [_metadata(kind, row.image_hash, row.description, row.full_path) for row in rows[:limit]]
    key = "emojis" if kind == ImageType.EMOJI else "images"
    next_cursor = rows[limit - 1].id if len(rows) > limit else None
    return {"success": True, key: items, "count": len(items), "next_cursor": next_cursor}


def _get(kind: ImageType, operation: str, args: Dict[str, Any]) -> Dict[str, Any]:
    key = "emoji" if kind == ImageType.EMOJI else "image"
    statement = _query(kind)
    if operation == "get_by_path":
        value = args.get("relative_path")
        if not isinstance(value, str) or not value.strip() or PureWindowsPath(value).anchor:
            raise ValueError("请提供实例相对路径 relative_path")
        relative = _relative_path(kind, value)
        # 现有记录使用实例相对路径，旧库也可能保存绝对路径或 Windows 分隔符。
        absolute = (get_instance_root() / relative).as_posix()
        stored = func.replace(Images.full_path, "\\", "/")
        statement = statement.where(stored.in_([relative, "./" + relative, absolute]))
    else:
        image_hash = args.get("hash")
        if not isinstance(image_hash, str) or not image_hash.strip():
            raise ValueError("缺少图片 hash")
        statement = statement.where(col(Images.image_hash) == image_hash.strip().lower())
    statement = statement.order_by(col(Images.id))
    if operation == "get_relative_path":
        key = "relative_path"
    with get_db_session() as session:
        row = session.exec(statement.limit(1)).first()
    if row is None:
        return {"success": True, key: None}

    relative = _relative_path(kind, row.full_path)
    if operation == "get_relative_path":
        return {"success": True, key: relative}
    path = resolve_stored_image_path(relative)
    media_dir = get_instance_root() / "data" / ("emoji" if kind == ImageType.EMOJI else "images")
    if not path.is_relative_to(media_dir):
        raise ValueError("文件不在对应的媒体目录内")
    try:
        with path.open("rb") as source:
            data = source.read(_MAX_IMAGE_BYTES + 1)
    except FileNotFoundError:
        return {"success": True, key: None}
    if len(data) > _MAX_IMAGE_BYTES:
        raise ValueError("单张图片编码后超出插件 RPC 单帧预算")
    item = _metadata(kind, row.image_hash, row.description, row.full_path)
    item["base64"] = b64encode(data).decode("ascii")
    return {"success": True, key: item}


async def list_media(plugin_id: str, capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
    kind = ImageType.EMOJI if capability == "emoji.list" else ImageType.IMAGE
    return await asyncio.to_thread(_list, kind, args.get("cursor", 0), args.get("limit", 100))


async def get_media(plugin_id: str, capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
    namespace, operation = capability.split(".")
    kind = ImageType.EMOJI if namespace == "emoji" else ImageType.IMAGE
    return await asyncio.to_thread(_get, kind, operation, args)
