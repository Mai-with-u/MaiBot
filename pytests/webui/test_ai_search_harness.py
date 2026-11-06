from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio
import json

import pytest

from src.llm_models.payload_content.context_item import (
    ContextToolCall,
    FunctionCallOutputItem,
    SystemMessageItem,
    UserMessageItem,
    build_portable_output_items,
    get_item_text,
)
from src.webui.services import ai_search_tools
from src.webui.services.ai_search_grounding import AISearchGroundingError, validate_model_output_evidence
from src.webui.services.ai_search_models import AISearchCandidate, AISearchModelOutput, AISearchRequest
from src.webui.services.ai_search_tools import HELP_LOOKUP_MAX_CHARS_PER_DOC


@pytest.fixture
def agent_module(monkeypatch):
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


@pytest.fixture
def scripted_model(monkeypatch, agent_module):
    def configure(call_rounds, final_output):
        requests = []
        responses = iter(
            [
                *(
                    agent_module.LLMResponseResult(
                        output_items=build_portable_output_items(
                            logical_turn_id=f"tools-{index}", reasoning="preserve reasoning", tool_calls=calls
                        ),
                    )
                    for index, calls in enumerate(call_rounds)
                ),
                agent_module.LLMResponseResult(
                    output_items=build_portable_output_items(
                        logical_turn_id="answer", content=json.dumps(final_output)
                    ),
                ),
            ]
        )

        async def generate(build_context, *, options):
            requests.append((build_context(None), options.tool_options))
            return next(responses)

        model = SimpleNamespace(generate_response_with_context=generate)
        monkeypatch.setattr(agent_module, "_get_ai_search_model", lambda: model)
        return requests

    return configure


@pytest.mark.asyncio
async def test_help_diagnostics_and_artifacts_are_scoped_to_the_session(monkeypatch, agent_module, scripted_model):
    image_url = "https://example.org/diagram.png"
    record_image = "/api/webui/reasoning-process/image?path=cache%2Ftrace.png"
    record_path = "planner/session/older.json"
    read = AsyncMock(return_value={"record": {"stage": "planner"}})
    monkeypatch.setattr(ai_search_tools, "execute_diagnostic_tool", read)

    def call(call_id, name, **arguments):
        return ContextToolCall.create(call_id=call_id, func_name=name, args=arguments)

    requests = scripted_model(
        [
            [
                call("activate", "load_help_skill", skill="troubleshoot"),
                call("early", "read_recent_logs"),
            ],
            [
                call("record", "read_reasoning_record", record_id="record"),
                call(
                    "unknown",
                    "publish_help_artifact",
                    kind="image",
                    title="Unknown",
                    content="https://example.org/other.png",
                ),
                call("given", "publish_help_artifact", kind="image", title="Provided", content=image_url),
                call("trace-image", "publish_help_artifact", kind="image", title="Trace", content=record_image),
            ],
            [
                call("guessed-path", "read_reasoning_record", record_path="planner/session/guessed.json"),
                call("supplied-path", "read_reasoning_record", record_path=record_path),
            ],
        ],
        {"answer": "Finished"},
    )

    result = await agent_module.run_ai_search_agent(
        AISearchRequest(
            query=(
                f"Show {image_url} and {record_image}; "
                "read /api/webui/reasoning-process/file?path=planner%2Fsession%2Folder.json"
            ),
            candidates=[AISearchCandidate(id="help", title="Help")],
            help_session_id="new",
        )
    )

    first_tools = {tool["name"] for tool in requests[0][1]}
    assert "publish_help_artifact" in first_tools
    assert "read_recent_logs" not in first_tools
    assert "read_recent_logs" in {tool["name"] for tool in requests[1][1]}
    assert [entry.args for entry in read.await_args_list] == [
        ("read_reasoning_record", {"record_id": "record"}),
        ("read_reasoning_record", {"record_path": record_path}),
    ]
    outputs = {
        item.call_id: json.loads(item.output) for item in requests[3][0] if isinstance(item, FunctionCallOutputItem)
    }
    assert "error" in outputs["early"]
    assert "error" in outputs["unknown"]
    assert "error" in outputs["guessed-path"]
    assert outputs["trace-image"] == {"published": "image"}
    assert [artifact.content for artifact in result.artifacts] == [image_url, record_image]


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [False, True])
async def test_config_write_requires_per_turn_capability(monkeypatch, agent_module, scripted_model, allowed):
    arguments = {"plugin_id": "voice", "changes": [{"path": ["general", "enabled"], "value": True}]}
    activate = ContextToolCall.create(call_id="activate", func_name="load_help_skill", args={"skill": "plugin"})
    premature = ContextToolCall.create(call_id="premature", func_name="update_plugin_config", args=arguments)
    call = ContextToolCall.create(call_id="write", func_name="update_plugin_config", args=arguments)
    execute = AsyncMock(return_value={"message": "saved"})
    monkeypatch.setattr(agent_module.AISearchMarketStore, "execute", execute)
    requests = scripted_model([[activate, premature], [call]], {"answer": "Finished"})

    await agent_module.run_ai_search_agent(
        AISearchRequest(
            query="Enable the voice plugin",
            candidates=[AISearchCandidate(id="plugins", title="Plugins")],
            allow_plugin_config_write=allowed,
        ),
    )

    initial_tools = {tool["name"] for tool in requests[0][1]}
    assert "load_help_skill" in initial_tools
    assert "publish_help_artifact" not in initial_tools
    assert {"read_plugin_config", "update_plugin_config"}.isdisjoint(initial_tools)
    active_tools = {tool["name"] for tool in requests[1][1]}
    assert "read_plugin_config" in active_tools
    assert ("update_plugin_config" in active_tools) is allowed
    outputs = {
        item.call_id: json.loads(item.output) for item in requests[2][0] if isinstance(item, FunctionCallOutputItem)
    }
    assert "error" in outputs["premature"]
    assert "error" not in outputs["activate"]
    if allowed:
        execute.assert_awaited_once_with("update_plugin_config", arguments)
        assert outputs["write"] == {"message": "saved"}
    else:
        execute.assert_not_awaited()
        assert "error" in outputs["write"]


@pytest.mark.asyncio
async def test_read_calls_run_concurrently_and_keep_ordered_paired_outputs(monkeypatch, agent_module, scripted_model):
    monkeypatch.setattr(agent_module, "AI_SEARCH_MAX_TOOL_CALLS_PER_ROUND", 2)
    calls = [
        ContextToolCall.create(call_id="slow", func_name="lookup_help", args={"query": "slow docs"}),
        ContextToolCall.create(call_id="fast", func_name="lookup_help", args={"query": "fast docs"}),
        ContextToolCall.create(call_id="overflow", func_name="lookup_help", args={"query": "skip"}),
    ]
    reads_finished = []
    started = asyncio.Event()
    finished = asyncio.Event()

    async def search_docs(query, limit):
        assert limit == 2
        if query == "slow docs":
            started.set()
            await asyncio.wait_for(finished.wait(), timeout=5)
            reads_finished.append("slow")
            return [{"path": "/slow", "title": "Documentation", "snippet_offset": 0}]

        assert query == "fast docs"
        await asyncio.wait_for(started.wait(), timeout=5)
        reads_finished.append("fast")
        finished.set()
        return [{"path": "/fast", "title": "Page", "snippet_offset": 0}]

    async def read_docs(paths, _offset=0):
        [path] = paths
        return [
            {
                "title": "Documentation" if path == "/slow" else "Page",
                "source_id": path,
                "offset": _offset,
                "content": "Relevant passage.",
            }
        ]

    monkeypatch.setattr(agent_module._document_store, "search_official_docs", search_docs)
    monkeypatch.setattr(agent_module._document_store, "read_official_docs", read_docs)
    requests = scripted_model([calls], {"answer": "Finished"})

    await agent_module.run_ai_search_agent(
        AISearchRequest(query="Help", candidates=[AISearchCandidate(id="help", title="Help")]),
    )

    assert reads_finished == ["fast", "slow"]
    results = [item for item in requests[1][0] if isinstance(item, FunctionCallOutputItem)]
    assert [(item.call_id, item.meta.logical_turn_id) for item in results] == [
        ("slow", "tools-0"),
        ("fast", "tools-0"),
        ("overflow", "tools-0"),
    ]
    assert json.loads(results[0].output)["documents"][0]["title"] == "Documentation"
    assert json.loads(results[1].output)["documents"][0]["title"] == "Page"
    assert "error" in json.loads(results[2].output)


@pytest.mark.asyncio
async def test_lookup_help_returns_citable_passage_and_observed_navigation(monkeypatch, agent_module, scripted_model):
    search_docs = AsyncMock(return_value=[{"path": "/guide", "title": "Guide", "snippet_offset": 24}])
    passage = "Set `bot.enabled` to true.\n" + "x" * 4000
    read_docs = AsyncMock(return_value=[{"title": "Guide", "source_id": "/guide", "offset": 24, "content": passage}])
    monkeypatch.setattr(agent_module._document_store, "search_official_docs", search_docs)
    monkeypatch.setattr(agent_module._document_store, "read_official_docs", read_docs)
    monkeypatch.setattr(
        agent_module._document_store,
        "build_sources",
        Mock(return_value=[{"title": "Guide", "url": "https://docs.mai-mai.org/guide"}]),
    )
    call = ContextToolCall.create(call_id="lookup", func_name="lookup_help", args={"query": "bot"})
    requests = scripted_model(
        [[call]],
        {
            "answer": "Set `bot.enabled` to true.",
            "results": [{"id": "bot", "reason": "Related settings"}],
            "source_ids": ["/guide", "/unread"],
        },
    )

    result = await agent_module.run_ai_search_agent(
        AISearchRequest(query="How do I enable the bot?", candidates=[AISearchCandidate(id="bot", title="Bot")])
    )

    assert len(requests) == 2
    search_docs.assert_awaited_once_with("bot", limit=2)
    read_docs.assert_awaited_once_with(["/guide"], 24)
    [lookup_output] = [item for item in requests[1][0] if isinstance(item, FunctionCallOutputItem)]
    [document] = json.loads(lookup_output.output)["documents"]
    assert document["content"] == passage[:HELP_LOOKUP_MAX_CHARS_PER_DOC]
    assert document["next_offset"] == 24 + HELP_LOOKUP_MAX_CHARS_PER_DOC
    agent_module._document_store.build_sources.assert_called_once_with(["/guide"])
    assert result.answer == "Set `bot.enabled` to true."
    assert [item.id for item in result.results] == ["bot"]
    assert [source.title for source in result.sources] == ["Guide"]


@pytest.mark.asyncio
async def test_lookup_help_cites_relevant_reference_even_if_bundle_is_unavailable(
    monkeypatch, agent_module, scripted_model
):
    monkeypatch.setattr(
        agent_module._document_store, "search_official_docs", AsyncMock(side_effect=ValueError("empty bundle"))
    )
    call = ContextToolCall.create(call_id="lookup", func_name="lookup_help", args={"query": "Ollama"})
    answer = "Use `http://localhost:11434/v1/`."
    scripted_model([[call]], {"answer": answer, "source_ids": ["help:ollama", "help:nginx-websocket"]})

    result = await agent_module.run_ai_search_agent(
        AISearchRequest(query="Configure Ollama", candidates=[AISearchCandidate(id="model", title="Model")])
    )

    assert result.answer == answer
    assert [source.url for source in result.sources] == ["https://docs.ollama.com/api/openai-compatibility"]


def test_plugin_dependency_requires_matching_name_and_version_in_the_same_evidence_record():
    evidence = '{"dependencies":[{"name":"aiohttp","version_spec":">=3.9.0"},{"name":"httpx","version_spec":">=0.27"}]}'
    validate_model_output_evidence(AISearchModelOutput(answer="Needs `aiohttp >= 3.9.0`."), evidence)
    with pytest.raises(AISearchGroundingError, match="aiohttp >= 0.27"):
        validate_model_output_evidence(AISearchModelOutput(answer="Needs `aiohttp >= 0.27`."), evidence)


@pytest.mark.asyncio
async def test_preloaded_reference_supports_answer_in_one_model_call(monkeypatch, agent_module, scripted_model):
    search_docs = AsyncMock()
    monkeypatch.setattr(agent_module._document_store, "search_official_docs", search_docs)
    answer = "Use `http://localhost:11434/v1/`."
    requests = scripted_model([], {"answer": answer, "source_ids": ["help:ollama"]})

    result = await agent_module.run_ai_search_agent(
        AISearchRequest(query="Configure Ollama", candidates=[AISearchCandidate(id="model", title="Model")])
    )

    assert len(requests) == 1
    search_docs.assert_not_awaited()
    assert result.answer == answer
    assert [source.url for source in result.sources] == ["https://docs.ollama.com/api/openai-compatibility"]


@pytest.mark.asyncio
async def test_malformed_core_tool_arguments_fail_without_running_the_tool(monkeypatch, agent_module, scripted_model):
    search_docs = AsyncMock()
    monkeypatch.setattr(agent_module._document_store, "search_official_docs", search_docs)
    call = ContextToolCall.create(call_id="lookup", func_name="lookup_help", args={"query": 42})
    requests = scripted_model([[call]], {"answer": "Finished"})

    await agent_module.run_ai_search_agent(
        AISearchRequest(query="Help", candidates=[AISearchCandidate(id="help", title="Help")])
    )

    [output] = [item for item in requests[1][0] if isinstance(item, FunctionCallOutputItem)]
    assert "参数无效" in json.loads(output.output)["error"]
    search_docs.assert_not_awaited()


@pytest.mark.asyncio
async def test_identical_reads_reuse_original_call_without_duplicating_evidence(
    monkeypatch, agent_module, scripted_model
):
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(agent_module._document_store, "search_official_docs", search)

    def lookup(call_id):
        return ContextToolCall.create(call_id=call_id, func_name="lookup_help", args={"query": "bot"})

    requests = scripted_model([[lookup("original"), lookup("same-batch")], [lookup("next-round")]], {"answer": "Done"})
    await agent_module.run_ai_search_agent(
        AISearchRequest(query="Bot", candidates=[AISearchCandidate(id="bot", title="Bot")])
    )

    search.assert_awaited_once_with("bot", limit=2)
    outputs = {
        item.call_id: json.loads(item.output) for item in requests[-1][0] if isinstance(item, FunctionCallOutputItem)
    }
    assert [item["id"] for item in outputs["original"]["navigation"]] == ["bot"]
    for call_id in ("same-batch", "next-round"):
        assert outputs[call_id]["reused_call_id"] == "original"
        assert "navigation" not in outputs[call_id]


@pytest.mark.asyncio
async def test_skills_load_on_demand_within_budget_and_emit_validated_plan(agent_module, scripted_model):
    first_step = {"title": "Identify the failing component", "status": "in_progress"}
    second_step = {"title": "Find the supported fix", "status": "pending"}
    requests = scripted_model(
        [
            [
                ContextToolCall.create(call_id="setup", func_name="load_help_skill", args={"skill": "setup"}),
                ContextToolCall.create(
                    call_id="troubleshoot", func_name="load_help_skill", args={"skill": "troubleshoot"}
                ),
                ContextToolCall.create(call_id="excess", func_name="load_help_skill", args={"skill": "plugin"}),
            ],
            [
                ContextToolCall.create(
                    call_id="invalid-plan",
                    func_name="update_help_plan",
                    args={"steps": [first_step, {**second_step, "status": "in_progress"}]},
                ),
                ContextToolCall.create(
                    call_id="plan", func_name="update_help_plan", args={"steps": [first_step, second_step]}
                ),
            ],
        ],
        {"answer": "Finished"},
    )
    events = []

    async def progress(event):
        events.append(event)

    await agent_module.run_ai_search_agent(
        AISearchRequest(query="Help", candidates=[AISearchCandidate(id="help", title="Help")]), progress
    )

    outputs = {
        item.call_id: json.loads(item.output) for item in requests[-1][0] if isinstance(item, FunctionCallOutputItem)
    }
    assert outputs["setup"]["instructions"]
    assert outputs["troubleshoot"]["instructions"]
    assert "error" in outputs["excess"]
    assert "error" in outputs["invalid-plan"]
    assert "error" not in outputs["plan"]
    assert "search_plugins" not in {tool["name"] for tool in requests[-1][1]}
    assert [event.skill for event in events if event.stage == "skill"] == ["setup", "troubleshoot"]
    [plan_event] = [event for event in events if event.stage == "plan"]
    assert [step.model_dump() for step in plan_event.plan] == [first_step, second_step]


@pytest.mark.asyncio
async def test_policy_excludes_untrusted_text_and_results_require_observed_candidate(
    monkeypatch, agent_module, scripted_model
):
    call = ContextToolCall.create(call_id="index", func_name="search_webui_index", args={"query": "actual"})
    monkeypatch.setattr(
        agent_module._document_store, "search_candidates", Mock(return_value=[{"id": "actual", "title": "Actual"}])
    )
    requests = scripted_model(
        [[call]],
        {
            "answer": "Finished",
            "results": [{"id": item_id, "reason": "Related"} for item_id in ("actual", "unseen", "invented")],
        },
    )

    result = await agent_module.run_ai_search_agent(
        AISearchRequest(
            query="query-marker",
            candidates=[AISearchCandidate(id=item_id, title=item_id) for item_id in ("actual", "unseen")],
        ),
        history=[("history-marker", "old answer")],
    )

    [policy, user_input] = requests[0][0]
    assert isinstance(policy, SystemMessageItem)
    assert isinstance(user_input, UserMessageItem)
    for untrusted_text in ("query-marker", "history-marker"):
        assert untrusted_text not in get_item_text(policy)
        assert untrusted_text in get_item_text(user_input)
    assert [item.id for item in result.results] == ["actual"]


@pytest.mark.asyncio
async def test_reused_call_id_is_rejected_before_plugin_write(monkeypatch, agent_module):
    read = ContextToolCall.create(call_id="reused", func_name="search_webui_index", args={"query": "voice"})
    write = ContextToolCall.create(call_id="reused", func_name="update_plugin_config", args={"plugin_id": "voice"})
    responses = [
        agent_module.LLMResponseResult(
            output_items=build_portable_output_items(logical_turn_id=turn, tool_calls=[call])
        )
        for turn, call in (("read", read), ("write", write))
    ]
    model = SimpleNamespace(generate_response_with_context=AsyncMock(side_effect=responses))
    monkeypatch.setattr(agent_module, "_get_ai_search_model", lambda: model)
    execute = AsyncMock()
    monkeypatch.setattr(agent_module.AISearchMarketStore, "execute", execute)

    with pytest.raises(agent_module.AISearchOutputError, match="重复 function call"):
        await agent_module.run_ai_search_agent(
            AISearchRequest(
                query="Enable voice",
                candidates=[AISearchCandidate(id="voice", title="Voice")],
                allow_plugin_config_write=True,
            )
        )

    execute.assert_not_awaited()
