"""有界内存诊断；只记录计数和分配位置，不记录消息、配置或对象内容。"""

from heapq import nlargest
from itertools import islice
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import asyncio
import ctypes
import hashlib
import json
import logging
import os
import re
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
        names = {"VmRSS:": "rss_bytes", "RssAnon:": "anonymous_rss_bytes", "VmSwap:": "swap_bytes",
                 "RssFile:": "file_rss_bytes", "RssShmem:": "shared_memory_rss_bytes",
                 "VmHWM:": "peak_rss_bytes", "VmSize:": "virtual_bytes", "VmPTE:": "page_table_bytes"}
        for line in Path("/proc/self/status").read_text().splitlines():
            values = line.split()
            if values[0] in names:
                fields[names[values[0]]] = int(values[1]) * 1024
            elif values[0] == "Threads:":
                fields["process_threads"] = int(values[1])
        return fields
    raise NotImplementedError("Memory diagnostics supports Windows and Linux")


# smaps 中未出现的可选字段不补零，避免把“不支持”误认为“没有占用”。
_SMAP_FIELDS = {
    "Size:": "virtual_bytes", "Rss:": "rss_bytes", "Pss:": "pss_bytes",
    "Private_Clean:": "private_clean_bytes", "Private_Dirty:": "private_dirty_bytes",
    "Shared_Clean:": "shared_clean_bytes", "Shared_Dirty:": "shared_dirty_bytes",
    "Anonymous:": "anonymous_bytes", "Swap:": "swap_bytes", "SwapPss:": "swap_pss_bytes",
    "AnonHugePages:": "anonymous_huge_bytes", "Pss_Anon:": "pss_anonymous_bytes",
    "Pss_File:": "pss_file_bytes", "Pss_Shmem:": "pss_shared_memory_bytes",
    "Private_Hugetlb:": "private_hugetlb_bytes", "Shared_Hugetlb:": "shared_hugetlb_bytes",
}
_MAP_HEADER = re.compile(r"^[0-9a-fA-F]+-[0-9a-fA-F]+\s")


def _smaps_values(lines: Iterable[str]) -> Dict[str, int]:
    values: Dict[str, int] = {}
    for line in lines:
        parts = line.split()
        if parts and parts[0] in _SMAP_FIELDS:
            if len(parts) != 3 or parts[2] != "kB":
                raise ValueError("Unexpected smaps memory unit")
            values[_SMAP_FIELDS[parts[0]]] = int(parts[1]) * 1024
    if "rss_bytes" not in values:
        raise ValueError("Missing smaps RSS")
    return values


def _mapping_category(name: str, permissions: str) -> str:
    if name == "[heap]":
        return "heap"
    if name.startswith("[stack"):
        return "stack"
    if name.startswith(("/dev/shm/", "/memfd:", "memfd:", "/SYSV", "[anon_shmem:")):
        return "shared_memory"
    if not name or name.startswith("[anon:"):
        return "anonymous_shared" if permissions.endswith("s") else "anonymous_private"
    if name.startswith("["):
        return "kernel_special"
    return "file_shared" if permissions.endswith("s") else "file_private"


def linux_memory_maps(*, detailed: bool) -> Dict[str, Any]:
    """流式汇总映射；只保留 RSS 最大的 20 段，不记录地址和完整路径。"""
    if not sys.platform.startswith("linux"):
        return {"supported": False, "reason": "linux_procfs_only"}
    result: Dict[str, Any] = {"supported": True, "sampled_at": time.time()}
    # 两个来源独立报错；缺失 rollup 时仍保留 smaps 的真实结果，而不是伪造零值。
    try:
        with Path("/proc/self/smaps_rollup").open() as source:
            result["rollup"] = _smaps_values(source)
    except (OSError, ValueError) as exc:
        result["rollup_error"] = {"type": type(exc).__name__, "errno": exc.errno if isinstance(exc, OSError) else None}
    if not detailed:
        return result
    categories: Dict[str, Dict[str, int]] = {}
    top = []
    count = 0
    current: Optional[Dict[str, Any]] = None

    def finish() -> None:
        nonlocal count, top
        if current is None:
            return
        if "rss_bytes" not in current:
            raise ValueError("Missing mapping RSS")
        count += 1
        totals = categories.setdefault(current["category"], {"mapping_count": 0})
        totals["mapping_count"] += 1
        for key in _SMAP_FIELDS.values():
            if key in current:
                totals[key] = totals.get(key, 0) + current[key]
        top = nlargest(20, [*top, current], key=lambda item: item["rss_bytes"])

    try:
        with Path("/proc/self/smaps").open() as source:
            for line in source:
                if _MAP_HEADER.match(line):
                    finish()
                    parts = line.split(maxsplit=5)
                    name = parts[5].strip() if len(parts) == 6 else ""
                    current = {
                        "mapping_id": hashlib.sha256(line.rstrip().encode()).hexdigest()[:16],
                        "category": _mapping_category(name, parts[1]), "permissions": parts[1],
                        "deleted": name.endswith(" (deleted)"),
                    }
                    if current["category"] in ("file_private", "file_shared"):
                        current["file_basename"] = Path(name.removesuffix(" (deleted)")).name[:160]
                elif current is not None:
                    parts = line.split()
                    if parts and parts[0] in _SMAP_FIELDS:
                        if len(parts) != 3 or parts[2] != "kB":
                            raise ValueError("Unexpected smaps memory unit")
                        current[_SMAP_FIELDS[parts[0]]] = int(parts[1]) * 1024
        finish()
        if not count:
            raise ValueError("No smaps mappings")
        result.update(categories=categories, largest_mappings=top, mapping_count=count,
                      largest_mappings_truncated=count > 20)
    except (OSError, ValueError) as exc:
        result["smaps_error"] = {"type": type(exc).__name__, "errno": exc.errno if isinstance(exc, OSError) else None}
    return result


def diagnostic_checkpoint() -> Dict[str, Any]:
    """阶段边界的进程读数，不等于诊断器独占内存（业务线程仍在运行）。"""
    result: Dict[str, Any] = {"sampled_at": time.time(), **process_memory()}
    if tracemalloc.is_tracing():
        current, peak = tracemalloc.get_traced_memory()
        result.update(traced_bytes=current, traced_peak_bytes=peak,
                      tracer_overhead_bytes=tracemalloc.get_tracemalloc_memory())
    return result


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
        # 只保留按行聚合的数字，不让数百万条 trace 的完整快照常驻。
        self.previous: Optional[Dict[Tuple[str, int], Tuple[int, int]]] = None
        self.baseline: Optional[Dict[Tuple[str, int], Tuple[int, int]]] = None
        self.previous_sampled_at: Optional[float] = None
        self.baseline_sampled_at: Optional[float] = None
        self.previous_maps: Optional[Dict[str, Dict[str, int]]] = None
        self.last_maps = float("-inf")
        self.previous_maps_sampled_at: Optional[float] = None
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

    def _deep_sample(self, phases: Dict[str, Any]) -> Dict[str, Any]:
        started = time.monotonic()
        sampled_at = time.time()
        current, peak = tracemalloc.get_traced_memory()
        snapshot = tracemalloc.take_snapshot()
        phases["after_snapshot"] = diagnostic_checkpoint()
        phases["snapshot_ms"] = round((time.monotonic() - started) * 1000, 3)
        aggregation_started = time.monotonic()
        statistics = snapshot.statistics("lineno")
        # 路径相对化，避免把用户目录带入上报日志；不读取源码行。
        # 内部键保留原路径，避免同名文件在做差时被合并。
        totals = {(stat.traceback[0].filename, stat.traceback[0].lineno): (stat.size, stat.count)
                  for stat in statistics}
        phases["after_aggregation"] = diagnostic_checkpoint()
        del statistics, snapshot
        phases["after_snapshot_release"] = diagnostic_checkpoint()
        phases["aggregation_ms"] = round((time.monotonic() - aggregation_started) * 1000, 3)

        def row(key: Tuple[str, int], reference: Optional[Dict[Tuple[str, int], Tuple[int, int]]] = None) -> Dict[str, Any]:
            path = Path(key[0])
            try:
                filename = str(path.relative_to(Path.cwd()))
            except ValueError:
                filename = path.name
            size, count = totals.get(key, (0, 0))
            value = {"file": filename, "line": key[1], "bytes": size, "count": count}
            if reference is not None:
                old_size, old_count = reference.get(key, (0, 0))
                value.update(delta_bytes=size - old_size, delta_count=count - old_count)
            return value

        def changes(reference: Dict[Tuple[str, int], Tuple[int, int]], *, absolute: bool) -> List[Dict[str, Any]]:
            # 包含已经完全释放的位置；排行有界，避免保存完整差异对象列表。
            keys = totals.keys() | reference.keys()
            def score(key: Tuple[str, int]) -> int:
                delta = totals.get(key, (0, 0))[0] - reference.get(key, (0, 0))[0]
                return abs(delta) if absolute else delta
            candidates = keys if absolute else (key for key in keys if score(key) > 0)
            return [row(key, reference) for key in nlargest(20, candidates, key=score)]

        largest = [row(key) for key in nlargest(20, totals, key=lambda key: totals[key][0])]
        result = {
            "sampled_at": sampled_at, "previous_sampled_at": self.previous_sampled_at,
            "baseline_sampled_at": self.baseline_sampled_at if self.baseline is not None else sampled_at,
            "traced_bytes": current, "traced_peak_bytes": peak,
            "traceback_limit": tracemalloc.get_traceback_limit(),
            "locations": changes(self.previous, absolute=True) if self.previous is not None else largest,
            "largest_locations": largest,
            "growing_locations": changes(self.previous, absolute=False) if self.previous is not None else [],
            "baseline_growing_locations": changes(self.baseline, absolute=False) if self.baseline is not None else [],
            "location_count": len(totals), "retained_full_snapshots": 0,
        }
        self.previous = totals
        self.previous_sampled_at = sampled_at
        if self.baseline is None:
            self.baseline = totals
            self.baseline_sampled_at = sampled_at
        result["tracer_overhead_bytes"] = tracemalloc.get_tracemalloc_memory()
        return result

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
                record: Dict[str, Any] = {
                    "timestamp": time.time(), "pid": os.getpid(), "schema": 2,
                    "python_version": sys.version.split()[0], "platform": sys.platform,
                    "tracer_owned": self.owns_tracer,
                }
                try:
                    if not self.pending.is_set() and not self.loop.is_closed():
                        self.pending.set()
                        self.loop.call_soon_threadsafe(self._collect_runtime)
                    record.update(process_memory(), python_threads=threading.active_count(),
                                  databases=database_counts(), runtime=self.latest, deep_enabled=self.deep)
                    phases: Dict[str, Any] = {
                        "before": diagnostic_checkpoint(),
                        "measurement_note": "process_wide_change_includes_concurrent_business_activity",
                    }
                    record["diagnostics"] = phases
                    maps_started = time.monotonic()
                    detailed = started - self.last_maps >= self.deep_interval
                    record["memory_maps"] = linux_memory_maps(detailed=detailed)
                    if detailed:
                        self.last_maps = started
                        categories = record["memory_maps"].get("categories")
                        if categories is not None:
                            if self.previous_maps is not None:
                                record["memory_maps"]["previous_sampled_at"] = self.previous_maps_sampled_at
                                record["memory_maps"]["category_delta_bytes"] = {
                                    category: {
                                        key: categories.get(category, {}).get(key, 0)
                                        - self.previous_maps.get(category, {}).get(key, 0)
                                        for key in (categories.get(category, {}).keys()
                                                    | self.previous_maps.get(category, {}).keys())
                                        if key.endswith("_bytes")
                                    }
                                    for category in categories.keys() | self.previous_maps.keys()
                                }
                            self.previous_maps = categories
                            self.previous_maps_sampled_at = record["memory_maps"]["sampled_at"]
                    phases["maps_ms"] = round((time.monotonic() - maps_started) * 1000, 3)
                    phases["after_maps"] = diagnostic_checkpoint()
                    if self.deep and started - self.last_deep >= self.deep_interval:
                        self.last_deep = started
                        try:
                            record["allocations"] = self._deep_sample(phases)
                        finally:
                            # 函数退出后临时快照/统计对象已释放；不强制 GC 或 trim 干扰现场。
                            phases["after_deep_cleanup"] = diagnostic_checkpoint()

                except Exception as exc:
                    record["error_type"] = type(exc).__name__
                # 在 except 作用域之外读数，避免异常 traceback 继续持有失败的快照。
                if "diagnostics" in record:
                    try:
                        record["diagnostics"]["after"] = diagnostic_checkpoint()
                        record["diagnostics"]["rss_change_bytes"] = (
                            record["diagnostics"]["after"]["rss_bytes"]
                            - record["diagnostics"]["before"]["rss_bytes"]
                        )
                    except Exception as exc:
                        record["diagnostics"]["after_error_type"] = type(exc).__name__
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
            self.baseline = None
            self.previous_maps = None
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
