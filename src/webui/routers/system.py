"""
系统控制路由

提供系统重启、状态查询等功能
"""

from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, inspect, text
from sqlmodel import col, select

import asyncio
import os
import time

from src.common.database.database import engine, get_db_session
from src.common.database.database_model import Images, ImageType
from src.common.logger import get_logger
from src.config.config import MMC_VERSION
from src.webui.dashboard_update import (
    DASHBOARD_PACKAGE_NAME,
    PYPI_PROJECT_URL,
    detect_package_runner,
    get_dashboard_version_info,
)
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
_restart_task: asyncio.Task[None] | None = None


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


class DashboardVersionResponse(BaseModel):
    """WebUI 版本检查响应"""

    current_version: str
    latest_version: Optional[str] = None
    has_update: bool = False
    runner: str = "unknown"
    package_name: str = DASHBOARD_PACKAGE_NAME
    pypi_url: str = PYPI_PROJECT_URL


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


class DatabaseStorageStats(BaseModel):
    """数据库存储统计。"""

    files: list[DatabaseFileStats]
    tables: list[DatabaseTableStats]
    total_size: int


class LocalCacheStatsResponse(BaseModel):
    """本地缓存统计响应。"""

    directories: list[CacheDirectoryStats]
    database: DatabaseStorageStats


class LocalCacheCleanupRequest(BaseModel):
    """本地缓存清理请求。"""

    target: Literal["images", "emoji", "log_files", "database_logs"]
    tables: list[Literal["llm_usage", "tool_records", "mai_messages"]] = Field(default_factory=list)


class LocalCacheCleanupResponse(BaseModel):
    """本地缓存清理响应。"""

    success: bool
    message: str
    target: str
    removed_files: int = 0
    removed_bytes: int = 0
    removed_records: int = 0


def _parse_version_parts(version: str | None) -> Optional[list[int]]:
    """将版本号转换为可比较的整数列表。"""
    if not version:
        return None
    parts: list[int] = []
    for raw_part in version.split("."):
        if not raw_part.isdigit():
            return None
        parts.append(int(raw_part))
    return parts


def _is_newer_version(latest: str | None, current: str | None) -> bool:
    """判断 latest 是否新于 current。"""
    latest_parts = _parse_version_parts(latest)
    current_parts = _parse_version_parts(current)
    if latest_parts is None or current_parts is None:
        return False

    max_len = max(len(latest_parts), len(current_parts))
    latest_parts.extend([0] * (max_len - len(latest_parts)))
    current_parts.extend([0] * (max_len - len(current_parts)))
    return latest_parts > current_parts


def _iter_files(directory: Path) -> list[Path]:
    if not directory.exists() or not directory.is_dir():
        return []
    return [path for path in directory.rglob("*") if path.is_file()]


def _get_directory_size(directory: Path) -> tuple[int, int]:
    files = _iter_files(directory)
    total_size = 0
    for file_path in files:
        try:
            total_size += file_path.stat().st_size
        except OSError:
            logger.warning(f"读取缓存文件大小失败: {file_path}")
    return len(files), total_size


def _get_image_record_count(image_type: ImageType) -> int:
    with get_db_session() as session:
        statement = select(func.count()).select_from(Images).where(col(Images.image_type) == image_type)
        return int(session.exec(statement).one())


def _build_directory_stats(key: str, label: str, path: Path, image_type: ImageType | None = None) -> CacheDirectoryStats:
    file_count, total_size = _get_directory_size(path)
    return CacheDirectoryStats(
        key=key,
        label=label,
        path=str(path),
        exists=path.exists(),
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


def _get_database_table_stats() -> list[DatabaseTableStats]:
    inspector = inspect(engine)
    table_stats: list[DatabaseTableStats] = []
    with engine.connect() as connection:
        for table_name in inspector.get_table_names():
            quoted_table_name = table_name.replace('"', '""')
            rows = connection.execute(text(f'SELECT COUNT(*) FROM "{quoted_table_name}"')).scalar_one()
            table_stats.append(DatabaseTableStats(name=table_name, rows=int(rows)))
    return sorted(table_stats, key=lambda item: item.name)


def _build_database_stats() -> DatabaseStorageStats:
    files = _get_database_files()
    return DatabaseStorageStats(
        files=files,
        tables=_get_database_table_stats(),
        total_size=sum(file.size for file in files),
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


def _delete_image_records(image_type: ImageType) -> int:
    removed_records = 0
    with get_db_session() as session:
        statement = select(Images).where(col(Images.image_type) == image_type)
        for record in session.exec(statement).all():
            session.delete(record)
            removed_records += 1
    return removed_records


def _delete_log_records(table_names: list[str]) -> int:
    allowed_tables = {"llm_usage", "tool_records", "mai_messages"}
    invalid_tables = set(table_names) - allowed_tables
    if invalid_tables:
        raise ValueError(f"不支持清理这些表: {', '.join(sorted(invalid_tables))}")

    removed_records = 0
    with engine.begin() as connection:
        for table_name in table_names:
            quoted_table_name = table_name.replace('"', '""')
            result = connection.execute(text(f'DELETE FROM "{quoted_table_name}"'))
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
    await _stop_runtime_before_restart()
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


@router.get("/dashboard-version", response_model=DashboardVersionResponse)
async def get_dashboard_version(current_version: Optional[str] = None):
    """获取 WebUI 当前版本和 PyPI 最新版本。"""
    version_info = await get_dashboard_version_info(current_version)

    return DashboardVersionResponse(
        current_version=version_info.current_version,
        latest_version=version_info.latest_version,
        has_update=version_info.has_update,
        runner=detect_package_runner(),
    )


@router.get("/local-cache", response_model=LocalCacheStatsResponse)
async def get_local_cache_stats():
    """获取 data 目录下图片、表情包和数据库的本地存储情况。"""
    try:
        return LocalCacheStatsResponse(
            directories=[
                _build_directory_stats("images", "图片缓存", _IMAGE_DIR, ImageType.IMAGE),
                _build_directory_stats("emoji", "表情包缓存", _EMOJI_DIR, ImageType.EMOJI),
                _build_directory_stats("emoji_thumbnails", "表情包缩略图缓存", _EMOJI_THUMBNAIL_DIR),
                _build_directory_stats("logs", "日志文件", _LOG_DIR),
            ],
            database=_build_database_stats(),
        )
    except Exception as e:
        logger.exception(f"获取本地缓存统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取本地缓存统计失败: {str(e)}") from e


@router.post("/local-cache/cleanup", response_model=LocalCacheCleanupResponse)
async def cleanup_local_cache(request: LocalCacheCleanupRequest):
    """清理指定的本地缓存区域。"""
    try:
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

        removed_records = _delete_log_records(list(request.tables))
        return LocalCacheCleanupResponse(
            success=True,
            message="数据库日志记录已清理",
            target=request.target,
            removed_records=removed_records,
        )
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
