from pathlib import Path
from types import SimpleNamespace

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.webui.services import git_mirror_service as mirror_service_module
from src.webui.routers.plugin import icon_routes as icon_routes_module
from src.webui.routers.plugin import management as management_module
from src.webui.routers.plugin import support as support_module


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
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


def test_install_plugin_preserves_manifest_declared_id(client: TestClient, monkeypatch):
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

    assert response.status_code == 200
    plugin_path = support_module.resolve_installed_plugin_path("author.declared")
    assert plugin_path is not None
    manifest = json.loads((plugin_path / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "author.declared"


def test_install_plugin_backfills_missing_manifest_id(client: TestClient, monkeypatch):
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

    assert response.status_code == 200
    plugin_path = support_module.resolve_installed_plugin_path("market.legacy")
    assert plugin_path is not None
    manifest = json.loads((plugin_path / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "market.legacy"


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

    class FakeMirrorConfig:
        def get_enabled_mirrors(self):
            return [
                {
                    "id": "test-mirror",
                    "name": "测试镜像源",
                    "clone_prefix": "https://example.com/https://github.com",
                    "raw_prefix": "https://example.com/https://raw.githubusercontent.com",
                    "enabled": True,
                    "priority": 1,
                }
            ]

    async def collect_progress(**kwargs):
        events.append(kwargs)

    def fake_run(cmd, capture_output, text, timeout):
        assert cmd[:2] == ["git", "clone"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    service = mirror_service_module.GitMirrorService(max_retries=1, timeout=1, config=FakeMirrorConfig())
    monkeypatch.setattr(mirror_service_module.subprocess, "run", fake_run)
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
    assert any(event.get("mirror_name") == "测试镜像源" for event in events)
    assert any(event.get("attempt") == 1 and event.get("max_attempts") == 1 for event in events)


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
