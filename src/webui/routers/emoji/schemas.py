from pathlib import Path
from typing import Annotated, List, Optional

from fastapi import File, Form, UploadFile
from pydantic import BaseModel

from src.common.database.database_model import Images

EmojiFile = Annotated[UploadFile, File(description="表情包图片文件")]
EmojiFiles = Annotated[List[UploadFile], File(description="多个表情包图片文件")]
DescriptionForm = Annotated[str, Form(description="表情包描述")]
EmotionForm = Annotated[str, Form(description="情感标签，多个用逗号分隔")]
IsRegisteredForm = Annotated[bool, Form(description="是否直接注册")]


class EmojiResponse(BaseModel):
    """表情包响应"""

    id: int
    full_path: str
    format: str
    emoji_hash: str
    description: str
    usage_count: int
    query_count: int
    is_registered: bool
    is_banned: bool
    emotion: Optional[str]
    record_time: float
    register_time: Optional[float]
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


class EmojiUploadResponse(BaseModel):
    """表情包上传响应"""

    success: bool
    message: str
    data: Optional[EmojiResponse] = None


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


def emoji_to_response(image: Images) -> EmojiResponse:
    """将数据库表情包模型转换为响应对象。"""
    return EmojiResponse(
        id=image.id if image.id is not None else 0,
        full_path=image.full_path,
        format=Path(image.full_path).suffix.lower().lstrip(".") or "unknown",
        emoji_hash=image.image_hash,
        description=image.description,
        usage_count=image.query_count,
        query_count=image.query_count,
        is_registered=image.is_registered,
        is_banned=image.is_banned,
        emotion=image.emotion,
        record_time=image.record_time.timestamp() if image.record_time else 0.0,
        register_time=image.register_time.timestamp() if image.register_time else None,
        last_used_time=image.last_used_time.timestamp() if image.last_used_time else None,
    )
