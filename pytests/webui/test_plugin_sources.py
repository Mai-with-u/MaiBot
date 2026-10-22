from urllib.error import HTTPError
from urllib.parse import urlsplit
import asyncio
import json

import pytest

from src.webui.services import git_mirror_service as git
from src.plugin_market import sources as market
from src.plugin_market.cache import MarketCache


@pytest.fixture(autouse=True)
def isolated_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "market_cache", MarketCache())
    monkeypatch.setattr(market, "SETTINGS_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(git, "_validate_custom_outbound_url", lambda value: value)
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
