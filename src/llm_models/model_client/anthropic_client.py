from typing import Any, AsyncIterator, Dict, List, Tuple

import asyncio
import json

import httpx

from src.common.http_client import get_shared_ssl_context_async
from src.config.model_configs import APIProvider
from src.llm_models.exceptions import (
    EmptyResponseException,
    LLMRequestError,
    NetworkConnectionError,
    RespNotOkException,
    RespParseException,
)
from src.llm_models.openai_compat import build_openai_compatible_client_config
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextImagePart,
    ContextItemMeta,
    ContextRefusalPart,
    ContextTextPart,
    ContextToolCall,
    FunctionCallItem,
    FunctionCallOutputItem,
    ModelOutputItem,
    ProviderReplayFragment,
    ProviderScope,
    ReasoningItem,
    ReasoningRepresentation,
    SystemMessageItem,
    UserMessageItem,
    build_provider_endpoint_fingerprint,
)
from src.llm_models.payload_content.resp_format import RespFormatType

from .adapter_base import (
    AdapterClient,
    ProviderResponseParser,
    ProviderStreamResponseHandler,
    await_task_with_interrupt,
)
from .base_client import (
    APIResponse,
    AudioTranscriptionRequest,
    EmbeddingRequest,
    ResponseRequest,
    UsageTuple,
    client_registry,
)

WireObject = Dict[str, Any]
IMAGE_FORMATS = ("jpeg", "png", "gif", "webp")
NATIVE_PARAMS = (
    "cache_control",
    "metadata",
    "output_config",
    "service_tier",
    "stop_sequences",
    "thinking",
    "top_k",
    "top_p",
)


def build_anthropic_endpoint(base_url: str, endpoint: str) -> httpx.URL:
    """接受 Anthropic SDK 基址与已经含 /v1 的基址。"""
    base = httpx.URL(base_url.strip())
    path = base.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1"
    return base.copy_with(path=f"{path}/{endpoint.lstrip('/')}")


def build_anthropic_http_options(provider: APIProvider) -> Tuple[httpx.Headers, Dict[str, Any]]:
    config = build_openai_compatible_client_config(provider)
    headers = httpx.Headers({"anthropic-version": "2023-06-01"})
    headers.update(config.default_headers)
    if config.api_key:
        headers["x-api-key"] = config.api_key
    return headers, config.default_query


def _build_payload(request: ResponseRequest, scope: ProviderScope) -> WireObject:
    messages: List[WireObject] = []
    system: List[WireObject] = []

    def append(role: str, blocks: List[WireObject]) -> None:
        if not blocks:
            return
        if not messages or messages[-1]["role"] != role:
            messages.append({"role": role, "content": []})
        content = messages[-1]["content"]
        if blocks[0]["type"] == "tool_result":
            # Anthropic requires tool results before regular content in the user turn.
            index = next((i for i, block in enumerate(content) if block["type"] != "tool_result"), len(content))
            content[index:index] = blocks
        else:
            content.extend(blocks)

    for item in request.context_items:
        if isinstance(item, SystemMessageItem):
            blocks = [{"type": "text", "text": part.text} for part in item.parts if isinstance(part, ContextTextPart)]
            if len(blocks) != len(item.parts):
                raise LLMRequestError("Anthropic 系统消息仅支持文本")
            system.extend(blocks)
        elif isinstance(item, (UserMessageItem, AssistantMessageItem)):
            blocks = []
            for part in item.parts:
                if isinstance(part, ContextTextPart):
                    blocks.append({"type": "text", "text": part.text})
                elif isinstance(part, ContextRefusalPart):
                    blocks.append({"type": "text", "text": part.refusal})
                elif isinstance(part, ContextImagePart) and isinstance(item, UserMessageItem):
                    if part.normalized_image_format not in IMAGE_FORMATS:
                        raise LLMRequestError(f"Anthropic 不支持 {part.image_format} 图片格式")
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": f"image/{part.normalized_image_format}",
                                "data": part.image_base64,
                            },
                        }
                    )
                else:
                    raise LLMRequestError("Anthropic assistant 消息仅支持文本")
            append(item.role.value, blocks)
        elif isinstance(item, FunctionCallItem):
            call = item.tool_call
            append(
                "assistant",
                [
                    {
                        "type": "tool_use",
                        "id": call.call_id,
                        "name": call.func_name,
                        "input": call.materialize_args(),
                    }
                ],
            )
        elif isinstance(item, FunctionCallOutputItem):
            append(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": item.call_id,
                        "content": item.output,
                        "is_error": not item.success,
                    }
                ],
            )
        elif isinstance(item, ReasoningItem) and item.replay is not None and item.replay.scope == scope:
            append("assistant", [item.replay.materialize()])

    payload = {key: request.extra_params[key] for key in NATIVE_PARAMS if key in request.extra_params}
    payload.update(
        model=scope.model_identifier,
        messages=messages,
        max_tokens=4096 if request.max_tokens is None else request.max_tokens,
    )
    if system:
        payload["system"] = system
    # 新版 Claude 拒绝 MaiBot 任务级的默认温度。仅传递用户为这个模型明确配置的值。
    if (
        request.temperature is not None
        and (request.model_info.temperature is not None or "temperature" in request.extra_params)
        and (payload.get("thinking") or {}).get("type") not in {"adaptive", "enabled"}
    ):
        payload["temperature"] = request.temperature
    if request.tool_options:
        payload["tools"] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters_schema or {"type": "object", "properties": {}},
            }
            for tool in request.tool_options
        ]
        if request.tool_choice == "none":
            payload["tool_choice"] = {"type": "none"}
    if request.response_format is not None:
        schema = request.response_format.get_schema_object()
        if schema is not None:
            _close_object_schemas(schema)
            payload["output_config"] = {
                **payload.get("output_config", {}),
                "format": {"type": "json_schema", "schema": schema},
            }
        elif request.response_format.format_type == RespFormatType.JSON_OBJ:
            system.append({"type": "text", "text": "Respond with a valid JSON object only."})
            payload["system"] = system
    return payload


def _close_object_schemas(schema: WireObject) -> None:
    """补齐 Anthropic 结构化输出要求的封闭对象声明。"""
    if schema.get("type") == "object":
        schema.setdefault("additionalProperties", False)
    for key in ("properties", "$defs", "definitions"):
        for child in schema.get(key, {}).values():
            if isinstance(child, dict):
                _close_object_schemas(child)
    for key in ("anyOf", "allOf", "oneOf", "prefixItems"):
        for child in schema.get(key, ()):
            if isinstance(child, dict):
                _close_object_schemas(child)
    if isinstance(schema.get("items"), dict):
        _close_object_schemas(schema["items"])


def _parse_response(
    response: WireObject,
    request: ResponseRequest,
    scope: ProviderScope,
) -> Tuple[APIResponse, UsageTuple]:
    items: List[ModelOutputItem] = []
    try:
        for block in response["content"]:
            kind = block["type"]
            meta = ContextItemMeta.create(logical_turn_id=request.logical_turn_id)
            if kind == "text" and block["text"]:
                part = (
                    ContextRefusalPart(block["text"])
                    if response.get("stop_reason") == "refusal"
                    else ContextTextPart(block["text"])
                )
                items.append(AssistantMessageItem(meta=meta, parts=(part,)))
            elif kind in {"thinking", "redacted_thinking"}:
                summary = block.get("thinking")
                items.append(
                    ReasoningItem(
                        meta=meta,
                        summary_parts=(summary,) if summary else (),
                        representation=ReasoningRepresentation.SUMMARY if summary else ReasoningRepresentation.OPAQUE,
                        replay=ProviderReplayFragment.from_payload(scope, block),
                    )
                )
            elif kind == "tool_use":
                items.append(
                    FunctionCallItem(
                        meta=meta,
                        tool_call=ContextToolCall.create(
                            call_id=block["id"], func_name=block["name"], args=block["input"]
                        ),
                    )
                )
        usage = response["usage"]
        read = usage.get("cache_read_input_tokens") or 0
        missed = usage["input_tokens"] + (usage.get("cache_creation_input_tokens") or 0)
        output = usage["output_tokens"]
    except (KeyError, TypeError, ValueError) as error:
        raise RespParseException(message=f"Anthropic Messages 响应格式错误: {error}") from error

    if not items:
        raise EmptyResponseException(message=f"Anthropic Messages 响应为空（{response.get('stop_reason')}）")
    cache_reported = any(
        usage.get(key) is not None for key in ("cache_read_input_tokens", "cache_creation_input_tokens")
    )
    return APIResponse(output_items=tuple(items), provider_response=response), (
        read + missed,
        output,
        read + missed + output,
        read,
        missed,
        cache_reported,
    )


async def _stream_events(response: httpx.Response) -> AsyncIterator[WireObject]:
    data: List[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif not line and data:
            yield json.loads("\n".join(data))
            data.clear()


async def _collect_stream(events: AsyncIterator[WireObject]) -> WireObject:
    message: WireObject = {}
    blocks: Dict[int, WireObject] = {}
    tool_inputs: Dict[int, List[str]] = {}
    async for event in events:
        kind = event["type"]
        if kind == "message_start":
            message = event["message"]
        elif kind == "content_block_start":
            blocks[event["index"]] = event["content_block"]
        elif kind == "content_block_delta":
            index, delta = event["index"], event["delta"]
            block = blocks[index]
            for field in ("text", "thinking", "signature"):
                if field in delta:
                    block[field] = block.get(field, "") + delta[field]
            if delta["type"] == "input_json_delta":
                tool_inputs.setdefault(index, []).append(delta["partial_json"])
        elif kind == "content_block_stop":
            index = event["index"]
            if index in tool_inputs:
                blocks[index]["input"] = json.loads("".join(tool_inputs.pop(index)))
        elif kind == "message_delta":
            message.update(event["delta"])
            message["usage"].update(event["usage"])
        elif kind == "message_stop":
            message["content"] = [blocks[index] for index in sorted(blocks)]
            return message
        elif kind == "error":
            error = event["error"]
            status = {"overloaded_error": 529, "rate_limit_error": 429, "api_error": 500}.get(error["type"], 400)
            raise RespNotOkException(status, error["message"])
    raise RespParseException(message="Anthropic Messages 响应流在 message_stop 前结束")


@client_registry.register_client_class("anthropic")
class AnthropicClient(AdapterClient[AsyncIterator[WireObject], WireObject]):
    """通过 Anthropic Messages API 请求 Claude 或兼容服务。"""

    def _scope(self, request: ResponseRequest) -> ProviderScope:
        provider = self.api_provider
        return ProviderScope(
            schema_version=1,
            client_type=provider.client_type,
            provider_name=provider.name,
            endpoint_fingerprint=build_provider_endpoint_fingerprint(provider.client_type, provider.base_url),
            model_identifier=request.model_info.model_identifier,
        )

    def _build_default_response_parser(self, request: ResponseRequest) -> ProviderResponseParser[WireObject]:
        scope = self._scope(request)

        def parse(response: WireObject) -> Tuple[APIResponse, UsageTuple]:
            return _parse_response(response, request, scope)

        return parse

    def _build_default_stream_response_handler(
        self,
        request: ResponseRequest,
    ) -> ProviderStreamResponseHandler[AsyncIterator[WireObject]]:
        parser = self._build_default_response_parser(request)

        async def handle(
            events: AsyncIterator[WireObject],
            interrupt_flag: asyncio.Event | None,
        ) -> Tuple[APIResponse, UsageTuple | None]:
            return parser(await _collect_stream(events))

        return handle

    async def _execute_response_request(
        self,
        request: ResponseRequest,
        stream_response_handler: ProviderStreamResponseHandler[AsyncIterator[WireObject]],
        response_parser: ProviderResponseParser[WireObject],
    ) -> Tuple[APIResponse, UsageTuple | None]:
        payload = _build_payload(request, self._scope(request))
        streaming = request.model_info.force_stream_mode
        if streaming:
            payload["stream"] = True
        provider = self.api_provider
        headers, query = build_anthropic_http_options(provider)
        url = build_anthropic_endpoint(provider.base_url, "/messages")

        async def execute() -> Tuple[APIResponse, UsageTuple | None]:
            async with httpx.AsyncClient(
                verify=await get_shared_ssl_context_async(), timeout=provider.timeout
            ) as client:
                async with client.stream("POST", url, headers=headers, params=query, json=payload) as response:
                    if not response.is_success:
                        await response.aread()
                        raise RespNotOkException(response.status_code, response.text)
                    if streaming:
                        return await stream_response_handler(_stream_events(response), request.interrupt_flag)
                    await response.aread()
                    return response_parser(response.json())

        try:
            response, usage = await await_task_with_interrupt(asyncio.create_task(execute()), request.interrupt_flag)
        except httpx.TimeoutException as error:
            raise RespNotOkException(408, "Anthropic Messages 请求超时") from error
        except httpx.RequestError as error:
            raise NetworkConnectionError(f"Anthropic Messages 连接失败: {error}") from error
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise RespParseException(message=f"Anthropic Messages 响应格式错误: {error}") from error
        response.wire_protocol = "anthropic_messages"
        response.request_wire_payload = payload
        response.request_snapshot = request.detached_snapshot()
        return response, usage

    async def _execute_embedding_request(self, request: EmbeddingRequest) -> Tuple[APIResponse, UsageTuple | None]:
        raise LLMRequestError("Anthropic Messages API 不提供文本嵌入")

    async def _execute_audio_transcription_request(
        self,
        request: AudioTranscriptionRequest,
    ) -> Tuple[APIResponse, UsageTuple | None]:
        raise LLMRequestError("Anthropic Messages API 不提供音频转录")

    def get_support_image_formats(self) -> List[str]:
        return ["jpg", *IMAGE_FORMATS]
