"""思考参数探测必须验证开关行为，不能将兼容接口的成功响应当成支持。"""

from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Dict, List

from fastapi import FastAPI
from openai import APIConnectionError, APIStatusError

import asyncio
import httpx
import importlib
import json

import pytest

from .test_model_routes import load_model_routes


@pytest.fixture
def probe():
    return importlib.import_module("src.webui.services.thinking_probe")


@pytest.mark.parametrize("client_type", ["openai", "openai_responses"])
@pytest.mark.parametrize(
    "mode, enabled, expected",
    [
        ("thinking_type", True, {"thinking": {"type": "enabled"}}),
        ("thinking_type", False, {"thinking": {"type": "disabled"}}),
        ("enable_thinking", True, {"enable_thinking": True}),
        ("enable_thinking", False, {"enable_thinking": False}),
        ("reasoning_effort_nested", True, {"reasoning": {"effort": "high"}}),
        ("reasoning_effort_nested", False, {"reasoning": {"effort": "none"}}),
        ("reasoning_effort", True, {"reasoning_effort": "high"}),
        ("reasoning_effort", False, {"reasoning_effort": "off"}),
    ],
)
def test_probe_replaces_conflicting_nested_and_top_level_parameters(
    probe, client_type: str, mode: str, enabled: bool, expected: Dict[str, Any]
) -> None:
    params = {
        "thinking": {"type": "adaptive"},
        "enable_thinking": True,
        "reasoning": {"effort": "max"},
        "reasoning_effort": "high",
        "thinking_budget": 8192,
        "body": {
            "thinking": {"type": "disabled"},
            "enable_thinking": False,
            "reasoning": {"effort": "low"},
            "reasoning_effort": "low",
            "thinking_budget": 0,
            "top_p": 0.8,
            "messages": [{"role": "user", "content": "原始消息不能进入测试"}],
            "max_tokens": 99999,
            "max_output_tokens": 99999,
        },
        "headers": {"X-Request-Token": "not-a-body-value"},
        "query": {"api-version": "test"},
        "top_p": 0.9,
    }
    original = deepcopy(params)
    body = probe._build_probe_body(params, mode, enabled, client_type)

    assert params == original
    for key in ("thinking", "enable_thinking", "reasoning", "reasoning_effort", "thinking_budget"):
        if key in expected:
            assert body[key] == expected[key]
        else:
            assert key not in body
    assert body["top_p"] == 0.9
    assert "body" not in body
    assert "headers" not in body
    assert "query" not in body
    assert body.get("messages") != original["body"]["messages"]
    assert body.get("max_tokens") != 99999
    assert body.get("max_output_tokens") != 99999


def chat_response(**message_fields: Any) -> Dict[str, Any]:
    return {
        "choices": [{"finish_reason": "stop", "message": {"content": "42", **message_fields}}],
    }


def response_payload() -> Dict[str, Any]:
    return {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "42"}]}],
    }


@pytest.fixture
def isolated_probe(probe, monkeypatch: pytest.MonkeyPatch):
    async def ssl_context():
        return False

    class FakeOpenAI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: Any):
            return False

    monkeypatch.setattr(probe, "get_shared_ssl_context_async", ssl_context)
    monkeypatch.setattr(probe, "validate_public_url", lambda url, **kwargs: url)
    monkeypatch.setattr(probe, "_create_probe_client", lambda *args: FakeOpenAI())
    provider = probe.APIProvider(name="saved-provider", base_url="https://example.com/v1", api_key="secret-test-key")
    request = probe.ThinkingProbeRequest(api_provider="saved-provider", model_identifier="unsaved-model")
    return probe, provider, request


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled_reasoning, disabled_reasoning", [(False, False), (True, True), (False, True)])
async def test_ignored_or_inverted_switch_is_never_matched(
    isolated_probe, monkeypatch: pytest.MonkeyPatch, enabled_reasoning: bool, disabled_reasoning: bool
) -> None:
    probe, provider, request = isolated_probe

    async def fake_request(client, provider, request, mode, enabled, should_stop=None):
        return probe.ProbeObservation(text="10", reasoning=enabled_reasoning if enabled else disabled_reasoning, complete=True)

    monkeypatch.setattr(probe, "_request_probe", fake_request)
    result = await probe.probe_thinking(provider, request)
    assert result.status == "inconclusive"
    assert result.matches == []
    assert len(result.attempts) == 4
    assert all(attempt.status == "inconclusive" for attempt in result.attempts)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_observation", ["truncated", "empty"])
async def test_incomplete_response_cannot_match_a_mode(
    isolated_probe, monkeypatch: pytest.MonkeyPatch, invalid_observation: str
) -> None:
    probe, provider, request = isolated_probe

    async def fake_request(client, provider, request, mode, enabled, should_stop=None):
        return probe.ProbeObservation(
            text="" if invalid_observation == "empty" else "10",
            reasoning=enabled,
            complete=invalid_observation != "truncated",
        )

    monkeypatch.setattr(probe, "_request_probe", fake_request)
    result = await probe.probe_thinking(provider, request)
    assert result.status == "inconclusive"
    assert result.matches == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code, expected_calls", [(400, 4), (422, 4), (401, 1), (403, 1), (404, 1), (429, 1)])
async def test_upstream_rejection_does_not_leak_secrets_or_retry(
    isolated_probe, monkeypatch: pytest.MonkeyPatch, status_code: int, expected_calls: int
) -> None:
    probe, provider, request = isolated_probe
    calls = []

    async def fake_request(client, provider, request, mode, enabled, should_stop=None):
        calls.append((mode, enabled))
        response = httpx.Response(status_code, request=httpx.Request("POST", "https://example.com/v1"))
        raise APIStatusError("secret-test-key must not be echoed", response=response, body={"error": "secret-test-key"})

    monkeypatch.setattr(probe, "_request_probe", fake_request)
    result = await probe.probe_thinking(provider, request)
    assert result.status == "inconclusive"
    assert result.matches == []
    assert len(calls) == expected_calls
    assert "secret-test-key" not in result.model_dump_json()
    assert all(attempt.status == "rejected" for attempt in result.attempts)


@pytest.mark.asyncio
async def test_timeout_stops_remaining_calls(isolated_probe, monkeypatch: pytest.MonkeyPatch) -> None:
    probe, provider, request = isolated_probe
    calls = []

    async def fake_request(client, provider, request, mode, enabled, should_stop=None):
        calls.append(mode)
        raise TimeoutError

    monkeypatch.setattr(probe, "_request_probe", fake_request)
    result = await probe.probe_thinking(provider, request)
    assert result.status == "inconclusive"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_disconnect_stops_before_next_paid_request(isolated_probe, monkeypatch: pytest.MonkeyPatch) -> None:
    probe, provider, request = isolated_probe
    calls = []

    async def fake_request(client, provider, request, mode, enabled, should_stop=None):
        calls.append((mode, enabled))
        return probe.ProbeObservation(text="10", reasoning=True, complete=True)

    async def should_stop():
        return bool(calls)

    monkeypatch.setattr(probe, "_request_probe", fake_request)
    result = await probe.probe_thinking(provider, request, should_stop=should_stop)
    assert len(calls) == 1
    assert result.matches == []


@pytest.mark.asyncio
async def test_modes_run_in_two_parallel_batches_and_keep_result_order(
    isolated_probe, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe, provider, request = isolated_probe
    modes = ["thinking_type", "enable_thinking", "reasoning_effort_nested", "reasoning_effort"]
    started = asyncio.Queue()
    releases = {(mode, enabled): asyncio.Event() for mode in modes for enabled in (True, False)}
    completed = {mode: asyncio.Event() for mode in modes}
    starts = []
    finishes = []
    active = 0
    peak = 0
    checking_disconnect = 0
    peak_disconnect_checks = 0

    async def should_stop() -> bool:
        nonlocal checking_disconnect, peak_disconnect_checks
        checking_disconnect += 1
        peak_disconnect_checks = max(peak_disconnect_checks, checking_disconnect)
        try:
            # 实际让出执行权，确认并行模式不会同时消费 Request.is_disconnected。
            await asyncio.sleep(0)
            return False
        finally:
            checking_disconnect -= 1

    async def fake_request(client, provider, request, mode, enabled, should_stop=None):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        starts.append((mode, enabled))
        started.put_nowait((mode, enabled))
        try:
            await releases[mode, enabled].wait()
            if not enabled:
                finishes.append(mode)
                completed[mode].set()
            return probe.ProbeObservation(text="10", reasoning=enabled, complete=True)
        finally:
            active -= 1

    monkeypatch.setattr(probe, "_request_probe", fake_request)
    task = asyncio.create_task(probe.probe_thinking(provider, request, should_stop=should_stop))
    try:
        first_starts = [await asyncio.wait_for(started.get(), 2) for _ in range(2)]
        assert set(first_starts) == {(modes[0], True), (modes[1], True)}
        assert active == 2

        # 第二个模式先跑完整对照，第一模式仍阻塞；第三模式必须等整批结束。
        releases[modes[1], True].set()
        assert await asyncio.wait_for(started.get(), 2) == (modes[1], False)
        releases[modes[1], False].set()
        await asyncio.wait_for(completed[modes[1]].wait(), 2)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert started.empty()
        assert all(mode in modes[:2] for mode, _ in starts)
        assert active == 1

        releases[modes[0], True].set()
        assert await asyncio.wait_for(started.get(), 2) == (modes[0], False)
        releases[modes[0], False].set()
        second_starts = [await asyncio.wait_for(started.get(), 2) for _ in range(2)]
        assert set(second_starts) == {(modes[2], True), (modes[3], True)}
        assert completed[modes[0]].is_set() and completed[modes[1]].is_set()

        # 第二批也故意逆序完成，结果应始终按候选模式顺序呈现。
        for mode in reversed(modes[2:]):
            releases[mode, True].set()
            assert await asyncio.wait_for(started.get(), 2) == (mode, False)
            releases[mode, False].set()
            await asyncio.wait_for(completed[mode].wait(), 2)
        result = await asyncio.wait_for(task, 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert peak == 2
    assert active == 0
    assert peak_disconnect_checks == 1
    assert finishes == [modes[1], modes[0], modes[3], modes[2]]
    assert result.matches == modes
    assert [attempt.mode for attempt in result.attempts] == modes
    for mode in modes:
        assert [enabled for call_mode, enabled in starts if call_mode == mode] == [True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [401, 403, 404, 429, "connection", "timeout"])
async def test_fatal_failure_allows_inflight_completion_but_stops_following_requests(
    isolated_probe, monkeypatch: pytest.MonkeyPatch, failure: Any
) -> None:
    probe, provider, request = isolated_probe
    started = asyncio.Queue()
    fail_now = asyncio.Event()
    failure_raised = asyncio.Event()
    finish_inflight = asyncio.Event()
    calls = []

    async def fake_request(client, provider, request, mode, enabled, should_stop=None):
        calls.append((mode, enabled))
        started.put_nowait((mode, enabled))
        if mode == "thinking_type":
            await fail_now.wait()
            failure_raised.set()
            if failure == "timeout":
                raise TimeoutError
            upstream_request = httpx.Request("POST", "https://example.com/v1")
            if failure == "connection":
                raise APIConnectionError(request=upstream_request)
            response = httpx.Response(failure, request=upstream_request)
            raise APIStatusError("do not expose upstream secrets", response=response, body={})
        await finish_inflight.wait()
        return probe.ProbeObservation(text="10", reasoning=enabled, complete=True)

    monkeypatch.setattr(probe, "_request_probe", fake_request)
    task = asyncio.create_task(probe.probe_thinking(provider, request))
    try:
        first_starts = [await asyncio.wait_for(started.get(), 2) for _ in range(2)]
        assert set(first_starts) == {("thinking_type", True), ("enable_thinking", True)}
        fail_now.set()
        await asyncio.wait_for(failure_raised.wait(), 2)
        await asyncio.sleep(0)
        assert not task.done()
        finish_inflight.set()
        result = await asyncio.wait_for(task, 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert calls == first_starts
    assert len(calls) == 2
    assert result.matches == []
    assert result.attempts[0].mode == "thinking_type"
    assert "do not expose upstream secrets" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_cancellation_cleans_up_both_inflight_requests_and_client(
    isolated_probe, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe, provider, request = isolated_probe
    started = asyncio.Queue()
    active = set()
    cancelled = []
    client_exited = asyncio.Event()
    client_exit_active = []

    class TrackingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: Any):
            client_exit_active.append(set(active))
            client_exited.set()
            return False

    async def fake_request(client, provider, request, mode, enabled, should_stop=None):
        active.add(mode)
        started.put_nowait((mode, enabled))
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(mode)
            raise
        finally:
            active.remove(mode)

    monkeypatch.setattr(probe, "_create_probe_client", lambda *args: TrackingClient())
    monkeypatch.setattr(probe, "_request_probe", fake_request)
    task = asyncio.create_task(probe.probe_thinking(provider, request))
    try:
        first_starts = [await asyncio.wait_for(started.get(), 2) for _ in range(2)]
        assert set(first_starts) == {("thinking_type", True), ("enable_thinking", True)}
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert set(cancelled) == {"thinking_type", "enable_thinking"}
    assert not active
    assert started.empty()
    assert client_exited.is_set()
    assert client_exit_active == [set()]


class FakeStream:
    def __init__(self, events: List[Dict[str, Any]]) -> None:
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any):
        return False

    async def __aiter__(self):
        for event in self.events:
            yield SimpleNamespace(model_dump=lambda event=event: event)


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", ["openai", "openai_responses"])
async def test_streamed_text_and_final_reasoning_usage_are_combined(probe, client_type: str) -> None:
    provider = probe.APIProvider(
        name="stream-provider", base_url="https://example.com/v1", api_key="test-key", client_type=client_type,
    )
    request = probe.ThinkingProbeRequest(api_provider=provider.name, model_identifier="stream-model", force_stream_mode=True)
    if client_type == "openai":
        events = [
            {"choices": [{"delta": {"content": "10"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"completion_tokens_details": {"reasoning_tokens": 16}}},
        ]
    else:
        completed = response_payload()
        completed["usage"] = {"output_tokens_details": {"reasoning_tokens": 16}}
        events = [{"type": "response.completed", "response": completed}]

    async def create(**kwargs: Any):
        assert kwargs["stream"] is True
        assert kwargs["model"] == "stream-model"
        return FakeStream(events)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), responses=SimpleNamespace(create=create),
    )
    result = await probe._request_probe(client, provider, request, "enable_thinking", True)
    assert result.text == ("10" if client_type == "openai" else "42")
    assert result.reasoning is True
    assert result.complete is True


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", ["openai", "openai_responses"])
async def test_streamed_refusal_is_not_erased_by_terminal_success(probe, client_type: str) -> None:
    provider = probe.APIProvider(
        name="stream-provider", base_url="https://example.com/v1", api_key="test-key", client_type=client_type,
    )
    request = probe.ThinkingProbeRequest(api_provider=provider.name, model_identifier="stream-model", force_stream_mode=True)
    if client_type == "openai":
        events = [
            {"choices": [{"delta": {"refusal": "不能回答"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "拒绝答复"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
    else:
        events = [
            {"type": "response.refusal.delta", "delta": "不能回答"},
            {"type": "response.completed", "response": response_payload()},
        ]

    async def create(**kwargs: Any):
        return FakeStream(events)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), responses=SimpleNamespace(create=create),
    )
    result = await probe._request_probe(client, provider, request, "enable_thinking", False)
    assert result.refused is True
    assert result.complete is False


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type", ["bearer", "none", "header", "query"])
async def test_probe_wire_preserves_provider_auth_without_placeholder_bearer(probe, auth_type: str) -> None:
    provider = probe.APIProvider(
        name="auth-provider", base_url="https://example.com/v1", api_key="" if auth_type == "bearer" else "test-key", auth_type=auth_type,
        auth_header_name="X-API-Key", auth_header_prefix="", auth_query_name="key",
        default_headers={"X-App": "maibot"}, default_query={"api-version": "test"},
    )
    request = probe.ThinkingProbeRequest(api_provider=provider.name, model_identifier="draft-model")
    captured = []

    async def respond(http_request: httpx.Request):
        captured.append(http_request)
        payload = chat_response()
        payload["choices"][0]["index"] = 0
        payload["choices"][0]["message"]["role"] = "assistant"
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "model": "draft-model", **payload})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        client_config = probe.build_openai_compatible_client_config(provider)
        async with probe._create_probe_client(client_config, provider, http_client) as client:
            assert client.max_retries == 0
            result = await probe._request_probe(client, provider, request, "enable_thinking", True)
    assert result.complete is True
    assert len(captured) == 1
    actual = captured[0]
    assert actual.url.path == "/v1/chat/completions"
    assert "authorization" not in actual.headers
    assert actual.headers["X-App"] == "maibot"
    assert actual.url.params["api-version"] == "test"
    if auth_type == "header":
        assert actual.headers["X-API-Key"] == "test-key"
    elif auth_type == "query":
        assert actual.url.params["key"] == "test-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("simultaneous_rejection", [False, True])
async def test_explicit_token_field_rejection_adapts_once_and_keeps_original_request(
    probe, monkeypatch: pytest.MonkeyPatch, simultaneous_rejection: bool
) -> None:
    provider = probe.APIProvider(name="provider", base_url="https://example.com/v1", api_key="test-key")
    request = probe.ThinkingProbeRequest(
        api_provider=provider.name, model_identifier="draft-model", extra_params={"body": {"max_tokens": 4096}, "top_p": 0.9},
    )
    original = request.model_dump()
    bodies = []
    budget_barrier = asyncio.Event()
    rejected_budgets = 0

    async def respond(http_request: httpx.Request):
        nonlocal rejected_budgets
        body = json.loads(http_request.content)
        bodies.append(body)
        if (simultaneous_rejection and "max_tokens" in body) or len(bodies) == 1:
            rejected_budgets += 1
            if simultaneous_rejection:
                if rejected_budgets == 2:
                    budget_barrier.set()
                await asyncio.wait_for(budget_barrier.wait(), 2)
            return httpx.Response(400, json={"error": {
                "message": "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.",
                "type": "invalid_request_error", "param": "max_tokens", "code": "unsupported_parameter",
            }})
        payload = chat_response(reasoning_content="计算" if body.get("thinking", {}).get("type") == "enabled" else "")
        payload["choices"][0]["index"] = 0
        payload["choices"][0]["message"]["role"] = "assistant"
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "model": "draft-model", **payload})

    async def ssl_context():
        return False

    original_factory = probe._create_probe_client
    monkeypatch.setattr(probe, "get_shared_ssl_context_async", ssl_context)
    monkeypatch.setattr(probe, "validate_public_url", lambda url, **kwargs: url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as mock_http_client:
        monkeypatch.setattr(probe, "_create_probe_client", lambda config, provider, http_client: original_factory(
            config, provider, mock_http_client,
        ))
        result = await probe.probe_thinking(provider, request)

    assert result.matches == ["thinking_type"]
    assert len(bodies) == 8 + rejected_budgets
    assert rejected_budgets == (2 if simultaneous_rejection else 1)
    assert "max_tokens" in bodies[0]
    legacy_budgets = [body for body in bodies if "max_tokens" in body]
    assert rejected_budgets <= len(legacy_budgets) <= 2
    # 首批两个开启请求可能在字段协商前已发出；后续关闭与第二批必须共享协商结果。
    assert all(
        body.get("thinking", {}).get("type") == "enabled" or body.get("enable_thinking") is True
        for body in legacy_budgets
    )
    assert all(body["max_completion_tokens"] == 1024 for body in bodies if "max_tokens" not in body)
    assert all(body["top_p"] == 0.9 for body in bodies)
    assert request.model_dump() == original


@pytest.mark.asyncio
async def test_fatal_sibling_error_prevents_token_field_negotiation_retry(
    probe, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = probe.APIProvider(name="provider", base_url="https://example.com/v1", api_key="test-key")
    request = probe.ThinkingProbeRequest(
        api_provider=provider.name, model_identifier="draft-model", extra_params={"body": {"max_tokens": 4096}},
    )
    original = request.model_dump()
    bodies = []
    fatal_propagating = asyncio.Event()
    original_request_probe = probe._request_probe
    original_factory = probe._create_probe_client

    async def track_fatal_request(*args: Any, **kwargs: Any):
        try:
            return await original_request_probe(*args, **kwargs)
        except APIStatusError as error:
            if error.status_code == 429:
                # 同一协程随后立即进入 probe_mode 的错误处理，先设置共享停止标志。
                fatal_propagating.set()
            raise

    async def respond(http_request: httpx.Request):
        body = json.loads(http_request.content)
        bodies.append(body)
        if body.get("enable_thinking") is True:
            return httpx.Response(429, json={"error": {"message": "quota exceeded", "type": "rate_limit_error"}})
        await asyncio.wait_for(fatal_propagating.wait(), 2)
        return httpx.Response(400, json={"error": {
            "message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead.",
            "type": "invalid_request_error", "param": "max_tokens",
        }})

    async def ssl_context():
        return False

    monkeypatch.setattr(probe, "get_shared_ssl_context_async", ssl_context)
    monkeypatch.setattr(probe, "validate_public_url", lambda url, **kwargs: url)
    monkeypatch.setattr(probe, "_request_probe", track_fatal_request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as mock_http_client:
        monkeypatch.setattr(probe, "_create_probe_client", lambda config, provider, http_client: original_factory(
            config, provider, mock_http_client,
        ))
        result = await asyncio.wait_for(probe.probe_thinking(provider, request), 3)

    assert len(bodies) == 2
    assert all("max_tokens" in body and "max_completion_tokens" not in body for body in bodies)
    assert result.matches == []
    assert [(attempt.mode, attempt.status) for attempt in result.attempts] == [("enable_thinking", "rejected")]
    assert "429" in result.attempts[0].message
    assert request.model_dump() == original


@pytest.mark.asyncio
async def test_unrelated_parameter_rejection_is_not_retried(probe) -> None:
    provider = probe.APIProvider(name="provider", base_url="https://example.com/v1", api_key="test-key")
    request = probe.ThinkingProbeRequest(api_provider=provider.name, model_identifier="draft-model")
    calls = []

    async def respond(http_request: httpx.Request):
        calls.append(http_request)
        return httpx.Response(400, json={"error": {
            "message": "Unsupported parameter: thinking", "type": "invalid_request_error", "param": "thinking",
        }})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        client_config = probe.build_openai_compatible_client_config(provider)
        async with probe._create_probe_client(client_config, provider, http_client) as client:
            with pytest.raises(APIStatusError):
                await probe._request_probe(client, provider, request, "thinking_type", True)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_only_top_level_reasoning_effort_controls_hidden_reasoning(
    probe, monkeypatch: pytest.MonkeyPatch
) -> None:
    """前三种参数被接口忽略时，必须继续验证第四种 high/off 模式。"""
    provider = probe.APIProvider(name="provider", base_url="https://example.com/v1", api_key="test-key")
    request = probe.ThinkingProbeRequest(api_provider=provider.name, model_identifier="draft-model")
    bodies = []

    async def respond(http_request: httpx.Request):
        body = json.loads(http_request.content)
        bodies.append(body)
        payload = chat_response()
        payload["choices"][0]["index"] = 0
        payload["choices"][0]["message"]["role"] = "assistant"
        payload["usage"] = {
            "completion_tokens_details": {"reasoning_tokens": 0 if body.get("reasoning_effort") == "off" else 48},
        }
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "model": "draft-model", **payload})

    async def ssl_context():
        return False

    original_factory = probe._create_probe_client
    monkeypatch.setattr(probe, "get_shared_ssl_context_async", ssl_context)
    monkeypatch.setattr(probe, "validate_public_url", lambda url, **kwargs: url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as mock_http_client:
        monkeypatch.setattr(probe, "_create_probe_client", lambda config, provider, http_client: original_factory(
            config, provider, mock_http_client,
        ))
        result = await probe.probe_thinking(provider, request)

    assert result.status == "matched"
    assert result.matches == ["reasoning_effort"]
    assert [attempt.status for attempt in result.attempts] == ["inconclusive", "inconclusive", "inconclusive", "matched"]
    assert len(bodies) == 8
    assert [body.get("reasoning_effort") for body in bodies] == [None, None, None, None, None, None, "high", "off"]


@pytest.fixture
def probe_route(monkeypatch: pytest.MonkeyPatch):
    model_routes = load_model_routes(monkeypatch)
    app = FastAPI()
    app.include_router(model_routes.router, prefix="/api/webui")
    return model_routes, app


@pytest.mark.asyncio
async def test_route_can_test_unsaved_model_with_saved_provider(probe_route, monkeypatch: pytest.MonkeyPatch) -> None:
    model_routes, app = probe_route
    monkeypatch.setattr(model_routes, "_get_provider_config", lambda name: {
        "name": name, "base_url": "https://example.com/v1", "api_key": "secret-test-key", "client_type": "openai",
    })
    monkeypatch.setattr(model_routes, "_get_model_config", lambda name: pytest.fail("不应要求模型已保存"))

    async def fake_probe(provider, request, **kwargs: Any):
        assert provider.name == "saved-provider"
        assert request.model_identifier == "unsaved-model"
        return model_routes.ThinkingProbeResponse(status="matched", matches=["thinking_type"], attempts=[], message="已验证")

    monkeypatch.setattr(model_routes, "probe_thinking", fake_probe)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/api/webui/models/probe-thinking", json={
            "api_provider": "saved-provider", "model_identifier": "unsaved-model",
        })
    assert result.status_code == 200
    assert result.json()["matches"] == ["thinking_type"]
    assert "secret-test-key" not in result.text
