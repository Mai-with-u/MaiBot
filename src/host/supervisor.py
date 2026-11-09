"""Bot 生命周期：注册表记录期望状态，宿主让实际进程向它收敛。"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Set

import asyncio
import logging
import sys
import time

from src.common.ipc import allocate_endpoint, release_endpoint
from src.common.process_lease import ProcessLease, instance_lock_path
from src.common.runtime_context import (
    RESTART_EXIT_CODE,
    RUNTIME_CONTEXT_ENV,
    WORKER_SHUTDOWN_TIMEOUT,
    RuntimeContext,
    read_phase,
)
from src.config.shared_models import CATALOG_FILENAME, SHARED_MODELS_ENV
from src.config.legacy_upgrade_confirmation import LEGACY_UPGRADE_CONFIRM_ENV, collect_legacy_upgrade_reasons

from .layout import Workspace, bot_environment
from .process import ManagedProcess
from .registry import BotRecord


# 宿主的硬截止比 Worker 自身的关闭预算多出余量，超时才强制回收进程树。
STOP_TIMEOUT = WORKER_SHUTDOWN_TIMEOUT + 15.0
RESTART_DELAY_MIN = 1.0
RESTART_DELAY_MAX = 300.0
CRASH_WINDOW = 600.0
CRASH_LIMIT = 5
_POLL_INTERVAL = 0.25
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def worker_command(_bot: BotRecord) -> List[str]:
    return [sys.executable, "-m", "src.runtime"]


@dataclass
class _Runtime:
    bot: BotRecord
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    process: Optional[ManagedProcess] = None
    watcher: Optional["asyncio.Task[None]"] = None
    retry: Optional["asyncio.Task[None]"] = None
    endpoint: Optional[str] = None
    status: str = "stopped"
    detail: str = ""
    ready: bool = False
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    started_at: Optional[str] = None
    exited_at: Optional[str] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    retry_at: Optional[float] = None
    crashes: Deque[float] = field(default_factory=deque)

    @property
    def log_path(self) -> Path:
        return self.bot.directory / "temp" / "worker-console.log"

    @property
    def status_path(self) -> Path:
        return self.bot.directory / "temp" / "host-status.json"

    def snapshot(self) -> Dict[str, Any]:
        return {
            "bot_id": self.bot.bot_id,
            "name": self.bot.name,
            "directory": str(self.bot.directory),
            "owned": self.bot.owned,
            "created_at": self.bot.created_at,
            "initialization": self.bot.initialization,
            "initialization_error": self.bot.error,
            "desired_state": self.bot.desired_state,
            "restart_policy": self.bot.restart_policy,
            "status": self.status,
            "detail": self.detail,
            "pid": self.process.pid if self.process is not None else None,
            "started_at": self.started_at,
            "exited_at": self.exited_at,
            "exit_code": self.exit_code,
            "error": self.error,
            "retry_at": self.retry_at,
            "log_path": str(self.log_path),
        }


class BotSupervisor:
    """status 表示进程的实际状态；running 仅在 Worker 报告就绪后成立。"""

    def __init__(
        self, workspace: Workspace, *, public_base_url: str,
        command_factory: Callable[[BotRecord], List[str]] = worker_command,
        stop_timeout: float = STOP_TIMEOUT,
    ) -> None:
        self.workspace = workspace
        self.public_base_url = public_base_url
        self.upgrade_confirmed: Set[str] = set()
        self._command_factory = command_factory
        self._stop_timeout = stop_timeout
        self._runtimes: Dict[str, _Runtime] = {}
        self._closing = False
        self._maintenance = False
        self._resume_task: Optional["asyncio.Task[List[str]]"] = None
        self.prepare_dependencies: Optional[Callable[[], Awaitable[Any]]] = None
        self.control_endpoint: Optional[str] = None

    def _start_blocker(self) -> Optional[str]:
        """关闭或维护期间不能启动 Bot，崩溃后的自动重启与 Worker 的重启请求也一并暂停。"""
        if self._closing:
            return "宿主正在关闭，不能启动 Bot"
        if self._maintenance:
            return "宿主正在维护，完成后会按期望状态恢复 Bot"
        return None

    async def _runtime(self, bot_id: str) -> _Runtime:
        bot = await asyncio.to_thread(self.workspace.registry.get_bot, bot_id)
        runtime = self._runtimes.setdefault(bot_id, _Runtime(bot))
        runtime.bot = bot
        return runtime

    async def statuses(self) -> List[Dict[str, Any]]:
        bots = await asyncio.to_thread(self.workspace.registry.list_bots)
        result = []
        for bot in bots:
            runtime = self._runtimes.setdefault(bot.bot_id, _Runtime(bot))
            runtime.bot = bot
            result.append(runtime.snapshot())
        return result

    async def status(self, bot_id: str) -> Dict[str, Any]:
        return (await self._runtime(bot_id)).snapshot()

    def proxy_endpoint(self, bot_id: str) -> str:
        """只读内存状态；检查与读取之间没有 await，端点必然属于当前这次运行。"""
        runtime = self._runtimes.get(bot_id)
        if runtime is None or runtime.status != "running" or runtime.endpoint is None:
            raise RuntimeError("此 Bot 未运行或尚未就绪")
        return runtime.endpoint

    # ── 进程 ────────────────────────────────────────────────

    def _environment(self, runtime: _Runtime, endpoint: str) -> Dict[str, str]:
        env = bot_environment(runtime.bot.directory)
        if self.control_endpoint is not None:
            env["MAIBOT_HOST_CONTROL_ENDPOINT"] = self.control_endpoint
        env[SHARED_MODELS_ENV] = str(self.workspace.host_directory / CATALOG_FILENAME)
        context = RuntimeContext(
            bot_id=runtime.bot.bot_id, http_endpoint=endpoint,
            public_base_url=self.public_base_url, status_path=runtime.status_path,
        )
        env[RUNTIME_CONTEXT_ENV] = context.to_env()
        if runtime.bot.bot_id in self.upgrade_confirmed:
            env[LEGACY_UPGRADE_CONFIRM_ENV] = "1"
        return env

    async def _spawn(self, runtime: _Runtime) -> None:
        """启动一次 Worker；任何失败都记录到 status 与 error 后再抛出。"""
        bot = runtime.bot
        runtime.status, runtime.detail, runtime.ready = "starting", "", False
        runtime.error = runtime.exit_code = runtime.exited_at = runtime.retry_at = None
        runtime.settled.clear()
        endpoint: Optional[str] = None
        try:
            if bot.initialization != "initialized":
                raise ValueError(f"Bot 尚未完成配置初始化: {bot.name}")
            reasons = await asyncio.to_thread(collect_legacy_upgrade_reasons, bot.directory)
            if reasons and bot.bot_id not in self.upgrade_confirmed:
                raise RuntimeError(f"Bot {bot.name} 需要确认 0.x 升级，请在终端重新启动宿主并确认：" + "；".join(reasons))
            await asyncio.to_thread(runtime.status_path.unlink, missing_ok=True)
            endpoint = allocate_endpoint("bot")
            runtime.process = await ManagedProcess.start(
                self._command_factory(bot), cwd=bot.directory, env=self._environment(runtime, endpoint),
                log_path=runtime.log_path, console_prefix=f"[{bot.name}] ",
            )
        except BaseException as exc:
            if endpoint is not None:
                release_endpoint(endpoint)
            runtime.status, runtime.error, runtime.exited_at = "failed", f"{type(exc).__name__}: {exc}", _now()
            runtime.settled.set()
            raise
        runtime.endpoint = endpoint
        runtime.started_at = _now()
        runtime.watcher = asyncio.create_task(self._watch(runtime, runtime.process))

    def _release(self, runtime: _Runtime, process: ManagedProcess, code: int) -> None:
        runtime.process = None
        runtime.exit_code = code
        runtime.exited_at = _now()
        if runtime.endpoint is not None:
            release_endpoint(runtime.endpoint)
            runtime.endpoint = None
        if process.forced or process.stop_error:
            runtime.error = process.stop_error or "进程树已强制终止；未确认优雅关闭"
            logger.error("Bot %s 关闭异常: PID=%s 退出码=%s; %s", runtime.bot.name, process.pid, code, runtime.error)
        runtime.settled.set()

    async def _watch(self, runtime: _Runtime, process: ManagedProcess) -> None:
        wait = asyncio.ensure_future(process.wait())
        while not wait.done():
            await asyncio.wait({wait}, timeout=_POLL_INTERVAL)
            try:
                phase = await asyncio.to_thread(read_phase, runtime.status_path)
            except ValueError as exc:
                phase = None
                runtime.detail = str(exc)
            async with runtime.lock:
                if runtime.process is not process or phase is None or runtime.status == "stopping":
                    continue
                runtime.detail = phase["detail"]
                if phase["phase"] == "ready":
                    runtime.status, runtime.ready = "running", True
                    runtime.settled.set()
                elif phase["phase"] == "migrating":
                    runtime.status = "migrating"
                elif phase["phase"] == "stopping":
                    runtime.status = "stopping"
        try:
            code = wait.result()
        except Exception as exc:
            async with runtime.lock:
                if runtime.process is process:
                    # 无法确认进程树已退出：保留句柄，不能把它当成已停止。
                    runtime.status, runtime.error = "failed", f"无法确认进程树已退出: {exc}"
                    logger.exception("Bot %s 进程树回收失败: PID=%s", runtime.bot.name, process.pid)
                    runtime.settled.set()
            return
        async with runtime.lock:
            if runtime.process is not process or runtime.status == "stopping":
                return  # 由 stop() 处理请求的退出。
            was_ready = runtime.ready
            self._release(runtime, process, code)
            if code == RESTART_EXIT_CODE and not process.forced and self._start_blocker() is None:
                # Worker 主动请求重启（如配置或插件变更），立即用同一登记重新拉起。
                await self._spawn_reported(runtime)
                return
            if code == 0 and not process.forced:
                runtime.status = "exited"
                return
            runtime.status = "failed"
            runtime.error = runtime.error or f"Worker 退出码 {code}；请查看 {runtime.log_path}"
            if was_ready and self._start_blocker() is None:
                self._schedule_restart(runtime)

    async def _spawn_reported(self, runtime: _Runtime) -> None:
        """后台重启没有调用方可以接收异常；_spawn 已把原因记录到 status 与 error。"""
        try:
            await self._spawn(runtime)
        except Exception:
            return

    def _schedule_restart(self, runtime: _Runtime) -> None:
        """就绪后的崩溃按指数退避重启；启动阶段的失败不重试，避免反复执行失败的迁移。"""
        if runtime.bot.desired_state != "running" or runtime.bot.restart_policy != "on-failure":
            return
        now = time.monotonic()
        runtime.crashes.append(now)
        while runtime.crashes and runtime.crashes[0] < now - CRASH_WINDOW:
            runtime.crashes.popleft()
        if len(runtime.crashes) >= CRASH_LIMIT:
            runtime.status = "crash_loop"
            runtime.error = f"{CRASH_WINDOW / 60:g} 分钟内崩溃 {len(runtime.crashes)} 次，已停止自动重启：{runtime.error}"
            return
        delay = min(RESTART_DELAY_MIN * 2 ** (len(runtime.crashes) - 1), RESTART_DELAY_MAX)
        runtime.retry_at = time.time() + delay

        async def restart_later() -> None:
            await asyncio.sleep(delay)
            async with runtime.lock:
                runtime.retry = None
                if self._start_blocker() or runtime.process is not None or runtime.bot.desired_state != "running":
                    return
                await self._spawn_reported(runtime)

        runtime.retry = asyncio.create_task(restart_later())

    def _cancel_retry(self, runtime: _Runtime) -> None:
        if runtime.retry is not None:
            runtime.retry.cancel()
            runtime.retry = None
        runtime.retry_at = None

    async def _stop_locked(self, runtime: _Runtime) -> bool:
        """返回本次停止是否干净完成；没有运行中的进程视为完成。"""
        self._cancel_retry(runtime)
        process = runtime.process
        if process is None:
            if runtime.status not in {"failed", "crash_loop", "exited"}:
                runtime.status = "stopped"
            return True
        runtime.status = "stopping"
        code = await process.stop(timeout=self._stop_timeout)
        self._release(runtime, process, code)
        clean = not (process.forced or process.stop_error)
        runtime.status = "stopped" if clean else "failed"
        return clean

    # ── 对外操作 ────────────────────────────────────────────

    async def manage(self, bot_id: str, action: str, name: str = "") -> Dict[str, Any]:
        from .bot_operations import archive_bot, clone_bot

        runtime = await self._runtime(bot_id)
        async with runtime.lock:
            if blocker := self._start_blocker():
                raise RuntimeError(blocker)
            if action == "rename":
                runtime.bot = await asyncio.to_thread(self.workspace.registry.rename_bot, bot_id, name)
                return runtime.snapshot()
            if action not in {"archive", "clone"}:
                raise ValueError("未知管理操作")
            if not await self._stop_locked(runtime):
                raise RuntimeError(f"停止 Bot 未完成，已取消操作：{runtime.error}")
            if action == "archive":
                runtime.bot = await asyncio.to_thread(self.workspace.registry.set_desired_state, bot_id, "stopped")
                result = await asyncio.to_thread(archive_bot, self.workspace, runtime.bot)
                self._runtimes.pop(bot_id, None)
                return result
            try:
                bot = await asyncio.to_thread(clone_bot, self.workspace, runtime.bot, name)
                return {"bot_id": bot.bot_id, "name": bot.name, "status": "stopped"}
            finally:
                if runtime.bot.desired_state == "running" and self._start_blocker() is None:
                    await self._spawn_reported(runtime)

    async def run_file_operation(self, bot_id: str, operation: Any, *args: Any) -> None:
        """保持期望状态，锁住目标实例并等待磁盘事务结束，期间禁止再次拉起。"""
        runtime = await self._runtime(bot_id)
        async with runtime.lock:
            if blocker := self._start_blocker():
                raise RuntimeError(blocker)
            if not await self._stop_locked(runtime):
                raise RuntimeError("Bot 未干净停止，已取消数据传输")
            def run_locked() -> None:
                with ProcessLease(instance_lock_path(runtime.bot.directory)):
                    operation(*args)

            task = asyncio.create_task(asyncio.to_thread(run_locked))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    async def start(self, bot_id: str) -> Dict[str, Any]:
        """把期望状态设为运行并启动；不等待就绪。"""
        runtime = await self._runtime(bot_id)
        if blocker := self._start_blocker():
            raise RuntimeError(blocker)
        if self.prepare_dependencies is not None:
            await self.prepare_dependencies()
        async with runtime.lock:
            if blocker := self._start_blocker():
                raise RuntimeError(blocker)
            runtime.bot = await asyncio.to_thread(self.workspace.registry.set_desired_state, bot_id, "running")
            runtime.crashes.clear()
            self._cancel_retry(runtime)
            if runtime.process is None:
                await self._spawn(runtime)
            return runtime.snapshot()

    async def stop(self, bot_id: str) -> Dict[str, Any]:
        runtime = await self._runtime(bot_id)
        async with runtime.lock:
            runtime.bot = await asyncio.to_thread(self.workspace.registry.set_desired_state, bot_id, "stopped")
            await self._stop_locked(runtime)
            return runtime.snapshot()

    async def restart(self, bot_id: str) -> Dict[str, Any]:
        runtime = await self._runtime(bot_id)
        if blocker := self._start_blocker():
            raise RuntimeError(blocker)
        if self.prepare_dependencies is not None:
            await self.prepare_dependencies()
        async with runtime.lock:
            if blocker := self._start_blocker():
                raise RuntimeError(blocker)
            runtime.bot = await asyncio.to_thread(self.workspace.registry.set_desired_state, bot_id, "running")
            if not await self._stop_locked(runtime):
                raise RuntimeError(f"停止过程异常，未启动新的 Bot: {runtime.error}")
            runtime.crashes.clear()
            await self._spawn(runtime)
            return runtime.snapshot()

    async def start_desired(self) -> List[str]:
        """按登记顺序逐个启动：前一个就绪或失败后再启动下一个，避免多个大数据迁移同时争抢磁盘。"""
        if self._start_blocker():
            return []
        if self.prepare_dependencies is not None:
            try:
                await self.prepare_dependencies()
            except Exception as exc:
                for bot in await asyncio.to_thread(self.workspace.registry.list_bots):
                    if bot.desired_state != "running":
                        continue
                    runtime = await self._runtime(bot.bot_id)
                    async with runtime.lock:
                        if runtime.process is None:
                            runtime.status, runtime.error = "failed", f"插件依赖准备失败: {exc}"
                            runtime.settled.set()
                return [f"插件依赖准备失败: {exc}"]
        failures = []
        for bot in await asyncio.to_thread(self.workspace.registry.list_bots):
            if self._start_blocker():
                break
            if bot.desired_state != "running" or bot.initialization != "initialized":
                continue
            runtime = await self._runtime(bot.bot_id)
            async with runtime.lock:
                if runtime.process is not None:
                    continue
                try:
                    await self._spawn(runtime)
                except Exception as exc:
                    failures.append(f"{bot.name}: {exc}")
                    continue
            await runtime.settled.wait()
            if runtime.status != "running":
                failures.append(f"{bot.name}: {runtime.error}")
        return failures

    async def enter_maintenance(self) -> None:
        """停止全部 Bot 但不改变期望状态；之后不再启动 Bot，直到 leave_maintenance。"""
        if self._maintenance:
            raise RuntimeError("宿主已在维护中")
        self._maintenance = True

        async def stop_runtime(runtime: _Runtime) -> None:
            async with runtime.lock:
                if not await self._stop_locked(runtime):
                    raise RuntimeError(f"{runtime.bot.name}: {runtime.error}")

        results = await asyncio.gather(*(stop_runtime(item) for item in self._runtimes.values()), return_exceptions=True)
        failures = [str(result) for result in results if isinstance(result, BaseException)]
        if failures:
            # 有进程未确认退出时不能修改共享环境：取消本次维护，其余 Bot 按期望状态恢复。
            self.leave_maintenance()
            raise RuntimeError("停止 Bot 未完成，已取消维护: " + "; ".join(failures))

    def leave_maintenance(self) -> "asyncio.Task[List[str]]":
        """结束维护，并在后台按期望状态逐个恢复 Bot。"""
        self._maintenance = False
        self._resume_task = asyncio.create_task(self.start_desired())
        return self._resume_task

    async def shutdown(self) -> None:
        """停止全部 Bot 但不改变期望状态，下次启动宿主时恢复。"""
        self._closing = True

        async def stop_runtime(runtime: _Runtime) -> None:
            async with runtime.lock:
                if not await self._stop_locked(runtime):
                    raise RuntimeError(f"{runtime.bot.name}: {runtime.error}")

        results = await asyncio.gather(*(stop_runtime(item) for item in self._runtimes.values()), return_exceptions=True)
        watchers = [item.watcher for item in self._runtimes.values() if item.watcher is not None]
        if self._resume_task is not None:
            # 维护后的恢复在关闭标记下会停止启动新的 Bot。
            watchers.append(self._resume_task)
        await asyncio.gather(*watchers, return_exceptions=True)
        failures = [str(result) for result in results if isinstance(result, BaseException)]
        if failures:
            raise RuntimeError("宿主关闭未完成: " + "; ".join(failures))
