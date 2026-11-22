from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Dict, Set, Tuple
from PIL import Image
from sqlmodel import col, select
import os
import threading

from src.common.database.database import get_db_session
from src.common.database.database_model import Images, ImageType
from src.common.logger import get_logger

logger = get_logger("webui.emoji")

THUMBNAIL_CACHE_DIR = Path("data/emoji_thumbnails")
THUMBNAIL_SIZE = (200, 200)
THUMBNAIL_QUALITY = 80
EMOJI_REGISTERED_DIR = os.path.join("data", "emoji")
EMOJI_DIR = EMOJI_REGISTERED_DIR

_thumbnail_locks: Dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()
_thumbnail_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thumbnail")
_generating_thumbnails: Set[str] = set()
_generating_lock = threading.Lock()


def get_thumbnail_executor() -> ThreadPoolExecutor:
    """获取缩略图生成线程池。"""
    return _thumbnail_executor


def get_generating_lock() -> threading.Lock:
    """获取缩略图生成状态锁。"""
    return _generating_lock


def get_generating_thumbnails() -> Set[str]:
    """获取正在生成的缩略图哈希集合。"""
    return _generating_thumbnails


def _get_thumbnail_lock(file_hash: str) -> threading.Lock:
    """获取指定文件哈希的锁，用于防止并发生成同一缩略图。"""
    with _locks_lock:
        if file_hash not in _thumbnail_locks:
            _thumbnail_locks[file_hash] = threading.Lock()
        return _thumbnail_locks[file_hash]


def _background_generate_thumbnail(source_path: str, file_hash: str) -> None:
    """在线程池中后台生成缩略图。"""
    try:
        _generate_thumbnail(source_path, file_hash)
    except Exception as e:
        logger.warning(f"后台生成缩略图失败 {file_hash}: {e}")
    finally:
        with _generating_lock:
            _generating_thumbnails.discard(file_hash)


def ensure_thumbnail_cache_dir() -> Path:
    """确保缩略图缓存目录存在。"""
    _ = THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return THUMBNAIL_CACHE_DIR


def get_thumbnail_cache_path(file_hash: str) -> Path:
    """获取缩略图缓存路径。"""
    return THUMBNAIL_CACHE_DIR / f"{file_hash}.webp"


def _generate_thumbnail(source_path: str, file_hash: str) -> Path:
    """生成缩略图并保存到缓存目录。"""
    ensure_thumbnail_cache_dir()
    cache_path = get_thumbnail_cache_path(file_hash)

    lock = _get_thumbnail_lock(file_hash)
    with lock:
        if cache_path.exists():
            return cache_path

        temp_path: Path | None = None
        try:
            with Image.open(source_path) as img:
                if getattr(img, "n_frames", 1) > 1:
                    img.seek(0)

                if img.mode in ("P", "PA"):
                    img = img.convert("RGBA")
                elif img.mode == "LA":
                    img = img.convert("RGBA")
                elif img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")

                img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
                # 读取端以缓存文件存在作为就绪信号，编码完成后再原子发布。
                with NamedTemporaryFile(dir=cache_path.parent, suffix=".tmp", delete=False) as output:
                    temp_path = Path(output.name)
                    img.save(output, "WEBP", quality=THUMBNAIL_QUALITY, method=6)
                temp_path.replace(cache_path)
                logger.debug(f"生成缩略图: {file_hash} -> {cache_path}")
        except Exception as e:
            logger.warning(f"生成缩略图失败 {file_hash}: {e}")
            raise
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    return cache_path


def generate_thumbnail(source_path: str, file_hash: str) -> Path:
    """暴露给路由层的缩略图生成函数。"""
    return _generate_thumbnail(source_path, file_hash)


def background_generate_thumbnail(source_path: str, file_hash: str) -> None:
    """暴露给路由层的后台缩略图生成函数。"""
    _background_generate_thumbnail(source_path, file_hash)


def cleanup_orphaned_thumbnails() -> Tuple[int, int]:
    """清理孤立的缩略图缓存。"""
    if not THUMBNAIL_CACHE_DIR.exists():
        return 0, 0

    with get_db_session() as session:
        statement = select(Images.image_hash).where(col(Images.image_type) == ImageType.EMOJI)
        valid_hashes = set(session.exec(statement).all())

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
