"""插件 WebUI API 白名单闭合校验测试。"""

from typing import Any, Dict

from src.plugin_runtime.protocol.envelope import ComponentDeclaration
from src.plugin_runtime.runner.manifest_validator import ManifestValidator
from src.plugin_runtime.runner.runner_main import PluginRunner


def _build_manifest() -> Dict[str, Any]:
    """构造带 WebUI 页面 API 白名单的合法 Manifest。"""

    return {
        "manifest_version": 2,
        "version": "1.0.0",
        "name": "WebUI API 校验插件",
        "description": "用于测试 WebUI API 白名单闭合校验",
        "author": {"name": "MaiBot", "url": "https://example.com"},
        "license": "GPL-v3.0-or-later",
        "urls": {"repository": "https://example.com/repository"},
        "host_application": {"min_version": "1.0.0", "max_version": "9.99.99"},
        "sdk": {"min_version": "2.0.0", "max_version": "2.99.99"},
        "dependencies": [],
        "capabilities": [],
        "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
        "id": "maibot-team.webui-api-check",
        "extensions": {
            "webui_pages": [
                {
                    "id": "hello",
                    "title": "Hello",
                    "route": "hello",
                    "entry": "webui/dist/index.js",
                    "api": {
                        "greet": "webui.hello.greet",
                        "missing": "webui.hello.missing",
                    },
                }
            ]
        },
    }


def test_webui_api_whitelist_gap_logs_warning_without_blocking(caplog: Any) -> None:
    """声明的 WebUI API 不存在时应告警，但不阻止插件继续注册。"""

    manifest = ManifestValidator(validate_python_package_dependencies=False).parse_manifest(_build_manifest())
    assert manifest is not None
    components = [
        ComponentDeclaration(
            name="webui.hello.greet",
            component_type="API",
            plugin_id=manifest.id,
        )
    ]

    with caplog.at_level("WARNING"):
        PluginRunner._warn_webui_api_whitelist_gaps(manifest, components)

    assert "webui.hello.missing" in caplog.text
    assert "webui-api-check" in caplog.text
