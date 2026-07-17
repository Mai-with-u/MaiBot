"""实验性动作票据式网页浏览测试。"""

from playwright.async_api import Error as PlaywrightError, async_playwright
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import asyncio
import json

import pytest

from src.config.config import Config
from src.config.official_configs import ExperimentalConfig
from src.maisaka.browser_tool.service import (
    _PAGE_OBSERVATION_SCRIPT,
    BrowserActionError,
    BrowserActionManager,
    BrowserActionSettings,
    BrowserImageContent,
    BrowserRecoverableActionError,
    BrowserScreenshot,
)
from src.maisaka.browser_tool.provider import BrowserActionToolProvider, _build_browser_tool_specs
from src.services import html_render_service as html_render_service_module
from src.services.html_render_service import HTMLRenderService
from src.webui.config_schema import ConfigSchemaGenerator


class FakeElementHandle:
    """记录测试动作的最小元素句柄。"""

    def __init__(
        self,
        click_error: Optional[Exception] = None,
        image_bytes: bytes = b"",
        page: Optional["FakePage"] = None,
        popup_url: str = "",
    ) -> None:
        self.click_count = 0
        self.click_error = click_error
        self.disposed = False
        self.filled_value: Optional[str] = None
        self.image_bytes = image_bytes
        self.image_screenshot_count = 0
        self.page = page
        self.popup_url = popup_url
        self.selected_value: Optional[str] = None

    async def click(self, *, timeout: int) -> None:
        del timeout
        self.click_count += 1
        if self.click_error is not None:
            raise self.click_error
        if self.page is not None and self.page.context is not None and self.popup_url:
            async def open_popup() -> None:
                await asyncio.sleep(0.05)
                popup_page = FakePage([])
                popup_page.context = self.page.context
                popup_page.url = self.popup_url
                self.page.context.pages.append(popup_page)

            asyncio.create_task(open_popup())

    async def fill(self, value: str, *, timeout: int) -> None:
        del timeout
        self.filled_value = value

    async def select_option(self, *, value: str, timeout: int) -> None:
        del timeout
        self.selected_value = value

    async def screenshot(self, *, animations: str, type: str) -> bytes:
        assert animations == "disabled"
        assert type == "png"
        self.image_screenshot_count += 1
        return self.image_bytes

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

    def __init__(
        self,
        element_descriptors: List[Dict[str, Any]],
        image_descriptors: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self._element_descriptors = element_descriptors
        self._image_descriptors = image_descriptors or []
        self.context: Optional[FakeContext] = None
        self.url = "https://example.com/"
        self.closed = False
        self.created_handles: List[FakeElementHandle] = []
        self.handles_by_marker: Dict[str, FakeElementHandle] = {}
        self.readiness_wait_count = 0
        self.screenshot_count = 0
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
                handle = FakeElementHandle(
                    click_error=descriptor.get("_click_error"),
                    page=self,
                    popup_url=str(descriptor.get("_popup_url") or ""),
                )
                self.created_handles.append(handle)
                self.handles_by_marker[marker] = handle
                elements.append(
                    {
                        "marker": marker,
                        **{key: value for key, value in descriptor.items() if not key.startswith("_")},
                    }
                )
            images: List[Dict[str, Any]] = []
            for index, descriptor in enumerate(self._image_descriptors):
                marker = f"{marker_prefix}-image-{index}"
                handle = FakeElementHandle(image_bytes=descriptor.get("_image_bytes", b"fake-image"))
                self.created_handles.append(handle)
                self.handles_by_marker[marker] = handle
                images.append(
                    {
                        "marker": marker,
                        **{key: value for key, value in descriptor.items() if not key.startswith("_")},
                    }
                )
            return {
                "elements": elements,
                "historyLength": 1,
                "images": images,
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

    async def wait_for_function(self, script: str, *, polling: int, timeout: int) -> None:
        assert "document.readyState" in script
        assert polling == 100
        assert timeout == 3000
        self.readiness_wait_count += 1

    async def screenshot(self, *, animations: str, full_page: bool, type: str) -> bytes:
        assert animations == "disabled"
        assert full_page is False
        assert type == "png"
        self.screenshot_count += 1
        return b"fake-png"

    async def title(self) -> str:
        return "示例页面"

    async def go_back(self, *, timeout: int, wait_until: str) -> None:
        del timeout, wait_until

    async def close(self) -> None:
        self.closed = True


class FakeCDPSession:
    """记录响应阶段重定向守卫的 CDP 调用。"""

    def __init__(self) -> None:
        self.handlers: Dict[str, Any] = {}
        self.sent_commands: List[Dict[str, Any]] = []

    def on(self, event_name: str, handler: Any) -> None:
        self.handlers[event_name] = handler

    async def send(self, method: str, params: Dict[str, Any]) -> None:
        self.sent_commands.append({"method": method, "params": params})


class FakeContext:
    """隔离浏览器上下文替身。"""

    def __init__(self, page: FakePage) -> None:
        self._page = page
        self._page.context = self
        self.pages = [page]
        self.closed = False
        self.cdp_session = FakeCDPSession()
        self.route_handler: Any = None

    async def route(self, pattern: str, handler: Any) -> None:
        del pattern
        self.route_handler = handler

    async def new_page(self) -> FakePage:
        return self._page

    async def new_cdp_session(self, page: FakePage) -> FakeCDPSession:
        assert page in self.pages
        return self.cdp_session

    async def close(self) -> None:
        self.closed = True


class FakeBrowserRuntime:
    """不启动真实浏览器的 BrowserRuntime。"""

    def __init__(
        self,
        element_descriptors: List[Dict[str, Any]],
        image_descriptors: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.page = FakePage(element_descriptors, image_descriptors)
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


class RecordingNetworkUrlManager(BrowserActionManager):
    """记录重定向守卫准备放行的网络 URL。"""

    def __init__(self, browser_runtime: FakeBrowserRuntime) -> None:
        super().__init__(browser_runtime)
        self.validated_network_urls: List[str] = []

    async def _validate_network_url(self, url: str) -> None:
        self.validated_network_urls.append(url)


def _settings() -> BrowserActionSettings:
    return BrowserActionSettings(
        session_timeout_seconds=300,
        navigation_timeout_seconds=30,
        max_page_text_length=6000,
        max_actions=20,
    )


@pytest.mark.asyncio
async def test_page_observation_keeps_actions_from_different_components() -> None:
    """大量重复卡片不能挤掉页面其他组件提供的交互能力。"""

    async with async_playwright() as playwright:
        launch_options: Dict[str, Any] = {"headless": True}
        executable_path = next(
            (path for path in HTMLRenderService._get_candidate_executable_paths() if path.is_file()),
            None,
        )
        if executable_path is not None:
            launch_options["executable_path"] = str(executable_path)
        try:
            browser = await playwright.chromium.launch(**launch_options)
        except PlaywrightError as exc:
            pytest.skip(f"当前环境没有可用的 Chromium 浏览器：{exc}")

        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            cards = "".join(
                f"""
                <article class="video-card">
                    <a href="https://example.com/video/{index}">视频 {index}</a>
                    <a href="https://example.com/video/{index}">视频 {index} 标题</a>
                    <a href="https://example.com/author/{index}">作者 {index}</a>
                </article>
                """
                for index in range(24)
            )
            await page.set_content(
                """
                <header>
                    <form>
                        <input type="search" placeholder="搜索内容">
                        <button type="submit">搜索</button>
                    </form>
                    <a href="https://example.com/hot">站外热点</a>
                    <a href="https://example.com/archive.zip" download>下载归档</a>
                </header>
                """
                """
                <main>
                    <figure>
                        <img
                            alt="明日方舟角色 COS"
                            width="240"
                            height="160"
                            src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='240' height='160'%3E%3Crect width='240' height='160' fill='red'/%3E%3C/svg%3E"
                        >
                    </figure>
                """
                f'{cards}<button type="button">换一换</button></main>',
            )

            state = await page.evaluate(
                _PAGE_OBSERVATION_SCRIPT,
                {
                    "markerAttribute": "data-maibot-browser-action",
                    "markerPrefix": "test-action",
                    "maxActions": 17,
                    "maxImages": 6,
                    "maxTextLength": 2000,
                },
            )
        finally:
            await browser.close()

    actions = state["elements"]
    search_action_index = next(
        index
        for index, action in enumerate(actions)
        if action["kind"] == "fill" and action["label"] == "搜索内容"
    )
    disclosed_targets = [action["href"] for action in actions if action["href"]]

    assert search_action_index < 5
    assert len(disclosed_targets) == len(set(disclosed_targets))
    assert not any(action["label"].endswith("标题") for action in actions)
    assert any(
        action["kind"] == "open" and action["label"] == "站外热点"
        for action in actions
    )
    assert not any(action["label"] == "下载归档" for action in actions)
    assert state["images"] == [
        {
            "height": 160,
            "label": "明日方舟角色 COS",
            "marker": "test-action-image-0",
            "width": 240,
        }
    ]


def test_search_results_are_deduplicated_and_limited() -> None:
    raw_results = [
        {
            "key": f"https://example.com/result-{index}",
            "snippet": f"第 {index} 条结果摘要",
            "source": "example.com",
            "title": f"第 {index} 条结果",
        }
        for index in range(1, 7)
    ]
    raw_results.insert(1, dict(raw_results[0]))

    page_text, truncated = BrowserActionManager._format_search_results(raw_results, 6000)

    assert page_text.count("来源：example.com") == 5
    assert page_text.count("1. 第 1 条结果") == 1
    assert "第 5 条结果" in page_text
    assert "第 6 条结果" not in page_text
    assert not truncated


@pytest.mark.asyncio
async def test_page_settle_waits_for_meaningful_dynamic_content() -> None:
    page = FakePage([])

    await BrowserActionManager._settle_page(page)

    assert page.readiness_wait_count == 1


@pytest.mark.asyncio
async def test_redirect_guard_blocks_localhost_before_browser_follows() -> None:
    runtime = FakeBrowserRuntime([])
    manager = BrowserActionManager(runtime)
    cdp_session = runtime.context.cdp_session

    await manager._handle_redirect_response(
        cdp_session,
        {
            "requestId": "redirect-request",
            "request": {"url": "https://public.example/redirect"},
            "responseStatusCode": 302,
            "responseHeaders": [{"name": "Location", "value": "http://localhost:8000"}],
        },
    )

    assert cdp_session.sent_commands == [
        {
            "method": "Fetch.failRequest",
            "params": {"requestId": "redirect-request", "errorReason": "BlockedByClient"},
        }
    ]

    await manager.shutdown()


@pytest.mark.asyncio
async def test_redirect_guard_validates_relative_location_before_continuing() -> None:
    runtime = FakeBrowserRuntime([])
    manager = RecordingNetworkUrlManager(runtime)
    cdp_session = runtime.context.cdp_session

    await manager._handle_redirect_response(
        cdp_session,
        {
            "requestId": "redirect-request",
            "request": {"url": "https://public.example/path/redirect"},
            "responseStatusCode": 307,
            "responseHeaders": [{"name": "location", "value": "../safe-target"}],
        },
    )

    assert manager.validated_network_urls == ["https://public.example/safe-target"]
    assert cdp_session.sent_commands == [
        {
            "method": "Fetch.continueRequest",
            "params": {"requestId": "redirect-request"},
        }
    ]

    await manager.shutdown()


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
    assert runtime.context.cdp_session.sent_commands[0] == {
        "method": "Fetch.enable",
        "params": {"patterns": [{"urlPattern": "*", "requestStage": "Response"}]},
    }

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
async def test_click_adopts_page_opened_asynchronously() -> None:
    runtime = FakeBrowserRuntime(
        [
            {
                "_popup_url": "https://example.com/popup",
                "href": "",
                "kind": "click",
                "label": "打开详情",
                "options": [],
                "role": "button",
                "tag": "button",
                "type": "button",
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

    next_manifest = await manager.step(
        action_id=manifest["actions"][0]["action_id"],
        browser_session_id=manifest["browser_session_id"],
        owner_id="owner-1",
        page_version=1,
        scope_key="qq:real-session-id",
    )

    assert next_manifest["page"]["url"] == "https://example.com/popup"
    assert next_manifest["actions"][0]["kind"] == "previous_tab"

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


def test_browser_start_schema_requires_url_without_search_shortcut() -> None:
    tool_specs = _build_browser_tool_specs()
    browser_start = next(spec for spec in tool_specs if spec.name == "browser_start")

    assert "goal" not in browser_start.parameters_schema["properties"]
    assert browser_start.parameters_schema["required"] == ["url"]
    assert set(browser_start.parameters_schema["properties"]) == {"url"}
    assert all(spec.name != "browser_search" for spec in tool_specs)
    assert "所有浏览都必须从 URL 开始" in browser_start.description


def test_browser_screenshot_schema_is_explicit_and_read_only() -> None:
    browser_screenshot = next(
        spec for spec in _build_browser_tool_specs() if spec.name == "browser_screenshot"
    )

    assert browser_screenshot.parameters_schema["required"] == [
        "browser_session_id",
        "page_version",
    ]
    assert set(browser_screenshot.parameters_schema["properties"]) == {
        "browser_session_id",
        "page_version",
    }
    assert "普通阅读和操作不要截图" in browser_screenshot.description


def test_browser_get_image_schema_requires_current_image_ticket() -> None:
    browser_get_image = next(
        spec for spec in _build_browser_tool_specs() if spec.name == "browser_get_image"
    )

    assert browser_get_image.parameters_schema["required"] == [
        "browser_session_id",
        "page_version",
        "image_id",
    ]
    assert "不接受任意 URL" in browser_get_image.description
    assert "reply.attach_pic" in browser_get_image.description


@pytest.mark.asyncio
async def test_browser_get_image_returns_disclosed_image_without_mutating_page() -> None:
    runtime = FakeBrowserRuntime(
        [],
        [
            {
                "_image_bytes": b"coser-image",
                "height": 480,
                "label": "明日方舟角色 COS",
                "width": 320,
            }
        ],
    )
    manager = PublicUrlTestManager(runtime)
    manifest = await manager.start(
        owner_id="owner-1",
        scope_key="qq:real-session-id",
        settings=_settings(),
        url="https://example.com/",
    )
    image_ticket = manifest["images"][0]

    image = await manager.get_image(
        browser_session_id=manifest["browser_session_id"],
        image_id=image_ticket["image_id"],
        owner_id="owner-1",
        page_version=manifest["page_version"],
        scope_key="qq:real-session-id",
    )

    assert image.image_bytes == b"coser-image"
    assert image.label == "明日方舟角色 COS"
    session = manager._sessions_by_id[manifest["browser_session_id"]]
    assert session.page_version == manifest["page_version"]
    assert image_ticket["image_id"] in session.images

    with pytest.raises(BrowserActionError, match="图片票据不存在或已经失效"):
        await manager.get_image(
            browser_session_id=manifest["browser_session_id"],
            image_id="img_not-disclosed",
            owner_id="owner-1",
            page_version=manifest["page_version"],
            scope_key="qq:real-session-id",
        )

    await manager.shutdown()


def test_browser_get_image_result_keeps_base64_out_of_text_content() -> None:
    result = BrowserActionToolProvider._image_success(
        "browser_get_image",
        BrowserImageContent(
            browser_session_id="browser-test",
            image_bytes=b"coser-image",
            image_id="img-test",
            label="明日方舟角色 COS",
            page_version=2,
            url="https://example.com/gallery",
        ),
    )

    assert result.success
    assert "Y29zZXItaW1hZ2U=" not in result.content
    assert result.content_items[0].data == "Y29zZXItaW1hZ2U="
    assert result.structured_content["image_id"] == "img-test"
    assert result.content_items[0].metadata["source_url"] == "https://example.com/gallery"


@pytest.mark.asyncio
async def test_browser_screenshot_preserves_page_version_and_action_tickets() -> None:
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

    screenshot = await manager.screenshot(
        browser_session_id=manifest["browser_session_id"],
        owner_id="owner-1",
        page_version=manifest["page_version"],
        scope_key="qq:real-session-id",
    )

    assert screenshot.image_bytes == b"fake-png"
    assert screenshot.page_version == manifest["page_version"]
    assert runtime.page.screenshot_count == 1
    session = manager._sessions_by_id[manifest["browser_session_id"]]
    assert session.page_version == manifest["page_version"]
    assert set(session.actions) == {action["action_id"] for action in manifest["actions"]}

    await manager.shutdown()


@pytest.mark.asyncio
async def test_browser_screenshot_rejects_stale_page_version() -> None:
    runtime = FakeBrowserRuntime([])
    manager = PublicUrlTestManager(runtime)
    manifest = await manager.start(
        owner_id="owner-1",
        scope_key="qq:real-session-id",
        settings=_settings(),
        url="https://example.com/",
    )

    with pytest.raises(BrowserActionError, match="页面版本已过期"):
        await manager.screenshot(
            browser_session_id=manifest["browser_session_id"],
            owner_id="owner-1",
            page_version=manifest["page_version"] + 1,
            scope_key="qq:real-session-id",
        )

    assert runtime.page.screenshot_count == 0
    await manager.shutdown()


def test_browser_screenshot_result_keeps_base64_out_of_text_content() -> None:
    result = BrowserActionToolProvider._screenshot_success(
        "browser_screenshot",
        BrowserScreenshot(
            browser_session_id="browser-test",
            image_bytes=b"fake-png",
            page_version=3,
            title="示例页面",
            url="https://example.com/",
        ),
    )

    assert result.success
    assert "ZmFrZS1wbmc=" not in result.content
    assert result.content_items[0].content_type == "image"
    assert result.content_items[0].data == "ZmFrZS1wbmc="
    assert result.content_items[0].metadata["source_url"] == "https://example.com/"


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
        ],
        [
            {
                "_image_bytes": b"old-page-image",
                "height": 320,
                "label": "旧页面图片",
                "width": 240,
            }
        ],
    )
    manager = PublicUrlTestManager(runtime)
    manifest = await manager.start(
        owner_id="owner-1",
        scope_key="qq:real-session-id",
        settings=_settings(),
        url="https://example.com/",
    )
    action_id = manifest["actions"][0]["action_id"]
    image_id = manifest["images"][0]["image_id"]

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
    assert runtime.page.created_handles[1].disposed is True
    assert next_manifest["images"][0]["image_id"] != image_id
    with pytest.raises(BrowserActionError, match="图片票据不存在或已经失效"):
        await manager.get_image(
            browser_session_id=manifest["browser_session_id"],
            image_id=image_id,
            owner_id="owner-1",
            page_version=next_manifest["page_version"],
            scope_key="qq:real-session-id",
        )
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
