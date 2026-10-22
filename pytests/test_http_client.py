from unittest.mock import Mock

import asyncio
import threading

import pytest

from src.common import http_client


@pytest.mark.asyncio
async def test_cold_ssl_initialization_yields_and_shares_one_context(monkeypatch):
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    entered = asyncio.Event()
    release = threading.Event()
    context = object()

    def create_context():
        assert threading.get_ident() != loop_thread
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return context

    factory = Mock(side_effect=create_context)
    monkeypatch.setattr(http_client, "_shared_ssl_context", None)
    monkeypatch.setattr(http_client.httpx, "create_ssl_context", factory)
    tasks = [asyncio.create_task(http_client.get_shared_ssl_context_async()) for _ in range(4)]
    try:
        # 证书构造尚未完成时，事件循环必须仍可调度其他协程。
        await asyncio.wait_for(entered.wait(), 2)
        assert not any(task.done() for task in tasks)
    finally:
        release.set()
        results = await asyncio.gather(*tasks)

    assert all(result is context for result in results)
    factory.assert_called_once_with()

    async def unexpected_thread(*args):
        raise AssertionError("热路径不应再进入线程池")

    monkeypatch.setattr(http_client.asyncio, "to_thread", unexpected_thread)
    assert await http_client.get_shared_ssl_context_async() is context
    assert http_client.get_shared_ssl_context() is context
