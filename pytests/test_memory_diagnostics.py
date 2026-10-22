from pathlib import Path
from types import SimpleNamespace
import asyncio
import json
import sys
import threading
import time
import tracemalloc

from src.common import memory_diagnostics as diagnostics


def test_process_memory():
    assert diagnostics.process_memory()["rss_bytes"] > 0


def test_database_counts_does_not_initialize_modules(monkeypatch):
    monkeypatch.delitem(sys.modules, "src.common.database.database", raising=False)
    monkeypatch.delitem(sys.modules, "src.A_memorix.runtime_registry", raising=False)
    assert diagnostics.database_counts() == {}
    assert "src.common.database.database" not in sys.modules


def test_connections_busy_does_not_wait(monkeypatch):
    lock = threading.Lock()
    manager = SimpleNamespace(_lock=lock, _connections={threading.current_thread(): object()})
    store = SimpleNamespace(_connection_manager=manager)
    monkeypatch.setitem(sys.modules, "src.A_memorix.runtime_registry",
                        SimpleNamespace(get_runtime_kernel=lambda: SimpleNamespace(metadata_store=store)))
    with lock:
        assert diagnostics.database_counts()["memory_connections_busy"]
    assert diagnostics.database_counts()["memory_connections"] == 1


def test_light_sampler_rotates_and_stops(tmp_path, monkeypatch):
    # 缩短仅测试实例的间隔，快速覆盖多轮滚动；生产配置至少十秒。
    monkeypatch.setattr(diagnostics, "database_counts", lambda: {"connections": 4})
    loop = asyncio.new_event_loop()
    sampler = diagnostics.MemoryDiagnostics(tmp_path, loop, max_bytes=1024, backups=2)
    sampler.interval = .005
    tracing_before = tracemalloc.is_tracing()
    sampler.start()
    deadline = time.monotonic() + 5
    while not (tmp_path / "memory.jsonl.2").exists() and time.monotonic() < deadline:
        time.sleep(.01)
    sampler.stop()
    loop.close()
    assert not sampler.thread.is_alive()
    assert tracemalloc.is_tracing() == tracing_before
    files = list(tmp_path.glob("memory.jsonl*"))
    assert len(files) == 3
    assert sum(p.stat().st_size for p in files) <= 3 * 1024
    for file in files:
        for line in file.read_text().splitlines():
            item = json.loads(line)
            assert item["rss_bytes"] > 0
            assert "allocations" not in item


def test_deep_diff_has_locations_but_not_contents(tmp_path):
    loop = asyncio.new_event_loop()
    sampler = diagnostics.MemoryDiagnostics(tmp_path, loop, deep=True)
    tracemalloc.start(1)
    try:
        sampler._deep_sample()
        retained = [bytearray(65536) for _ in range(32)]
        result = sampler._deep_sample()
        assert result["traced_bytes"] >= 2 * 1024 * 1024
        assert any(row.get("delta_bytes", 0) > 1024 * 1024 for row in result["locations"])
        assert len(result["locations"]) <= 20
        assert len(retained) == 32
    finally:
        sampler.previous = None
        tracemalloc.stop()
        loop.close()


def test_sampler_does_not_stop_external_tracer(tmp_path):
    loop = asyncio.new_event_loop()
    tracemalloc.start(1)
    sampler = diagnostics.MemoryDiagnostics(tmp_path, loop, deep=True)
    try:
        sampler.start()
        deadline = time.monotonic() + 5
        while sampler.previous is None and time.monotonic() < deadline:
            time.sleep(.01)
        sampler.stop()
        assert not sampler.owns_tracer
        assert tracemalloc.is_tracing()
        assert sampler.previous is None
    finally:
        tracemalloc.stop()
        loop.close()


def test_runtime_sampling_is_bounded(monkeypatch):
    runtime = SimpleNamespace(_chat_history=[object()] * 1000, message_cache=[None] * 3,
                              _internal_turn_queue=asyncio.Queue())
    manager = SimpleNamespace(heartflow_chat_list={i: runtime for i in range(300)})
    monkeypatch.setitem(sys.modules, "src.chat.heart_flow.heartflow_manager", SimpleNamespace(heartflow_manager=manager))
    monkeypatch.delitem(sys.modules, "src.maisaka.context.messages", raising=False)
    async def check():
        counts = diagnostics.runtime_counts()
        assert counts["sampled_runtimes"] == 256
        assert counts["counts_truncated"]
        assert counts["history_items"] == 256000
    asyncio.run(check())


def test_disabled_does_not_start_thread(monkeypatch):
    monkeypatch.setitem(sys.modules, "src.config.config", SimpleNamespace(
        global_config=SimpleNamespace(log=SimpleNamespace(memory_diagnostics_enabled=False))))
    monkeypatch.setattr(diagnostics, "_diagnostics", None)
    monkeypatch.setitem(sys.modules, "src.common.logger", SimpleNamespace(LOG_DIR=Path("unused")))
    diagnostics.start_memory_diagnostics()
    assert diagnostics._diagnostics is None


def test_blocked_loop_has_only_one_pending_callback(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "database_counts", lambda: {})
    loop = asyncio.new_event_loop()
    sampler = diagnostics.MemoryDiagnostics(tmp_path, loop)
    sampler.interval = .005
    sampler.start()
    deadline = time.monotonic() + 5
    while not sampler.pending.is_set() and time.monotonic() < deadline:
        time.sleep(.01)
    time.sleep(.03)
    sampler.stop()
    assert len(loop._ready) == 1
    loop.close()


def test_runtime_callback_records_age_and_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "runtime_counts", lambda: {"history_items": 123})
    async def check():
        sampler = diagnostics.MemoryDiagnostics(tmp_path, asyncio.get_running_loop())
        sampler.interval = .01
        sampler.start()
        deadline = time.monotonic() + 5
        while not sampler.latest and time.monotonic() < deadline:
            await asyncio.sleep(.01)
        sampler.stop()
        assert sampler.latest["history_items"] == 123
        assert sampler.latest["sampled_at"] > 0
        assert "collection_ms" in sampler.latest
    asyncio.run(check())
