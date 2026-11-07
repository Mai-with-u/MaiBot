from email.message import Message
from io import BytesIO
from unittest.mock import AsyncMock, Mock
from urllib.error import HTTPError
from urllib.parse import urlsplit
import asyncio
import ipaddress
import json
import subprocess

import pytest

from src.webui.services import git_mirror_service as git
from src.webui.routers.plugin import icon_routes
from src.common import network_security
from src.plugin_market import documents, sources as market
from src.plugin_market.cache import MarketCache


@pytest.fixture(autouse=True)
def isolated_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "market_cache", MarketCache())
    monkeypatch.setattr(market, "SETTINGS_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(network_security, "_should_enforce_public_network", lambda value: True if value is None else value)
    monkeypatch.setattr(network_security, "_resolve_ip_addresses", lambda *args: {ipaddress.ip_address("127.0.0.1")})


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
@pytest.mark.parametrize("download,host", [("cnb", "cnb.cool"), ("github", "github.com"), ("custom", "127.0.0.1")])
def test_download_uses_only_selected_source_even_on_failure(monkeypatch, tmp_path, information, download, host):
    market.save_source_settings(market.PluginSourceSettings(
        information_source=information, download_source=download, custom_download_prefix="https://127.0.0.1",
    ))
    service = git.GitMirrorService()
    calls = []

    async def clone(url, *args, **kwargs):
        calls.append(url)
        return {"success": False, "error": "selected source failed", "mirror_used": download}

    monkeypatch.setattr(service, "_clone_with_url", clone)
    result = asyncio.run(service.clone_repository("Owner", "Plugin", tmp_path, branch="v1.2.3", mirror_id="obsolete"))
    assert not result["success"] and result["error"] == "selected source failed"
    assert len(calls) == 1 and urlsplit(calls[0]).hostname == host


@pytest.mark.parametrize("url", ["http://localhost:3000/owner/repo.git", "https://192.168.1.10/owner/repo.git"])
@pytest.mark.parametrize("operation", ["clone", "pull"])
def test_direct_git_url_allows_private_network(monkeypatch, tmp_path, url, operation):
    run = AsyncMock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(git, "_run_git_process", run)
    service = git.GitMirrorService()
    if operation == "clone":
        result = asyncio.run(service.clone_repository("owner", "repo", tmp_path / "repo", custom_url=url))
        assert url in run.call_args.args[0]
    else:
        (tmp_path / ".git").mkdir()
        result = asyncio.run(service.pull_repository(tmp_path, remote_url=url))
        assert run.call_args_list[0].args[0] == ["git", "remote", "set-url", "origin", url]
    assert result["success"]


@pytest.mark.parametrize("kind,declared", [("readme", None), ("changelog", "changes.md"), ("changelog", "https://127.0.0.1/changes.md")])
def test_source_documents_allow_private_network_but_declared_urls_do_not(monkeypatch, kind, declared):
    configure(download_source="custom", custom_download_prefix="https://127.0.0.1")
    monkeypatch.setattr(documents, "market_cache", MarketCache())
    read = Mock(return_value="document")
    monkeypatch.setattr(documents.GitSource, "read_text", read)
    plugin = {"repository_url": "https://github.com/test/plugin", "manifest": {"changelog": declared}}
    if declared and declared.startswith("https://"):
        with pytest.raises(ValueError, match="禁止访问非公网地址"):
            documents._remote_document(plugin, kind, "cnb", "main", False)
        read.assert_not_called()
    else:
        result = documents._remote_document(plugin, kind, "cnb", "main", False)
        assert result["content"] == "document"
        assert read.call_args.args[0].startswith("https://127.0.0.1/" if kind == "readme" else "https://cnb.cool/")


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
            icon_routes.get_market_icon(url)
        assert error.value.status_code == status
    else:
        result = icon_routes.get_market_icon(url)
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

    with pytest.raises(HTTPException) as error:
        icon_routes.get_market_icon(url)
    assert error.value.status_code == 400
