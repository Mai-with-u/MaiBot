"""插件市场单条容错：一条脏记录不应让整个市场不可用。"""

from typing import Any, Dict

import json

import pytest

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


@pytest.mark.parametrize("broken", [None, 42, {"id": "broken"}])
def test_parse_release_index_skips_invalid_models(broken):
    content = json.dumps({"schema_version": 1, "plugins": [
        broken, _index_entry("valid", "valid"),
    ]})
    assert [entry.id for entry in parse_release_index(content).plugins] == ["valid"]


def test_parse_release_index_all_invalid_entries_yield_empty_index():
    content = json.dumps({"schema_version": 1, "plugins": [_index_entry("renamed", "old")]})
    assert parse_release_index(content).plugins == []


@pytest.mark.parametrize("content", ["not json", '{}', '{"schema_version":2,"plugins":[]}',
                                     '{"schema_version":1,"plugins":{}}'])
def test_parse_release_index_rejects_invalid_envelope(content):
    with pytest.raises(ValueError):
        parse_release_index(content)


def test_parse_release_index_rejects_conflicting_aliases():
    first = _index_entry("first", "shared")
    first["manifest_id"] = "shared"
    second = _index_entry("second", "shared")
    second["manifest_id"] = "shared"
    with pytest.raises(ValueError, match="插件 ID 冲突"):
        parse_release_index(json.dumps({"schema_version": 1, "plugins": [first, second]}))


@pytest.mark.parametrize("repository_url", ["", "https://gitlab.com/example/plugin"])
def test_catalog_skips_item_without_usable_repository_url(repository_url):
    """索引未收录且清单仓库地址无效时跳过该条，同批其余插件不受影响。"""
    plugins = service._catalog([
        {"id": "valid", "manifest": _manifest("valid")},
        {"id": "invalid", "manifest": _manifest("invalid", repository_url)},
    ])
    assert [plugin["id"] for plugin in plugins] == ["valid"]


def test_catalog_keeps_renamed_plugin_without_invalid_release():
    """历史快照 ID 不匹配时，目录仍可展示当前清单，但不提供无效发布版本。"""
    index = parse_release_index(json.dumps({"schema_version": 1, "plugins": [
        _index_entry("valid", "valid"), _index_entry("renamed", "old"),
    ]}))
    plugins = service._catalog([
        {"id": "valid", "manifest": _manifest("valid")},
        {"id": "renamed", "manifest": _manifest("renamed")},
    ], index)
    assert [plugin["id"] for plugin in plugins] == ["valid", "renamed"]
    assert plugins[1]["releases"]["versions"] == []
    assert plugins[1]["releases"]["recommended_version"] is None
