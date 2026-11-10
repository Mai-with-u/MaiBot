"""插件市场单条容错：一条脏记录不应让整个市场不可用。"""

from types import SimpleNamespace
from typing import Any, Dict

import json

from src.plugin_market import service
from src.plugin_market.releases import parse_release_index

COMMIT = "a" * 40
REPOSITORY_URL = "https://github.com/example/plugin"


def _manifest(plugin_id: str, repository_url: str = REPOSITORY_URL) -> Dict[str, Any]:
    """构造插件清单；repository_url 传空串表示清单未登记仓库地址。"""

    manifest: Dict[str, Any] = {
        "manifest_version": 2,
        "id": plugin_id,
        "name": "Test",
        "version": "1.0.0",
    }
    if repository_url:
        manifest["repository_url"] = repository_url
    return manifest


def _release(manifest_id: str) -> Dict[str, Any]:
    """构造一条 release，manifest_id 与条目登记 ID 不一致时即触发容错分支。"""

    return {
        "version": "1.0.0",
        "tag": "v1.0.0",
        "commit": COMMIT,
        "prerelease": False,
        "manifest": _manifest(manifest_id),
    }


def _index_entry(plugin_id: str, release_manifest_id: str) -> Dict[str, Any]:
    """构造一条版本索引条目。"""

    return {
        "id": plugin_id,
        "manifest_id": plugin_id,
        "repositoryUrl": REPOSITORY_URL,
        "mode": "releases",
        "versions": [_release(release_manifest_id)],
    }


def _official_item(
    plugin_id: str,
    release_manifest_id: str,
    repository_url: str = REPOSITORY_URL,
    manifest_repository_url: str = REPOSITORY_URL,
) -> Dict[str, Any]:
    """构造官方市场摘要条目；两个仓库地址可分别置空以覆盖回退路径。"""

    return {
        "id": plugin_id,
        "marketplace_id": plugin_id,
        "repository_url": repository_url,
        "install_mode": "releases",
        "manifest": _manifest(plugin_id, manifest_repository_url),
        "release": _release(release_manifest_id),
        "stats": None,
        "icon_url": None,
    }


def test_parse_release_index_skips_entry_with_mismatched_manifest_id():
    """条目内 release 的 manifest ID 与登记 ID 不一致时跳过该条，其余条目照常返回。"""

    content = json.dumps(
        {
            "schema_version": 1,
            "plugins": [
                _index_entry("github.example.valid", "github.example.valid"),
                _index_entry("github.example.renamed", "github.example.old"),
            ],
        }
    )

    parsed = parse_release_index(content)

    assert [entry.id for entry in parsed.plugins] == ["github.example.valid"]


def test_parse_release_index_skips_entry_with_invalid_release_fields():
    """条目字段过不了模型校验（如 commit 非 40 位十六进制）时也只跳过该条。"""

    broken = _index_entry("github.example.broken", "github.example.broken")
    broken["versions"][0]["commit"] = "deadbeef"
    content = json.dumps(
        {
            "schema_version": 1,
            "plugins": [_index_entry("github.example.valid", "github.example.valid"), broken],
        }
    )

    parsed = parse_release_index(content)

    assert [entry.id for entry in parsed.plugins] == ["github.example.valid"]


def test_official_catalog_skips_item_with_mismatched_manifest_id(monkeypatch):
    """官方摘要逐条解析，失配条目退化为空版本列表而不是拖垮整个列表。"""

    monkeypatch.setattr(service, "download_source", lambda: SimpleNamespace(id="github"))

    plugins = service._catalog(
        [
            _official_item("github.example.valid", "github.example.valid"),
            _official_item("github.example.renamed", "github.example.old"),
        ],
        "official",
    )

    assert [plugin["id"] for plugin in plugins] == ["github.example.valid", "github.example.renamed"]
    renamed = next(plugin for plugin in plugins if plugin["id"] == "github.example.renamed")
    assert renamed["releases"]["versions"] == []


def test_official_catalog_falls_back_to_official_repository_url(monkeypatch):
    """清单未登记仓库地址时，回退发布信息改用官方条目声明的仓库地址，并把该地址写回插件信息。"""

    monkeypatch.setattr(service, "download_source", lambda: SimpleNamespace(id="github"))

    plugins = service._catalog(
        [_official_item("github.example.renamed", "github.example.old", manifest_repository_url="")],
        "official",
    )

    assert [plugin["id"] for plugin in plugins] == ["github.example.renamed"]
    assert plugins[0]["releases"]["repositoryUrl"] == REPOSITORY_URL
    assert plugins[0]["repository_url"] == REPOSITORY_URL


def test_official_catalog_falls_back_to_manifest_repository_url(monkeypatch):
    """官方地址非空但过不了发布条目校验时改用清单登记的仓库地址，插件不因此被跳过。"""

    monkeypatch.setattr(service, "download_source", lambda: SimpleNamespace(id="github"))

    plugins = service._catalog(
        [
            _official_item(
                "github.example.renamed", "github.example.old", repository_url="https://gitlab.com/example/plugin"
            )
        ],
        "official",
    )

    assert [plugin["id"] for plugin in plugins] == ["github.example.renamed"]
    assert plugins[0]["releases"]["repositoryUrl"] == REPOSITORY_URL
    assert plugins[0]["repository_url"] == REPOSITORY_URL


def test_official_catalog_skips_item_without_usable_repository_url(monkeypatch):
    """两个来源都拿不到仓库地址时跳过该条，同批其余插件不受影响。"""

    monkeypatch.setattr(service, "download_source", lambda: SimpleNamespace(id="github"))

    plugins = service._catalog(
        [
            _official_item("github.example.valid", "github.example.valid"),
            _official_item(
                "github.example.renamed", "github.example.old", repository_url="", manifest_repository_url=""
            ),
        ],
        "official",
    )

    assert [plugin["id"] for plugin in plugins] == ["github.example.valid"]
