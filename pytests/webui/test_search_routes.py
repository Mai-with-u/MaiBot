from typing import List
import json

import pytest

from src.common.data_models.llm_service_data_models import LLMGenerationOptions, LLMResponseResult
from src.llm_models.payload_content.message import Message, RoleType
from src.llm_models.payload_content.tool_option import ToolCall
from src.webui.routers import search as search_router
from src.webui.services import ai_search_agent as search_agent
from src.webui.services import ai_search_grounding as search_grounding
from src.webui.services.ai_search_documents import AISearchDocumentStore, OfficialDocument
from src.webui.services.ai_search_models import AISearchModelOutput


class FakeSearchModel:
    def __init__(self) -> None:
        self.calls: List[tuple[List[Message], LLMGenerationOptions]] = []

    async def generate_response_with_messages(
        self,
        message_factory,
        options: LLMGenerationOptions,
    ) -> LLMResponseResult:
        messages = message_factory(None)
        self.calls.append((messages, options))

        if len(self.calls) == 1:
            return LLMResponseResult(
                tool_calls=[
                    ToolCall(
                        call_id="read-docs",
                        func_name="read_official_docs",
                        args={"paths": ["/manual/configuration/bot-config.md"]},
                    )
                ]
            )
        if len(self.calls) == 2:
            return LLMResponseResult(response="已读取配置文档")
        return LLMResponseResult(
            response=(
                '{"answer":"配置文件是 bot_config.toml","suggestions":[],"source_ids":[],'
                '"expanded_terms":[],"results":[]}'
            ),
            model_name="fake-model",
        )


def test_ai_search_document_store_searches_and_reads_candidates() -> None:
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
    documents = store.read_candidates(["emoji", "emoji", "missing"], candidates, 6)

    assert [match["id"] for match in matches] == ["emoji"]
    assert documents == [
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
    ) -> str:
        del tool_call, candidates, read_source_ids
        return '{"content":"bot_config.toml 使用 [emoji] 段，不存在 config.yaml"}'

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

    result, model_output = await search_agent.run_ai_search_agent(request)

    final_messages, final_options = model.calls[-1]
    assert result.response
    assert "bot_config.toml" in model_output.answer
    assert validation_calls == [model_output.answer]
    assert final_options.temperature == 0
    assert final_options.tool_options is None
    assert all(message.role != RoleType.Tool for message in final_messages)
    assert all(not message.tool_calls for message in final_messages)
    assert any("bot_config.toml" in message.get_text_content() for message in final_messages)
    assert any("config.yaml" in message.get_text_content() for message in final_messages)


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

    events = [
        json.loads(line)
        async for line in search_router._stream_ai_search_events(request)
    ]

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
        return (
            LLMResponseResult(model_name="fake-model", total_tokens=42),
            AISearchModelOutput(answer="根据文档完成回答"),
        )

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

    response = await search_router._execute_ai_search_request(request)

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

    events = [
        json.loads(line)
        async for line in search_router._stream_ai_search_events(request)
    ]

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
        answer=(
            "在 `config/bot_config.toml` 的 `[chat]` 中设置 "
            "`reply_frequency_limit = 10`。"
        ),
        suggestions=["启用 `[[keyword_reaction.keyword_rules]]`。"],
    )
    evidence = (
        '{"content":"config/bot_config.toml 使用 [chat.reply_timing]，'
        '群聊频率字段为 talk_value，范围为 0 到 1。"}'
    )

    with pytest.raises(search_grounding.AISearchGroundingError) as exc_info:
        search_grounding.validate_model_output_evidence(model_output, evidence)

    error_message = str(exc_info.value)
    assert "reply_frequency_limit = 10" in error_message
    assert "[[keyword_reaction.keyword_rules]]" in error_message


def test_validate_model_output_evidence_accepts_exact_config_claims() -> None:
    model_output = AISearchModelOutput(
        answer=(
            "在 `config/bot_config.toml` 的 `[chat.reply_timing]` 中调低 "
            "`talk_value`，其范围为 `0` 到 `1`。"
        ),
    )
    evidence = (
        '{"content":"config/bot_config.toml\\n[chat.reply_timing]\\n'
        'talk_value = 1.0，范围为 0 到 1。"}'
    )

    search_grounding.validate_model_output_evidence(model_output, evidence)


def test_validate_model_output_evidence_accepts_documented_field_wildcard() -> None:
    model_output = AISearchModelOutput(
        answer="可检查 `no_action_backoff_*` 这一组空闲退避配置。",
    )
    evidence = (
        '{"content":"no_action_backoff_base_seconds、no_action_backoff_cap_seconds、'
        'no_action_backoff_start_count"}'
    )

    search_grounding.validate_model_output_evidence(model_output, evidence)


def test_validate_model_output_evidence_accepts_structured_assignment() -> None:
    model_output = AISearchModelOutput(
        answer="确认 `[emoji]` 下的 `emoji.steal_emoji = true`。",
    )
    evidence = '{"content":"[emoji]\\nsteal_emoji = true"}'

    search_grounding.validate_model_output_evidence(model_output, evidence)


def test_validate_model_output_evidence_normalizes_escaped_quoted_value() -> None:
    model_output = AISearchModelOutput(
        answer=r"将 `chat.reply_timing.reply_trigger_mode` 设为 `\"reply_necessity\"`。",
    )
    evidence = (
        '{"content":"chat.reply_timing.reply_trigger_mode 支持 frequency 和 reply_necessity"}'
    )

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
        '{"documents":[{"content":"bot_config.toml\\n[emoji]\\nsteal_emoji = true\\n'
        'self.ctx.emoji\\nbot_config"}]}'
    )

    search_grounding.validate_model_output_evidence(model_output, evidence)


@pytest.mark.asyncio
async def test_ai_search_uses_search_evidence_and_rewrites_once_after_grounding_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CorrectingSearchModel:
        def __init__(self) -> None:
            self.calls: List[tuple[List[Message], LLMGenerationOptions]] = []

        async def generate_response_with_messages(
            self,
            message_factory,
            options: LLMGenerationOptions,
        ) -> LLMResponseResult:
            messages = message_factory(None)
            self.calls.append((messages, options))
            if len(self.calls) == 1:
                return LLMResponseResult(
                    tool_calls=[
                        ToolCall(
                            call_id="search-config",
                            func_name="search_webui_index",
                            args={"query": "空闲退避"},
                        )
                    ]
                )
            if len(self.calls) == 2:
                return LLMResponseResult(response="资料读取完成")
            if len(self.calls) == 3:
                return LLMResponseResult(
                    response=(
                        '{"answer":"调用 `POST /api/webui/auth/verify` 检查状态",'
                        '"suggestions":[],"source_ids":[],"expanded_terms":[],"results":[]}'
                    )
                )
            return LLMResponseResult(
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
    ) -> str:
        del tool_call, candidates, read_source_ids
        return '{"content":"no_action_backoff_base_seconds 控制空闲退避基准"}'

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

    result, model_output = await search_agent.run_ai_search_agent(request, capture_progress)

    assert len(model.calls) == 4
    assert "no_action_backoff_*" in result.response
    assert "no_action_backoff_*" in model_output.answer
    assert len(validation_calls) == 2
    assert "POST /api/webui/auth/verify" in validation_calls[0]
    assert "no_action_backoff_*" in validation_calls[1]
    assert any(event.stage == "correcting" for event in progress_events)
    correction_prompt = model.calls[-1][0][-1].get_text_content()
    assert "POST /api/webui/auth/verify" in correction_prompt
