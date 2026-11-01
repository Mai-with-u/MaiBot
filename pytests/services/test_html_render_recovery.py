from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncio
import pytest

from src.services.html_render_service import HTMLRenderService, HtmlRenderRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("failures", [1, 2])
async def test_driver_disconnect_retries_once(monkeypatch, failures):
    service = HTMLRenderService()
    config = SimpleNamespace(enabled=True)
    request = SimpleNamespace(device_scale_factor=1, viewport_width=100, viewport_height=100)
    context = SimpleNamespace(new_page=AsyncMock(return_value=object()), close=AsyncMock())
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context))
    monkeypatch.setattr(service, "_get_render_config", lambda: config)
    monkeypatch.setattr(service, "_normalize_request", lambda *args: request)
    monkeypatch.setattr(service, "_get_render_semaphore", lambda: asyncio.Semaphore(1))
    monkeypatch.setattr(service, "_ensure_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr(service, "_configure_page", AsyncMock())
    errors = [RuntimeError("Connection closed while reading from the driver")] * failures
    capture = AsyncMock(side_effect=[*errors, b"png"])
    monkeypatch.setattr(service, "_capture_image", capture)
    monkeypatch.setattr(service, "_measure_image_size", Mock(return_value=(100, 100)))
    monkeypatch.setattr(service, "_maybe_restart_browser", AsyncMock())
    reset = AsyncMock()
    monkeypatch.setattr(service, "_reset_browser_after_render_error", reset)
    if failures == 1:
        result = await service.render_html_to_png(HtmlRenderRequest(html="<p>test</p>"))
        assert (result.width, result.height) == (100, 100)
    else:
        with pytest.raises(RuntimeError, match="Connection closed"):
            await service.render_html_to_png(HtmlRenderRequest(html="<p>test</p>"))
    assert capture.await_count == 2
    assert context.close.await_count == 2
    assert all(call.kwargs["restart_playwright"] for call in reset.await_args_list)


@pytest.mark.asyncio
async def test_stale_error_does_not_close_replacement_browser(monkeypatch):
    service = HTMLRenderService()
    old_browser, old_driver = object(), object()
    service._browser = object()
    service._playwright = object()
    close = AsyncMock()
    monkeypatch.setattr(service, "_close_browser_unlocked", close)
    await service._reset_browser_after_render_error(old_browser, old_driver, restart_playwright=True)
    close.assert_not_awaited()
