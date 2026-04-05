"""表情包管理 API 路由"""

import asyncio
import hashlib
import io
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Cookie, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
from sqlalchemy import func
from sqlmodel import col, select

from src.common.database.database import get_db_session
from src.common.database.database_model import Images, ImageType
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
    EMOJI_REGISTERED_DIR,
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


@router.get("/list", response_model=EmojiListResponse)
async def get_emoji_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    is_registered: Optional[bool] = Query(None, description="是否已注册筛选"),
    is_banned: Optional[bool] = Query(None, description="是否被禁用筛选"),
    sort_by: Optional[str] = Query("query_count", description="排序字段"),
    sort_order: Optional[str] = Query("desc", description="排序方向"),
    maibot_session: Optional[str] = Cookie(None),
) -> EmojiListResponse:
    """获取表情包列表。"""
    try:
        verify_auth_token(maibot_session)

        statement = select(Images).where(col(Images.image_type) == ImageType.EMOJI)

        if search:
            statement = statement.where(
                (col(Images.description).contains(search)) | (col(Images.image_hash).contains(search))
            )

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
        sort_field = sort_field_map.get(sort_by or "query_count", col(Images.query_count))
        statement = statement.order_by(sort_field.asc() if sort_order == "asc" else sort_field.desc())

        offset = (page - 1) * page_size
        statement = statement.offset(offset).limit(page_size)

        with get_db_session() as session:
            emojis = session.exec(statement).all()
            emoji_responses = [emoji_to_response(emoji) for emoji in emojis]

            count_statement = select(func.count()).select_from(Images).where(col(Images.image_type) == ImageType.EMOJI)
            if search:
                count_statement = count_statement.where(
                    (col(Images.description).contains(search)) | (col(Images.image_hash).contains(search))
                )
            if is_registered is not None:
                count_statement = count_statement.where(col(Images.is_registered) == is_registered)
            if is_banned is not None:
                count_statement = count_statement.where(col(Images.is_banned) == is_banned)
            total = session.exec(count_statement).one()

        return EmojiListResponse(
            success=True,
            total=total,
            page=page,
            page_size=page_size,
            data=emoji_responses,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取表情包列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取表情包列表失败: {str(e)}") from e


@router.get("/{emoji_id}", response_model=EmojiDetailResponse)
async def get_emoji_detail(emoji_id: int, maibot_session: Optional[str] = Cookie(None)) -> EmojiDetailResponse:
    """获取表情包详细信息。"""
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
    """增量更新表情包。"""
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
                update_data["register_time"] = datetime.now()

            for field, value in update_data.items():
                setattr(emoji, field, value)

            session.add(emoji)
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
    """删除表情包。"""
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
    """获取表情包统计数据。"""
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

            total = session.exec(total_statement).one()
            registered = session.exec(registered_statement).one()
            banned = session.exec(banned_statement).one()

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
    """注册表情包。"""
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
                raise HTTPException(status_code=400, detail="该表情包已经注册")

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
    """禁用表情包。"""
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
    """获取表情包缩略图。"""
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
            if not os.path.exists(emoji.full_path):
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
                suffix = Path(emoji.full_path).suffix.lower().lstrip(".")
                media_type = mime_types.get(suffix, "application/octet-stream")
                return FileResponse(
                    path=emoji.full_path,
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
                    get_thumbnail_executor().submit(background_generate_thumbnail, emoji.full_path, emoji.image_hash)

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
    """批量删除表情包。"""
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
    """上传并注册表情包。"""
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

        emoji_hash = hashlib.md5(file_content).hexdigest()

        with get_db_session() as session:
            existing_statement = select(Images).where(
                col(Images.image_hash) == emoji_hash,
                col(Images.image_type) == ImageType.EMOJI,
            )
            if existing_emoji := session.exec(existing_statement).first():
                raise HTTPException(status_code=409, detail=f"已存在相同的表情包 (ID: {existing_emoji.id})")

        os.makedirs(EMOJI_REGISTERED_DIR, exist_ok=True)

        timestamp = int(datetime.now().timestamp())
        filename = f"emoji_{timestamp}_{emoji_hash[:8]}.{img_format}"
        full_path = os.path.join(EMOJI_REGISTERED_DIR, filename)

        counter = 1
        while os.path.exists(full_path):
            filename = f"emoji_{timestamp}_{emoji_hash[:8]}_{counter}.{img_format}"
            full_path = os.path.join(EMOJI_REGISTERED_DIR, filename)
            counter += 1

        with open(full_path, "wb") as output_file:
            _ = output_file.write(file_content)

        logger.info(f"表情包文件已保存: {full_path}")
        emotion_str = ",".join(item.strip() for item in emotion.split(",") if item.strip()) if emotion else ""

        current_time = datetime.now()
        with get_db_session() as session:
            emoji = Images(
                image_type=ImageType.EMOJI,
                full_path=full_path,
                image_hash=emoji_hash,
                description=description,
                emotion=emotion_str or None,
                query_count=0,
                is_registered=is_registered,
                is_banned=False,
                record_time=current_time,
                register_time=current_time if is_registered else None,
                last_used_time=None,
            )
            session.add(emoji)
            session.flush()

            logger.info(f"表情包已上传并注册: ID={emoji.id}, hash={emoji_hash}")
            return EmojiUploadResponse(
                success=True,
                message="表情包上传成功" + ("并已注册" if is_registered else ""),
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
    """批量上传表情包。"""
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
        os.makedirs(EMOJI_REGISTERED_DIR, exist_ok=True)

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

                emoji_hash = hashlib.md5(file_content).hexdigest()

                with get_db_session() as session:
                    existing_statement = select(Images).where(
                        col(Images.image_hash) == emoji_hash,
                        col(Images.image_type) == ImageType.EMOJI,
                    )
                    if session.exec(existing_statement).first():
                        results["failed"] += 1
                        results["details"].append(
                            {"filename": file.filename, "success": False, "error": "已存在相同的表情包"}
                        )
                        continue

                timestamp = int(datetime.now().timestamp())
                filename = f"emoji_{timestamp}_{emoji_hash[:8]}.{img_format}"
                full_path = os.path.join(EMOJI_REGISTERED_DIR, filename)

                counter = 1
                while os.path.exists(full_path):
                    filename = f"emoji_{timestamp}_{emoji_hash[:8]}_{counter}.{img_format}"
                    full_path = os.path.join(EMOJI_REGISTERED_DIR, filename)
                    counter += 1

                with open(full_path, "wb") as output_file:
                    _ = output_file.write(file_content)

                emotion_str = ",".join(item.strip() for item in emotion.split(",") if item.strip()) if emotion else ""
                current_time = datetime.now()

                with get_db_session() as session:
                    emoji = Images(
                        image_type=ImageType.EMOJI,
                        full_path=full_path,
                        image_hash=emoji_hash,
                        description="",
                        emotion=emotion_str or None,
                        query_count=0,
                        is_registered=is_registered,
                        is_banned=False,
                        record_time=current_time,
                        register_time=current_time if is_registered else None,
                        last_used_time=None,
                    )
                    session.add(emoji)
                    session.flush()

                    results["uploaded"] += 1
                    results["details"].append({"filename": file.filename, "success": True, "id": emoji.id})
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
    """获取缩略图缓存统计信息。"""
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
    """清理孤立的缩略图缓存。"""
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
    """预热缩略图缓存。"""
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
            emojis = session.exec(statement).all()

        generated = 0
        skipped = 0
        failed = 0

        for emoji in emojis:
            if generated >= limit:
                break

            cache_path = get_thumbnail_cache_path(emoji.image_hash)
            if cache_path.exists():
                skipped += 1
                continue
            if not os.path.exists(emoji.full_path):
                failed += 1
                continue

            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    get_thumbnail_executor(), generate_thumbnail, emoji.full_path, emoji.image_hash
                )
                generated += 1
            except Exception as e:
                logger.warning(f"预热缩略图失败 {emoji.image_hash}: {e}")
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
    """清空所有缩略图缓存。"""
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
