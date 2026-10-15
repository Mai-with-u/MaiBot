"""Maisaka Prompt 预览落盘器。"""

from __future__ import annotations

from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

import json
import os
import queue
import re
import shutil
import sqlite3
import threading
import time

from src.common.logger import get_logger
from src.common.operation_timing import log_operation, timed_operation

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
    enqueued_at: float = field(default_factory=time.perf_counter)


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
    _INDEX_PERSIST_INTERVAL_SECONDS = 60
    _INDEX_SCHEMA_VERSION = 1

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
    _storage_operation: str = ""
    _image_index_ready = False
    _images_by_preview: Dict[Path, Set[str]] = {}
    _previews_by_image: Dict[str, Set[Path]] = {}
    # 无图片引用的日志也保存签名，避免每次巡检重新读取。
    _record_signatures: Dict[Path, Tuple[int, int]] = {}
    _index_pending: Set[Path] = set()
    _index_last_saved_at = 0.0

    @classmethod
    @contextmanager
    def _timed_storage_lock(cls, operation: str, *, quiet: bool = False) -> Iterator[None]:
        """记录锁等待与持有时间，让主循环等待后台清理的原因可见。"""
        started_at = time.perf_counter()
        thread_name = threading.current_thread().name
        previous_operation = cls._storage_operation
        waiting_owner = previous_operation or "空闲"
        if not quiet:
            logger.debug(f"存储锁等待开始: operation={operation} thread={thread_name} owner={waiting_owner}")
        with cls._storage_lock:
            acquired_at = time.perf_counter()
            wait_seconds = acquired_at - started_at
            # 获取锁后再读取，兼容同线程 RLock 的嵌套调用。
            previous_operation = cls._storage_operation
            cls._storage_operation = f"{operation} thread={thread_name}"
            log = logger.warning if wait_seconds >= 0.5 else logger.debug
            if not quiet or wait_seconds >= 0.5:
                log(
                    f"存储锁已获取: operation={operation} thread={thread_name} "
                    f"等待={wait_seconds:.3f}s waiting_owner={waiting_owner}"
                )
            try:
                yield
            finally:
                hold_seconds = time.perf_counter() - acquired_at
                cls._storage_operation = previous_operation
                log = logger.warning if hold_seconds >= 0.5 else logger.debug
                if not quiet or hold_seconds >= 0.5:
                    log(f"存储锁操作结束: operation={operation} thread={thread_name} 持有={hold_seconds:.3f}s")

    @classmethod
    @contextmanager
    def collect_image_assets(cls) -> Iterator[Dict[Path, bytes]]:
        """在内存中收集本次预览图片，与 JSON 一起交给后台线程落盘。"""
        assets: Dict[Path, bytes] = {}
        token = _image_assets.set(assets)
        try:
            yield assets
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
    @timed_operation(logger, "preview.submit")
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
    @timed_operation(logger, "preview.cleanup_orphan_images")
    def cleanup_orphan_images(cls) -> int:
        """按增量引用索引清理图片，只核对实际引用图片的记录是否被删除。"""
        with cls._timed_storage_lock("cleanup_orphan_images"):
            if not cls._IMAGE_DIR.exists():
                return 0
            with log_operation(logger, "preview.scan_image_candidates", directory=str(cls._IMAGE_DIR)) as stats:
                candidates = [
                    path
                    for path in cls._IMAGE_DIR.iterdir()
                    if cls._CACHE_IMAGE_NAME_PATTERN.fullmatch(path.name) and path.is_file() and not path.is_symlink()
                ]
                stats["candidates"] = len(candidates)
            if not candidates:
                return 0
            cls._ensure_image_index()
            # 历史记录视为不可变；无需遍历十万份日志，只检查持有图片引用的路径。
            for record in list(cls._images_by_preview):
                try:
                    record.stat()
                except FileNotFoundError:
                    cls._release_record_index(record)
            cls._persist_image_index(force=True)
            deleted_count = 0
            deleted_bytes = 0
            with log_operation(logger, "preview.delete_orphan_images", candidates=len(candidates)) as stats:
                # 只删除专用预览缓存，绝不触碰聊天图片或表情原件。
                for path in candidates:
                    if path.name in cls._previews_by_image:
                        continue
                    size = path.stat().st_size
                    path.unlink()
                    deleted_count += 1
                    deleted_bytes += size
                stats.update(deleted=deleted_count, bytes=deleted_bytes)
            if deleted_count:
                logger.info(
                    f"Prompt 孤立图片清理完成: 删除 {deleted_count} 张，释放 {deleted_bytes / (1024 * 1024):.2f} MiB"
                )
            return deleted_count

    @classmethod
    @timed_operation(logger, "preview.write_task")
    def _write_task(cls, task: _PreviewWriteTask) -> None:
        """写入单个预览文件并执行超量清理。"""

        queue_seconds = time.perf_counter() - task.enqueued_at
        log = logger.warning if queue_seconds >= 0.5 else logger.debug
        log(
            f"预览写入出队: path={task.file_path} 排队={queue_seconds:.3f}s "
            f"剩余队列={cls._write_queue.qsize()}"
        )
        with cls._timed_storage_lock(f"preview_write:{task.file_path}"):
            cls._write_task_locked(task)

    @classmethod
    def _write_task_locked(cls, task: _PreviewWriteTask) -> None:
        cls._write_record_locked(task.file_path, task.content, task.image_assets)
        cls._trim_overflow(task.chat_dir)
        cls._persist_image_index()

    @classmethod
    @timed_operation(logger, "preview.write_record_file")
    def write_record_file(cls, file_path: Path, content: str, image_assets: Dict[Path, bytes]) -> None:
        """同步原子更新记录，与预览写入和图片清理共用存储锁。"""
        with cls._timed_storage_lock(f"record_write:{file_path}"):
            cls._write_record_locked(file_path, content, image_assets)
            cls._persist_image_index()

    @classmethod
    @timed_operation(logger, "preview.write_record_locked")
    def _write_record_locked(cls, file_path: Path, content: str, image_assets: Dict[Path, bytes]) -> None:
        cls._ensure_image_index()
        with log_operation(logger, "preview.write_images", path=str(file_path), image_count=len(image_assets)):
            # 图片与记录串行落盘；即使清理先于排队中的记录发生，也会重新写回所需图片。
            for path, content_bytes in image_assets.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_bytes(content_bytes)
        with log_operation(logger, "preview.write_json", path=str(file_path), chars=len(content)):
            # 先写临时文件再改名，避免 WebUI 读到只写了一半的 JSON。
            file_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = file_path.with_name(f".{file_path.name}.tmp")
            temporary_path.write_text(content, encoding="utf-8")
            # 先持久化引用，再发布记录。进程中断最多留下多余引用，不能漏记而误删图片。
            previous_names = cls._images_by_preview.get(file_path, set()).copy()
            cls._index_preview(file_path, content)
            stat = temporary_path.stat()
            cls._record_signatures[file_path] = (stat.st_mtime_ns, stat.st_size)
            cls._index_pending.add(file_path)
            cls._persist_image_index(force=True)
            os.replace(temporary_path, file_path)
            current_names = set(cls._IMAGE_NAME_PATTERN.findall(content))
            if previous_names - current_names:
                # 覆盖失败时旧引用仍被保留；只有新文件发布成功后才释放旧引用。
                cls._release_record_index(file_path)
                cls._index_preview(file_path, content)
                cls._record_signatures[file_path] = (stat.st_mtime_ns, stat.st_size)
                cls._index_pending.add(file_path)
                cls._persist_image_index(force=True)

    @classmethod
    def append_record_event(cls, file_path: Path, content: str, image_assets: Dict[Path, bytes]) -> None:
        """追加一行事件，旧事件保持原样；持久引用先于事件写入，防止漏记图片。"""
        with cls._timed_storage_lock(f"event_append:{file_path}"):
            cls._ensure_image_index()
            file_path.parent.mkdir(parents=True, exist_ok=True)
            for path, content_bytes in image_assets.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_bytes(content_bytes)
            cls._index_preview(file_path, content)
            # 事件流只追加，因此引用需要取并集。
            cls._record_signatures[file_path] = (time.time_ns(), 0)
            cls._index_pending.add(file_path)
            cls._persist_image_index(force=True)
            with file_path.open("a", encoding="utf-8") as stream:
                stream.write(content + "\n")

    @classmethod
    def delete_record_file(cls, file_path: Path) -> None:
        """删除日志并同步持久索引，供记录保留数量限制使用。"""
        with cls._timed_storage_lock(f"record_delete:{file_path}"):
            cls._ensure_image_index()
            file_path.unlink(missing_ok=True)
            cls._release_record_index(file_path)
            cls._persist_image_index(force=True)

    @classmethod
    def _release_record_index(cls, path: Path) -> None:
        """更新记录前移除旧索引；孤立图片交给巡检回收，避免删除新记录仍需的图片。"""
        cls._record_signatures.pop(path, None)
        cls._index_pending.add(path)
        for name in cls._images_by_preview.pop(path, set()):
            previews = cls._previews_by_image[name]
            previews.discard(path)
            if not previews:
                del cls._previews_by_image[name]

    @classmethod
    def _index_preview(cls, path: Path, content: str) -> None:
        # 哈希文件名在相对路径、绝对路径和 file URI 中一致，也兼容旧 HTML/TXT 预览。
        names = set(cls._IMAGE_NAME_PATTERN.findall(content))
        if not names:
            return
        cls._images_by_preview.setdefault(path, set()).update(names)
        for name in names:
            cls._previews_by_image.setdefault(name, set()).add(path)

    @classmethod
    def _ensure_image_index(cls) -> None:
        """启动只加载持久索引；没有缓存时才全量读取，外部导入需显式重建。"""
        if cls._image_index_ready:
            return
        cls._images_by_preview.clear()
        cls._previews_by_image.clear()
        cls._record_signatures.clear()
        cls._index_pending.clear()
        has_cache = (cls._BASE_DIR / ".image-reference-index.sqlite3").exists() or (cls._BASE_DIR / ".image-reference-index").exists()
        cls._load_image_index()
        if not has_cache:
            cls._refresh_image_index()
        cls._image_index_ready = True
        cls._persist_image_index(force=True)

    @classmethod
    def _restore_index_record(cls, relative_path: str, mtime_ns: int, size: int, names: object) -> None:
        """缓存只接受目录内相对路径与专用图片名，损坏数据报错并中止清理。"""
        relative = Path(relative_path)
        if relative.drive or relative.is_absolute() or ".." in relative.parts or relative.suffix not in {".json", ".jsonl", ".html", ".txt"}:
            raise ValueError(f"图片引用索引包含无效日志路径: {relative_path}")
        if not isinstance(mtime_ns, int) or not isinstance(size, int) or not isinstance(names, list):
            raise ValueError(f"图片引用索引签名无效: {relative_path}")
        if any(not isinstance(name, str) or not cls._CACHE_IMAGE_NAME_PATTERN.fullmatch(name) for name in names):
            raise ValueError(f"图片引用索引图片名无效: {relative_path}")
        path = cls._BASE_DIR / relative
        cls._record_signatures[path] = (mtime_ns, size)
        if names:
            cls._images_by_preview[path] = set(names)
            for name in names:
                cls._previews_by_image.setdefault(name, set()).add(path)

    @classmethod
    def _load_image_index(cls) -> None:
        cache_path = cls._BASE_DIR / ".image-reference-index.sqlite3"
        if cache_path.exists():
            with log_operation(logger, "preview.load_image_index", report=True, path=str(cache_path)) as stats:
                # 图片清理只需有引用的记录；无图片日志留在数据库，不逐条构造内存对象。
                # 引用记录的格式/内容损坏时明确报错，不能依据不完整缓存清理图片。
                with closing(sqlite3.connect(cache_path)) as connection:
                    version = connection.execute("PRAGMA user_version").fetchone()[0]
                    if version != cls._INDEX_SCHEMA_VERSION:
                        raise ValueError(f"图片引用索引数据库版本不支持: {version}")
                    for relative_path, mtime_ns, size, image_names in connection.execute(
                        "SELECT path, mtime_ns, size, image_names FROM records "
                        "WHERE image_names IS NOT NULL AND image_names != '[]'"
                    ):
                        names = json.loads(image_names) if image_names is not None else []
                        cls._restore_index_record(relative_path, mtime_ns, size, names)
                stats["files"] = len(cls._record_signatures)
            return

        # 仅首次升级读取旧 JSON 缓存，迁移后不再全量序列化它。
        legacy_path = cls._BASE_DIR / ".image-reference-index"
        if not legacy_path.exists():
            return
        with log_operation(logger, "preview.migrate_image_index", report=True, path=str(legacy_path)) as stats:
            payload = json.loads(legacy_path.read_text(encoding="utf-8"))
            if payload["version"] != cls._INDEX_SCHEMA_VERSION:
                raise ValueError(f"图片引用索引缓存版本不支持: {payload['version']}")
            for relative_path, (mtime_ns, size, names) in payload["records"].items():
                cls._restore_index_record(relative_path, mtime_ns, size, names)
            cls._index_pending.update(cls._record_signatures)
            stats["files"] = len(cls._record_signatures)

    @classmethod
    def _refresh_image_index(cls) -> None:
        """复用 scandir 的文件属性，只读取签名变化的文件，扫描完整后再更新引用。"""
        with log_operation(logger, "preview.refresh_image_index", report=True, directory=str(cls._BASE_DIR)) as stats:
            inventory: Dict[Path, Tuple[int, int]] = {}
            updates: Dict[Path, Tuple[Tuple[int, int], Set[str]]] = {}
            directories = [cls._BASE_DIR]
            started_at = time.perf_counter()
            next_progress = started_at + 10
            while directories:
                directory = directories.pop()
                if directory == cls._BASE_DIR and not directory.exists():
                    continue
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False):
                            directories.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False) or not entry.name.endswith((".json", ".jsonl", ".html", ".txt")):
                            continue
                        path = Path(entry.path)
                        stat = entry.stat(follow_symlinks=False)
                        signature = (stat.st_mtime_ns, stat.st_size)
                        inventory[path] = signature
                        if cls._record_signatures.get(path) != signature:
                            content = path.read_text(encoding="utf-8")
                            after_read = path.stat()
                            if (after_read.st_mtime_ns, after_read.st_size) != signature:
                                raise RuntimeError(f"图片引用扫描期间日志被修改，中止本轮清理: {path}")
                            updates[path] = (signature, set(cls._IMAGE_NAME_PATTERN.findall(content)))
                        now = time.perf_counter()
                        if now >= next_progress:
                            logger.info(
                                f"图片引用扫描进度: files={len(inventory)} changed={len(updates)} "
                                f"耗时={now - started_at:.1f}s path={path}"
                            )
                            next_progress = now + 10
            # 完整扫描失败时保留旧索引，调用方不会进入图片删除阶段。
            removed = cls._record_signatures.keys() - inventory.keys()
            for path in removed:
                cls._release_record_index(path)
            for path, (_, names) in updates.items():
                cls._release_record_index(path)
                if names:
                    cls._images_by_preview[path] = names
                    for name in names:
                        cls._previews_by_image.setdefault(name, set()).add(path)
            cls._record_signatures = inventory
            stats.update(files=len(inventory), changed=len(updates), removed=len(removed))

    @classmethod
    def rebuild_image_index(cls) -> None:
        """手动导入或修改历史日志后，显式核对磁盘并保存引用索引。"""
        with cls._timed_storage_lock("rebuild_image_index"):
            cls._ensure_image_index()
            cls._refresh_image_index()
            cls._persist_image_index(force=True)

    @classmethod
    def _persist_image_index(cls, *, force: bool = False) -> None:
        """按分钟批量提交变化记录；事务成功后才清除待保存集合。"""
        if not cls._image_index_ready or not cls._index_pending:
            return
        now = time.monotonic()
        if not force and now - cls._index_last_saved_at < cls._INDEX_PERSIST_INTERVAL_SECONDS:
            return
        cache_path = cls._BASE_DIR / ".image-reference-index.sqlite3"
        with log_operation(logger, "preview.persist_image_index", changed=len(cls._index_pending)) as stats:
            upserts: List[Tuple[str, int, int, Optional[str]]] = []
            deletes: List[Tuple[str]] = []
            # 仅遍历本轮变更，更新时间与大小相同的十万条记录完全不参与落盘。
            for path in cls._index_pending:
                relative_path = path.relative_to(cls._BASE_DIR).as_posix()
                signature = cls._record_signatures.get(path)
                if signature is None:
                    deletes.append((relative_path,))
                else:
                    names = cls._images_by_preview.get(path)
                    image_names = json.dumps(sorted(names), separators=(",", ":")) if names else None
                    upserts.append((relative_path, *signature, image_names))
            cls._BASE_DIR.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(cache_path)) as connection:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    version = connection.execute("PRAGMA user_version").fetchone()[0]
                    if version == 0:
                        connection.execute(
                            "CREATE TABLE records (path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, "
                            "size INTEGER NOT NULL, image_names TEXT)"
                        )
                        connection.execute(f"PRAGMA user_version = {cls._INDEX_SCHEMA_VERSION}")
                    elif version != cls._INDEX_SCHEMA_VERSION:
                        raise ValueError(f"图片引用索引数据库版本不支持: {version}")
                    connection.executemany(
                        "INSERT INTO records (path, mtime_ns, size, image_names) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns, "
                        "size=excluded.size, image_names=excluded.image_names",
                        upserts,
                    )
                    connection.executemany("DELETE FROM records WHERE path = ?", deletes)
            cls._index_pending.clear()
            cls._index_last_saved_at = now
            stats.update(updated=len(upserts), deleted=len(deletes))

    @classmethod
    def _release_preview_images(cls, path: Path) -> None:
        # 删除日志仅更新引用，图片留给巡检回收，避免影响排队中的新记录。
        cls._release_record_index(path)

    @classmethod
    @timed_operation(logger, "preview.clear_stage")
    def clear_stage(cls, stage_dir: Path) -> int:
        """与后台写入互斥地清空推理类型，并释放不再被其他记录引用的图片。"""
        stage_dir = stage_dir.resolve()
        with cls._timed_storage_lock(f"clear_stage:{stage_dir}"):
            if not stage_dir.is_relative_to(cls._BASE_DIR) or stage_dir == cls._BASE_DIR:
                raise ValueError("无效的推理过程类型路径")
            if not stage_dir.exists():
                return 0
            cls._ensure_image_index()
            with log_operation(logger, "preview.scan_stage", directory=str(stage_dir)) as stats:
                paths = [path for path in stage_dir.rglob("*") if path.is_file()]
                stats["files"] = len(paths)
            with log_operation(logger, "preview.delete_stage", directory=str(stage_dir), files=len(paths)):
                shutil.rmtree(stage_dir)
            with log_operation(logger, "preview.release_stage_images", files=len(paths)):
                for path in paths:
                    cls._release_preview_images(path)
            cls._persist_image_index(force=True)
            return len(paths)

    @classmethod
    @timed_operation(logger, "preview.trim_overflow")
    def _trim_overflow(cls, chat_dir: Path) -> None:
        """超过阈值时删除多出来的预览文件，最终恰好保留阈值数量。

        文件名就是毫秒时间戳，按文件名排序等价于按时间排序，因此不需要为每个
        文件查询修改时间；os.scandir 的 DirEntry 已带类型信息，也无需额外 stat。
        """

        with log_operation(logger, "preview.scan_overflow", directory=str(chat_dir)) as stats:
            max_preview_groups = cls._get_max_preview_groups_per_chat()
            try:
                with os.scandir(chat_dir) as entries:
                    names = [entry.name for entry in entries if entry.is_file() and entry.name.endswith(".json")]
            except FileNotFoundError:
                return

            stats.update(files=len(names), limit=max_preview_groups)
        if len(names) <= max_preview_groups:
            return

        cls._ensure_image_index()
        with log_operation(logger, "preview.delete_overflow", directory=str(chat_dir), records=len(names) - max_preview_groups):
            names.sort()
            for name in names[: len(names) - max_preview_groups]:
                try:
                    (chat_dir / name).unlink()
                except FileNotFoundError:
                    pass
                cls._release_preview_images(chat_dir / name)
            cls._persist_image_index(force=True)

    @classmethod
    def _get_max_preview_groups_per_chat(cls) -> int:
        try:
            from src.config.config import global_config

            configured_limit = global_config.log.maisaka_prompt_preview_limit
            return max(1, int(configured_limit or cls._DEFAULT_MAX_PREVIEW_GROUPS_PER_CHAT))
        except Exception:
            return cls._DEFAULT_MAX_PREVIEW_GROUPS_PER_CHAT
