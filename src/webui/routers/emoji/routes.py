"""表情包管理 API 路由"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Cookie, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
from sqlalchemy import func
from sqlmodel import col, select

import asyncio
import hashlib
import io
import os
import re

from src.common.database.database import get_db_session
from src.common.database.database_model import Images, ImageType
from src.common.utils.image_path import StoredImagePathError, resolve_stored_image_path, serialize_stored_image_path
from src.webui.core import get_token_manager
from src.webui.core import verify_auth_token_from_cookie_or_header as verify_auth_token

from .schemas import (
    BatchDeleteRequest,
    BatchDeleteResponse,
    DescriptionForm,
    EmojiDeleteResponse,
    EmojiDetailResponse,
    EmojiFile,
    EmojiFiles,
    EmojiListResponse,
    EmojiUpdateRequest,
    EmojiUpdateResponse,
    EmojiUploadResponse,
    EmotionForm,
    IsRegisteredForm,
    ThumbnailCacheStatsResponse,
    ThumbnailCleanupResponse,
    ThumbnailPreheatResponse,
    emoji_to_response,
)
from .support import (
    EMOJI_DIR,
    THUMBNAIL_CACHE_DIR,
    background_generate_thumbnail,
    cleanup_orphaned_thumbnails,
    ensure_thumbnail_cache_dir,
    generate_thumbnail,
    get_generating_lock,
    get_generating_thumbnails,
    get_thumbnail_cache_path,
    get_thumbnail_executor,
    logger,
)

router = APIRouter(prefix="/emoji", tags=["Emoji"])


def _normalize_emoji_description(description: str = "", emotion: str = "") -> str:
    """将上传参数中的描述或情绪标签归一化为可存储描述。

    Args:
        description: 用户输入的表情包描述。
        emotion: 用户输入的情绪标签。

    Returns:
        str: 归一化后的描述字符串。
    """
    normalized_description = str(description or "").strip()
    normalized_emotion = str(emotion or "").strip()
    if normalized_description:
        return normalized_description
    if not normalized_emotion:
        return ""

    tags = re.split(r"[,，、;；\s]+", normalized_emotion)
    return ",".join(item.strip() for item in tags if item.strip())


def _apply_emoji_status_filter(statement: Any, status: Optional[str]) -> Any:
    """按 WebUI 的统一表情包状态筛选数据库查询。"""
    if status is None:
        return statement

    description_text = func.trim(func.coalesce(col(Images.description), ""))
    if status == "discarded":
        return statement.where(col(Images.is_banned).is_(True))
    if status == "adopted":
        return statement.where(
            col(Images.is_registered).is_(True),
            col(Images.is_banned).is_(False),
        )
    if status == "known":
        return statement.where(
            col(Images.is_registered).is_(False),
            col(Images.is_banned).is_(False),
            description_text != "",
        )
    if status == "unknown":
        return statement.where(
            col(Images.is_registered).is_(False),
            col(Images.is_banned).is_(False),
            description_text == "",
        )

    raise HTTPException(status_code=400, detail="无效的表情包状态")


def _apply_emoji_format_filter(statement: Any, image_format: Optional[str]) -> Any:
    """按图片文件后缀筛选表情包。"""
    if not image_format:
        return statement

    normalized_format = image_format.strip().lower().lstrip(".")
    if not normalized_format:
        return statement
    if normalized_format == "unknown":
        return statement.where(~col(Images.full_path).contains("."))

    return statement.where(func.lower(col(Images.full_path)).like(f"%.{normalized_format}"))


def _save_uploaded_emoji_file(file_content: bytes, emoji_hash: str, image_format: str) -> str:
    """保存上传的表情包文件并返回数据库存储路径。"""
    os.makedirs(EMOJI_DIR, exist_ok=True)

    timestamp = int(datetime.now().timestamp())
    filename = f"emoji_{timestamp}_{emoji_hash[:8]}.{image_format}"
    full_path = os.path.join(EMOJI_DIR, filename)

    counter = 1
    while os.path.exists(full_path):
        filename = f"emoji_{timestamp}_{emoji_hash[:8]}_{counter}.{image_format}"
        full_path = os.path.join(EMOJI_DIR, filename)
        counter += 1

    with open(full_path, "wb") as output_file:
        _ = output_file.write(file_content)

    logger.info(f"表情包文件已保存: {full_path}")
    return serialize_stored_image_path(full_path)


def _delete_emoji_file(full_path: str) -> bool:
    """删除表情包本地文件，文件不存在时视为已删除。"""
    if not full_path:
        return True

    try:
        file_path = resolve_stored_image_path(full_path)
        if not file_path.exists():
            return True
        if not file_path.is_file():
            logger.warning(f"跳过删除非文件路径: {full_path}")
            return False
        file_path.unlink()
        logger.info(f"表情包文件已删除: {full_path}")
        return True
    except StoredImagePathError:
        logger.warning(f"跳过删除目录外表情包文件: {full_path}")
        return True
    except Exception as exc:
        logger.error(f"删除表情包文件失败: {full_path}, error={exc}")
        return False


def _resolve_existing_emoji_file_path(full_path: str) -> Optional[Path]:
    """解析表情包文件路径，目录外或文件不存在时返回 None。"""
    try:
        file_path = resolve_stored_image_path(full_path)
    except (OSError, RuntimeError, StoredImagePathError):
        return None
    return file_path if file_path.exists() else None


def _remove_emoji_from_memory(emoji_hash: str) -> None:
    """从运行中的表情包管理器里移除指定表情。"""
    from src.emoji_system.emoji_manager import emoji_manager

    emoji_manager.emojis = [emoji for emoji in emoji_manager.emojis if emoji.file_hash != emoji_hash]
    emoji_manager._emoji_num = len(emoji_manager.emojis)

def _delete_emoji_thumbnail_cache(emoji_hash: str) -> bool:
    """删除表情包缩略图缓存，返回是否实际删除了缓存文件。"""
    cache_path = get_thumbnail_cache_path(emoji_hash)
    if not cache_path.exists():
        return False

    cache_path.unlink()
    return True


async def _review_emoji_record_for_registration(
    emoji: Images,
    *,
    image_bytes: Optional[bytes] = None,
    image_format: str = "",
) -> bool:
    """复用 EmojiManager 的注册前审核逻辑检查 WebUI 注册入口。"""
    from src.common.data_models.image_data_model import MaiEmoji
    from src.emoji_system.emoji_manager import emoji_manager

    try:
        emoji_path = _resolve_existing_emoji_file_path(emoji.full_path)
        if emoji_path is None:
            raise FileNotFoundError(emoji.full_path)
        target_emoji = MaiEmoji(full_path=emoji_path, image_bytes=image_bytes)
    except Exception as exc:
        logger.warning(f"表情包注册审核失败，无法读取文件: id={emoji.id}, path={emoji.full_path}, error={exc}")
        return False

    target_emoji.file_hash = emoji.image_hash
    target_emoji.description = emoji.description or ""
    target_emoji.image_format = image_format.strip().lower() or emoji_path.suffix.lower().lstrip(".")
    return await emoji_manager.review_emoji_for_registration(target_emoji)


@router.get("/list", response_model=EmojiListResponse)
async def get_emoji_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    is_registered: Optional[bool] = Query(None, description="是否已注册筛选"),
    is_banned: Optional[bool] = Query(None, description="是否被禁用筛选"),
    status: Optional[str] = Query(None, description="表情包状态筛选"),
    format: Optional[str] = Query(None, description="图片格式筛选"),
    sort_by: Optional[str] = Query("register_time", description="排序字段"),
    sort_order: Optional[str] = Query("desc", description="排序方向"),
    maibot_session: Optional[str] = Cookie(None),
) -> EmojiListResponse:
    """获取表情包列表。

    Args:
        page: 页码，从 1 开始。
        page_size: 每页数量，范围为 1-100。
        search: 搜索关键词，用于匹配描述或哈希。
        is_registered: 是否已注册筛选条件。
        is_banned: 是否被禁用筛选条件。
        sort_by: 排序字段。
        sort_order: 排序方向。
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        EmojiListResponse: 分页后的表情包列表。
    """
    try:
        verify_auth_token(maibot_session)

        statement = select(Images).where(col(Images.image_type) == ImageType.EMOJI)

        if search:
            statement = statement.where(
                (col(Images.description).contains(search)) | (col(Images.image_hash).contains(search))
            )

        statement = _apply_emoji_status_filter(statement, status)
        statement = _apply_emoji_format_filter(statement, format)

        if status is None:
            if is_registered is not None:
                statement = statement.where(col(Images.is_registered) == is_registered)

            if is_banned is not None:
                statement = statement.where(col(Images.is_banned) == is_banned)

        sort_field_map = {
            "usage_count": col(Images.query_count),
            "query_count": col(Images.query_count),
            "register_time": col(Images.register_time),
            "record_time": col(Images.record_time),
            "last_used_time": col(Images.last_used_time),
        }
        sort_field = sort_field_map.get(sort_by or "register_time", col(Images.register_time))
        statement = statement.order_by(sort_field.asc() if sort_order == "asc" else sort_field.desc())

        offset = (page - 1) * page_size
        statement = statement.offset(offset).limit(page_size)

        with get_db_session() as session:
            emojis = session.exec(statement).all()

            count_statement = select(func.count()).select_from(Images).where(col(Images.image_type) == ImageType.EMOJI)
            if search:
                count_statement = count_statement.where(
                    (col(Images.description).contains(search)) | (col(Images.image_hash).contains(search))
                )
            count_statement = _apply_emoji_status_filter(count_statement, status)
            count_statement = _apply_emoji_format_filter(count_statement, format)
            if status is None:
                if is_registered is not None:
                    count_statement = count_statement.where(col(Images.is_registered) == is_registered)
                if is_banned is not None:
                    count_statement = count_statement.where(col(Images.is_banned) == is_banned)
            total = session.exec(count_statement).one()
            data = [emoji_to_response(emoji) for emoji in emojis]

        return EmojiListResponse(
            success=True,
            total=total,
            page=page,
            page_size=page_size,
            data=data,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取表情包列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取表情包列表失败: {str(e)}") from e


@router.get("/{emoji_id}", response_model=EmojiDetailResponse)
async def get_emoji_detail(emoji_id: int, maibot_session: Optional[str] = Cookie(None)) -> EmojiDetailResponse:
    """获取表情包详细信息。

    Args:
        emoji_id: 表情包 ID。
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        EmojiDetailResponse: 表情包详细信息。
    """
    try:
        verify_auth_token(maibot_session)

        with get_db_session() as session:
            statement = select(Images).where(
                col(Images.id) == emoji_id,
                col(Images.image_type) == ImageType.EMOJI,
            )
            if not (emoji := session.exec(statement).first()):
                raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")

            return EmojiDetailResponse(success=True, data=emoji_to_response(emoji))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取表情包详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取表情包详情失败: {str(e)}") from e


@router.patch("/{emoji_id}", response_model=EmojiUpdateResponse)
async def update_emoji(
    emoji_id: int,
    request: EmojiUpdateRequest,
    maibot_session: Optional[str] = Cookie(None),
) -> EmojiUpdateResponse:
    """增量更新表情包。

    Args:
        emoji_id: 表情包 ID。
        request: 只包含需要更新字段的请求数据。
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        EmojiUpdateResponse: 更新结果和更新后的表情包数据。
    """
    try:
        verify_auth_token(maibot_session)

        with get_db_session() as session:
            statement = select(Images).where(
                col(Images.id) == emoji_id,
                col(Images.image_type) == ImageType.EMOJI,
            )
            emoji = session.exec(statement).first()

            if not emoji:
                raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")

            update_data = request.model_dump(exclude_unset=True)
            if not update_data:
                raise HTTPException(status_code=400, detail="未提供任何需要更新的字段")

            if "is_registered" in update_data and update_data["is_registered"] and not emoji.is_registered:
                if not await _review_emoji_record_for_registration(emoji):
                    raise HTTPException(status_code=400, detail="表情包未通过内容审核，无法注册")
                update_data["register_time"] = datetime.now()

            if "emotion" in update_data:
                normalized_description = _normalize_emoji_description(
                    description=update_data.get("description", ""),
                    emotion=update_data.get("emotion", ""),
                )
                update_data["description"] = normalized_description
                update_data.pop("emotion", None)

            if "description" in update_data:
                emoji.description = update_data["description"]
            if "is_registered" in update_data:
                emoji.is_registered = update_data["is_registered"]
            if "is_banned" in update_data:
                emoji.is_banned = update_data["is_banned"]
            if "register_time" in update_data:
                emoji.register_time = update_data["register_time"]

            session.add(emoji)

            # 若通过更新接口封禁表情包，同步从内存列表移除
            if update_data.get("is_banned") or update_data.get("is_registered") is False:
                from src.emoji_system.emoji_manager import emoji_manager

                emoji_manager.emojis = [
                    e for e in emoji_manager.emojis if e.file_hash != emoji.image_hash
                ]
                emoji_manager._emoji_num = len(emoji_manager.emojis)

            logger.info(f"表情包已更新: ID={emoji_id}, 字段: {list(update_data.keys())}")

            return EmojiUpdateResponse(
                success=True,
                message=f"成功更新 {len(update_data)} 个字段",
                data=emoji_to_response(emoji),
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"更新表情包失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新表情包失败: {str(e)}") from e


@router.delete("/{emoji_id}", response_model=EmojiDeleteResponse)
async def delete_emoji(emoji_id: int, maibot_session: Optional[str] = Cookie(None)) -> EmojiDeleteResponse:
    """删除表情包。

    Args:
        emoji_id: 表情包 ID。
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        EmojiDeleteResponse: 删除结果。
    """
    try:
        verify_auth_token(maibot_session)

        with get_db_session() as session:
            statement = select(Images).where(
                col(Images.id) == emoji_id,
                col(Images.image_type) == ImageType.EMOJI,
            )
            emoji = session.exec(statement).first()

            if not emoji:
                raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")

            emoji_hash = emoji.image_hash
            full_path = emoji.full_path
            was_registered = emoji.is_registered
            if not _delete_emoji_file(full_path):
                raise HTTPException(status_code=500, detail="删除表情包文件失败")
            _delete_emoji_thumbnail_cache(emoji_hash)
            if was_registered:
                _remove_emoji_from_memory(emoji_hash)
            session.delete(emoji)
            logger.info(f"表情包已删除: ID={emoji_id}, hash={emoji_hash}")
            return EmojiDeleteResponse(success=True, message=f"成功删除表情包: {emoji_hash}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"删除表情包失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除表情包失败: {str(e)}") from e


@router.get("/stats/summary")
async def get_emoji_stats(maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    """获取表情包统计数据。

    Args:
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        Dict[str, Any]: 表情包总数、格式分布和高频使用统计。
    """
    try:
        verify_auth_token(maibot_session)

        with get_db_session() as session:
            total_statement = select(func.count()).select_from(Images).where(col(Images.image_type) == ImageType.EMOJI)
            registered_statement = (
                select(func.count())
                .select_from(Images)
                .where(
                    col(Images.image_type) == ImageType.EMOJI,
                    col(Images.is_registered),
                )
            )
            banned_statement = (
                select(func.count())
                .select_from(Images)
                .where(
                    col(Images.image_type) == ImageType.EMOJI,
                    col(Images.is_banned),
                )
            )
            description_text = func.trim(func.coalesce(col(Images.description), ""))
            known_statement = (
                select(func.count())
                .select_from(Images)
                .where(
                    col(Images.image_type) == ImageType.EMOJI,
                    col(Images.is_registered).is_(False),
                    col(Images.is_banned).is_(False),
                    description_text != "",
                )
            )
            unknown_statement = (
                select(func.count())
                .select_from(Images)
                .where(
                    col(Images.image_type) == ImageType.EMOJI,
                    col(Images.is_registered).is_(False),
                    col(Images.is_banned).is_(False),
                    description_text == "",
                )
            )

            total = session.exec(total_statement).one()
            registered = session.exec(registered_statement).one()
            banned = session.exec(banned_statement).one()
            known = session.exec(known_statement).one()
            unknown = session.exec(unknown_statement).one()

            formats: Dict[str, int] = {}
            format_statement = select(Images.full_path).where(col(Images.image_type) == ImageType.EMOJI)
            for full_path in session.exec(format_statement).all():
                suffix = Path(full_path).suffix.lower().lstrip(".")
                fmt = suffix or "unknown"
                formats[fmt] = formats.get(fmt, 0) + 1

            top_used_statement = (
                select(Images)
                .where(col(Images.image_type) == ImageType.EMOJI)
                .order_by(col(Images.query_count).desc())
                .limit(10)
            )
            top_used_list = [
                {
                    "id": emoji.id,
                    "emoji_hash": emoji.image_hash,
                    "description": emoji.description,
                    "usage_count": emoji.query_count,
                }
                for emoji in session.exec(top_used_statement).all()
            ]

        return {
            "success": True,
            "data": {
                "total": total,
                "registered": registered,
                "banned": banned,
                "unregistered": total - registered,
                "known": known,
                "unknown": unknown,
                "adopted": registered,
                "discarded": banned,
                "formats": formats,
                "top_used": top_used_list,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取统计数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计数据失败: {str(e)}") from e


@router.post("/{emoji_id}/register", response_model=EmojiUpdateResponse)
async def register_emoji(emoji_id: int, maibot_session: Optional[str] = Cookie(None)) -> EmojiUpdateResponse:
    """注册表情包。

    Args:
        emoji_id: 表情包 ID。
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        EmojiUpdateResponse: 注册结果和更新后的表情包数据。
    """
    try:
        verify_auth_token(maibot_session)

        with get_db_session() as session:
            statement = select(Images).where(
                col(Images.id) == emoji_id,
                col(Images.image_type) == ImageType.EMOJI,
            )
            emoji = session.exec(statement).first()

            if not emoji:
                raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")
            if emoji.is_registered:
                return EmojiUpdateResponse(success=True, message="表情包已注册", data=emoji_to_response(emoji))
            if not await _review_emoji_record_for_registration(emoji):
                raise HTTPException(status_code=400, detail="表情包未通过内容审核，无法注册")

            emoji.is_registered = True
            emoji.is_banned = False
            emoji.register_time = datetime.now()
            session.add(emoji)

            logger.info(f"表情包已注册: ID={emoji_id}")
            return EmojiUpdateResponse(success=True, message="表情包注册成功", data=emoji_to_response(emoji))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"注册表情包失败: {e}")
        raise HTTPException(status_code=500, detail=f"注册表情包失败: {str(e)}") from e


@router.post("/{emoji_id}/ban", response_model=EmojiUpdateResponse)
async def ban_emoji(emoji_id: int, maibot_session: Optional[str] = Cookie(None)) -> EmojiUpdateResponse:
    """禁用表情包。

    Args:
        emoji_id: 表情包 ID。
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        EmojiUpdateResponse: 禁用结果和更新后的表情包数据。
    """
    try:
        verify_auth_token(maibot_session)

        with get_db_session() as session:
            statement = select(Images).where(
                col(Images.id) == emoji_id,
                col(Images.image_type) == ImageType.EMOJI,
            )
            emoji = session.exec(statement).first()

            if not emoji:
                raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")

            emoji.is_banned = True
            emoji.is_registered = False
            session.add(emoji)

            # 同步从内存列表移除，确保插件等消费者不会继续选中已封禁表情包
            from src.emoji_system.emoji_manager import emoji_manager

            emoji_manager.emojis = [
                e for e in emoji_manager.emojis if e.file_hash != emoji.image_hash
            ]
            emoji_manager._emoji_num = len(emoji_manager.emojis)

            logger.info(f"表情包已禁用: ID={emoji_id}")
            return EmojiUpdateResponse(success=True, message="表情包禁用成功", data=emoji_to_response(emoji))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"禁用表情包失败: {e}")
        raise HTTPException(status_code=500, detail=f"禁用表情包失败: {str(e)}") from e


@router.get("/{emoji_id}/thumbnail", response_model=None)
async def get_emoji_thumbnail(
    emoji_id: int,
    token: Optional[str] = Query(None, description="访问令牌"),
    maibot_session: Optional[str] = Cookie(None),
    original: bool = Query(False, description="是否返回原图"),
) -> FileResponse | JSONResponse:
    """获取表情包缩略图。

    Args:
        emoji_id: 表情包 ID。
        token: URL 中携带的访问令牌。
        maibot_session: WebUI 登录会话 Cookie。
        original: 是否返回原图。

    Returns:
        FileResponse | JSONResponse: 缩略图文件、原图文件或生成中的状态响应。
    """
    try:
        token_manager = get_token_manager()
        is_valid = False

        if maibot_session and token_manager.verify_token(maibot_session):
            is_valid = True
        elif token and token_manager.verify_token(token):
            is_valid = True

        if not is_valid:
            raise HTTPException(status_code=401, detail="Token 无效或已过期")

        with get_db_session() as session:
            statement = select(Images).where(
                col(Images.id) == emoji_id,
                col(Images.image_type) == ImageType.EMOJI,
            )
            emoji = session.exec(statement).first()

            if not emoji:
                raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")
            emoji_path = _resolve_existing_emoji_file_path(emoji.full_path)
            if emoji_path is None:
                raise HTTPException(status_code=404, detail="表情包文件不存在")

            if original:
                mime_types = {
                    "png": "image/png",
                    "jpg": "image/jpeg",
                    "jpeg": "image/jpeg",
                    "gif": "image/gif",
                    "webp": "image/webp",
                    "bmp": "image/bmp",
                }
                suffix = emoji_path.suffix.lower().lstrip(".")
                media_type = mime_types.get(suffix, "application/octet-stream")
                return FileResponse(
                    path=emoji_path,
                    media_type=media_type,
                    filename=f"{emoji.image_hash}.{suffix}",
                )

            cache_path = get_thumbnail_cache_path(emoji.image_hash)
            if cache_path.exists():
                return FileResponse(
                    path=str(cache_path),
                    media_type="image/webp",
                    filename=f"{emoji.image_hash}_thumb.webp",
                )

            generating_lock = get_generating_lock()
            generating_thumbnails = get_generating_thumbnails()
            with generating_lock:
                if emoji.image_hash not in generating_thumbnails:
                    generating_thumbnails.add(emoji.image_hash)
                    get_thumbnail_executor().submit(background_generate_thumbnail, str(emoji_path), emoji.image_hash)

            return JSONResponse(
                status_code=202,
                content={
                    "status": "generating",
                    "message": "缩略图正在生成中，请稍后重试",
                    "emoji_id": emoji_id,
                },
                headers={"Retry-After": "1"},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取表情包缩略图失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取表情包缩略图失败: {str(e)}") from e


@router.post("/batch/delete", response_model=BatchDeleteResponse)
async def batch_delete_emojis(
    request: BatchDeleteRequest,
    maibot_session: Optional[str] = Cookie(None),
) -> BatchDeleteResponse:
    """批量删除表情包。

    Args:
        request: 包含要删除表情包 ID 列表的请求。
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        BatchDeleteResponse: 批量删除结果。
    """
    try:
        verify_auth_token(maibot_session)

        if not request.emoji_ids:
            raise HTTPException(status_code=400, detail="未提供要删除的表情包ID")

        deleted_count = 0
        failed_count = 0
        failed_ids: List[int] = []

        for emoji_id in request.emoji_ids:
            try:
                with get_db_session() as session:
                    statement = select(Images).where(
                        col(Images.id) == emoji_id,
                        col(Images.image_type) == ImageType.EMOJI,
                    )
                    if emoji := session.exec(statement).first():
                        emoji_hash = emoji.image_hash
                        full_path = emoji.full_path
                        was_registered = emoji.is_registered
                        if not _delete_emoji_file(full_path):
                            failed_count += 1
                            failed_ids.append(emoji_id)
                            continue
                        _delete_emoji_thumbnail_cache(emoji_hash)
                        if was_registered:
                            _remove_emoji_from_memory(emoji_hash)
                        session.delete(emoji)
                        deleted_count += 1
                        logger.info(f"批量删除表情包: {emoji_id}")
                    else:
                        failed_count += 1
                        failed_ids.append(emoji_id)
            except Exception as e:
                logger.error(f"删除表情包 {emoji_id} 失败: {e}")
                failed_count += 1
                failed_ids.append(emoji_id)

        message = f"成功删除 {deleted_count} 个表情包"
        if failed_count > 0:
            message += f"，{failed_count} 个失败"

        return BatchDeleteResponse(
            success=True,
            message=message,
            deleted_count=deleted_count,
            failed_count=failed_count,
            failed_ids=failed_ids,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"批量删除表情包失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量删除失败: {str(e)}") from e


@router.post("/upload", response_model=EmojiUploadResponse)
async def upload_emoji(
    file: EmojiFile,
    description: DescriptionForm = "",
    emotion: EmotionForm = "",
    is_registered: IsRegisteredForm = True,
    maibot_session: Optional[str] = Cookie(None),
) -> EmojiUploadResponse:
    """上传并注册表情包。

    Args:
        file: 上传的表情包文件。
        description: 表情包描述。
        emotion: 情绪标签。
        is_registered: 是否上传后直接注册。
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        EmojiUploadResponse: 上传结果和新表情包数据。
    """
    try:
        verify_auth_token(maibot_session)

        if not file.content_type:
            raise HTTPException(status_code=400, detail="无法识别文件类型")

        allowed_types = ["image/jpeg", "image/png", "image/gif", "image/webp"]
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型: {file.content_type}，支持: {', '.join(allowed_types)}",
            )

        file_content = await file.read()
        if not file_content:
            raise HTTPException(status_code=400, detail="文件内容为空")

        try:
            with Image.open(io.BytesIO(file_content)) as img:
                img.verify()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"无效的图片文件: {str(e)}") from e

        with Image.open(io.BytesIO(file_content)) as img:
            img_format = img.format.lower() if img.format else "png"

        emoji_hash = hashlib.sha256(file_content).hexdigest()
        final_description = _normalize_emoji_description(description=description, emotion=emotion)
        current_time = datetime.now()

        with get_db_session() as session:
            existing_statement = select(Images).where(
                col(Images.image_hash) == emoji_hash,
                col(Images.image_type) == ImageType.EMOJI,
            )
            if existing_emoji := session.exec(existing_statement).first():
                if _resolve_existing_emoji_file_path(existing_emoji.full_path) is None or existing_emoji.no_file_flag:
                    existing_emoji.full_path = _save_uploaded_emoji_file(file_content, emoji_hash, img_format)
                    existing_emoji.no_file_flag = False
                existing_emoji.description = final_description
                existing_emoji.is_registered = True
                existing_emoji.is_banned = False
                existing_emoji.register_time = current_time
                session.add(existing_emoji)
                session.flush()
                return EmojiUploadResponse(
                    success=True,
                    message="表情包已存在，已标记为据为己用",
                    data=emoji_to_response(existing_emoji),
                )

        full_path = _save_uploaded_emoji_file(file_content, emoji_hash, img_format)

        with get_db_session() as session:
            emoji = Images(
                image_type=ImageType.EMOJI,
                full_path=full_path,
                image_hash=emoji_hash,
                description=final_description,
                query_count=0,
                is_registered=True,
                is_banned=False,
                record_time=current_time,
                register_time=current_time,
                last_used_time=None,
            )
            session.add(emoji)
            session.flush()

            logger.info(
                f"表情包已上传: ID={emoji.id}, hash={emoji_hash}, registered={emoji.is_registered}"
            )
            message = "表情包上传成功"
            message += "并已注册"
            return EmojiUploadResponse(
                success=True,
                message=message,
                data=emoji_to_response(emoji),
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"上传表情包失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}") from e


@router.post("/batch/upload")
async def batch_upload_emoji(
    files: EmojiFiles,
    emotion: EmotionForm = "",
    is_registered: IsRegisteredForm = True,
    maibot_session: Optional[str] = Cookie(None),
) -> Dict[str, Any]:
    """批量上传表情包。

    Args:
        files: 上传的表情包文件列表。
        emotion: 批量应用的情绪标签。
        is_registered: 是否上传后直接注册。
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        Dict[str, Any]: 每个文件的上传结果和汇总统计。
    """
    try:
        verify_auth_token(maibot_session)

        results: Dict[str, Any] = {
            "success": True,
            "total": len(files),
            "uploaded": 0,
            "failed": 0,
            "details": [],
        }

        allowed_types = ["image/jpeg", "image/png", "image/gif", "image/webp"]
        os.makedirs(EMOJI_DIR, exist_ok=True)

        for file in files:
            try:
                if file.content_type not in allowed_types:
                    results["failed"] += 1
                    results["details"].append(
                        {
                            "filename": file.filename,
                            "success": False,
                            "error": f"不支持的文件类型: {file.content_type}",
                        }
                    )
                    continue

                file_content = await file.read()
                if not file_content:
                    results["failed"] += 1
                    results["details"].append({"filename": file.filename, "success": False, "error": "文件内容为空"})
                    continue

                try:
                    with Image.open(io.BytesIO(file_content)) as img:
                        img_format = img.format.lower() if img.format else "png"
                except Exception as e:
                    results["failed"] += 1
                    results["details"].append(
                        {"filename": file.filename, "success": False, "error": f"无效的图片: {str(e)}"}
                    )
                    continue

                emoji_hash = hashlib.sha256(file_content).hexdigest()
                current_time = datetime.now()
                final_description = _normalize_emoji_description(emotion=emotion)

                with get_db_session() as session:
                    existing_statement = select(Images).where(
                        col(Images.image_hash) == emoji_hash,
                        col(Images.image_type) == ImageType.EMOJI,
                    )
                    if existing_emoji := session.exec(existing_statement).first():
                        if _resolve_existing_emoji_file_path(existing_emoji.full_path) is None or existing_emoji.no_file_flag:
                            existing_emoji.full_path = _save_uploaded_emoji_file(file_content, emoji_hash, img_format)
                            existing_emoji.no_file_flag = False
                        existing_emoji.description = final_description
                        existing_emoji.is_registered = True
                        existing_emoji.is_banned = False
                        existing_emoji.register_time = current_time
                        session.add(existing_emoji)
                        session.flush()
                        results["uploaded"] += 1
                        results["details"].append(
                            {"filename": file.filename, "success": True, "id": existing_emoji.id, "existed": True}
                        )
                        continue

                full_path = _save_uploaded_emoji_file(file_content, emoji_hash, img_format)

                with get_db_session() as session:
                    emoji = Images(
                        image_type=ImageType.EMOJI,
                        full_path=full_path,
                        image_hash=emoji_hash,
                        description=final_description,
                        query_count=0,
                        is_registered=True,
                        is_banned=False,
                        record_time=current_time,
                        register_time=current_time,
                        last_used_time=None,
                    )
                    session.add(emoji)
                    session.flush()

                    results["uploaded"] += 1
                    detail = {"filename": file.filename, "success": True, "id": emoji.id}
                    results["details"].append(detail)
            except Exception as e:
                results["failed"] += 1
                results["details"].append({"filename": file.filename, "success": False, "error": str(e)})

        results["message"] = f"成功上传 {results['uploaded']} 个，失败 {results['failed']} 个"
        return results
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"批量上传表情包失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量上传失败: {str(e)}") from e


@router.get("/thumbnail-cache/stats", response_model=ThumbnailCacheStatsResponse)
async def get_thumbnail_cache_stats(maibot_session: Optional[str] = Cookie(None)) -> ThumbnailCacheStatsResponse:
    """获取缩略图缓存统计信息。

    Args:
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        ThumbnailCacheStatsResponse: 缩略图缓存数量、大小和覆盖率统计。
    """
    try:
        verify_auth_token(maibot_session)

        ensure_thumbnail_cache_dir()
        cache_files = list(THUMBNAIL_CACHE_DIR.glob("*.webp"))
        total_count = len(cache_files)
        total_size_mb = round(sum(item.stat().st_size for item in cache_files) / (1024 * 1024), 2)

        with get_db_session() as session:
            count_statement = select(func.count()).select_from(Images).where(col(Images.image_type) == ImageType.EMOJI)
            emoji_count = session.exec(count_statement).one()

        coverage_percent = round((total_count / emoji_count * 100) if emoji_count > 0 else 0, 1)
        return ThumbnailCacheStatsResponse(
            success=True,
            cache_dir=str(THUMBNAIL_CACHE_DIR.absolute()),
            total_count=total_count,
            total_size_mb=total_size_mb,
            emoji_count=emoji_count,
            coverage_percent=coverage_percent,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取缩略图缓存统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计失败: {str(e)}") from e


@router.post("/thumbnail-cache/cleanup", response_model=ThumbnailCleanupResponse)
async def cleanup_thumbnail_cache(maibot_session: Optional[str] = Cookie(None)) -> ThumbnailCleanupResponse:
    """清理孤立的缩略图缓存。

    Args:
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        ThumbnailCleanupResponse: 清理结果和删除数量。
    """
    try:
        verify_auth_token(maibot_session)

        cleaned, kept = cleanup_orphaned_thumbnails()
        return ThumbnailCleanupResponse(
            success=True,
            message=f"清理完成：删除 {cleaned} 个孤立缓存，保留 {kept} 个有效缓存",
            cleaned_count=cleaned,
            kept_count=kept,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"清理缩略图缓存失败: {e}")
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}") from e


@router.post("/thumbnail-cache/preheat", response_model=ThumbnailPreheatResponse)
async def preheat_thumbnail_cache(
    limit: int = Query(100, ge=1, le=1000, description="最多预热数量"),
    maibot_session: Optional[str] = Cookie(None),
) -> ThumbnailPreheatResponse:
    """预热缩略图缓存。

    Args:
        limit: 最多预热的缩略图数量。
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        ThumbnailPreheatResponse: 预热生成、跳过和失败数量统计。
    """
    try:
        verify_auth_token(maibot_session)

        ensure_thumbnail_cache_dir()

        with get_db_session() as session:
            statement = (
                select(Images)
                .where(
                    col(Images.image_type) == ImageType.EMOJI,
                    col(Images.is_banned).is_(False),
                )
                .order_by(col(Images.query_count).desc())
                .limit(limit * 2)
            )
            emojis = [
                {
                    "image_hash": emoji.image_hash,
                    "full_path": emoji.full_path,
                }
                for emoji in session.exec(statement).all()
            ]

        generated = 0
        skipped = 0
        failed = 0

        for emoji in emojis:
            if generated >= limit:
                break

            image_hash = emoji["image_hash"]
            full_path = emoji["full_path"]

            cache_path = get_thumbnail_cache_path(image_hash)
            if cache_path.exists():
                skipped += 1
                continue
            resolved_full_path = _resolve_existing_emoji_file_path(full_path)
            if resolved_full_path is None:
                failed += 1
                continue

            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    get_thumbnail_executor(), generate_thumbnail, str(resolved_full_path), image_hash
                )
                generated += 1
            except Exception as e:
                logger.warning(f"预热缩略图失败 {image_hash}: {e}")
                failed += 1

        return ThumbnailPreheatResponse(
            success=True,
            message=f"预热完成：生成 {generated} 个，跳过 {skipped} 个已缓存，失败 {failed} 个",
            generated_count=generated,
            skipped_count=skipped,
            failed_count=failed,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"预热缩略图缓存失败: {e}")
        raise HTTPException(status_code=500, detail=f"预热失败: {str(e)}") from e


@router.delete("/thumbnail-cache/clear", response_model=ThumbnailCleanupResponse)
async def clear_all_thumbnail_cache(maibot_session: Optional[str] = Cookie(None)) -> ThumbnailCleanupResponse:
    """清空所有缩略图缓存。

    Args:
        maibot_session: WebUI 登录会话 Cookie。

    Returns:
        ThumbnailCleanupResponse: 清空结果和删除数量。
    """
    try:
        verify_auth_token(maibot_session)

        if not THUMBNAIL_CACHE_DIR.exists():
            return ThumbnailCleanupResponse(
                success=True,
                message="缓存目录不存在，无需清理",
                cleaned_count=0,
                kept_count=0,
            )

        cleaned = 0
        for cache_file in THUMBNAIL_CACHE_DIR.glob("*.webp"):
            try:
                cache_file.unlink()
                cleaned += 1
            except Exception as e:
                logger.warning(f"删除缓存文件失败 {cache_file.name}: {e}")

        logger.info(f"已清空缩略图缓存: 删除 {cleaned} 个文件")
        return ThumbnailCleanupResponse(
            success=True,
            message=f"已清空所有缩略图缓存：删除 {cleaned} 个文件",
            cleaned_count=cleaned,
            kept_count=0,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"清空缩略图缓存失败: {e}")
        raise HTTPException(status_code=500, detail=f"清空失败: {str(e)}") from e
