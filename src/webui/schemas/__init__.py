"""WebUI Schemas - Pydantic models for API requests and responses."""

# Auth schemas
from .auth import (
    TokenVerifyRequest,
    TokenVerifyResponse,
    TokenUpdateRequest,
    TokenUpdateResponse,
    TokenRegenerateResponse,
    FirstSetupStatusResponse,
    CompleteSetupResponse,
    ResetSetupResponse,
)

# Statistics schemas
from .statistics import (
    StatisticsSummary,
    ModelStatistics,
    TimeSeriesData,
    DashboardData,
)

# Emoji schemas
from .emoji import (
    EmojiResponse,
    EmojiListResponse,
    EmojiDetailResponse,
    EmojiUpdateRequest,
    EmojiUpdateResponse,
    EmojiDeleteResponse,
    BatchDeleteRequest,
    BatchDeleteResponse,
    EmojiUploadResponse,
    ThumbnailCacheStatsResponse,
    ThumbnailCleanupResponse,
    ThumbnailPreheatResponse,
)

# Chat schemas
from .chat import (
    VirtualIdentityConfig,
    ChatHistoryMessage,
)

# Plugin schemas
from .plugin import (
    FetchRawFileRequest,
    FetchRawFileResponse,
    CloneRepositoryRequest,
    CloneRepositoryResponse,
    MirrorConfigResponse,
    AvailableMirrorsResponse,
    AddMirrorRequest,
    UpdateMirrorRequest,
    GitStatusResponse,
    InstallPluginRequest,
    VersionResponse,
    UninstallPluginRequest,
    UpdatePluginRequest,
    UpdatePluginConfigRequest,
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
    # Chat
    "VirtualIdentityConfig",
    "ChatHistoryMessage",
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
