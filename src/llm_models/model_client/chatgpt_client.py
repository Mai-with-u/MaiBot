"""Official SIWC public Responses transport, with no API-key/account fallback."""

from copy import deepcopy
from typing import Any, Dict

import asyncio
import json

from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from openai._types import Omit

from src.common.chatgpt_auth import ChatGPTAccountStore, RESOURCE, USAGE_URL
from src.common.http_client import get_shared_ssl_context
from src.config.model_configs import APIProvider, ToolArgumentParseMode
from src.llm_models.exceptions import ReqAbortException

from .base_client import (
    APIResponse,
    AudioTranscriptionRequest,
    BaseClient,
    EmbeddingRequest,
    ImageEmbeddingRequest,
    ResponseRequest,
    client_registry,
)
from .openai_responses_client import OpenAIResponsesClient

UNSUPPORTED_FIELDS = frozenset(
    {
        "background",
        "conversation",
        "max_output_tokens",
        "max_tool_calls",
        "metadata",
        "moderation",
        "multi_agent",
        "prompt",
        "prompt_cache_retention",
        "safety_identifier",
        "temperature",
        "top_logprobs",
        "top_p",
        "truncation",
        "user",
        "previous_response_id",
    }
)


def prepare_chatgpt_request(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the public SIWC HTTP contract after all user-supplied extra parameters."""
    result = deepcopy({key: value for key, value in kwargs.items() if not isinstance(value, Omit)})
    if result.get("extra_headers") or result.get("extra_query"):
        raise ValueError("ChatGPT SIWC 不接受模型级别的自定义请求头或 query。")
    body = result.pop("extra_body", None) or {}
    if any(key in body for key in ("input", "model", "stream", "store", "previous_response_id")):
        raise ValueError("ChatGPT SIWC 不允许覆盖受保护的请求字段。")
    for key in UNSUPPORTED_FIELDS:
        result.pop(key, None)
        body.pop(key, None)
    result.update(store=False, stream=True)
    inputs = result.get("input")
    if not isinstance(inputs, list):
        raise ValueError("ChatGPT SIWC 要求完整上下文 input 数组。")
    tools = result.get("tools") or []
    functions, native = [], []
    for tool in tools:
        if tool.get("type") == "function":
            functions.append(tool)
        elif tool.get("type") in {"web_search", "web_search_preview"}:
            native.append(tool)
        else:
            raise ValueError("ChatGPT SIWC 此客户端仅支持本地 function 工具与受账号许可的 web_search。")
    if functions:
        native.append({"type": "namespace", "name": "maibot", "description": "MaiBot local tools", "tools": functions})
    if "tools" in result:
        result["tools"] = native
    for item in inputs:
        if item.get("role") == "system":
            item["role"] = "developer"
        if item.get("type") == "function_call":
            item["namespace"] = "maibot"
    choice = body.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") == "function":
        choice["namespace"] = "maibot"
    if body:
        result["extra_body"] = body
    return result


@client_registry.register_client_class("chatgpt")
class ChatGPTClient(OpenAIResponsesClient):
    """Explicit account label, protected local grants, official HTTPS endpoint only."""

    def __init__(self, api_provider: APIProvider) -> None:
        BaseClient.__init__(self, api_provider)
        self.accounts = ChatGPTAccountStore()
        self.tool_argument_parse_mode = ToolArgumentParseMode(api_provider.tool_argument_parse_mode)
        self._omit_sdk_authorization = False
        self.client = AsyncOpenAI(
            api_key="not-signed-in",
            base_url=RESOURCE,
            organization="",
            project="",
            max_retries=0,
            timeout=api_provider.timeout,
            http_client=DefaultAsyncHttpxClient(
                verify=get_shared_ssl_context(),
                follow_redirects=False,
                trust_env=False,
            ),
        )

    def _prepare_response_kwargs(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return prepare_chatgpt_request(kwargs)

    def _replay_provider_name(self) -> str:
        return json.dumps(["chatgpt", self.api_provider.name, self.api_provider.chatgpt_account])

    async def get_response(self, request: ResponseRequest) -> APIResponse:
        try:
            # Resolve credentials per request, outside the event loop. The store serializes
            # refreshes across processes; the provider's account never changes implicitly.
            self.client.api_key = await asyncio.to_thread(self.accounts.access_token, self.api_provider.chatgpt_account)
            return await super().get_response(request)
        except ReqAbortException:
            raise
        except Exception:
            # Abort the orchestrator's model-fallback chain, including another paid provider.
            # Never propagate SDK/HTTP objects carrying bearer headers into diagnostics.
            raise ReqAbortException(
                f"ChatGPT 请求未完成；请检查独立登录、账号权限和用量：{USAGE_URL}。未切换账号或 API 计费。"
            ) from None

    def _attach_failure_snapshot(
        self,
        error: Exception,
        request: ResponseRequest,
        provider_request: Dict[str, Any],
        *,
        original_error: Exception | None = None,
    ) -> None:
        # The normal error object may retain an authenticated httpx.Request. Save only
        # the safe request/body snapshot and a fixed diagnostic, never the SDK error.
        super()._attach_failure_snapshot(
            ReqAbortException("ChatGPT Responses request failed"),
            request,
            provider_request,
        )

    async def get_embedding(self, request: EmbeddingRequest) -> APIResponse:
        raise ReqAbortException("ChatGPT SIWC 不支持嵌入端点；不会自动使用其他提供商。")

    async def get_image_embedding(self, request: ImageEmbeddingRequest) -> APIResponse:
        raise ReqAbortException("ChatGPT SIWC 不支持图片嵌入端点；不会自动使用其他提供商。")

    async def get_audio_transcriptions(self, request: AudioTranscriptionRequest) -> APIResponse:
        raise ReqAbortException("ChatGPT SIWC 不支持语音转录端点；不会自动使用其他提供商。")
