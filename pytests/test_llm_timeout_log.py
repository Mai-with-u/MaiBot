import httpx
import pytest
from openai import APITimeoutError

from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.exceptions import ModelAttemptFailed, NetworkConnectionError
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.utils_model import LLMOrchestrator
import src.llm_models.utils_model as utils_model


@pytest.mark.asyncio
async def test_api_timeout_logs_provider_limit_attempts_and_replay(monkeypatch, tmp_path):
    warnings = []
    errors = []
    monkeypatch.setattr(utils_model.logger, "warning", warnings.append)
    monkeypatch.setattr(utils_model.logger, "error", errors.append)
    monkeypatch.setattr(utils_model, "save_failed_request_snapshot", lambda **kwargs: tmp_path / "snapshot.json")
    monkeypatch.setattr(utils_model, "update_failed_request_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(LLMOrchestrator, "_schedule_llm_retry_event", lambda self, **kwargs: None)

    async def no_wait(seconds):
        pass

    monkeypatch.setattr(utils_model.asyncio, "sleep", no_wait)
    provider = APIProvider(
        name="test-provider", base_url="https://example.com/v1", auth_type="none", client_type="openai",
        timeout=42, retry_interval=1,
    )
    request = ResponseRequest(
        model_info=ModelInfo(name="test-model", model_identifier="test-model", api_provider="test-provider"),
        context_items=[],
    )

    class FailingClient:
        async def get_response(self, request):
            error = NetworkConnectionError("Request timed out.")
            raise error from APITimeoutError(request=httpx.Request("POST", "https://example.com/v1/chat/completions"))

    orchestrator = object.__new__(LLMOrchestrator)
    orchestrator.request_type = "A_Memorix.ChatSummarization"
    with pytest.raises(ModelAttemptFailed):
        await orchestrator._attempt_request_on_model(provider, FailingClient(), request, retry_limit=2)

    assert len(warnings) == 1
    assert len(errors) == 1
    assert "遇到错误: 网络连接超时" in warnings[0]
    assert "APITimeoutError | Request timed out." in warnings[0]
    assert "最大超时时间：42s | 重试次数: 0 | 剩余重试次数: 1" in warnings[0]
    assert "重试次数: 1 | 剩余重试次数: 0" in errors[0]
    assert "调用完整信息: uv run python scripts/replay_llm_request.py" in warnings[0]
    assert "snapshot.json" in warnings[0]
    assert "调用完整信息（如果需要求助" not in warnings[0]
