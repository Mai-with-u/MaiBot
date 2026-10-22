"""进程内更新检查缓存；定时刷新在线程执行，不占用 WebUI 事件循环。"""

from dataclasses import replace
from threading import Lock, Timer
from typing import Optional
import time

from src.common.logger import get_logger
from src.common.self_update import LocalState, UpdateCheck, check_for_update, inspect_local

logger = get_logger("webui_self_update")
CACHE_TTL = 60 * 60


class SelfUpdateCache:
    def __init__(self) -> None:
        self._lock = Lock()
        self._check: Optional[UpdateCheck] = None
        self._expires_at = 0.0
        self._timer: Optional[Timer] = None
        self._active = False
        self._source_id = "github"
        self._custom_prefix = ""

    def start(self) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False
        if self._timer is not None:
            self._timer.cancel()

    def _schedule(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        if self._active:
            self._timer = Timer(CACHE_TTL, self._refresh)
            self._timer.daemon = True
            self._timer.start()

    def _refresh(self) -> None:
        try:
            # get 会在锁内复核 TTL，避免与刚完成的手动检查重复联网。
            self.get()
        except Exception:
            logger.exception("自动刷新更新检查失败，60 分钟后重试")

    def peek(self, local: LocalState) -> UpdateCheck:
        cached = self._check
        if local.blocker is not None:
            return UpdateCheck(local=local, blockers=[local.blocker])
        if cached is None or (
            cached.local.branch, cached.local.commit, cached.local.version, cached.local.deployment_type
        ) != (local.branch, local.commit, local.version, local.deployment_type):
            return UpdateCheck(local=local)
        # 未提交文件可能在缓存期内变化，始终使用当前工作区状态。
        blockers = [item for item in cached.blockers if item != "dirty_worktree"]
        if local.dirty_files:
            blockers.append("dirty_worktree")
        return replace(cached, local=local, blockers=blockers)

    def get(
        self, force: bool = False, source_id: Optional[str] = None, custom_prefix: str = "",
    ) -> UpdateCheck:
        # 合并并发的自动检查；手动检查即使有缓存也明确重新联网。
        with self._lock:
            local = inspect_local()
            cached = self.peek(local)
            if local.blocker is not None:
                self._schedule()
                return cached
            if not force and cached.checked_at is not None and time.monotonic() < self._expires_at:
                return cached
            if source_id is not None:
                self._source_id = source_id
                self._custom_prefix = custom_prefix
            elif self._check is None:
                self._source_id = "pypi" if local.deployment_type == "package" else "github"
            try:
                check = check_for_update(source_id=self._source_id, custom_prefix=self._custom_prefix)
                self._check = check
                self._expires_at = time.monotonic() + CACHE_TTL
                return check
            finally:
                # 失败不伪装成新缓存；记录完整错误并延后下一次定时重试。
                self._schedule()


self_update_cache = SelfUpdateCache()
