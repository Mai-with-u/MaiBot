"""Maisaka Prompt 预览落盘器。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import threading
import time

from src.common.logger import get_logger

from .preview_path_utils import build_preview_chat_dir_name, normalize_preview_name

logger = get_logger("maisaka_prompt_preview")


class PromptPreviewLogger:
    """负责保存 Maisaka Prompt 预览文件并控制目录容量。

    写入同步完成，确保返回路径可立即访问；目录清理在单线程后台 executor 中执行，
    避免 iterdir/stat/unlink 直接跑在事件循环上造成秒级卡顿。
    """

    _BASE_DIR = Path("logs") / "maisaka_prompt"
    _DEFAULT_MAX_PREVIEW_GROUPS_PER_CHAT = 256
    _TRIM_COUNT = 100

    _io_executor: ThreadPoolExecutor | None = None
    _executor_lock = threading.Lock()
    _stem_lock = threading.Lock()
    _last_stem_base: str = ""
    _last_stem_suffix: int = 0

    @classmethod
    def _get_io_executor(cls) -> ThreadPoolExecutor:
        if cls._io_executor is None:
            with cls._executor_lock:
                if cls._io_executor is None:
                    cls._io_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prompt-preview")
        return cls._io_executor

    @classmethod
    def _build_file_stem(cls, chat_dir: Path) -> str:
        with cls._stem_lock:
            base_stem = str(int(time.time() * 1000))
            suffix_index = cls._last_stem_suffix + 1 if base_stem == cls._last_stem_base else 0
            while True:
                candidate_stem = base_stem if suffix_index == 0 else f"{base_stem}_{suffix_index}"
                if not (chat_dir / f"{candidate_stem}.json").exists():
                    cls._last_stem_base = base_stem
                    cls._last_stem_suffix = suffix_index
                    return candidate_stem
                suffix_index += 1

    @classmethod
    def save_preview_file(
        cls,
        chat_id: str,
        category: str,
        content: str,
    ) -> Path:
        """保存 Prompt 预览 JSON，并在后台执行超量清理。"""

        normalized_category = normalize_preview_name(category)
        chat_dir = (cls._BASE_DIR / normalized_category / build_preview_chat_dir_name(chat_id)).resolve()
        chat_dir.mkdir(parents=True, exist_ok=True)
        stem = cls._build_file_stem(chat_dir)
        file_path = chat_dir / f"{stem}.json"
        file_path.write_text(content, encoding="utf-8")
        trim_future = cls._get_io_executor().submit(cls._trim_overflow, chat_dir)
        trim_future.add_done_callback(cls._log_background_error)
        return file_path

    @staticmethod
    def _log_background_error(future: Future[None]) -> None:
        try:
            future.result()
        except Exception as exc:
            logger.exception(f"Prompt 预览目录后台清理失败: {exc}")

    @staticmethod
    def _group_sort_key(item: tuple[str, list[Path]]) -> float:
        stem = item[0]
        try:
            return float(stem.split("_", 1)[0])
        except ValueError:
            try:
                return min(path.stat().st_mtime * 1000 for path in item[1])
            except OSError:
                return 0.0

    @classmethod
    def _trim_overflow(cls, chat_dir: Path) -> None:
        """超过阈值时按批次删除最老的若干组预览文件。"""

        max_preview_groups = cls._get_max_preview_groups_per_chat()
        grouped_files: dict[str, list[Path]] = {}
        for file_path in chat_dir.iterdir():
            if not file_path.is_file():
                continue
            grouped_files.setdefault(file_path.stem, []).append(file_path)

        if len(grouped_files) <= max_preview_groups:
            return

        sorted_groups = sorted(grouped_files.items(), key=cls._group_sort_key)
        overflow_count = len(grouped_files) - max_preview_groups
        trim_count = min(len(sorted_groups), max(cls._TRIM_COUNT, overflow_count))
        for _, file_group in sorted_groups[:trim_count]:
            for old_file in file_group:
                try:
                    old_file.unlink()
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
