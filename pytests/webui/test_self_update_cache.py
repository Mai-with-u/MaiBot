from dataclasses import replace

import pytest

from src.common.self_update import LocalState, SelfUpdateError, UpdateCheck
from src.webui.services import self_update_cache as module


@pytest.fixture
def cache_setup(monkeypatch):
    clock = [100.0]
    local = [LocalState(version="1.0.0", branch="dev", commit="1" * 40)]
    calls = []
    timers = []

    class FakeTimer:
        def __init__(self, interval, callback):
            assert interval == 3600
            self.callback = callback
            self.cancelled = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    def check(**kwargs):
        calls.append(kwargs)
        return UpdateCheck(local=local[0], checked_at=clock[0], latest_commit="2" * 40)

    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "inspect_local", lambda: local[0])
    monkeypatch.setattr(module, "check_for_update", check)
    monkeypatch.setattr(module, "Timer", FakeTimer)
    cache = module.SelfUpdateCache()
    cache.start()
    yield cache, clock, local, calls, timers
    cache.stop()


def test_auto_requests_reuse_cache_and_manual_forces_refresh(cache_setup):
    cache, clock, _, calls, timers = cache_setup
    cache.get()
    clock[0] += 3599
    cache.get()
    assert len(calls) == 1
    cache.get(force=True)
    assert len(calls) == 2
    assert timers[0].cancelled


def test_expiry_refreshes_without_a_web_request_and_stop_cancels_timer(cache_setup):
    cache, clock, _, calls, timers = cache_setup
    cache.get()
    clock[0] += 3600
    timers[-1].callback()
    assert len(calls) == 2
    assert len(timers) == 2
    cache.stop()
    assert timers[-1].cancelled


@pytest.mark.parametrize("changes", [{"commit": "3" * 40}, {"branch": "main"}, {"version": "1.1.0"}])
def test_local_identity_change_invalidates_cache(cache_setup, changes):
    cache, _, local, calls, _ = cache_setup
    cache.get()
    local[0] = replace(local[0], **changes)
    cache.get()
    assert len(calls) == 2


def test_workspace_changes_are_visible_without_network(cache_setup):
    cache, _, local, calls, _ = cache_setup
    cache.get()
    local[0] = replace(local[0], dirty_files=["example.py"])
    assert "dirty_worktree" in cache.get().blockers
    assert len(calls) == 1


def test_refresh_failure_preserves_timestamp_and_schedules_retry(cache_setup, monkeypatch):
    cache, clock, local, _, timers = cache_setup
    old = cache.get()

    def fail(**kwargs):
        raise SelfUpdateError("fetch_failed", "offline")

    monkeypatch.setattr(module, "check_for_update", fail)
    clock[0] += 3600
    timers[-1].callback()
    assert cache.peek(local[0]).checked_at == old.checked_at
    assert len(timers) == 2
    with pytest.raises(SelfUpdateError, match="offline"):
        cache.get(force=True)
