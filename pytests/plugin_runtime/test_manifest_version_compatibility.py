from typing import Any, Dict

from src.plugin_runtime.update_compatibility_notice import _is_host_compatible
from src.plugin_runtime.runner.manifest_validator import ManifestValidator, VersionComparator


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


def _build_validator(host_version: str) -> ManifestValidator:
    return ManifestValidator(
        host_version=host_version,
        sdk_version="2.0.0",
        validate_python_package_dependencies=False,
        log_errors=False,
        log_compat_warnings=False,
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
