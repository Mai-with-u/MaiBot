"""有界内存诊断；只记录计数和分配位置，不记录消息、配置或对象内容。"""

from itertools import islice
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional
import asyncio
import ctypes
import json
import logging
import os
import sys
import threading
import time
import tracemalloc


def process_memory() -> Dict[str, int]:
    """Linux 的匿名 RSS 与 Windows 的私有提交量含义不同，分别命名。"""
    if sys.platform == "win32":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "peak_rss", "rss", "peak_paged", "paged", "peak_nonpaged", "nonpaged",
                    "pagefile", "peak_pagefile", "private",
                )
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        info = Counters()
        info.cb = ctypes.sizeof(info)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(info), info.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return {"rss_bytes": info.rss, "private_commit_bytes": info.private, "peak_rss_bytes": info.peak_rss}
    if sys.platform.startswith("linux"):
        fields = {}
        names = {"VmRSS:": "rss_bytes", "RssAnon:": "anonymous_rss_bytes", "VmSwap:": "swap_bytes"}
        for line in Path("/proc/self/status").read_text().splitlines():
            values = line.split()
            if values[0] in names:
                fields[names[values[0]]] = int(values[1]) * 1024
            elif values[0] == "Threads:":
                fields["process_threads"] = int(values[1])
        return fields
    raise NotImplementedError("Memory diagnostics supports Windows and Linux")


def database_counts() -> Dict[str, Any]:
    """只查看已加载的组件；不导入数据库、不建连接、不执行 SQL。"""
    result: Dict[str, Any] = {}
    database = sys.modules.get("src.common.database.database")
    if database is not None and hasattr(database, "engine"):
        result["main_pool_status"] = database.engine.pool.status()
    registry = sys.modules.get("src.A_memorix.runtime_registry")
    if registry is not None:
        kernel = registry.get_runtime_kernel()
        if kernel is not None and kernel.metadata_store is not None:
            manager = kernel.metadata_store._connection_manager
            if manager is not None:
                # 诊断不应阻塞业务连接锁；本轮拿不到锁就明确标记。
                if manager._lock.acquire(blocking=False):
                    try:
                        result["memory_connections"] = len(manager._connections)
                        result["memory_connection_owners"] = [
                            {"thread_id": thread.ident, "alive": thread.is_alive()}
                            for thread in islice(manager._connections, 64)
                        ]
                        result["owners_truncated"] = len(manager._connections) > 64
                    finally:
                        manager._lock.release()
                else:
                    result["memory_connections_busy"] = True
    return result


def runtime_counts() -> Dict[str, Any]:
    """在主循环线程读取所属对象；图片统计有扫描上限，不是全堆大小。"""
    result: Dict[str, Any] = {"main_loop_tasks": len(asyncio.all_tasks())}
    chats = sys.modules.get("src.chat.message_receive.chat_manager")
    if chats is not None:
        result["chat_sessions"] = len(chats.chat_manager.sessions)
    flows = sys.modules.get("src.chat.heart_flow.heartflow_manager")
    if flows is None:
        return result
    runtimes = flows.heartflow_manager.heartflow_chat_list
    result.update(active_runtimes=len(runtimes), sampled_runtimes=0, history_items=0,
                  cached_messages=0, queued_turns=0, sampled_image_bytes=0, sampled_components=0)
    message_module = sys.modules.get("src.maisaka.context.messages")
    components = sys.modules.get("src.common.data_models.message_component_data_model")
    scanned_messages = 0
    for runtime in islice(runtimes.values(), 256):
        result["sampled_runtimes"] += 1
        result["history_items"] += len(runtime._chat_history)
        result["cached_messages"] += len(runtime.message_cache)
        result["queued_turns"] += runtime._internal_turn_queue.qsize()
        if message_module is None or components is None:
            continue
        for message in islice(runtime._chat_history, max(0, 512 - scanned_messages)):
            scanned_messages += 1
            if not isinstance(message, message_module.ContentContextMessage):
                continue
            for component in islice(message.media_sequence.components, max(0, 2048 - result["sampled_components"])):
                result["sampled_components"] += 1
                if isinstance(component, (components.ImageComponent, components.EmojiComponent)):
                    result["sampled_image_bytes"] += len(component.binary_data)
    result["counts_truncated"] = len(runtimes) > 256
    result["image_sample_limited"] = scanned_messages >= 512 or result["sampled_components"] >= 2048
    return result


class MemoryDiagnostics:
    def __init__(self, directory: Path, loop: asyncio.AbstractEventLoop, *, interval: float = 60,
                 deep: bool = False, deep_interval: float = 600, max_bytes: int = 5 * 1024 * 1024,
                 backups: int = 3) -> None:
        self.directory = directory
        self.loop = loop
        self.interval = max(1.0, interval)
        self.deep = deep
        self.deep_interval = max(60.0, deep_interval)
        self.max_bytes = max_bytes
        self.backups = backups
        self.stopped = threading.Event()
        self.pending = threading.Event()
        self.latest: Dict[str, Any] = {}
        self.thread = threading.Thread(target=self._run, name="memory-diagnostics", daemon=True)
        self.previous: Optional[tracemalloc.Snapshot] = None
        self.owns_tracer = False
        self.last_deep = float("-inf")

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=1)

    def _collect_runtime(self) -> None:
        started = time.monotonic()
        try:
            if not self.stopped.is_set():
                counts = runtime_counts()
                self.latest = {"sampled_at": time.time(), **counts,
                               "collection_ms": round((time.monotonic() - started) * 1000, 3)}
        except Exception as exc:
            self.latest = {"sampled_at": time.time(), "error_type": type(exc).__name__}
        finally:
            self.pending.clear()

    def _deep_sample(self) -> Dict[str, Any]:
        current, peak = tracemalloc.get_traced_memory()
        snapshot = tracemalloc.take_snapshot()
        statistics = (snapshot.compare_to(self.previous, "lineno") if self.previous is not None
                      else snapshot.statistics("lineno"))
        rows = []
        for stat in islice(statistics, 20):
            frame = stat.traceback[0]
            # 路径相对化，避免把用户目录带入上报日志；不读取源码行。
            path = Path(frame.filename)
            try:
                filename = str(path.relative_to(Path.cwd()))
            except ValueError:
                filename = path.name
            row = {"file": filename, "line": frame.lineno, "bytes": stat.size, "count": stat.count}
            if isinstance(stat, tracemalloc.StatisticDiff):
                row.update(delta_bytes=stat.size_diff, delta_count=stat.count_diff)
            rows.append(row)
        self.previous = snapshot
        return {"traced_bytes": current, "traced_peak_bytes": peak,
                "tracer_overhead_bytes": tracemalloc.get_tracemalloc_memory(), "locations": rows}

    def _run(self) -> None:
        handler: Optional[RotatingFileHandler] = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(self.directory / "memory.jsonl", maxBytes=self.max_bytes,
                                          backupCount=self.backups, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            if self.deep and not tracemalloc.is_tracing():
                tracemalloc.start(1)
                self.owns_tracer = True
            while not self.stopped.is_set():
                started = time.monotonic()
                record: Dict[str, Any] = {"timestamp": time.time(), "pid": os.getpid(), "schema": 1}
                try:
                    if not self.pending.is_set() and not self.loop.is_closed():
                        self.pending.set()
                        self.loop.call_soon_threadsafe(self._collect_runtime)
                    record.update(process_memory(), python_threads=threading.active_count(),
                                  databases=database_counts(), runtime=self.latest, deep_enabled=self.deep)
                    if self.deep and started - self.last_deep >= self.deep_interval:
                        self.last_deep = started
                        record["allocations"] = self._deep_sample()
                except Exception as exc:
                    record["error_type"] = type(exc).__name__
                record["collection_ms"] = round((time.monotonic() - started) * 1000, 3)
                handler.emit(logging.LogRecord("memory_diagnostics", logging.INFO, "", 0,
                                              json.dumps(record, ensure_ascii=True), (), None))
                if self.stopped.wait(self.interval):
                    break
        except Exception:
            # 诊断故障只报告一次，不应让主程序无法启动或制造错误日志风暴。
            logging.getLogger(__name__).exception("内存诊断线程停止，业务继续运行")
        finally:
            self.previous = None
            if self.owns_tracer:
                tracemalloc.stop()
            if handler is not None:
                handler.close()


_diagnostics: Optional[MemoryDiagnostics] = None


def start_memory_diagnostics() -> None:
    global _diagnostics
    from src.config.config import global_config
    from .logger import LOG_DIR

    if _diagnostics is not None or not global_config.log.memory_diagnostics_enabled:
        return
    _diagnostics = MemoryDiagnostics(
        LOG_DIR / "memory_diagnostics", asyncio.get_running_loop(),
        interval=global_config.log.memory_diagnostics_interval_seconds,
        deep=global_config.log.memory_diagnostics_deep_enabled,
    )
    try:
        _diagnostics.start()
    except RuntimeError:
        _diagnostics = None
        logging.getLogger(__name__).exception("无法启动内存诊断线程，业务继续运行")


def stop_memory_diagnostics() -> None:
    global _diagnostics
    if _diagnostics is not None:
        _diagnostics.stop()
        if not _diagnostics.thread.is_alive():
            _diagnostics = None
