import json
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from pathlib import Path
from typing import Any, Dict

from src.plugin_runtime.compat_policy import (
    build_force_plugin_compatibility_env,
    parse_force_plugin_compatibility_env,
)
from src.plugin_runtime.runner.plugin_loader import PluginLoader
from src.plugin_runtime.runner.manifest_validator import ManifestValidator, VersionComparator
from src.plugin_runtime.update_compatibility_notice import _is_host_compatible


def _build_manifest() -> Dict[str, Any]:
    """构造仅用于运行时版本兼容测试的最小合法 Manifest。"""

    return {
        "manifest_version": 2,
        "version": "1.0.0",
        "name": "版本兼容测试插件",
        "description": "测试 Host 版本兼容规则",
        "author": {"name": "MaiBot", "url": "https://example.com"},
        "license": "GPL-v3.0-or-later",
        "urls": {"repository": "https://example.com/repository"},
        "host_application": {"min_version": "1.0.0", "max_version": "1.0.0"},
        "sdk": {"min_version": "2.0.0", "max_version": "2.99.99"},
        "dependencies": [],
        "capabilities": [],
        "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
        "id": "maibot-team.version-compatibility-test",
    }


def _build_validator(
    host_version: str,
    *,
    force_plugin_compatibility: bool = False,
    validate_python_package_dependencies: bool = False,
) -> ManifestValidator:
    return ManifestValidator(
        host_version=host_version,
        sdk_version="2.0.0",
        validate_python_package_dependencies=validate_python_package_dependencies,
        log_errors=False,
        log_compat_warnings=False,
        force_plugin_compatibility=force_plugin_compatibility,
    )


def test_higher_patch_version_uses_compatibility_mode() -> None:
    validator = _build_validator("1.0.1")

    assert validator.parse_manifest(_build_manifest()) is not None
    assert validator.errors == []
    assert validator.warnings == ["当前版本 1.0.1 以兼容模式加载插件（插件声明的 Host 最高支持版本为 1.0.0）"]


def test_higher_minor_version_is_incompatible() -> None:
    validator = _build_validator("1.1.0")

    assert validator.parse_manifest(_build_manifest()) is None
    assert validator.warnings == []
    assert validator.errors == ["Host 版本不兼容: 版本 1.1.0 高于最大支持 1.0.0 (当前 Host: 1.1.0)"]


def test_patch_compatibility_requires_same_major_and_minor() -> None:
    assert VersionComparator.is_same_major_minor_higher_version("1.0.1", "1.0.0") is True
    assert VersionComparator.is_same_major_minor_higher_version("1.1.0", "1.0.9") is False
    assert VersionComparator.is_same_major_minor_higher_version("2.0.0", "1.0.9") is False
    assert VersionComparator.is_same_major_higher_version("1.1.0", "1.0.9") is False


def test_update_notice_uses_same_patch_compatibility_rule() -> None:
    assert _is_host_compatible("1.0.1", "1.0.0", "1.0.0") is True
    assert _is_host_compatible("1.1.0", "1.0.0", "1.0.9") is False


def test_force_compatibility_loads_plugin_out_of_host_and_sdk_range() -> None:
    """开启强制插件兼容后，Host 与 SDK 版本范围都不再阻止加载。"""

    manifest = _build_manifest()
    manifest["host_application"] = {"min_version": "1.0.0", "max_version": "1.2.3"}
    manifest["sdk"] = {"min_version": "9.0.0", "max_version": "9.9.9"}
    validator = _build_validator("2.0.0", force_plugin_compatibility=True)

    assert validator.parse_manifest(manifest) is not None
    assert validator.errors == []
    assert validator.warnings == [
        "已开启强制插件兼容，跳过版本校验（插件声明 Host 1.0.0 - 1.2.3、SDK 9.0.0 - 9.9.9，"
        "当前 Host 2.0.0 / SDK 2.0.0）"
    ]


@pytest.mark.parametrize("force_compatibility", [False, True])
def test_installed_python_package_version_mismatch_only_warns(force_compatibility: bool) -> None:
    """已有依赖沿用当前版本，普通和强制兼容模式均允许插件尝试加载。"""

    manifest = _build_manifest()
    manifest["host_application"] = {"min_version": "1.0.0", "max_version": "1.2.3"}
    manifest["dependencies"] = [{"type": "python_package", "name": "packaging", "version_spec": ">=999.0.0"}]
    validator = _build_validator(
        "1.0.0",
        force_plugin_compatibility=force_compatibility,
        validate_python_package_dependencies=True,
    )

    assert validator.parse_manifest(manifest) is not None
    assert validator.errors == []
    assert any(warning.startswith("Python 包依赖版本不匹配: packaging 需要 >=999.0.0") for warning in validator.warnings)


def test_plugin_loader_force_compatibility_loads_incompatible_plugin(tmp_path: Path) -> None:
    """加载器透传强制兼容开关后，Host 版本不兼容的插件依然进入候选列表。"""

    plugin_root = tmp_path / "plugins"
    plugin_dir = plugin_root / "incompatible-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
    (plugin_dir / "_manifest.json").write_text(
        json.dumps({**_build_manifest(), "id": "maibot-team.incompatible-plugin"}),
        encoding="utf-8",
    )

    strict_loader = PluginLoader(host_version="2.0.0")
    assert strict_loader.discover_candidates([str(plugin_root)])[0] == {}

    force_loader = PluginLoader(host_version="2.0.0", force_plugin_compatibility=True)
    candidates, duplicates = force_loader.discover_candidates([str(plugin_root)])

    assert duplicates == {}
    assert set(candidates) == {"maibot-team.incompatible-plugin"}


def test_force_plugin_compatibility_env_round_trip() -> None:
    assert parse_force_plugin_compatibility_env(build_force_plugin_compatibility_env(True)) is True
    assert parse_force_plugin_compatibility_env(build_force_plugin_compatibility_env(False)) is False


def test_force_plugin_compatibility_env_accepts_common_values() -> None:
    for raw_value in ("1", "true", "TRUE", "yes", "on", " 1 "):
        assert parse_force_plugin_compatibility_env(raw_value) is True
    for raw_value in ("", "0", "false", "off", "unknown", None):
        assert parse_force_plugin_compatibility_env(raw_value) is False


@pytest.mark.parametrize("sdk_version", ["1.0.0", "3.0.0"])
def test_sdk_outside_declared_range_only_warns_once(monkeypatch, sdk_version):
    from src.plugin_runtime.runner import manifest_validator as module

    monkeypatch.setattr(ManifestValidator, "_LOGGED_WARNING_KEYS", set())
    warning = Mock()
    monkeypatch.setattr(module.logger, "warning", warning)
    validator = ManifestValidator(host_version="1.0.0", sdk_version=sdk_version,
                                  validate_python_package_dependencies=False)
    for _ in range(2):
        assert validator.parse_manifest(_build_manifest()) is not None
        assert validator.errors == []
        assert len(validator.warnings) == 1
    warning.assert_called_once()
    assert "已放行" in warning.call_args.args[0]


@pytest.mark.parametrize("valid_token", [True, False])
def test_runner_sdk_mismatch_does_not_skip_authentication(valid_token):
    from src.plugin_runtime.host.rpc_server import RPCServer
    from src.plugin_runtime.protocol.envelope import Envelope, MessageType

    server = RPCServer(transport=Mock(), session_token="test-token", host_version="1.0.0")
    server._logger = Mock()
    hello = Envelope(request_id=1, message_type=MessageType.REQUEST, method="runner.hello", payload={
        "runner_id": "test", "sdk_version": "9999.0.0",
        "session_token": "test-token" if valid_token else "wrong-token",
    })
    conn = Mock(recv_frame=AsyncMock(return_value=server._codec.encode_envelope(hello)), send_frame=AsyncMock())
    assert asyncio.run(server._handle_handshake(conn)) is valid_token
    response = server._codec.decode_envelope(conn.send_frame.call_args.args[0])
    assert response.payload["accepted"] is valid_token
    assert server._logger.warning.call_count == int(valid_token)


@pytest.mark.parametrize("sdk", [
    {"min_version": "999.0.0", "max_version": "999.99.99"},
    {"min_version": "0.0.0", "max_version": "0.0.1"},
])
def test_local_plugin_discovery_allows_sdk_range_mismatch(tmp_path, sdk):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
    (plugin_dir / "_manifest.json").write_text(json.dumps({**_build_manifest(), "sdk": sdk}), encoding="utf-8")
    loader = PluginLoader(host_version="1.0.0")
    candidates, duplicates = loader.discover_candidates([str(tmp_path)])
    assert set(candidates) == {"maibot-team.version-compatibility-test"}
    assert duplicates == {}
