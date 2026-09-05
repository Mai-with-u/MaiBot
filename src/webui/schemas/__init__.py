"""WebUI Schemas - Pydantic models for API requests and responses."""

# Auth schemas
from .auth import (
    CompleteSetupResponse,
    FirstSetupStatusResponse,
    ResetSetupResponse,
    TokenRegenerateResponse,
    TokenUpdateRequest,
    TokenUpdateResponse,
    TokenVerifyRequest,
    TokenVerifyResponse,
)

# Emoji schemas
from .emoji import (
    BatchDeleteRequest,
    BatchDeleteResponse,
    EmojiDeleteResponse,
    EmojiDetailResponse,
    EmojiListResponse,
    EmojiResponse,
    EmojiUpdateRequest,
    EmojiUpdateResponse,
    EmojiUploadResponse,
    ThumbnailCacheStatsResponse,
    ThumbnailCleanupResponse,
    ThumbnailPreheatResponse,
)

# Plugin schemas
from .plugin import (
    AddMirrorRequest,
    AvailableMirrorsResponse,
    CloneRepositoryRequest,
    CloneRepositoryResponse,
    FetchRawFileRequest,
    FetchRawFileResponse,
    GitStatusResponse,
    InstallPluginRequest,
    MirrorConfigResponse,
    UninstallPluginRequest,
    UpdateMirrorRequest,
    UpdatePluginConfigRequest,
    UpdatePluginRequest,
    VersionResponse,
)

# Statistics schemas
from .statistics import (
    DashboardData,
    ModelStatistics,
    StatisticsSummary,
    TimeSeriesData,
)

__all__ = [
    # Auth
    "TokenVerifyRequest",
    "TokenVerifyResponse",
    "TokenUpdateRequest",
    "TokenUpdateResponse",
    "TokenRegenerateResponse",
    "FirstSetupStatusResponse",
    "CompleteSetupResponse",
    "ResetSetupResponse",
    # Statistics
    "StatisticsSummary",
    "ModelStatistics",
    "TimeSeriesData",
    "DashboardData",
    # Emoji
    "EmojiResponse",
    "EmojiListResponse",
    "EmojiDetailResponse",
    "EmojiUpdateRequest",
    "EmojiUpdateResponse",
    "EmojiDeleteResponse",
    "BatchDeleteRequest",
    "BatchDeleteResponse",
    "EmojiUploadResponse",
    "ThumbnailCacheStatsResponse",
    "ThumbnailCleanupResponse",
    "ThumbnailPreheatResponse",
    # Plugin
    "FetchRawFileRequest",
    "FetchRawFileResponse",
    "CloneRepositoryRequest",
    "CloneRepositoryResponse",
    "MirrorConfigResponse",
    "AvailableMirrorsResponse",
    "AddMirrorRequest",
    "UpdateMirrorRequest",
    "GitStatusResponse",
    "InstallPluginRequest",
    "VersionResponse",
    "UninstallPluginRequest",
    "UpdatePluginRequest",
    "UpdatePluginConfigRequest",
]
