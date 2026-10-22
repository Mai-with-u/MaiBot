from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Generator, List, Optional, Tuple
from unittest.mock import AsyncMock, Mock

import asyncio
import json
import sys
import tomllib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugin_runtime import integration as integration_module
from src.plugin_runtime.protocol.envelope import (
    Envelope,
    InspectPluginConfigPayload,
    InspectPluginConfigResultPayload,
    MessageType,
    ValidatePluginConfigPayload,
    ValidatePluginConfigResultPayload,
)
from src.plugin_runtime.runner.runner_main import PluginRunner
from src.webui.services import git_mirror_service as mirror_service_module
from src.webui.routers.plugin import config_routes as config_routes_module
from src.webui.routers.plugin import icon_routes as icon_routes_module
from src.webui.routers.plugin import management as management_module
from src.webui.routers.plugin import support as support_module


@pytest.mark.parametrize("contents", [b"{invalid json", b"\xff", b"[]"])
def test_invalid_manifest_preserves_diagnostics(tmp_path: Path, monkeypatch, contents: bytes) -> None:
    manifest_path = tmp_path / "_manifest.json"
    manifest_path.write_bytes(contents)
    logger = Mock()
    monkeypatch.setattr(support_module, "logger", logger)

    assert support_module.load_manifest_json(manifest_path) is None
    logger.warning.assert_called_once()
    assert str(manifest_path) in logger.warning.call_args.args[0]
    if contents != b"[]":
        assert logger.warning.call_args.kwargs["exc_info"] is True


def test_manifest_loader_does_not_hide_unexpected_errors(tmp_path: Path, monkeypatch) -> None:
    manifest_path = tmp_path / "_manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(support_module.json, "load", Mock(side_effect=RuntimeError("unexpected loader failure")))

    with pytest.raises(RuntimeError, match="unexpected loader failure"):
        support_module.load_manifest_json(manifest_path)


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    manager = integration_module.PluginRuntimeManager()
    monkeypatch.setattr(integration_module, "get_plugin_runtime_manager", lambda: manager)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)

    demo_dir = plugins_dir / "demo_plugin"
    demo_dir.mkdir()
    (demo_dir / "_manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "id": "test.demo",
                "name": "Demo Plugin",
                "version": "1.0.0",
                "description": "demo plugin",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(management_module, "require_plugin_token", lambda _: "ok")
    monkeypatch.setattr(icon_routes_module, "require_plugin_token", lambda _: "ok")
    monkeypatch.setattr(support_module, "get_plugins_dir", lambda: plugins_dir)

    app = FastAPI()
    app.include_router(management_module.router, prefix="/api/webui/plugins")
    app.include_router(icon_routes_module.router, prefix="/api/webui/plugins")
    return TestClient(app)


def test_installed_plugins_only_scan_plugins_dir_and_exclude_a_memorix(client: TestClient):
    response = client.get("/api/webui/plugins/installed")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True

    ids = [plugin["id"] for plugin in payload["plugins"]]
    assert ids == ["test.demo"]
    assert "a-dawn.a-memorix" not in ids
    assert all("/src/plugins/built_in/" not in plugin["path"] for plugin in payload["plugins"])


def test_installed_plugins_expose_duplicate_id_failure_reason(client: TestClient, monkeypatch) -> None:
    plugins_dir = support_module.get_plugins_dir()
    (plugins_dir / "demo_plugin" / "config.toml").write_text("[plugin]\nenabled = true\n", encoding="utf-8")
    duplicate_dir = plugins_dir / "demo_plugin_copy"
    duplicate_dir.mkdir()
    duplicate_manifest = json.loads((plugins_dir / "demo_plugin" / "_manifest.json").read_text(encoding="utf-8"))
    (duplicate_dir / "_manifest.json").write_text(json.dumps(duplicate_manifest), encoding="utf-8")
    failure_reason = (
        "插件 ID 重复，已阻止加载；冲突目录: "
        f"{plugins_dir / 'demo_plugin'}, {duplicate_dir}"
    )
    async def snapshot():
        return {"statuses": {"test.demo": "failed"}, "failure_reasons": {"test.demo": failure_reason},
                "circuit_statuses": {}, "running": True}
    monkeypatch.setattr(integration_module.get_plugin_runtime_manager(), "get_plugin_state_snapshot", snapshot)
    response = client.get("/api/webui/plugins/installed")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["plugins"][0]["load_status"] == "failed"
    assert payload["plugins"][0]["load_error"] == failure_reason


@pytest.mark.parametrize("runtime_status, expected", [
    ("failed", "disabled"), ("offline", "disabled"),
    ("success", "success"), ("loading", "loading"), ("stopping", "stopping"),
])
def test_disabled_plugin_ignores_scan_failures_but_preserves_live_state(
    client: TestClient, monkeypatch, runtime_status: str, expected: str,
) -> None:
    plugin_path = support_module.resolve_installed_plugin_path("test.demo")
    (plugin_path / "config.toml").write_text("[plugin]\nenabled = false\n", encoding="utf-8")

    async def snapshot():
        return {"statuses": {"test.demo": runtime_status},
                "failure_reasons": {"test.demo": "Host 版本不兼容"},
                "circuit_statuses": {}, "running": True}

    monkeypatch.setattr(integration_module.get_plugin_runtime_manager(), "get_plugin_state_snapshot", snapshot)
    response = client.get("/api/webui/plugins/installed")
    assert response.status_code == 200
    plugin = response.json()["plugins"][0]
    assert plugin["enabled"] is False
    assert plugin["load_status"] == expected
    assert plugin["load_error"] == ""


def test_installed_plugins_expose_offline_adapter_status(client: TestClient, monkeypatch) -> None:
    async def snapshot():
        return {"statuses": {"test.demo": "offline"}, "failure_reasons": {}, "circuit_statuses": {}, "running": True}
    monkeypatch.setattr(integration_module.get_plugin_runtime_manager(), "get_plugin_state_snapshot", snapshot)

    response = client.get("/api/webui/plugins/installed")

    assert response.status_code == 200
    plugin = response.json()["plugins"][0]
    assert plugin["load_status"] == "offline"
    assert plugin["load_error"] == ""


def test_toggle_plugin_applies_enabled_state_before_returning(client: TestClient, monkeypatch) -> None:
    plugin_path = support_module.resolve_installed_plugin_path("test.demo")
    assert plugin_path is not None
    (plugin_path / "config.toml").write_text("[plugin]\nenabled = false\n", encoding="utf-8")
    waited_states: List[Tuple[str, bool]] = []

    # 缺包插件无法冷导入；切换开关必须先保存 enabled，再交给运行时安装依赖。
    inspect_config = AsyncMock(side_effect=AssertionError("toggle imported plugin before installing dependencies"))

    async def fake_apply_config(plugin_id: str, write_config) -> str:
        await write_config()
        waited_states.append((plugin_id, True))
        assert "enabled = true" in (plugin_path / "config.toml").read_text(encoding="utf-8")
        return "success"

    monkeypatch.setattr(config_routes_module, "_inspect_plugin_config_via_runtime", inspect_config)
    monkeypatch.setattr(integration_module.get_plugin_runtime_manager(), "apply_plugin_config", fake_apply_config)
    monkeypatch.setattr(config_routes_module, "require_plugin_token", lambda _: "ok")

    app = FastAPI()
    app.include_router(config_routes_module.router, prefix="/api/webui/plugins")

    response = TestClient(app).post("/api/webui/plugins/config/test.demo/toggle")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "enabled": True,
        "runtime_status": "success",
        "message": "插件已启用",
        "note": "状态更改已同步到插件运行时",
    }
    assert waited_states == [("test.demo", True)]
    inspect_config.assert_not_awaited()


def test_toggle_exposes_runtime_failure_instead_of_reporting_success(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        config_routes_module,
        "_inspect_plugin_config_via_runtime",
        AsyncMock(return_value=InspectPluginConfigResultPayload(success=True)),
    )

    async def fail(plugin_id, write_config):
        await write_config()
        raise RuntimeError("模拟插件加载失败")
    monkeypatch.setattr(integration_module.get_plugin_runtime_manager(), "apply_plugin_config", fail)
    monkeypatch.setattr(config_routes_module, "require_plugin_token", lambda _: "ok")
    app = FastAPI()
    app.include_router(config_routes_module.router, prefix="/api/webui/plugins")
    response = TestClient(app).post("/api/webui/plugins/config/test.demo/toggle")
    assert response.status_code == 409
    assert "模拟插件加载失败" in response.json()["detail"]


_CONFIG_MODEL_PLUGIN_ID = "test.config_model"

# 真实 SDK 插件：[plugin] 节只声明 SDK 要求的 config_version，未声明 enabled（#2080）
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


def _write_config_model_plugin_config(plugin_path: Path, *, enabled: bool) -> None:
    (plugin_path / "config.toml").write_text(
        "[plugin]\n"
        'config_version = "1.0.0"\n'
        f"enabled = {'true' if enabled else 'false'}\n"
        "\n"
        "[greeting]\n"
        'message = "hi"\n',
        encoding="utf-8",
    )


def _read_plugin_config(plugin_path: Path) -> Dict[str, Any]:
    with (plugin_path / "config.toml").open("rb") as file_obj:
        return tomllib.load(file_obj)


@pytest.fixture
def config_model_plugin(client: TestClient, monkeypatch) -> Generator[Tuple[TestClient, Path], None, None]:
    """安装一个声明 config_model 的真实 SDK 插件，并把配置路由接到真实 Runner 的配置解析。"""

    plugins_dir = support_module.get_plugins_dir()
    plugin_path = plugins_dir / "config_model_plugin"
    plugin_path.mkdir()
    (plugin_path / "plugin.py").write_text(_CONFIG_MODEL_PLUGIN_SOURCE, encoding="utf-8")
    (plugin_path / "_manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "version": "1.0.0",
                "name": _CONFIG_MODEL_PLUGIN_ID,
                "description": _CONFIG_MODEL_PLUGIN_ID,
                "author": {"name": "MaiBot", "url": "https://example.com"},
                "license": "GPL-v3.0-or-later",
                "urls": {"repository": "https://example.com/repo"},
                "host_application": {"min_version": "0.0.0", "max_version": "9999.9999.9999"},
                "sdk": {"min_version": "0.0.0", "max_version": "9999.9999.9999"},
                "dependencies": [],
                "capabilities": [],
                "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
                "id": _CONFIG_MODEL_PLUGIN_ID,
                "plugin_type": "extension",
            }
        ),
        encoding="utf-8",
    )
    runner = PluginRunner(host_address="unused", session_token="test", plugin_dirs=[str(plugins_dir)])
    # 加载 SDK 插件可能安装旧版导入钩子，限制在本测试内
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))

    def runner_request(method: str, plugin_id: str, payload: Dict[str, Any]) -> Envelope:
        return Envelope(
            request_id=1,
            message_type=MessageType.REQUEST,
            method=method,
            plugin_id=plugin_id,
            payload=payload,
        )

    async def inspect_via_runner(
        plugin_id: str,
        config_data: Optional[Dict[str, Any]] = None,
        *,
        use_provided_config: bool = False,
    ) -> InspectPluginConfigResultPayload:
        payload = InspectPluginConfigPayload(config_data=config_data or {}, use_provided_config=use_provided_config)
        response = await runner._handle_inspect_plugin_config(
            runner_request("plugin.inspect_config", plugin_id, payload.model_dump())
        )
        assert response.error is None
        return InspectPluginConfigResultPayload.model_validate(response.payload)

    async def validate_via_runner(plugin_id: str, config_data: Dict[str, Any]) -> Dict[str, Any]:
        payload = ValidatePluginConfigPayload(config_data=config_data)
        response = await runner._handle_validate_plugin_config(
            runner_request("plugin.validate_config", plugin_id, payload.model_dump())
        )
        assert response.error is None
        return dict(ValidatePluginConfigResultPayload.model_validate(response.payload).normalized_config)

    monkeypatch.setattr(config_routes_module, "_inspect_plugin_config_via_runtime", inspect_via_runner)
    monkeypatch.setattr(config_routes_module, "_validate_plugin_config_via_runtime", validate_via_runner)
    monkeypatch.setattr(config_routes_module, "require_plugin_token", lambda _: "ok")

    app = FastAPI()
    app.include_router(config_routes_module.router, prefix="/api/webui/plugins")
    try:
        yield TestClient(app), plugin_path
    finally:
        runner._loader.purge_plugin_modules(_CONFIG_MODEL_PLUGIN_ID, str(plugin_path))


def test_toggle_round_trips_config_model_plugin_without_declared_enabled(
    config_model_plugin: Tuple[TestClient, Path], monkeypatch
) -> None:
    config_client, plugin_path = config_model_plugin
    _write_config_model_plugin_config(plugin_path, enabled=True)
    waited_states: List[bool] = []

    async def fake_apply_config(plugin_id: str, write_config) -> str:
        await write_config()
        enabled = _read_plugin_config(plugin_path)["plugin"]["enabled"]
        waited_states.append(enabled)
        return "success" if enabled else "inactive"

    monkeypatch.setattr(integration_module.get_plugin_runtime_manager(), "apply_plugin_config", fake_apply_config)

    toggled_states: List[bool] = []
    for _ in range(3):
        response = config_client.post(f"/api/webui/plugins/config/{_CONFIG_MODEL_PLUGIN_ID}/toggle")
        assert response.status_code == 200
        toggled_states.append(response.json()["enabled"])
        assert _read_plugin_config(plugin_path)["plugin"]["enabled"] is toggled_states[-1]

    assert toggled_states == [False, True, False]
    assert waited_states == [False, True, False]


def test_saving_config_keeps_config_model_plugin_disabled(config_model_plugin: Tuple[TestClient, Path]) -> None:
    config_client, plugin_path = config_model_plugin
    _write_config_model_plugin_config(plugin_path, enabled=False)

    bundle = config_client.get(f"/api/webui/plugins/config/{_CONFIG_MODEL_PLUGIN_ID}/bundle").json()
    # 配置页的启用开关读取 config.plugin.enabled
    assert bundle["config"]["plugin"]["enabled"] is False

    form_config = bundle["config"]
    form_config["greeting"]["message"] = "hello"
    response = config_client.put(
        f"/api/webui/plugins/config/{_CONFIG_MODEL_PLUGIN_ID}",
        json={"config": form_config},
    )

    assert response.status_code == 200
    saved_config = _read_plugin_config(plugin_path)
    assert saved_config["plugin"] == {"config_version": "1.0.0", "enabled": False}
    assert saved_config["greeting"] == {"message": "hello"}


def test_resolve_installed_plugin_path_falls_back_to_manifest_id(client: TestClient):
    plugin_path = support_module.resolve_installed_plugin_path("test.demo")

    assert plugin_path is not None
    assert plugin_path.name == "demo_plugin"


def test_resolve_installed_plugin_path_accepts_manifest_id_case_mismatch(client: TestClient):
    plugin_path = support_module.resolve_installed_plugin_path("Test.Demo")

    assert plugin_path is not None
    assert plugin_path.name == "demo_plugin"


def test_get_plugin_icon_serves_manifest_declared_local_icon(client: TestClient):
    plugin_path = support_module.resolve_installed_plugin_path("test.demo")
    assert plugin_path is not None
    assets_dir = plugin_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "icon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"></svg>',
        encoding="utf-8",
    )
    manifest = json.loads((plugin_path / "_manifest.json").read_text(encoding="utf-8"))
    manifest["display"] = {
        "icon": {
            "type": "local",
            "value": "assets/icon.svg",
        }
    }
    (plugin_path / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    response = client.get("/api/webui/plugins/icon/test.demo")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg")
    assert b"<svg" in response.content


def test_get_plugin_icon_rejects_manifest_declared_parent_path(client: TestClient):
    plugin_path = support_module.resolve_installed_plugin_path("test.demo")
    assert plugin_path is not None
    manifest = json.loads((plugin_path / "_manifest.json").read_text(encoding="utf-8"))
    manifest["display"] = {
        "icon": {
            "type": "local",
            "value": "../icon.svg",
        }
    }
    (plugin_path / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    response = client.get("/api/webui/plugins/icon/test.demo")

    assert response.status_code == 400


def test_install_plugin_rejects_mismatching_manifest_id(client: TestClient, monkeypatch):
    class FakeGitMirrorService:
        async def clone_repository(self, **kwargs):
            target_path = kwargs["target_path"]
            target_path.mkdir(parents=True, exist_ok=True)
            (target_path / "_manifest.json").write_text(
                json.dumps(
                    {
                        "manifest_version": 2,
                        "id": "author.declared",
                        "name": "Declared Plugin",
                        "version": "1.0.0",
                        "author": {"name": "author"},
                    }
                ),
                encoding="utf-8",
            )
            return {"success": True}

    monkeypatch.setattr(management_module, "get_git_mirror_service", lambda: FakeGitMirrorService())

    response = client.post(
        "/api/webui/plugins/install",
        json={
            "plugin_id": "market.plugin",
            "repository_url": "https://github.com/author/declared",
            "branch": "main",
        },
    )

    assert response.status_code == 400
    assert "插件 ID 不匹配" in response.json()["detail"]
    assert not support_module.get_plugin_candidate_paths("market.plugin")[0].exists()


def test_install_plugin_rejects_missing_manifest_id(client: TestClient, monkeypatch):
    class FakeGitMirrorService:
        async def clone_repository(self, **kwargs):
            target_path = kwargs["target_path"]
            target_path.mkdir(parents=True, exist_ok=True)
            (target_path / "_manifest.json").write_text(
                json.dumps(
                    {
                        "manifest_version": 2,
                        "name": "Legacy Plugin",
                        "version": "1.0.0",
                        "author": {"name": "author"},
                    }
                ),
                encoding="utf-8",
            )
            return {"success": True}

    monkeypatch.setattr(management_module, "get_git_mirror_service", lambda: FakeGitMirrorService())

    response = client.post(
        "/api/webui/plugins/install",
        json={
            "plugin_id": "market.legacy",
            "repository_url": "https://github.com/author/legacy",
            "branch": "main",
        },
    )

    assert response.status_code == 400
    assert "缺少必需字段: id" in response.json()["detail"]
    assert not support_module.get_plugin_candidate_paths("market.legacy")[0].exists()


def test_install_plugin_cleans_config_only_residue(client: TestClient, monkeypatch):
    residue_path, _ = support_module.get_plugin_candidate_paths("market.residue")
    residue_path.mkdir(parents=True)
    (residue_path / "config.toml").write_text("[plugin]\nenabled = true\n", encoding="utf-8")

    class FakeGitMirrorService:
        async def clone_repository(self, **kwargs):
            target_path = kwargs["target_path"]
            assert target_path == residue_path
            assert not (target_path / "config.toml").exists()
            target_path.mkdir(parents=True, exist_ok=True)
            (target_path / "_manifest.json").write_text(
                json.dumps(
                    {
                        "manifest_version": 2,
                        "id": "market.residue",
                        "name": "Residue Plugin",
                        "version": "1.0.0",
                        "author": {"name": "market"},
                    }
                ),
                encoding="utf-8",
            )
            return {"success": True}

    monkeypatch.setattr(management_module, "get_git_mirror_service", lambda: FakeGitMirrorService())

    response = client.post(
        "/api/webui/plugins/install",
        json={
            "plugin_id": "market.residue",
            "repository_url": "https://github.com/market/residue",
            "branch": "main",
        },
    )

    assert response.status_code == 200
    assert (residue_path / "_manifest.json").exists()
    assert not (residue_path / "config.toml").exists()


def test_clone_repository_reports_plugin_and_mirror_progress(tmp_path, monkeypatch):
    events = []

    from src.common.git_source import GitSource
    from src.plugin_market import sources as market_sources

    monkeypatch.setattr(market_sources, "download_source", lambda: GitSource.select("cnb"))
    monkeypatch.setattr(mirror_service_module, "_validate_custom_outbound_url", lambda value: value)

    async def collect_progress(**kwargs):
        events.append(kwargs)

    async def fake_run(cmd, cwd=None):
        assert cmd[:2] == ["git", "clone"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    service = mirror_service_module.GitMirrorService(max_retries=1)
    monkeypatch.setattr(mirror_service_module, "_run_git_process", fake_run)
    mirror_service_module.set_update_progress_callback(collect_progress)

    try:
        result = asyncio.run(
            service.clone_repository(
                owner="owner",
                repo="repo",
                target_path=tmp_path / "repo",
                depth=1,
                plugin_id="market.plugin",
            )
        )
    finally:
        mirror_service_module.set_update_progress_callback(None)

    assert result["success"] is True
    assert any(event.get("plugin_id") == "market.plugin" for event in events)
    assert any(event.get("mirror_name") == "CNB 镜像" for event in events)
    assert any(event.get("attempt") == 1 and event.get("max_attempts") == 1 for event in events)


def test_clone_repository_cleans_partial_directory_on_git_failure(tmp_path, monkeypatch):
    target_path = tmp_path / "bad_plugin"

    async def fake_run(cmd, cwd=None):
        assert cmd[:2] == ["git", "clone"]
        target_path.mkdir(parents=True, exist_ok=True)
        (target_path / ".git").mkdir()
        return SimpleNamespace(returncode=128, stdout="", stderr="network failed")

    service = mirror_service_module.GitMirrorService(max_retries=1)
    monkeypatch.setattr(mirror_service_module, "_run_git_process", fake_run)

    result = asyncio.run(
        service._clone_with_url(
            url="https://github.com/test/bad.git",
            target_path=target_path,
            branch=None,
            depth=1,
            mirror_type="test",
        )
    )

    assert result["success"] is False
    assert "network failed" in result["error"]
    assert not target_path.exists()


def test_uninstall_plugin_releases_runtime_before_delete(client: TestClient, monkeypatch):
    from src.plugin_runtime import integration as integration_module

    plugin_path = support_module.resolve_installed_plugin_path("test.demo")
    assert plugin_path is not None
    reload_calls = []

    class FakeRuntimeManager:
        async def reload_plugins_globally(self, plugin_ids, reason="manual"):
            reload_calls.append((list(plugin_ids), reason))
            config_text = (plugin_path / "config.toml").read_text(encoding="utf-8")
            assert "enabled = false" in config_text
            return True

    monkeypatch.setattr(integration_module, "get_plugin_runtime_manager", lambda: FakeRuntimeManager())

    response = client.post("/api/webui/plugins/uninstall", json={"plugin_id": "test.demo"})

    assert response.status_code == 200
    assert reload_calls == [(["test.demo"], "uninstall")]
    assert not plugin_path.exists()


def test_update_non_git_plugin_reinstalls_and_preserves_known_user_files(client: TestClient, monkeypatch):
    plugin_path = support_module.resolve_installed_plugin_path("test.demo")
    assert plugin_path is not None
    (plugin_path / "plugin.py").write_text("old source", encoding="utf-8")
    (plugin_path / "config.toml").write_text("[plugin]\nenabled = false\n", encoding="utf-8")
    (plugin_path / "custom.json").write_text('{"user": true}', encoding="utf-8")
    config_backup_dir = plugin_path / "config_back"
    config_backup_dir.mkdir()
    (config_backup_dir / "config.toml.backup").write_text("[plugin]\nenabled = true\n", encoding="utf-8")

    class FakeGitMirrorService:
        async def clone_repository(self, **kwargs):
            assert kwargs["operation"] == "update"
            target_path = kwargs["target_path"]
            target_path.mkdir(parents=True, exist_ok=True)
            (target_path / ".git").mkdir()
            (target_path / "plugin.py").write_text("new source", encoding="utf-8")
            (target_path / "config.toml").write_text("[plugin]\nenabled = true\n", encoding="utf-8")
            (target_path / "_manifest.json").write_text(
                json.dumps(
                    {
                        "manifest_version": 2,
                        "id": "test.demo",
                        "name": "Demo Plugin",
                        "version": "1.1.0",
                    }
                ),
                encoding="utf-8",
            )
            return {"success": True}

    monkeypatch.setattr(management_module, "get_git_mirror_service", lambda: FakeGitMirrorService())

    response = client.post(
        "/api/webui/plugins/update",
        json={
            "plugin_id": "test.demo",
            "repository_url": "https://github.com/test/demo",
            "branch": "main",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["update_mode"] == "reinstall_from_backup"
    backup_path = Path(payload["backup_path"])
    assert backup_path.exists()
    assert (backup_path / "custom.json").read_text(encoding="utf-8") == '{"user": true}'
    assert (plugin_path / ".git").is_dir()
    assert (plugin_path / "plugin.py").read_text(encoding="utf-8") == "new source"
    assert (plugin_path / "config.toml").read_text(encoding="utf-8") == "[plugin]\nenabled = false\n"
    assert (plugin_path / "config_back" / "config.toml.backup").exists()
    assert not (plugin_path / "custom.json").exists()
    manifest = json.loads((plugin_path / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.1.0"


def test_update_non_git_plugin_rolls_back_when_manifest_id_mismatches(client: TestClient, monkeypatch):
    plugin_path = support_module.resolve_installed_plugin_path("test.demo")
    assert plugin_path is not None
    (plugin_path / "plugin.py").write_text("old source", encoding="utf-8")
    (plugin_path / "custom.json").write_text('{"user": true}', encoding="utf-8")

    class FakeGitMirrorService:
        async def clone_repository(self, **kwargs):
            target_path = kwargs["target_path"]
            target_path.mkdir(parents=True, exist_ok=True)
            (target_path / ".git").mkdir()
            (target_path / "plugin.py").write_text("wrong source", encoding="utf-8")
            (target_path / "_manifest.json").write_text(
                json.dumps(
                    {
                        "manifest_version": 2,
                        "id": "other.demo",
                        "name": "Other Plugin",
                        "version": "1.1.0",
                    }
                ),
                encoding="utf-8",
            )
            return {"success": True}

    monkeypatch.setattr(management_module, "get_git_mirror_service", lambda: FakeGitMirrorService())

    response = client.post(
        "/api/webui/plugins/update",
        json={
            "plugin_id": "test.demo",
            "repository_url": "https://github.com/test/demo",
            "branch": "main",
        },
    )

    assert response.status_code == 400
    assert "新版本插件 ID 不匹配" in response.json()["detail"]
    assert (plugin_path / "plugin.py").read_text(encoding="utf-8") == "old source"
    assert (plugin_path / "custom.json").read_text(encoding="utf-8") == '{"user": true}'
    assert not (plugin_path / ".git").exists()


@pytest.mark.parametrize(
    ("active_operation", "active_label"),
    [("install", "安装"), ("uninstall", "卸载"), ("update", "更新")],
)
def test_update_rejects_conflicting_operation_for_same_plugin(
    client: TestClient,
    active_operation: str,
    active_label: str,
):
    with management_module._reserve_plugin_operation("test.demo", active_operation):
        response = client.post(
            "/api/webui/plugins/update",
            json={
                "plugin_id": "test.demo",
                "repository_url": "https://github.com/test/demo",
                "branch": "main",
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == f"插件 test.demo 正在执行{active_label}操作，请等待完成后重试"


def test_plugin_operation_reservation_allows_different_plugins(client: TestClient):
    with management_module._reserve_plugin_operation("test.demo", "update"):
        with management_module._reserve_plugin_operation("other.demo", "update"):
            pass


def test_plugin_operation_reservation_releases_after_failure(client: TestClient):
    with pytest.raises(RuntimeError, match="模拟操作失败"):
        with management_module._reserve_plugin_operation("test.demo", "update"):
            raise RuntimeError("模拟操作失败")

    with management_module._reserve_plugin_operation("test.demo", "update"):
        pass


def test_update_disconnect_cancels_worker_and_waits_for_cleanup(monkeypatch) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        cleaned = asyncio.Event()
        progress = []

        async def worker(request, token):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        class DisconnectedRequest:
            async def receive(self) -> Dict[str, Any]:
                await started.wait()
                return {"type": "http.disconnect"}

        async def record_progress(**kwargs):
            progress.append(kwargs)

        monkeypatch.setattr(management_module, "require_plugin_token", lambda _: "ok")
        monkeypatch.setattr(management_module, "_perform_update_plugin", worker)
        monkeypatch.setattr(management_module, "update_progress", record_progress)
        request = management_module.UpdatePluginRequest(
            plugin_id="test.demo", repository_url="https://github.com/test/demo", branch="main",
        )
        with pytest.raises(management_module.HTTPException) as error:
            await management_module.update_plugin(request, "ok", DisconnectedRequest())
        assert error.value.status_code == 499
        assert cleaned.is_set()
        assert progress[-1]["message"] == "更新已取消"

    asyncio.run(scenario())


def test_cancel_git_process_terminates_process_before_returning(monkeypatch) -> None:
    async def scenario() -> None:
        processes = []
        original_popen = mirror_service_module.subprocess.Popen

        def capture_process(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            return process

        monkeypatch.setattr(mirror_service_module.subprocess, "Popen", capture_process)
        task = asyncio.create_task(mirror_service_module._run_git_process(
            [sys.executable, "-c", "import time; time.sleep(60)"],
        ))
        while not processes:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[0].poll() is not None

    asyncio.run(scenario())


def test_cancel_non_git_update_preserves_plugin_and_removes_candidate(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        plugin_path = tmp_path / "demo"
        plugin_path.mkdir()
        (plugin_path / "plugin.py").write_text("old source", encoding="utf-8")
        manifest = {"id": "test.demo", "version": "1.0.0"}
        candidate_paths = []
        started = asyncio.Event()

        async def clone(request, target_path):
            target_path.mkdir()
            (target_path / "partial").write_text("partial download", encoding="utf-8")
            candidate_paths.append(target_path)
            started.set()
            await asyncio.Event().wait()

        async def progress(**kwargs):
            pass

        monkeypatch.setattr(management_module, "_clone_plugin_repository_for_update", clone)
        monkeypatch.setattr(management_module, "update_progress", progress)
        request = management_module.UpdatePluginRequest(plugin_id="test.demo")
        task = asyncio.create_task(management_module._update_non_git_plugin(
            "test.demo", plugin_path, manifest, request,
        ))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (plugin_path / "plugin.py").read_text(encoding="utf-8") == "old source"
        assert not candidate_paths[0].exists()

    asyncio.run(scenario())
