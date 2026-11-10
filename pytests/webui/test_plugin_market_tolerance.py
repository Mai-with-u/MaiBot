"""插件市场单条容错：一条脏记录不应让整个市场不可用。"""

from types import SimpleNamespace
from typing import Any, Dict

import json

from src.plugin_market import service
from src.plugin_market.releases import parse_release_index

COMMIT = "a" * 40
REPOSITORY_URL = "https://github.com/example/plugin"


def _manifest(plugin_id: str) -> Dict[str, Any]:
    return {
        "manifest_version": 2,
        "id": plugin_id,
        "name": "Test",
        "version": "1.0.0",
        "repository_url": REPOSITORY_URL,
    }


def _release(manifest_id: str) -> Dict[str, Any]:
    return {
        "version": "1.0.0",
        "tag": "v1.0.0",
        "commit": COMMIT,
        "prerelease": False,
        "manifest": _manifest(manifest_id),
    }


def _index_entry(plugin_id: str, release_manifest_id: str) -> Dict[str, Any]:
    return {
        "id": plugin_id,
        "manifest_id": plugin_id,
        "repositoryUrl": REPOSITORY_URL,
        "mode": "releases",
        "versions": [_release(release_manifest_id)],
    }


def _official_item(plugin_id: str, release_manifest_id: str) -> Dict[str, Any]:
    return {
        "id": plugin_id,
        "marketplace_id": plugin_id,
        "repository_url": REPOSITORY_URL,
        "install_mode": "releases",
        "manifest": _manifest(plugin_id),
        "release": _release(release_manifest_id),
        "stats": None,
        "icon_url": None,
    }


def test_parse_release_index_skips_entry_with_mismatched_manifest_id():
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


def test_official_catalog_skips_item_with_mismatched_manifest_id(monkeypatch):
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
