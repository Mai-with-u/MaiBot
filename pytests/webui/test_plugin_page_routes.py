from pathlib import Path
from typing import Any, Dict

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.webui.dependencies import require_auth
from src.webui.routers.plugin import pages as pages_module
from src.webui.services.plugin_page_registry import discover_plugin_pages


def _write_plugin(
    plugins_dir: Path,
    plugin_id: str,
    *,
    entry: str = "webui/dist/index.js",
    write_entry: bool = True,
) -> Path:
    plugin_dir = plugins_dir / plugin_id.replace(".", "_")
    entry_path = plugin_dir / Path(entry)
    manifest: Dict[str, Any] = {
        "manifest_version": 2,
        "version": "1.0.0",
        "name": plugin_id,
        "description": "WebUI 页面测试插件",
        "author": {"name": "MaiBot", "url": "https://example.com"},
        "license": "GPL-v3.0-or-later",
        "urls": {"repository": "https://example.com/repository"},
        "host_application": {"min_version": "1.0.0", "max_version": "9.99.99"},
        "sdk": {"min_version": "2.0.0", "max_version": "9.99.99"},
        "dependencies": [],
        "capabilities": ["webui.page"],
        "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
        "id": plugin_id,
        "extensions": {
            "webui_pages": [
                {
                    "id": "hello",
                    "title": "Hello World",
                    "route": "hello",
                    "entry": entry,
                    "component": "mount",
                }
            ]
        },
    }
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if write_entry:
        entry_path.parent.mkdir(parents=True, exist_ok=True)
        entry_path.write_text("export function mount() {}", encoding="utf-8")
    return plugin_dir


def test_discovery_only_includes_loaded_plugins_and_generates_host_urls(tmp_path: Path) -> None:
    first_plugin = _write_plugin(tmp_path, "example.first")
    second_plugin = _write_plugin(tmp_path, "example.second")

    pages = discover_plugin_pages([first_plugin, second_plugin], {"example.first"})

    assert [page.page_id for page in pages] == ["hello"]
    assert pages[0].plugin_id == "example.first"
    assert pages[0].route == "/plugin-pages/example.first/hello"
    assert pages[0].entry_url.endswith(
        "/api/webui/plugins/example.first/assets/webui/dist/index.js?v=1.0.0"
    )


def test_discovery_rejects_missing_entry_file(tmp_path: Path) -> None:
    plugin_path = _write_plugin(tmp_path, "example.missing", write_entry=False)

    with pytest.raises(ValueError, match="入口文件不存在"):
        discover_plugin_pages([plugin_path], {"example.missing"})


def test_discovery_rejects_symlinked_entry_file(tmp_path: Path) -> None:
    plugin_path = _write_plugin(tmp_path, "example.symlink", write_entry=False)
    entry_path = plugin_path / "webui" / "dist" / "index.js"
    outside_path = tmp_path / "outside.js"
    outside_path.write_text("export function mount() {}", encoding="utf-8")
    try:
        entry_path.symlink_to(outside_path)
    except OSError as exc:
        pytest.skip(f"当前环境不支持符号链接: {exc}")

    with pytest.raises(ValueError, match="符号链接"):
        discover_plugin_pages([plugin_path], {"example.symlink"})


@pytest.fixture
def page_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    plugin_path = _write_plugin(tmp_path, "example.first")
    pages = discover_plugin_pages([plugin_path], {"example.first"})
    monkeypatch.setattr(pages_module, "discover_runtime_plugin_pages", lambda: pages)

    app = FastAPI()
    app.include_router(pages_module.router, prefix="/api/webui/plugins")
    return app


def test_page_list_requires_auth(page_app: FastAPI) -> None:
    unauthenticated_client = TestClient(page_app)

    response = unauthenticated_client.get("/api/webui/plugins/pages")

    assert response.status_code == 401


def test_page_list_returns_host_generated_urls(page_app: FastAPI) -> None:
    page_app.dependency_overrides[require_auth] = lambda: "test-token"
    authenticated_client = TestClient(page_app)

    response = authenticated_client.get("/api/webui/plugins/pages")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "pages": [
            {
                "plugin_id": "example.first",
                "page_id": "hello",
                "title": "Hello World",
                "route": "/plugin-pages/example.first/hello",
                "entry": "/api/webui/plugins/example.first/assets/webui/dist/index.js?v=1.0.0",
                "component": "mount",
                "icon": None,
                "order": 0,
                "permissions": [],
                "api_base": "/api/webui/plugins/example.first/pages/hello/api",
            }
        ],
        "warnings": [],
    }


def test_asset_route_rejects_parent_path(page_app: FastAPI) -> None:
    page_app.dependency_overrides[require_auth] = lambda: "test-token"
    authenticated_client = TestClient(page_app)

    response = authenticated_client.get(
        "/api/webui/plugins/example.first/assets/../_manifest.json"
    )

    assert response.status_code in {400, 404}


def test_asset_route_returns_javascript(page_app: FastAPI) -> None:
    page_app.dependency_overrides[require_auth] = lambda: "test-token"
    authenticated_client = TestClient(page_app)

    response = authenticated_client.get(
        "/api/webui/plugins/example.first/assets/webui/dist/index.js"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert b"function mount" in response.content
