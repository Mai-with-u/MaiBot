"""验证冷启动导入边界，以及线程加载期间主循环和连接的归属。"""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import asyncio
import subprocess
import sys
import threading

import pytest

from src.common import startup_diagnostics
from src.config.official_configs import MCPConfig, MCPServerItemConfig
from src.mcp_module import service as service_module


@pytest.mark.parametrize("sdk_preloaded", [False, True])
def test_cold_provider_import_keeps_sdk_off_main_thread(sdk_preloaded: bool) -> None:
    """新进程避免测试收集预先导入 SDK，从而把缓存命中误当成修复。"""
    script = """
import asyncio
import importlib.abc
import importlib
import sys
import threading

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
main_thread = threading.get_ident()
sdk_import_threads = []

class SDKImportGuard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'mcp' or fullname.startswith('mcp.'):
            thread_id = threading.get_ident()
            assert thread_id != main_thread, f'SDK imported on event loop: {fullname}'
            sdk_import_threads.append(thread_id)
        return None

sys.meta_path.insert(0, SDKImportGuard())
from src.mcp_module.provider import MCPToolProvider
from src.mcp_module.service import _load_manager_class, _load_sampling_bridge_class
assert 'mcp' not in sys.modules
assert 'src.mcp_module.manager' not in sys.modules
assert 'src.mcp_module.host_llm_bridge' not in sys.modules

from src.common import startup_diagnostics
events = []
startup_diagnostics.logger.info = events.append
startup_diagnostics.logger.warning = events.append

async def verify():
    if SDK_PRELOADED:
        await asyncio.to_thread(importlib.import_module, 'mcp')
    manager_class = await asyncio.to_thread(_load_manager_class)
    assert sdk_import_threads
    stage = 'mcp.resolve_sdk' if SDK_PRELOADED else 'mcp.import_sdk'
    sdk_starts = [e for e in events if f'stage={stage} ' in e and '阶段开始' in e]
    assert len(sdk_starts) == 1, events
    assert 'thread=MainThread' not in sdk_starts[0]
    from src.mcp_module import MCPManager
    assert MCPManager is manager_class
    before = list(events)
    assert await asyncio.to_thread(_load_manager_class) is manager_class
    assert events == before, 'cached import must not report a second SDK import'
    bridge_class = await asyncio.to_thread(_load_sampling_bridge_class)
    assert bridge_class.__name__ == 'MCPHostLLMBridge'

asyncio.run(verify())
""".replace("SDK_PRELOADED", repr(sdk_preloaded))
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("sampling_enabled", [False, True])
async def test_reload_keeps_loop_responsive_and_creates_connections_on_loop(
    monkeypatch: pytest.MonkeyPatch, sampling_enabled: bool,
) -> None:
    started = threading.Event()
    release = threading.Event()
    loop_thread = threading.get_ident()
    loop = asyncio.get_running_loop()
    manager = Mock(server_count=0, tool_count=0)
    manager.close = AsyncMock()
    manager.get_status_snapshot.return_value = {"initialized": True, "servers": []}
    manager.get_tool_specs.return_value = []
    loads = []
    connects = []
    bridge_loads = []
    callbacks = object()

    class SamplingBridge:
        def __init__(self, **kwargs):
            assert threading.get_ident() == loop_thread
            assert asyncio.get_running_loop() is loop

        def build_callbacks(self):
            return callbacks

    def load_bridge():
        assert threading.get_ident() != loop_thread
        bridge_loads.append(True)
        return SamplingBridge

    class ManagerClass:
        @classmethod
        async def from_app_config(cls, config, **kwargs):
            assert threading.get_ident() == loop_thread
            assert asyncio.get_running_loop() is loop
            assert kwargs["host_callbacks"] is (callbacks if sampling_enabled else None)
            connects.append(config)
            return manager

    def load_manager():
        assert threading.get_ident() != loop_thread
        loads.append(True)
        started.set()
        assert release.wait(3), "loop failed to release the import worker"
        return ManagerClass

    monkeypatch.setattr(service_module, "_load_manager_class", load_manager)
    monkeypatch.setattr(service_module, "_load_sampling_bridge_class", load_bridge)
    service = service_module.MCPService()
    config = MCPConfig(servers=[MCPServerItemConfig(name="local", command="server")])
    config.client.sampling.enable = sampling_enabled
    first = asyncio.create_task(service.reload(config))
    second = asyncio.create_task(service.reload(config))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
        # 导入线程仍被门闩挡住，主循环必须能继续调度，且尚未开始建立连接。
        await asyncio.sleep(0)
        assert not first.done()
        assert not connects
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=3)
        assert len(loads) == len(connects) == 1
        assert len(bridge_loads) == int(sampling_enabled)
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_empty_config_does_not_load_runtime_dependencies(monkeypatch) -> None:
    loader = Mock(side_effect=AssertionError("unused MCP dependencies must stay unloaded"))
    monkeypatch.setattr(service_module, "_load_manager_class", loader)
    monkeypatch.setattr(service_module, "_load_sampling_bridge_class", loader)
    service = service_module.MCPService()
    await service.reload(MCPConfig())
    assert await service.list_tools() == []
    loader.assert_not_called()
    await service.close()


@pytest.mark.asyncio
async def test_import_error_propagates_and_reports_failed_preparation(monkeypatch) -> None:
    error = RuntimeError("import failed")
    warning = Mock()
    monkeypatch.setattr(startup_diagnostics.logger, "warning", warning)
    monkeypatch.setattr(service_module, "_load_manager_class", Mock(side_effect=error))
    service = service_module.MCPService()
    config = MCPConfig(servers=[MCPServerItemConfig(name="local", command="server")])
    with pytest.raises(RuntimeError) as caught:
        await service.reload(config)
    assert caught.value is error
    assert any(
        "stage=mcp.prepare_manager " in call.args[0] and "outcome=failed" in call.args[0]
        for call in warning.call_args_list
    )
    assert service.get_status_snapshot()["initialized"] is False
    await service.close()
