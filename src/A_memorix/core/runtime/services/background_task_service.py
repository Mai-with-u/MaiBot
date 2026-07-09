from __future__ import annotations

from typing import Any, Callable, Coroutine, Dict

import asyncio
import time

from src.common.logger import get_logger

from ...utils import profile_policy
from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")


class MemoryBackgroundTaskService(KernelServiceBase):
    async def _start_background_tasks(self) -> None:
        async with self._background_lock:
            self._background_stopping = False
            self._ensure_background_task("auto_save", self._auto_save_loop)
            self._ensure_background_task("episode_pending", self._episode_pending_loop)
            self._ensure_background_task("embedding_probe", self._embedding_probe_loop)
            self._ensure_background_task("paragraph_vector_backfill", self._paragraph_vector_backfill_loop)
            self._ensure_background_task("memory_maintenance", self._memory_maintenance_loop)
            self._ensure_background_task("person_profile_refresh", self._person_profile_refresh_loop)
            self._ensure_background_task("person_profile_refresh_queue", self._person_profile_refresh_queue_loop)
            self._ensure_background_task("feedback_correction", self._feedback_correction_loop)
            self._ensure_background_task("feedback_correction_reconcile", self._feedback_correction_reconcile_loop)
            if self._should_start_dual_vector_auto_migration():
                self._ensure_background_task("dual_vector_auto_migration", self._dual_vector_auto_migration_loop)

    def _ensure_background_task(
        self,
        name: str,
        factory: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        task = self._background_tasks.get(name)
        if task is not None and not task.done():
            return
        self._background_tasks[name] = asyncio.create_task(factory(), name=f"A_Memorix.{name}")

    async def _sleep_background(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, float(seconds or 0.0)))

    async def _dual_vector_auto_migration_loop(self) -> None:
        from .. import sdk_memory_kernel as kernel_module

        if not self._should_start_dual_vector_auto_migration():
            return

        self._dual_vector_auto_migration_attempted = True
        started_at = time.time()
        self._dual_vector_auto_migration_status.update(
            {
                "running": True,
                "attempted": True,
                "success": False,
                "stage": "initial_delay",
                "progress": self._normalize_dual_vector_auto_migration_progress(
                    {"total": 0, "processed": 0},
                    now=started_at,
                    explicit_processed=True,
                ),
                "last_error": "",
                "started_at": started_at,
                "finished_at": None,
                "updated_at": started_at,
            }
        )
        try:
            await self._sleep_background(kernel_module.DUAL_VECTOR_AUTO_MIGRATION_INITIAL_DELAY_SECONDS)
            if self._background_stopping or self._dual_vector_pools_enabled():
                finished_at = time.time()
                success = self._dual_vector_pools_enabled()
                progress = self._normalize_dual_vector_auto_migration_progress(
                    self._dual_vector_auto_migration_status.get("progress"),
                    now=finished_at,
                    completed=True,
                    success=success,
                )
                self._dual_vector_auto_migration_status.update(
                    {
                        "running": False,
                        "success": success,
                        "stage": "skipped",
                        "progress": progress,
                        "finished_at": finished_at,
                        "updated_at": finished_at,
                    }
                )
                return

            retry_delays = [0.0, *kernel_module.DUAL_VECTOR_AUTO_MIGRATION_LOCK_RETRY_DELAYS_SECONDS]
            result: Dict[str, Any] = {}
            for index, delay in enumerate(retry_delays):
                if self._background_stopping or self._dual_vector_pools_enabled():
                    break
                if delay > 0:
                    self._update_dual_vector_auto_migration_stage("retry_delay", retry_index=index, delay_seconds=delay)
                    await self._sleep_background(delay)
                if self._vector_rebuild_lock.locked():
                    self._update_dual_vector_auto_migration_stage("waiting_rebuild_lock", retry_index=index)
                    if index == len(retry_delays) - 1:
                        result = {
                            "success": False,
                            "error": "vector_rebuild_running",
                            "detail": "已有向量重建任务正在运行",
                        }
                    continue
                self._update_dual_vector_auto_migration_stage("rebuild_start", retry_index=index)
                result = await self._rebuild_all_vectors()
                if str(result.get("error", "") or "") != "vector_rebuild_running":
                    break

            success = bool(result.get("success", False)) or self._dual_vector_pools_enabled()
            last_error = ""
            if not success:
                errors = result.get("errors") if isinstance(result, dict) else None
                if isinstance(errors, list) and errors:
                    last_error = "; ".join(str(item) for item in errors[:5])
                else:
                    last_error = str(
                        result.get("detail")
                        or result.get("error")
                        or "dual_vector_auto_migration_failed"
                    )
                logger.warning(f"双池后台自动迁移未完成，继续使用单池: {last_error}")
            else:
                logger.info("双池后台自动迁移完成，已切换到双池检索")
            finished_at = time.time()
            progress = {
                **dict(self._dual_vector_auto_migration_status.get("progress") or {}),
                "result": result,
            }
            progress = self._normalize_dual_vector_auto_migration_progress(
                progress,
                now=finished_at,
                completed=True,
                success=success,
            )
            self._dual_vector_auto_migration_status.update(
                {
                    "running": False,
                    "success": success,
                    "stage": "completed" if success else "failed",
                    "progress": progress,
                    "last_error": last_error[:500],
                    "finished_at": finished_at,
                    "updated_at": finished_at,
                }
            )
        except asyncio.CancelledError:
            finished_at = time.time()
            progress = self._normalize_dual_vector_auto_migration_progress(
                self._dual_vector_auto_migration_status.get("progress"),
                now=finished_at,
                completed=True,
                success=False,
            )
            self._dual_vector_auto_migration_status.update(
                {
                    "running": False,
                    "stage": "cancelled",
                    "progress": progress,
                    "last_error": "cancelled",
                    "finished_at": finished_at,
                    "updated_at": finished_at,
                }
            )
            raise
        except Exception as exc:
            logger.warning(f"双池后台自动迁移异常，继续使用单池: {exc}")
            finished_at = time.time()
            progress = self._normalize_dual_vector_auto_migration_progress(
                self._dual_vector_auto_migration_status.get("progress"),
                now=finished_at,
                completed=True,
                success=False,
            )
            self._dual_vector_auto_migration_status.update(
                {
                    "running": False,
                    "success": False,
                    "stage": "exception",
                    "progress": progress,
                    "last_error": str(exc)[:500],
                    "finished_at": finished_at,
                    "updated_at": finished_at,
                }
            )

    async def _stop_background_tasks(self) -> None:
        async with self._background_lock:
            self._background_stopping = True
            tasks = [task for task in self._background_tasks.values() if task is not None and not task.done()]
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning(f"后台任务退出异常: {exc}")
            self._background_tasks.clear()

    async def _auto_save_loop(self) -> None:
        try:
            while not self._background_stopping:
                interval_minutes = max(1.0, float(self._cfg("advanced.auto_save_interval_minutes", 5) or 5))
                await asyncio.sleep(interval_minutes * 60.0)
                if self._background_stopping:
                    break
                if bool(self._cfg("advanced.enable_auto_save", True)):
                    self._persist()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"auto_save loop 异常: {exc}")

    async def _episode_pending_loop(self) -> None:
        try:
            while not self._background_stopping:
                await asyncio.sleep(60.0)
                if self._background_stopping:
                    break
                if not bool(self._cfg("episode.enabled", True)):
                    continue
                if not bool(self._cfg("episode.generation_enabled", True)):
                    continue
                await self.process_episode_pending_batch(
                    limit=max(1, int(self._cfg("episode.pending_batch_size", 50) or 50)),
                    max_retry=max(1, int(self._cfg("episode.pending_max_retry", 3) or 3)),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"episode_pending loop 异常: {exc}")

    async def _embedding_probe_loop(self) -> None:
        try:
            while not self._background_stopping:
                await asyncio.sleep(self._embedding_probe_interval_seconds())
                if self._background_stopping:
                    break
                startup_deferred = self._is_startup_self_check_deferred()
                if not self._embedding_fallback_enabled() and not startup_deferred:
                    continue
                if not self._is_embedding_degraded() and not startup_deferred:
                    continue
                try:
                    await self._recover_embedding_once()
                except Exception as exc:
                    logger.warning(f"embedding 恢复探测失败: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"embedding_probe loop 异常: {exc}")

    async def _paragraph_vector_backfill_loop(self) -> None:
        try:
            while not self._background_stopping:
                await asyncio.sleep(self._paragraph_vector_backfill_interval_seconds())
                if self._background_stopping:
                    break
                if not self._paragraph_vector_backfill_enabled():
                    continue
                if self._is_embedding_degraded():
                    continue
                await self._run_paragraph_backfill_once(
                    limit=self._paragraph_vector_backfill_batch_size(),
                    max_retry=self._paragraph_vector_backfill_max_retry(),
                    trigger="loop",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"paragraph_vector_backfill loop 异常: {exc}")

    async def _person_profile_refresh_loop(self) -> None:
        try:
            while not self._background_stopping:
                interval_minutes = max(1.0, float(self._cfg("person_profile.refresh_interval_minutes", 30) or 30))
                await asyncio.sleep(max(60.0, interval_minutes * 60.0))
                if self._background_stopping:
                    break
                if not bool(self._cfg("person_profile.enabled", True)):
                    continue
                active_window_hours = max(1.0, float(self._cfg("person_profile.active_window_hours", 72.0) or 72.0))
                max_refresh = max(1, int(self._cfg("person_profile.max_refresh_per_cycle", 50) or 50))
                cutoff = time.time() - active_window_hours * 3600.0
                candidates = [
                    person_id
                    for person_id, seen_at in sorted(
                        self._active_person_timestamps.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )
                    if seen_at >= cutoff
                ][:max_refresh]
                for person_id in candidates:
                    try:
                        if self._has_pending_person_profile_refresh(person_id):
                            continue
                        await self.refresh_person_profile(
                            person_id,
                            limit=max(4, int(self._cfg("person_profile.top_k_evidence", 12) or 12)),
                            mark_active=False,
                        )
                    except Exception as exc:
                        logger.warning(f"刷新人物画像失败: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"person_profile_refresh loop 异常: {exc}")

    async def _person_profile_refresh_queue_loop(self) -> None:
        try:
            while not self._background_stopping:
                await asyncio.sleep(profile_policy.person_profile_refresh_queue_interval_seconds(self._cfg))
                if self._background_stopping:
                    break
                if not bool(self._cfg("person_profile.enabled", True)):
                    continue
                await self._process_person_profile_refresh_queue_batch(
                    limit=profile_policy.person_profile_refresh_queue_batch_size(self._cfg)
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"person_profile_refresh_queue loop 异常: {exc}")
