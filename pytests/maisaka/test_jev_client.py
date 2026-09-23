"""Jev 决策客户端测试。"""

from typing import Any

import httpx
import pytest

from src.maisaka.jev import JevClient, JevConfigError, build_jev_client_from_config
from src.maisaka.jev.client import _parse_evaluation_payload


class _FakeResponse:
    """模拟 httpx.Response 的最小实现。"""

    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "https://api.typesafe.ai/v1/systemone"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> dict[str, Any]:
        return self._payload


class _RecordingClient:
    """记录请求参数的假 httpx.AsyncClient。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_RecordingClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(self._payload)


@pytest.fixture
def jev_client() -> JevClient:
    return JevClient(
        api_key="test-key",
        base_url="https://api.typesafe.ai/v1/",
        model="jev-latest",
        timeout_seconds=5,
    )


@pytest.mark.asyncio
async def test_evaluate_sends_expected_request(monkeypatch, jev_client) -> None:
    recording = _RecordingClient(
        {
            "model": "jev-1.13.0",
            "answers": {
                "reply_action": {
                    "type": "choice",
                    "choice": "reply",
                    "probabilities": {"reply": 0.83, "ignore": 0.17},
                    "confidence": 0.66,
                }
            },
            "usage": {"input_tokens": 120, "output_tokens": 3},
        }
    )
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: recording)

    result = await jev_client.evaluate(
        state={"chat_type": "群聊", "pending_messages": [{"sender": "小明", "text": "在吗", "at_bot": False}]},
        questions={"reply_action": {"type": "choice", "instructions": "该回复吗"}},
    )

    assert len(recording.calls) == 1
    call = recording.calls[0]
    assert call["url"] == "https://api.typesafe.ai/v1/systemone"
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["json"]["model"] == "jev-latest"
    assert call["json"]["questions"]["reply_action"]["type"] == "choice"

    assert result.model == "jev-1.13.0"
    assert result.input_tokens == 120
    assert result.output_tokens == 3
    answer = result.answers["reply_action"]
    assert answer.answer_type == "choice"
    assert answer.choice == "reply"
    assert answer.probabilities["reply"] == pytest.approx(0.83)
    assert answer.confidence == pytest.approx(0.66)


@pytest.mark.asyncio
async def test_evaluate_raises_on_http_error(monkeypatch, jev_client) -> None:
    class _FailingClient(_RecordingClient):
        async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _FakeResponse:
            return _FakeResponse({"error": "unauthorized"}, status_code=401)

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _FailingClient({"error": "x"}))

    with pytest.raises(httpx.HTTPStatusError):
        await jev_client.evaluate(state="在吗", questions={"q": {"type": "noul", "instructions": "是吗"}})


def test_parse_noul_answer() -> None:
    result = _parse_evaluation_payload(
        {
            "model": "jev-1.13.0",
            "answers": {"is_urgent": {"type": "noul", "noul": 0.91, "confidence": 0.74}},
            "usage": {"input_tokens": 10, "output_tokens": 1},
        }
    )

    answer = result.answers["is_urgent"]
    assert answer.answer_type == "noul"
    assert answer.noul == pytest.approx(0.91)
    assert answer.confidence == pytest.approx(0.74)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"model": "jev-1.13.0"},
        {"model": "jev-1.13.0", "answers": {}},
        {"model": "jev-1.13.0", "answers": {"q": "not-an-object"}},
        {"model": "jev-1.13.0", "answers": {"q": {"type": "unknown"}}},
        {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": "high"}}},
    ],
)
def test_parse_payload_rejects_invalid_shapes(payload: Any) -> None:
    with pytest.raises(ValueError):
        _parse_evaluation_payload(payload)


def test_build_client_requires_api_key(monkeypatch) -> None:
    monkeypatch.setattr("src.maisaka.jev.client.global_config.chat.jev.api_key", "")
    monkeypatch.setattr("src.maisaka.jev.client.global_config.chat.jev.base_url", "https://api.typesafe.ai/v1")

    with pytest.raises(JevConfigError):
        build_jev_client_from_config()


def test_build_client_requires_base_url(monkeypatch) -> None:
    monkeypatch.setattr("src.maisaka.jev.client.global_config.chat.jev.api_key", "test-key")
    monkeypatch.setattr("src.maisaka.jev.client.global_config.chat.jev.base_url", "  ")

    with pytest.raises(JevConfigError):
        build_jev_client_from_config()


@pytest.mark.parametrize("insecure_url", ["http://api.typesafe.ai/v1", "http://127.0.0.1:8080/v1", "api.typesafe.ai"])
def test_build_client_rejects_non_https_base_url(monkeypatch, insecure_url: str) -> None:
    """请求会带上 Bearer API Key 与聊天内容，明文地址必须被拒绝。"""

    monkeypatch.setattr("src.maisaka.jev.client.global_config.chat.jev.api_key", "test-key")
    monkeypatch.setattr("src.maisaka.jev.client.global_config.chat.jev.base_url", insecure_url)

    with pytest.raises(JevConfigError, match="https"):
        build_jev_client_from_config()


def test_build_client_accepts_https_base_url(monkeypatch) -> None:
    monkeypatch.setattr("src.maisaka.jev.client.global_config.chat.jev.api_key", "test-key")
    monkeypatch.setattr("src.maisaka.jev.client.global_config.chat.jev.base_url", "https://api.typesafe.ai/v1")

    client = build_jev_client_from_config()

    assert isinstance(client, JevClient)
