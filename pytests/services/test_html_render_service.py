from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, List, Optional

import inspect

import pytest

from src.services.html_render_service import HTMLRenderService, HtmlRenderRequest


class _FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = SimpleNamespace(url=url)
        self.actions: List[str] = []

    async def continue_(self) -> None:
        self.actions.append("continue")

    async def abort(self) -> None:
        self.actions.append("abort")


class _FakePage:
    def __init__(self) -> None:
        self.route_handler: Optional[Callable[..., Any]] = None

    def set_default_timeout(self, timeout: int) -> None:
        del timeout

    async def route(self, pattern: str, handler: Callable[..., Any]) -> None:
        del pattern
        self.route_handler = handler

    async def set_content(self, html: str, timeout: int, wait_until: str) -> None:
        del html, timeout, wait_until


async def _call_like_playwright(handler: Callable[..., Any], route: _FakeRoute) -> None:
    """按 Playwright 1.58 的方式调用路由处理器：按参数个数传入 (route, request)。"""

    arg_count = len(inspect.signature(handler).parameters)
    await handler(*[route, route.request][:arg_count])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allow_network,url,expected_action",
    [
        (False, "https://example.com/a.png", "abort"),
        (False, "data:image/png;base64,AAAA", "continue"),
        (True, "https://example.com/a.png", "continue"),
    ],
)
async def test_route_handler_accepts_playwright_arguments(
    allow_network: bool,
    url: str,
    expected_action: str,
) -> None:
    page = _FakePage()
    await HTMLRenderService()._configure_page(page, HtmlRenderRequest(html="<p>hi</p>", allow_network=allow_network))

    assert page.route_handler is not None
    route = _FakeRoute(url)
    await _call_like_playwright(page.route_handler, route)

    assert route.actions == [expected_action]
