from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncio

import pytest

from src.plugin_runtime.integration import PluginRuntimeManager


def _group(name, *, error=None):
    return SimpleNamespace(
        group_name=name,
        start=AsyncMock(side_effect=error),
        stop=AsyncMock(),
        set_external_available_plugins=Mock(),
        set_blocked_plugin_reasons=Mock(),
        get_loaded_plugin_versions=lambda: {f"test.{name}": "1.0.0"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("dependent_groups", [False, True])
@pytest.mark.parametrize("failed_group", ["builtin", "third_party"])
async def test_failed_group_does_not_stop_other_group(monkeypatch, dependent_groups, failed_group):
    manager = PluginRuntimeManager()
    groups = {
        name: _group(name, error=TimeoutError("startup timed out") if name == failed_group else None)
        for name in ("builtin", "third_party")
    }
    manager._builtin_supervisor = groups["builtin"]
    manager._third_party_supervisor = groups["third_party"]
    monkeypatch.setattr(manager, "_build_group_start_order", lambda *args, **kwargs: ["builtin", "third_party"])
    monkeypatch.setattr(manager, "_get_group_dependency_flags", lambda *args, **kwargs: (False, dependent_groups))

    started = await manager._start_supervisors([], [])
    healthy_name = "third_party" if failed_group == "builtin" else "builtin"
    assert started == [groups[healthy_name]]
    groups[healthy_name].stop.assert_not_awaited()
    groups[healthy_name].start.assert_awaited_once()
    if dependent_groups:
        # 不把失败分组曾注册的插件当成已经可用的跨组依赖。
        expected_versions = {"test.builtin": "1.0.0"} if failed_group == "third_party" else {}
        groups["third_party"].set_external_available_plugins.assert_called_once_with(expected_versions)


@pytest.mark.asyncio
async def test_all_failed_groups_do_not_report_runtime_started(monkeypatch):
    manager = PluginRuntimeManager()
    manager._builtin_supervisor = _group("builtin", error=TimeoutError())
    manager._third_party_supervisor = _group("third_party", error=RuntimeError())
    monkeypatch.setattr(manager, "_build_group_start_order", lambda *args, **kwargs: ["builtin", "third_party"])
    monkeypatch.setattr(manager, "_get_group_dependency_flags", lambda *args, **kwargs: (False, False))
    with pytest.raises(RuntimeError, match="所有插件运行时分组均启动失败"):
        await manager._start_supervisors([], [])


@pytest.mark.asyncio
async def test_cancelled_group_start_cleans_all_groups(monkeypatch):
    manager = PluginRuntimeManager()
    entered = asyncio.Event()

    async def wait_for_ready():
        entered.set()
        await asyncio.Event().wait()

    groups = [_group("builtin"), _group("third_party")]
    groups[0].start = wait_for_ready
    manager._builtin_supervisor, manager._third_party_supervisor = groups
    monkeypatch.setattr(manager, "_build_group_start_order", lambda *args, **kwargs: ["builtin", "third_party"])
    monkeypatch.setattr(manager, "_get_group_dependency_flags", lambda *args, **kwargs: (False, False))
    task = asyncio.create_task(manager._start_supervisors([], []))
    await asyncio.wait_for(entered.wait(), 1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for group in groups:
        group.stop.assert_awaited_once()
