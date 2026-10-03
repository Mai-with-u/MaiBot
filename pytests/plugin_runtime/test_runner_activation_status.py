"""Exercise activation classification with real loading, lifecycle and reloads.

Only the Host transport and process logging are isolated. Manifests, config
files, dependency resolution and activation all use the production paths.
"""

from __future__ import annotations

from collections import Counter, deque
import json
from pathlib import Path
import sys
from typing import Any

import pytest

from src.plugin_runtime.protocol.envelope import (
    Envelope,
    MessageType,
    ReloadPluginPayload,
    ReloadPluginResultPayload,
    ReloadPluginsPayload,
    ReloadPluginsResultPayload,
    RunnerReadyPayload,
)
from src.plugin_runtime.runner.runner_main import PluginRunner

_DEPENDENCY = "test.activation_dependency"
_ADAPTER = "test.activation_adapter"
_CHILD = "test.activation_child"
_UNRELATED = "test.activation_unrelated"
_FAILURE = "test.activation_failure"


def _request(method: str, payload: dict[str, Any]) -> Envelope:
    return Envelope(request_id=1, message_type=MessageType.REQUEST, method=method, payload=payload)


def _write_plugin(root: Path, plugin_id: str, dependencies: list[str] | None = None) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text(
        "class Plugin:\n"
        "    def __init__(self):\n"
        "        self.loads = 0\n"
        "        self.unloads = 0\n"
        "    async def on_load(self):\n"
        "        self.loads += 1\n"
        "    async def on_unload(self):\n"
        "        self.unloads += 1\n"
        "def create_plugin():\n"
        "    return Plugin()\n",
        encoding="utf-8",
    )
    (plugin_dir / "_manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "version": "1.0.0",
                "name": plugin_id,
                "description": plugin_id,
                "author": {"name": "MaiBot", "url": "https://example.com"},
                "license": "GPL-v3.0-or-later",
                "urls": {"repository": "https://example.com/repo"},
                "host_application": {"min_version": "0.0.0", "max_version": "9999.9999.9999"},
                "sdk": {"min_version": "0.0.0", "max_version": "9999.9999.9999"},
                "dependencies": [
                    {"type": "plugin", "id": dependency_id, "version_spec": ">=1.0.0"}
                    for dependency_id in dependencies or []
                ],
                "capabilities": [],
                "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
                "id": plugin_id,
                "plugin_type": "adapter" if plugin_id == _ADAPTER else "extension",
            }
        ),
        encoding="utf-8",
    )
    return plugin_dir


def _configure(plugin_dir: Path, *, enabled: bool) -> None:
    (plugin_dir / "config.toml").write_text(f"[plugin]\nenabled = {'true' if enabled else 'false'}\n", encoding="utf-8")


class _HostTransport:
    def __init__(self, runner: PluginRunner) -> None:
        self.runner = runner
        self.ready: RunnerReadyPayload | None = None
        self.registered: set[str] = set()
        self.registration_failures: Counter[str] = Counter()
        self.handlers: dict[str, Any] = {}
        self.disconnected = False

    async def connect_and_handshake(self) -> bool:
        return True

    def register_method(self, method: str, handler: Any) -> None:
        self.handlers[method] = handler

    async def disconnect(self) -> None:
        self.disconnected = True

    async def send_request(self, method: str, **kwargs: Any) -> Envelope:
        request = _request(method, kwargs["payload"])
        plugin_id = kwargs.get("plugin_id", "")
        if method == "plugin.register_components":
            if self.registration_failures[plugin_id]:
                self.registration_failures[plugin_id] -= 1
                return request.make_error_response("E_INTERNAL", "registration refused")
            self.registered.add(plugin_id)
        elif method == "plugin.unregister":
            self.registered.discard(plugin_id)
        elif method == "runner.ready":
            self.ready = RunnerReadyPayload.model_validate(request.payload)
            self.runner._shutting_down = True
        elif method != "plugin.bootstrap":
            raise AssertionError(f"Unexpected RPC method: {method}")
        return request.make_response(payload={"accepted": True})


@pytest.fixture
def activation_runtime(tmp_path: Path, monkeypatch):
    root = tmp_path / "plugins"
    root.mkdir()
    paths = {
        _DEPENDENCY: _write_plugin(root, _DEPENDENCY),
        _ADAPTER: _write_plugin(root, _ADAPTER, [_DEPENDENCY]),
        _CHILD: _write_plugin(root, _CHILD, [_ADAPTER]),
        _UNRELATED: _write_plugin(root, _UNRELATED),
    }
    runner = PluginRunner(host_address="unused", session_token="test", plugin_dirs=[str(root)])
    transport = _HostTransport(runner)
    monkeypatch.setattr(runner, "_rpc_client", transport)
    monkeypatch.setattr(runner, "_install_log_handler", lambda: None)

    async def no_process_logging() -> None:
        pass

    monkeypatch.setattr(runner, "_uninstall_log_handler", no_process_logging)
    # Loading may install the SDK's legacy import hook. Keep it local to this test.
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    try:
        yield runner, transport, paths
    finally:
        for plugin_id, plugin_dir in paths.items():
            runner._loader.purge_plugin_modules(plugin_id, str(plugin_dir))


async def _reload(runner: PluginRunner, operation: str, plugin_ids: list[str]):
    if operation == "single":
        assert len(plugin_ids) == 1
        payload = ReloadPluginPayload(plugin_id=plugin_ids[0], reason="test_operator")
        response = await runner._handle_reload_plugin(_request("plugin.reload", payload.model_dump()))
        result_type = ReloadPluginResultPayload
    else:
        payload = ReloadPluginsPayload(plugin_ids=plugin_ids, reason="test_operator")
        response = await runner._handle_reload_plugins(_request("plugin.reload_batch", payload.model_dump()))
        result_type = ReloadPluginsResultPayload
    assert response.error is None
    return result_type.model_validate(response.payload)


@pytest.mark.asyncio
async def test_startup_distinguishes_self_disable_from_transitive_dependency_blocking(activation_runtime) -> None:
    runner, transport, paths = activation_runtime
    _configure(paths[_DEPENDENCY], enabled=False)

    await runner.run()

    assert transport.disconnected is True
    assert transport.ready is not None
    assert transport.ready.loaded_plugins == [_UNRELATED]
    assert set(transport.ready.inactive_plugins) == {_DEPENDENCY, _ADAPTER, _CHILD}
    assert transport.ready.explicitly_disabled_plugins == [_DEPENDENCY]
    assert transport.ready.failed_plugins == []
    assert transport.registered == {_UNRELATED}
    assert runner._loader.list_plugins() == [_UNRELATED]
    assert runner._loader.get_plugin(_UNRELATED).instance.loads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled_plugin_id", [_ADAPTER, _CHILD])
async def test_startup_self_disable_takes_precedence_over_inactive_dependencies(
    activation_runtime, disabled_plugin_id: str
) -> None:
    runner, transport, paths = activation_runtime
    _configure(paths[_DEPENDENCY], enabled=False)
    _configure(paths[disabled_plugin_id], enabled=False)

    await runner.run()

    assert transport.ready is not None
    assert set(transport.ready.inactive_plugins) == {_DEPENDENCY, _ADAPTER, _CHILD}
    assert set(transport.ready.explicitly_disabled_plugins) == {_DEPENDENCY, disabled_plugin_id}
    assert transport.ready.failed_plugins == []
    assert transport.registered == {_UNRELATED}
    assert runner._loader.list_plugins() == [_UNRELATED]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["single", "batch"])
@pytest.mark.parametrize("disabled_root", [_DEPENDENCY, _ADAPTER])
async def test_reload_classifies_only_the_disabled_root_not_its_dependents(
    activation_runtime, operation: str, disabled_root: str
) -> None:
    runner, transport, paths = activation_runtime
    await runner.run()
    assert transport.registered == set(paths)
    old_metas = {plugin_id: runner._loader.get_plugin(plugin_id) for plugin_id in paths}
    _configure(paths[disabled_root], enabled=False)

    result = await _reload(runner, operation, [disabled_root])

    affected = {_ADAPTER, _CHILD}
    if disabled_root == _DEPENDENCY:
        affected.add(_DEPENDENCY)
    assert result.success is True
    assert set(result.inactive_plugins) == affected
    assert result.explicitly_disabled_plugins == [disabled_root]
    assert set(result.unloaded_plugins) == affected
    assert result.reloaded_plugins == []
    assert result.failed_plugins == {}
    assert transport.registered == set(paths) - affected
    assert set(runner._loader.list_plugins()) == set(paths) - affected
    for plugin_id in affected:
        assert old_metas[plugin_id].instance.loads == 1
        assert old_metas[plugin_id].instance.unloads == 1
    assert runner._loader.get_plugin(_UNRELATED) is old_metas[_UNRELATED]
    assert old_metas[_UNRELATED].instance.unloads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["single", "batch"])
async def test_reload_reports_self_disable_even_when_dependency_is_also_disabled(
    activation_runtime, operation: str
) -> None:
    runner, transport, paths = activation_runtime
    await runner.run()
    old_metas = {plugin_id: runner._loader.get_plugin(plugin_id) for plugin_id in paths}
    _configure(paths[_DEPENDENCY], enabled=False)
    _configure(paths[_ADAPTER], enabled=False)
    requested_ids = [_DEPENDENCY] if operation == "single" else [_DEPENDENCY, _UNRELATED]

    result = await _reload(runner, operation, requested_ids)

    assert result.success is True
    assert set(result.inactive_plugins) == {_DEPENDENCY, _ADAPTER, _CHILD}
    assert set(result.explicitly_disabled_plugins) == {_DEPENDENCY, _ADAPTER}
    assert result.failed_plugins == {}
    assert result.reloaded_plugins == ([_UNRELATED] if operation == "batch" else [])
    assert transport.registered == {_UNRELATED}
    assert runner._loader.list_plugins() == [_UNRELATED]
    for plugin_id in (_DEPENDENCY, _ADAPTER, _CHILD):
        assert old_metas[plugin_id].instance.loads == 1
        assert old_metas[plugin_id].instance.unloads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["single", "batch"])
@pytest.mark.parametrize("rollback_enabled", [False, True])
async def test_failed_reload_does_not_report_disable_rolled_back_with_transaction(
    activation_runtime, monkeypatch, operation: str, rollback_enabled: bool
) -> None:
    runner, transport, paths = activation_runtime
    root = paths[_DEPENDENCY].parent
    paths[_FAILURE] = _write_plugin(root, _FAILURE, [_DEPENDENCY])
    await runner.run()
    old_metas = {plugin_id: runner._loader.get_plugin(plugin_id) for plugin_id in paths}
    # Config inputs change between the failed attempt and restoring old metas.
    # Neither activation, transaction handling nor dependency decisions are replaced.
    config_inputs = deque([False, rollback_enabled])
    load_config = runner._load_plugin_config

    def next_config(plugin_dir: str, plugin_id: str = "") -> dict[str, Any]:
        if plugin_id == _ADAPTER:
            return {"plugin": {"enabled": config_inputs.popleft()}}
        return load_config(plugin_dir, plugin_id)

    monkeypatch.setattr(runner, "_load_plugin_config", next_config)
    transport.registration_failures[_FAILURE] = 1

    result = await _reload(runner, operation, [_DEPENDENCY])

    assert result.success is False
    assert _FAILURE in result.failed_plugins
    assert result.explicitly_disabled_plugins == []
    assert result.inactive_plugins == []
    assert result.reloaded_plugins == []
    assert not config_inputs
    assert runner._loader.get_plugin(_FAILURE) is old_metas[_FAILURE]
    assert old_metas[_FAILURE].instance.loads == 2
    assert old_metas[_FAILURE].instance.unloads == 1
    assert runner._loader.get_plugin(_UNRELATED) is old_metas[_UNRELATED]
    assert old_metas[_UNRELATED].instance.unloads == 0
    if rollback_enabled:
        assert set(runner._loader.list_plugins()) == set(paths)
        assert transport.registered == set(paths)
        for plugin_id in (_DEPENDENCY, _ADAPTER, _CHILD):
            assert runner._loader.get_plugin(plugin_id) is old_metas[plugin_id]
            assert old_metas[plugin_id].instance.loads == 2
    else:
        assert _ADAPTER in result.failed_plugins
        assert runner._loader.get_plugin(_ADAPTER) is None
        assert _ADAPTER not in transport.registered
        assert old_metas[_ADAPTER].instance.loads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["single", "batch"])
async def test_reload_requested_dependency_blocked_adapter_fails_without_disable_exemption(
    activation_runtime, operation: str
) -> None:
    runner, transport, paths = activation_runtime
    _configure(paths[_DEPENDENCY], enabled=False)
    await runner.run()
    assert transport.registered == {_UNRELATED}

    result = await _reload(runner, operation, [_ADAPTER])

    assert result.success is False
    assert _ADAPTER in result.failed_plugins
    assert result.explicitly_disabled_plugins == []
    assert result.inactive_plugins == []
    assert runner._loader.get_plugin(_ADAPTER) is None
    assert transport.registered == {_UNRELATED}
