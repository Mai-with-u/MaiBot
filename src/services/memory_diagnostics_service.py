from __future__ import annotations

from collections import Counter
from itertools import islice
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import asyncio
import gc
import json
import sys
import time
import tracemalloc

try:
    import psutil
except ImportError:  # pragma: no cover - psutil 是可选诊断依赖的兜底
    psutil = None  # type: ignore[assignment]

from src.common.logger import get_logger
from src.config.config import PROJECT_ROOT, global_config
from src.manager.async_task_manager import AsyncTask

logger = get_logger("memory_diagnostics")

BYTES_PER_MB = 1024 * 1024
DEFAULT_TOP_ALLOCATIONS = 20
DEFAULT_HISTORY_LOOP_SAMPLE_SIZE = 50
DEFAULT_FORWARD_COMPONENT_MAX_DEPTH = 5
DEFAULT_JSONL_ROTATED_FILE_KEEP = 5
DEFAULT_TOP_CHILD_PROCESSES = 10
DEFAULT_TASK_AWAIT_CHAIN_LIMIT = 10
DEFAULT_TASK_LOCAL_LIMIT = 64
DEFAULT_TASK_VALUE_MAX_DEPTH = 3
DEFAULT_TASK_VALUE_MAX_ITEMS = 64


def is_memory_diagnostics_enabled() -> bool:
    """判断是否启用内存诊断任务。"""

    return global_config.debug.enable_memory_diagnostics


class MemoryDiagnosticsTask(AsyncTask):
    """周期性采集长时间运行内存诊断指标。"""

    def __init__(self) -> None:
        interval_seconds = max(
            10,
            int(global_config.debug.memory_diagnostics_interval_seconds),
        )
        super().__init__(
            task_name="MemoryDiagnosticsTask",
            wait_before_start=min(30, interval_seconds),
            run_interval=interval_seconds,
        )
        self._last_rss_mb: Optional[float] = None
        self._baseline_tracemalloc_snapshot: Optional[tracemalloc.Snapshot] = None
        self._baseline_rss_mb: Optional[float] = None

        if self._tracemalloc_enabled() and not tracemalloc.is_tracing():
            tracemalloc.start(25)

    async def run(self) -> None:
        """采集一次诊断快照并写入日志。"""

        started_at = time.time()
        try:
            payload = self._collect_snapshot()
        except Exception as exc:
            logger.warning(f"内存诊断快照采集失败: {exc}", exc_info=True)
            return

        payload["collector"] = {
            "duration_ms": round((time.time() - started_at) * 1000, 3),
            "tracemalloc_enabled": self._tracemalloc_enabled(),
        }
        try:
            await asyncio.to_thread(self._write_snapshot, payload)
        except Exception as exc:
            logger.warning(f"内存诊断快照写入失败: {exc}", exc_info=True)

        try:
            self._log_summary(payload)
        except Exception as exc:
            logger.warning(f"内存诊断摘要日志输出失败: {exc}", exc_info=True)

    def _collect_snapshot(self) -> dict[str, Any]:
        errors: list[str] = []
        payload: dict[str, Any] = {
            "timestamp": time.time(),
            "process": self._collect_process_metrics(errors),
            "python": self._collect_python_metrics(errors),
            "asyncio": self._collect_asyncio_metrics(errors),
            "heartflow": self._collect_heartflow_metrics(errors),
            "chat_manager": self._collect_chat_manager_metrics(errors),
            "websocket": self._collect_websocket_metrics(errors),
            "media_tasks": self._collect_media_task_metrics(errors),
            "memory_automation": self._collect_memory_automation_metrics(errors),
            "a_memorix": self._collect_a_memorix_metrics(errors),
        }
        tracemalloc_metrics = self._collect_tracemalloc_metrics(payload["process"], errors)
        if tracemalloc_metrics:
            payload["tracemalloc"] = tracemalloc_metrics
        if errors:
            payload["collector_errors"] = errors
        return payload

    def _collect_process_metrics(self, errors: list[str]) -> dict[str, Any]:
        if psutil is None:
            return {"available": False, "reason": "psutil_not_installed"}

        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            try:
                full_memory_info = process.memory_full_info()
                uss_bytes = getattr(full_memory_info, "uss", 0)
            except Exception:
                uss_bytes = 0
            payload = {
                "available": True,
                "rss_mb": _bytes_to_mb(getattr(memory_info, "rss", 0)),
                "vms_mb": _bytes_to_mb(getattr(memory_info, "vms", 0)),
                "uss_mb": _bytes_to_mb(uss_bytes),
                "thread_count": process.num_threads(),
                "open_files": 0,
                "connections": 0,
            }
            try:
                payload["open_files"] = len(process.open_files())
            except Exception:
                payload["open_files_error"] = True
            try:
                payload["connections"] = _safe_len(process.net_connections())
            except Exception:
                payload["connections_error"] = True
            try:
                payload["handle_count"] = process.num_handles()
            except Exception:
                try:
                    payload["fd_count"] = process.num_fds() if hasattr(process, "num_fds") else 0
                except Exception:
                    payload["fd_count_error"] = True

            child_metrics = _collect_child_process_metrics(process)
            payload["children"] = child_metrics
            payload["process_tree_rss_mb"] = round(
                payload["rss_mb"] + child_metrics.get("rss_mb", 0),
                3,
            )
            payload["process_tree_uss_mb"] = round(
                payload["uss_mb"] + child_metrics.get("uss_mb", 0),
                3,
            )
            payload["process_tree_vms_mb"] = round(
                payload["vms_mb"] + child_metrics.get("vms_mb", 0),
                3,
            )
            return payload
        except Exception as exc:
            errors.append(f"process:{exc}")
            return {"available": False, "reason": str(exc)}

    def _collect_python_metrics(self, errors: list[str]) -> dict[str, Any]:
        try:
            payload = {
                "gc_count": list(gc.get_count()),
                "gc_threshold": list(gc.get_threshold()),
            }
            if self._tracemalloc_enabled():
                payload["object_count"] = len(gc.get_objects())
            return payload
        except Exception as exc:
            errors.append(f"python:{exc}")
            return {}

    @staticmethod
    def _collect_asyncio_metrics(errors: list[str]) -> dict[str, Any]:
        try:
            current_task = asyncio.current_task()
            tasks = list(asyncio.all_tasks())
            task_names = Counter(task.get_name() for task in tasks)
            coro_names = Counter(_task_coro_name(task) for task in tasks)
            interesting_tasks = []
            for task in tasks:
                # 诊断任务自身通常命中 memory 关键字，跳过可避免污染 interesting_tasks。
                if task is current_task:
                    continue
                name = task.get_name()
                coro_name = _task_coro_name(task)
                if _is_interesting_task(name, coro_name):
                    interesting_tasks.append(
                        {
                            "name": name,
                            "coro": coro_name,
                            "done": task.done(),
                            "cancelled": task.cancelled(),
                        }
                    )
            return {
                "task_count": len(tasks),
                "top_task_names": _counter_to_top_list(task_names),
                "top_coro_names": _counter_to_top_list(coro_names),
                "interesting_tasks": interesting_tasks[:50],
            }
        except Exception as exc:
            errors.append(f"asyncio:{exc}")
            return {}

    def _collect_heartflow_metrics(self, errors: list[str]) -> dict[str, Any]:
        try:
            heartflow_manager = _get_loaded_attr(
                "src.chat.heart_flow.heartflow_manager",
                "heartflow_manager",
            )
            if heartflow_manager is None:
                return _empty_loaded_metrics(False)

            runtime_items = list(getattr(heartflow_manager, "heartflow_chat_list", {}).items())
            lock_count = _safe_len(getattr(heartflow_manager, "_chat_create_locks", {}))
            cheap_sessions = [
                self._collect_runtime_metrics(session_id, runtime)
                for session_id, runtime in runtime_items
            ]
            scan_limit = self._binary_scan_limit()
            scan_counts = _plan_binary_scan_counts(cheap_sessions, scan_limit)
            runtime_by_session_id = {
                str(session_id): runtime
                for session_id, runtime in runtime_items
            }
            for session in cheap_sessions:
                scan_count = scan_counts.get(str(session.get("session_id", "")), 0)
                if scan_count <= 0:
                    _mark_binary_scan_skipped(session)
                    continue
                runtime = runtime_by_session_id.get(str(session.get("session_id", "")))
                if runtime is None:
                    _mark_binary_scan_skipped(session)
                    continue
                self._fill_runtime_binary_metrics(session, runtime, scan_count)

            binary_scanned_messages = sum(item.get("binary_scan_messages", 0) for item in cheap_sessions)
            binary_skipped_messages = sum(item.get("binary_scan_skipped_messages", 0) for item in cheap_sessions)
            scan_budget_remaining = max(0, scan_limit - binary_scanned_messages)
            binary_lower_bound = any(
                item.get("binary_lower_bound", False) or item.get("binary_scan_skipped", False)
                for item in cheap_sessions
            )

            totals = {
                "message_cache": sum(item.get("message_cache", 0) for item in cheap_sessions),
                "source_messages": sum(item.get("source_messages", 0) for item in cheap_sessions),
                "message_received_markers": sum(item.get("message_received_markers", 0) for item in cheap_sessions),
                "history_loop": sum(item.get("history_loop", 0) for item in cheap_sessions),
                "history_loop_estimated_mb": round(
                    sum(item.get("history_loop_estimated_bytes", 0) for item in cheap_sessions) / BYTES_PER_MB,
                    3,
                ),
                "chat_history": sum(item.get("chat_history", 0) for item in cheap_sessions),
                "internal_queue": sum(item.get("internal_queue", 0) for item in cheap_sessions),
                "voice_binary_mb": round(sum(item.get("voice_binary_bytes", 0) for item in cheap_sessions) / BYTES_PER_MB, 3),
                "binary_mb": round(sum(item.get("binary_bytes", 0) for item in cheap_sessions) / BYTES_PER_MB, 3),
                "binary_lower_bound": binary_lower_bound,
                "binary_scan_budget": scan_limit,
                "binary_scan_remaining": scan_budget_remaining,
                "binary_scanned_messages": binary_scanned_messages,
                "binary_skipped_messages": binary_skipped_messages,
                "binary_scanned_sessions": sum(1 for item in cheap_sessions if item.get("binary_scan_messages", 0) > 0),
                "binary_truncated_sessions": sum(1 for item in cheap_sessions if item.get("binary_scan_truncated", False)),
                "binary_unscanned_sessions": sum(1 for item in cheap_sessions if item.get("binary_scan_skipped", False)),
                "reply_effect_pending": sum(item.get("reply_effect_pending", 0) for item in cheap_sessions),
                "reply_effect_timeout_tasks": sum(item.get("reply_effect_timeout_tasks", 0) for item in cheap_sessions),
            }
            top_n = self._top_session_limit()
            return {
                "loaded": True,
                "runtime_count": len(runtime_items),
                "lock_count": lock_count,
                "scan_budget_remaining": scan_budget_remaining,
                "totals": totals,
                "top_sessions": sorted(
                    cheap_sessions,
                    key=lambda item: (
                        item.get("message_cache", 0)
                        + item.get("source_messages", 0)
                        + item.get("history_loop", 0),
                    ),
                    reverse=True,
                )[:top_n],
                "top_binary_sessions": sorted(
                    cheap_sessions,
                    key=lambda item: (item.get("voice_binary_bytes", 0), item.get("binary_bytes", 0)),
                    reverse=True,
                )[:top_n],
            }
        except Exception as exc:
            errors.append(f"heartflow:{exc}")
            return {}

    def _collect_runtime_metrics(self, session_id: str, runtime: Any) -> dict[str, Any]:
        message_cache = getattr(runtime, "message_cache", []) or []
        history_loop = getattr(runtime, "history_loop", []) or []
        history_loop_summary = _estimate_history_loop_bytes(history_loop)
        expression_learner = getattr(runtime, "_expression_learner", None)
        pending_expression_messages = 0
        if expression_learner is not None and hasattr(expression_learner, "get_pending_count"):
            try:
                pending_expression_messages = int(expression_learner.get_pending_count(message_cache))
            except Exception:
                pending_expression_messages = 0

        reply_effect_tracker = getattr(runtime, "_reply_effect_tracker", None)
        item = {
            "session_id": str(session_id),
            "session_name": str(getattr(runtime, "session_name", "") or ""),
            "running": bool(getattr(runtime, "_running", False)),
            "agent_state": str(getattr(runtime, "_agent_state", "") or ""),
            "message_cache": len(message_cache),
            "runtime_processed_index": int(getattr(runtime, "_last_processed_index", 0) or 0),
            "expression_pending": pending_expression_messages,
            "expression_processed_index": int(getattr(expression_learner, "_last_processed_index", 0) or 0),
            "source_messages": _safe_len(getattr(runtime, "_source_messages_by_id", {})),
            "message_received_markers": _safe_len(getattr(runtime, "_message_received_at_by_id", {})),
            "history_loop": _safe_len(history_loop),
            "history_loop_estimated_bytes": history_loop_summary["estimated_bytes"],
            "history_loop_estimated_mb": history_loop_summary["estimated_mb"],
            "history_loop_sample_count": history_loop_summary["sample_count"],
            "history_loop_average_bytes": history_loop_summary["average_bytes"],
            "chat_history": _safe_len(getattr(runtime, "_chat_history", [])),
            "internal_queue": _queue_size(getattr(runtime, "_internal_turn_queue", None)),
            "reply_effect_pending": _safe_len(getattr(reply_effect_tracker, "_pending_records", {})),
            "reply_effect_timeout_tasks": _safe_len(getattr(reply_effect_tracker, "_timeout_tasks", {})),
        }
        return item

    def _fill_runtime_binary_metrics(self, item: dict[str, Any], runtime: Any, scan_budget: int) -> int:
        message_cache = getattr(runtime, "message_cache", []) or []
        scan_count = min(len(message_cache), max(0, scan_budget))
        binary_summary = _estimate_messages_binary(_iter_spread(message_cache, scan_count))
        item.update(binary_summary)
        item["binary_scan_skipped"] = False
        item["binary_scan_messages"] = scan_count
        item["binary_scan_truncated"] = scan_count < len(message_cache)
        item["binary_scan_strategy"] = "spread"
        item["binary_scan_skipped_messages"] = max(0, len(message_cache) - scan_count)
        item["binary_lower_bound"] = bool(item["binary_scan_truncated"])
        return scan_count

    @staticmethod
    def _collect_chat_manager_metrics(errors: list[str]) -> dict[str, Any]:
        try:
            chat_manager = _get_loaded_attr(
                "src.chat.message_receive.chat_manager",
                "chat_manager",
            )
            if chat_manager is None:
                return _empty_loaded_metrics(False)

            last_messages = list(getattr(chat_manager, "last_messages", {}).values())
            binary_summary = _estimate_messages_binary(last_messages)
            return {
                "loaded": True,
                "sessions": _safe_len(getattr(chat_manager, "sessions", {})),
                "last_messages": len(last_messages),
                "last_message_binary_mb": _bytes_to_mb(binary_summary.get("binary_bytes", 0)),
                "last_message_voice_binary_mb": _bytes_to_mb(binary_summary.get("voice_binary_bytes", 0)),
            }
        except Exception as exc:
            errors.append(f"chat_manager:{exc}")
            return {}

    @staticmethod
    def _collect_websocket_metrics(errors: list[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        try:
            websocket_manager = _get_loaded_attr(
                "src.webui.routers.websocket.manager",
                "websocket_manager",
            )
            if websocket_manager is None:
                payload["unified"] = _empty_loaded_metrics(False)
            else:
                connections = list(getattr(websocket_manager, "connections", {}).values())
                queue_sizes = [_queue_size(getattr(connection, "send_queue", None)) for connection in connections]
                payload["unified"] = {
                    "loaded": True,
                    "connections": len(connections),
                    "total_send_queue": sum(queue_sizes),
                    "max_send_queue": max(queue_sizes, default=0),
                    "subscribed_connections": sum(
                        1 for connection in connections if getattr(connection, "subscriptions", set())
                    ),
                    "chat_session_mappings": sum(
                        _safe_len(getattr(connection, "chat_sessions", {})) for connection in connections
                    ),
                }
        except Exception as exc:
            errors.append(f"websocket.unified:{exc}")

        try:
            logs_ws = sys.modules.get("src.webui.logs_ws")
            if logs_ws is None:
                payload["legacy_logs"] = _empty_loaded_metrics(False)
            else:
                payload["legacy_logs"] = {
                    "loaded": True,
                    "active_connections": _safe_len(getattr(logs_ws, "active_connections", set())),
                }
        except Exception as exc:
            errors.append(f"websocket.legacy_logs:{exc}")
        return payload

    @staticmethod
    def _collect_media_task_metrics(errors: list[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        try:
            image_manager = _get_loaded_attr(
                "src.chat.image_system.image_manager",
                "image_manager",
            )
            if image_manager is None:
                payload["image"] = _empty_loaded_metrics(False)
            else:
                tasks = list(getattr(image_manager, "_pending_description_tasks", {}).values())
                payload["image"] = {"loaded": True, **_summarize_pending_tasks(tasks)}
        except Exception as exc:
            errors.append(f"media.image:{exc}")

        try:
            emoji_manager = _get_loaded_attr(
                "src.emoji_system.emoji_manager",
                "emoji_manager",
            )
            if emoji_manager is None:
                payload["emoji"] = _empty_loaded_metrics(False)
            else:
                tasks = list(getattr(emoji_manager, "_pending_description_tasks", {}).values())
                payload["emoji"] = {"loaded": True, **_summarize_pending_tasks(tasks)}
        except Exception as exc:
            errors.append(f"media.emoji:{exc}")
        return payload

    @staticmethod
    def _collect_memory_automation_metrics(errors: list[str]) -> dict[str, Any]:
        try:
            memory_automation_service = _get_loaded_attr(
                "src.services.memory_flow_service",
                "memory_automation_service",
            )
            if memory_automation_service is None:
                return _empty_loaded_metrics(False)

            fact_writeback = getattr(memory_automation_service, "fact_writeback", None)
            chat_summary = getattr(memory_automation_service, "chat_summary_writeback", None)
            return {
                "loaded": True,
                "started": bool(getattr(memory_automation_service, "_started", False)),
                "fact_writeback_queue": _queue_size(getattr(fact_writeback, "_queue", None)),
                "fact_writeback_worker_active": _task_active(getattr(fact_writeback, "_worker_task", None)),
                "chat_summary_queue": _queue_size(getattr(chat_summary, "_queue", None)),
                "chat_summary_worker_active": _task_active(getattr(chat_summary, "_worker_task", None)),
                "chat_summary_states": _safe_len(getattr(chat_summary, "_states", {})),
            }
        except Exception as exc:
            errors.append(f"memory_automation:{exc}")
            return {}

    @staticmethod
    def _collect_a_memorix_metrics(errors: list[str]) -> dict[str, Any]:
        try:
            a_memorix_host_service = _get_loaded_attr(
                "src.A_memorix.host_service",
                "a_memorix_host_service",
            )
            if a_memorix_host_service is None:
                return {"loaded": False, "enabled": False, "kernel_loaded": False}

            kernel = getattr(a_memorix_host_service, "_kernel", None)
            if kernel is None:
                return {"loaded": True, "enabled": False, "kernel_loaded": False}

            payload: dict[str, Any] = {"loaded": True, "enabled": True, "kernel_loaded": True}
            vector_store = getattr(kernel, "vector_store", None)
            if vector_store is not None:
                index = getattr(vector_store, "_index", None)
                fallback_index = getattr(vector_store, "_fallback_index", None)
                payload["vector_store"] = {
                    "dimension": int(getattr(vector_store, "dimension", 0) or 0),
                    "index_ntotal": int(getattr(index, "ntotal", 0) or 0),
                    "fallback_ntotal": int(getattr(fallback_index, "ntotal", 0) or 0),
                    "is_trained": bool(getattr(vector_store, "_is_trained", False)),
                    "bin_count": int(getattr(vector_store, "_bin_count", 0) or 0),
                    "known_hashes": _safe_len(getattr(vector_store, "_known_hashes", set())),
                    "deleted_ids": _safe_len(getattr(vector_store, "_deleted_ids", set())),
                    "reservoir_buffer": _safe_len(getattr(vector_store, "_reservoir_buffer", [])),
                    "write_buffer_ids": _safe_len(getattr(vector_store, "_write_buffer_ids", [])),
                }

            embedding_manager = getattr(kernel, "embedding_manager", None)
            if embedding_manager is not None:
                payload["embedding"] = {
                    "cache_enabled": bool(getattr(embedding_manager, "enable_cache", False)),
                    "global_text_cache": _safe_len(getattr(embedding_manager, "_GLOBAL_TEXT_EMBEDDING_CACHE", {})),
                    "global_dimension_cache": _safe_len(getattr(embedding_manager, "_GLOBAL_DIMENSION_CACHE", {})),
                    "local_cache": _safe_len(getattr(embedding_manager, "_embedding_cache", {})),
                    "total_encoded": int(getattr(embedding_manager, "_total_encoded", 0) or 0),
                    "total_errors": int(getattr(embedding_manager, "_total_errors", 0) or 0),
                }

            metadata_store = getattr(kernel, "metadata_store", None)
            if metadata_store is not None and hasattr(metadata_store, "get_statistics"):
                try:
                    payload["metadata"] = metadata_store.get_statistics()
                except Exception as exc:
                    payload["metadata_error"] = str(exc)
            return payload
        except Exception as exc:
            errors.append(f"a_memorix:{exc}")
            return {}

    def _collect_tracemalloc_metrics(self, process_metrics: dict[str, Any], errors: list[str]) -> dict[str, Any]:
        if not self._tracemalloc_enabled():
            return {}
        if not tracemalloc.is_tracing():
            tracemalloc.start(25)

        try:
            current_snapshot = tracemalloc.take_snapshot()
            current_rss_mb = float(process_metrics.get("rss_mb") or 0.0)
            threshold_mb = max(
                1.0,
                float(global_config.debug.memory_diagnostics_snapshot_growth_mb),
            )
            payload: dict[str, Any] = {
                "current_mb": _bytes_to_mb(sum(stat.size for stat in current_snapshot.statistics("filename"))),
                "rss_growth_mb": 0.0,
                "rss_interval_growth_mb": 0.0,
                "rss_baseline_growth_mb": 0.0,
                "diff": [],
            }

            if self._last_rss_mb is not None:
                payload["rss_interval_growth_mb"] = round(current_rss_mb - self._last_rss_mb, 3)

            if self._baseline_tracemalloc_snapshot is None or self._baseline_rss_mb is None:
                self._reset_tracemalloc_growth_baseline(current_snapshot, current_rss_mb)
            else:
                baseline_growth_mb = current_rss_mb - self._baseline_rss_mb
                if baseline_growth_mb <= 0:
                    self._reset_tracemalloc_growth_baseline(current_snapshot, current_rss_mb)
                else:
                    rounded_growth = round(baseline_growth_mb, 3)
                    payload["rss_growth_mb"] = rounded_growth
                    payload["rss_baseline_growth_mb"] = rounded_growth
                if baseline_growth_mb >= threshold_mb:
                    top_stats = current_snapshot.compare_to(self._baseline_tracemalloc_snapshot, "lineno")
                    payload["diff"] = [
                        {
                            "file": str(stat.traceback[0].filename),
                            "line": int(stat.traceback[0].lineno),
                            "size_diff_mb": _bytes_to_mb(stat.size_diff),
                            "count_diff": int(stat.count_diff),
                        }
                        for stat in top_stats[:DEFAULT_TOP_ALLOCATIONS]
                    ]
                    payload["diff_baseline"] = "last_alert_or_reset"
                    self._reset_tracemalloc_growth_baseline(current_snapshot, current_rss_mb)

            self._last_rss_mb = current_rss_mb
            return payload
        except Exception as exc:
            errors.append(f"tracemalloc:{exc}")
            return {}

    def _reset_tracemalloc_growth_baseline(
        self,
        snapshot: tracemalloc.Snapshot,
        rss_mb: float,
    ) -> None:
        self._baseline_tracemalloc_snapshot = snapshot
        self._baseline_rss_mb = rss_mb

    def _write_snapshot(self, payload: dict[str, Any]) -> None:
        raw_path = global_config.debug.memory_diagnostics_jsonl_path.strip()
        output_path = Path(raw_path or "logs/memory_diagnostics/memory_diagnostics.jsonl")
        if not output_path.is_absolute():
            output_path = PROJECT_ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_snapshot_file_if_needed(output_path)
        with output_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def _rotate_snapshot_file_if_needed(output_path: Path) -> None:
        max_total_size_mb = max(1, int(global_config.debug.memory_diagnostics_jsonl_max_total_size_mb))
        max_total_size_bytes = max_total_size_mb * BYTES_PER_MB
        try:
            if output_path.exists() and output_path.stat().st_size >= max_total_size_bytes:
                rotated_path = _build_rotated_snapshot_path(output_path)
                output_path.replace(rotated_path)
                _cleanup_rotated_snapshot_files(output_path, keep=DEFAULT_JSONL_ROTATED_FILE_KEEP)
                logger.warning(f"内存诊断 JSONL 超过 {max_total_size_mb}MB，已轮转到 {rotated_path}")
        except Exception as exc:
            logger.warning(f"内存诊断 JSONL 轮转失败: {exc}")

    @staticmethod
    def _log_summary(payload: dict[str, Any]) -> None:
        process = payload.get("process", {})
        heartflow_raw = payload.get("heartflow", {})
        heartflow = heartflow_raw if isinstance(heartflow_raw, dict) else {}
        totals_raw = heartflow.get("totals", {})
        totals = totals_raw if isinstance(totals_raw, dict) else {}
        websocket = payload.get("websocket", {})
        unified_ws = websocket.get("unified", {}) if isinstance(websocket, dict) else {}
        logger.info(
            "内存诊断快照: "
            f"rss={process.get('rss_mb', 0)}MB "
            f"tree_rss={process.get('process_tree_rss_mb', process.get('rss_mb', 0))}MB "
            f"uss={process.get('uss_mb', 0)}MB "
            f"runtime={heartflow.get('runtime_count', 0)} "
            f"message_cache={totals.get('message_cache', 0)} "
            f"source_messages={totals.get('source_messages', 0)} "
            f"history_loop={totals.get('history_loop', 0)} "
            f"internal_queue={totals.get('internal_queue', 0)} "
            f"voice_binary={totals.get('voice_binary_mb', 0)}MB "
            f"binary_lower_bound={totals.get('binary_lower_bound', False)} "
            f"ws_queue={unified_ws.get('total_send_queue', 0)}"
        )
        _log_threshold_warnings(heartflow, totals)

    @staticmethod
    def _tracemalloc_enabled() -> bool:
        return global_config.debug.memory_diagnostics_enable_tracemalloc

    @staticmethod
    def _top_session_limit() -> int:
        return max(1, int(global_config.debug.memory_diagnostics_top_sessions))

    @staticmethod
    def _binary_scan_limit() -> int:
        return max(0, int(global_config.debug.memory_diagnostics_binary_scan_message_limit))


def _estimate_messages_binary(messages: Iterable[Any]) -> dict[str, Any]:
    total_bytes = 0
    voice_bytes = 0
    image_bytes = 0
    emoji_bytes = 0
    component_counts: Counter[str] = Counter()

    for message in messages:
        summary = _estimate_sequence_binary(getattr(message, "raw_message", None))
        total_bytes += summary["binary_bytes"]
        voice_bytes += summary["voice_binary_bytes"]
        image_bytes += summary["image_binary_bytes"]
        emoji_bytes += summary["emoji_binary_bytes"]
        component_counts.update(summary["component_counts"])

    return {
        "binary_bytes": total_bytes,
        "binary_mb": _bytes_to_mb(total_bytes),
        "voice_binary_bytes": voice_bytes,
        "voice_binary_mb": _bytes_to_mb(voice_bytes),
        "image_binary_bytes": image_bytes,
        "image_binary_mb": _bytes_to_mb(image_bytes),
        "emoji_binary_bytes": emoji_bytes,
        "emoji_binary_mb": _bytes_to_mb(emoji_bytes),
        "component_counts": dict(component_counts),
    }


def _iter_spread(items: Any, limit: int) -> Iterable[Any]:
    """在固定预算内覆盖首尾和中间样本，避免只看最新消息漏掉早期滞留二进制。"""

    if limit <= 0:
        return ()

    if isinstance(items, (list, tuple)):
        return _sample_sequence_spread(items, limit)

    try:
        len(items)
    except Exception:
        return islice(items, limit)

    return islice(items, limit)


def _sample_sequence_spread(items: Sequence[Any], limit: int) -> list[Any]:
    item_count = len(items)
    if item_count <= limit:
        return list(items)
    if limit == 1:
        return [items[-1]]

    step = (item_count - 1) / (limit - 1)
    indexes = [round(index * step) for index in range(limit)]
    return [items[index] for index in indexes]


def _get_loaded_attr(module_name: str, attr_name: str) -> Any:
    """只读取已经加载的模块属性，避免诊断采集触发模块初始化副作用。"""

    module = sys.modules.get(module_name)
    if module is None:
        return None
    return getattr(module, attr_name, None)


def _empty_loaded_metrics(loaded: bool) -> dict[str, Any]:
    return {"loaded": loaded}


def _plan_binary_scan_counts(sessions: list[dict[str, Any]], scan_limit: int) -> dict[str, int]:
    """为每个 session 分配二进制扫描预算，避免大文本 session 独占全部预算。"""

    budget = max(0, int(scan_limit))
    if budget <= 0:
        return {}

    non_empty_sessions = [
        session
        for session in sessions
        if int(session.get("message_cache", 0) or 0) > 0 and str(session.get("session_id", "") or "")
    ]
    if not non_empty_sessions:
        return {}

    counts: dict[str, int] = {}
    fair_quota = min(1000, max(1, budget // len(non_empty_sessions)))
    for session in sorted(non_empty_sessions, key=lambda item: int(item.get("message_cache", 0) or 0), reverse=True):
        if budget <= 0:
            break
        session_id = str(session.get("session_id", "") or "")
        message_count = int(session.get("message_cache", 0) or 0)
        scan_count = min(message_count, fair_quota, budget)
        counts[session_id] = scan_count
        budget -= scan_count

    if budget <= 0:
        return counts

    # 剩余预算再给缓存最大的 session，用于尽可能提高大 backlog 场景下的估算精度。
    for session in sorted(non_empty_sessions, key=lambda item: int(item.get("message_cache", 0) or 0), reverse=True):
        if budget <= 0:
            break
        session_id = str(session.get("session_id", "") or "")
        message_count = int(session.get("message_cache", 0) or 0)
        already_planned = counts.get(session_id, 0)
        extra_count = min(max(0, message_count - already_planned), budget)
        if extra_count <= 0:
            continue
        counts[session_id] = already_planned + extra_count
        budget -= extra_count
    return counts


def _mark_binary_scan_skipped(item: dict[str, Any]) -> None:
    message_count = int(item.get("message_cache", 0) or 0)
    item["binary_scan_skipped"] = True
    item["binary_scan_messages"] = 0
    item["binary_scan_skipped_messages"] = message_count
    item["binary_scan_truncated"] = message_count > 0
    item["binary_scan_strategy"] = "spread"
    item["binary_lower_bound"] = message_count > 0


def _estimate_sequence_binary(message_sequence: Any) -> dict[str, Any]:
    total_bytes = 0
    voice_bytes = 0
    image_bytes = 0
    emoji_bytes = 0
    component_counts: Counter[str] = Counter()
    seen_component_ids: set[int] = set()

    def visit_component(component: Any, depth: int = 0) -> None:
        nonlocal total_bytes, voice_bytes, image_bytes, emoji_bytes
        if depth > DEFAULT_FORWARD_COMPONENT_MAX_DEPTH:
            component_counts["ForwardDepthTruncated"] += 1
            return

        component_id = id(component)
        if component_id in seen_component_ids:
            component_counts["ForwardCycleSkipped"] += 1
            return
        seen_component_ids.add(component_id)

        class_name = component.__class__.__name__
        component_counts[class_name] += 1
        binary_data = getattr(component, "binary_data", b"")
        binary_size = _binary_size(binary_data)
        total_bytes += binary_size
        if class_name == "VoiceComponent":
            voice_bytes += binary_size
        elif class_name == "ImageComponent":
            image_bytes += binary_size
        elif class_name == "EmojiComponent":
            emoji_bytes += binary_size

        for forward_component in getattr(component, "forward_components", []) or []:
            for child in getattr(forward_component, "content", []) or []:
                visit_component(child, depth + 1)

    for item in getattr(message_sequence, "components", []) or []:
        visit_component(item)

    return {
        "binary_bytes": total_bytes,
        "voice_binary_bytes": voice_bytes,
        "image_binary_bytes": image_bytes,
        "emoji_binary_bytes": emoji_bytes,
        "component_counts": component_counts,
    }


def _estimate_history_loop_bytes(history_loop: Any) -> dict[str, Any]:
    sample_size = DEFAULT_HISTORY_LOOP_SAMPLE_SIZE
    loop_count = _safe_len(history_loop)
    if loop_count <= 0:
        return _empty_history_loop_summary()

    samples = _sample_history_loop(history_loop, sample_size)
    if not samples:
        return _empty_history_loop_summary()

    sampled_bytes = sum(_estimate_cycle_detail_bytes(cycle_detail) for cycle_detail in samples)
    average_bytes = int(sampled_bytes / len(samples))
    estimated_bytes = average_bytes * loop_count
    return {
        "estimated_bytes": estimated_bytes,
        "estimated_mb": _bytes_to_mb(estimated_bytes),
        "sample_count": len(samples),
        "average_bytes": average_bytes,
    }


def _sample_history_loop(history_loop: Any, sample_size: int) -> list[Any]:
    if sample_size <= 0:
        return []

    try:
        loop_count = len(history_loop)
    except Exception:
        try:
            return list(islice(history_loop, sample_size))
        except TypeError:
            return []

    if loop_count <= 0:
        return []
    if isinstance(history_loop, (list, tuple)):
        if loop_count <= sample_size:
            return list(history_loop)
        head_count = max(1, sample_size // 2)
        tail_count = max(1, sample_size - head_count)
        return list(history_loop[:head_count]) + list(history_loop[-tail_count:])

    try:
        return list(islice(history_loop, sample_size))
    except TypeError:
        return []


def _empty_history_loop_summary() -> dict[str, Any]:
    return {
        "estimated_bytes": 0,
        "estimated_mb": 0.0,
        "sample_count": 0,
        "average_bytes": 0,
    }


def _estimate_cycle_detail_bytes(cycle_detail: Any) -> int:
    size = sys.getsizeof(cycle_detail, 0)
    for field_name in ("thinking_id", "time_records", "loop_plan_info", "loop_action_info"):
        size += _limited_deep_size(getattr(cycle_detail, field_name, None), max_depth=2, max_items=32)
    return size


def _limited_deep_size(value: Any, *, max_depth: int, max_items: int, seen_ids: Optional[set[int]] = None) -> int:
    if seen_ids is None:
        seen_ids = set()

    value_id = id(value)
    if value_id in seen_ids:
        return 0
    seen_ids.add(value_id)

    size = sys.getsizeof(value, 0)
    nbytes = getattr(value, "nbytes", None)
    if isinstance(nbytes, int):
        size += nbytes

    if max_depth <= 0:
        return size

    if isinstance(value, dict):
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                break
            size += _limited_deep_size(key, max_depth=max_depth - 1, max_items=max_items, seen_ids=seen_ids)
            size += _limited_deep_size(item, max_depth=max_depth - 1, max_items=max_items, seen_ids=seen_ids)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            if index >= max_items:
                break
            size += _limited_deep_size(item, max_depth=max_depth - 1, max_items=max_items, seen_ids=seen_ids)

    return size


def _summarize_pending_tasks(tasks: list[asyncio.Task[Any]]) -> dict[str, Any]:
    pending_tasks = [task for task in tasks if not task.done()]
    binary_bytes = sum(_estimate_task_binary_bytes(task) for task in pending_tasks)
    return {
        "task_count": len(tasks),
        "pending_task_count": len(pending_tasks),
        "estimated_binary_mb": _bytes_to_mb(binary_bytes),
        "done_task_count": len(tasks) - len(pending_tasks),
    }


def _estimate_task_binary_bytes(task: asyncio.Task[Any]) -> int:
    seen_ids: set[int] = set()

    def visit_value(value: Any, depth: int = DEFAULT_TASK_VALUE_MAX_DEPTH) -> int:
        value_id = id(value)
        if value_id in seen_ids:
            return 0
        seen_ids.add(value_id)
        size = _binary_size(value)

        if depth <= 0:
            return size

        if isinstance(value, dict):
            for index, item in enumerate(value.values()):
                if index >= DEFAULT_TASK_VALUE_MAX_ITEMS:
                    break
                size += visit_value(item, depth - 1)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for index, item in enumerate(value):
                if index >= DEFAULT_TASK_VALUE_MAX_ITEMS:
                    break
                size += visit_value(item, depth - 1)
        elif hasattr(value, "binary_data"):
            size += _binary_size(getattr(value, "binary_data", b""))
        elif hasattr(value, "image_bytes"):
            size += _binary_size(getattr(value, "image_bytes", b""))
        return size

    size = 0
    coro = task.get_coro()
    frame_count = 0
    while coro is not None and frame_count < DEFAULT_TASK_AWAIT_CHAIN_LIMIT:
        frame = getattr(coro, "cr_frame", None)
        if frame is not None:
            for index, value in enumerate(frame.f_locals.values()):
                if index >= DEFAULT_TASK_LOCAL_LIMIT:
                    break
                size += visit_value(value)
        coro = getattr(coro, "cr_await", None)
        frame_count += 1
    return size


def _bytes_to_mb(value: int | float) -> float:
    return round(float(value or 0) / BYTES_PER_MB, 3)


def _collect_child_process_metrics(process: Any) -> dict[str, Any]:
    try:
        children = process.children(recursive=True)
    except Exception as exc:
        return {
            "available": False,
            "reason": str(exc),
            "count": 0,
            "rss_mb": 0.0,
            "uss_mb": 0.0,
            "vms_mb": 0.0,
            "top": [],
        }

    total_rss = 0
    total_uss = 0
    total_vms = 0
    skipped = 0
    child_items: list[dict[str, Any]] = []
    for child in children:
        try:
            with child.oneshot():
                memory_info = child.memory_info()
                rss_bytes = int(getattr(memory_info, "rss", 0) or 0)
                vms_bytes = int(getattr(memory_info, "vms", 0) or 0)
                try:
                    full_memory_info = child.memory_full_info()
                    uss_bytes = int(getattr(full_memory_info, "uss", 0) or 0)
                except Exception:
                    uss_bytes = 0
                child_items.append(
                    {
                        "pid": int(child.pid),
                        "ppid": _safe_process_int(child, "ppid"),
                        "name": _safe_process_text(child, "name"),
                        "status": _safe_process_text(child, "status"),
                        "rss_mb": _bytes_to_mb(rss_bytes),
                        "uss_mb": _bytes_to_mb(uss_bytes),
                        "vms_mb": _bytes_to_mb(vms_bytes),
                        "cmdline": _safe_process_cmdline(child),
                    }
                )
                total_rss += rss_bytes
                total_uss += uss_bytes
                total_vms += vms_bytes
        except Exception:
            skipped += 1

    child_items.sort(key=lambda item: item.get("rss_mb", 0), reverse=True)
    return {
        "available": True,
        "count": len(children),
        "sampled_count": len(child_items),
        "skipped_count": skipped,
        "rss_mb": _bytes_to_mb(total_rss),
        "uss_mb": _bytes_to_mb(total_uss),
        "vms_mb": _bytes_to_mb(total_vms),
        "top": child_items[:DEFAULT_TOP_CHILD_PROCESSES],
    }


def _safe_process_int(process: Any, method_name: str) -> int:
    try:
        return int(getattr(process, method_name)())
    except Exception:
        return 0


def _safe_process_text(process: Any, method_name: str) -> str:
    try:
        return str(getattr(process, method_name)() or "")
    except Exception:
        return ""


def _safe_process_cmdline(process: Any) -> list[str]:
    try:
        return [str(item) for item in process.cmdline()[:6]]
    except Exception:
        return []


def _build_rotated_snapshot_path(output_path: Path) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    base_name = f"{output_path.stem}.{timestamp}"
    candidate = output_path.with_name(f"{base_name}{output_path.suffix}")
    index = 1
    while candidate.exists():
        candidate = output_path.with_name(f"{base_name}.{index}{output_path.suffix}")
        index += 1
    return candidate


def _cleanup_rotated_snapshot_files(output_path: Path, *, keep: int) -> None:
    if keep <= 0:
        return

    rotated_paths: list[Path] = []
    rotated_prefix = f"{output_path.stem}."
    for candidate in output_path.parent.iterdir():
        if not candidate.is_file():
            continue
        if candidate.name.startswith(rotated_prefix) and candidate.suffix == output_path.suffix:
            rotated_paths.append(candidate)

    rotated_paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for stale_path in rotated_paths[keep:]:
        stale_path.unlink()


def _binary_size(value: Any) -> int:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    return 0


def _safe_len(value: Any) -> int:
    try:
        return len(value)
    except Exception:
        return 0


def _queue_size(queue: Any) -> int:
    if queue is None or not hasattr(queue, "qsize"):
        return 0
    try:
        return int(queue.qsize())
    except Exception:
        return 0


def _task_active(task: Any) -> bool:
    return task is not None and hasattr(task, "done") and not task.done()


def _task_coro_name(task: asyncio.Task[Any]) -> str:
    coro = task.get_coro()
    code = getattr(coro, "cr_code", None)
    if code is not None and getattr(code, "co_qualname", ""):
        return str(code.co_qualname)
    return str(getattr(coro, "__qualname__", coro.__class__.__name__))


def _counter_to_top_list(counter: Counter[str], limit: int = 20) -> list[dict[str, Any]]:
    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


def _log_threshold_warnings(heartflow: dict[str, Any], totals: dict[str, Any]) -> None:
    warnings = []
    runtime_count = int(heartflow.get("runtime_count", 0) or 0)
    message_cache = int(totals.get("message_cache", 0) or 0)
    voice_binary_mb = float(totals.get("voice_binary_mb", 0) or 0)

    runtime_limit = int(global_config.debug.memory_diagnostics_warn_runtime_count)
    message_cache_limit = int(global_config.debug.memory_diagnostics_warn_message_cache_count)
    voice_binary_limit = float(global_config.debug.memory_diagnostics_warn_voice_binary_mb)

    if runtime_limit > 0 and runtime_count > runtime_limit:
        warnings.append(f"runtime 数 {runtime_count} 超过阈值 {runtime_limit}")
    if message_cache_limit > 0 and message_cache > message_cache_limit:
        warnings.append(f"message_cache 总量 {message_cache} 超过阈值 {message_cache_limit}")
    if voice_binary_limit > 0 and voice_binary_mb > voice_binary_limit:
        warnings.append(f"语音二进制估算 {voice_binary_mb}MB 超过阈值 {voice_binary_limit}MB")

    if warnings:
        logger.warning("内存诊断告警: " + "；".join(warnings))


def _is_interesting_task(task_name: str, coro_name: str) -> bool:
    lowered = f"{task_name} {coro_name}".lower()
    keywords = (
        "learn",
        "description",
        "heartflow",
        "maisaka",
        "memory",
        "websocket",
        "reply_effect",
        "embedding",
    )
    return any(keyword in lowered for keyword in keywords)
