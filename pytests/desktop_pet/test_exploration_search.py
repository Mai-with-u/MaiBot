from types import SimpleNamespace
import json

import pytest

from src.desktop_pet import exploration, exploration_search, exploration_tools


@pytest.fixture
def saved_products(tmp_path, monkeypatch):
    monkeypatch.setattr(exploration, "get_instance_root", lambda: tmp_path)
    monkeypatch.setattr(exploration_search, "is_owner_chat", lambda session: session == "owner")
    monkeypatch.setattr(exploration_tools, "is_owner_chat", lambda session: session == "owner")
    for owner in ("owner", "other"):
        task = exploration.exploration_tasks(owner, "create", goal="检索方法与资料")
        exploration.exploration_tasks(owner, "complete", task["id"], summary="已记录", result="检索有歧义",
            products={"resources": [{"title": "Python 文档", "kind": "text", "summary": "接口说明",
                                     "content": "搜索接口支持关键词", "source": "https://example.com/python"},
                                    {"title": "其他文档", "kind": "text", "summary": "Python 扩展",
                                     "content": "查询参数说明"}],
                      "experiences": [{"title": "Python 搜索方法", "conditions": "可访问文档", "path": ["查询关键词", "核对结果"],
                                       "outcome": "failed", "result": "关键词匹配不唯一", "feedback": "增加模块名",
                                       "feedback_source": "observation"}]})
    return tmp_path / "data/desktop_pet/exploration_tasks.json"


def test_search_returns_scoped_ranked_products_without_mutation(saved_products):
    before = saved_products.read_bytes()
    result = exploration_search.search_exploration_products("owner", ["ＰＹＴＨＯＮ"], limit=1)
    assert result["total"] == 3
    assert result["next_offset"] == 1
    assert "python" in result["items"][0]["product"]["title"].casefold()
    page = exploration_search.search_exploration_products("owner", ["python"], offset=1, limit=10)
    assert len(page["items"]) == 2
    assert page["next_offset"] is None
    ids = {task["id"] for task in json.loads(before) if task["session_id"] == "owner"}
    assert all(item["task_id"] in ids for item in result["items"] + page["items"])
    assert saved_products.read_bytes() == before


def test_filter_matches_content_and_preserves_failed_experience(saved_products):
    result = exploration_search.search_exploration_products("owner", ["增加模块名"], "experience")
    assert result["total"] == 1
    assert result["items"][0]["product"]["outcome"] == "failed"
    assert result["items"][0]["task_status"] == "completed"
    assert exploration_search.search_exploration_products("owner", ["增加模块名"], "resource")["total"] == 0
    assert exploration_search.search_exploration_products("missing", ["python"])["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_tool_works_in_normal_chat_without_exploration_id(saved_products, monkeypatch, enabled):
    monkeypatch.setattr(exploration_tools.global_config.experimental, "desktop_pet_exploration_enabled", enabled)
    provider = exploration_tools.ExplorationToolProvider()
    owner = SimpleNamespace(session_id="owner")
    if not enabled:
        assert await provider.list_tools(owner) == []
        assert exploration_search.build_exploration_reuse_context("owner") == ""
        invocation = SimpleNamespace(tool_name="search_exploration_products", arguments={"keywords": ["python"]})
        before = saved_products.read_bytes()
        result = await provider.invoke(invocation, owner)
        assert not result.success
        assert saved_products.read_bytes() == before
        return
    assert "search_exploration_products" in {spec.name for spec in await provider.list_tools(owner)}
    assert await provider.list_tools(SimpleNamespace(session_id="group")) == []
    invocation = SimpleNamespace(tool_name="search_exploration_products", arguments={"keywords": ["python"]})
    result = await provider.invoke(invocation, owner)
    assert result.success and result.structured_content["total"] == 3
    denied = await provider.invoke(invocation, SimpleNamespace(session_id="group"))
    assert not denied.success
