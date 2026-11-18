from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import json

import pytest

from src.plugin_market import documents, engagement, service, sources, statistics
from src.plugin_market.cache import MarketCache
from src.plugin_runtime.capabilities import market
from src.plugin_runtime.host.authorization import AuthorizationManager
from src.plugin_runtime.host.capability_service import CapabilityService
from src.plugin_runtime.protocol.envelope import Envelope, MessageType


def item(plugin_id, downloads, rank):
    return {
        "id": plugin_id, "marketplace_id": f"repo.{plugin_id}",
        "repository_url": f"https://github.com/test/{plugin_id}", "install_mode": "releases", "release": None,
        "manifest": {
            "manifest_version": 2, "id": plugin_id, "name": f"Plugin {plugin_id}",
            "version": "1.0.0", "description": "voice 语音", "categories": ["Audio"],
            "urls": {"repository": f"https://github.com/test/{plugin_id}"},
        },
        "stats": {"plugin_id": plugin_id, "downloads": downloads, "likes": 3, "dislikes": 0,
                  "rating": 4.0, "rating_count": 1, "comment_count": 2, "downloads_growth_rank_7d": rank},
    }


class Catalog(dict):
    """默认信息源 CNB：清单来自 plugin_details.json / plugin_versions.json，统计来自官方统计服务。"""

    index_plugins: list
    text_calls: list
    json_calls: list

    def text(self, url):
        if "plugin_versions" in url:
            return json.dumps({"schema_version": 1, "plugins": self.index_plugins})
        raw = []
        for plugin in self["plugins"]:
            entry = {"id": plugin["marketplace_id"], "manifest": plugin["manifest"]}
            if "icon_url" in plugin:
                entry["assets"] = {"icon_64": plugin["icon_url"]}
            raw.append(entry)
        return json.dumps(raw)

    def stats(self, url):
        records = {plugin["stats"]["plugin_id"]: plugin["stats"] for plugin in self["plugins"] if plugin.get("stats")}
        if url.endswith("/stats/summary"):
            return {"success": True, "stats": deepcopy(records)}
        return {"success": True, "stats": deepcopy(records[url.rsplit("/", 1)[1]])}


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "SETTINGS_FILE", tmp_path / "source.json")
    monkeypatch.setattr(sources, "market_cache", MarketCache())
    monkeypatch.setattr(engagement, "market_cache", sources.market_cache)
    monkeypatch.setattr(documents, "market_cache", MarketCache())
    monkeypatch.setattr(sources, "get_shared_ssl_context", lambda: None)
    monkeypatch.setattr(documents, "get_shared_ssl_context", lambda: None)
    monkeypatch.setattr(documents, "get_instance_root", lambda: tmp_path)
    monkeypatch.setattr(documents, "validate_public_url", lambda url, **kwargs: url)
    monkeypatch.setattr("src.common.network_security.validate_public_url", lambda url, **kwargs: url)
    payload = Catalog(plugins=[item("a", 100, 9), item("b", 200, 2), item("c", 50, None)])
    payload.index_plugins, payload.text_calls, payload.json_calls = [], [], []
    monkeypatch.setattr(sources.GitSource, "read_text", lambda url, *args: payload.text_calls.append(url) or payload.text(url))
    monkeypatch.setattr(sources.GitSource, "read_json", lambda url, *args: payload.json_calls.append(url) or payload.stats(url))
    return payload


def test_search_shares_webui_snapshot_and_preserves_ids_and_ranks(catalog):
    from src.webui.routers.plugin import marketplace

    asyncio.run(marketplace.get_marketplace())
    assert len(catalog.text_calls) == 2 and not catalog.json_calls
    result = asyncio.run(service.search(service.MarketSearch(
        query="voice", sort_by="downloads_growth_7d", include_stats=True, limit=1,
    )))
    # 目录复用 WebUI 的缓存；统计单独读取一次官方统计汇总。
    assert len(catalog.text_calls) == 2
    assert catalog.json_calls == [f"{sources.MARKET_BASE_URL}/stats/summary"]
    assert result["cache"]["hit"]
    assert result["matched"] == 2
    assert result["plugins"][0]["id"] == "b"
    assert result["plugins"][0]["marketplace_id"] == "repo.b"
    assert result["plugins"][0]["stats"]["downloads_growth_rank_7d"] == 2
    assert "manifest" not in result["plugins"][0]
    assert result["pagination"]["has_next"]
    asyncio.run(marketplace.get_marketplace(force_refresh=True))
    assert len(catalog.text_calls) == 4


def test_filter_refresh_and_unsupported_ranking(catalog, monkeypatch):
    result = asyncio.run(service.search(service.MarketSearch(query="语音 repo.a", category="Audio")))
    assert [plugin["id"] for plugin in result["plugins"]] == ["a"]
    assert "stats" not in result["plugins"][0]
    catalog["plugins"][0]["manifest"]["name"] = "Changed"
    assert asyncio.run(service.search(service.MarketSearch()))["plugins"][0]["name"] == "Plugin a"
    refreshed = asyncio.run(service.search(service.MarketSearch(force_refresh=True)))
    assert refreshed["plugins"][0]["name"] == "Changed"
    with pytest.raises(ValueError, match="未提供"):
        asyncio.run(service.search(service.MarketSearch(sort_by="likes_growth_30d")))
    with pytest.raises(ValueError):
        service.MarketSearch(limit=51)


@pytest.mark.parametrize("information", ["github", "cnb"])
@pytest.mark.parametrize("download,prefix", [
    ("cnb", "https://cnb.cool/Mai-with-u/plugin-repo/-/git/raw/"),
    ("github", "https://raw.githubusercontent.com/Mai-with-u/plugin-repo/"),
    ("custom", "https://download.example/Mai-with-u/plugin-repo/"),
])
@pytest.mark.parametrize("icon,resource", [
    ("icons/a/icon_64.png", "main/icons/a/icon_64.png"),
    ("https://raw.githubusercontent.com/Mai-with-u/plugin-repo/" + "a" * 40 + "/icons/a/icon_64.png",
     "a" * 40 + "/icons/a/icon_64.png"),
    ("https://cnb.cool/Mai-with-u/plugin-repo/-/git/raw/main/icons/a/icon_64.png",
     "main/icons/a/icon_64.png"),
])
def test_market_icons_follow_download_source(catalog, information, download, prefix, icon, resource):
    sources.SETTINGS_FILE.write_text(json.dumps({
        "information_source": information, "download_source": download,
        "custom_download_prefix": "https://download.example",
    }), encoding="utf-8")
    record = {"id": "repo.a", "manifest": catalog["plugins"][0]["manifest"], "assets": {"icon_64": icon}}
    plugins = service._catalog([record])
    expected_resource = "main/" + resource.split("/", 1)[1] if download == "cnb" else resource
    assert plugins[0]["assets"]["icon_64"] == prefix + expected_resource


def test_download_source_change_rewrites_icons_from_cached_catalog(catalog):
    catalog["plugins"][0]["icon_url"] = (
        "https://raw.githubusercontent.com/Mai-with-u/plugin-repo/main/icons/a/icon_64.png"
    )
    catalog["plugins"][1]["icon_url"] = "https://images.example/plugin.png"
    first = asyncio.run(service.get_catalog())
    assert first["plugins"][0]["assets"]["icon_64"] == (
        "https://cnb.cool/Mai-with-u/plugin-repo/-/git/raw/main/icons/a/icon_64.png"
    )
    sources.SETTINGS_FILE.write_text('{"download_source":"github"}', encoding="utf-8")
    second = asyncio.run(service.get_catalog())
    assert second["cache"]["hit"] and len(catalog.text_calls) == 2
    assert second["plugins"][0]["assets"]["icon_64"] == catalog["plugins"][0]["icon_url"]
    assert second["plugins"][1]["assets"]["icon_64"] == "https://images.example/plugin.png"
    assert second["plugins"][2]["assets"] is None


def test_growth_sort_tolerates_plugins_without_statistics_record(catalog):
    del catalog["plugins"][2]["stats"]  # 统计服务没有该插件的记录时，清单里的 stats 为 null
    result = asyncio.run(service.search(service.MarketSearch(sort_by="downloads_growth_7d", include_stats=True)))
    assert [plugin["id"] for plugin in result["plugins"]] == ["b", "a"]
    assert [plugin["id"] for plugin in asyncio.run(service.search(service.MarketSearch(include_stats=True)))["plugins"]] == ["a", "b", "c"]


def test_default_source_is_cnb_and_legacy_official_setting_migrates(catalog):
    assert sources.load_source_settings().information_source == "cnb"
    for legacy in ('{"information_source":"official","download_source":"github"}', '{"use_github":false}'):
        sources.SETTINGS_FILE.write_text(legacy, encoding="utf-8")
        assert sources.load_source_settings().information_source == "cnb"
    assert sources.load_source_settings().download_source == "cnb"
    sources.SETTINGS_FILE.write_text('{"information_source":"official","download_source":"github"}', encoding="utf-8")
    assert sources.load_source_settings().download_source == "github"
    with pytest.raises(ValueError):
        sources.PluginSourceSettings(information_source="official")


def test_source_change_keeps_catalog_and_statistics_independent(catalog):
    asyncio.run(service.search(service.MarketSearch()))
    assert len(catalog.text_calls) == 2 and catalog.text_calls[0].startswith("https://cnb.cool/")
    assert not catalog.json_calls
    sources.SETTINGS_FILE.write_text('{"information_source":"github"}', encoding="utf-8")
    result = asyncio.run(service.search(service.MarketSearch()))
    assert result["source"] == "github" and result["features"]["stats"]
    assert not result["cache"]["hit"] and len(catalog.text_calls) == 4
    assert catalog.text_calls[2].startswith("https://raw.githubusercontent.com/")
    with_stats = asyncio.run(service.search(service.MarketSearch(include_stats=True, sort_by="downloads")))
    assert [plugin["id"] for plugin in with_stats["plugins"]] == ["b", "a", "c"]
    assert with_stats["plugins"][0]["stats"]["downloads"] == 200
    assert catalog.json_calls == [f"{sources.MARKET_BASE_URL}/stats/summary"]


@pytest.mark.parametrize("source", ["cnb", "github"])
def test_statistics_reads_official_service_without_loading_catalog(catalog, monkeypatch, source):
    sources.SETTINGS_FILE.write_text(json.dumps({"information_source": source}), encoding="utf-8")
    monkeypatch.setattr(service, "_snapshot", AsyncMock(side_effect=AssertionError("statistics loaded catalog")))
    calls = []
    record = {**catalog["plugins"][0]["stats"], "recent_ratings": [{"comment": "review"}]}

    def read(url, *args):
        calls.append(url)
        return {"success": True, "stats": {"a": record} if url.endswith("/stats/summary") else record}

    monkeypatch.setattr(sources.GitSource, "read_json", read)
    summary = asyncio.run(statistics.get_summary())
    detail = asyncio.run(service.get_stats("a"))
    assert summary["source"] == detail["source"] == "official"
    assert summary["stats"]["a"]["downloads"] == 100
    assert detail["stats"]["recent_ratings"] == [{"comment": "review"}]
    assert calls == [f"{sources.MARKET_BASE_URL}/stats/summary", f"{sources.MARKET_BASE_URL}/stats/a"]
    assert not catalog.text_calls


def test_statistics_route_is_separate_from_plugin_detail(catalog, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.webui.routers.plugin.marketplace import router

    read = AsyncMock(return_value={"success": True, "stats": {"a": catalog["plugins"][0]["stats"]}})
    monkeypatch.setattr(statistics, "get_summary", read)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/marketplace/stats/summary?force_refresh=true")
    assert response.status_code == 200
    assert response.json()["stats"]["a"]["downloads"] == 100
    read.assert_awaited_once_with(True)


def test_statistics_preserve_official_ids_without_compatibility_mapping(catalog, monkeypatch):
    catalog["plugins"][0]["stats"]["plugin_id"] = "legacy.repo.a"
    summary = asyncio.run(statistics.get_summary())
    assert "a" not in summary["stats"]
    assert summary["stats"]["legacy.repo.a"]["plugin_id"] == "legacy.repo.a"
    request = AsyncMock(return_value=engagement.MarketReply(200, {"success": True}))
    monkeypatch.setattr(engagement, "_request", request)
    asyncio.run(engagement.vote(engagement.VoteRequest(plugin_id="a", user_id="test")))
    assert request.call_args.args[2]["plugin_id"] == "a"


@pytest.mark.parametrize("invalid", [
    {"downloads": True}, {"downloads": -1}, {"rating": None},
])
def test_invalid_statistics_are_exposed_and_not_cached(catalog, monkeypatch, invalid):
    record = deepcopy(catalog["plugins"][0]["stats"])
    record.update(invalid)
    calls = []
    monkeypatch.setattr(sources.GitSource, "read_json", lambda *args: calls.append(args[0]) or {
        "success": True, "stats": {"a": record},
    })
    with pytest.raises(ValueError):
        asyncio.run(statistics.get_summary())
    record.update(catalog["plugins"][0]["stats"])
    assert asyncio.run(statistics.get_summary())["stats"]["a"]["downloads"] == 100
    assert len(calls) == 2


@pytest.mark.parametrize("payload", [
    {"success": False, "stats": {}}, {"success": True}, {"success": True, "stats": []},
    {"success": True, "stats": {"a": {"plugin_id": "b", "downloads": 1, "likes": 0, "dislikes": 0,
                                       "rating": 0.0, "rating_count": 0, "comment_count": 0}}},
])
def test_malformed_statistics_summary_is_rejected(catalog, monkeypatch, payload):
    monkeypatch.setattr(sources.GitSource, "read_json", lambda *args: payload)
    with pytest.raises(ValueError):
        asyncio.run(statistics.get_summary())


def test_mirror_catalog_does_not_wait_for_or_hide_statistics_failure(catalog, monkeypatch):
    sources.SETTINGS_FILE.write_text('{"information_source":"cnb"}', encoding="utf-8")
    raw_items = [{"id": item["marketplace_id"], "manifest": item["manifest"]} for item in catalog["plugins"]]
    monkeypatch.setattr(sources.GitSource, "read_text", lambda url, *args: json.dumps(
        {"schema_version": 1, "plugins": []} if "plugin_versions" in url else raw_items
    ))
    monkeypatch.setattr(sources.GitSource, "read_json", lambda *args: (_ for _ in ()).throw(OSError("stats offline")))
    result = asyncio.run(service.get_catalog())
    assert result["source"] == "cnb" and len(result["plugins"]) == 3
    assert all(plugin["stats"] is None for plugin in result["plugins"])
    with pytest.raises(OSError, match="stats offline"):
        asyncio.run(statistics.get_summary())


def test_interaction_invalidates_statistics_but_keeps_catalog_resources(catalog, monkeypatch):
    urls = []
    record = deepcopy(catalog["plugins"][0]["stats"])
    monkeypatch.setattr(sources.GitSource, "read_json", lambda url, *args: urls.append(url) or {
        "success": True, "stats": {"a": record},
    })
    cache = sources.market_cache
    mirror_key = "https://cnb.cool/plugin_details.json"
    official_summary = f"{sources.MARKET_BASE_URL}/stats/summary"
    official_stats = f"{sources.MARKET_BASE_URL}/stats/a"
    for key in (mirror_key, official_stats):
        cache.get(key, lambda: {"cached": True}, 60)
    assert official_summary.startswith(f"{sources.MARKET_BASE_URL}/stats/")
    asyncio.run(statistics.get_summary())
    assert asyncio.run(statistics.get_summary())["cache"]["hit"]

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def request(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, is_success=True, json=lambda: {"success": True, "likes": 4})

    monkeypatch.setattr(engagement.httpx, "AsyncClient", Client)
    monkeypatch.setattr(engagement, "get_shared_ssl_context_async", AsyncMock(return_value=None))
    asyncio.run(engagement.vote(engagement.VoteRequest(plugin_id="a", user_id="test")))
    assert not asyncio.run(statistics.get_summary())["cache"]["hit"]
    assert len(urls) == 2
    # 清单资源不受互动影响；单插件统计与统计汇总同在 /stats/ 前缀下，会一起失效（汇总已由上面的 miss 验证）。
    assert cache.get(mirror_key, lambda: pytest.fail("unrelated resource was invalidated"), 60)[1]["hit"]
    assert not cache.get(official_stats, lambda: {"new": True}, 60)[1]["hit"]


def test_failed_source_does_not_fallback_or_hide_error(catalog, monkeypatch):
    calls = []

    def fail(url, *args):
        calls.append(url)
        raise OSError("offline")

    monkeypatch.setattr(sources.GitSource, "read_text", fail)
    with pytest.raises(OSError, match="offline"):
        asyncio.run(service.search(service.MarketSearch()))
    assert calls and all(url.startswith("https://cnb.cool/") for url in calls)


def test_cache_coalesces_requests_from_two_event_loops_and_isolates_results(catalog, monkeypatch):
    entered, release, waiting = Event(), Event(), Event()
    calls = []

    def read(url, *args):
        calls.append(url)
        entered.set()
        assert release.wait(5)
        return catalog.text(url)

    monkeypatch.setattr(sources.GitSource, "read_text", read)
    original_get = sources.market_cache.get

    def get(*args, **kwargs):
        if entered.is_set():
            waiting.set()
        return original_get(*args, **kwargs)

    monkeypatch.setattr(sources.market_cache, "get", get)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(asyncio.run, service.search(service.MarketSearch()))
        assert entered.wait(5)
        second = pool.submit(asyncio.run, service.search(service.MarketSearch()))
        assert waiting.wait(5)
        release.set()
        result, other = first.result(5), second.result(5)
    assert len(calls) == 2  # plugin_details.json 与 plugin_versions.json 各读取一次，两个事件循环共享在途请求
    result["plugins"][0]["name"] = "Mutated"
    assert other["plugins"][0]["name"] == "Plugin a"


def test_failed_cache_request_can_be_retried():
    cache = MarketCache()
    with pytest.raises(ValueError):
        cache.get("key", lambda: (_ for _ in ()).throw(ValueError("bad data")), 60)
    assert cache.get("key", lambda: {"ok": True}, 60)[0] == {"ok": True}


def test_detail_keeps_market_identity_and_install_rechecks_remote_state(catalog):
    entry = {"id": "repo.a", "manifest_id": "a", "repositoryUrl": "https://github.com/test/a",
             "mode": "releases", "versions": []}
    catalog.index_plugins.append(entry)
    result = asyncio.run(service.get_detail("repo.a"))
    assert result["plugin"]["id"] == "a" and result["plugin"]["marketplace_id"] == "repo.a"
    assert result["releases"]["recommended_version"] is None
    # 已缓存的详情仍可展示，但安装必须重新查询远端同步/撤回状态。
    from src.plugin_market.errors import MarketError
    from src.plugin_market.service import resolve_release

    entry["sync_error"] = "remote withdrawn"
    with pytest.raises(MarketError) as error:
        asyncio.run(resolve_release("a", "latest"))
    assert error.value.status_code == 409 and "remote withdrawn" in str(error.value)
    catalog.index_plugins.clear()
    with pytest.raises(ValueError, match="版本索引"):
        asyncio.run(service.get_detail("a", force_refresh=True))


def test_git_compatibility_includes_compatible_branch_and_stable_release(catalog, monkeypatch):
    sources.SETTINGS_FILE.write_text('{"information_source":"github"}', encoding="utf-8")
    manifest = {
        "manifest_version": 2, "id": "test.voice", "name": "Voice", "description": "voice",
        "version": "1.0.0", "author": {"name": "Test", "url": "https://github.com/test"},
        "license": "MIT", "urls": {"repository": "https://github.com/test/voice"},
        "host_application": {"min_version": "1.0.0", "max_version": "1.99.99"},
        "sdk": {"min_version": "2.0.0", "max_version": "2.99.99"}, "capabilities": [],
        "i18n": {"default_locale": "zh-CN"},
    }
    index = {"schema_version": 1, "plugins": [{
        "id": "repo.voice", "manifest_id": "test.voice", "repositoryUrl": "https://github.com/test/voice",
        "mode": "branch", "versions": [],
    }]}

    def read(url, *args):
        return json.dumps(index if "plugin_versions" in url else [{"id": "repo.voice", "manifest": manifest}])

    monkeypatch.setattr(sources.GitSource, "read_text", read)
    branch = asyncio.run(service.search(service.MarketSearch(compatible_only=True)))
    assert [p["id"] for p in branch["plugins"]] == ["test.voice"]
    entry = index["plugins"][0]
    entry.update(mode="releases", versions=[{
        "version": "1.0.0", "tag": "v1.0.0", "commit": "a" * 40,
        "prerelease": False, "manifest": manifest,
    }])
    released = asyncio.run(service.get_detail("test.voice", force_refresh=True))
    assert released["releases"]["recommended_version"] == "1.0.0"
    assert released["releases"]["versions"][0]["compatible"]


def test_tag_manifest_survives_legacy_branch_even_without_compatible_release(catalog):
    from src.plugin_market.releases import parse_release_index

    manifest = catalog["plugins"][0]["manifest"]
    index = parse_release_index(json.dumps({"schema_version": 1, "plugins": [{
        "id": "repo.a", "manifest_id": "a", "repositoryUrl": "https://github.com/test/a", "mode": "releases",
        "versions": [{"version": "1.0.0", "tag": "v1.0.0", "commit": "a" * 40, "prerelease": False,
                      "manifest": manifest}],
    }]}))
    plugins = service._catalog([{"id": "repo.a", "manifest": {"manifest_version": 1}}], index)
    assert len(plugins) == 1 and plugins[0]["id"] == "a"
    # 不兼容版本仍可展示具体原因，不会因分支清单过旧而被静默丢弃。
    assert plugins[0]["releases"]["versions"][0]["reasons"]


def test_readme_reads_local_without_network_and_remote_uses_only_selected_branch(catalog, tmp_path, monkeypatch):
    folder = tmp_path / "plugins" / "a"
    folder.mkdir(parents=True)
    (folder / "_manifest.json").write_text(json.dumps(catalog["plugins"][0]["manifest"]), encoding="utf-8")
    (folder / "README.md").write_text("local" * 200, encoding="utf-8")
    local = asyncio.run(service.get_readme("a", max_chars=500))
    assert local["source_type"] == "local" and local["truncated"]
    asyncio.run(service.get_catalog())  # 目录和文档共用 GitSource.read_text，先缓存目录再替换
    calls = []
    monkeypatch.setattr(documents.GitSource, "read_text", lambda url, *args: calls.append(url) or "remote")
    remote = asyncio.run(service.get_readme("a", prefer_local=False, branch="docs"))
    assert remote["content"] == "remote"
    assert calls == [sources.GitSource.select("cnb").raw_url("test", "a", "docs", "README.md")]
    with pytest.raises(ValueError, match="匹配多个"):
        asyncio.run(service.get_readme("Plugin", prefer_local=False))


def test_changelog_declared_path_and_inline_content_are_resolved_in_public_service(catalog, tmp_path, monkeypatch):
    folder = tmp_path / "plugins" / "a"
    (folder / "docs").mkdir(parents=True)
    manifest = catalog["plugins"][0]["manifest"]
    manifest["changelog"] = "docs/changes.md"
    (folder / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "docs" / "changes.md").write_text("local changelog", encoding="utf-8")
    assert asyncio.run(service.get_changelog("a"))["content"] == "local changelog"
    manifest["changelog"] = "# Inline changelog"
    assert asyncio.run(service.get_changelog("a", prefer_local=False))["content"] == "# Inline changelog"


def test_document_remote_urls_and_refs_are_not_reconstructed_in_frontend(catalog, monkeypatch):
    catalog["plugins"][0]["manifest"]["changelog"] = "https://docs.example/changes.md"
    asyncio.run(service.get_catalog())
    calls = []
    monkeypatch.setattr(documents.GitSource, "read_text", lambda url, *args: calls.append(url) or "content")
    assert asyncio.run(service.get_changelog("a", prefer_local=False))["content"] == "content"
    asyncio.run(service.get_readme("a", prefer_local=False, branch="a" * 40))
    assert calls == ["https://docs.example/changes.md", sources.GitSource.select("cnb").raw_url("test", "a", "a" * 40, "README.md")]


def test_readme_uses_selected_download_source(catalog, monkeypatch):
    asyncio.run(service.get_catalog())
    calls = []
    monkeypatch.setattr(documents.GitSource, "read_text", lambda url, *args: calls.append(url) or "content")
    asyncio.run(service.get_readme("a", prefer_local=False))
    sources.SETTINGS_FILE.write_text('{"download_source":"github"}', encoding="utf-8")
    asyncio.run(service.get_readme("a", prefer_local=False))
    assert calls == [
        sources.GitSource.select("cnb").raw_url("test", "a", "main", "README.md"),
        "https://raw.githubusercontent.com/test/a/main/README.md",
    ]


def test_missing_documents_and_errors_remain_distinct(catalog, monkeypatch):
    assert asyncio.run(service.get_changelog("a", prefer_local=False))["source_type"] == "missing"

    def fail(*args):
        raise OSError("offline")

    monkeypatch.setattr(documents.GitSource, "read_text", fail)
    with pytest.raises(OSError, match="offline"):
        asyncio.run(service.get_readme("a", prefer_local=False))


def test_local_changelog_cannot_escape_plugin_directory(catalog, tmp_path):
    folder = tmp_path / "plugins" / "a"
    folder.mkdir(parents=True)
    manifest = catalog["plugins"][0]["manifest"]
    manifest["changelog"] = "../outside.md"
    (folder / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder.parent / "outside.md").write_text("outside", encoding="utf-8")
    with pytest.raises(ValueError):
        asyncio.run(service.get_changelog("a"))


def test_invalidating_inflight_market_request_prevents_old_cache_refill():
    cache = MarketCache()
    entered, release = Event(), Event()

    def old_request():
        entered.set()
        assert release.wait(5)
        return "before mutation"

    with ThreadPoolExecutor(1) as pool:
        old = pool.submit(cache.get, "market/catalog", old_request, 60)
        assert entered.wait(5)
        cache.invalidate("market/")
        assert cache.get("market/catalog", lambda: "after mutation", 60)[0] == "after mutation"
        release.set()
        old.result(5)
    assert cache.get("market/catalog", lambda: "wrong", 60)[0] == "after mutation"


def test_webui_uses_public_catalog_and_removed_read_routes_are_unavailable(catalog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.webui.routers.plugin import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/plugins/marketplace")
        assert response.status_code == 200
        plugin = response.json()["plugins"][0]
        assert plugin["marketplace_id"] == "repo.a" and "releases" in plugin
        for path in ("/plugins/releases", "/plugins/local-readme/a", "/plugins/local-changelog/a",
                     "/plugins/stats-proxy/stats/summary"):
            assert client.get(path).status_code == 404
        assert client.post("/plugins/fetch-raw", json={}).status_code == 404


@pytest.mark.asyncio
async def test_host_requires_declared_market_capability(catalog, monkeypatch):
    authorization = AuthorizationManager()
    authorization.register_plugin("test", [])
    capabilities = CapabilityService(authorization)
    capabilities.register_capability("market.search", market.search)
    search = AsyncMock(return_value={"plugins": []})
    monkeypatch.setattr(service, "search", search)
    envelope = Envelope(
        request_id=1, message_type=MessageType.REQUEST, plugin_id="test", method="cap.call",
        payload={"capability": "market.search", "args": {"query": "语音"}},
    )
    denied = await capabilities.handle_capability_request(envelope)
    assert denied.error["code"] == "E_CAPABILITY_DENIED"
    search.assert_not_awaited()
    authorization.register_plugin("test", ["market.search"])
    response = await capabilities.handle_capability_request(envelope)
    assert response.error is None
    assert search.await_args.args[0].query == "语音"
