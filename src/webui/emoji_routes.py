"""表情包管理 API 路由"""

from fastapi import APIRouter, HTTPException, Header, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from src.common.logger import get_logger
from src.common.database.database_model import Emoji
from .token_manager import get_token_manager
import json
import time
import os

logger = get_logger("webui.emoji")

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


def verify_auth_token(authorization: Optional[str]) -> bool:
    """验证认证 Token"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供有效的认证信息")

    token = authorization.replace("Bearer ", "")
    token_manager = get_token_manager()

    if not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    return True


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
        authorization: Authorization header

    Returns:
        表情包列表
    """
    try:
        verify_auth_token(authorization)

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

        # 排序：使用次数倒序，然后按记录时间倒序
        from peewee import Case

        query = query.order_by(
            Emoji.usage_count.desc(), Case(None, [(Emoji.record_time.is_null(), 1)], 0), Emoji.record_time.desc()
        )

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
async def get_emoji_detail(emoji_id: int, authorization: Optional[str] = Header(None)):
    """
    获取表情包详细信息

    Args:
        emoji_id: 表情包ID
        authorization: Authorization header

    Returns:
        表情包详细信息
    """
    try:
        verify_auth_token(authorization)

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
async def update_emoji(emoji_id: int, request: EmojiUpdateRequest, authorization: Optional[str] = Header(None)):
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
        verify_auth_token(authorization)

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
async def delete_emoji(emoji_id: int, authorization: Optional[str] = Header(None)):
    """
    删除表情包

    Args:
        emoji_id: 表情包ID
        authorization: Authorization header

    Returns:
        删除结果
    """
    try:
        verify_auth_token(authorization)

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
async def get_emoji_stats(authorization: Optional[str] = Header(None)):
    """
    获取表情包统计数据

    Args:
        authorization: Authorization header

    Returns:
        统计数据
    """
    try:
        verify_auth_token(authorization)

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
async def register_emoji(emoji_id: int, authorization: Optional[str] = Header(None)):
    """
    注册表情包（快捷操作）

    Args:
        emoji_id: 表情包ID
        authorization: Authorization header

    Returns:
        更新结果
    """
    try:
        verify_auth_token(authorization)

        emoji = Emoji.get_or_none(Emoji.id == emoji_id)

        if not emoji:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")

        if emoji.is_registered:
            raise HTTPException(status_code=400, detail="该表情包已经注册")

        if emoji.is_banned:
            raise HTTPException(status_code=400, detail="该表情包已被禁用，无法注册")

        # 注册表情包
        emoji.is_registered = True
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
async def ban_emoji(emoji_id: int, authorization: Optional[str] = Header(None)):
    """
    禁用表情包（快捷操作）

    Args:
        emoji_id: 表情包ID
        authorization: Authorization header

    Returns:
        更新结果
    """
    try:
        verify_auth_token(authorization)

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
    authorization: Optional[str] = Header(None),
):
    """
    获取表情包缩略图

    Args:
        emoji_id: 表情包ID
        token: 访问令牌（通过 query parameter）
        authorization: Authorization header

    Returns:
        表情包图片文件
    """
    try:
        # 优先使用 query parameter 中的 token（用于 img 标签）
        if token:
            token_manager = get_token_manager()
            if not token_manager.verify_token(token):
                raise HTTPException(status_code=401, detail="Token 无效或已过期")
        else:
            # 如果没有 query token，则验证 Authorization header
            verify_auth_token(authorization)

        emoji = Emoji.get_or_none(Emoji.id == emoji_id)

        if not emoji:
            raise HTTPException(status_code=404, detail=f"未找到 ID 为 {emoji_id} 的表情包")

        # 检查文件是否存在
        if not os.path.exists(emoji.full_path):
            raise HTTPException(status_code=404, detail="表情包文件不存在")

        # 根据格式设置 MIME 类型
        mime_types = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
            "bmp": "image/bmp",
        }

        media_type = mime_types.get(emoji.format.lower(), "application/octet-stream")

        return FileResponse(path=emoji.full_path, media_type=media_type, filename=f"{emoji.emoji_hash}.{emoji.format}")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取表情包缩略图失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取表情包缩略图失败: {str(e)}") from e


@router.post("/batch/delete", response_model=BatchDeleteResponse)
async def batch_delete_emojis(request: BatchDeleteRequest, authorization: Optional[str] = Header(None)):
    """
    批量删除表情包

    Args:
        request: 包含emoji_ids列表的请求
        authorization: Authorization header

    Returns:
        批量删除结果
    """
    try:
        verify_auth_token(authorization)

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
