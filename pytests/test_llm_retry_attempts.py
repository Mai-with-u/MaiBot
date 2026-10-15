import asyncio

import httpx
import pytest

from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.exceptions import ModelAttemptFailed
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.model_client.openai_client import OpenaiClient
from src.llm_models.model_client.openai_responses_client import RESPONSES_CLIENT_TYPE, OpenAIResponsesClient
from src.llm_models.utils_model import LLMOrchestrator
import src.llm_models.model_client.openai_client as openai_client
import src.llm_models.model_client.openai_responses_client as openai_responses_client
import src.llm_models.utils_model as utils_model


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_class", "client_type", "endpoint"),
    [
        (OpenaiClient, "openai", "/v1/chat/completions"),
        (OpenAIResponsesClient, RESPONSES_CLIENT_TYPE, "/v1/responses"),
    ],
)
async def test_timed_out_request_is_sent_at_most_max_retry_times(
    monkeypatch, tmp_path, client_class, client_type, endpoint
):
    """超时请求的出站 HTTP 次数应等于 Provider 的 max_retry，而不是 SDK 与外层循环重试次数的乘积。"""
    warnings = []
    errors = []
    monkeypatch.setattr(utils_model.logger, "warning", warnings.append)
    monkeypatch.setattr(utils_model.logger, "error", errors.append)
    monkeypatch.setattr(utils_model, "save_failed_request_snapshot", lambda **kwargs: tmp_path / "snapshot.json")
    monkeypatch.setattr(openai_client, "save_failed_request_snapshot", lambda **kwargs: tmp_path / "snapshot.json")
    monkeypatch.setattr(
        openai_responses_client, "save_failed_request_snapshot", lambda **kwargs: tmp_path / "snapshot.json"
    )
    monkeypatch.setattr(utils_model, "update_failed_request_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(LLMOrchestrator, "_schedule_llm_retry_event", lambda self, **kwargs: None)

    # 跳过重试间隔等待，但仍让出事件循环，避免 await_task_with_interrupt 的轮询变成忙等
    original_sleep = asyncio.sleep

    async def no_wait(seconds):
        await original_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", no_wait)

    outbound_requests = []

    def always_time_out(http_request: httpx.Request) -> httpx.Response:
        outbound_requests.append(http_request)
        raise httpx.ReadTimeout("simulated read timeout", request=http_request)

    provider = APIProvider(
        name="test-provider",
        base_url="https://example.com/v1",
        auth_type="none",
        client_type=client_type,
        max_retry=3,
        timeout=42,
        retry_interval=1,
    )
    client = client_class(provider)
    # 仅替换底层传输层，保留生产代码构造的 SDK 客户端配置（含 max_retries）
    client.client = client.client.with_options(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(always_time_out))
    )
    request = ResponseRequest(
        model_info=ModelInfo(name="test-model", model_identifier="test-model", api_provider="test-provider"),
        context_items=[],
    )

    orchestrator = object.__new__(LLMOrchestrator)
    orchestrator.request_type = "memory"
    with pytest.raises(ModelAttemptFailed):
        await orchestrator._attempt_request_on_model(provider, client, request)

    assert len(outbound_requests) == provider.max_retry
    assert all(outbound_request.url.path == endpoint for outbound_request in outbound_requests)
    # 外层循环日志与实际出站次数一一对应：前两次告警，最后一次报错
    assert len(warnings) == 2
    assert len(errors) == 1
    assert "重试次数: 0 | 剩余重试次数: 2" in warnings[0]
    assert "重试次数: 1 | 剩余重试次数: 1" in warnings[1]
    assert "重试次数: 2 | 剩余重试次数: 0" in errors[0]
