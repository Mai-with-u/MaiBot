from typing import Any, Dict

import pytest

from src.plugin_runtime.runner.manifest_validator import ManifestValidator


def _build_manifest(**overrides: Any) -> Dict[str, Any]:
    """构造包含现有必填字段的合法 Manifest v2。"""
    manifest: Dict[str, Any] = {
        "manifest_version": 2,
        "version": "1.0.0",
        "name": "WebUI 页面测试插件",
        "description": "用于测试 WebUI 页面声明",
        "author": {"name": "MaiBot", "url": "https://example.com"},
        "license": "GPL-v3.0-or-later",
        "urls": {"repository": "https://example.com/repository"},
        "host_application": {"min_version": "1.0.0", "max_version": "9.99.99"},
        "sdk": {"min_version": "2.0.0", "max_version": "2.99.99"},
        "dependencies": [],
        "capabilities": [],
        "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
        "id": "maibot-team.webui-page-test",
    }
    manifest.update(overrides)
    return manifest


def _valid_page(**overrides: Any) -> Dict[str, Any]:
    page: Dict[str, Any] = {
        "id": "hello",
        "title": "Hello World",
        "route": "hello",
        "entry": "webui/dist/index.js",
        "component": "mount",
        "permissions": ["webui.page:view"],
        "api": {"get_status": "webui.hello.get_status"},
    }
    page.update(overrides)
    return page


def test_manifest_without_webui_extensions_remains_valid() -> None:
    validator = ManifestValidator(validate_python_package_dependencies=False)

    parsed_manifest = validator.parse_manifest(_build_manifest())

    assert parsed_manifest is not None
    assert parsed_manifest.extensions is None


def test_manifest_parses_webui_page_declaration() -> None:
    validator = ManifestValidator(validate_python_package_dependencies=False)
    manifest = _build_manifest(extensions={"webui_pages": [_valid_page()]})

    parsed_manifest = validator.parse_manifest(manifest)

    assert parsed_manifest is not None
    assert parsed_manifest.extensions is not None
    assert parsed_manifest.extensions.webui_pages[0].entry == "webui/dist/index.js"
    assert parsed_manifest.extensions.webui_pages[0].api["get_status"] == "webui.hello.get_status"


@pytest.mark.parametrize("route", ["../hello", "/hello", "hello/world", "hello\\world"])
def test_manifest_rejects_unsafe_page_route(route: str) -> None:
    validator = ManifestValidator(validate_python_package_dependencies=False)
    manifest = _build_manifest(extensions={"webui_pages": [_valid_page(route=route)]})

    assert validator.parse_manifest(manifest) is None
    assert any("route" in error for error in validator.errors)


@pytest.mark.parametrize(
    "entry",
    ["../webui/dist/index.js", "/webui/dist/index.js", "webui\\dist\\index.js", "webui/dist/index.css"],
)
def test_manifest_rejects_unsafe_page_entry(entry: str) -> None:
    validator = ManifestValidator(validate_python_package_dependencies=False)
    manifest = _build_manifest(extensions={"webui_pages": [_valid_page(entry=entry)]})

    assert validator.parse_manifest(manifest) is None
    assert any("entry" in error for error in validator.errors)


def test_manifest_rejects_duplicate_page_ids() -> None:
    validator = ManifestValidator(validate_python_package_dependencies=False)
    page = _valid_page()
    manifest = _build_manifest(extensions={"webui_pages": [page, page.copy()]})

    assert validator.parse_manifest(manifest) is None
    assert any("重复" in error for error in validator.errors)


def test_manifest_rejects_unknown_webui_page_field() -> None:
    validator = ManifestValidator(validate_python_package_dependencies=False)
    manifest = _build_manifest(
        extensions={"webui_pages": [_valid_page(unknown_field="not-allowed")]}
    )

    assert validator.parse_manifest(manifest) is None
    assert any("unknown_field" in error or "额外" in error for error in validator.errors)
