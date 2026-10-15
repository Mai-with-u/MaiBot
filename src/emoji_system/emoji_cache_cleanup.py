"""表情包缓存自动清理任务。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlmodel import col, select

import asyncio

from src.common.paths import get_instance_root
from src.common.database.database import get_db_session
from src.common.database.database_model import Images, ImageType
from src.common.logger import get_logger
from src.common.utils.image_path import StoredImagePathError, resolve_stored_image_path

logger = get_logger("emoji_cache_cleanup")

PROJECT_ROOT = get_instance_root()
EMOJI_DIR = PROJECT_ROOT / "data" / "emoji"
_CACHE_EMOJI_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".mpo",
    ".png",
    ".webp",
}
_DISABLED_POLL_SECONDS = 300
_CLEANUP_BATCH_SIZE = 1000


class EmojiCacheCleanupConfigLike(Protocol):
    enabled: bool
    check_interval_hours: float
    emoji_file_retention_days: int
    no_file_record_retention_days: int


@dataclass
class EmojiCacheCleanupResult:
    removed_files: int = 0
    removed_bytes: int = 0
    marked_no_file_records: int = 0
    removed_orphan_files: int = 0
    removed_orphan_bytes: int = 0
    removed_records: int = 0
    restored_file_flags: int = 0
    skipped_registered_files: int = 0

    @property
    def changed(self) -> bool:
        return any(
            (
                self.removed_files,
                self.removed_bytes,
                self.marked_no_file_records,
                self.removed_orphan_files,
                self.removed_orphan_bytes,
                self.removed_records,
                self.restored_file_flags,
            )
        )


def _interval_seconds(config: EmojiCacheCleanupConfigLike) -> float:
    return max(float(config.check_interval_hours or 0) * 3600.0, 60.0)


def _record_reference_time(record: Images, fallback: datetime) -> datetime:
    return record.last_used_time or record.register_time or record.record_time or fallback


def _resolve_path(path_text: str | None) -> Path | None:
    if not path_text:
        return None
    try:
        return resolve_stored_image_path(path_text)
    except (OSError, RuntimeError, StoredImagePathError):
        return None


def _resolve_cache_emoji_path(path_text: str | None) -> Path | None:
    path = _resolve_path(path_text)
    if path is None or path.suffix.lower() not in _CACHE_EMOJI_EXTENSIONS:
        return None

    try:
        path.relative_to(EMOJI_DIR.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    return path


def _record_file_exists(record: Images) -> bool:
    path = _resolve_path(record.full_path)
    return path is not None and path.is_file()


def _cleanup_unregistered_emoji_files(
    *,
    now: datetime,
    cutoff_time: datetime,
    batch_size: int,
) -> EmojiCacheCleanupResult:
    result = EmojiCacheCleanupResult()
    processed_records = 0
    registered_paths = _get_registered_emoji_paths()

    with get_db_session() as session:
        statement = select(Images).where(col(Images.image_type) == ImageType.EMOJI)
        for record in session.exec(statement).yield_per(100):
            if processed_records >= batch_size:
                break
            if record.is_registered:
                result.skipped_registered_files += 1
                continue

            cache_path = _resolve_cache_emoji_path(record.full_path)
            file_exists = _record_file_exists(record)

            if record.no_file_flag:
                if file_exists:
                    record.no_file_flag = False
                    session.add(record)
                    result.restored_file_flags += 1
                    processed_records += 1
                continue

            if not file_exists:
                record.no_file_flag = True
                session.add(record)
                result.marked_no_file_records += 1
                processed_records += 1
                continue

            if _record_reference_time(record, now) > cutoff_time:
                continue
            if cache_path is None:
                logger.warning(f"跳过不在表情包缓存目录内的文件清理: {record.full_path}")
                continue
            if cache_path in registered_paths:
                result.skipped_registered_files += 1
                continue

            try:
                file_size = cache_path.stat().st_size
                cache_path.unlink()
            except OSError as exc:
                logger.warning(f"清理表情包缓存文件失败: {cache_path}, error={exc}")
                continue

            record.no_file_flag = True
            session.add(record)
            result.removed_files += 1
            result.removed_bytes += file_size
            result.marked_no_file_records += 1
            processed_records += 1

    return result


def _get_registered_emoji_paths() -> set[Path]:
    paths: set[Path] = set()
    with get_db_session(auto_commit=False) as session:
        statement = select(Images.full_path).where(
            (col(Images.image_type) == ImageType.EMOJI) & (col(Images.is_registered).is_(True))
        )
        for full_path in session.exec(statement).all():
            cache_path = _resolve_cache_emoji_path(full_path)
            if cache_path is not None:
                paths.add(cache_path)
    return paths


def _get_tracked_emoji_paths() -> set[Path]:
    paths: set[Path] = set()
    with get_db_session(auto_commit=False) as session:
        statement = select(Images.full_path).where(col(Images.image_type) == ImageType.EMOJI)
        for full_path in session.exec(statement).all():
            cache_path = _resolve_cache_emoji_path(full_path)
            if cache_path is not None:
                paths.add(cache_path)
    return paths


def _cleanup_orphan_emoji_files(*, cutoff_time: datetime, batch_size: int) -> EmojiCacheCleanupResult:
    result = EmojiCacheCleanupResult()
    if not EMOJI_DIR.exists() or not EMOJI_DIR.is_dir():
        return result

    registered_paths = _get_registered_emoji_paths()
    tracked_paths = _get_tracked_emoji_paths()
    cutoff_timestamp = cutoff_time.timestamp()

    for file_path in EMOJI_DIR.rglob("*"):
        if result.removed_orphan_files >= batch_size:
            break
        if not file_path.is_file() or file_path.suffix.lower() not in _CACHE_EMOJI_EXTENSIONS:
            continue

        try:
            resolved_path = file_path.resolve()
            resolved_path.relative_to(EMOJI_DIR.resolve())
            file_stat = file_path.stat()
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(f"读取孤立表情包缓存文件失败: {file_path}, error={exc}")
            continue

        if resolved_path in registered_paths:
            result.skipped_registered_files += 1
            continue
        if resolved_path in tracked_paths or file_stat.st_mtime > cutoff_timestamp:
            continue

        try:
            file_path.unlink()
        except OSError as exc:
            logger.warning(f"清理孤立表情包缓存文件失败: {file_path}, error={exc}")
            continue

        result.removed_orphan_files += 1
        result.removed_orphan_bytes += file_stat.st_size

    return result


def _cleanup_unregistered_no_file_records(
    *,
    now: datetime,
    cutoff_time: datetime,
    batch_size: int,
) -> EmojiCacheCleanupResult:
    result = EmojiCacheCleanupResult()
    processed_records = 0

    with get_db_session() as session:
        statement = select(Images).where(col(Images.image_type) == ImageType.EMOJI)
        for record in session.exec(statement).yield_per(100):
            if processed_records >= batch_size:
                break
            if record.is_registered:
                continue

            if _record_file_exists(record):
                if record.no_file_flag:
                    record.no_file_flag = False
                    session.add(record)
                    result.restored_file_flags += 1
                    processed_records += 1
                continue

            if not record.no_file_flag:
                record.no_file_flag = True
                session.add(record)
                result.marked_no_file_records += 1
                processed_records += 1
                continue

            if _record_reference_time(record, now) > cutoff_time:
                continue

            session.delete(record)
            result.removed_records += 1
            processed_records += 1

    return result


def _merge_results(target: EmojiCacheCleanupResult, source: EmojiCacheCleanupResult) -> EmojiCacheCleanupResult:
    target.removed_files += source.removed_files
    target.removed_bytes += source.removed_bytes
    target.marked_no_file_records += source.marked_no_file_records
    target.removed_orphan_files += source.removed_orphan_files
    target.removed_orphan_bytes += source.removed_orphan_bytes
    target.removed_records += source.removed_records
    target.restored_file_flags += source.restored_file_flags
    target.skipped_registered_files += source.skipped_registered_files
    return target


def run_emoji_cache_cleanup(config: EmojiCacheCleanupConfigLike) -> EmojiCacheCleanupResult:
    """执行一次表情包缓存清理，始终保护已注册表情。"""
    now = datetime.now()
    file_cutoff = now - timedelta(days=max(1, int(config.emoji_file_retention_days or 1)))
    result = _cleanup_unregistered_emoji_files(now=now, cutoff_time=file_cutoff, batch_size=_CLEANUP_BATCH_SIZE)

    _merge_results(result, _cleanup_orphan_emoji_files(cutoff_time=file_cutoff, batch_size=_CLEANUP_BATCH_SIZE))

    record_cutoff = now - timedelta(days=max(1, int(config.no_file_record_retention_days or 1)))
    _merge_results(
        result,
        _cleanup_unregistered_no_file_records(now=now, cutoff_time=record_cutoff, batch_size=_CLEANUP_BATCH_SIZE),
    )

    if result.changed:
        logger.info(
            "表情包缓存自动清理完成："
            f"删除未注册文件 {result.removed_files} 个/{result.removed_bytes} 字节，"
            f"删除孤立文件 {result.removed_orphan_files} 个/{result.removed_orphan_bytes} 字节，"
            f"标记无文件记录 {result.marked_no_file_records} 条，"
            f"删除未注册无文件记录 {result.removed_records} 条，"
            f"恢复文件状态 {result.restored_file_flags} 条，"
            f"跳过已注册表情 {result.skipped_registered_files} 个"
        )
    return result


async def periodic_emoji_cache_cleanup() -> None:
    """按配置周期执行表情包缓存清理。"""
    from src.config.config import global_config

    while True:
        config = global_config.emoji.cache_cleanup
        interval_seconds = _interval_seconds(config)

        if config.enabled:
            try:
                await asyncio.to_thread(run_emoji_cache_cleanup, config)
            except Exception as exc:
                logger.error(f"表情包缓存自动清理失败: {exc}", exc_info=True)

        await asyncio.sleep(interval_seconds if config.enabled else _DISABLED_POLL_SECONDS)
