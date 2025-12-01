""" 表情包管理 API 路由"""

from fastapi import APIRouter, HTTPException, Header, Query, UploadFile, File, Form, Cookie
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Annotated
from src.common.logger import get_logger
from src.common.database.database_model import Emoji
from .token_manager import get_token_manager
from .auth import verify_auth_token_from_cookie_or_header
import time
import os
import hashlib
from PIL import Image
import io
from pathlib import Path
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = get_logger("webui.emoji")

# ==================== 缩略图缓存配置 ====================
# 缩略图缓存目录
THUMBNAIL_CACHE_DIR = Path("data/emoji_thumbnails")
# 缩略图尺寸 (宽, 高)
THUMBNAIL_SIZE = (200, 200)
# 缩略图质量 (WebP 格式, 1-100)
THUMBNAIL_QUALITY = 80
# 缓存锁，防止并发生成同一缩略图
_thumbnail_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()
# 缩略图生成专用线程池（避免阻塞事件循环）
_thumbnail_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thumbnail")
# 正在生成中的缩略图哈希集合（防止重复提交任务）
_generating_thumbnails: set[str] = set()
_generating_lock = threading.Lock()


def _get_thumbnail_lock(file_hash: str) -> threading.Lock:
    """获取指定文件哈希的锁，用于防止并发生成同一缩略图"""
    with _locks_lock:
        if file_hash not in _thumbnail_locks:
            _thumbnail_locks[file_hash] = threading.Lock()
        return _thumbnail_locks[file_hash]


def _background_generate_thumbnail(source_path: str, file_hash: str) -> None:
    """
    后台生成缩略图（在线程池中执行）
    
    生成完成后自动从 generating 集合中移除
    """
    try:
        _generate_thumbnail(source_path, file_hash)
    except Exception as e:
        logger.warning(f"后台生成缩略图失败 {file_hash}: {e}")
    finally:
        with _generating_lock:
            _generating_thumbnails.discard(file_hash)


def _ensure_thumbnail_cache_dir() -> Path:
    """确保缩略图缓存目录存在"""
    THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return THUMBNAIL_CACHE_DIR


def _get_thumbnail_cache_path(file_hash: str) -> Path:
    """获取缩略图缓存路径"""
    return THUMBNAIL_CACHE_DIR / f"{file_hash}.webp"


def _generate_thumbnail(source_path: str, file_hash: str) -> Path:
    """
    生成缩略图并保存到缓存目录
    
    Args:
        source_path: 原图路径
        file_hash: 文件哈希值，用作缓存文件名
        
    Returns:
        缩略图路径
        
    Features:
        - GIF: 提取第一帧作为缩略图
        - 所有格式统一转为 WebP
        - 保持宽高比缩放
    """
    _ensure_thumbnail_cache_dir()
    cache_path = _get_thumbnail_cache_path(file_hash)
    
    # 使用锁防止并发生成同一缩略图
    lock = _get_thumbnail_lock(file_hash)
    with lock:
        # 双重检查，可能在等待锁时已被其他线程生成
        if cache_path.exists():
            return cache_path
        
        try:
            with Image.open(source_path) as img:
                # GIF 处理：提取第一帧
                if hasattr(img, 'n_frames') and img.n_frames > 1:
                    img.seek(0)  # 确保在第一帧
                
                # 转换为 RGB/RGBA（WebP 支持透明度）
                if img.mode in ('P', 'PA'):
                    # 调色板模式转换为 RGBA 以保留透明度
                    img = img.convert('RGBA')
                elif img.mode == 'LA':
                    img = img.convert('RGBA')
                elif img.mode not in ('RGB', 'RGBA'):
                    img = img.convert('RGB')
                
                # 创建缩略图（保持宽高比）
                img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
                
                # 保存为 WebP 格式
                img.save(cache_path, 'WEBP', quality=THUMBNAIL_QUALITY, method=6)
                
                logger.debug(f"生成缩略图: {file_hash} -> {cache_path}")
                
        except Exception as e:
            logger.warning(f"生成缩略图失败 {file_hash}: {e}，将返回原图")
            # 生成失败时不创建缓存文件，下次会重试
            raise
    
    return cache_path


def cleanup_orphaned_thumbnails() -> tuple[int, int]:
    """
    清理孤立的缩略图缓存（原图已不存在的缩略图）
    
    Returns:
        (清理数量, 保留数量)
    """
    if not THUMBNAIL_CACHE_DIR.exists():
        return 0, 0
    
    # 获取所有表情包的哈希值
    valid_hashes = set()
    for emoji in Emoji.select(Emoji.emoji_hash):
        valid_hashes.add(emoji.emoji_hash)
    
    cleaned = 0
    kept = 0
    
    for cache_file in THUMBNAIL_CACHE_DIR.glob("*.webp"):
        file_hash = cache_file.stem
        if file_hash not in valid_hashes:
            try:
                cache_file.unlink()
                cleaned += 1
                logger.debug(f"清理孤立缩略图: {cache_file.name}")
            except Exception as e:
                logger.warning(f"清理缩略图失败 {cache_file.name}: {e}")
        else:
            kept += 1
    
    if cleaned > 0:
        logger.info(f"清理孤立缩略图: 删除 {cleaned} 个，保留 {kept} 个")
    
    return cleaned, kept

# 模块级别的类型别名（解决 B008 ruff 错误）
EmojiFile = Annotated[UploadFile, File(description="表情包图片文件")]
EmojiFiles = Annotated[List[UploadFile], File(description="多个表情包图片文件")]
DescriptionForm = Annotated[str, Form(description="表情包描述")]
EmotionForm = Annotated[str, Form(description="情感标签，多个用逗号分隔")]
IsRegisteredForm = Annotated[bool, Form(description="是否直接注册")]

# 创建路由器
router = APIRouter(prefix="/emoji", tags=["Emoji"])


class EmojiResponse(BaseModel):
    """表情包响应"""

    id: int
    full_path: str
    format: str
    emoji_hash: str
    description: str
    query_count: int
    is_registered: bool
    is_banned: bool
    emotion: Optional[str]  # 直接返回字符串
    record_time: float
    register_time: Optional[float]
    usage_count: int
    last_used_time: Optional[float]


class EmojiListResponse(BaseModel):
    """表情包列表响应"""

    success: bool
    total: int
    page: int
    page_size: int
    data: List[EmojiResponse]


class EmojiDetailResponse(BaseModel):
    """表情包详情响应"""

    success: bool
    data: EmojiResponse


class EmojiUpdateRequest(BaseModel):
    """表情包更新请求"""

    description: Optional[str] = None
    is_registered: Optional[bool] = None
    is_banned: Optional[bool] = None
    emotion: Optional[str] = None


class EmojiUpdateResponse(BaseModel):
    """表情包更新响应"""

    success: bool
    message: str
    data: Optional[EmojiResponse] = None


class EmojiDeleteResponse(BaseModel):
    """表情包删除响应"""

    success: bool
    message: str


class BatchDeleteRequest(BaseModel):
    """批量删除请求"""

    emoji_ids: List[int]


class BatchDeleteResponse(BaseModel):
    """批量删除响应"""

    success: bool
    message: str
    deleted_count: int
    failed_count: int
    failed_ids: List[int] = []


def verify_auth_token(
    maibot_session: Optional[str] = None,
    authorization: Optional[str] = None,
) -> bool:
    """验证认证 Token，支持 Cookie 和 Header"""
    return verify_auth_token_from_cookie_or_header(maibot_session, authorization)


def emoji_to_response(emoji: Emoji) -> EmojiResponse:
    """将 Emoji 模型转换为响应对象"""
    return EmojiResponse(
        id=emoji.id,
        full_path=emoji.full_path,
        format=emoji.format,
        emoji_hash=emoji.emoji_hash,
        description=emoji.description,
        query_count=emoji.query_count,
        is_registered=emoji.is_registered,
        is_banned=emoji.is_banned,
        emotion=str(emoji.emotion) if emoji.emotion is not None else None,
        record_time=emoji.record_time,
        register_time=emoji.register_time,
        usage_count=emoji.usage_count,
        last_used_time=emoji.last_used_time,
    )


@router.get("/list", response_model=EmojiListResponse)
async def get_emoji_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    is_registered: Optional[bool] = Query(None, description="是否已注册筛选"),
    is_banned: Optional[bool] = Query(None, description="是否被禁用筛选"),
    format: Optional[str] = Query(None, description="格式筛选"),
    sort_by: Optional[str] = Query("usage_count", description="排序字段"),
    sort_order: Optional[str] = Query("desc", description="排序方向"),
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    获取表情包列表

    Args:
        page: 页码 (从 1 开始)
        page_size: 每页数量 (1-100)
        search: 搜索关键词 (匹配 description, emoji_hash)
        is_registered: 是否已注册筛选
        is_banned: 是否被禁用筛选
        format: 格式筛选
        sort_by: 排序字段 (usage_count, register_time, record_time, last_used_time)
        sort_order: 排序方向 (asc, desc)
        authorization: Authorization header

    Returns:
        表情包列表
    """
    try:
        verify_auth_token(maibot_session, authorization)

        # 构建查询
        query = Emoji.select()

        # 搜索过滤
        if search:
            query = query.where((Emoji.description.contains(search)) | (Emoji.emoji_hash.contains(search)))

        # 注册状态过滤
        if is_registered is not None:
            query = query.where(Emoji.is_registered == is_registered)

        # 禁用状态过滤
        if is_banned is not None:
            query = query.where(Emoji.is_banned == is_banned)

        # 格式过滤
        if format:
            query = query.where(Emoji.format == format)

        # 排序字段映射
        sort_field_map = {
            "usage_count": Emoji.usage_count,
            "register_time": Emoji.register_time,
            "record_time": Emoji.record_time,
            "last_used_time": Emoji.last_used_time,
        }

        # 获取排序字段，默认使用 usage_count
        sort_field = sort_field_map.get(sort_by, Emoji.usage_count)

        # 应用排序
        if sort_order == "asc":
            query = query.order_by(sort_field.asc())
        else:
            query = query.order_by(sort_field.desc())

        # 获取总数
        total = query.count()

        # 分页
        offset = (page - 1) * page_size
        emojis = query.offset(offset).limit(page_size)

        # 转换为响应对象
        data = [emoji_to_response(emoji) for emoji in emojis]

        return EmojiListResponse(success=True, total=total, page=page, page_size=page_size, data=data)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取表情包列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取表情包列表失败: {str(e)}") from e


@router.get("/{emoji_id}", response_model=EmojiDetailResponse)
async def get_emoji_detail(emoji_id: int, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)):
    """
    获取表情包详细信息

    Args:
        emoji_id: 表情包ID
        authorization: Authorization header

    Returns:
        表情包详细信息
    """
    try:
        verify_auth_token(maibot_session, authorization)

        emoji = Emoji.get_or_none(Emoji.id == emoji_id)

        if not emoji:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")

        return EmojiDetailResponse(success=True, data=emoji_to_response(emoji))

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取表情包详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取表情包详情失败: {str(e)}") from e


@router.patch("/{emoji_id}", response_model=EmojiUpdateResponse)
async def update_emoji(emoji_id: int, request: EmojiUpdateRequest, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)):
    """
    增量更新表情包（只更新提供的字段）

    Args:
        emoji_id: 表情包ID
        request: 更新请求（只包含需要更新的字段）
        authorization: Authorization header

    Returns:
        更新结果
    """
    try:
        verify_auth_token(maibot_session, authorization)

        emoji = Emoji.get_or_none(Emoji.id == emoji_id)

        if not emoji:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")

        # 只更新提供的字段
        update_data = request.model_dump(exclude_unset=True)

        if not update_data:
            raise HTTPException(status_code=400, detail="未提供任何需要更新的字段")

        # emotion 字段直接使用字符串,无需转换

        # 如果注册状态从 False 变为 True，记录注册时间
        if "is_registered" in update_data and update_data["is_registered"] and not emoji.is_registered:
            update_data["register_time"] = time.time()

        # 执行更新
        for field, value in update_data.items():
            setattr(emoji, field, value)

        emoji.save()

        logger.info(f"表情包已更新: ID={emoji_id}, 字段: {list(update_data.keys())}")

        return EmojiUpdateResponse(
            success=True, message=f"成功更新 {len(update_data)} 个字段", data=emoji_to_response(emoji)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"更新表情包失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新表情包失败: {str(e)}") from e


@router.delete("/{emoji_id}", response_model=EmojiDeleteResponse)
async def delete_emoji(emoji_id: int, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)):
    """
    删除表情包

    Args:
        emoji_id: 表情包ID
        authorization: Authorization header

    Returns:
        删除结果
    """
    try:
        verify_auth_token(maibot_session, authorization)

        emoji = Emoji.get_or_none(Emoji.id == emoji_id)

        if not emoji:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")

        # 记录删除信息
        emoji_hash = emoji.emoji_hash

        # 执行删除
        emoji.delete_instance()

        logger.info(f"表情包已删除: ID={emoji_id}, hash={emoji_hash}")

        return EmojiDeleteResponse(success=True, message=f"成功删除表情包: {emoji_hash}")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"删除表情包失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除表情包失败: {str(e)}") from e


@router.get("/stats/summary")
async def get_emoji_stats(maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)):
    """
    获取表情包统计数据

    Args:
        authorization: Authorization header

    Returns:
        统计数据
    """
    try:
        verify_auth_token(maibot_session, authorization)

        total = Emoji.select().count()
        registered = Emoji.select().where(Emoji.is_registered).count()
        banned = Emoji.select().where(Emoji.is_banned).count()

        # 按格式统计
        formats = {}
        for emoji in Emoji.select(Emoji.format):
            fmt = emoji.format
            formats[fmt] = formats.get(fmt, 0) + 1

        # 获取最常用的表情包（前10）
        top_used = Emoji.select().order_by(Emoji.usage_count.desc()).limit(10)
        top_used_list = [
            {
                "id": emoji.id,
                "emoji_hash": emoji.emoji_hash,
                "description": emoji.description,
                "usage_count": emoji.usage_count,
            }
            for emoji in top_used
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
async def register_emoji(emoji_id: int, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)):
    """
    注册表情包（快捷操作）

    Args:
        emoji_id: 表情包ID
        authorization: Authorization header

    Returns:
        更新结果
    """
    try:
        verify_auth_token(maibot_session, authorization)

        emoji = Emoji.get_or_none(Emoji.id == emoji_id)

        if not emoji:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")

        if emoji.is_registered:
            raise HTTPException(status_code=400, detail="该表情包已经注册")

        # 注册表情包（如果已封禁，自动解除封禁）
        emoji.is_registered = True
        emoji.is_banned = False  # 注册时自动解除封禁
        emoji.register_time = time.time()
        emoji.save()

        logger.info(f"表情包已注册: ID={emoji_id}")

        return EmojiUpdateResponse(success=True, message="表情包注册成功", data=emoji_to_response(emoji))

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"注册表情包失败: {e}")
        raise HTTPException(status_code=500, detail=f"注册表情包失败: {str(e)}") from e


@router.post("/{emoji_id}/ban", response_model=EmojiUpdateResponse)
async def ban_emoji(emoji_id: int, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)):
    """
    禁用表情包（快捷操作）

    Args:
        emoji_id: 表情包ID
        authorization: Authorization header

    Returns:
        更新结果
    """
    try:
        verify_auth_token(maibot_session, authorization)

        emoji = Emoji.get_or_none(Emoji.id == emoji_id)

        if not emoji:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")

        # 禁用表情包（同时取消注册）
        emoji.is_banned = True
        emoji.is_registered = False
        emoji.save()

        logger.info(f"表情包已禁用: ID={emoji_id}")

        return EmojiUpdateResponse(success=True, message="表情包禁用成功", data=emoji_to_response(emoji))

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"禁用表情包失败: {e}")
        raise HTTPException(status_code=500, detail=f"禁用表情包失败: {str(e)}") from e


@router.get("/{emoji_id}/thumbnail")
async def get_emoji_thumbnail(
    emoji_id: int,
    token: Optional[str] = Query(None, description="访问令牌"),
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
    original: bool = Query(False, description="是否返回原图"),
):
    """
    获取表情包缩略图（懒加载生成 + 缓存）

    Args:
        emoji_id: 表情包ID
        token: 访问令牌（通过 query parameter，用于向后兼容）
        maibot_session: Cookie 中的 token
        authorization: Authorization header
        original: 是否返回原图（用于详情页查看原图）

    Returns:
        表情包缩略图（WebP 格式）或原图
        
    Features:
        - 懒加载：首次请求时生成缩略图
        - 缓存：后续请求直接返回缓存
        - GIF 支持：提取第一帧作为缩略图
        - 格式统一：所有缩略图统一为 WebP 格式
    """
    try:
        token_manager = get_token_manager()
        is_valid = False
        
        # 1. 优先使用 Cookie
        if maibot_session and token_manager.verify_token(maibot_session):
            is_valid = True
        # 2. 其次使用 query parameter（用于向后兼容 img 标签）
        elif token and token_manager.verify_token(token):
            is_valid = True
        # 3. 最后使用 Authorization header
        elif authorization and authorization.startswith("Bearer "):
            auth_token = authorization.replace("Bearer ", "")
            if token_manager.verify_token(auth_token):
                is_valid = True
        
        if not is_valid:
            raise HTTPException(status_code=401, detail="Token 无效或已过期")

        emoji = Emoji.get_or_none(Emoji.id == emoji_id)

        if not emoji:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")

        # 检查文件是否存在
        if not os.path.exists(emoji.full_path):
            raise HTTPException(status_code=404, detail="表情包文件不存在")

        # 如果请求原图，直接返回原文件
        if original:
            mime_types = {
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "gif": "image/gif",
                "webp": "image/webp",
                "bmp": "image/bmp",
            }
            media_type = mime_types.get(emoji.format.lower(), "application/octet-stream")
            return FileResponse(
                path=emoji.full_path, 
                media_type=media_type, 
                filename=f"{emoji.emoji_hash}.{emoji.format}"
            )

        # 尝试获取或生成缩略图
        cache_path = _get_thumbnail_cache_path(emoji.emoji_hash)
        
        # 检查缓存是否存在
        if cache_path.exists():
            # 缓存命中，直接返回
            return FileResponse(
                path=str(cache_path), 
                media_type="image/webp", 
                filename=f"{emoji.emoji_hash}_thumb.webp"
            )
        
        # 缓存未命中，触发后台生成并返回 202
        with _generating_lock:
            if emoji.emoji_hash not in _generating_thumbnails:
                # 标记为正在生成
                _generating_thumbnails.add(emoji.emoji_hash)
                # 提交到线程池后台生成
                _thumbnail_executor.submit(
                    _background_generate_thumbnail,
                    emoji.full_path,
                    emoji.emoji_hash
                )
        
        # 返回 202 Accepted，告诉前端缩略图正在生成中
        return JSONResponse(
            status_code=202,
            content={
                "status": "generating",
                "message": "缩略图正在生成中，请稍后重试",
                "emoji_id": emoji_id,
            },
            headers={
                "Retry-After": "1",  # 建议 1 秒后重试
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取表情包缩略图失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取表情包缩略图失败: {str(e)}") from e


@router.post("/batch/delete", response_model=BatchDeleteResponse)
async def batch_delete_emojis(request: BatchDeleteRequest, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)):
    """
    批量删除表情包

    Args:
        request: 包含emoji_ids列表的请求
        authorization: Authorization header

    Returns:
        批量删除结果
    """
    try:
        verify_auth_token(maibot_session, authorization)

        if not request.emoji_ids:
            raise HTTPException(status_code=400, detail="未提供要删除的表情包ID")

        deleted_count = 0
        failed_count = 0
        failed_ids = []

        for emoji_id in request.emoji_ids:
            try:
                emoji = Emoji.get_or_none(Emoji.id == emoji_id)
                if emoji:
                    emoji.delete_instance()
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


# 表情包存储目录
EMOJI_REGISTERED_DIR = os.path.join("data", "emoji_registed")


class EmojiUploadResponse(BaseModel):
    """表情包上传响应"""

    success: bool
    message: str
    data: Optional[EmojiResponse] = None


@router.post("/upload", response_model=EmojiUploadResponse)
async def upload_emoji(
    file: EmojiFile,
    description: DescriptionForm = "",
    emotion: EmotionForm = "",
    is_registered: IsRegisteredForm = True,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    上传并注册表情包

    Args:
        file: 表情包图片文件 (支持 jpg, jpeg, png, gif, webp)
        description: 表情包描述
        emotion: 情感标签，多个用逗号分隔
        is_registered: 是否直接注册，默认为 True
        authorization: Authorization header

    Returns:
        上传结果和表情包信息
    """
    try:
        verify_auth_token(maibot_session, authorization)

        # 验证文件类型
        if not file.content_type:
            raise HTTPException(status_code=400, detail="无法识别文件类型")

        allowed_types = ["image/jpeg", "image/png", "image/gif", "image/webp"]
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型: {file.content_type}，支持: {', '.join(allowed_types)}",
            )

        # 读取文件内容
        file_content = await file.read()

        if not file_content:
            raise HTTPException(status_code=400, detail="文件内容为空")

        # 验证图片并获取格式
        try:
            with Image.open(io.BytesIO(file_content)) as img:
                img_format = img.format.lower() if img.format else "png"
                # 验证图片可以正常打开
                img.verify()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"无效的图片文件: {str(e)}") from e

        # 重新打开图片（verify后需要重新打开）
        with Image.open(io.BytesIO(file_content)) as img:
            img_format = img.format.lower() if img.format else "png"

        # 计算文件哈希
        emoji_hash = hashlib.md5(file_content).hexdigest()

        # 检查是否已存在相同哈希的表情包
        existing_emoji = Emoji.get_or_none(Emoji.emoji_hash == emoji_hash)
        if existing_emoji:
            raise HTTPException(
                status_code=409,
                detail=f"已存在相同的表情包 (ID: {existing_emoji.id})",
            )

        # 确保目录存在
        os.makedirs(EMOJI_REGISTERED_DIR, exist_ok=True)

        # 生成文件名
        timestamp = int(time.time())
        filename = f"emoji_{timestamp}_{emoji_hash[:8]}.{img_format}"
        full_path = os.path.join(EMOJI_REGISTERED_DIR, filename)

        # 如果文件已存在，添加随机后缀
        counter = 1
        while os.path.exists(full_path):
            filename = f"emoji_{timestamp}_{emoji_hash[:8]}_{counter}.{img_format}"
            full_path = os.path.join(EMOJI_REGISTERED_DIR, filename)
            counter += 1

        # 保存文件
        with open(full_path, "wb") as f:
            f.write(file_content)

        logger.info(f"表情包文件已保存: {full_path}")

        # 处理情感标签
        emotion_str = ",".join(e.strip() for e in emotion.split(",") if e.strip()) if emotion else ""

        # 创建数据库记录
        current_time = time.time()
        emoji = Emoji.create(
            full_path=full_path,
            format=img_format,
            emoji_hash=emoji_hash,
            description=description,
            emotion=emotion_str,
            query_count=0,
            is_registered=is_registered,
            is_banned=False,
            record_time=current_time,
            register_time=current_time if is_registered else None,
            usage_count=0,
            last_used_time=None,
        )

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
    authorization: Optional[str] = Header(None),
):
    """
    批量上传表情包

    Args:
        files: 多个表情包图片文件
        emotion: 共用的情感标签
        is_registered: 是否直接注册
        authorization: Authorization header

    Returns:
        批量上传结果
    """
    try:
        verify_auth_token(maibot_session, authorization)

        results = {
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
                # 验证文件类型
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

                # 读取文件内容
                file_content = await file.read()

                if not file_content:
                    results["failed"] += 1
                    results["details"].append(
                        {
                            "filename": file.filename,
                            "success": False,
                            "error": "文件内容为空",
                        }
                    )
                    continue

                # 验证图片
                try:
                    with Image.open(io.BytesIO(file_content)) as img:
                        img_format = img.format.lower() if img.format else "png"
                except Exception as e:
                    results["failed"] += 1
                    results["details"].append(
                        {
                            "filename": file.filename,
                            "success": False,
                            "error": f"无效的图片: {str(e)}",
                        }
                    )
                    continue

                # 计算哈希
                emoji_hash = hashlib.md5(file_content).hexdigest()

                # 检查重复
                if Emoji.get_or_none(Emoji.emoji_hash == emoji_hash):
                    results["failed"] += 1
                    results["details"].append(
                        {
                            "filename": file.filename,
                            "success": False,
                            "error": "已存在相同的表情包",
                        }
                    )
                    continue

                # 生成文件名并保存
                timestamp = int(time.time())
                filename = f"emoji_{timestamp}_{emoji_hash[:8]}.{img_format}"
                full_path = os.path.join(EMOJI_REGISTERED_DIR, filename)

                counter = 1
                while os.path.exists(full_path):
                    filename = f"emoji_{timestamp}_{emoji_hash[:8]}_{counter}.{img_format}"
                    full_path = os.path.join(EMOJI_REGISTERED_DIR, filename)
                    counter += 1

                with open(full_path, "wb") as f:
                    f.write(file_content)

                # 处理情感标签
                emotion_str = ",".join(e.strip() for e in emotion.split(",") if e.strip()) if emotion else ""

                # 创建数据库记录
                current_time = time.time()
                emoji = Emoji.create(
                    full_path=full_path,
                    format=img_format,
                    emoji_hash=emoji_hash,
                    description="",  # 批量上传暂不设置描述
                    emotion=emotion_str,
                    query_count=0,
                    is_registered=is_registered,
                    is_banned=False,
                    record_time=current_time,
                    register_time=current_time if is_registered else None,
                    usage_count=0,
                    last_used_time=None,
                )

                results["uploaded"] += 1
                results["details"].append(
                    {
                        "filename": file.filename,
                        "success": True,
                        "id": emoji.id,
                    }
                )

            except Exception as e:
                results["failed"] += 1
                results["details"].append(
                    {
                        "filename": file.filename,
                        "success": False,
                        "error": str(e),
                    }
                )

        results["message"] = f"成功上传 {results['uploaded']} 个，失败 {results['failed']} 个"
        return results

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"批量上传表情包失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量上传失败: {str(e)}") from e


# ==================== 缩略图缓存管理 API ====================


class ThumbnailCacheStatsResponse(BaseModel):
    """缩略图缓存统计响应"""
    
    success: bool
    cache_dir: str
    total_count: int
    total_size_mb: float
    emoji_count: int
    coverage_percent: float


class ThumbnailCleanupResponse(BaseModel):
    """缩略图清理响应"""
    
    success: bool
    message: str
    cleaned_count: int
    kept_count: int


class ThumbnailPreheatResponse(BaseModel):
    """缩略图预热响应"""
    
    success: bool
    message: str
    generated_count: int
    skipped_count: int
    failed_count: int


@router.get("/thumbnail-cache/stats", response_model=ThumbnailCacheStatsResponse)
async def get_thumbnail_cache_stats(
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    获取缩略图缓存统计信息
    
    Returns:
        缓存目录、缓存数量、总大小、覆盖率等统计信息
    """
    try:
        verify_auth_token(maibot_session, authorization)
        
        _ensure_thumbnail_cache_dir()
        
        # 统计缓存文件
        cache_files = list(THUMBNAIL_CACHE_DIR.glob("*.webp"))
        total_count = len(cache_files)
        total_size = sum(f.stat().st_size for f in cache_files)
        total_size_mb = round(total_size / (1024 * 1024), 2)
        
        # 统计表情包总数
        emoji_count = Emoji.select().count()
        
        # 计算覆盖率
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
async def cleanup_thumbnail_cache(
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    清理孤立的缩略图缓存（原图已删除的表情包对应的缩略图）
    
    Returns:
        清理结果
    """
    try:
        verify_auth_token(maibot_session, authorization)
        
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
    authorization: Optional[str] = Header(None),
):
    """
    预热缩略图缓存（提前生成未缓存的缩略图）
    
    优先处理使用次数高的表情包
    
    Args:
        limit: 最多预热数量 (1-1000)
        
    Returns:
        预热结果
    """
    try:
        verify_auth_token(maibot_session, authorization)
        
        _ensure_thumbnail_cache_dir()
        
        # 获取使用次数最高的表情包（未缓存的优先）
        emojis = (
            Emoji.select()
            .where(Emoji.is_banned == False)  # noqa: E712  Peewee ORM requires == for boolean comparison
            .order_by(Emoji.usage_count.desc())
            .limit(limit * 2)  # 多查一些，因为有些可能已缓存
        )
        
        generated = 0
        skipped = 0
        failed = 0
        
        for emoji in emojis:
            if generated >= limit:
                break
                
            cache_path = _get_thumbnail_cache_path(emoji.emoji_hash)
            
            # 已缓存，跳过
            if cache_path.exists():
                skipped += 1
                continue
            
            # 原文件不存在，跳过
            if not os.path.exists(emoji.full_path):
                failed += 1
                continue
            
            try:
                # 使用线程池异步生成缩略图，避免阻塞事件循环
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    _thumbnail_executor,
                    _generate_thumbnail,
                    emoji.full_path,
                    emoji.emoji_hash
                )
                generated += 1
            except Exception as e:
                logger.warning(f"预热缩略图失败 {emoji.emoji_hash}: {e}")
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
async def clear_all_thumbnail_cache(
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """
    清空所有缩略图缓存（下次访问时会重新生成）
    
    Returns:
        清理结果
    """
    try:
        verify_auth_token(maibot_session, authorization)
        
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
