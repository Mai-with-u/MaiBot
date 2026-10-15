"""插件配置可视化表单读写链路测试。

插件配置模型与 Schema 使用真实的 SDK，配置解析 / 校验走 Runner 的真实实现，
仅把 Host 与 Runner 之间的 RPC 传输替换为进程内直接调用。
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional, Tuple
from unittest.mock import AsyncMock

import copy
import json
import tomllib

from fastapi import FastAPI
from fastapi.testclient import TestClient
from maibot_sdk import Field, MaiBotPlugin, PluginConfigBase
from maibot_sdk.config import generate_plugin_config_schema
import pytest

from src.plugin_runtime.protocol.envelope import InspectPluginConfigResultPayload
from src.plugin_runtime.runner.runner_main import PluginRunner
from src.webui.routers.plugin import config_routes as config_routes_module
from src.webui.routers.plugin import support as support_module

_PLUGIN_ID = "test.config_form"
_FLAT_FIELDS_CONFIG_TOML = (
    'api_contact = ""\nenable_feature = false\ntimeout = 45\n\n[plugin]\nenabled = true\nconfig_version = "1.0.0"\n'
)


class _PluginSection(PluginConfigBase):
    enabled: bool = True
    config_version: str = "1.0.0"


class _FlatFieldsConfig(PluginConfigBase):
    """根级扁平字段 + ``[plugin]`` 保留节，SDK 会把扁平字段归入虚构的 general 节。"""

    plugin: _PluginSection = Field(default_factory=_PluginSection)
    api_contact: str = ""
    enable_feature: bool = False
    timeout: int = 30


class _GeneralSection(PluginConfigBase):
    api_contact: str = ""
    enable_feature: bool = False


class _RealGeneralConfig(PluginConfigBase):
    """真实声明了 general 配置节的插件。"""

    plugin: _PluginSection = Field(default_factory=_PluginSection)
    general: _GeneralSection = Field(default_factory=_GeneralSection)


class _DictPluginSectionConfig(PluginConfigBase):
    """``plugin`` 保留节声明为普通 dict，SDK 会把它与其他扁平字段一起归入虚构的 general 节。"""

    plugin: Dict[str, Any] = Field(default_factory=lambda: {"enabled": True, "config_version": "1.0.0"})
    api_contact: str = ""


class _FlatFieldsPlugin(MaiBotPlugin):
    config_model = _FlatFieldsConfig


class _RealGeneralPlugin(MaiBotPlugin):
    config_model = _RealGeneralConfig


class _DictPluginSectionPlugin(MaiBotPlugin):
    config_model = _DictPluginSectionConfig


class _CustomGeneralSchemaPlugin(MaiBotPlugin):
    """未声明配置模型、自行提供含真实 general 配置节 Schema 的插件。"""

    def get_webui_config_schema(
        self,
        *,
        plugin_id: str = "",
        plugin_name: str = "",
        plugin_version: str = "",
        plugin_description: str = "",
        plugin_author: str = "",
    ) -> Dict[str, Any]:
        return generate_plugin_config_schema(
            _RealGeneralConfig,
            plugin_id=plugin_id,
            plugin_name=plugin_name,
            plugin_version=plugin_version,
            plugin_description=plugin_description,
            plugin_author=plugin_author,
        )


def _build_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plugin: MaiBotPlugin,
    config_toml: str,
) -> Tuple[TestClient, Path]:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "config_form_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "_manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "id": _PLUGIN_ID,
                "name": "Config Form Plugin",
                "version": "1.0.0",
                "description": "config form plugin",
            }
        ),
        encoding="utf-8",
    )
    config_path = plugin_dir / "config.toml"
    config_path.write_text(config_toml, encoding="utf-8")

    runner = PluginRunner(host_address="unused", session_token="test", plugin_dirs=[str(plugins_dir)])
    meta = SimpleNamespace(
        plugin_id=_PLUGIN_ID,
        plugin_dir=str(plugin_dir),
        instance=plugin,
        version="1.0.0",
        manifest=SimpleNamespace(name="Config Form Plugin", description="", author=SimpleNamespace(name="tester")),
    )

    async def fake_inspect_plugin_config(
        plugin_id: str,
        config_data: Optional[Dict[str, Any]] = None,
        *,
        use_provided_config: bool = False,
    ) -> InspectPluginConfigResultPayload:
        # 参数与 Runner 的 plugin.inspect_config 处理器一致
        assert plugin_id == _PLUGIN_ID
        return runner._inspect_plugin_config(
            meta,
            config_data=config_data,
            use_provided_config=use_provided_config,
            suppress_errors=True,
            enforce_version=not use_provided_config,
        )

    async def fake_validate_plugin_config(plugin_id: str, config_data: Dict[str, Any]) -> Dict[str, Any]:
        # 参数与 Runner 的 plugin.validate_config 处理器一致
        assert plugin_id == _PLUGIN_ID
        return runner._inspect_plugin_config(
            meta,
            config_data=config_data,
            use_provided_config=True,
            suppress_errors=False,
            enforce_version=True,
        ).normalized_config

    monkeypatch.setattr(support_module, "get_plugins_dir", lambda: plugins_dir)
    monkeypatch.setattr(config_routes_module, "require_plugin_token", lambda _: "ok")
    monkeypatch.setattr(config_routes_module, "_inspect_plugin_config_via_runtime", fake_inspect_plugin_config)
    monkeypatch.setattr(config_routes_module, "_validate_plugin_config_via_runtime", fake_validate_plugin_config)

    app = FastAPI()
    app.include_router(config_routes_module.router, prefix="/api/webui/plugins")
    return TestClient(app), config_path


def _edit_like_visual_form(
    schema: Dict[str, Any],
    config: Dict[str, Any],
    field_name: str,
    value: Any,
) -> Dict[str, Any]:
    """按 WebUI 可视化表单的方式修改字段：写入 ``config[section.name][field]``。"""

    for section_name, section in schema["sections"].items():
        if field_name in section["fields"]:
            form_section_name = section.get("name") or section_name
            edited_config = copy.deepcopy(config)
            section_config = edited_config.get(form_section_name)
            if not isinstance(section_config, dict):
                section_config = {}
                edited_config[form_section_name] = section_config
            section_config[field_name] = value
            return edited_config
    raise AssertionError(f"Schema 中不存在字段: {field_name}")


def _read_form_value(schema: Dict[str, Any], config: Dict[str, Any], field_name: str) -> Any:
    """按 WebUI 可视化表单的方式读取字段：``config[section.name][field]``。"""

    for section_name, section in schema["sections"].items():
        if field_name in section["fields"]:
            section_config = config.get(section.get("name") or section_name)
            return section_config.get(field_name) if isinstance(section_config, dict) else None
    raise AssertionError(f"Schema 中不存在字段: {field_name}")


def _load_disk_config(config_path: Path) -> Dict[str, Any]:
    with config_path.open("rb") as file_obj:
        return tomllib.load(file_obj)


def test_visual_form_reads_flat_root_fields_from_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _build_client(tmp_path, monkeypatch, _FlatFieldsPlugin(), _FLAT_FIELDS_CONFIG_TOML)

    bundle = client.get(f"/api/webui/plugins/config/{_PLUGIN_ID}/bundle").json()
    schema = bundle["schema"]

    # SDK 把根级扁平字段归入虚构的 general 节，表单需要能读到磁盘上的当前值
    assert "general" not in _FlatFieldsConfig.model_fields
    assert set(schema["sections"]["general"]["fields"]) == {"api_contact", "enable_feature", "timeout"}
    assert _read_form_value(schema, bundle["config"], "api_contact") == ""
    assert _read_form_value(schema, bundle["config"], "enable_feature") is False
    assert _read_form_value(schema, bundle["config"], "timeout") == 45


def test_visual_form_saves_flat_root_fields_to_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, config_path = _build_client(tmp_path, monkeypatch, _FlatFieldsPlugin(), _FLAT_FIELDS_CONFIG_TOML)

    bundle = client.get(f"/api/webui/plugins/config/{_PLUGIN_ID}/bundle").json()
    form_config = _edit_like_visual_form(bundle["schema"], bundle["config"], "api_contact", "me@example.com")
    form_config = _edit_like_visual_form(bundle["schema"], form_config, "enable_feature", True)
    response = client.put(f"/api/webui/plugins/config/{_PLUGIN_ID}", json={"config": form_config})

    assert response.status_code == 200
    assert _load_disk_config(config_path) == {
        "api_contact": "me@example.com",
        "enable_feature": True,
        "timeout": 45,
        "plugin": {"enabled": True, "config_version": "1.0.0"},
    }


def test_visual_form_keeps_plugin_section_for_flat_fields_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, config_path = _build_client(
        tmp_path,
        monkeypatch,
        _FlatFieldsPlugin(),
        'api_contact = "old@example.com"\nenable_feature = true\ntimeout = 45\n\n'
        '[plugin]\nenabled = true\nconfig_version = "1.0.0"\n',
    )

    bundle = client.get(f"/api/webui/plugins/config/{_PLUGIN_ID}/bundle").json()
    assert bundle["config"]["plugin"] == {"enabled": True, "config_version": "1.0.0"}

    form_config = _edit_like_visual_form(bundle["schema"], bundle["config"], "enabled", False)
    response = client.put(f"/api/webui/plugins/config/{_PLUGIN_ID}", json={"config": form_config})

    assert response.status_code == 200
    assert _load_disk_config(config_path) == {
        "api_contact": "old@example.com",
        "enable_feature": True,
        "timeout": 45,
        "plugin": {"enabled": False, "config_version": "1.0.0"},
    }


def test_visual_form_keeps_real_general_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, config_path = _build_client(
        tmp_path,
        monkeypatch,
        _RealGeneralPlugin(),
        '[plugin]\nenabled = true\nconfig_version = "1.0.0"\n\n'
        '[general]\napi_contact = "old@example.com"\nenable_feature = false\n',
    )

    bundle = client.get(f"/api/webui/plugins/config/{_PLUGIN_ID}/bundle").json()
    schema = bundle["schema"]
    assert bundle["config"] == {
        "plugin": {"enabled": True, "config_version": "1.0.0"},
        "general": {"api_contact": "old@example.com", "enable_feature": False},
    }

    form_config = _edit_like_visual_form(schema, bundle["config"], "api_contact", "me@example.com")
    response = client.put(f"/api/webui/plugins/config/{_PLUGIN_ID}", json={"config": form_config})

    assert response.status_code == 200
    assert _load_disk_config(config_path) == {
        "plugin": {"enabled": True, "config_version": "1.0.0"},
        "general": {"api_contact": "me@example.com", "enable_feature": False},
    }


def test_visual_form_keeps_root_plugin_section_declared_as_dict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, config_path = _build_client(
        tmp_path,
        monkeypatch,
        _DictPluginSectionPlugin(),
        'api_contact = "old@example.com"\n\n[plugin]\nenabled = false\nconfig_version = "1.0.0"\n',
    )

    bundle = client.get(f"/api/webui/plugins/config/{_PLUGIN_ID}/bundle").json()
    schema = bundle["schema"]

    # dict 形式的 plugin 字段同样被 SDK 归入虚构 general 节，但根级 plugin 仍需保留供启用状态判断
    assert set(schema["sections"]["general"]["fields"]) == {"plugin", "api_contact"}
    assert bundle["config"]["plugin"] == {"enabled": False, "config_version": "1.0.0"}
    assert _read_form_value(schema, bundle["config"], "plugin") == {"enabled": False, "config_version": "1.0.0"}
    assert _read_form_value(schema, bundle["config"], "api_contact") == "old@example.com"

    form_config = _edit_like_visual_form(schema, bundle["config"], "api_contact", "me@example.com")
    response = client.put(f"/api/webui/plugins/config/{_PLUGIN_ID}", json={"config": form_config})

    assert response.status_code == 200
    assert _load_disk_config(config_path) == {
        "api_contact": "me@example.com",
        "plugin": {"enabled": False, "config_version": "1.0.0"},
    }


def test_visual_form_keeps_custom_general_section_without_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, config_path = _build_client(
        tmp_path,
        monkeypatch,
        _CustomGeneralSchemaPlugin(),
        '[plugin]\nenabled = true\nconfig_version = "1.0.0"\n',
    )

    bundle = client.get(f"/api/webui/plugins/config/{_PLUGIN_ID}/bundle").json()
    schema = bundle["schema"]
    # 插件自定义的 general 节是真实配置节，即使 config.toml 中暂无 [general] 表也不能按虚构节处理
    assert bundle["config"] == {"plugin": {"enabled": True, "config_version": "1.0.0"}}

    form_config = _edit_like_visual_form(schema, bundle["config"], "api_contact", "me@example.com")
    response = client.put(f"/api/webui/plugins/config/{_PLUGIN_ID}", json={"config": form_config})

    assert response.status_code == 200
    assert _load_disk_config(config_path) == {
        "plugin": {"enabled": True, "config_version": "1.0.0"},
        "general": {"api_contact": "me@example.com"},
    }


@pytest.mark.parametrize("endpoint,method", [("/bundle", "GET"), ("/schema", "GET"), ("", "GET"), ("", "PUT")])
@pytest.mark.parametrize("invalid", [False, True])
def test_runtime_failure_is_reported_without_disk_fallback(tmp_path, monkeypatch, endpoint, method, invalid):
    from src.plugin_runtime import integration

    inspect = config_routes_module._inspect_plugin_config_via_runtime
    client, config_path = _build_client(tmp_path, monkeypatch, _FlatFieldsPlugin(), _FLAT_FIELDS_CONFIG_TOML)
    monkeypatch.setattr(config_routes_module, "_inspect_plugin_config_via_runtime", inspect)
    manager = SimpleNamespace(inspect_plugin_config=AsyncMock(return_value=None))
    if invalid:
        manager.inspect_plugin_config.side_effect = ValueError("invalid group target")
    monkeypatch.setattr(integration, "get_plugin_runtime_manager", lambda: manager)

    async def run_direct(coroutine):
        return await coroutine

    monkeypatch.setattr(config_routes_module, "run_on_main_loop", run_direct)
    original = config_path.read_bytes()
    kwargs = {"json": {"config": {"timeout": 60}}} if method == "PUT" else {}
    response = client.request(method, f"/api/webui/plugins/config/{_PLUGIN_ID}{endpoint}", **kwargs)
    assert response.status_code == (400 if invalid else 503)
    assert "detail" in response.json() and "schema" not in response.json()
    assert config_path.read_bytes() == original


def test_missing_schema_does_not_infer_disk_fields(tmp_path, monkeypatch):
    client, _ = _build_client(tmp_path, monkeypatch, _FlatFieldsPlugin(), _FLAT_FIELDS_CONFIG_TOML)
    inspect = config_routes_module._inspect_plugin_config_via_runtime

    async def missing_schema(*args, **kwargs):
        snapshot = await inspect(*args, **kwargs)
        return snapshot.model_copy(update={"config_schema": {}})

    monkeypatch.setattr(config_routes_module, "_inspect_plugin_config_via_runtime", missing_schema)
    for endpoint in ("/bundle", "/schema"):
        response = client.get(f"/api/webui/plugins/config/{_PLUGIN_ID}{endpoint}")
        assert response.status_code == 500
        assert "schema" not in response.json()


@pytest.mark.parametrize("invalid_toml", ['timeout = "broken"\n', 'timeout = [\n'])
def test_invalid_config_bundle_preserves_source_and_reports_error(tmp_path, monkeypatch, invalid_toml):
    raw = '# 用户注释\n' + invalid_toml + '[plugin]\nconfig_version = "1.0.0"\n'
    client, config_path = _build_client(tmp_path, monkeypatch, _FlatFieldsPlugin(), raw)
    response = client.get(f"/api/webui/plugins/config/{_PLUGIN_ID}/bundle")
    assert response.status_code == 200
    bundle = response.json()
    assert bundle["schema"] is None and bundle["config"] == {}
    assert bundle["raw_config"] == raw
    assert bundle["config_error"]
    assert config_path.read_text(encoding="utf-8") == raw
    for endpoint in ("", "/schema"):
        response = client.get(f"/api/webui/plugins/config/{_PLUGIN_ID}{endpoint}")
        assert response.status_code == 400
        assert response.json()["detail"] == bundle["config_error"]


def test_raw_save_applies_and_refreshes_repaired_config(tmp_path, monkeypatch):
    from src.plugin_runtime import integration

    client, config_path = _build_client(
        tmp_path, monkeypatch, _FlatFieldsPlugin(), 'timeout = "broken"\n[plugin]\nconfig_version = "1.0.0"\n',
    )
    applied = []

    async def apply(plugin_id, write_config):
        await write_config()
        snapshot = await config_routes_module._inspect_plugin_config_via_runtime(plugin_id)
        status = "config_error" if snapshot.config_error else "success"
        applied.append(status)
        return status

    async def run_direct(coroutine):
        return await coroutine

    monkeypatch.setattr(integration, "get_plugin_runtime_manager", lambda: SimpleNamespace(apply_plugin_config=apply))
    monkeypatch.setattr(config_routes_module, "run_on_main_loop", run_direct)
    response = client.put(f"/api/webui/plugins/config/{_PLUGIN_ID}/raw", json={"config": _FLAT_FIELDS_CONFIG_TOML})
    assert response.status_code == 200 and response.json()["runtime_status"] == "success"
    assert applied == ["success"]
    bundle = client.get(f"/api/webui/plugins/config/{_PLUGIN_ID}/bundle").json()
    assert bundle["schema"] and "config_error" not in bundle
    assert config_path.read_text(encoding="utf-8") == _FLAT_FIELDS_CONFIG_TOML
