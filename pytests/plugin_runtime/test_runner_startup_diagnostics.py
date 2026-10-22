from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncio

import pytest

from src.common import startup_diagnostics
from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor
from src.plugin_runtime.runner import rpc_client, runner_main


def test_handshake_timeout_keeps_failure_stage_and_exception(monkeypatch):
    error = TimeoutError("host did not respond")
    connection = SimpleNamespace(
        is_closed=False, send_frame=AsyncMock(), recv_frame=AsyncMock(side_effect=error),
    )
    transport = SimpleNamespace(connect=AsyncMock(return_value=connection))
    monkeypatch.setattr(rpc_client, "create_transport_client", lambda address: transport)
    warning = Mock()
    monkeypatch.setattr(startup_diagnostics.logger, "warning", warning)
    client = rpc_client.RPCClient("unused", "secret-session-token")
    with pytest.raises(TimeoutError) as caught:
        asyncio.run(client.connect_and_handshake())
    assert caught.value is error
    assert "runner.wait_hello_response:timeout=10s" in warning.call_args.args[0]
    assert "secret-session-token" not in str(warning.call_args_list)


def test_runner_fatal_error_is_logged_and_still_raised(monkeypatch):
    error = RuntimeError("startup failure")
    monkeypatch.setattr(runner_main, "initialize_logging", lambda **kwargs: None)
    monkeypatch.setattr(runner_main, "_disable_runner_console_logging", lambda: None)
    monkeypatch.setattr(runner_main, "_async_main", AsyncMock(side_effect=error))
    exception_log = Mock()
    monkeypatch.setattr(runner_main.logger, "exception", exception_log)
    with pytest.raises(RuntimeError) as caught:
        runner_main.main()
    assert caught.value is error
    exception_log.assert_called_once_with("Runner 进程异常退出")


def test_exited_runner_stderr_is_drained_before_cleanup(monkeypatch):
    async def scenario():
        supervisor = PluginRunnerSupervisor(plugin_dirs=[])
        supervisor._runner_process = SimpleNamespace(pid=1234, returncode=1)
        warning = Mock()
        monkeypatch.setattr(supervisor._logger, "warning", warning)
        stream = asyncio.StreamReader()
        supervisor._stderr_drain_task = asyncio.create_task(supervisor._drain_runner_stderr(stream))

        async def delayed_stderr():
            await asyncio.sleep(0)
            stream.feed_data(b"Traceback: runner handshake timed out\n")
            stream.feed_eof()

        writer = asyncio.create_task(delayed_stderr())
        await supervisor._shutdown_runner(reason="startup_failed")
        await writer
        assert supervisor._stderr_drain_task is None
        assert supervisor._runner_process is None
        warning.assert_any_call("[runner-stderr] Traceback: runner handshake timed out")

    asyncio.run(scenario())


@pytest.mark.parametrize("on_load_succeeded", [True, False])
def test_plugin_activation_records_separate_stages(monkeypatch, on_load_succeeded):
    runner = runner_main.PluginRunner.__new__(runner_main.PluginRunner)
    runner._loader = SimpleNamespace(set_loaded_plugin=Mock(), purge_plugin_modules=Mock())
    runner._bootstrap_plugin = AsyncMock(return_value=True)
    runner._register_plugin = AsyncMock(return_value=True)
    runner._invoke_plugin_on_load = AsyncMock(return_value=on_load_succeeded)
    runner._unregister_plugin = AsyncMock()
    runner._deactivate_plugin = AsyncMock()
    meta = SimpleNamespace(plugin_id="example.slow-plugin", config_error="", plugin_dir="unused")
    info = Mock()
    monkeypatch.setattr(startup_diagnostics.logger, "info", info)

    result = asyncio.run(runner._activate_plugin(meta))
    assert result == (
        runner_main.PluginActivationStatus.LOADED if on_load_succeeded else runner_main.PluginActivationStatus.FAILED
    )
    messages = [call.args[0] for call in info.call_args_list]
    for stage in ("bootstrap", "register", "on_load"):
        assert any(f"stage=runner.plugin_{stage}:example.slow-plugin" in message for message in messages)
    if on_load_succeeded:
        runner._loader.set_loaded_plugin.assert_called_once_with(meta)
    else:
        runner._unregister_plugin.assert_awaited_once_with(meta.plugin_id, reason="on_load_failed")
