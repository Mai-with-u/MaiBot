"""Exercise activation classification with real loading, lifecycle and reloads.

Only the Host transport and process logging are isolated. Manifests, config
files, dependency resolution and activation all use the production paths.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Counter, Dict, List, Optional, Set

import json
import sys
import tomllib

from watchfiles import Change
import pytest

from src.config.file_watcher import FileChange
from src.plugin_runtime.integration import PluginRuntimeManager
from src.plugin_runtime.protocol.envelope import (
    Envelope,
    InspectPluginConfigPayload,
    InspectPluginConfigResultPayload,
    MessageType,
    ReloadPluginPayload,
    ReloadPluginResultPayload,
    ReloadPluginsPayload,
    ReloadPluginsResultPayload,
    RunnerReadyPayload,
    ValidatePluginConfigPayload,
    ValidatePluginConfigResultPayload,
)
from src.plugin_runtime.protocol.errors import ErrorCode
from src.plugin_runtime.runner.runner_main import PluginRunner

_DEPENDENCY = "test.activation_dependency"
_ADAPTER = "test.activation_adapter"
_CHILD = "test.activation_child"
_UNRELATED = "test.activation_unrelated"
_FAILURE = "test.activation_failure"
_CONFIG_MODEL = "test.activation_config_model"
_PLAIN_VERSIONED = "test.activation_plain_versioned"

# A real SDK plugin whose [plugin] section only declares config_version, the
# minimum the SDK requires. SDK normalization drops undeclared keys such as
# [plugin].enabled, which the host still has to honour (#2080).
_CONFIG_MODEL_PLUGIN_SOURCE = """
from maibot_sdk import Field, MaiBotPlugin, PluginConfigBase


class PluginSection(PluginConfigBase):
    config_version: str = Field(default="1.0.0")


class GreetingSection(PluginConfigBase):
    message: str = Field(default="hi")


class DemoConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    greeting: GreetingSection = Field(default_factory=GreetingSection)


class DemoPlugin(MaiBotPlugin):
    config_model = DemoConfig

    async def on_load(self):
        pass

    async def on_unload(self):
        pass

    async def on_config_update(self, scope, config_data, version):
        pass


def create_plugin():
    return DemoPlugin()
"""

# A plain plugin without normalize_plugin_config. Its default config still drives
# the config_version upgrade, which rebuilds the config from the default skeleton
# and so drops [plugin].enabled as well (#2080).
_PLAIN_VERSIONED_PLUGIN_SOURCE = """
class Plugin:
    def get_default_config(self):
        return {"plugin": {"config_version": "1.0.0"}, "greeting": {"message": "hi"}}

    async def on_load(self):
        pass

    async def on_unload(self):
        pass


def create_plugin():
    return Plugin()
"""


def _request(method: str, payload: Dict[str, Any], plugin_id: str = "") -> Envelope:
    return Envelope(request_id=1, message_type=MessageType.REQUEST, method=method, plugin_id=plugin_id, payload=payload)


def _write_plugin(root: Path, plugin_id: str, dependencies: List[str] | None = None) -> Path:
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


def _write_config_model_plugin(root: Path) -> Path:
    plugin_dir = _write_plugin(root, _CONFIG_MODEL)
    (plugin_dir / "plugin.py").write_text(_CONFIG_MODEL_PLUGIN_SOURCE, encoding="utf-8")
    return plugin_dir


def _write_plain_versioned_plugin(root: Path) -> Path:
    plugin_dir = _write_plugin(root, _PLAIN_VERSIONED)
    (plugin_dir / "plugin.py").write_text(_PLAIN_VERSIONED_PLUGIN_SOURCE, encoding="utf-8")
    return plugin_dir


def _configure_config_model(plugin_dir: Path, *, enabled: bool, config_version: str = "1.0.0") -> None:
    (plugin_dir / "config.toml").write_text(
        "[plugin]\n"
        f'config_version = "{config_version}"\n'
        f"enabled = {'true' if enabled else 'false'}\n"
        "\n"
        "[greeting]\n"
        'message = "hi"\n',
        encoding="utf-8",
    )


def _read_config(plugin_dir: Path) -> Dict[str, Any]:
    with (plugin_dir / "config.toml").open("rb") as handle:
        return tomllib.load(handle)


async def _inspect(
    runner: PluginRunner,
    plugin_id: str,
    config_data: Optional[Dict[str, Any]] = None,
    *,
    use_provided_config: bool = False,
) -> InspectPluginConfigResultPayload:
    payload = InspectPluginConfigPayload(config_data=config_data or {}, use_provided_config=use_provided_config)
    response = await runner._handle_inspect_plugin_config(
        _request("plugin.inspect_config", payload.model_dump(), plugin_id=plugin_id)
    )
    assert response.error is None
    return InspectPluginConfigResultPayload.model_validate(response.payload)


async def _validate(runner: PluginRunner, plugin_id: str, config_data: Dict[str, Any]) -> Envelope:
    payload = ValidatePluginConfigPayload(config_data=config_data)
    return await runner._handle_validate_plugin_config(
        _request("plugin.validate_config", payload.model_dump(), plugin_id=plugin_id)
    )


class _HostTransport:
    def __init__(self, runner: PluginRunner) -> None:
        self.runner = runner
        self.ready: RunnerReadyPayload | None = None
        self.registered: Set[str] = set()
        self.registrations: Dict[str, Dict[str, Any]] = {}
        self.registration_failures: Counter[str] = Counter()
        self.handlers: Dict[str, Any] = {}
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
            self.registrations[plugin_id] = request.payload
        elif method == "plugin.unregister":
            self.registered.discard(plugin_id)
            self.registrations.pop(plugin_id, None)
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


async def _reload(runner: PluginRunner, operation: str, plugin_ids: List[str]):
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
@pytest.mark.parametrize("invalid_toml", ['[greeting]\nmessage = []\n', '[greeting\nmessage = "hi"\n'])
async def test_config_error_loads_management_only_and_repair_activates_business(
    activation_runtime, invalid_toml: str,
) -> None:
    runner, transport, paths = activation_runtime
    plugin_dir = _write_config_model_plugin(paths[_UNRELATED].parent)
    paths[_CONFIG_MODEL] = plugin_dir
    source = _CONFIG_MODEL_PLUGIN_SOURCE.replace(
        '    config_model = DemoConfig',
        '    config_model = DemoConfig\n'
        '    def __init__(self):\n'
        '        super().__init__()\n'
        '        self.loads = 0\n'
        '        self.unloads = 0\n'
        '    def get_components(self):\n'
        '        return [{"name": "sample", "type": "tool"}]',
    ).replace('    async def on_load(self):\n        pass', '    async def on_load(self):\n        self.loads += 1')
    source = source.replace('    async def on_unload(self):\n        pass', '    async def on_unload(self):\n        self.unloads += 1')
    (plugin_dir / "plugin.py").write_text(source, encoding="utf-8")
    config_path = plugin_dir / "config.toml"
    config_path.write_text('[plugin]\nconfig_version = "1.0.0"\n' + invalid_toml, encoding="utf-8")
    original = config_path.read_bytes()

    await runner.run()
    invalid_meta = runner._loader.get_plugin(_CONFIG_MODEL)
    assert invalid_meta is not None and invalid_meta.config_error
    assert invalid_meta.instance.loads == 0
    registration = transport.registrations[_CONFIG_MODEL]
    assert registration["config_error"] == invalid_meta.config_error
    assert registration["components"] == []
    assert registration["capabilities_required"] == []
    assert registration["config_schema"] == {}
    snapshot = await _inspect(runner, _CONFIG_MODEL)
    assert snapshot.config_error and not snapshot.enabled
    assert snapshot.normalized_config == {} and snapshot.config_schema == {}
    assert config_path.read_bytes() == original

    _configure_config_model(plugin_dir, enabled=True)
    result = await _reload(runner, "single", [_CONFIG_MODEL])
    assert result.success
    repaired_meta = runner._loader.get_plugin(_CONFIG_MODEL)
    assert repaired_meta.config_error == ""
    assert repaired_meta.instance.loads == 1
    assert invalid_meta.instance.unloads == 0
    assert transport.registrations[_CONFIG_MODEL]["components"][0]["name"] == "sample"

    # 再次写坏配置必须卸载已运行业务，不能保留之前的工具和调度。
    config_path.write_bytes(original)
    result = await _reload(runner, "single", [_CONFIG_MODEL])
    assert result.success
    assert repaired_meta.instance.unloads == 1
    assert runner._loader.get_plugin(_CONFIG_MODEL).instance.loads == 0
    assert transport.registrations[_CONFIG_MODEL]["components"] == []


@pytest.mark.asyncio
async def test_recovery_keeps_successful_plugins_when_another_plugin_fails(activation_runtime) -> None:
    runner, transport, paths = activation_runtime
    await runner.run()
    healthy_meta = runner._loader.get_plugin(_UNRELATED)
    await runner._unload_plugins_by_ids([_ADAPTER, _CHILD], "test_failure")
    paths[_FAILURE] = _write_plugin(paths[_UNRELATED].parent, _FAILURE)
    transport.registration_failures[_FAILURE] = 3

    payload = ReloadPluginsPayload(plugin_ids=[_ADAPTER, _CHILD, _FAILURE, _UNRELATED])
    response = await runner._handle_reload_plugins(_request("plugin.recover", payload.model_dump()))
    assert response.error is None
    result = ReloadPluginsResultPayload.model_validate(response.payload)

    assert result.success is False
    assert set(result.reloaded_plugins) == {_ADAPTER, _CHILD}
    assert _FAILURE in result.failed_plugins
    assert result.unloaded_plugins == []
    assert set(runner._loader.list_plugins()) == {_DEPENDENCY, _ADAPTER, _CHILD, _UNRELATED}
    assert transport.registered == {_DEPENDENCY, _ADAPTER, _CHILD, _UNRELATED}
    assert runner._loader.get_plugin(_UNRELATED) is healthy_meta
    assert healthy_meta.instance.loads == 1
    assert healthy_meta.instance.unloads == 0


@pytest.mark.asyncio
async def test_recovery_of_loaded_plugin_leaves_dependents_untouched(activation_runtime) -> None:
    runner, transport, paths = activation_runtime
    await runner.run()
    old_metas = {plugin_id: runner._loader.get_plugin(plugin_id) for plugin_id in paths}
    payload = ReloadPluginsPayload(plugin_ids=[_DEPENDENCY])
    response = await runner._handle_reload_plugins(_request("plugin.recover", payload.model_dump()))
    result = ReloadPluginsResultPayload.model_validate(response.payload)

    assert result.success is True
    assert result.reloaded_plugins == []
    assert result.unloaded_plugins == []
    for plugin_id, meta in old_metas.items():
        assert runner._loader.get_plugin(plugin_id) is meta
        assert meta.instance.loads == 1
        assert meta.instance.unloads == 0


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

    def next_config(plugin_dir: str, plugin_id: str = "") -> Dict[str, Any]:
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


@pytest.mark.asyncio
async def test_inspect_and_validate_keep_disk_disable_of_sdk_config_model_plugin(activation_runtime) -> None:
    runner, _, paths = activation_runtime
    paths[_CONFIG_MODEL] = _write_config_model_plugin(paths[_UNRELATED].parent)
    _configure_config_model(paths[_CONFIG_MODEL], enabled=False)

    snapshot = await _inspect(runner, _CONFIG_MODEL)

    assert snapshot.enabled is False
    assert snapshot.normalized_config == {
        "plugin": {"config_version": "1.0.0", "enabled": False},
        "greeting": {"message": "hi"},
    }

    # WebUI saves and /pm config set write back the validated config.
    edited_config = {
        "plugin": {"config_version": "1.0.0", "enabled": False},
        "greeting": {"message": "hello"},
    }
    response = await _validate(runner, _CONFIG_MODEL, edited_config)
    assert response.error is None
    validated = ValidatePluginConfigResultPayload.model_validate(response.payload)
    assert validated.normalized_config == edited_config


@pytest.mark.asyncio
@pytest.mark.parametrize(("raw_enabled", "expected"), [("False", False), ("off", False), (0, False), ("yes", True)])
async def test_validate_coerces_undeclared_enabled_of_sdk_config_model_plugin_to_bool(
    activation_runtime, raw_enabled: Any, expected: bool
) -> None:
    runner, _, paths = activation_runtime
    paths[_CONFIG_MODEL] = _write_config_model_plugin(paths[_UNRELATED].parent)

    # /pm config set keeps "False" / "off" as strings. The restored value must become a
    # real bool, as a declared `enabled: bool` field would, or the WebUI switch
    # (`enabled !== false`) disagrees with the runtime.
    response = await _validate(runner, _CONFIG_MODEL, {"plugin": {"config_version": "1.0.0", "enabled": raw_enabled}})

    assert response.error is None
    validated = ValidatePluginConfigResultPayload.model_validate(response.payload)
    assert validated.normalized_config["plugin"]["enabled"] is expected


@pytest.mark.asyncio
async def test_validate_rejects_invalid_undeclared_enabled_of_sdk_config_model_plugin(activation_runtime) -> None:
    runner, _, paths = activation_runtime
    paths[_CONFIG_MODEL] = _write_config_model_plugin(paths[_UNRELATED].parent)

    response = await _validate(runner, _CONFIG_MODEL, {"plugin": {"config_version": "1.0.0", "enabled": "maybe"}})

    assert response.error is not None
    assert response.error["code"] == ErrorCode.E_BAD_PAYLOAD.value
    assert "plugin.enabled" in response.error["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_startup_honours_disk_enabled_of_sdk_config_model_plugin(activation_runtime, enabled: bool) -> None:
    runner, transport, paths = activation_runtime
    paths[_CONFIG_MODEL] = _write_config_model_plugin(paths[_UNRELATED].parent)
    _configure_config_model(paths[_CONFIG_MODEL], enabled=enabled)

    await runner.run()

    assert transport.ready is not None
    assert transport.ready.failed_plugins == []
    assert (_CONFIG_MODEL in transport.ready.loaded_plugins) is enabled
    assert (_CONFIG_MODEL in transport.ready.explicitly_disabled_plugins) is not enabled
    assert (_CONFIG_MODEL in transport.registered) is enabled
    assert (runner._loader.get_plugin(_CONFIG_MODEL) is not None) is enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("plugin_id", [_DEPENDENCY, _ADAPTER])
async def test_disabled_plugin_skips_import_and_missing_dependencies(activation_runtime, plugin_id: str) -> None:
    runner, transport, paths = activation_runtime
    plugin_dir = paths[plugin_id]
    _configure(plugin_dir, enabled=False)
    original_source = (plugin_dir / "plugin.py").read_text(encoding="utf-8")
    (plugin_dir / "plugin.py").write_text('raise RuntimeError("disabled plugin imported")\n', encoding="utf-8")
    manifest_path = plugin_dir / "_manifest.json"
    original_manifest = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(original_manifest)
    manifest["dependencies"] = [
        {"type": "plugin", "id": "test.missing", "version_spec": ">=1"},
        {"type": "python_package", "name": "missing-disabled-plugin-package", "version_spec": ">=1"},
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    await runner.run()

    assert transport.ready is not None
    assert plugin_id in transport.ready.explicitly_disabled_plugins
    assert plugin_id not in transport.ready.failed_plugins
    assert plugin_id not in transport.registered
    assert runner._loader.get_plugin(plugin_id) is None

    # 恢复合法代码和依赖后，重新启用仍通过正常导入、配置与激活流程。
    (plugin_dir / "plugin.py").write_text(original_source, encoding="utf-8")
    manifest_path.write_text(original_manifest, encoding="utf-8")
    _configure(plugin_dir, enabled=True)
    result = await _reload(runner, "batch", [plugin_id])
    assert result.success
    assert plugin_id in result.reloaded_plugins


@pytest.mark.asyncio
async def test_disabled_sdk_config_model_plugin_skips_config_version_upgrade(activation_runtime) -> None:
    runner, transport, paths = activation_runtime
    paths[_CONFIG_MODEL] = _write_config_model_plugin(paths[_UNRELATED].parent)
    _configure_config_model(paths[_CONFIG_MODEL], enabled=False, config_version="0.9.0")

    await runner.run()

    assert transport.ready is not None
    assert _CONFIG_MODEL in transport.ready.explicitly_disabled_plugins
    assert _read_config(paths[_CONFIG_MODEL])["plugin"] == {"config_version": "0.9.0", "enabled": False}


@pytest.mark.asyncio
async def test_disabled_plugin_without_config_normalization_skips_config_version_upgrade(
    activation_runtime,
) -> None:
    runner, transport, paths = activation_runtime
    paths[_PLAIN_VERSIONED] = _write_plain_versioned_plugin(paths[_UNRELATED].parent)
    _configure_config_model(paths[_PLAIN_VERSIONED], enabled=False, config_version="0.9.0")

    await runner.run()

    assert transport.ready is not None
    assert _PLAIN_VERSIONED in transport.ready.explicitly_disabled_plugins
    assert _PLAIN_VERSIONED not in transport.registered
    assert _read_config(paths[_PLAIN_VERSIONED])["plugin"] == {"config_version": "0.9.0", "enabled": False}


@pytest.mark.asyncio
async def test_host_config_change_unloads_sdk_config_model_plugin_disabled_on_disk(
    activation_runtime, monkeypatch
) -> None:
    runner, transport, paths = activation_runtime
    paths[_CONFIG_MODEL] = _write_config_model_plugin(paths[_UNRELATED].parent)
    _configure_config_model(paths[_CONFIG_MODEL], enabled=True)
    await runner.run()
    assert _CONFIG_MODEL in transport.registered
    _configure_config_model(paths[_CONFIG_MODEL], enabled=False)

    manager = PluginRuntimeManager()
    manager._started = True
    handled: List[str] = []

    async def inspect_plugin_config(
        plugin_id: str,
        config_data: Optional[Dict[str, Any]] = None,
        *,
        use_provided_config: bool = False,
    ) -> InspectPluginConfigResultPayload:
        return await _inspect(runner, plugin_id, config_data, use_provided_config=use_provided_config)

    async def notify_plugin_config_updated(**kwargs: Any) -> bool:
        handled.append("config_updated")
        return True

    async def reload_plugins_globally(plugin_ids: List[str], reason: str = "manual") -> bool:
        handled.append(reason)
        return (await _reload(runner, "batch", list(plugin_ids))).success

    supervisor = SimpleNamespace(
        _registered_plugins=transport.registered,
        get_loaded_plugin_ids=lambda: list(transport.registered),
        get_plugin_config_error=lambda plugin_id: runner._loader.get_plugin(plugin_id).config_error,
        inspect_plugin_config=inspect_plugin_config,
        notify_plugin_config_updated=notify_plugin_config_updated,
    )
    monkeypatch.setattr(manager, "_third_party_supervisor", supervisor)
    monkeypatch.setattr(manager, "reload_plugins_globally", reload_plugins_globally)

    await manager._handle_plugin_config_changes(
        _CONFIG_MODEL,
        [FileChange(change_type=Change.modified, path=paths[_CONFIG_MODEL] / "config.toml")],
    )

    await manager._plugin_change_task
    assert handled == ["config_disabled"]
    assert _CONFIG_MODEL not in transport.registered
    assert runner._loader.get_plugin(_CONFIG_MODEL) is None
