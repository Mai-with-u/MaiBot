"""真实 Host → Kernel 生命周期；存储替身仅记录调用或抛出故障。"""

from pathlib import Path

import asyncio
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("failure", [None, "persist", "metadata_close"])
def test_host_awaits_kernel_and_propagates_storage_failure(tmp_path, failure):
    # 新解释器隔离模块缓存；不污染常规 pytest 会话，也不导入宿主配置/业务数据库。
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytests.A_memorix_test.test_host_shutdown_failure_propagation",
            str(tmp_path),
            failure or "normal",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env=os.environ | {"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def check_host_shutdown(tmp_path, monkeypatch, failure):
    from src.A_memorix.host_service import AMemorixHostService
    from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel

    events = []
    kernel = SDKMemoryKernel(plugin_root=tmp_path, config={"storage": {"data_dir": str(tmp_path / "memory")}})
    kernel._runtime_writer_lock.acquire()
    kernel._initialized = True

    class Metadata:
        def close(self):
            events.append("metadata_close")
            if failure == "metadata_close":
                raise RuntimeError("fixture metadata close failed")

    async def stop_background():
        events.append("stop_background")

    def persist():
        events.append("persist")
        if failure == "persist":
            raise RuntimeError("fixture persist failed")

    kernel.metadata_store = Metadata()
    monkeypatch.setattr(kernel, "_stop_background_tasks", stop_background)
    monkeypatch.setattr(kernel, "_persist", persist)
    # 不读取实际 config 或启动 Host；仅构造 stop() 所需的状态。
    host = object.__new__(AMemorixHostService)
    host._lock = asyncio.Lock()
    host._startup_task = None
    host._kernel = kernel
    host._runtime_state = "running"
    try:
        if failure:
            with pytest.raises(RuntimeError, match="fixture"):
                await host.stop()
        else:
            await host.stop()
            assert host._kernel is None
            assert host._runtime_state == "stopped"
        assert events == ["stop_background", "persist", "metadata_close"]
        assert not kernel._runtime_writer_lock.held
        assert not kernel._initialized
    finally:
        kernel._runtime_writer_lock.release()


if __name__ == "__main__":
    from pytests.startup_test.memory_shutdown_fixture import isolated

    with isolated() as patch:
        asyncio.run(check_host_shutdown(Path(sys.argv[1]), patch, None if sys.argv[2] == "normal" else sys.argv[2]))
