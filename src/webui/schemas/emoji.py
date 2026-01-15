from pydantic import BaseModel
from typing import Optional, List


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
    emotion: Optional[str]
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
