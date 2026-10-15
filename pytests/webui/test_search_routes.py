from typing import List
import json

import pytest

from src.common.data_models.llm_service_data_models import LLMGenerationOptions, LLMResponseResult
from src.llm_models.payload_content.context_item import (
    ContextItem,
    FunctionCallItem,
    FunctionCallOutputItem,
    get_item_text,
)
from src.config.config import Config
from src.llm_models.payload_content.tool_option import ToolCall
from src.webui.routers import search as search_router
from src.webui.services import ai_search_agent as search_agent
from src.webui.services import ai_search_documents as search_documents
from src.webui.services import ai_search_grounding as search_grounding
from src.webui.services import ai_search_local_config as search_local_config
from src.webui.services.ai_search_documents import AISearchDocumentStore, OfficialDocument
from src.webui.services.ai_search_models import AISearchModelOutput, AISearchResponse


@pytest.fixture(autouse=True)
def disable_official_docs_prewarm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent 启动时会在后台预热官方文档包，测试中不应发起真实下载。"""

    monkeypatch.setattr(search_agent._document_store, "prewarm_official_docs", lambda: None)


class FakeSearchModel:
    def __init__(self) -> None:
        self.calls: List[tuple[List[ContextItem], LLMGenerationOptions]] = []

    async def generate_response_with_context(
        self,
        context_factory,
        options: LLMGenerationOptions,
    ) -> LLMResponseResult:
        messages = context_factory(None)
        self.calls.append((messages, options))

        if len(self.calls) == 1:
            return LLMResponseResult.from_portable_output(
                tool_calls=[
                    ToolCall(
                        call_id="read-docs",
                        func_name="read_official_docs",
                        args={"paths": ["/manual/configuration/bot-config.md"]},
                    )
                ]
            )
        if len(self.calls) == 2:
            return LLMResponseResult.from_portable_output(response="已读取配置文档")
        return LLMResponseResult.from_portable_output(
            response=(
                '{"answer":"配置文件是 bot_config.toml","suggestions":[],"source_ids":[],'
                '"expanded_terms":[],"results":[]}'
            ),
            model_name="fake-model",
        )


def test_ai_search_document_store_searches_candidates_with_content() -> None:
    store = AISearchDocumentStore()
    candidates = [
        search_router.AISearchCandidate(
            id="emoji",
            title="表情配置",
            description="管理表情包发送",
            category="配置",
            document="emoji.emoji_send_num",
        ),
        search_router.AISearchCandidate(
            id="reply",
            title="回复配置",
            description="管理回复频率",
            category="配置",
            document="chat.reply_timing.talk_value",
        ),
    ]

    matches = store.search_candidates("表情 emoji", candidates, 6)

    assert matches == [
        {
            "id": "emoji",
            "title": "表情配置",
            "category": "配置",
            "content": "emoji.emoji_send_num",
        }
    ]


@pytest.mark.asyncio
async def test_ai_search_document_store_searches_cached_official_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AISearchDocumentStore()
    documents = [
        OfficialDocument(
            path="/manual/emoji.md",
            title="表情包功能",
            content="emoji_send_num 控制候选表情数量",
        ),
        OfficialDocument(
            path="/manual/reply.md",
            title="回复设置",
            content="talk_value 控制发言频率",
        ),
    ]

    async def fake_load_official_docs():
        return documents

    monkeypatch.setattr(store, "_load_official_docs", fake_load_official_docs)

    matches = await store.search_official_docs("表情 emoji_send_num", 4)

    assert [match["path"] for match in matches] == ["/manual/emoji.md"]
    assert matches[0]["url"] == "https://docs.mai-mai.org/manual/emoji"


@pytest.mark.asyncio
async def test_final_ai_search_request_preserves_tool_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeSearchModel()

    async def fake_execute_agent_tool(
        tool_call: ToolCall,
        candidates,
        read_source_ids,
    ) -> dict:
        del tool_call, candidates, read_source_ids
        return {"content": "bot_config.toml 使用 [emoji] 段，不存在 config.yaml"}

    monkeypatch.setattr(search_agent, "_get_ai_search_model", lambda: model)
    monkeypatch.setattr(search_agent, "_execute_agent_tool", fake_execute_agent_tool)
    validation_calls: List[str] = []
    validate_model_output_evidence = search_grounding.validate_model_output_evidence

    def track_validation(model_output: AISearchModelOutput, evidence: str) -> None:
        validation_calls.append(model_output.answer)
        validate_model_output_evidence(model_output, evidence)

    monkeypatch.setattr(search_agent, "validate_model_output_evidence", track_validation)

    request = search_router.AISearchRequest(
        query="为什么无法发送表情包",
        candidates=[
            search_router.AISearchCandidate(
                id="emoji",
                title="表情配置",
                document="emoji.emoji_send_num",
            )
        ],
    )

    response = await search_agent.run_ai_search_agent(request)

    final_messages, final_options = model.calls[-1]
    assert response.model_name == "fake-model"
    assert "bot_config.toml" in response.answer
    assert validation_calls == [response.answer]
    assert final_options.temperature is None
    assert all(options.max_tokens is None for _, options in model.calls)
    assert final_options.tool_options is None
    assert all(not isinstance(message, FunctionCallOutputItem) for message in final_messages)
    assert all(not isinstance(message, FunctionCallItem) for message in final_messages)
    assert any("bot_config.toml" in get_item_text(message) for message in final_messages)
    assert any("config.yaml" in get_item_text(message) for message in final_messages)


@pytest.mark.asyncio
async def test_stream_ai_search_events_returns_progress_before_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_ai_search_request(request, progress_callback=None):
        del request
        assert progress_callback is not None
        await progress_callback(search_router.AISearchProgressEvent(stage="start"))
        await progress_callback(
            search_router.AISearchProgressEvent(
                stage="tool",
                status="started",
                tool="search_official_docs",
                query="表情包 发送失败",
            )
        )
        return search_router.AISearchResponse(answer="根据文档完成回答")

    monkeypatch.setattr(search_router, "_execute_ai_search_request", fake_execute_ai_search_request)
    request = search_router.AISearchRequest(
        query="为什么无法发送表情包",
        candidates=[
            search_router.AISearchCandidate(
                id="emoji",
                title="表情配置",
                document="emoji.emoji_send_num",
            )
        ],
    )

    events = [json.loads(line) async for line in search_router._stream_ai_search_events(request)]

    assert [event["type"] for event in events] == ["progress", "progress", "progress", "result"]
    assert events[1]["tool"] == "search_official_docs"
    assert events[1]["query"] == "表情包 发送失败"
    assert events[-2]["stage"] == "completed"
    assert events[-1]["response"]["answer"] == "根据文档完成回答"


@pytest.mark.asyncio
async def test_execute_ai_search_request_writes_compact_search_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_entries: List[tuple[str, str, dict]] = []

    class CapturingLogger:
        def info(self, event: str, **fields) -> None:
            log_entries.append(("info", event, fields))

        def debug(self, event: str, **fields) -> None:
            log_entries.append(("debug", event, fields))

    async def fake_run_ai_search_agent(request, progress_callback=None):
        del request
        assert progress_callback is not None
        await progress_callback(
            search_router.AISearchProgressEvent(
                stage="tool",
                status="completed",
                tool="search_official_docs",
                query="表情包",
                count=2,
            )
        )
        return AISearchResponse(model_name="fake-model", total_tokens=42, answer="根据文档完成回答")

    monkeypatch.setattr(search_router, "logger", CapturingLogger())
    monkeypatch.setattr(search_router, "_get_cached_response", lambda _cache_key: None)
    monkeypatch.setattr(search_router, "_cache_response", lambda _cache_key, _response: None)
    monkeypatch.setattr(search_router, "run_ai_search_agent", fake_run_ai_search_agent)

    request = search_router.AISearchRequest(
        query="为什么无法发送表情包",
        candidates=[
            search_router.AISearchCandidate(
                id="emoji",
                title="表情配置",
                document="emoji.emoji_send_num",
            )
        ],
    )

    async def ignore_progress(event: search_router.AISearchProgressEvent) -> None:
        del event

    response = await search_router._execute_ai_search_request(request, ignore_progress)

    assert response.answer == "根据文档完成回答"
    summary = next(fields for level, event, fields in log_entries if level == "info" and event == "WebUI AI 搜索记录")
    assert summary["status"] == "completed"
    assert summary["query"] == "为什么无法发送表情包"
    assert summary["candidate_count"] == 1
    assert summary["progress"] == [
        {
            "stage": "tool",
            "status": "completed",
            "tool": "search_official_docs",
            "query": "表情包",
            "count": 2,
        }
    ]
    detail = next(fields for level, event, fields in log_entries if level == "debug" and event == "WebUI AI 搜索回答")
    assert detail["answer"] == "根据文档完成回答"


@pytest.mark.asyncio
async def test_stream_ai_search_events_reports_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_ai_search_request(request, progress_callback=None):
        del request
        assert progress_callback is not None
        await progress_callback(search_router.AISearchProgressEvent(stage="finalizing"))
        raise search_router.HTTPException(
            status_code=502,
            detail="AI 搜索结果解析失败: 模型返回的 JSON 不完整",
        )

    monkeypatch.setattr(search_router, "_execute_ai_search_request", fake_execute_ai_search_request)
    request = search_router.AISearchRequest(
        query="麦麦说话太多",
        candidates=[
            search_router.AISearchCandidate(
                id="frequency",
                title="发言频率",
                document="talk_frequency",
            )
        ],
    )

    events = [json.loads(line) async for line in search_router._stream_ai_search_events(request)]

    assert [event["type"] for event in events] == ["progress", "progress", "error"]
    assert events[-2]["stage"] == "failed"
    assert events[-2]["status"] == "failed"
    assert events[-2]["error"] == "AI 搜索结果解析失败: 模型返回的 JSON 不完整"
    assert events[-1]["status"] == 502


def test_extract_model_output_identifies_truncated_json() -> None:
    with pytest.raises(ValueError, match="max_token"):
        search_agent._extract_model_output('{"answer":"回答尚未完成","suggestions":[')


def test_validate_model_output_evidence_rejects_hallucinated_config_field() -> None:
    model_output = AISearchModelOutput(
        answer=("在 `config/bot_config.toml` 的 `[chat]` 中设置 `reply_frequency_limit = 10`。"),
        suggestions=["启用 `[[keyword_reaction.keyword_rules]]`。"],
    )
    evidence = (
        '{"content":"config/bot_config.toml 使用 [chat.reply_timing]，群聊频率字段为 talk_value，范围为 0 到 1。"}'
    )

    with pytest.raises(search_grounding.AISearchGroundingError) as exc_info:
        search_grounding.validate_model_output_evidence(model_output, evidence)

    error_message = str(exc_info.value)
    assert "reply_frequency_limit = 10" in error_message
    assert "[[keyword_reaction.keyword_rules]]" in error_message


def test_validate_model_output_evidence_accepts_exact_config_claims() -> None:
    model_output = AISearchModelOutput(
        answer=("在 `config/bot_config.toml` 的 `[chat.reply_timing]` 中调低 `talk_value`，其范围为 `0` 到 `1`。"),
    )
    evidence = '{"content":"config/bot_config.toml\\n[chat.reply_timing]\\ntalk_value = 1.0，范围为 0 到 1。"}'

    search_grounding.validate_model_output_evidence(model_output, evidence)


def test_validate_model_output_evidence_accepts_documented_field_wildcard() -> None:
    model_output = AISearchModelOutput(
        answer="可检查 `no_action_backoff_*` 这一组空闲退避配置。",
    )
    evidence = (
        '{"content":"no_action_backoff_base_seconds、no_action_backoff_cap_seconds、no_action_backoff_start_count"}'
    )

    search_grounding.validate_model_output_evidence(model_output, evidence)


def test_validate_model_output_evidence_accepts_structured_assignment() -> None:
    model_output = AISearchModelOutput(
        answer="确认 `[emoji]` 下的 `emoji.steal_emoji = true`。",
    )
    evidence = '{"content":"[emoji]\\nsteal_emoji = true"}'

    search_grounding.validate_model_output_evidence(model_output, evidence)


@pytest.mark.parametrize(
    "claim, evidence",
    [
        ("[experimental] enable_rich_reply = true", "[experimental]\nenable_rich_reply = true"),
        ("[experimental]enable_rich_reply = true", "experimental.enable_rich_reply = true"),
        ("[chat.reply_timing] talk_value = 1", "[chat.reply_timing]\ntalk_value = 1"),
        ("experimental.enable_rich_reply = true", "[experimental] enable_rich_reply = true"),
    ],
)
def test_validate_model_output_evidence_accepts_section_prefixed_fields(claim: str, evidence: str) -> None:
    model_output = AISearchModelOutput(answer=f"设置 `{claim}`。")

    search_grounding.validate_model_output_evidence(model_output, evidence)


@pytest.mark.parametrize(
    "evidence",
    [
        "[chat]\nenable_rich_reply = true",
        "[experimental]\nenable_rich_reply = false",
        "[experimental]\nother_field = true",
    ],
)
def test_validate_model_output_evidence_rejects_unsupported_section_assignment(evidence: str) -> None:
    model_output = AISearchModelOutput(answer="设置 `[experimental] enable_rich_reply = true`。")

    with pytest.raises(search_grounding.AISearchGroundingError):
        search_grounding.validate_model_output_evidence(model_output, evidence)


def test_validate_model_output_evidence_normalizes_escaped_quoted_value() -> None:
    model_output = AISearchModelOutput(
        answer=r"将 `chat.reply_timing.reply_trigger_mode` 设为 `\"reply_necessity\"`。",
    )
    evidence = '{"content":"chat.reply_timing.reply_trigger_mode 支持 frequency 和 reply_necessity"}'

    claims = search_grounding.extract_verifiable_claims(model_output.answer)

    assert claims.count("reply_necessity") == 1
    assert r"\"reply_necessity\"" not in claims
    search_grounding.validate_model_output_evidence(model_output, evidence)


def test_validate_model_output_evidence_accepts_http_method_and_path() -> None:
    model_output = AISearchModelOutput(
        answer="登录页通过 `POST /api/webui/auth/verify` 验证 Token。",
    )
    evidence = '{"method":"POST","path":"/api/webui/auth/verify"}'

    search_grounding.validate_model_output_evidence(model_output, evidence)


def test_validate_model_output_evidence_accepts_verified_emoji_claims() -> None:
    model_output = AISearchModelOutput(
        answer=(
            "配置位于 `bot_config.toml`，可检查 `emoji.steal_emoji = true`；"
            "插件通过 `self.ctx.emoji` 调用表情能力，配置对象名为 `bot_config`。"
        ),
    )
    evidence = (
        '{"documents":[{"content":"bot_config.toml\\n[emoji]\\nsteal_emoji = true\\nself.ctx.emoji\\nbot_config"}]}'
    )

    search_grounding.validate_model_output_evidence(model_output, evidence)


@pytest.mark.asyncio
async def test_ai_search_uses_search_evidence_and_rewrites_once_after_grounding_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CorrectingSearchModel:
        def __init__(self) -> None:
            self.calls: List[tuple[List[ContextItem], LLMGenerationOptions]] = []

        async def generate_response_with_context(
            self,
            context_factory,
            options: LLMGenerationOptions,
        ) -> LLMResponseResult:
            messages = context_factory(None)
            self.calls.append((messages, options))
            if len(self.calls) == 1:
                return LLMResponseResult.from_portable_output(
                    tool_calls=[
                        ToolCall(
                            call_id="search-config",
                            func_name="search_webui_index",
                            args={"query": "空闲退避"},
                        )
                    ]
                )
            if len(self.calls) == 2:
                return LLMResponseResult.from_portable_output(response="资料读取完成")
            if len(self.calls) == 3:
                return LLMResponseResult.from_portable_output(
                    response=(
                        '{"answer":"调用 `POST /api/webui/auth/verify` 检查状态",'
                        '"suggestions":[],"source_ids":[],"expanded_terms":[],"results":[]}'
                    )
                )
            return LLMResponseResult.from_portable_output(
                response=(
                    '{"answer":"检查 `no_action_backoff_*` 相关配置",'
                    '"suggestions":[],"source_ids":[],"expanded_terms":[],"results":[]}'
                )
            )

    model = CorrectingSearchModel()
    progress_events: List[search_router.AISearchProgressEvent] = []

    async def fake_execute_agent_tool(
        tool_call: ToolCall,
        candidates,
        read_source_ids,
    ) -> dict:
        del tool_call, candidates, read_source_ids
        return {"content": "no_action_backoff_base_seconds 控制空闲退避基准"}

    async def capture_progress(event: search_router.AISearchProgressEvent) -> None:
        progress_events.append(event)

    monkeypatch.setattr(search_agent, "_get_ai_search_model", lambda: model)
    monkeypatch.setattr(search_agent, "_execute_agent_tool", fake_execute_agent_tool)
    request = search_router.AISearchRequest(
        query="麦麦为什么不说话",
        candidates=[
            search_router.AISearchCandidate(
                id="reply-timing",
                title="回复时机",
                document="no_action_backoff_base_seconds",
            )
        ],
    )

    validation_calls: List[str] = []
    validate_model_output_evidence = search_grounding.validate_model_output_evidence

    def track_validation(model_output: AISearchModelOutput, evidence: str) -> None:
        validation_calls.append(model_output.answer)
        validate_model_output_evidence(model_output, evidence)

    monkeypatch.setattr(search_agent, "validate_model_output_evidence", track_validation)

    response = await search_agent.run_ai_search_agent(request, capture_progress)

    assert len(model.calls) == 4
    assert "no_action_backoff_*" in response.answer
    assert response.grounding_error == ""
    assert len(validation_calls) == 2
    assert "POST /api/webui/auth/verify" in validation_calls[0]
    assert "no_action_backoff_*" in validation_calls[1]
    assert any(event.stage == "correcting" for event in progress_events)
    correction_prompt = get_item_text(model.calls[-1][0][-1])
    assert "POST /api/webui/auth/verify" in correction_prompt


class ScriptedSearchModel:
    """按预设顺序返回响应的假模型。"""

    def __init__(self, results: List[LLMResponseResult]) -> None:
        self.results = results
        self.calls: List[tuple[List[ContextItem], LLMGenerationOptions]] = []

    async def generate_response_with_context(
        self,
        context_factory,
        options: LLMGenerationOptions,
    ) -> LLMResponseResult:
        self.calls.append((context_factory(None), options))
        return self.results[len(self.calls) - 1]


@pytest.mark.asyncio
async def test_ai_search_uses_agent_final_json_without_extra_finalizing_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_answer = "回复时机页面可以调整发言频率。" * 300
    model = ScriptedSearchModel(
        [
            LLMResponseResult.from_portable_output(
                tool_calls=[
                    ToolCall(
                        call_id="search-config",
                        func_name="search_webui_index",
                        args={"query": "回复时机"},
                    )
                ]
            ),
            LLMResponseResult.from_portable_output(
                response=json.dumps(
                    {
                        "answer": long_answer,
                        "suggestions": [f"建议 {index}" for index in range(12)],
                        "results": [{"id": "reply-timing", "reason": "调整发言频率"}],
                    },
                    ensure_ascii=False,
                ),
                model_name="fake-model",
            ),
        ]
    )
    progress_events: List[search_router.AISearchProgressEvent] = []

    async def capture_progress(event: search_router.AISearchProgressEvent) -> None:
        progress_events.append(event)

    monkeypatch.setattr(search_agent, "_get_ai_search_model", lambda: model)
    request = search_router.AISearchRequest(
        query="麦麦说话太多",
        candidates=[
            search_router.AISearchCandidate(
                id="reply-timing",
                title="回复时机",
                document="chat.reply_timing.talk_value",
            )
        ],
    )

    response = await search_agent.run_ai_search_agent(request, capture_progress)

    assert len(model.calls) == 2
    assert all(event.stage != "finalizing" for event in progress_events)
    # 模型输出不做任何长度或数量截断
    assert response.answer == long_answer
    assert len(response.suggestions) == 12
    assert [result.id for result in response.results] == ["reply-timing"]
    tool_event = next(event for event in progress_events if event.stage == "tool" and event.status == "completed")
    assert tool_event.titles == ["回复时机"]


@pytest.mark.asyncio
async def test_ai_search_keeps_results_when_answer_fails_grounding_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ungrounded_output = json.dumps(
        {
            "answer": "设置 `reply_frequency_limit = 10`。",
            "suggestions": ["检查 `config.yaml`。"],
            "results": [{"id": "reply-timing", "reason": "调整发言频率"}],
        },
        ensure_ascii=False,
    )
    model = ScriptedSearchModel(
        [
            LLMResponseResult.from_portable_output(response=ungrounded_output),
            LLMResponseResult.from_portable_output(response=ungrounded_output),
        ]
    )
    progress_events: List[search_router.AISearchProgressEvent] = []

    async def capture_progress(event: search_router.AISearchProgressEvent) -> None:
        progress_events.append(event)

    monkeypatch.setattr(search_agent, "_get_ai_search_model", lambda: model)
    request = search_router.AISearchRequest(
        query="麦麦说话太多",
        candidates=[
            search_router.AISearchCandidate(
                id="reply-timing",
                title="回复时机",
                document="chat.reply_timing.talk_value",
            )
        ],
    )

    response = await search_agent.run_ai_search_agent(request, capture_progress)

    assert len(model.calls) == 2
    assert response.answer == ""
    assert response.suggestions == []
    assert "reply_frequency_limit = 10" in response.grounding_error
    assert [result.id for result in response.results] == ["reply-timing"]
    assert [event.status for event in progress_events if event.stage == "correcting"] == ["started", "failed"]


def test_redact_config_value_hides_secrets_but_keeps_ordinary_fields() -> None:
    provider = {
        "name": "openai",
        "base_url": "https://user:pass@api.example.com/v1?key=abc",
        "api_key": "sk-real-secret",
        "auth_token": "",
        "default_headers": {"X-Api-Key": "header-secret"},
        "default_query": {"access": "query-secret"},
        "field_docs": {"api_key": "API密钥"},
        "max_tokens": 8192,
        "keywords": ["表情"],
        "extra_params": {"enable_thinking": False, "api_key": "nested-secret"},
    }

    redacted = search_local_config.redact_config_value("api_providers", [provider])[0]

    assert "secret" not in json.dumps(redacted, ensure_ascii=False)
    assert redacted["api_key"] == search_local_config.LOCAL_CONFIG_REDACTED_VALUE
    assert redacted["default_headers"] == search_local_config.LOCAL_CONFIG_REDACTED_VALUE
    assert redacted["extra_params"] == {
        "enable_thinking": False,
        "api_key": search_local_config.LOCAL_CONFIG_REDACTED_VALUE,
    }
    assert redacted["default_query"] == search_local_config.LOCAL_CONFIG_REDACTED_VALUE
    assert "field_docs" not in redacted
    # 未填写的敏感字段保留空值，便于判断是否漏填
    assert redacted["auth_token"] == ""
    assert redacted["base_url"] == "https://api.example.com/v1"
    assert redacted["max_tokens"] == 8192
    assert redacted["keywords"] == ["表情"]


def test_read_local_config_returns_current_values_with_section_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    monkeypatch.setattr(search_local_config.config_manager, "get_global_config", lambda: config)

    documents = search_local_config.read_local_config(
        ["emoji.steal_emoji", "emoji.steal_emoji", "emoji", "emoji.missing_field", "unknown_section"]
    )

    assert [document["title"] for document in documents] == [
        "emoji.steal_emoji",
        "emoji",
        "emoji.missing_field",
        "unknown_section",
    ]
    assert documents[0] == {
        "title": "emoji.steal_emoji",
        "source": "bot_config.toml",
        "value": config.emoji.steal_emoji,
        "section": "[emoji]",
    }
    assert documents[1]["section"] == "[emoji]"
    assert documents[1]["value"]["steal_emoji"] == config.emoji.steal_emoji
    assert "steal_emoji" in documents[2]["available"]
    assert "emoji" in documents[3]["available"]
    assert "models" in documents[3]["available"]


def test_ai_search_document_store_segments_chinese_query_without_spaces() -> None:
    store = AISearchDocumentStore()
    candidates = [
        search_router.AISearchCandidate(id="emoji", title="表情配置", document="emoji.steal_emoji"),
        search_router.AISearchCandidate(id="reply", title="回复时机", document="chat.reply_timing.talk_value"),
    ]

    matches = store.search_candidates("为什么无法发送表情包", candidates, 6)

    assert [match["id"] for match in matches] == ["emoji"]


@pytest.mark.asyncio
async def test_ai_search_document_store_reads_long_official_document_in_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AISearchDocumentStore()
    content = "前言" * 5000 + "talk_value 控制发言频率"
    documents = [OfficialDocument(path="/manual/reply.md", title="回复设置", content=content)]

    async def fake_load_official_docs():
        return documents

    monkeypatch.setattr(store, "_load_official_docs", fake_load_official_docs)

    match = (await store.search_official_docs("talk_value", 4))[0]
    first_segment = (await store.read_official_docs(["/manual/reply.md"]))[0]
    hit_segment = (await store.read_official_docs(["/manual/reply.md"], match["snippet_offset"]))[0]

    assert "talk_value" in match["snippet"]
    assert "talk_value" not in first_segment["content"]
    assert first_segment["next_offset"] == len(first_segment["content"])
    assert "talk_value" in hit_segment["content"]
    assert "next_offset" not in hit_segment


@pytest.mark.asyncio
async def test_ai_search_document_store_serves_stale_documents_while_refreshing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AISearchDocumentStore()
    stale_documents = [OfficialDocument(path="/manual/old.md", title="旧文档", content="旧内容")]
    fresh_documents = [OfficialDocument(path="/manual/new.md", title="新文档", content="新内容")]
    download_count = 0

    async def fake_download_official_docs():
        nonlocal download_count
        download_count += 1
        store._official_docs_cache = (search_documents.time.monotonic() + 600, fresh_documents)
        return fresh_documents

    monkeypatch.setattr(store, "_download_official_docs", fake_download_official_docs)
    store._official_docs_cache = (0.0, stale_documents)

    # 缓存过期时立即返回旧数据，并只启动一个后台刷新任务
    assert await store._load_official_docs() is stale_documents
    store.prewarm_official_docs()
    assert store._official_docs_refresh_task is not None
    await store._official_docs_refresh_task

    assert download_count == 1
    assert await store._load_official_docs() is fresh_documents


@pytest.mark.asyncio
async def test_ai_search_accepts_answer_repeating_field_name_from_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedSearchModel(
        [
            LLMResponseResult.from_portable_output(
                response=json.dumps({"answer": "没有找到名为 `reply_frequency_limit` 的配置项。"}, ensure_ascii=False)
            )
        ]
    )
    monkeypatch.setattr(search_agent, "_get_ai_search_model", lambda: model)
    request = search_router.AISearchRequest(
        query="reply_frequency_limit 在哪里设置",
        candidates=[search_router.AISearchCandidate(id="reply-timing", title="回复时机")],
    )

    response = await search_agent.run_ai_search_agent(request)

    assert len(model.calls) == 1
    assert response.grounding_error == ""
    assert "reply_frequency_limit" in response.answer


@pytest.mark.asyncio
async def test_execute_ai_search_request_does_not_cache_answers_based_on_local_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_count = 0

    async def fake_run_ai_search_agent(request, progress_callback=None):
        nonlocal run_count
        del request, progress_callback
        run_count += 1
        return AISearchResponse(answer="当前 talk_value 为 0.5", used_local_config=True)

    async def ignore_progress(event: search_router.AISearchProgressEvent) -> None:
        del event

    monkeypatch.setattr(search_router, "_AI_SEARCH_CACHE", search_router.OrderedDict())
    monkeypatch.setattr(search_router, "run_ai_search_agent", fake_run_ai_search_agent)
    request = search_router.AISearchRequest(
        query="现在的发言频率是多少",
        candidates=[search_router.AISearchCandidate(id="reply-timing", title="回复时机")],
    )

    await search_router._execute_ai_search_request(request, ignore_progress)
    response = await search_router._execute_ai_search_request(request, ignore_progress)

    assert run_count == 2
    assert response.cached is False
