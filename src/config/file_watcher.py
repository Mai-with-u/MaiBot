from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Sequence

from watchfiles import Change, awatch

import asyncio
import uuid

from src.common.logger import get_logger


logger = get_logger("file_watcher")


@dataclass(frozen=True)
class FileChange:
    change_type: Change
    path: Path


ChangeCallback = Callable[[Sequence[FileChange]], Awaitable[None] | None]


@dataclass(frozen=True)
class FileWatchSubscription:
    subscription_id: str
    callback: ChangeCallback
    paths: tuple[Path, ...]
    change_types: frozenset[Change] | None


@dataclass
class SubscriptionState:
    consecutive_failures: int = 0
    cooldown_until_monotonic: float = 0.0


@dataclass
class FileWatcherStats:
    batches_seen: int = 0
    changes_seen: int = 0
    callbacks_succeeded: int = 0
    callbacks_failed: int = 0
    callbacks_timed_out: int = 0
    callbacks_skipped_cooldown: int = 0
    restart_count: int = 0


class FileWatcher:
    def __init__(
        self,
        paths: Iterable[Path],
        debounce_ms: int = 600,
        callback_timeout_s: float = 10.0,
        callback_failure_threshold: int = 3,
        callback_cooldown_s: float = 30.0,
    ) -> None:
        self._paths = [path.resolve() for path in paths]
        self._debounce_ms = debounce_ms
        self._callback_timeout_s = callback_timeout_s
        self._callback_failure_threshold = callback_failure_threshold
        self._callback_cooldown_s = callback_cooldown_s
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._subscriptions: dict[str, FileWatchSubscription] = {}
        self._subscription_states: dict[str, SubscriptionState] = {}
        self._stats = FileWatcherStats()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def stats(self) -> FileWatcherStats:
        return FileWatcherStats(
            batches_seen=self._stats.batches_seen,
            changes_seen=self._stats.changes_seen,
            callbacks_succeeded=self._stats.callbacks_succeeded,
            callbacks_failed=self._stats.callbacks_failed,
            callbacks_timed_out=self._stats.callbacks_timed_out,
            callbacks_skipped_cooldown=self._stats.callbacks_skipped_cooldown,
            restart_count=self._stats.restart_count,
        )

    def subscribe(
        self,
        callback: ChangeCallback,
        *,
        paths: Iterable[Path] | None = None,
        change_types: Iterable[Change] | None = None,
    ) -> str:
        if not callable(callback):
            raise TypeError("callback 必须是可调用对象")

        normalized_paths = tuple(path.resolve() for path in paths) if paths is not None else ()
        normalized_change_types = frozenset(change_types) if change_types is not None else None

        subscription_id = str(uuid.uuid4())
        self._subscriptions[subscription_id] = FileWatchSubscription(
            subscription_id=subscription_id,
            callback=callback,
            paths=normalized_paths,
            change_types=normalized_change_types,
        )
        self._subscription_states[subscription_id] = SubscriptionState()
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        removed = self._subscriptions.pop(subscription_id, None) is not None
        self._subscription_states.pop(subscription_id, None)
        return removed

    async def start(self) -> None:
        if self._running:
            return
        if not self._subscriptions:
            raise RuntimeError("启动文件监视器前必须至少注册一个订阅")
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            return
        finally:
            self._task = None

    async def _run(self) -> None:
        while self._running:
            try:
                async for changes in awatch(*self._paths, debounce=self._debounce_ms):
                    if not self._running:
                        break
                    normalized_changes = self._normalize_changes(changes)
                    if not normalized_changes:
                        continue
                    self._stats.batches_seen += 1
                    self._stats.changes_seen += len(normalized_changes)
                    try:
                        await self._dispatch_changes(normalized_changes)
                    except Exception as exc:
                        logger.warning(f"文件变更分发失败: {exc}")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._stats.restart_count += 1
                logger.error(f"文件监视器运行异常，将在1秒后重试: {exc}")
                if self._running:
                    await asyncio.sleep(1.0)

    async def _dispatch_changes(self, changes: Sequence[FileChange]) -> None:
        for subscription in list(self._subscriptions.values()):
            matched_changes = self._match_changes(changes, subscription)
            if not matched_changes:
                continue
            state = self._subscription_states.get(subscription.subscription_id)
            if state is None:
                continue
            now_monotonic = asyncio.get_running_loop().time()
            if state.cooldown_until_monotonic > now_monotonic:
                self._stats.callbacks_skipped_cooldown += 1
                continue
            try:
                await asyncio.wait_for(self._invoke_callback(subscription.callback, matched_changes), timeout=self._callback_timeout_s)
                state.consecutive_failures = 0
                self._stats.callbacks_succeeded += 1
            except asyncio.TimeoutError:
                self._stats.callbacks_timed_out += 1
                self._stats.callbacks_failed += 1
                self._mark_callback_failure(subscription.subscription_id)
                logger.warning(
                    f"文件变更回调执行超时（subscription_id={subscription.subscription_id}, timeout={self._callback_timeout_s}s）"
                )
            except Exception as exc:
                self._stats.callbacks_failed += 1
                self._mark_callback_failure(subscription.subscription_id)
                logger.warning(f"文件变更回调执行失败（subscription_id={subscription.subscription_id}）: {exc}")

    async def _invoke_callback(self, callback: ChangeCallback, changes: Sequence[FileChange]) -> None:
        if asyncio.iscoroutinefunction(callback):
            await callback(changes)
            return
        await asyncio.to_thread(callback, changes)

    def _mark_callback_failure(self, subscription_id: str) -> None:
        state = self._subscription_states.get(subscription_id)
        if state is None:
            return
        state.consecutive_failures += 1
        if state.consecutive_failures >= self._callback_failure_threshold:
            now_monotonic = asyncio.get_running_loop().time()
            state.cooldown_until_monotonic = now_monotonic + self._callback_cooldown_s
            state.consecutive_failures = 0
            logger.warning(
                f"文件变更回调进入冷却（subscription_id={subscription_id}, cooldown={self._callback_cooldown_s}s）"
            )

    def _match_changes(self, changes: Sequence[FileChange], subscription: FileWatchSubscription) -> list[FileChange]:
        matched: list[FileChange] = []
        for change in changes:
            if subscription.change_types is not None and change.change_type not in subscription.change_types:
                continue
            if subscription.paths and not any(self._path_matches(change.path, path) for path in subscription.paths):
                continue
            matched.append(change)
        return matched

    def _path_matches(self, changed_path: Path, subscribed_path: Path) -> bool:
        if subscribed_path.is_dir():
            return changed_path == subscribed_path or changed_path.is_relative_to(subscribed_path)
        return changed_path == subscribed_path

    def _normalize_changes(self, changes: set[tuple[Change, str]]) -> list[FileChange]:
        return [FileChange(change_type=change, path=Path(path).resolve()) for change, path in changes]
