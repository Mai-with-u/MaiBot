from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import json

import pytest
from pydantic import ValidationError

from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.webui.services.ai_search_market import AISearchMarketStore, build_market_tools, service
from src.webui.services.ai_search_models import AISearchCandidate, AISearchRequest


@pytest.fixture
def plugin():
    return {
        "id": "test.voice",
        "name": "Voice",
        "version": "2.0.0",
        "description": "语音插件",
        "manifest": {
            "host_application": {"min_version": "1.4.0"},
            "sdk": {"min_version": "2.20.0"},
            "dependencies": ["test.audio"],
            "capabilities": ["send"],
        },
    }


@pytest.fixture
def detail(plugin):
    return {
        "plugin": deepcopy(plugin),
        "source": "official",
        "source_url": "https://market.example/plugins",
        "cache": "hit",
        "releases": {
            "mode": "releases",
            "recommended_version": "1.0.0",
            "sync_error": None,
            "versions": [
                {
                    "version": "2.0.0",
                    "compatible": False,
                    "reasons": ["SDK 版本不匹配"],
                    "manifest": plugin["manifest"],
                },
                {"version": "1.0.0", "compatible": True, "reasons": [], "manifest": plugin["manifest"]},
            ],
        },
    }


@pytest.mark.asyncio
async def test_search_detail_reuses_service_and_filters_fabricated_ids(monkeypatch, plugin, detail):
    search = AsyncMock(
        return_value={
            "plugins": [plugin],
            "matched": 11,
            "page": 2,
            "pagination": {"has_next": True},
            "source": "official",
            "source_url": "https://market.example/plugins",
            "cache": "hit",
        }
    )
    get_detail = AsyncMock(return_value=detail)
    monkeypatch.setattr(service, "search", search)
    monkeypatch.setattr(service, "get_detail", get_detail)
    store = AISearchMarketStore()

    result = await store.execute("search_plugins", {"query": "语音", "page": 2, "limit": 3, "compatible_only": True})
    assert result["pagination"]["has_next"] is True
    assert result["plugins"][0]["compatibility"] == "unknown"
    assert search.call_args.args[0] == service.MarketSearch(query="语音", page=2, limit=3, compatible_only=True)

    result = await store.execute("read_plugin_detail", {"plugin_id": "test.voice"})
    get_detail.assert_awaited_once_with("test.voice")
    assert result["plugin"]["recommended_version"] == "1.0.0"
    assert result["plugin"]["compatibility"] == "compatible"
    assert result["requirements"]["dependencies"] == ["test.audio"]
    assert result["releases"]["versions"][0]["requirements"]["sdk"] == {"min_version": "2.20.0"}
    assert "manifest" not in result["releases"]["versions"][0]
    assert detail["releases"]["versions"][0]["manifest"]  # 不修改共用服务返回的对象
    assert [card.id for card in store.select_plugins(["fabricated", "test.voice", "test.voice"])] == ["test.voice"]
    assert store.sources[result["source_id"]].url == "/plugins?pluginId=test.voice"
    assert AISearchMarketStore().select_plugins(["test.voice"]) == []
    assert store.used is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["branch", "releases"])
async def test_incompatible_result_preserves_service_reasons(monkeypatch, detail, mode):
    detail["releases"].update(
        mode=mode, recommended_version=None, branch_compatible=False, branch_reasons=["Host 版本不匹配"]
    )
    detail["releases"]["versions"] = detail["releases"]["versions"][:1]
    monkeypatch.setattr(service, "get_detail", AsyncMock(return_value=detail))
    result = await AISearchMarketStore().execute("read_plugin_detail", {"plugin_id": "test.voice"})
    assert result["plugin"]["compatibility"] == "incompatible"
    assert result["plugin"]["reasons"] == ["Host 版本不匹配" if mode == "branch" else "SDK 版本不匹配"]


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [True, False])
async def test_readme_preserves_origin_and_truncation_without_exposing_local_paths(monkeypatch, local):
    readme = AsyncMock(
        return_value={
            "plugin": {"id": "test.voice", "name": "Voice"},
            "content": "设置 voice.enabled",
            "chars": 9000,
            "truncated": True,
            "source_type": "local" if local else "remote",
            "source": "C:/private/plugins/voice" if local else "https://example.com/v1/README.md",
        }
    )
    monkeypatch.setattr(service, "get_readme", readme)
    store = AISearchMarketStore()
    result = await store.execute("read_plugin_readme", {"plugin_id": "test.voice", "branch": "v1"})
    readme.assert_awaited_once_with(plugin_id="test.voice", branch="v1", prefer_local=True, max_chars=8000)
    assert result["content"] == "设置 voice.enabled"
    assert result["truncated"] is True
    assert result["branch"] == (None if local else "v1")
    assert "C:/private" not in str(result)
    assert store.sources[result["source_id"]].url == (
        "/plugins?pluginId=test.voice" if local else "https://example.com/v1/README.md"
    )
    assert store.select_plugins(["test.voice"])[0].compatibility == "unknown"


@pytest.mark.asyncio
async def test_readme_failure_does_not_create_evidence_or_switch_branches(monkeypatch):
    readme = AsyncMock(side_effect=ValueError("分支不存在"))
    monkeypatch.setattr(service, "get_readme", readme)
    store = AISearchMarketStore()
    with pytest.raises(ValueError, match="分支不存在"):
        await store.execute("read_plugin_readme", {"plugin_id": "test.voice", "branch": "v1"})
    assert readme.await_count == 1
    assert store.sources == {}
    assert store.plugins == {}


@pytest.mark.asyncio
async def test_lookup_checks_only_two_matches_and_reads_verified_compatible_release(monkeypatch, plugin, detail):
    second = {**plugin, "id": "test.voice_alt", "name": "Voice Alt"}
    search = AsyncMock(
        return_value={
            "plugins": [plugin, second],
            "matched": 3,
            "page": 1,
            "pagination": {"has_next": True},
            "source": "official",
            "source_url": "https://market.example/plugins",
            "cache": "hit",
        }
    )
    first_detail = deepcopy(detail)
    first_detail["releases"].update(recommended_version=None, versions=detail["releases"]["versions"][:1])
    second_detail = deepcopy(detail)
    second_detail["plugin"] = second
    second_detail["releases"]["versions"][1]["tag"] = "v1.0.0"
    get_detail = AsyncMock(side_effect=[first_detail, second_detail])
    readme = AsyncMock(
        return_value={
            "plugin": {"id": second["id"], "name": second["name"]},
            "content": "启用 voice.enabled",
            "chars": 5000,
            "truncated": True,
            "source_type": "remote",
            "source": "https://example.com/v1.0.0/README.md",
        }
    )
    monkeypatch.setattr(service, "search", search)
    monkeypatch.setattr(service, "get_detail", get_detail)
    monkeypatch.setattr(service, "get_readme", readme)
    store = AISearchMarketStore()
    result = await store.execute("lookup_plugins", {"query": " 语音 ", "include_readme": True})

    search.assert_awaited_once_with(service.MarketSearch(query="语音", limit=2))
    assert [call.args[0] for call in get_detail.await_args_list] == [plugin["id"], second["id"]]
    assert [(item["id"], item["compatibility"]) for item in result["plugins"]] == [
        (plugin["id"], "incompatible"),
        (second["id"], "compatible"),
    ]
    assert result["plugins"][1]["recommended_version"] == "1.0.0"
    readme.assert_awaited_once_with(plugin_id=second["id"], branch="v1.0.0", prefer_local=True, max_chars=4000)
    assert result["readme"]["source_id"] == f"plugin:readme:{second['id']}"
    assert result["readme"]["truncated"] is True
    assert store.sources[result["readme"]["source_id"]].url == "https://example.com/v1.0.0/README.md"
    assert not store.install_requests


@pytest.mark.asyncio
async def test_lookup_keeps_partial_evidence_when_detail_and_readme_fail(monkeypatch, plugin):
    search = AsyncMock(
        return_value={
            "plugins": [plugin],
            "matched": 1,
            "page": 1,
            "pagination": {"has_next": False},
            "source": "official",
            "source_url": "https://market.example/plugins",
            "cache": "hit",
        }
    )
    readme = AsyncMock(side_effect=ValueError("README 不可用"))
    monkeypatch.setattr(service, "search", search)
    monkeypatch.setattr(service, "get_detail", AsyncMock(side_effect=ValueError("详情不可用")))
    monkeypatch.setattr(service, "get_readme", readme)
    store = AISearchMarketStore()
    result = await store.execute("lookup_plugins", {"query": "语音", "include_readme": True})

    readme.assert_awaited_once_with(plugin_id=plugin["id"], branch="main", prefer_local=True, max_chars=4000)
    assert result["plugins"][0]["compatibility"] == "unknown"
    assert result["plugins"][0]["detail_error"] == "详情不可用"
    assert result["readme_error"] == "README 不可用"
    assert list(store.sources) == [f"plugin:detail:{plugin['id']}"]


@pytest.mark.asyncio
async def test_invalid_arguments_are_rejected_before_service_call(monkeypatch):
    search = AsyncMock()
    monkeypatch.setattr(service, "search", search)
    with pytest.raises(ValidationError):
        await AISearchMarketStore().execute("search_plugins", {"limit": 100})
    with pytest.raises(ValidationError):
        await AISearchMarketStore().execute("lookup_plugins", {"query": " "})
    search.assert_not_awaited()
    assert {tool["name"] for tool in build_market_tools()} == {
        "lookup_plugins",
        "search_plugins",
        "read_plugin_detail",
        "read_plugin_readme",
        "prepare_plugin_install",
        "list_installed_plugins",
        "read_plugin_config",
        "update_plugin_config",
    }


@pytest.fixture
def agent_module(monkeypatch):
    # 使用内存中的最小有效配置，避免 Agent 导入阶段依赖用户的模型设置。
    from src.config.config import Config, ModelConfig, config_manager
    from src.config.model_configs import APIProvider, ModelInfo

    monkeypatch.setattr(config_manager, "global_config", Config())
    monkeypatch.setattr(
        config_manager,
        "model_config",
        ModelConfig(
            models=[ModelInfo(model_type="llm", name="test", model_identifier="test", api_provider="test")],
            api_providers=[APIProvider(name="test", base_url="https://example.test", api_key="test")],
        ),
    )
    from src.webui.services import ai_search_agent

    return ai_search_agent


@pytest.mark.asyncio
async def test_agent_returns_grounded_plugin_cards_sources_and_progress(monkeypatch, agent_module, detail):
    from src.llm_models.payload_content.context_item import ContextToolCall, build_portable_output_items

    monkeypatch.setattr(service, "get_detail", AsyncMock(return_value=detail))
    monkeypatch.setattr(
        service,
        "get_readme",
        AsyncMock(
            return_value={
                "plugin": {"id": "test.voice", "name": "Voice"},
                "content": "启用 voice.enabled",
                "chars": 18,
                "truncated": False,
                "source_type": "local",
                "source": "C:/private/plugins/voice",
            }
        ),
    )
    model_output = {
        "answer": "启用 `voice.enabled`。",
        "plugin_ids": ["fake", "test.voice", "test.voice"],
        "source_ids": ["plugin:detail:test.voice", "plugin:readme:test.voice", "fake-source"],
    }
    calls = [
        ContextToolCall.create(call_id=name, func_name=name, args={"plugin_id": "test.voice"})
        for name in ("read_plugin_detail", "read_plugin_readme")
    ]
    responses = [
        LLMResponseResult(
            model_name="test",
            total_tokens=2,
            output_items=build_portable_output_items(
                logical_turn_id="activate",
                tool_calls=[
                    ContextToolCall.create(call_id="load", func_name="load_help_skill", args={"skill": "plugin"}),
                ],
            ),
        ),
        LLMResponseResult(
            model_name="test",
            total_tokens=10,
            output_items=build_portable_output_items(
                logical_turn_id="tools",
                tool_calls=calls,
            ),
        ),
        LLMResponseResult(
            model_name="test",
            total_tokens=5,
            output_items=build_portable_output_items(
                logical_turn_id="answer",
                content=json.dumps(model_output),
            ),
        ),
    ]
    model = SimpleNamespace(generate_response_with_context=AsyncMock(side_effect=responses))
    monkeypatch.setattr(agent_module, "_get_ai_search_model", lambda: model)
    download_docs = AsyncMock()
    monkeypatch.setattr(agent_module._document_store, "_download_official_docs", download_docs)
    progress = []

    async def record(event):
        progress.append(event)

    result = await agent_module.run_ai_search_agent(
        AISearchRequest(
            query="语音插件怎么用",
            candidates=[AISearchCandidate(id="c0", title="插件商店")],
        ),
        record,
    )

    assert result.answer == "启用 `voice.enabled`。"
    assert result.grounding_error == ""
    download_docs.assert_not_awaited()
    assert result.used_plugin_market is True
    assert result.total_tokens == 17
    assert [card.id for card in result.plugins] == ["test.voice"]
    assert result.plugins[0].recommended_version == "1.0.0"
    assert len(result.sources) == 2
    assert all(source.url == "/plugins?pluginId=test.voice" for source in result.sources)
    completed_reads = [
        event
        for event in progress
        if event.status == "completed" and event.tool in {"read_plugin_detail", "read_plugin_readme"}
    ]
    assert [event.tool for event in completed_reads] == [
        "read_plugin_detail",
        "read_plugin_readme",
    ]
    assert all(event.count == 1 for event in completed_reads)
