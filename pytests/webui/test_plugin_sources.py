from email.message import Message
from io import BytesIO
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.parse import urlsplit
import asyncio
import json

import pytest

from src.webui.services import git_mirror_service as git
from src.webui.routers.plugin import icon_routes
from src.plugin_market import sources as market
from src.plugin_market.cache import MarketCache


@pytest.fixture(autouse=True)
def isolated_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "market_cache", MarketCache())
    monkeypatch.setattr(market, "SETTINGS_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(git, "_validate_custom_outbound_url", lambda value, label: value)
    monkeypatch.setattr("src.common.network_security.validate_public_url", lambda value, **kw: value)


def configure(**values):
    market.SETTINGS_FILE.write_text(json.dumps(values), encoding="utf-8")


@pytest.mark.parametrize("source,host", [("cnb", "cnb.cool"), ("github", "raw.githubusercontent.com")])
@pytest.mark.parametrize("status", [200, 503])
def test_information_source_is_fixed_and_never_uses_download_source(monkeypatch, source, host, status):
    configure(information_source=source, download_source="custom", custom_download_prefix="https://download.example")
    calls = []
    def read(url, *args):
        calls.append(urlsplit(url))
        if status != 200:
            raise HTTPError(url, status, "offline", {}, None)
        return "[]"

    monkeypatch.setattr(market.GitSource, "read_text", read)
    if status == 200:
        result = asyncio.run(market.fetch_information_file("plugin_details.json"))
        assert result["mirror_used"] == source
    else:
        with pytest.raises(HTTPError):
            asyncio.run(market.fetch_information_file("plugin_details.json"))
    assert len(calls) == 1 and calls[0].hostname == host


@pytest.mark.parametrize("information", ["official", "cnb", "github"])
@pytest.mark.parametrize("download,host", [("cnb", "cnb.cool"), ("github", "github.com"), ("custom", "download.example")])
def test_download_uses_only_selected_source_even_on_failure(monkeypatch, tmp_path, information, download, host):
    configure(information_source=information, download_source=download, custom_download_prefix="https://download.example")
    service = git.GitMirrorService()
    calls = []

    async def clone(url, *args, **kwargs):
        calls.append(url)
        return {"success": False, "error": "selected source failed", "mirror_used": download}

    monkeypatch.setattr(service, "_clone_with_url", clone)
    result = asyncio.run(service.clone_repository("Owner", "Plugin", tmp_path, branch="v1.2.3", mirror_id="obsolete"))
    assert not result["success"] and result["error"] == "selected source failed"
    assert len(calls) == 1 and urlsplit(calls[0]).hostname == host


def test_release_index_cache_does_not_cross_information_sources(monkeypatch):
    from src.plugin_market import service as releases

    calls = []

    def fetch(url, *args):
        calls.append(urlsplit(url).hostname)
        return '{"schema_version": 1, "plugins": []}'

    monkeypatch.setattr(market.GitSource, "read_text", fetch)
    configure(information_source="cnb")
    asyncio.run(releases.load_release_index())
    asyncio.run(releases.load_release_index())
    configure(information_source="github")
    asyncio.run(releases.load_release_index())
    assert calls == ["cnb.cool", "raw.githubusercontent.com"]


@pytest.mark.parametrize("source", ["official", "cnb", "github"])
def test_marketplace_listing_only_calls_selected_information_source(monkeypatch, source):
    from src.plugin_market import service

    configure(information_source=source)
    calls = []

    def fail(url, *args):
        calls.append(url)
        raise OSError("offline")

    monkeypatch.setattr(market.GitSource, "read_json", fail)
    monkeypatch.setattr(market.GitSource, "read_text", fail)
    with pytest.raises(OSError, match="offline"):
        asyncio.run(service.get_catalog())
    assert calls
    expected = {"official": "hyybuth.xyz", "cnb": "cnb.cool", "github": "raw.githubusercontent.com"}[source]
    assert all(urlsplit(url).hostname == expected for url in calls)


@pytest.mark.parametrize("media_type,size,status", [
    ("image/webp", 32, 200),
    ("text/html", 32, 502),
    ("image/webp", 512 * 1024 + 1, 502),
])
def test_cnb_icon_proxy_validates_images_and_sets_browser_cache(monkeypatch, media_type, size, status):
    from fastapi import HTTPException

    monkeypatch.setattr(icon_routes, "require_plugin_token", lambda token: None)
    monkeypatch.setattr(icon_routes, "validate_public_url", lambda url, **kw: url)
    monkeypatch.setattr(icon_routes, "get_shared_ssl_context", lambda: None)
    response = BytesIO(b"x" * size)
    response.headers = Message()
    response.headers["Content-Type"] = media_type
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(icon_routes, "build_opener", lambda *args: opener)
    url = "https://cnb.cool/Mai-with-u/plugin-repo/-/git/raw/main/icons/a.webp"
    if status != 200:
        with pytest.raises(HTTPException) as error:
            icon_routes.get_market_icon(url, "session")
        assert error.value.status_code == status
    else:
        result = icon_routes.get_market_icon(url, "session")
        assert result.body == b"x" * size
        assert result.media_type == "image/webp"
        assert result.headers["cache-control"] == "private, max-age=3600"
        assert "cross-origin-resource-policy" not in result.headers
    assert opener.open.call_count == 1
    assert opener.open.call_args.args[0].full_url == url


@pytest.mark.parametrize("url", [
    "https://example.com/icon.webp",
    "https://cnb.cool/other/repo/-/git/raw/main/icon.webp",
    "https://cnb.cool/Mai-with-u/plugin-repo/-/git/raw/main/%2e%2e/icon.webp",
])
def test_cnb_icon_proxy_rejects_non_market_paths(monkeypatch, url):
    from fastapi import HTTPException

    monkeypatch.setattr(icon_routes, "require_plugin_token", lambda token: None)
    with pytest.raises(HTTPException) as error:
        icon_routes.get_market_icon(url, "session")
    assert error.value.status_code == 400
