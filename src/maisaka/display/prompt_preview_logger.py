"""Maisaka Prompt 预览落盘器。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import os
import queue
import threading
import time

from src.common.logger import get_logger

from .preview_path_utils import REPO_ROOT, build_preview_chat_dir_name, normalize_preview_name

logger = get_logger("maisaka_prompt_preview")


@dataclass(frozen=True)
class _PreviewWriteTask:
    """一次待落盘的预览写入任务。"""

    chat_dir: Path
    file_path: Path
    content: str


class PromptPreviewLogger:
    """负责保存 Maisaka Prompt 预览文件并控制目录容量。

    落盘与目录清理由专用线程串行执行：推理循环只需要拿到文件路径用于展示，
    不应该为磁盘写入和目录扫描付出等待时间。
    """

    # 使用仓库绝对路径，与 WebUI 读取预览时的目录保持一致，不依赖当前工作目录。
    _BASE_DIR = REPO_ROOT / "logs" / "maisaka_prompt"
    _DEFAULT_MAX_PREVIEW_GROUPS_PER_CHAT = 256
    _QUEUE_MAXSIZE = 256

    _write_queue: "queue.Queue[_PreviewWriteTask]" = queue.Queue(maxsize=_QUEUE_MAXSIZE)
    _writer_lock = threading.Lock()
    _writer_thread: threading.Thread | None = None
    # 记录每个目录最近分配过的时间戳，保证文件名严格递增且不重名
    _stem_lock = threading.Lock()
    _last_stem_by_dir: dict[Path, int] = {}

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
        cls._submit(_PreviewWriteTask(chat_dir=chat_dir, file_path=file_path, content=content))
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
            logger.error(
                f"Prompt 预览写入队列已满（上限 {cls._QUEUE_MAXSIZE}），本次预览未落盘: {task.file_path}"
            )

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
    def _writer_loop(cls) -> None:
        """串行消费落盘任务；单个任务失败不中断其余预览。"""

        while True:
            task = cls._write_queue.get()
            try:
                cls._write_task(task)
            except Exception as exc:
                logger.error(f"Prompt 预览落盘失败: {task.file_path}, error={exc}", exc_info=True)

    @classmethod
    def _write_task(cls, task: _PreviewWriteTask) -> None:
        """写入单个预览文件并执行超量清理。"""

        task.chat_dir.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再改名，避免 WebUI 读到只写了一半的 JSON。
        temporary_path = task.file_path.with_name(f".{task.file_path.name}.tmp")
        temporary_path.write_text(task.content, encoding="utf-8")
        os.replace(temporary_path, task.file_path)
        cls._trim_overflow(task.chat_dir)

    @classmethod
    def _trim_overflow(cls, chat_dir: Path) -> None:
        """超过阈值时删除多出来的预览文件，最终恰好保留阈值数量。

        文件名就是毫秒时间戳，按文件名排序等价于按时间排序，因此不需要为每个
        文件查询修改时间；os.scandir 的 DirEntry 已带类型信息，也无需额外 stat。
        """

        max_preview_groups = cls._get_max_preview_groups_per_chat()
        try:
            with os.scandir(chat_dir) as entries:
                names = [
                    entry.name for entry in entries if entry.is_file() and entry.name.endswith(".json")
                ]
        except FileNotFoundError:
            return

        if len(names) <= max_preview_groups:
            return

        names.sort()
        for name in names[: len(names) - max_preview_groups]:
            try:
                (chat_dir / name).unlink()
            except FileNotFoundError:
                continue

    @classmethod
    def _get_max_preview_groups_per_chat(cls) -> int:
        try:
            from src.config.config import global_config

            configured_limit = global_config.log.maisaka_prompt_preview_limit
            return max(1, int(configured_limit or cls._DEFAULT_MAX_PREVIEW_GROUPS_PER_CHAT))
        except Exception:
            return cls._DEFAULT_MAX_PREVIEW_GROUPS_PER_CHAT
