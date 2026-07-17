"""实验性动作票据式网页浏览测试。"""

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import asyncio
import json

import pytest

from src.config.config import Config
from src.config.official_configs import ExperimentalConfig
from src.maisaka.browser_tool.service import (
    BrowserActionError,
    BrowserActionManager,
    BrowserActionSettings,
    BrowserRecoverableActionError,
)
from src.maisaka.browser_tool.provider import _build_browser_tool_specs
from src.services import html_render_service as html_render_service_module
from src.services.html_render_service import HTMLRenderService
from src.webui.config_schema import ConfigSchemaGenerator


class FakeElementHandle:
    """记录测试动作的最小元素句柄。"""

    def __init__(self, click_error: Optional[Exception] = None) -> None:
        self.click_count = 0
        self.click_error = click_error
        self.disposed = False
        self.filled_value: Optional[str] = None
        self.selected_value: Optional[str] = None

    async def click(self, *, timeout: int) -> None:
        del timeout
        self.click_count += 1
        if self.click_error is not None:
            raise self.click_error

    async def fill(self, value: str, *, timeout: int) -> None:
        del timeout
        self.filled_value = value

    async def select_option(self, *, value: str, timeout: int) -> None:
        del timeout
        self.selected_value = value

    async def dispose(self) -> None:
        self.disposed = True


class FakeLocator:
    """返回绑定指定 marker 的元素句柄。"""

    def __init__(self, page: "FakePage", marker: str) -> None:
        self._page = page
        self._marker = marker

    async def element_handle(self) -> Optional[FakeElementHandle]:
        return self._page.handles_by_marker.get(self._marker)


class FakePage:
    """提供页面观察脚本所需的最小 Playwright Page 行为。"""

    def __init__(self, element_descriptors: List[Dict[str, Any]]) -> None:
        self._element_descriptors = element_descriptors
        self.url = "https://example.com/"
        self.closed = False
        self.created_handles: List[FakeElementHandle] = []
        self.handles_by_marker: Dict[str, FakeElementHandle] = {}
        self.scroll_y = 0

    def set_default_timeout(self, timeout_ms: int) -> None:
        self.timeout_ms = timeout_ms

    async def goto(self, url: str, *, timeout: int, wait_until: str) -> None:
        del timeout, wait_until
        self.url = url

    async def evaluate(self, script: str, argument: Any) -> Any:
        if isinstance(argument, dict):
            marker_prefix = argument["markerPrefix"]
            elements: List[Dict[str, Any]] = []
            self.handles_by_marker = {}
            for index, descriptor in enumerate(self._element_descriptors):
                marker = f"{marker_prefix}-{index}"
                handle = FakeElementHandle(click_error=descriptor.get("_click_error"))
                self.created_handles.append(handle)
                self.handles_by_marker[marker] = handle
                elements.append(
                    {
                        "marker": marker,
                        **{key: value for key, value in descriptor.items() if not key.startswith("_")},
                    }
                )
            return {
                "elements": elements,
                "historyLength": 1,
                "pageText": "这是页面正文。",
                "pageTextTruncated": False,
                "scrollHeight": 720,
                "scrollY": self.scroll_y,
                "title": "示例页面",
                "url": self.url,
                "viewportHeight": 720,
            }
        if "scrollBy" in script:
            self.scroll_y += int(argument)
        return None

    def locator(self, selector: str) -> FakeLocator:
        marker = selector.split('="', 1)[1].rsplit('"]', 1)[0]
        return FakeLocator(self, marker)

    async def wait_for_load_state(self, state: str, *, timeout: int) -> None:
        del state, timeout

    async def wait_for_timeout(self, timeout_ms: int) -> None:
        del timeout_ms

    async def go_back(self, *, timeout: int, wait_until: str) -> None:
        del timeout, wait_until

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    """隔离浏览器上下文替身。"""

    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.pages = [page]
        self.closed = False
        self.route_handler: Any = None

    async def route(self, pattern: str, handler: Any) -> None:
        del pattern
        self.route_handler = handler

    async def new_page(self) -> FakePage:
        return self._page

    async def close(self) -> None:
        self.closed = True


class FakeBrowserRuntime:
    """不启动真实浏览器的 BrowserRuntime。"""

    def __init__(self, element_descriptors: List[Dict[str, Any]]) -> None:
        self.page = FakePage(element_descriptors)
        self.context = FakeContext(self.page)
        self.create_count = 0
        self.reset_count = 0

    async def create_browser_context(
        self,
        *,
        accept_downloads: bool = False,
        locale: str = "zh-CN",
        service_workers: str = "allow",
        viewport_height: int = 720,
        viewport_width: int = 1280,
    ) -> FakeContext:
        del accept_downloads, locale, service_workers, viewport_height, viewport_width
        self.create_count += 1
        return self.context

    async def reset_browser(self, restart_playwright: bool = False) -> None:
        del restart_playwright
        self.reset_count += 1


class FakeDisconnectingBrowser:
    """关闭时同步触发 disconnected 回调的浏览器替身。"""

    def __init__(self, service: HTMLRenderService) -> None:
        self._service = service

    async def close(self) -> None:
        self._service._handle_browser_disconnected(self)


class FakeLogger:
    """记录浏览器关闭路径使用的日志级别。"""

    def __init__(self) -> None:
        self.debug_messages: List[str] = []
        self.warning_messages: List[str] = []

    def debug(self, message: str) -> None:
        self.debug_messages.append(message)

    def warning(self, message: str) -> None:
        self.warning_messages.append(message)


class PublicUrlTestManager(BrowserActionManager):
    """在纯单元测试中跳过公网 DNS 解析。"""

    async def _validate_top_level_url(self, url: str) -> None:
        assert url.startswith("https://")


class CountingPublicHostManager(BrowserActionManager):
    """统计同一公网主机实际执行的解析次数。"""

    def __init__(self, browser_runtime: FakeBrowserRuntime) -> None:
        super().__init__(browser_runtime)
        self.resolve_count = 0

    async def _resolve_and_validate_public_host(self, hostname: str, port: int) -> None:
        del hostname, port
        self.resolve_count += 1
        await asyncio.sleep(0.01)


def _settings() -> BrowserActionSettings:
    return BrowserActionSettings(
        session_timeout_seconds=300,
        navigation_timeout_seconds=30,
        max_page_text_length=6000,
        max_actions=20,
    )


@pytest.mark.asyncio
async def test_public_host_validation_is_coalesced_and_cached() -> None:
    manager = CountingPublicHostManager(FakeBrowserRuntime([]))
    parsed_url = urlparse("https://example.com/assets/app.js")

    await asyncio.gather(*(manager._validate_public_host(parsed_url) for _ in range(8)))
    await manager._validate_public_host(parsed_url)

    assert manager.resolve_count == 1

    await manager.shutdown()


@pytest.mark.asyncio
async def test_browser_start_discloses_action_tickets_without_selectors() -> None:
    runtime = FakeBrowserRuntime(
        [
            {
                "href": "",
                "kind": "fill",
                "label": "搜索框",
                "options": [],
                "role": "textbox",
                "tag": "input",
                "type": "search",
            },
            {
                "href": "https://example.com/docs",
                "kind": "click",
                "label": "打开文档",
                "options": [],
                "role": "link",
                "tag": "a",
                "type": "",
            },
        ]
    )
    manager = PublicUrlTestManager(runtime)

    manifest = await manager.start(
        owner_id="owner-1",
        scope_key="qq:real-session-id",
        settings=_settings(),
        url="https://example.com/",
    )

    assert manifest["page_version"] == 1
    assert {action["kind"] for action in manifest["actions"]} == {"click", "fill"}
    assert "goal" not in manifest
    assert "text_truncated" not in manifest["page"]
    assert all("available" not in action for action in manifest["actions"])
    assert all("risk" not in action for action in manifest["actions"])
    assert all("input_schema" not in action for action in manifest["actions"])
    serialized_manifest = json.dumps(manifest, ensure_ascii=False)
    assert "data-maibot-browser-action" not in serialized_manifest
    assert "selector" not in serialized_manifest
    assert "搜索框" in serialized_manifest

    await manager.shutdown()


@pytest.mark.asyncio
async def test_browser_start_search_query_uses_international_results() -> None:
    runtime = FakeBrowserRuntime([])
    manager = PublicUrlTestManager(runtime)

    await manager.start(
        owner_id="owner-1",
        scope_key="qq:real-session-id",
        settings=_settings(),
        search_query="MaiBot GitHub",
    )

    assert runtime.page.url == "https://www.bing.com/search?q=MaiBot+GitHub&ensearch=1"

    await manager.shutdown()


@pytest.mark.asyncio
async def test_semantic_link_action_opens_url_without_element_click() -> None:
    runtime = FakeBrowserRuntime(
        [
            {
                "href": "https://example.com/docs",
                "kind": "open",
                "label": "阅读文档",
                "options": [],
                "role": "link",
                "tag": "a",
                "type": "",
            }
        ]
    )
    manager = PublicUrlTestManager(runtime)
    manifest = await manager.start(
        owner_id="owner-1",
        scope_key="qq:real-session-id",
        settings=_settings(),
        url="https://example.com/",
    )

    assert manifest["actions"][0]["kind"] == "open"
    await manager.step(
        action_id=manifest["actions"][0]["action_id"],
        browser_session_id=manifest["browser_session_id"],
        owner_id="owner-1",
        page_version=1,
        scope_key="qq:real-session-id",
    )

    assert runtime.page.url == "https://example.com/docs"
    assert runtime.page.created_handles[0].click_count == 0

    await manager.shutdown()


@pytest.mark.asyncio
async def test_browser_step_failure_refreshes_tickets_without_closing_session() -> None:
    runtime = FakeBrowserRuntime(
        [
            {
                "_click_error": RuntimeError("element is covered\nlong browser call log"),
                "href": "https://example.com/docs",
                "kind": "click",
                "label": "打开文档",
                "options": [],
                "role": "link",
                "tag": "a",
                "type": "",
            }
        ]
    )
    manager = PublicUrlTestManager(runtime)
    manifest = await manager.start(
        owner_id="owner-1",
        scope_key="qq:real-session-id",
        settings=_settings(),
        url="https://example.com/",
    )

    with pytest.raises(BrowserRecoverableActionError) as exc_info:
        await manager.step(
            action_id=manifest["actions"][0]["action_id"],
            browser_session_id=manifest["browser_session_id"],
            owner_id="owner-1",
            page_version=1,
            scope_key="qq:real-session-id",
        )

    recovery_manifest = exc_info.value.manifest
    assert recovery_manifest["browser_session_id"] == manifest["browser_session_id"]
    assert recovery_manifest["page_version"] == 2
    assert recovery_manifest["action_error"] == {
        "code": "action_failed",
        "message": "动作“打开文档”执行失败，页面状态和动作票据已刷新。",
        "retryable": True,
    }
    assert runtime.context.closed is False
    assert runtime.reset_count == 0

    await manager.shutdown()


def test_browser_start_schema_omits_redundant_goal() -> None:
    browser_start = next(spec for spec in _build_browser_tool_specs() if spec.name == "browser_start")

    assert "goal" not in browser_start.parameters_schema["properties"]
    assert "required" not in browser_start.parameters_schema


@pytest.mark.asyncio
async def test_intentional_browser_release_does_not_log_disconnect_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = HTMLRenderService()
    logger = FakeLogger()
    service._browser = FakeDisconnectingBrowser(service)
    monkeypatch.setattr(html_render_service_module, "logger", logger)

    await service.reset_browser()

    assert logger.debug_messages == ["HTML 渲染浏览器已主动释放"]
    assert logger.warning_messages == []


@pytest.mark.asyncio
async def test_browser_step_invalidates_previous_page_version() -> None:
    runtime = FakeBrowserRuntime(
        [
            {
                "href": "",
                "kind": "fill",
                "label": "搜索框",
                "options": [],
                "role": "textbox",
                "tag": "input",
                "type": "search",
            }
        ]
    )
    manager = PublicUrlTestManager(runtime)
    manifest = await manager.start(
        owner_id="owner-1",
        scope_key="qq:real-session-id",
        settings=_settings(),
        url="https://example.com/",
    )
    action_id = manifest["actions"][0]["action_id"]

    next_manifest = await manager.step(
        action_id=action_id,
        browser_session_id=manifest["browser_session_id"],
        owner_id="owner-1",
        page_version=1,
        scope_key="qq:real-session-id",
        value="MaiBot",
    )

    assert next_manifest["page_version"] == 2
    assert runtime.page.created_handles[0].filled_value == "MaiBot"
    assert runtime.page.created_handles[0].disposed is True
    with pytest.raises(BrowserActionError, match="页面版本已过期"):
        await manager.step(
            action_id=action_id,
            browser_session_id=manifest["browser_session_id"],
            owner_id="owner-1",
            page_version=1,
            scope_key="qq:real-session-id",
            value="再次输入",
        )

    await manager.shutdown()


@pytest.mark.asyncio
async def test_high_risk_page_action_is_disclosed_but_blocked() -> None:
    runtime = FakeBrowserRuntime(
        [
            {
                "formMethod": "post",
                "href": "",
                "kind": "click",
                "label": "保存资料",
                "options": [],
                "role": "button",
                "tag": "button",
                "type": "submit",
            }
        ]
    )
    manager = PublicUrlTestManager(runtime)
    manifest = await manager.start(
        owner_id="owner-1",
        scope_key="qq:real-session-id",
        settings=_settings(),
        url="https://example.com/",
    )
    action = manifest["actions"][0]

    assert action["risk"] == "high"
    assert action["available"] is False
    with pytest.raises(BrowserActionError, match="高风险操作"):
        await manager.step(
            action_id=action["action_id"],
            browser_session_id=manifest["browser_session_id"],
            owner_id="owner-1",
            page_version=1,
            scope_key="qq:real-session-id",
        )
    assert runtime.page.created_handles[0].click_count == 0

    await manager.shutdown()


@pytest.mark.asyncio
async def test_browser_start_blocks_private_network_address() -> None:
    runtime = FakeBrowserRuntime([])
    manager = BrowserActionManager(runtime)

    with pytest.raises(BrowserActionError, match="非公网地址"):
        await manager.start(
            owner_id="owner-1",
            scope_key="qq:real-session-id",
            settings=_settings(),
            url="http://127.0.0.1:7999/",
        )
    assert runtime.create_count == 0

    await manager.shutdown()


def test_experimental_browser_is_disabled_by_default() -> None:
    config = ExperimentalConfig()

    assert config.browser.enabled is False
    assert config.browser.max_actions == 20


def test_experimental_browser_switch_is_exposed_in_config_schema() -> None:
    schema = ConfigSchemaGenerator.generate_schema(Config)
    browser_schema = schema["nested"]["experimental"]["nested"]["browser"]
    enabled_field = next(field for field in browser_schema["fields"] if field["name"] == "enabled")

    assert browser_schema["uiLabel"] == "网页浏览"
    assert enabled_field["label"]["zh_CN"] == "启用网页浏览"
    assert enabled_field["x-widget"] == "switch"
