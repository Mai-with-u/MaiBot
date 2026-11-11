from copy import deepcopy
from fastapi import HTTPException
from unittest.mock import AsyncMock

import json

import pytest

from src.webui.services import ai_search_market as market
from src.webui.services import ai_search_plugin_config as config


@pytest.mark.asyncio
async def test_installed_plugin_tool_reports_redacted_runtime_load_errors(monkeypatch):
    from src.webui.routers.plugin import management

    plugins = [
        {
            "id": plugin_id,
            "manifest": {"name": plugin_id, "version": "1.0.0"},
            "enabled": True,
            "load_status": status,
            "load_error": error,
        }
        for plugin_id, status, error in (
            ("test.voice", "failed", "on_load 失败: FileNotFoundError: ffmpeg"),
            ("test.slow", "timed_out", "超过 30 秒的等待阈值"),
            ("test.private", "failed", "on_load 失败: api_key=do-not-leak"),
            ("test.ready", "success", ""),
        )
    ]
    monkeypatch.setattr(management, "get_installed_plugins", AsyncMock(return_value={"plugins": plugins}))

    result = await market.AISearchMarketStore().execute("list_installed_plugins", {})
    installed = {plugin["id"]: plugin for plugin in result["plugins"]}

    assert installed["test.voice"]["load_error"] == plugins[0]["load_error"]
    assert installed["test.slow"]["load_error"] == plugins[1]["load_error"]
    assert "已隐藏" in installed["test.private"]["load_error"]
    assert "do-not-leak" not in json.dumps(result)
    assert installed["test.ready"]["load_error"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("load_status", ["failed", "timed_out", "success"])
async def test_config_http_error_reports_only_recorded_load_failures(monkeypatch, load_status):
    error = HTTPException(status_code=400, detail="private config value")
    monkeypatch.setattr(config, "load_plugin_config", AsyncMock(side_effect=error))
    plugin = {
        "id": "test.voice",
        "name": "Voice",
        "load_status": load_status,
        "load_error": "cannot import name 'ActivationType'" if load_status != "success" else "",
    }
    monkeypatch.setattr(market, "list_installed_plugins", AsyncMock(return_value={"plugins": [plugin]}))
    store = market.AISearchMarketStore()

    if load_status == "success":
        with pytest.raises(HTTPException) as exc_info:
            await store.execute("read_plugin_config", {"plugin_id": "test.voice"})
        assert exc_info.value is error
    else:
        result = await store.execute("read_plugin_config", {"plugin_id": "test.voice"})
        assert result["plugin"] == plugin
        assert "error" not in result  # 结构化加载诊断要进入最终回答的证据。
        assert "private config value" not in json.dumps(result)


@pytest.fixture
def bundle():
    fields = {
        "frequency": {"type": "int", "description": "发送间隔"},
        "apiKey": {"type": "str", "default": "do-not-leak"},
        "credential": {"type": "str", "input_type": "password"},
        "nested": {"type": "dict"},
        "disabled_field": {"type": "str", "disabled": True},
    }
    return {
        "schema": {"plugin_info": {"name": "Voice"}, "sections": {"general": {"fields": fields}}},
        "config": {
            "general": {
                "frequency": 5,
                "apiKey": "do-not-leak",
                "credential": "another-secret",
                "nested": {"accessToken": "nested-secret"},
                "disabled_field": "fixed",
            }
        },
        "raw_config": "api_key = 'do-not-leak'",
    }


@pytest.mark.asyncio
async def test_config_read_redacts_secrets_and_patch_only_updates_requested_field(monkeypatch, bundle):
    monkeypatch.setattr(config, "load_plugin_config", AsyncMock(side_effect=lambda _: deepcopy(bundle)))
    save = AsyncMock(return_value={"success": True, "message": "配置已保存", "config": {"frequency": 8}})
    monkeypatch.setattr(config, "save_plugin_config", save)
    store = config.PluginConfigStore()
    result = await store.read("test.voice")
    encoded = json.dumps(result)
    assert all(secret not in encoded for secret in ("do-not-leak", "another-secret", "nested-secret", "raw_config"))
    snapshot = repr(store.snapshots)
    assert all(secret not in snapshot for secret in ("do-not-leak", "another-secret", "nested-secret", "raw_config"))
    fields = {field["path"][-1]: field for field in result["fields"]}
    assert fields["frequency"]["value"] == 5
    assert fields["apiKey"]["read_only"] is True

    result = await store.update(
        config.PluginConfigPatch(
            plugin_id="test.voice",
            changes=[
                config.PluginFieldChange(path=["general", "frequency"], value="8"),
            ],
        )
    )
    save.assert_awaited_once_with("test.voice", {"general": {"frequency": "8"}})
    assert result["config_update"]["changes"] == [{"path": ["general", "frequency"], "before": 5, "after": 8}]
    assert "test.voice" not in store.snapshots
    assert len(store.updates) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["apiKey", "credential", "nested", "disabled_field", "invented"])
async def test_sensitive_readonly_or_unknown_fields_never_reach_save(monkeypatch, bundle, field):
    monkeypatch.setattr(config, "load_plugin_config", AsyncMock(return_value=bundle))
    save = AsyncMock()
    monkeypatch.setattr(config, "save_plugin_config", save)
    store = config.PluginConfigStore()
    await store.read("test.voice")
    with pytest.raises(ValueError):
        await store.update(
            config.PluginConfigPatch(
                plugin_id="test.voice",
                changes=[
                    config.PluginFieldChange(path=["general", field], value="replacement"),
                ],
            )
        )
    save.assert_not_awaited()
    assert store.updates == []


@pytest.mark.asyncio
async def test_config_requires_read_and_rejects_stale_values(monkeypatch, bundle):
    changed = deepcopy(bundle)
    changed["config"]["general"]["frequency"] = 9
    load = AsyncMock(side_effect=[bundle, changed])
    save = AsyncMock()
    monkeypatch.setattr(config, "load_plugin_config", load)
    monkeypatch.setattr(config, "save_plugin_config", save)
    store = config.PluginConfigStore()
    request = config.PluginConfigPatch(
        plugin_id="test.voice",
        changes=[
            config.PluginFieldChange(path=["general", "frequency"], value=8),
        ],
    )
    with pytest.raises(ValueError, match="修改前必须"):
        await store.update(request)
    load.assert_not_awaited()
    await store.read("test.voice")
    with pytest.raises(ValueError, match="其他操作"):
        await store.update(request)
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_validation_failure_does_not_report_success(monkeypatch, bundle):
    monkeypatch.setattr(config, "load_plugin_config", AsyncMock(return_value=bundle))
    monkeypatch.setattr(config, "save_plugin_config", AsyncMock(side_effect=ValueError("校验失败")))
    store = config.PluginConfigStore()
    await store.read("test.voice")
    with pytest.raises(ValueError, match="校验失败"):
        await store.update(
            config.PluginConfigPatch(
                plugin_id="test.voice",
                changes=[
                    config.PluginFieldChange(path=["general", "frequency"], value=8),
                ],
            )
        )
    assert store.updates == []


@pytest.fixture
def install_detail():
    return {
        "plugin": {
            "id": "test.voice",
            "name": "Voice",
            "version": "2.0.0",
            "repository_url": "https://github.com/test/voice",
        },
        "releases": {
            "mode": "releases",
            "recommended_version": "1.0.0",
            "sync_error": None,
            "versions": [{"version": "1.0.0", "compatible": True}, {"version": "2.0.0", "compatible": False}],
        },
    }


@pytest.mark.asyncio
async def test_prepare_install_only_produces_manual_confirmation_request(monkeypatch, install_detail):
    monkeypatch.setattr(market.service, "get_detail", AsyncMock(return_value=install_detail))
    monkeypatch.setattr(market, "list_installed_plugins", AsyncMock(return_value={"plugins": []}))
    store = market.AISearchMarketStore()
    result = await store.execute("prepare_plugin_install", {"plugin_id": "Voice"})
    request = result["install_request"]
    assert request["plugin_id"] == "test.voice"
    assert request["version"] == "1.0.0"
    assert "尚未安装" in result["message"]
    repeated = await store.execute("prepare_plugin_install", {"plugin_id": "Voice"})
    assert repeated["install_request"]["id"] == request["id"]
    assert len(store.install_requests) == 1
    assert "install_plugin" not in market.PLUGIN_TOOL_NAMES
    assert "install_plugin" not in {tool["name"] for tool in market.build_market_tools()}


@pytest.mark.asyncio
@pytest.mark.parametrize("installed,version", [(True, ""), (False, "2.0.0")])
async def test_installed_or_incompatible_plugin_has_no_install_request(monkeypatch, install_detail, installed, version):
    monkeypatch.setattr(market.service, "get_detail", AsyncMock(return_value=install_detail))
    monkeypatch.setattr(
        market,
        "list_installed_plugins",
        AsyncMock(return_value={"plugins": [{"id": "test.voice"}] if installed else []}),
    )
    store = market.AISearchMarketStore()
    with pytest.raises(ValueError):
        await store.execute("prepare_plugin_install", {"plugin_id": "test.voice", "version": version})
    assert store.install_requests == {}
