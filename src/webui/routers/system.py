"""
系统控制路由

提供系统重启、状态查询等功能
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, inspect, text
from sqlmodel import col, select

import asyncio
import mimetypes
import os
import sqlite3
import time

from src.common.database.database import engine, get_db_session
from src.common.database.database_model import Images, ImageType
from src.common.logger import get_logger
from src.common.update_notice import get_pending_update_notice, mark_update_notice_seen
from src.common.utils.image_path import (
    StoredImagePathError,
    resolve_stored_image_path,
    stored_image_paths_equal,
)
from src.config.config import MMC_VERSION
from src.webui.dependencies import require_auth

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_auth)])
logger = get_logger("webui_system")

_start_time = time.time()
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = _PROJECT_ROOT / "data"
_IMAGE_DIR = _DATA_DIR / "images"
_EMOJI_DIR = _DATA_DIR / "emoji"
_EMOJI_THUMBNAIL_DIR = _DATA_DIR / "emoji_thumbnails"
_LOG_DIR = _PROJECT_ROOT / "logs"
_DATABASE_FILE = _DATA_DIR / "MaiBot.db"
_DATABASE_AUXILIARY_SUFFIXES = ("-wal", "-shm")
_RESTART_EXIT_CODE = 42
_LOCAL_CACHE_STATS_CACHE_TTL_SECONDS = 120
_SQLITE_BUSY_TIMEOUT_SECONDS = 30
_CACHE_IMAGE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
}
_DATABASE_CLEANUP_TABLES: dict[str, dict[str, str]] = {
    "llm_usage": {
        "label": "LLM 调用记录",
        "category": "调用与统计",
        "description": "模型调用、Token、耗时和费用记录。",
        "date_column": "timestamp",
    },
    "tool_records": {
        "label": "工具调用记录",
        "category": "调用与统计",
        "description": "内置工具和插件工具的调用过程记录。",
        "date_column": "timestamp",
    },
    "mai_messages": {
        "label": "消息记录",
        "category": "聊天历史",
        "description": "收到的聊天消息与消息元数据。",
        "date_column": "timestamp",
    },
    "chat_history": {
        "label": "聊天摘要历史",
        "category": "聊天历史",
        "description": "聊天片段摘要、主题和关键词记录。",
        "date_column": "end_timestamp",
    },
    "online_time": {
        "label": "在线时长记录",
        "category": "运行统计",
        "description": "运行在线时长统计记录。",
        "date_column": "timestamp",
    },
    "statistics_message_hourly": {
        "label": "消息小时统计",
        "category": "统计缓存",
        "description": "按小时聚合的消息统计缓存。",
        "date_column": "bucket_time",
    },
    "statistics_tool_hourly": {
        "label": "工具小时统计",
        "category": "统计缓存",
        "description": "按小时聚合的工具调用统计缓存。",
        "date_column": "bucket_time",
    },
    "statistics_model_hourly": {
        "label": "模型小时统计",
        "category": "统计缓存",
        "description": "按小时聚合的模型调用统计缓存。",
        "date_column": "bucket_time",
    },
}
_PROTECTED_DATA_PATHS = {
    _DATABASE_FILE.resolve(),
    *[Path(f"{_DATABASE_FILE}{suffix}").resolve() for suffix in _DATABASE_AUXILIARY_SUFFIXES],
}
_SPECIAL_CACHE_DIRS = (
    _IMAGE_DIR.resolve(),
    _EMOJI_DIR.resolve(),
    _EMOJI_THUMBNAIL_DIR.resolve(),
)
_restart_task: asyncio.Task[None] | None = None

CacheImageTarget = Literal["images", "emoji"]
DatabaseCleanupMode = Literal["all", "older_than_days"]
MonitorMediaKind = Literal["image", "emoji"]


class RestartResponse(BaseModel):
    """重启响应"""

    success: bool
    message: str


class StatusResponse(BaseModel):
    """状态响应"""

    running: bool
    uptime: float
    version: str
    start_time: str


class UpdateNoticeResponse(BaseModel):
    """更新公告响应。"""

    pending: bool
    current_version: str
    from_version: str | None = None
    versions: list[str] = Field(default_factory=list)
    content: str = ""


class UpdateNoticeAckResponse(BaseModel):
    """更新公告确认响应。"""

    success: bool
    message: str
    version: str


class CacheDirectoryStats(BaseModel):
    """本地缓存目录统计。"""

    key: str
    label: str
    path: str
    exists: bool
    file_count: int
    total_size: int
    db_records: int = 0


class DatabaseFileStats(BaseModel):
    """数据库文件统计。"""

    path: str
    exists: bool
    size: int


class DatabaseTableStats(BaseModel):
    """数据库表统计。"""

    name: str
    rows: int
    size: int = 0
    size_source: Literal["dbstat", "estimated"] = "estimated"
    label: str = ""
    category: str = "其他"
    description: str = ""
    cleanup_supported: bool = False
    cleanup_date_column: str | None = None


class DatabaseStorageStats(BaseModel):
    """数据库存储统计。"""

    files: list[DatabaseFileStats]
    tables: list[DatabaseTableStats]
    total_size: int
    page_size: int = 0
    page_count: int = 0
    freelist_count: int = 0
    free_size: int = 0


class LocalCacheStatsResponse(BaseModel):
    """本地缓存统计响应。"""

    directories: list[CacheDirectoryStats]
    database: DatabaseStorageStats


_local_cache_stats_cache: tuple[float, LocalCacheStatsResponse] | None = None
_database_stats_cache: tuple[float, DatabaseStorageStats] | None = None


class LocalCacheImageItem(BaseModel):
    """本地缓存图片文件条目。"""

    relative_path: str
    file_name: str
    full_path: str
    size: int
    modified_time: float
    format: str
    db_id: int | None = None
    image_hash: str | None = None
    description: str = ""
    is_registered: bool | None = None
    is_banned: bool | None = None
    no_file_flag: bool | None = None


class LocalCacheImageDateGroup(BaseModel):
    """本地缓存图片日期分组。"""

    date: str
    file_count: int
    total_size: int


class LocalCacheImageListResponse(BaseModel):
    """本地缓存图片列表响应。"""

    success: bool
    target: CacheImageTarget
    total: int
    page: int
    page_size: int
    total_size: int
    data: list[LocalCacheImageItem]
    date_groups: list[LocalCacheImageDateGroup] = Field(default_factory=list)


class LocalCacheLogDirectoryItem(BaseModel):
    """本地日志目录条目。"""

    relative_path: str
    name: str
    full_path: str
    depth: int
    file_count: int
    total_size: int
    modified_time: float
    root_files_only: bool = False


class LocalCacheLogDirectoryListResponse(BaseModel):
    """本地日志目录列表响应。"""

    success: bool
    total: int
    data: list[LocalCacheLogDirectoryItem]


class LocalCacheDataEntry(BaseModel):
    """data 目录中的文件或文件夹条目。"""

    relative_path: str
    name: str
    full_path: str
    kind: Literal["file", "directory"]
    file_count: int
    total_size: int
    modified_time: float
    protected: bool = False
    protection_reason: str | None = None


class LocalCacheDataEntriesResponse(BaseModel):
    """data 目录浏览响应。"""

    success: bool
    root_path: str
    relative_path: str
    current_path: str
    parent_path: str | None = None
    file_count: int
    total_size: int
    total: int
    data: list[LocalCacheDataEntry]


class LocalCacheCleanupRequest(BaseModel):
    """本地缓存清理请求。"""

    target: Literal["images", "emoji", "log_files", "database_logs"]
    tables: list[str] = Field(default_factory=list)
    database_mode: DatabaseCleanupMode = "all"
    older_than_days: int | None = Field(default=None, ge=1)
    vacuum_after_cleanup: bool = True


class LocalCacheCleanupResponse(BaseModel):
    """本地缓存清理响应。"""

    success: bool
    message: str
    target: str
    removed_files: int = 0
    removed_bytes: int = 0
    removed_records: int = 0
    vacuumed: bool = False
    database_size_before: int | None = None
    database_size_after: int | None = None
    reclaimed_bytes: int = 0


class LocalCacheDatabaseVacuumResponse(BaseModel):
    """数据库 VACUUM 维护响应。"""

    success: bool
    message: str
    database_size_before: int
    database_size_after: int
    reclaimed_bytes: int
    checkpoint_busy: int = 0
    checkpoint_log: int = 0
    checkpointed: int = 0


class LocalCacheImageDeleteRequest(BaseModel):
    """本地缓存单张图片删除请求。"""

    target: CacheImageTarget
    relative_path: str


class LocalCacheImageBulkDeleteRequest(BaseModel):
    """本地缓存图片批量删除请求。"""

    target: CacheImageTarget
    mode: Literal["date_range", "older_than_recent_days"]
    start_date: str | None = None
    end_date: str | None = None
    keep_recent_days: Literal[1, 7, 30] | None = None


class LocalCacheLogDirectoryDeleteRequest(BaseModel):
    """本地日志目录清理请求。"""

    relative_path: str


class LocalCacheDataEntryDeleteRequest(BaseModel):
    """data 目录条目删除请求。"""

    relative_path: str


def _iter_files(directory: Path) -> list[Path]:
    if not directory.exists() or not directory.is_dir():
        return []
    return [path for path in directory.rglob("*") if path.is_file()]


def _get_path_summary(path: Path) -> tuple[int, int, float]:
    if not path.exists():
        return 0, 0, 0.0
    if path.is_file():
        try:
            file_stat = path.stat()
        except OSError:
            logger.warning(f"读取文件信息失败: {path}")
            return 0, 0, 0.0
        return 1, file_stat.st_size, file_stat.st_mtime

    file_count = 0
    total_size = 0
    modified_time = 0.0
    for file_path in _iter_files(path):
        try:
            file_stat = file_path.stat()
        except OSError:
            logger.warning(f"读取目录文件信息失败: {file_path}")
            continue
        file_count += 1
        total_size += file_stat.st_size
        modified_time = max(modified_time, file_stat.st_mtime)
    return file_count, total_size, modified_time


def _is_cache_image_file(path: Path) -> bool:
    return path.suffix.lower() in _CACHE_IMAGE_EXTENSIONS


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _resolve_data_path(relative_path: str = "") -> Path:
    root_path = _DATA_DIR.resolve()
    try:
        target_path = (root_path / relative_path).resolve()
        target_path.relative_to(root_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="无效的 data 路径") from exc
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="未找到指定 data 条目")
    return target_path


def _get_data_path_protection_reason(path: Path) -> str | None:
    resolved_path = path.resolve()
    if resolved_path == _DATA_DIR.resolve():
        return "data 根目录不能删除"
    if resolved_path in _PROTECTED_DATA_PATHS:
        return "当前正在使用的主数据库文件不能在这里删除"
    for cache_dir in _SPECIAL_CACHE_DIRS:
        if _is_relative_to(resolved_path, cache_dir):
            return "图片、表情和缩略图缓存请使用上方专用清理入口，以同步数据库记录"
    return None


def _build_data_entry(path: Path, root_path: Path) -> LocalCacheDataEntry:
    resolved_path = path.resolve()
    file_count, total_size, modified_time = _get_path_summary(path)
    protection_reason = _get_data_path_protection_reason(resolved_path)
    return LocalCacheDataEntry(
        relative_path=resolved_path.relative_to(root_path).as_posix(),
        name=path.name,
        full_path=str(resolved_path),
        kind="directory" if path.is_dir() else "file",
        file_count=file_count,
        total_size=total_size,
        modified_time=modified_time,
        protected=protection_reason is not None,
        protection_reason=protection_reason,
    )


def _build_data_entries_response(relative_path: str) -> LocalCacheDataEntriesResponse:
    root_path = _DATA_DIR.resolve()
    current_path = _resolve_data_path(relative_path)
    if not current_path.is_dir():
        raise HTTPException(status_code=400, detail="只能浏览 data 目录中的文件夹")

    entries: list[LocalCacheDataEntry] = []
    for child in current_path.iterdir():
        try:
            entries.append(_build_data_entry(child, root_path))
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(f"读取 data 条目信息失败: {child}, error={exc}")

    file_count = sum(entry.file_count for entry in entries)
    total_size = sum(entry.total_size for entry in entries)
    current_relative_path = "" if current_path == root_path else current_path.relative_to(root_path).as_posix()
    parent_path = None
    if current_path != root_path:
        parent_path = "" if current_path.parent == root_path else current_path.parent.relative_to(root_path).as_posix()

    return LocalCacheDataEntriesResponse(
        success=True,
        root_path=str(root_path),
        relative_path=current_relative_path,
        current_path=str(current_path),
        parent_path=parent_path,
        file_count=file_count,
        total_size=total_size,
        total=len(entries),
        data=sorted(entries, key=lambda item: (item.kind != "directory", -item.total_size, item.name.lower())),
    )


def _get_cache_image_target(target: CacheImageTarget) -> tuple[Path, ImageType, str]:
    if target == "images":
        return _IMAGE_DIR, ImageType.IMAGE, "图片"
    return _EMOJI_DIR, ImageType.EMOJI, "表情包"


def _resolve_cache_image_file(target: CacheImageTarget, relative_path: str) -> Path:
    root_dir, _, label = _get_cache_image_target(target)
    root_path = root_dir.resolve()

    try:
        file_path = (root_path / relative_path).resolve()
        file_path.relative_to(root_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"无效的{label}路径") from exc

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"未找到指定{label}文件")
    if not _is_cache_image_file(file_path):
        raise HTTPException(status_code=400, detail="只能浏览图片缓存文件")
    return file_path


def _resolve_monitor_media_file(media_kind: MonitorMediaKind, media_hash: str) -> Path:
    """根据监控事件中的媒体 hash 解析原始图片或表情文件。"""

    normalized_hash = media_hash.strip()
    if not normalized_hash:
        raise HTTPException(status_code=400, detail="媒体 hash 不能为空")

    image_type = ImageType.IMAGE if media_kind == "image" else ImageType.EMOJI
    label = "图片" if media_kind == "image" else "表情包"
    with get_db_session(auto_commit=False) as session:
        statement = select(Images).filter_by(image_hash=normalized_hash, image_type=image_type).limit(1)
        image_record = session.exec(statement).first()

    if image_record is None:
        raise HTTPException(status_code=404, detail=f"未找到指定{label}记录")

    try:
        file_path = resolve_stored_image_path(image_record.full_path)
    except (OSError, RuntimeError, StoredImagePathError) as exc:
        raise HTTPException(status_code=404, detail=f"无法解析指定{label}文件") from exc

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"未找到指定{label}文件")
    return file_path


def _paths_equal(left: str, right: Path) -> bool:
    try:
        return stored_image_paths_equal(left, right)
    except (OSError, RuntimeError, StoredImagePathError):
        return False


def _get_image_records_by_path(image_type: ImageType) -> dict[Path, list[Images]]:
    records_by_path: dict[Path, list[Images]] = {}
    with get_db_session(auto_commit=False) as session:
        statement = select(Images).where(col(Images.image_type) == image_type)
        records = session.exec(statement).all()

    for record in records:
        try:
            record_path = resolve_stored_image_path(record.full_path)
        except (OSError, RuntimeError, StoredImagePathError):
            continue
        records_by_path.setdefault(record_path, []).append(record)
    return records_by_path


def _build_cache_image_items(target: CacheImageTarget) -> list[LocalCacheImageItem]:
    root_dir, image_type, _ = _get_cache_image_target(target)
    if not root_dir.exists() or not root_dir.is_dir():
        return []

    root_path = root_dir.resolve()
    records_by_path = _get_image_records_by_path(image_type)
    items: list[LocalCacheImageItem] = []

    for file_path in _iter_files(root_path):
        if not _is_cache_image_file(file_path):
            continue

        try:
            file_stat = file_path.stat()
            resolved_path = file_path.resolve()
            relative_path = resolved_path.relative_to(root_path).as_posix()
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(f"读取缓存图片信息失败: {file_path}, error={exc}")
            continue

        record = next(iter(records_by_path.get(resolved_path, [])), None)
        items.append(
            LocalCacheImageItem(
                relative_path=relative_path,
                file_name=file_path.name,
                full_path=str(resolved_path),
                size=file_stat.st_size,
                modified_time=file_stat.st_mtime,
                format=file_path.suffix.lower().lstrip(".") or "unknown",
                db_id=record.id if record is not None else None,
                image_hash=record.image_hash if record is not None else None,
                description=record.description if record is not None else "",
                is_registered=record.is_registered if record is not None else None,
                is_banned=record.is_banned if record is not None else None,
                no_file_flag=record.no_file_flag if record is not None else None,
            )
        )

    return sorted(items, key=lambda item: item.modified_time, reverse=True)


def _parse_date_filter(value: str | None, field_name: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} 必须是 YYYY-MM-DD 格式") from exc


def _get_cache_image_item_date(item: LocalCacheImageItem) -> str:
    return datetime.fromtimestamp(item.modified_time).date().isoformat()


def _filter_cache_image_items_by_date(
    items: list[LocalCacheImageItem],
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[LocalCacheImageItem]:
    start = _parse_date_filter(start_date, "start_date")
    end = _parse_date_filter(end_date, "end_date")
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")

    filtered_items: list[LocalCacheImageItem] = []
    for item in items:
        item_date = datetime.fromtimestamp(item.modified_time).date()
        if start is not None and item_date < start.date():
            continue
        if end is not None and item_date > end.date():
            continue
        filtered_items.append(item)
    return filtered_items


def _build_cache_image_date_groups(items: list[LocalCacheImageItem]) -> list[LocalCacheImageDateGroup]:
    groups: dict[str, LocalCacheImageDateGroup] = {}
    for item in items:
        date_key = _get_cache_image_item_date(item)
        group = groups.setdefault(date_key, LocalCacheImageDateGroup(date=date_key, file_count=0, total_size=0))
        group.file_count += 1
        group.total_size += item.size
    return sorted(groups.values(), key=lambda group: group.date, reverse=True)


def _get_files_stats(files: list[Path]) -> tuple[int, int, float]:
    total_size = 0
    modified_time = 0.0
    file_count = 0
    for file_path in files:
        try:
            file_stat = file_path.stat()
        except OSError:
            logger.warning(f"读取缓存文件信息失败: {file_path}")
            continue

        file_count += 1
        total_size += file_stat.st_size
        modified_time = max(modified_time, file_stat.st_mtime)
    return file_count, total_size, modified_time


def _build_log_directory_items() -> list[LocalCacheLogDirectoryItem]:
    if not _LOG_DIR.exists() or not _LOG_DIR.is_dir():
        return []

    root_path = _LOG_DIR.resolve()
    items: list[LocalCacheLogDirectoryItem] = []
    root_files = [path for path in _LOG_DIR.iterdir() if path.is_file()]
    root_file_count, root_total_size, root_modified_time = _get_files_stats(root_files)
    if root_file_count > 0:
        items.append(
            LocalCacheLogDirectoryItem(
                relative_path="",
                name="根目录文件",
                full_path=str(root_path),
                depth=0,
                file_count=root_file_count,
                total_size=root_total_size,
                modified_time=root_modified_time,
                root_files_only=True,
            )
        )

    for directory in sorted((path for path in _LOG_DIR.rglob("*") if path.is_dir()), key=lambda path: path.as_posix()):
        try:
            resolved_path = directory.resolve()
            relative_path = resolved_path.relative_to(root_path).as_posix()
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(f"读取日志目录信息失败: {directory}, error={exc}")
            continue

        file_count, total_size, modified_time = _get_files_stats(_iter_files(directory))
        items.append(
            LocalCacheLogDirectoryItem(
                relative_path=relative_path,
                name=directory.name,
                full_path=str(resolved_path),
                depth=len(Path(relative_path).parts),
                file_count=file_count,
                total_size=total_size,
                modified_time=modified_time,
            )
        )

    return sorted(items, key=lambda item: (not item.root_files_only, item.relative_path))


def _resolve_log_directory(relative_path: str) -> Path:
    root_path = _LOG_DIR.resolve()
    try:
        target_path = (root_path / relative_path).resolve()
        target_path.relative_to(root_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="无效的日志目录路径") from exc

    if target_path == root_path:
        return target_path
    if not target_path.is_dir():
        raise HTTPException(status_code=404, detail="未找到指定日志目录")
    return target_path


def _get_directory_size(directory: Path) -> tuple[int, int]:
    file_count, total_size, _ = _get_path_summary(directory)
    return file_count, total_size


def _get_image_record_count(image_type: ImageType) -> int:
    with get_db_session() as session:
        statement = select(func.count()).select_from(Images).where(col(Images.image_type) == image_type)
        return int(session.exec(statement).one())


def _build_directory_stats(
    key: str,
    label: str,
    path: Path,
    image_type: ImageType | None = None,
    extra_paths: tuple[Path, ...] = (),
) -> CacheDirectoryStats:
    file_count, total_size = _get_directory_size(path)
    exists = path.exists()
    for extra_path in extra_paths:
        extra_file_count, extra_total_size = _get_directory_size(extra_path)
        file_count += extra_file_count
        total_size += extra_total_size
        exists = exists or extra_path.exists()

    return CacheDirectoryStats(
        key=key,
        label=label,
        path=str(path),
        exists=exists,
        file_count=file_count,
        total_size=total_size,
        db_records=_get_image_record_count(image_type) if image_type is not None else 0,
    )


def _get_database_files() -> list[DatabaseFileStats]:
    db_paths = [_DATABASE_FILE, *[Path(f"{_DATABASE_FILE}{suffix}") for suffix in _DATABASE_AUXILIARY_SUFFIXES]]
    result: list[DatabaseFileStats] = []
    for db_path in db_paths:
        exists = db_path.exists()
        size = 0
        if exists:
            try:
                size = db_path.stat().st_size
            except OSError:
                logger.warning(f"读取数据库文件大小失败: {db_path}")
        result.append(DatabaseFileStats(path=str(db_path), exists=exists, size=size))
    return result


def _get_database_total_size() -> int:
    return sum(file.size for file in _get_database_files())


def _get_database_page_stats() -> tuple[int, int, int]:
    if not _DATABASE_FILE.exists():
        return 0, 0, 0

    try:
        with sqlite3.connect(_DATABASE_FILE, timeout=_SQLITE_BUSY_TIMEOUT_SECONDS) as connection:
            connection.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_SECONDS * 1000}")
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0] or 0)
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0] or 0)
            freelist_count = int(connection.execute("PRAGMA freelist_count").fetchone()[0] or 0)
    except sqlite3.Error as exc:
        logger.warning(f"读取数据库分页统计失败: {exc}")
        return 0, 0, 0
    return page_size, page_count, freelist_count


def _quote_sqlite_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _get_table_indexes(connection, table_name: str) -> list[str]:
    quoted_table_name = _quote_sqlite_identifier(table_name)
    indexes = []
    for row in connection.execute(text(f"PRAGMA index_list({quoted_table_name})")).fetchall():
        if len(row) > 1 and row[1]:
            indexes.append(str(row[1]))
    return indexes


def _get_dbstat_table_sizes(connection, table_names: list[str]) -> dict[str, int] | None:
    try:
        connection.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS temp.local_cache_dbstat USING dbstat(main)"))
        rows = connection.execute(
            text("SELECT name, SUM(pgsize) AS size FROM temp.local_cache_dbstat GROUP BY name")
        ).fetchall()
    except Exception as exc:
        logger.debug(f"当前 SQLite 环境不支持 dbstat，数据库表大小将使用估算值: {exc}")
        return None

    object_sizes = {str(row[0]): int(row[1] or 0) for row in rows}
    table_sizes: dict[str, int] = {}
    for table_name in table_names:
        table_size = object_sizes.get(table_name, 0)
        for index_name in _get_table_indexes(connection, table_name):
            table_size += object_sizes.get(index_name, 0)
        table_sizes[table_name] = table_size
    return table_sizes


def _estimate_table_data_size(connection, table_name: str, rows: int) -> int:
    if rows <= 0:
        return 0

    inspector = inspect(engine)
    columns = inspector.get_columns(table_name)
    if not columns:
        return 0

    quoted_table_name = _quote_sqlite_identifier(table_name)
    sample_limit = min(rows, 200)
    column_expressions = [
        f"COALESCE(LENGTH(CAST({_quote_sqlite_identifier(column['name'])} AS BLOB)), 0)" for column in columns
    ]
    expression = " + ".join(column_expressions)
    sample_size, sample_rows = connection.execute(
        text(
            f"SELECT COALESCE(SUM({expression}), 0), COUNT(*) "
            f"FROM (SELECT * FROM {quoted_table_name} LIMIT {sample_limit})"
        )
    ).one()
    if int(sample_rows or 0) == 0:
        return 0
    return int(int(sample_size or 0) * rows / int(sample_rows))


def _get_database_table_stats() -> list[DatabaseTableStats]:
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    table_stats: list[DatabaseTableStats] = []
    with engine.connect() as connection:
        dbstat_sizes = _get_dbstat_table_sizes(connection, table_names)
        for table_name in table_names:
            quoted_table_name = _quote_sqlite_identifier(table_name)
            rows = connection.execute(text(f"SELECT COUNT(*) FROM {quoted_table_name}")).scalar_one()
            row_count = int(rows)
            if dbstat_sizes is None:
                size = _estimate_table_data_size(connection, table_name, row_count)
                size_source: Literal["dbstat", "estimated"] = "estimated"
            else:
                size = dbstat_sizes.get(table_name, 0)
                size_source = "dbstat"
            cleanup_config = _DATABASE_CLEANUP_TABLES.get(table_name)
            table_stats.append(
                DatabaseTableStats(
                    name=table_name,
                    rows=row_count,
                    size=size,
                    size_source=size_source,
                    label=cleanup_config["label"] if cleanup_config is not None else table_name,
                    category=cleanup_config["category"] if cleanup_config is not None else "其他",
                    description=cleanup_config["description"] if cleanup_config is not None else "",
                    cleanup_supported=cleanup_config is not None,
                    cleanup_date_column=cleanup_config["date_column"] if cleanup_config is not None else None,
                )
            )
    return sorted(table_stats, key=lambda item: item.name)


def _build_database_stats(include_tables: bool = True) -> DatabaseStorageStats:
    files = _get_database_files()
    page_size, page_count, freelist_count = _get_database_page_stats()
    return DatabaseStorageStats(
        files=files,
        tables=_get_database_table_stats() if include_tables else [],
        total_size=sum(file.size for file in files),
        page_size=page_size,
        page_count=page_count,
        freelist_count=freelist_count,
        free_size=page_size * freelist_count,
    )


def _build_local_cache_stats_response() -> LocalCacheStatsResponse:
    return LocalCacheStatsResponse(
        directories=[
            _build_directory_stats("images", "图片缓存", _IMAGE_DIR, ImageType.IMAGE),
            _build_directory_stats(
                "emoji",
                "表情包缓存",
                _EMOJI_DIR,
                ImageType.EMOJI,
                extra_paths=(_EMOJI_THUMBNAIL_DIR,),
            ),
            _build_directory_stats("logs", "日志文件", _LOG_DIR),
        ],
        database=_build_database_stats(include_tables=False),
    )


def _get_cached_local_cache_stats_response() -> LocalCacheStatsResponse | None:
    cached = _local_cache_stats_cache
    if cached is None:
        return None

    expires_at, response = cached
    if time.monotonic() >= expires_at:
        return None
    return response


def _store_local_cache_stats_response(response: LocalCacheStatsResponse) -> LocalCacheStatsResponse:
    global _local_cache_stats_cache

    expires_at = time.monotonic() + _LOCAL_CACHE_STATS_CACHE_TTL_SECONDS
    _local_cache_stats_cache = (expires_at, response)
    return response


def _get_cached_database_stats_response() -> DatabaseStorageStats | None:
    cached = _database_stats_cache
    if cached is None:
        return None

    expires_at, response = cached
    if time.monotonic() >= expires_at:
        return None
    return response


def _store_database_stats_response(response: DatabaseStorageStats) -> DatabaseStorageStats:
    global _database_stats_cache

    expires_at = time.monotonic() + _LOCAL_CACHE_STATS_CACHE_TTL_SECONDS
    _database_stats_cache = (expires_at, response)
    return response


def _invalidate_local_cache_stats_cache() -> None:
    global _database_stats_cache, _local_cache_stats_cache

    _local_cache_stats_cache = None
    _database_stats_cache = None


def _build_local_cache_image_list_response(
    target: CacheImageTarget,
    page: int,
    page_size: int,
    start_date: str | None,
    end_date: str | None,
) -> LocalCacheImageListResponse:
    all_items = _build_cache_image_items(target)
    date_groups = _build_cache_image_date_groups(all_items)
    items = _filter_cache_image_items_by_date(all_items, start_date, end_date)
    start = (page - 1) * page_size
    end = start + page_size
    return LocalCacheImageListResponse(
        success=True,
        target=target,
        total=len(items),
        page=page,
        page_size=page_size,
        total_size=sum(item.size for item in items),
        data=items[start:end],
        date_groups=date_groups,
    )


def _delete_local_cache_image_response(request: LocalCacheImageDeleteRequest) -> LocalCacheCleanupResponse:
    removed_files, removed_bytes, removed_records = _delete_cache_image_file(request.target, request.relative_path)
    _, _, label = _get_cache_image_target(request.target)
    return LocalCacheCleanupResponse(
        success=True,
        message=f"{label}缓存文件已删除",
        target=request.target,
        removed_files=removed_files,
        removed_bytes=removed_bytes,
        removed_records=removed_records,
    )


def _delete_local_cache_images_bulk_response(request: LocalCacheImageBulkDeleteRequest) -> LocalCacheCleanupResponse:
    items = _build_cache_image_items(request.target)
    if request.mode == "date_range":
        if not request.start_date and not request.end_date:
            raise HTTPException(status_code=400, detail="请至少选择开始日期或结束日期")
        items = _filter_cache_image_items_by_date(items, request.start_date, request.end_date)
        message = "指定日期区间缓存已删除"
    else:
        if request.keep_recent_days is None:
            raise HTTPException(status_code=400, detail="请选择要保留的最近天数")
        cutoff_time = time.time() - request.keep_recent_days * 24 * 60 * 60
        items = [item for item in items if item.modified_time < cutoff_time]
        message = f"最近 {request.keep_recent_days} 天以外的缓存已删除"

    removed_files, removed_bytes, removed_records = _delete_cache_image_items(request.target, items)
    return LocalCacheCleanupResponse(
        success=True,
        message=message,
        target=request.target,
        removed_files=removed_files,
        removed_bytes=removed_bytes,
        removed_records=removed_records,
    )


def _build_local_cache_log_directory_list_response() -> LocalCacheLogDirectoryListResponse:
    items = _build_log_directory_items()
    return LocalCacheLogDirectoryListResponse(success=True, total=len(items), data=items)


def _delete_local_cache_log_directory_response(
    request: LocalCacheLogDirectoryDeleteRequest,
) -> LocalCacheCleanupResponse:
    log_path = _resolve_log_directory(request.relative_path)
    if log_path == _LOG_DIR.resolve():
        removed_files, removed_bytes = _remove_direct_files(_LOG_DIR)
        message = "日志根目录文件已清理"
    else:
        removed_files, removed_bytes = _remove_directory_contents(log_path)
        message = f"日志目录 {request.relative_path} 已清理"

    return LocalCacheCleanupResponse(
        success=True,
        message=message,
        target="log_files",
        removed_files=removed_files,
        removed_bytes=removed_bytes,
    )


def _delete_data_entry_response(request: LocalCacheDataEntryDeleteRequest) -> LocalCacheCleanupResponse:
    target_path = _resolve_data_path(request.relative_path)
    protection_reason = _get_data_path_protection_reason(target_path)
    if protection_reason is not None:
        raise HTTPException(status_code=400, detail=protection_reason)

    if target_path.is_file():
        try:
            file_size = target_path.stat().st_size
            target_path.unlink()
        except OSError as exc:
            logger.warning(f"删除 data 文件失败: {target_path}, error={exc}")
            raise HTTPException(status_code=500, detail="删除 data 文件失败") from exc
        return LocalCacheCleanupResponse(
            success=True,
            message="data 文件已删除",
            target="data",
            removed_files=1,
            removed_bytes=file_size,
        )

    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail="只能删除 data 目录中的文件或文件夹")

    removed_files, removed_bytes = _remove_directory_contents(target_path)
    try:
        target_path.rmdir()
    except OSError as exc:
        logger.warning(f"删除 data 文件夹失败: {target_path}, error={exc}")
        raise HTTPException(status_code=500, detail="删除 data 文件夹失败，可能仍有文件被占用") from exc

    return LocalCacheCleanupResponse(
        success=True,
        message="data 文件夹已删除",
        target="data",
        removed_files=removed_files,
        removed_bytes=removed_bytes,
    )


def _checkpoint_database() -> tuple[int, int, int]:
    if not _DATABASE_FILE.exists():
        return 0, 0, 0

    try:
        with sqlite3.connect(_DATABASE_FILE, timeout=_SQLITE_BUSY_TIMEOUT_SECONDS) as connection:
            connection.isolation_level = None
            connection.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_SECONDS * 1000}")
            row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error as exc:
        logger.warning(f"数据库 WAL checkpoint 失败: {exc}")
        return 0, 0, 0

    if row is None:
        return 0, 0, 0
    return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)


def _vacuum_database_response() -> LocalCacheDatabaseVacuumResponse:
    if not _DATABASE_FILE.exists():
        raise HTTPException(status_code=404, detail="数据库文件不存在")

    before_size = _get_database_total_size()
    _checkpoint_database()

    try:
        with sqlite3.connect(_DATABASE_FILE, timeout=_SQLITE_BUSY_TIMEOUT_SECONDS) as connection:
            connection.isolation_level = None
            connection.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_SECONDS * 1000}")
            connection.execute("VACUUM")
    except sqlite3.Error as exc:
        logger.warning(f"数据库 VACUUM 失败: {exc}")
        raise HTTPException(status_code=500, detail=f"数据库 VACUUM 失败: {exc}") from exc

    checkpoint_busy, checkpoint_log, checkpointed = _checkpoint_database()
    after_size = _get_database_total_size()
    return LocalCacheDatabaseVacuumResponse(
        success=True,
        message="数据库 VACUUM 已完成",
        database_size_before=before_size,
        database_size_after=after_size,
        reclaimed_bytes=max(0, before_size - after_size),
        checkpoint_busy=checkpoint_busy,
        checkpoint_log=checkpoint_log,
        checkpointed=checkpointed,
    )


def _cleanup_local_cache_response(request: LocalCacheCleanupRequest) -> LocalCacheCleanupResponse:
    if request.target == "images":
        removed_files, removed_bytes = _remove_directory_contents(_IMAGE_DIR)
        removed_records = _delete_image_records(ImageType.IMAGE)
        return LocalCacheCleanupResponse(
            success=True,
            message="图片缓存已清理",
            target=request.target,
            removed_files=removed_files,
            removed_bytes=removed_bytes,
            removed_records=removed_records,
        )

    if request.target == "emoji":
        emoji_files, emoji_bytes = _remove_directory_contents(_EMOJI_DIR)
        thumbnail_files, thumbnail_bytes = _remove_directory_contents(_EMOJI_THUMBNAIL_DIR)
        removed_records = _delete_image_records(ImageType.EMOJI)
        return LocalCacheCleanupResponse(
            success=True,
            message="表情包缓存已清理",
            target=request.target,
            removed_files=emoji_files + thumbnail_files,
            removed_bytes=emoji_bytes + thumbnail_bytes,
            removed_records=removed_records,
        )

    if request.target == "log_files":
        removed_files, removed_bytes = _remove_directory_contents(_LOG_DIR)
        return LocalCacheCleanupResponse(
            success=True,
            message="日志文件已清理",
            target=request.target,
            removed_files=removed_files,
            removed_bytes=removed_bytes,
        )

    if not request.tables:
        raise HTTPException(status_code=400, detail="请至少选择一个要清理的数据库表")

    removed_records = _delete_database_records(list(request.tables), request.database_mode, request.older_than_days)
    maintenance_result = None
    if request.vacuum_after_cleanup and removed_records > 0:
        maintenance_result = _vacuum_database_response()

    return LocalCacheCleanupResponse(
        success=True,
        message="数据库记录已清理",
        target=request.target,
        removed_records=removed_records,
        vacuumed=maintenance_result is not None,
        database_size_before=maintenance_result.database_size_before if maintenance_result is not None else None,
        database_size_after=maintenance_result.database_size_after if maintenance_result is not None else None,
        reclaimed_bytes=maintenance_result.reclaimed_bytes if maintenance_result is not None else 0,
    )


def _remove_directory_contents(directory: Path) -> tuple[int, int]:
    if not directory.exists() or not directory.is_dir():
        return 0, 0

    removed_files = 0
    removed_bytes = 0
    for file_path in _iter_files(directory):
        try:
            file_size = file_path.stat().st_size
            file_path.unlink()
            removed_files += 1
            removed_bytes += file_size
        except OSError as exc:
            logger.warning(f"删除缓存文件失败: {file_path}, error={exc}")

    for child in sorted(directory.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if child.is_dir():
            try:
                child.rmdir()
            except OSError:
                pass
    return removed_files, removed_bytes


def _remove_direct_files(directory: Path) -> tuple[int, int]:
    if not directory.exists() or not directory.is_dir():
        return 0, 0

    removed_files = 0
    removed_bytes = 0
    for file_path in directory.iterdir():
        if not file_path.is_file():
            continue
        try:
            file_size = file_path.stat().st_size
            file_path.unlink()
            removed_files += 1
            removed_bytes += file_size
        except OSError as exc:
            logger.warning(f"删除缓存文件失败: {file_path}, error={exc}")
    return removed_files, removed_bytes


def _remove_empty_parent_dirs(start_dir: Path, stop_dir: Path) -> None:
    stop_path = stop_dir.resolve()
    current_dir = start_dir.resolve()

    while current_dir != stop_path:
        try:
            current_dir.relative_to(stop_path)
            current_dir.rmdir()
        except OSError:
            break
        except ValueError:
            break
        current_dir = current_dir.parent


def _delete_emoji_thumbnail_files(image_hashes: set[str]) -> tuple[int, int]:
    removed_files = 0
    removed_bytes = 0
    for image_hash in image_hashes:
        thumbnail_path = _EMOJI_THUMBNAIL_DIR / f"{image_hash}.webp"
        if not thumbnail_path.exists():
            continue
        try:
            file_size = thumbnail_path.stat().st_size
            thumbnail_path.unlink()
            removed_files += 1
            removed_bytes += file_size
        except OSError as exc:
            logger.warning(f"删除表情包缩略图缓存失败: {thumbnail_path}, error={exc}")
    return removed_files, removed_bytes


def _remove_emoji_hashes_from_memory(image_hashes: set[str]) -> None:
    if not image_hashes:
        return

    try:
        from src.emoji_system.emoji_manager import emoji_manager

        emoji_manager.emojis = [emoji for emoji in emoji_manager.emojis if emoji.file_hash not in image_hashes]
        emoji_manager._emoji_num = len(emoji_manager.emojis)
    except Exception as exc:
        logger.warning(f"同步移除内存表情包失败: {exc}")


def _delete_image_records_for_file(image_type: ImageType, file_path: Path) -> tuple[int, set[str]]:
    removed_records = 0
    removed_hashes: set[str] = set()

    with get_db_session() as session:
        statement = select(Images).where(col(Images.image_type) == image_type)
        for record in session.exec(statement).all():
            if not _paths_equal(record.full_path, file_path):
                continue

            if record.image_hash:
                removed_hashes.add(record.image_hash)
            session.delete(record)
            removed_records += 1

    if image_type == ImageType.EMOJI:
        _remove_emoji_hashes_from_memory(removed_hashes)
    return removed_records, removed_hashes


def _delete_cache_image_file(target: CacheImageTarget, relative_path: str) -> tuple[int, int, int]:
    root_dir, image_type, label = _get_cache_image_target(target)
    file_path = _resolve_cache_image_file(target, relative_path)

    try:
        file_size = file_path.stat().st_size
        file_path.unlink()
    except OSError as exc:
        logger.warning(f"删除{label}缓存文件失败: {file_path}, error={exc}")
        raise HTTPException(status_code=500, detail=f"删除{label}缓存文件失败") from exc

    removed_records, removed_hashes = _delete_image_records_for_file(image_type, file_path)
    thumbnail_files = 0
    thumbnail_bytes = 0
    if image_type == ImageType.EMOJI:
        thumbnail_files, thumbnail_bytes = _delete_emoji_thumbnail_files(removed_hashes)

    _remove_empty_parent_dirs(file_path.parent, root_dir)
    return 1 + thumbnail_files, file_size + thumbnail_bytes, removed_records


def _delete_cache_image_items(target: CacheImageTarget, items: list[LocalCacheImageItem]) -> tuple[int, int, int]:
    removed_files = 0
    removed_bytes = 0
    removed_records = 0
    for item in items:
        try:
            item_removed_files, item_removed_bytes, item_removed_records = _delete_cache_image_file(
                target, item.relative_path
            )
        except HTTPException as exc:
            if exc.status_code == 404:
                continue
            raise
        removed_files += item_removed_files
        removed_bytes += item_removed_bytes
        removed_records += item_removed_records
    return removed_files, removed_bytes, removed_records


def _delete_image_records(image_type: ImageType) -> int:
    removed_records = 0
    removed_hashes: set[str] = set()
    with get_db_session() as session:
        statement = select(Images).where(col(Images.image_type) == image_type)
        for record in session.exec(statement).all():
            if record.image_hash:
                removed_hashes.add(record.image_hash)
            session.delete(record)
            removed_records += 1
    if image_type == ImageType.EMOJI:
        _remove_emoji_hashes_from_memory(removed_hashes)
    return removed_records


def _delete_database_records(
    table_names: list[str],
    mode: DatabaseCleanupMode,
    older_than_days: int | None,
) -> int:
    allowed_tables = set(_DATABASE_CLEANUP_TABLES)
    invalid_tables = set(table_names) - allowed_tables
    if invalid_tables:
        raise ValueError(f"不支持清理这些表: {', '.join(sorted(invalid_tables))}")
    if mode == "older_than_days" and older_than_days is None:
        raise HTTPException(status_code=400, detail="按时间清理时必须设置保留天数")

    removed_records = 0
    existing_tables = set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        for table_name in table_names:
            if table_name not in existing_tables:
                continue

            quoted_table_name = _quote_sqlite_identifier(table_name)
            if mode == "all":
                result = connection.execute(text(f"DELETE FROM {quoted_table_name}"))
            else:
                cleanup_config = _DATABASE_CLEANUP_TABLES[table_name]
                date_column = cleanup_config["date_column"]
                cutoff_time = datetime.now() - timedelta(days=older_than_days or 0)
                quoted_date_column = _quote_sqlite_identifier(date_column)
                result = connection.execute(
                    text(f"DELETE FROM {quoted_table_name} WHERE {quoted_date_column} < :cutoff_time"),
                    {"cutoff_time": cutoff_time},
                )
            removed_records += int(result.rowcount or 0)
    return removed_records


async def _stop_runtime_before_restart() -> None:
    """WebUI 重启前主动停止插件运行时，避免遗留 runner 子进程。"""
    try:
        from src.core.event_bus import event_bus
        from src.core.types import EventType

        await event_bus.emit(event_type=EventType.ON_STOP)
    except Exception as exc:
        logger.warning(f"WebUI 重启前触发 ON_STOP 事件失败: {exc}")

    try:
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        await get_plugin_runtime_manager().stop()
    except Exception as exc:
        logger.error(f"WebUI 重启前停止插件运行时失败: {exc}", exc_info=True)

    try:
        from src.manager.async_task_manager import async_task_manager

        await async_task_manager.stop_and_wait_all_tasks()
    except Exception as exc:
        logger.warning(f"WebUI 重启前停止异步任务失败: {exc}")


async def _delayed_restart() -> None:
    await asyncio.sleep(0.5)  # 延迟 0.5 秒，确保响应已发送
    logger.info("WebUI 请求重启，正在停止插件运行时")
    from src.common.runtime_loop import run_on_main_loop

    try:
        await run_on_main_loop(_stop_runtime_before_restart())
    except Exception as exc:
        logger.error(f"WebUI 重启前清理运行时失败，将继续退出以触发外部 runner 重启: {exc}", exc_info=True)
    finally:
        logger.info(f"WebUI 请求重启，退出代码 {_RESTART_EXIT_CODE}")
        os._exit(_RESTART_EXIT_CODE)


@router.post("/restart", response_model=RestartResponse)
async def restart_maibot():
    """
    重启麦麦主程序

    请求重启当前进程，配置更改将在重启后生效。
    注意：此操作会使麦麦暂时离线。
    """
    try:
        global _restart_task

        # 记录重启操作
        logger.info("WebUI 触发重启操作")

        # 创建后台任务执行重启；退出码 42 是外部 runner 约定的重启状态码。
        if _restart_task is None or _restart_task.done():
            _restart_task = asyncio.create_task(_delayed_restart())

        # 立即返回成功响应
        return RestartResponse(success=True, message="麦麦正在重启中...")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重启失败: {str(e)}") from e


@router.get("/status", response_model=StatusResponse)
async def get_maibot_status():
    """
    获取麦麦运行状态

    返回麦麦的运行状态、运行时长和版本信息。
    """
    try:
        uptime = time.time() - _start_time

        # 尝试获取版本信息（需要根据实际情况调整）
        version = MMC_VERSION  # 可以从配置或常量中读取

        return StatusResponse(
            running=True, uptime=uptime, version=version, start_time=datetime.fromtimestamp(_start_time).isoformat()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取状态失败: {str(e)}") from e


@router.get("/update-notice", response_model=UpdateNoticeResponse)
async def get_update_notice() -> UpdateNoticeResponse:
    """获取当前 WebUI 是否需要弹出更新公告。"""

    try:
        notice = get_pending_update_notice("webui", current_version=MMC_VERSION)
        if notice is None:
            return UpdateNoticeResponse(pending=False, current_version=MMC_VERSION)
        return UpdateNoticeResponse(
            pending=True,
            current_version=notice.current_version,
            from_version=notice.from_version,
            versions=notice.versions,
            content=notice.content,
        )
    except Exception as e:
        logger.exception(f"获取更新公告失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取更新公告失败: {str(e)}") from e


@router.post("/update-notice/ack", response_model=UpdateNoticeAckResponse)
async def ack_update_notice() -> UpdateNoticeAckResponse:
    """确认当前版本的 WebUI 更新公告已展示。"""

    try:
        mark_update_notice_seen("webui", current_version=MMC_VERSION)
        return UpdateNoticeAckResponse(success=True, message="更新公告已确认", version=MMC_VERSION)
    except Exception as e:
        logger.exception(f"确认更新公告失败: {e}")
        raise HTTPException(status_code=500, detail=f"确认更新公告失败: {str(e)}") from e


@router.get("/local-cache", response_model=LocalCacheStatsResponse)
async def get_local_cache_stats():
    """获取本地存储概览，数据库表详情会按需单独加载以加快首屏速度。"""
    try:
        cached_response = _get_cached_local_cache_stats_response()
        if cached_response is not None:
            return cached_response

        response = await asyncio.to_thread(_build_local_cache_stats_response)
        return _store_local_cache_stats_response(response)
    except Exception as e:
        logger.exception(f"获取本地缓存统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取本地缓存统计失败: {str(e)}") from e


@router.get("/local-cache/database", response_model=DatabaseStorageStats)
async def get_local_cache_database_stats() -> DatabaseStorageStats:
    """获取数据库文件、分页空闲空间和表级存储详情。"""
    try:
        cached_response = _get_cached_database_stats_response()
        if cached_response is not None:
            return cached_response

        response = await asyncio.to_thread(_build_database_stats, True)
        return _store_database_stats_response(response)
    except Exception as e:
        logger.exception(f"获取数据库存储统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取数据库存储统计失败: {str(e)}") from e


@router.post("/local-cache/database/vacuum", response_model=LocalCacheDatabaseVacuumResponse)
async def vacuum_local_cache_database() -> LocalCacheDatabaseVacuumResponse:
    """执行 SQLite VACUUM，释放删除记录后留下的空闲页。"""
    try:
        response = await asyncio.to_thread(_vacuum_database_response)
        _invalidate_local_cache_stats_cache()
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"数据库 VACUUM 失败: {e}")
        raise HTTPException(status_code=500, detail=f"数据库 VACUUM 失败: {str(e)}") from e


@router.get("/local-cache/data-entries", response_model=LocalCacheDataEntriesResponse)
async def list_local_cache_data_entries(
    relative_path: Annotated[str, Query(description="相对于 data 目录的文件夹路径")] = "",
) -> LocalCacheDataEntriesResponse:
    """浏览 data 目录下的文件和文件夹，按需统计当前层级。"""
    try:
        return await asyncio.to_thread(_build_data_entries_response, relative_path)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取 data 目录条目失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取 data 目录条目失败: {str(e)}") from e


@router.delete("/local-cache/data-entries", response_model=LocalCacheCleanupResponse)
async def delete_local_cache_data_entry(request: LocalCacheDataEntryDeleteRequest) -> LocalCacheCleanupResponse:
    """删除 data 目录中的非受保护文件或文件夹。"""
    try:
        response = await asyncio.to_thread(_delete_data_entry_response, request)
        _invalidate_local_cache_stats_cache()
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"删除 data 目录条目失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除 data 目录条目失败: {str(e)}") from e


@router.get("/local-cache/images", response_model=LocalCacheImageListResponse)
async def list_local_cache_images(
    target: Annotated[CacheImageTarget, Query(description="缓存类型：images 或 emoji")],
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200, description="每页数量")] = 40,
    start_date: Annotated[str | None, Query(description="开始日期，格式 YYYY-MM-DD")] = None,
    end_date: Annotated[str | None, Query(description="结束日期，格式 YYYY-MM-DD")] = None,
) -> LocalCacheImageListResponse:
    """分页列出 images 或 emoji 本地缓存中的图片文件。"""
    try:
        return await asyncio.to_thread(
            _build_local_cache_image_list_response,
            target,
            page,
            page_size,
            start_date,
            end_date,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取本地缓存图片列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取本地缓存图片列表失败: {str(e)}") from e


@router.get("/local-cache/images/preview", response_model=None)
async def preview_local_cache_image(
    target: Annotated[CacheImageTarget, Query(description="缓存类型：images 或 emoji")],
    relative_path: Annotated[str, Query(description="相对于缓存目录的图片路径")],
) -> FileResponse:
    """返回本地缓存图片文件预览。"""
    file_path = _resolve_cache_image_file(target, relative_path)
    media_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    return FileResponse(file_path, media_type=media_type, filename=file_path.name)


@router.get("/maisaka-monitor/media/{media_kind}/{media_hash}", response_model=None)
async def get_maisaka_monitor_media(media_kind: MonitorMediaKind, media_hash: str) -> FileResponse:
    """返回 MaiSaka 观察面板消息中的原始图片或表情文件。"""

    file_path = _resolve_monitor_media_file(media_kind, media_hash)
    media_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    return FileResponse(file_path, media_type=media_type, filename=file_path.name)


@router.delete("/local-cache/images", response_model=LocalCacheCleanupResponse)
async def delete_local_cache_image(request: LocalCacheImageDeleteRequest) -> LocalCacheCleanupResponse:
    """删除 images 或 emoji 缓存中的单个图片文件。"""
    try:
        response = await asyncio.to_thread(_delete_local_cache_image_response, request)
        _invalidate_local_cache_stats_cache()
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"删除本地缓存图片失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除本地缓存图片失败: {str(e)}") from e


@router.delete("/local-cache/images/bulk", response_model=LocalCacheCleanupResponse)
async def delete_local_cache_images_bulk(request: LocalCacheImageBulkDeleteRequest) -> LocalCacheCleanupResponse:
    """按日期范围批量删除 images 或 emoji 缓存。"""
    try:
        response = await asyncio.to_thread(_delete_local_cache_images_bulk_response, request)
        _invalidate_local_cache_stats_cache()
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"批量删除本地缓存图片失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量删除本地缓存图片失败: {str(e)}") from e


@router.get("/local-cache/log-directories", response_model=LocalCacheLogDirectoryListResponse)
async def list_local_cache_log_directories() -> LocalCacheLogDirectoryListResponse:
    """列出 logs 目录下可分别清理的日志目录。"""
    try:
        return await asyncio.to_thread(_build_local_cache_log_directory_list_response)
    except Exception as e:
        logger.exception(f"获取日志目录列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取日志目录列表失败: {str(e)}") from e


@router.delete("/local-cache/log-directories", response_model=LocalCacheCleanupResponse)
async def delete_local_cache_log_directory(request: LocalCacheLogDirectoryDeleteRequest) -> LocalCacheCleanupResponse:
    """清理 logs 下的指定目录，空路径仅清理 logs 根目录下的文件。"""
    try:
        response = await asyncio.to_thread(_delete_local_cache_log_directory_response, request)
        _invalidate_local_cache_stats_cache()
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"清理日志目录失败: {e}")
        raise HTTPException(status_code=500, detail=f"清理日志目录失败: {str(e)}") from e


@router.post("/local-cache/cleanup", response_model=LocalCacheCleanupResponse)
async def cleanup_local_cache(request: LocalCacheCleanupRequest):
    """清理指定的本地缓存区域。"""
    try:
        response = await asyncio.to_thread(_cleanup_local_cache_response, request)
        _invalidate_local_cache_stats_cache()
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"清理本地缓存失败: {e}")
        raise HTTPException(status_code=500, detail=f"清理本地缓存失败: {str(e)}") from e


# 可选：添加更多系统控制功能


@router.post("/reload-config")
async def reload_config():
    """
    热重载配置（不重启进程）

    仅重新加载配置文件，某些配置可能需要重启才能生效。
    此功能需要在主程序中实现配置热重载逻辑。
    """
    # 这里需要调用主程序的配置重载函数
    # 示例：await app_instance.reload_config()

    return {"success": True, "message": "配置重载功能待实现"}
