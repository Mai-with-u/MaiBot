from unittest.mock import Mock

import asyncio
import threading

import pytest

from src.common import startup_diagnostics as diagnostics


def test_sampler_captures_blocked_loop_without_locals_and_stops(monkeypatch):
    sampled = threading.Event()
    messages = []

    def record(message):
        messages.append(message)
        sampled.set()

    monkeypatch.setattr(diagnostics.logger, "debug", record)

    async def blocked_startup():
        secret = "sensitive-local-value"
        with diagnostics.monitor_startup_loop(warn_after=0.02, interval=0.005):
            # 故意占住事件循环，等待独立采样线程采到当前栈；协程看门狗无法通过此测试。
            assert sampled.wait(2.0)
        await asyncio.sleep(0.05)
        assert len(messages) == 1
        assert "blocked_startup" in messages[0]
        assert "启动主循环卡顿现场" in messages[0]
        assert secret not in messages[0]
        assert not any(thread.name == "startup-stack-sampler" for thread in threading.enumerate())

    asyncio.run(blocked_startup())


def test_disabled_sampler_does_not_start_thread(monkeypatch):
    factory = Mock(side_effect=AssertionError("must not start a thread"))
    monkeypatch.setattr(diagnostics.threading, "Thread", factory)
    with diagnostics.monitor_startup_loop(enabled=False):
        pass
    factory.assert_not_called()


def test_stage_preserves_exception_and_records_failure(monkeypatch):
    warning = Mock()
    monkeypatch.setattr(diagnostics.logger, "warning", warning)
    error = ValueError("probe failure")
    with pytest.raises(ValueError) as caught:
        with diagnostics.diagnostic_stage("test.failure"):
            raise error
    assert caught.value is error
    assert "stage=test.failure" in warning.call_args.args[0]
    assert "outcome=failed" in warning.call_args.args[0]
