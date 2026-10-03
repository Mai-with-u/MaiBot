"""Maisaka Prompt 预览落盘器。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, Optional, Set

import os
import queue
import re
import shutil
import threading
import time

from src.common.logger import get_logger

from .preview_path_utils import REPO_ROOT, build_preview_chat_dir_name, normalize_preview_name

logger = get_logger("maisaka_prompt_preview")
_image_assets: ContextVar[Optional[Dict[Path, bytes]]] = ContextVar("prompt_preview_image_assets", default=None)


@dataclass(frozen=True)
class _PreviewWriteTask:
    """一次待落盘的预览写入任务。"""

    chat_dir: Path
    file_path: Path
    content: str
    image_assets: Dict[Path, bytes]


class PromptPreviewLogger:
    """负责保存 Maisaka Prompt 预览文件并控制目录容量。

    落盘与目录清理由专用线程串行执行：推理循环只需要拿到文件路径用于展示，
    不应该为磁盘写入和目录扫描付出等待时间。
    """

    # 使用仓库绝对路径，与 WebUI 读取预览时的目录保持一致，不依赖当前工作目录。
    _BASE_DIR = REPO_ROOT / "logs" / "maisaka_prompt"
    _DEFAULT_MAX_PREVIEW_GROUPS_PER_CHAT = 256
    _QUEUE_MAXSIZE = 256
    _ORPHAN_IMAGE_CHECK_INTERVAL_SECONDS = 60 * 60

    _write_queue: "queue.Queue[_PreviewWriteTask]" = queue.Queue(maxsize=_QUEUE_MAXSIZE)
    _writer_lock = threading.Lock()
    _writer_thread: threading.Thread | None = None
    # 记录每个目录最近分配过的时间戳，保证文件名严格递增且不重名
    _stem_lock = threading.Lock()
    _last_stem_by_dir: dict[Path, int] = {}
    _IMAGE_DIR = REPO_ROOT / "data" / "prompt_imgs"
    _IMAGE_NAME_PATTERN = re.compile(r"prompt_imgs(?:[/\\]|%2[fF]|%5[cC])+([0-9a-f]{64}\.[A-Za-z0-9]+)")
    _CACHE_IMAGE_NAME_PATTERN = re.compile(r"[0-9a-f]{64}\.[A-Za-z0-9]+")
    _storage_lock = threading.RLock()
    _image_index_ready = False
    _images_by_preview: Dict[Path, Set[str]] = {}
    _previews_by_image: Dict[str, Set[Path]] = {}

    @classmethod
    @contextmanager
    def collect_image_assets(cls) -> Iterator[None]:
        """在内存中收集本次预览图片，与 JSON 一起交给后台线程落盘。"""
        token = _image_assets.set({})
        try:
            yield
        finally:
            _image_assets.reset(token)

    @classmethod
    def add_image_asset(cls, path: Path, content: bytes) -> None:
        assets = _image_assets.get()
        if assets is None:
            raise RuntimeError("Prompt 图片必须在预览构建上下文中登记")
        assets[path] = content

    @classmethod
    def save_preview_file(
        cls,
        chat_id: str,
        category: str,
        content: str,
    ) -> Path:
        """登记一次预览落盘，并立即返回该预览的文件路径。

        磁盘写入与超量清理交给后台写线程，本方法只做内存计算与入队。
        """

        normalized_category = normalize_preview_name(category)
        chat_dir = cls._BASE_DIR / normalized_category / build_preview_chat_dir_name(chat_id)
        file_path = chat_dir / f"{cls._allocate_stem(chat_dir)}.json"
        cls._submit(
            _PreviewWriteTask(
                chat_dir=chat_dir,
                file_path=file_path,
                content=content,
                image_assets=dict(_image_assets.get() or {}),
            )
        )
        return file_path

    @classmethod
    def _allocate_stem(cls, chat_dir: Path) -> int:
        """为目录分配一个严格递增的毫秒时间戳文件名。

        文件名同时承担排序职责，因此同一毫秒内连续写入必须递增，不能重复。
        """

        with cls._stem_lock:
            stem = max(int(time.time() * 1000), cls._last_stem_by_dir.get(chat_dir, 0) + 1)
            cls._last_stem_by_dir[chat_dir] = stem
            return stem

    @classmethod
    def _submit(cls, task: _PreviewWriteTask) -> None:
        """把落盘任务交给写线程；队列积满时明确报错，而不是阻塞推理循环。"""

        cls._ensure_writer_thread()
        try:
            cls._write_queue.put_nowait(task)
        except queue.Full:
            logger.error(f"Prompt 预览写入队列已满（上限 {cls._QUEUE_MAXSIZE}），本次预览未落盘: {task.file_path}")

    @classmethod
    def _ensure_writer_thread(cls) -> None:
        if cls._writer_thread is not None and cls._writer_thread.is_alive():
            return

        with cls._writer_lock:
            if cls._writer_thread is not None and cls._writer_thread.is_alive():
                return
            cls._writer_thread = threading.Thread(
                target=cls._writer_loop,
                name="maisaka-prompt-preview-writer",
                daemon=True,
            )
            cls._writer_thread.start()

    @classmethod
    def start(cls) -> None:
        """启动后台写入与巡检线程，即使没有新预览也定期回收遗留图片。"""
        cls._ensure_writer_thread()

    @classmethod
    def _writer_loop(cls) -> None:
        """串行消费落盘任务并巡检孤立图片；单个任务失败不中断其余预览。"""

        next_image_check = time.monotonic()
        while True:
            if time.monotonic() >= next_image_check:
                try:
                    cls.cleanup_orphan_images()
                except Exception as exc:
                    logger.error(f"Prompt 孤立图片巡检失败: error={exc}", exc_info=True)
                next_image_check = time.monotonic() + cls._ORPHAN_IMAGE_CHECK_INTERVAL_SECONDS
            # 超时唤醒保证无请求时仍执行巡检；每轮检查时间，持续写入也不会饿死清理任务。
            try:
                task = cls._write_queue.get(timeout=max(0, next_image_check - time.monotonic()))
            except queue.Empty:
                continue
            try:
                cls._write_task(task)
            except Exception as exc:
                logger.error(f"Prompt 预览落盘失败: {task.file_path}, error={exc}", exc_info=True)
            finally:
                cls._write_queue.task_done()
                del task

    @classmethod
    def cleanup_orphan_images(cls) -> int:
        """重新核对现存记录，删除专用缓存目录中无人引用的哈希图片。"""
        with cls._storage_lock:
            if not cls._IMAGE_DIR.exists():
                return 0
            candidates = [
                path
                for path in cls._IMAGE_DIR.iterdir()
                if cls._CACHE_IMAGE_NAME_PATTERN.fullmatch(path.name) and path.is_file() and not path.is_symlink()
            ]
            if not candidates:
                return 0
            # 巡检重新读取磁盘，覆盖旧版本遗留图片及用户从文件系统删除/修改日志的情况。
            # 扫描或读取失败会直接报错，本轮不会依据不完整的引用索引删除图片。
            cls._image_index_ready = False
            cls._ensure_image_index()
            deleted_count = 0
            deleted_bytes = 0
            for path in candidates:
                if path.name in cls._previews_by_image:
                    continue
                size = path.stat().st_size
                path.unlink()
                deleted_count += 1
                deleted_bytes += size
            if deleted_count:
                logger.info(
                    f"Prompt 孤立图片清理完成: 删除 {deleted_count} 张，释放 {deleted_bytes / (1024 * 1024):.2f} MiB"
                )
            return deleted_count

    @classmethod
    def _write_task(cls, task: _PreviewWriteTask) -> None:
        """写入单个预览文件并执行超量清理。"""

        with cls._storage_lock:
            cls._write_task_locked(task)

    @classmethod
    def _write_task_locked(cls, task: _PreviewWriteTask) -> None:
        task.chat_dir.mkdir(parents=True, exist_ok=True)
        # 图片与记录串行落盘；即使清理先于排队中的记录发生，也会重新写回所需图片。
        for path, content in task.image_assets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(content)
        # 先写临时文件再改名，避免 WebUI 读到只写了一半的 JSON。
        temporary_path = task.file_path.with_name(f".{task.file_path.name}.tmp")
        temporary_path.write_text(task.content, encoding="utf-8")
        os.replace(temporary_path, task.file_path)
        if cls._image_index_ready:
            cls._index_preview(task.file_path, task.content)
        cls._trim_overflow(task.chat_dir)

    @classmethod
    def _index_preview(cls, path: Path, content: str) -> None:
        # 哈希文件名在相对路径、绝对路径和 file URI 中一致，也兼容旧 HTML/TXT 预览。
        names = set(cls._IMAGE_NAME_PATTERN.findall(content))
        if not names:
            return
        cls._images_by_preview[path] = names
        for name in names:
            cls._previews_by_image.setdefault(name, set()).add(path)

    @classmethod
    def _ensure_image_index(cls) -> None:
        """首次清理前建立存量引用索引，后续写入/删除增量更新，避免反复全量扫描。"""
        if cls._image_index_ready:
            return
        cls._images_by_preview.clear()
        cls._previews_by_image.clear()
        for path in cls._BASE_DIR.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".html", ".txt"}:
                cls._index_preview(path, path.read_text(encoding="utf-8"))
        cls._image_index_ready = True

    @classmethod
    def _release_preview_images(cls, path: Path) -> None:
        for name in cls._images_by_preview.pop(path, set()):
            previews = cls._previews_by_image[name]
            previews.discard(path)
            if previews:
                continue
            del cls._previews_by_image[name]
            # 只删除专用预览缓存，绝不触碰聊天图片或表情原件。
            (cls._IMAGE_DIR / name).unlink(missing_ok=True)

    @classmethod
    def clear_stage(cls, stage_dir: Path) -> int:
        """与后台写入互斥地清空推理类型，并释放不再被其他记录引用的图片。"""
        stage_dir = stage_dir.resolve()
        with cls._storage_lock:
            if not stage_dir.is_relative_to(cls._BASE_DIR) or stage_dir == cls._BASE_DIR:
                raise ValueError("无效的推理过程类型路径")
            if not stage_dir.exists():
                return 0
            cls._ensure_image_index()
            paths = [path for path in stage_dir.rglob("*") if path.is_file()]
            shutil.rmtree(stage_dir)
            for path in paths:
                cls._release_preview_images(path)
            return len(paths)

    @classmethod
    def _trim_overflow(cls, chat_dir: Path) -> None:
        """超过阈值时删除多出来的预览文件，最终恰好保留阈值数量。

        文件名就是毫秒时间戳，按文件名排序等价于按时间排序，因此不需要为每个
        文件查询修改时间；os.scandir 的 DirEntry 已带类型信息，也无需额外 stat。
        """

        max_preview_groups = cls._get_max_preview_groups_per_chat()
        try:
            with os.scandir(chat_dir) as entries:
                names = [entry.name for entry in entries if entry.is_file() and entry.name.endswith(".json")]
        except FileNotFoundError:
            return

        if len(names) <= max_preview_groups:
            return

        cls._ensure_image_index()
        names.sort()
        for name in names[: len(names) - max_preview_groups]:
            try:
                (chat_dir / name).unlink()
            except FileNotFoundError:
                pass
            cls._release_preview_images(chat_dir / name)

    @classmethod
    def _get_max_preview_groups_per_chat(cls) -> int:
        try:
            from src.config.config import global_config

            configured_limit = global_config.log.maisaka_prompt_preview_limit
            return max(1, int(configured_limit or cls._DEFAULT_MAX_PREVIEW_GROUPS_PER_CHAT))
        except Exception:
            return cls._DEFAULT_MAX_PREVIEW_GROUPS_PER_CHAT
