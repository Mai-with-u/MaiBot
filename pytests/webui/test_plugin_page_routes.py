import json
from pathlib import Path
from typing import Any, Dict

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
    route: str = "hello",
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
                    "route": route,
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

    pages, _warnings = discover_plugin_pages([first_plugin, second_plugin], {"example.first"})

    assert [page.page_id for page in pages] == ["hello"]
    assert pages[0].plugin_id == "example.first"
    assert pages[0].route == "/plugin-pages/example.first/hello"
    assert pages[0].entry_url.endswith(
        "/api/webui/plugins/example.first/assets/webui/dist/index.js?v=1.0.0"
    )


def test_discovery_skips_missing_entry_file_with_warning(tmp_path: Path) -> None:
    plugin_path = _write_plugin(tmp_path, "example.missing", write_entry=False)

    pages, warnings = discover_plugin_pages([plugin_path], {"example.missing"})

    assert pages == []
    assert any("入口文件不存在" in warning for warning in warnings)


def test_discovery_skips_symlinked_entry_file_with_warning(tmp_path: Path) -> None:
    plugin_path = _write_plugin(tmp_path, "example.symlink", write_entry=False)
    entry_path = plugin_path / "webui" / "dist" / "index.js"
    outside_path = tmp_path / "outside.js"
    outside_path.write_text("export function mount() {}", encoding="utf-8")
    try:
        entry_path.symlink_to(outside_path)
    except OSError as exc:
        pytest.skip(f"当前环境不支持符号链接: {exc}")

    pages, warnings = discover_plugin_pages([plugin_path], {"example.symlink"})

    assert pages == []
    assert any("符号链接" in warning for warning in warnings)


def test_discovery_skips_broken_page_and_keeps_valid_pages(tmp_path: Path) -> None:
    bad_plugin = _write_plugin(tmp_path, "example.bad", write_entry=False)
    good_plugin = _write_plugin(tmp_path, "example.good")

    pages, warnings = discover_plugin_pages([bad_plugin, good_plugin], {"example.bad", "example.good"})

    assert [page.plugin_id for page in pages] == ["example.good"]
    assert any("入口文件不存在" in warning for warning in warnings)


def test_discovery_uses_manifest_route_for_host_route(tmp_path: Path) -> None:
    plugin_path = _write_plugin(tmp_path, "example.custom", route="custom-slug")

    pages, _warnings = discover_plugin_pages([plugin_path], {"example.custom"})

    assert len(pages) == 1
    assert pages[0].page_id == "hello"
    assert pages[0].route == "/plugin-pages/example.custom/custom-slug"
    assert pages[0].entry_url.endswith(
        "/api/webui/plugins/example.custom/assets/webui/dist/index.js?v=1.0.0"
    )
    assert pages[0].api_base == "/api/webui/plugins/example.custom/pages/hello/api"


def test_discovery_rejects_dist_symlink_escaping_plugin_root(tmp_path: Path) -> None:
    plugin_path = _write_plugin(tmp_path, "example.distlink", write_entry=False)
    outside_dist = tmp_path / "outside_dist"
    outside_dist.mkdir()
    (outside_dist / "index.js").write_text("export function mount() {}", encoding="utf-8")
    dist_path = plugin_path / "webui" / "dist"
    dist_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        dist_path.symlink_to(outside_dist, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前环境不支持符号链接: {exc}")

    pages, warnings = discover_plugin_pages([plugin_path], {"example.distlink"})

    assert pages == []
    assert any("webui/dist 目录超出插件根目录" in warning for warning in warnings)


@pytest.fixture
def page_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    plugin_path = _write_plugin(tmp_path, "example.first")
    pages, _warnings = discover_plugin_pages([plugin_path], {"example.first"})
    monkeypatch.setattr(pages_module, "discover_runtime_plugin_pages", lambda: (pages, []))

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


def test_asset_route_rejects_intermediate_symlink_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin_path = _write_plugin(tmp_path, "example.symlinkdir")
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "leak.js").write_text("export function mount() {}", encoding="utf-8")
    sub_path = plugin_path / "webui" / "dist" / "sub"
    try:
        sub_path.symlink_to(outside_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前环境不支持符号链接: {exc}")

    pages, _warnings = discover_plugin_pages([plugin_path], {"example.symlinkdir"})
    monkeypatch.setattr(pages_module, "discover_runtime_plugin_pages", lambda: (pages, []))

    app = FastAPI()
    app.include_router(pages_module.router, prefix="/api/webui/plugins")
    app.dependency_overrides[require_auth] = lambda: "test-token"

    response = TestClient(app).get(
        "/api/webui/plugins/example.symlinkdir/assets/webui/dist/sub/leak.js"
    )

    assert response.status_code == 400


def test_page_list_uses_loaded_runtime_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_path = _write_plugin(tmp_path, "example.runtime")

    class FakeRuntimeManager:
        def get_loaded_plugin_paths(self) -> list[tuple[str, Path]]:
            return [("example.runtime", plugin_path)]

    monkeypatch.setattr(pages_module, "get_plugin_runtime_manager", lambda: FakeRuntimeManager())
    app = FastAPI()
    app.include_router(pages_module.router, prefix="/api/webui/plugins")
    app.dependency_overrides[require_auth] = lambda: "test-token"

    response = TestClient(app).get("/api/webui/plugins/pages")

    assert response.status_code == 200
    assert [page["plugin_id"] for page in response.json()["pages"]] == ["example.runtime"]
