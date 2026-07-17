# ruff: noqa: B025

from typing import Any, AsyncIterator, Callable, Coroutine, Dict, List, Optional, Tuple, cast

import asyncio
import base64
import binascii
import io
import json

from google import genai
from google.genai.errors import (
    ClientError,
    FunctionInvocationError,
    ServerError,
    UnknownFunctionCallArgumentError,
    UnsupportedFunctionError,
)
from google.genai.types import (
    Candidate,
    Content,
    ContentListUnion,
    ContentUnion,
    EmbedContentConfig,
    EmbedContentResponse,
    FunctionDeclaration,
    FunctionCall,
    FunctionResponse,
    GenerateContentConfig,
    GenerateContentResponse,
    GoogleSearch,
    HarmBlockThreshold,
    HarmCategory,
    HttpOptions,
    Part,
    SafetySetting,
    ThinkingConfig,
    Tool,
)

from src.common.logger import get_logger
from src.config.model_configs import APIProvider
from src.llm_models.exceptions import (
    EmptyResponseException,
    NetworkConnectionError,
    ReqAbortException,
    RespNotOkException,
    RespParseException,
)
from src.llm_models.payload_content.message import ImageMessagePart, Message, RoleType, TextMessagePart
from src.llm_models.payload_content.resp_format import RespFormat, RespFormatType
from src.llm_models.payload_content.tool_option import ToolCall, ToolOption

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
from ..request_snapshot import (
    attach_request_snapshot,
    has_request_snapshot,
    save_failed_request_snapshot,
    serialize_audio_request_snapshot,
    serialize_embedding_request_snapshot,
    serialize_response_request_snapshot,
)

logger = get_logger("Gemini客户端")

GeminiStreamResponseHandler = Callable[
    [AsyncIterator[GenerateContentResponse], asyncio.Event | None],
    Coroutine[Any, Any, Tuple[APIResponse, Optional[UsageTuple]]],
]
"""Gemini 流式响应处理函数类型。"""

GeminiResponseParser = Callable[[GenerateContentResponse], Tuple[APIResponse, Optional[UsageTuple]]]
"""Gemini 非流式响应解析函数类型。"""

THINKING_BUDGET_LIMITS: Dict[str, Dict[str, int | bool]] = {
    "gemini-2.5-flash": {"min": 1, "max": 24576, "can_disable": True},
    "gemini-2.5-flash-lite": {"min": 512, "max": 24576, "can_disable": True},
    "gemini-2.5-pro": {"min": 128, "max": 32768, "can_disable": False},
}
"""不同 Gemini 模型允许的思考预算范围。"""

THINKING_BUDGET_AUTO = -1
"""自动思考预算模式，由模型自行决定。"""

THINKING_BUDGET_DISABLED = 0
"""禁用思考预算模式。仅部分模型支持。"""

GEMINI_SAFE_SETTINGS: List[SafetySetting] = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY, threshold=HarmBlockThreshold.BLOCK_NONE),
]
"""默认安全策略，避免 Gemini 在部分内容上返回空响应。"""

GENERATE_CONFIG_RESERVED_EXTRA_PARAMS = {
    "thinking_budget",
    "include_thoughts",
    "enable_google_search",
    "transcription_prompt",
    "audio_mime_type",
}
"""由当前客户端自行处理、不再直接透传给 `GenerateContentConfig` 的额外参数。"""

EMBED_CONFIG_SUPPORTED_EXTRA_PARAMS = {
    "task_type",
    "title",
    "output_dimensionality",
    "mime_type",
    "auto_truncate",
}
"""可透传给 `EmbedContentConfig` 的额外参数字段。"""

GEMINI_EXTRA_CONTENT_PROVIDER_KEY = "google"
GEMINI_EXTRA_CONTENT_THOUGHT_SIGNATURE_KEY = "thought_signature"
GEMINI_FALLBACK_THOUGHT_SIGNATURE = b"skip_thought_signature_validator"
"""当历史 function call 没有原始 thought signature 时，使用官方允许的占位签名跳过校验。"""


def _normalize_image_mime_type(image_format: str) -> str:
    """将图片格式名称转换为标准 MIME 类型。

    Args:
        image_format: 图片格式名，例如 `png`、`jpg`。

    Returns:
        str: 规范化后的图片 MIME 类型。
    """
    normalized_image_format = image_format.lower()
    if normalized_image_format in {"jpg", "jpeg"}:
        return "image/jpeg"
    return f"image/{normalized_image_format}"


def _build_non_tool_parts(message: Message) -> List[Part]:
    """将消息中的文本与图片片段转换为 Gemini `Part` 列表。

    Args:
        message: 内部统一消息对象。

    Returns:
        List[Part]: Gemini 所需的内容片段列表。
    """
    converted_parts: List[Part] = []
    for message_part in message.parts:
        if isinstance(message_part, TextMessagePart):
            converted_parts.append(Part.from_text(text=message_part.text))
            continue
        if isinstance(message_part, ImageMessagePart):
            converted_parts.append(
                Part.from_bytes(
                    data=base64.b64decode(message_part.image_base64),
                    mime_type=_normalize_image_mime_type(message_part.normalized_image_format),
                )
            )
    return converted_parts


def _normalize_function_response_payload(message: Message) -> Dict[str, Any]:
    """将内部工具结果消息转换为 Gemini 函数响应负载。

    Args:
        message: 工具结果消息。

    Returns:
        Dict[str, Any]: 可用于 `Part.from_function_response()` 的响应对象。
    """
    content = message.content
    if isinstance(content, str):
        stripped_content = content.strip()
        if not stripped_content:
            return {}
        try:
            parsed_content = json.loads(stripped_content)
        except json.JSONDecodeError:
            return {"result": content}
        if isinstance(parsed_content, dict):
            return parsed_content
        return {"result": parsed_content}

    return {"result": content}


def _build_gemini_tool_call_extra_content(thought_signature: bytes | None) -> Dict[str, Any] | None:
    """将 Gemini thought signature 编码为内部工具调用附加信息。"""
    if not thought_signature:
        return None
    return {
        GEMINI_EXTRA_CONTENT_PROVIDER_KEY: {
            GEMINI_EXTRA_CONTENT_THOUGHT_SIGNATURE_KEY: base64.b64encode(thought_signature).decode("ascii")
        }
    }


def _extract_gemini_thought_signature(tool_call: ToolCall) -> bytes | None:
    """从内部工具调用附加信息中提取 Gemini thought signature。"""
    if not tool_call.extra_content:
        return None

    provider_payload = tool_call.extra_content.get(GEMINI_EXTRA_CONTENT_PROVIDER_KEY)
    if not isinstance(provider_payload, dict):
        return None

    raw_thought_signature = provider_payload.get(GEMINI_EXTRA_CONTENT_THOUGHT_SIGNATURE_KEY)
    if isinstance(raw_thought_signature, bytes):
        return raw_thought_signature
    if not isinstance(raw_thought_signature, str):
        return None

    normalized_signature = raw_thought_signature.strip()
    if not normalized_signature:
        return None

    try:
        return base64.b64decode(normalized_signature.encode("ascii"), validate=True)
    except (binascii.Error, ValueError):
        return normalized_signature.encode("utf-8")


def _build_gemini_function_call_part(
    tool_call: ToolCall,
    *,
    inject_fallback_signature: bool,
) -> Part:
    """根据内部工具调用构建 Gemini function call part。"""
    thought_signature = _extract_gemini_thought_signature(tool_call)
    if thought_signature is None and inject_fallback_signature:
        thought_signature = GEMINI_FALLBACK_THOUGHT_SIGNATURE

    return Part(
        function_call=FunctionCall(
            id=tool_call.call_id,
            name=tool_call.func_name,
            args=tool_call.args or {},
        ),
        thought_signature=thought_signature,
    )


def _get_candidates(response: GenerateContentResponse) -> List[Candidate]:
    """安全获取 Gemini 响应中的候选列表。

    Args:
        response: Gemini 响应对象。

    Returns:
        List[Candidate]: 非空时返回原候选列表，否则返回空列表。
    """
    return response.candidates or []


def _extract_response_json_schema(response_format: RespFormat) -> Dict[str, object] | None:
    """从内部响应格式中提取可供 Gemini 使用的 JSON Schema。

    Args:
        response_format: 输出格式定义。

    Returns:
        Dict[str, object] | None: 可直接传给 `response_json_schema` 的 JSON Schema。
    """
    schema_payload = response_format.get_schema_object()
    if schema_payload is None:
        return None
    return cast(Dict[str, object], schema_payload)


def _append_content_if_non_empty(contents: List[ContentUnion], content: Content) -> None:
    """仅在 Content 含有有效 parts 时追加，避免 Gemini 因空 parts 报错。"""
    if content.parts:
        contents.append(content)
    else:
        logger.debug("跳过 parts 为空的 Gemini Content（role=%s）", content.role)


def _convert_messages(messages: List[Message]) -> Tuple[ContentListUnion, str | None]:
    """将内部统一消息列表转换为 Gemini 内容结构。

    Args:
        messages: 内部统一消息列表。

    Returns:
        Tuple[ContentListUnion, str | None]: `contents` 与可选的 `system_instruction`。

    Raises:
        ValueError: 当消息结构无法映射到 Gemini 内容模型，或有效对话内容为空时抛出。
    """
    contents: List[ContentUnion] = []
    system_instruction_chunks: List[str] = []
    tool_name_by_call_id: Dict[str, str] = {}
    # Gemini 要求同一轮并行 function call 的全部 function response 放在同一条 Content 的 parts 中；
    # 若拆成多条 Content，会报 “number of function response parts is equal to ...”。
    pending_tool_response_parts: List[Part] = []

    def flush_pending_tool_responses() -> None:
        nonlocal pending_tool_response_parts
        if not pending_tool_response_parts:
            return
        _append_content_if_non_empty(
            contents,
            Content(role="tool", parts=list(pending_tool_response_parts)),
        )
        pending_tool_response_parts = []

    for message in messages:
        if message.role == RoleType.System:
            flush_pending_tool_responses()
            system_text = message.get_text_content().strip()
            if not system_text:
                raise ValueError("Gemini 的 system message 必须为非空文本")
            system_instruction_chunks.append(system_text)
            continue

        if message.role == RoleType.User:
            flush_pending_tool_responses()
            _append_content_if_non_empty(
                contents,
                Content(role="user", parts=_build_non_tool_parts(message)),
            )
            continue

        if message.role == RoleType.Assistant:
            flush_pending_tool_responses()
            assistant_parts = _build_non_tool_parts(message)
            if message.tool_calls:
                for tool_call_index, tool_call in enumerate(message.tool_calls):
                    assistant_parts.append(
                        _build_gemini_function_call_part(
                            tool_call,
                            inject_fallback_signature=tool_call_index == 0,
                        )
                    )
                    tool_name_by_call_id[tool_call.call_id] = tool_call.func_name
            _append_content_if_non_empty(
                contents,
                Content(role="model", parts=assistant_parts),
            )
            continue

        if message.role == RoleType.Tool:
            if not message.tool_call_id:
                raise ValueError("Gemini 工具结果消息缺少 tool_call_id")
            tool_name = (message.tool_name or tool_name_by_call_id.get(message.tool_call_id, "")).strip()
            if not tool_name:
                raise ValueError(
                    f"Gemini 无法根据 tool_call_id={message.tool_call_id} 找到对应的工具名称，"
                    "且消息中未携带 tool_name"
                )
            tool_name_by_call_id[message.tool_call_id] = tool_name
            pending_tool_response_parts.append(
                Part(
                    function_response=FunctionResponse(
                        id=message.tool_call_id,
                        name=tool_name,
                        response=_normalize_function_response_payload(message),
                    )
                )
            )
            continue

        raise ValueError(f"不支持的消息角色: {message.role}")

    flush_pending_tool_responses()
    system_instruction = "\n\n".join(chunk for chunk in system_instruction_chunks if chunk.strip()) or None

    # Gemini 要求 contents 至少包含一条带 parts 的消息；仅有 system_instruction 时 API 会直接报错。
    # 在仅有 system 消息的场景下，补一条最小 user 占位，保证请求可提交。
    if not contents:
        if system_instruction:
            logger.debug("仅有 system 消息，补充最小 user 内容以满足 Gemini contents 非空要求")
            contents.append(Content(role="user", parts=[Part.from_text(text="请根据系统指令继续。")]))
        else:
            raise ValueError("Gemini 请求的 contents 不能为空，至少需要一条 user/assistant/tool 消息")

    return contents, system_instruction


def _build_tools(tool_options: List[ToolOption]) -> List[Tool]:
    """将内部工具定义转换为 Gemini `Tool` 列表。

    Args:
        tool_options: 内部统一工具定义列表。

    Returns:
        List[Tool]: Gemini 所需工具列表。
    """
    function_declarations: List[FunctionDeclaration] = []
    for tool_option in tool_options:
        payload: Dict[str, Any] = {
            "name": tool_option.name,
            "description": tool_option.description,
        }
        if tool_option.parameters_schema is not None:
            payload["parameters_json_schema"] = tool_option.parameters_schema
        function_declarations.append(FunctionDeclaration(**payload))
    return [Tool(function_declarations=function_declarations)] if function_declarations else []


def _extract_usage_record(response: GenerateContentResponse) -> Optional[UsageTuple]:
    """从 Gemini 响应中提取使用量信息。

    Args:
        response: Gemini 响应对象。

    Returns:
        Optional[UsageTuple]: 统一的使用量三元组；缺失时返回 `None`。
    """
    usage_metadata = getattr(response, "usage_metadata", None)
    if usage_metadata is None:
        return None
    prompt_tokens = getattr(usage_metadata, "prompt_token_count", 0) or 0
    completion_tokens = (
        (getattr(usage_metadata, "candidates_token_count", 0) or 0)
        + (getattr(usage_metadata, "thoughts_token_count", 0) or 0)
    )
    total_tokens = getattr(usage_metadata, "total_token_count", 0) or 0
    return prompt_tokens, completion_tokens, total_tokens


def _extract_finish_reason(response: GenerateContentResponse | None) -> str | None:
    """提取 Gemini 响应的结束原因。

    Args:
        response: Gemini 响应对象。

    Returns:
        str | None: 结束原因字符串；获取失败时返回 `None`。
    """
    if response is None:
        return None
    candidates = _get_candidates(response)
    if not candidates:
        return None
    for candidate in candidates:
        finish_reason = getattr(candidate, "finish_reason", None) or getattr(candidate, "finishReason", None)
        if finish_reason:
            return str(finish_reason)
    return None


def _warn_if_max_tokens_truncated(
    response: GenerateContentResponse | None,
    content: str | None,
    tool_calls: List[ToolCall] | None,
) -> None:
    """在 Gemini 因 token 限制截断时输出警告。

    Args:
        response: Gemini 响应对象。
        content: 已解析的可见文本内容。
        tool_calls: 已解析的工具调用列表。
    """
    finish_reason = _extract_finish_reason(response)
    if finish_reason is None or "MAX_TOKENS" not in finish_reason:
        return
    has_visible_output = bool((content and content.strip()) or tool_calls)
    if has_visible_output:
        logger.warning(
            "Gemini 响应因达到 max_tokens 限制被部分截断，可能影响回复完整性，建议调整模型 max_tokens 配置。"
        )
        return
    logger.warning("Gemini 响应因达到 max_tokens 限制被截断，且未返回可见输出，请检查模型 max_tokens 配置。")


def _collect_function_calls(response: GenerateContentResponse) -> List[ToolCall]:
    """从 Gemini 响应中提取工具调用列表。

    Args:
        response: Gemini 响应对象。

    Returns:
        List[ToolCall]: 规范化后的工具调用列表。

    Raises:
        RespParseException: 当函数调用结构不合法时抛出。
    """
    candidates = _get_candidates(response)
    tool_calls: List[ToolCall] = []

    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            function_call = getattr(part, "function_call", None)
            if function_call is None:
                continue

            call_name = getattr(function_call, "name", None)
            call_id = getattr(function_call, "id", None) or f"gemini-tool-call-{len(tool_calls) + 1}"
            call_args = getattr(function_call, "args", None) or {}
            if not isinstance(call_name, str) or not call_name:
                raise RespParseException(response, "响应解析失败，Gemini 工具调用缺少 name 字段")
            if not isinstance(call_args, dict):
                raise RespParseException(response, "响应解析失败，Gemini 工具调用参数无法解析为字典")

            tool_calls.append(
                ToolCall(
                    call_id=call_id,
                    func_name=call_name,
                    args=call_args,
                    extra_content=_build_gemini_tool_call_extra_content(getattr(part, "thought_signature", None)),
                )
            )

    if tool_calls:
        return tool_calls

    raw_function_calls = getattr(response, "function_calls", None)
    if not raw_function_calls:
        return []

    for index, function_call in enumerate(raw_function_calls, start=1):
        call_name = getattr(function_call, "name", None)
        call_id = getattr(function_call, "id", None) or f"gemini-tool-call-{index}"
        call_args = getattr(function_call, "args", None) or {}
        if not isinstance(call_name, str) or not call_name:
            raise RespParseException(response, "响应解析失败，Gemini 工具调用缺少 name 字段")
        if not isinstance(call_args, dict):
            raise RespParseException(response, "响应解析失败，Gemini 工具调用参数无法解析为字典")
        tool_calls.append(ToolCall(call_id=call_id, func_name=call_name, args=call_args))
    return tool_calls


def _process_stream_chunk(
    chunk: GenerateContentResponse,
    content_buffer: io.StringIO,
    tool_calls_buffer: List[ToolCall],
    response: APIResponse,
) -> None:
    """处理单个 Gemini 流式响应块。

    Args:
        chunk: 当前流式响应块。
        content_buffer: 正文缓冲区。
        tool_calls_buffer: 工具调用缓冲区。
        response: 当前累积的统一响应对象。
    """
    candidates = _get_candidates(chunk)
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = getattr(part, "text", None)
            if not part_text:
                continue
            if getattr(part, "thought", False):
                response.reasoning_content = (response.reasoning_content or "") + part_text
            else:
                content_buffer.write(part_text)

    tool_calls_buffer.extend(_collect_function_calls(chunk))


def _build_stream_api_response(
    content_buffer: io.StringIO,
    tool_calls_buffer: List[ToolCall],
    last_response: GenerateContentResponse | None,
    response: APIResponse,
) -> APIResponse:
    """根据流式缓冲区内容构建统一响应对象。

    Args:
        content_buffer: 正文缓冲区。
        tool_calls_buffer: 工具调用缓冲区。
        last_response: 最后一个 Gemini 响应块。
        response: 已累积的响应对象。

    Returns:
        APIResponse: 构建完成的统一响应对象。

    Raises:
        EmptyResponseException: 响应中既无正文也无工具调用且无思考内容时抛出。
    """
    if content_buffer.tell() > 0:
        response.content = content_buffer.getvalue()
    content_buffer.close()

    if tool_calls_buffer:
        response.tool_calls = list(tool_calls_buffer)
    response.raw_data = last_response

    _warn_if_max_tokens_truncated(last_response, response.content, response.tool_calls)
    if not response.content and not response.tool_calls and not response.reasoning_content:
        raise EmptyResponseException(last_response)
    return response


async def _default_stream_response_handler(
    response_stream: AsyncIterator[GenerateContentResponse],
    interrupt_flag: asyncio.Event | None,
) -> Tuple[APIResponse, Optional[UsageTuple]]:
    """处理 Gemini 流式响应。

    Args:
        response_stream: Gemini 异步流式响应迭代器。
        interrupt_flag: 外部中断标记。

    Returns:
        Tuple[APIResponse, Optional[UsageTuple]]: 统一响应对象与可选的使用量信息。
    """
    content_buffer = io.StringIO()
    tool_calls_buffer: List[ToolCall] = []
    api_response = APIResponse()
    usage_record: Optional[UsageTuple] = None
    last_response: GenerateContentResponse | None = None

    try:
        async for chunk in response_stream:
            last_response = chunk
            if interrupt_flag and interrupt_flag.is_set():
                raise ReqAbortException("请求被外部信号中断")
            _process_stream_chunk(chunk, content_buffer, tool_calls_buffer, api_response)
            usage_record = _extract_usage_record(chunk) or usage_record
        return _build_stream_api_response(content_buffer, tool_calls_buffer, last_response, api_response), usage_record
    except Exception:
        if not content_buffer.closed:
            content_buffer.close()
        raise


def _default_normal_response_parser(
    response: GenerateContentResponse,
) -> Tuple[APIResponse, Optional[UsageTuple]]:
    """解析 Gemini 非流式响应。

    Args:
        response: Gemini 响应对象。

    Returns:
        Tuple[APIResponse, Optional[UsageTuple]]: 统一响应对象与可选的使用量信息。

    Raises:
        EmptyResponseException: 响应中既无正文也无工具调用且无思考内容时抛出。
    """
    api_response = APIResponse(raw_data=response)
    visible_parts: List[str] = []

    for candidate in _get_candidates(response):
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = getattr(part, "text", None)
            if not part_text:
                continue
            if getattr(part, "thought", False):
                api_response.reasoning_content = (api_response.reasoning_content or "") + part_text
            else:
                visible_parts.append(part_text)

    api_response.content = "".join(visible_parts).strip() or getattr(response, "text", None)

    tool_calls = _collect_function_calls(response)
    if tool_calls:
        api_response.tool_calls = tool_calls

    usage_record = _extract_usage_record(response)
    _warn_if_max_tokens_truncated(response, api_response.content, api_response.tool_calls)
    if not api_response.content and not api_response.tool_calls and not api_response.reasoning_content:
        raise EmptyResponseException(response, "响应中既无文本内容也无工具调用")
    return api_response, usage_record


def _build_http_options(api_provider: APIProvider) -> HttpOptions:
    """根据 Provider 配置构建 Gemini SDK 的 `HttpOptions`。

    Args:
        api_provider: API 提供商配置。

    Returns:
        HttpOptions: Gemini SDK HTTP 选项对象。
    """
    http_options_payload: Dict[str, Any] = {}
    if api_provider.timeout is not None:
        http_options_payload["timeout"] = int(api_provider.timeout * 1000)

    base_url = api_provider.base_url.strip()
    if base_url:
        normalized_base_url = base_url.rstrip("/")
        version_candidate = normalized_base_url.rsplit("/", 1)
        if len(version_candidate) == 2 and version_candidate[1].startswith("v"):
            http_options_payload["base_url"] = f"{version_candidate[0]}/"
            http_options_payload["api_version"] = version_candidate[1]
        else:
            http_options_payload["base_url"] = f"{normalized_base_url}/"

    return HttpOptions(**http_options_payload)


def _filter_generate_content_extra_params(extra_params: Dict[str, Any]) -> Dict[str, Any]:
    """筛选可透传给 `GenerateContentConfig` 的额外参数。

    Args:
        extra_params: 模型级额外参数。

    Returns:
        Dict[str, Any]: 可直接透传到 `GenerateContentConfig` 的字段字典。
    """
    filtered_params: Dict[str, Any] = {}
    for key, value in extra_params.items():
        if key in GENERATE_CONFIG_RESERVED_EXTRA_PARAMS:
            continue
        if key in GenerateContentConfig.model_fields:
            filtered_params[key] = value
    return filtered_params


def _build_embed_content_config(extra_params: Dict[str, Any]) -> EmbedContentConfig:
    """构建 Gemini 嵌入配置。

    Args:
        extra_params: 模型级额外参数。

    Returns:
        EmbedContentConfig: Gemini 嵌入配置对象。
    """
    config_payload: Dict[str, Any] = {"task_type": extra_params.get("task_type", "SEMANTIC_SIMILARITY")}
    for key in EMBED_CONFIG_SUPPORTED_EXTRA_PARAMS:
        if key == "task_type":
            continue
        if key in extra_params:
            config_payload[key] = extra_params[key]
    return EmbedContentConfig(**config_payload)


@client_registry.register_client_class("gemini")
class GeminiClient(AdapterClient[AsyncIterator[GenerateContentResponse], GenerateContentResponse]):
    """Gemini 官方 SDK 客户端适配器。"""

    client: genai.Client

    def __init__(self, api_provider: APIProvider) -> None:
        """初始化 Gemini 客户端。

        Args:
            api_provider: API 提供商配置。
        """
        super().__init__(api_provider)
        self.client = genai.Client(
            api_key=api_provider.api_key,
            http_options=_build_http_options(api_provider),
        )

    @staticmethod
    def clamp_thinking_budget(extra_params: Dict[str, Any] | None, model_id: str) -> int:
        """将思考预算裁剪到模型允许的范围内。

        Args:
            extra_params: 请求额外参数。
            model_id: 当前模型标识。

        Returns:
            int: 裁剪后的思考预算值。
        """
        thinking_budget = THINKING_BUDGET_AUTO
        if extra_params and "thinking_budget" in extra_params:
            try:
                thinking_budget = int(extra_params["thinking_budget"])
            except (TypeError, ValueError):
                logger.warning(f"无效的 thinking_budget={extra_params['thinking_budget']}，已回退为自动模式")

        limits: Dict[str, int | bool] | None = None
        if model_id in THINKING_BUDGET_LIMITS:
            limits = THINKING_BUDGET_LIMITS[model_id]
        else:
            for candidate_prefix in sorted(THINKING_BUDGET_LIMITS.keys(), key=len, reverse=True):
                if model_id == candidate_prefix or model_id.startswith(f"{candidate_prefix}-"):
                    limits = THINKING_BUDGET_LIMITS[candidate_prefix]
                    break

        if thinking_budget == THINKING_BUDGET_AUTO:
            return THINKING_BUDGET_AUTO

        if thinking_budget == THINKING_BUDGET_DISABLED:
            if limits and bool(limits.get("can_disable", False)):
                return THINKING_BUDGET_DISABLED
            if limits:
                minimum_value = int(limits["min"])
                logger.warning(f"模型 {model_id} 不支持禁用思考预算，已回退为最小值 {minimum_value}")
                return minimum_value
            return THINKING_BUDGET_AUTO

        if limits is None:
            logger.warning(f"模型 {model_id} 未配置思考预算范围，已回退为自动模式")
            return THINKING_BUDGET_AUTO

        minimum_value = int(limits["min"])
        maximum_value = int(limits["max"])
        if thinking_budget < minimum_value:
            logger.warning(f"模型 {model_id} 的 thinking_budget={thinking_budget} 过小，已调整为 {minimum_value}")
            return minimum_value
        if thinking_budget > maximum_value:
            logger.warning(f"模型 {model_id} 的 thinking_budget={thinking_budget} 过大，已调整为 {maximum_value}")
            return maximum_value
        return thinking_budget

    @staticmethod
    def _resolve_model_identifier(model_identifier: str, extra_params: Dict[str, Any]) -> Tuple[str, bool]:
        """解析请求实际使用的 Gemini 模型标识。

        Args:
            model_identifier: 原始模型标识。
            extra_params: 模型级额外参数。

        Returns:
            Tuple[str, bool]: `(实际模型标识, 是否启用 Google Search)`。
        """
        enable_google_search = bool(extra_params.get("enable_google_search", False))
        resolved_model_identifier = model_identifier
        if resolved_model_identifier.endswith("-search"):
            resolved_model_identifier = resolved_model_identifier.removesuffix("-search")
            enable_google_search = True
        return resolved_model_identifier, enable_google_search

    def _build_generation_config(
        self,
        *,
        model_identifier: str,
        system_instruction: str | None,
        tool_options: List[ToolOption] | None,
        response_format: RespFormat | None,
        max_tokens: int | None,
        temperature: float | None,
        extra_params: Dict[str, Any],
        enable_google_search: bool,
    ) -> GenerateContentConfig:
        """构建 Gemini 生成配置。

        Args:
            model_identifier: 当前请求实际使用的模型标识。
            system_instruction: 系统指令文本。
            tool_options: 内部工具定义列表。
            response_format: 输出格式定义。
            max_tokens: 最大输出 token 数。
            temperature: 温度参数。
            extra_params: 模型级额外参数。
            enable_google_search: 是否自动追加 Google Search 工具。

        Returns:
            GenerateContentConfig: Gemini 生成配置对象。
        """
        config_payload = _filter_generate_content_extra_params(extra_params)

        if max_tokens is not None and "max_output_tokens" not in config_payload:
            config_payload["max_output_tokens"] = max_tokens
        if temperature is not None and "temperature" not in config_payload:
            config_payload["temperature"] = temperature
        if system_instruction and "system_instruction" not in config_payload:
            config_payload["system_instruction"] = system_instruction
        if "response_modalities" not in config_payload:
            config_payload["response_modalities"] = ["TEXT"]
        if "safety_settings" not in config_payload:
            config_payload["safety_settings"] = GEMINI_SAFE_SETTINGS
        if "thinking_config" not in config_payload:
            config_payload["thinking_config"] = ThinkingConfig(
                include_thoughts=bool(extra_params.get("include_thoughts", True)),
                thinking_budget=self.clamp_thinking_budget(extra_params, model_identifier),
            )

        tools = _build_tools(tool_options) if tool_options else []
        if enable_google_search:
            tools.append(Tool(google_search=GoogleSearch()))
        if tools:
            if "tools" in config_payload:
                existing_tools = config_payload["tools"]
                if isinstance(existing_tools, list):
                    config_payload["tools"] = [*existing_tools, *tools]
                else:
                    config_payload["tools"] = [existing_tools, *tools]
            else:
                config_payload["tools"] = tools

        if response_format is not None:
            if response_format.format_type == RespFormatType.TEXT:
                config_payload.setdefault("response_mime_type", "text/plain")
            elif response_format.format_type == RespFormatType.JSON_OBJ:
                config_payload.setdefault("response_mime_type", "application/json")
            elif response_format.format_type == RespFormatType.JSON_SCHEMA:
                config_payload.setdefault("response_mime_type", "application/json")
                response_json_schema = _extract_response_json_schema(response_format)
                if (
                    response_json_schema is not None
                    and "response_json_schema" not in config_payload
                    and "response_schema" not in config_payload
                ):
                    config_payload["response_json_schema"] = response_json_schema

        return GenerateContentConfig(**config_payload)

    def _build_default_stream_response_handler(
        self,
        request: ResponseRequest,
    ) -> ProviderStreamResponseHandler[AsyncIterator[GenerateContentResponse]]:
        """构建 Gemini 默认流式响应处理器。

        Args:
            request: 统一响应请求对象。

        Returns:
            ProviderStreamResponseHandler[AsyncIterator[GenerateContentResponse]]: 默认流式处理器。
        """
        del request
        return _default_stream_response_handler

    def _build_default_response_parser(
        self,
        request: ResponseRequest,
    ) -> ProviderResponseParser[GenerateContentResponse]:
        """构建 Gemini 默认非流式响应解析器。

        Args:
            request: 统一响应请求对象。

        Returns:
            ProviderResponseParser[GenerateContentResponse]: 默认非流式解析器。
        """
        del request
        return _default_normal_response_parser

    async def _execute_response_request(
        self,
        request: ResponseRequest,
        stream_response_handler: ProviderStreamResponseHandler[AsyncIterator[GenerateContentResponse]],
        response_parser: ProviderResponseParser[GenerateContentResponse],
    ) -> Tuple[APIResponse, UsageTuple | None]:
        """执行 Gemini 的文本/多模态响应请求。

        Args:
            request: 统一响应请求对象。
            stream_response_handler: 流式响应处理器。
            response_parser: 非流式响应解析器。

        Returns:
            Tuple[APIResponse, UsageTuple | None]: 统一响应对象与可选使用量信息。
        """
        model_info = request.model_info
        snapshot_provider_request = {
            "base_url": self.api_provider.base_url,
            "endpoint": "/models/{model}:generateContent",
            "method": "POST",
            "operation": "models.generate_content",
            "request_kwargs": {},
        }

        try:
            contents, system_instruction = _convert_messages(request.message_list)
            model_identifier, enable_google_search = self._resolve_model_identifier(
                model_info.model_identifier,
                request.extra_params,
            )
            generation_config = self._build_generation_config(
                model_identifier=model_identifier,
                system_instruction=system_instruction,
                tool_options=request.tool_options,
                response_format=request.response_format,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                extra_params=request.extra_params,
                enable_google_search=enable_google_search,
            )
            snapshot_provider_request["request_kwargs"] = {
                "config": generation_config,
                "contents": contents,
                "enable_google_search": enable_google_search,
                "model": model_identifier,
                "system_instruction": system_instruction,
            }
            if model_info.force_stream_mode:
                stream_task: asyncio.Task[AsyncIterator[GenerateContentResponse]] = asyncio.create_task(
                    self.client.aio.models.generate_content_stream(
                        model=model_identifier,
                        contents=contents,
                        config=generation_config,
                    )
                )
                raw_response_stream = cast(
                    AsyncIterator[GenerateContentResponse],
                    await await_task_with_interrupt(stream_task, request.interrupt_flag),
                )
                return await stream_response_handler(raw_response_stream, request.interrupt_flag)

            completion_task: asyncio.Task[GenerateContentResponse] = asyncio.create_task(
                self.client.aio.models.generate_content(
                    model=model_identifier,
                    contents=contents,
                    config=generation_config,
                )
            )
            raw_response = cast(
                GenerateContentResponse,
                await await_task_with_interrupt(completion_task, request.interrupt_flag),
            )
            return response_parser(raw_response)
        except ReqAbortException:
            raise
        except (ClientError, ServerError) as exc:
            status_code = int(getattr(exc, "code", 500) or 500)
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="gemini",
                error=exc,
                internal_request=serialize_response_request_snapshot(request),
                model_info=model_info,
                operation="models.generate_content",
                provider_request=snapshot_provider_request,
            )
            wrapped_error = RespNotOkException(status_code, str(exc))
            attach_request_snapshot(wrapped_error, snapshot_path)
            raise wrapped_error from exc
        except (UnknownFunctionCallArgumentError, UnsupportedFunctionError, FunctionInvocationError) as exc:
            wrapped_error = RespParseException(None, f"Gemini 工具调用参数错误: {exc}")
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="gemini",
                error=wrapped_error,
                internal_request=serialize_response_request_snapshot(request),
                model_info=model_info,
                operation="models.generate_content",
                provider_request=snapshot_provider_request,
            )
            attach_request_snapshot(wrapped_error, snapshot_path)
            raise wrapped_error from exc
        except EmptyResponseException as exc:
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="gemini",
                error=exc,
                internal_request=serialize_response_request_snapshot(request),
                model_info=model_info,
                operation="models.generate_content",
                provider_request=snapshot_provider_request,
            )
            attach_request_snapshot(exc, snapshot_path)
            raise
        except Exception as exc:
            if has_request_snapshot(exc):
                raise
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="gemini",
                error=exc,
                internal_request=serialize_response_request_snapshot(request),
                model_info=model_info,
                operation="models.generate_content",
                provider_request=snapshot_provider_request,
            )
            wrapped_error = (
                exc if isinstance(exc, (EmptyResponseException, RespParseException)) else NetworkConnectionError(str(exc))
            )
            attach_request_snapshot(wrapped_error, snapshot_path)
            if wrapped_error is exc:
                raise
            raise wrapped_error from exc
        except (UnknownFunctionCallArgumentError, UnsupportedFunctionError, FunctionInvocationError) as exc:
            raise RespParseException(None, f"Gemini 工具调用参数错误: {exc}") from exc
        except EmptyResponseException:
            raise
        except Exception as exc:
            raise NetworkConnectionError(str(exc)) from exc

    async def _execute_embedding_request(
        self,
        request: EmbeddingRequest,
    ) -> Tuple[APIResponse, UsageTuple | None]:
        """执行 Gemini 文本嵌入请求。

        Args:
            request: 统一嵌入请求对象。

        Returns:
            Tuple[APIResponse, UsageTuple | None]: 统一响应对象与可选使用量信息。
        """
        model_info = request.model_info
        embedding_input = request.embedding_input
        extra_params = request.extra_params
        snapshot_provider_request = {
            "base_url": self.api_provider.base_url,
            "endpoint": "/models/{model}:embedContent",
            "method": "POST",
            "operation": "models.embed_content",
            "request_kwargs": {},
        }

        try:
            embed_config = _build_embed_content_config(extra_params)
            snapshot_provider_request["request_kwargs"] = {
                "config": embed_config,
                "contents": embedding_input,
                "model": model_info.model_identifier,
            }
            raw_response: EmbedContentResponse = await self.client.aio.models.embed_content(
                model=model_info.model_identifier,
                contents=embedding_input,
                config=embed_config,
            )
        except (ClientError, ServerError) as exc:
            status_code = int(getattr(exc, "code", 500) or 500)
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="gemini",
                error=exc,
                internal_request=serialize_embedding_request_snapshot(request),
                model_info=model_info,
                operation="models.embed_content",
                provider_request=snapshot_provider_request,
            )
            wrapped_error = RespNotOkException(status_code, str(exc))
            attach_request_snapshot(wrapped_error, snapshot_path)
            raise wrapped_error from exc
        except Exception as exc:
            if has_request_snapshot(exc):
                raise
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="gemini",
                error=exc,
                internal_request=serialize_embedding_request_snapshot(request),
                model_info=model_info,
                operation="models.embed_content",
                provider_request=snapshot_provider_request,
            )
            wrapped_error = (
                exc if isinstance(exc, (EmptyResponseException, RespParseException)) else NetworkConnectionError(str(exc))
            )
            attach_request_snapshot(wrapped_error, snapshot_path)
            if wrapped_error is exc:
                raise
            raise wrapped_error from exc

        response = APIResponse(raw_data=raw_response)
        if not raw_response.embeddings:
            exc = RespParseException(raw_response, "Gemini 嵌入响应解析失败，缺少 embeddings 字段。")
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="gemini",
                error=exc,
                internal_request=serialize_embedding_request_snapshot(request),
                model_info=model_info,
                operation="models.embed_content",
                provider_request=snapshot_provider_request,
            )
            attach_request_snapshot(exc, snapshot_path)
            raise exc
        if raw_response.embeddings:
            response.embedding = raw_response.embeddings[0].values
        else:
            raise RespParseException(raw_response, "响应解析失败，缺失 embeddings 字段")

        billable_character_count = 0
        if raw_response.metadata is not None:
            billable_character_count = getattr(raw_response.metadata, "billable_character_count", 0) or 0
        usage_record: UsageTuple = (
            billable_character_count or len(embedding_input),
            0,
            billable_character_count or len(embedding_input),
        )
        return response, usage_record

    async def _execute_audio_transcription_request(
        self,
        request: AudioTranscriptionRequest,
    ) -> Tuple[APIResponse, UsageTuple | None]:
        """执行 Gemini 音频转录请求。

        Args:
            request: 统一音频转录请求对象。

        Returns:
            Tuple[APIResponse, UsageTuple | None]: 统一响应对象与可选使用量信息。
        """
        model_info = request.model_info
        audio_base64 = request.audio_base64
        max_tokens = request.max_tokens
        extra_params = request.extra_params
        snapshot_provider_request = {
            "base_url": self.api_provider.base_url,
            "endpoint": "/models/{model}:generateContent",
            "method": "POST",
            "operation": "models.generate_content",
            "request_kwargs": {},
        }

        transcription_prompt = str(
            extra_params.get(
                "transcription_prompt",
                "Generate a transcript of the speech. The language of the transcript should match the speech.",
            )
        )
        audio_mime_type = str(extra_params.get("audio_mime_type", "audio/wav"))

        try:
            model_identifier, _ = self._resolve_model_identifier(model_info.model_identifier, extra_params)
            contents: List[ContentUnion] = [
                Content(
                    role="user",
                    parts=[
                        Part.from_text(text=transcription_prompt),
                        Part.from_bytes(data=base64.b64decode(audio_base64), mime_type=audio_mime_type),
                    ],
                )
            ]
            generation_config = self._build_generation_config(
                model_identifier=model_identifier,
                system_instruction=None,
                tool_options=None,
                response_format=None,
                max_tokens=max_tokens,
                temperature=None,
                extra_params=extra_params,
                enable_google_search=False,
            )
            snapshot_provider_request["request_kwargs"] = {
                "audio_base64": audio_base64,
                "audio_mime_type": audio_mime_type,
                "config": generation_config,
                "contents": contents,
                "model": model_identifier,
                "transcription_prompt": transcription_prompt,
            }
            raw_response: GenerateContentResponse = await self.client.aio.models.generate_content(
                model=model_identifier,
                contents=contents,
                config=generation_config,
            )
            response, usage_record = _default_normal_response_parser(raw_response)
        except (ClientError, ServerError) as exc:
            status_code = int(getattr(exc, "code", 500) or 500)
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="gemini",
                error=exc,
                internal_request=serialize_audio_request_snapshot(request),
                model_info=model_info,
                operation="models.generate_content",
                provider_request=snapshot_provider_request,
            )
            wrapped_error = RespNotOkException(status_code, str(exc))
            attach_request_snapshot(wrapped_error, snapshot_path)
            raise wrapped_error from exc
        except Exception as exc:
            if has_request_snapshot(exc):
                raise
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="gemini",
                error=exc,
                internal_request=serialize_audio_request_snapshot(request),
                model_info=model_info,
                operation="models.generate_content",
                provider_request=snapshot_provider_request,
            )
            wrapped_error = (
                exc if isinstance(exc, (EmptyResponseException, RespParseException)) else NetworkConnectionError(str(exc))
            )
            attach_request_snapshot(wrapped_error, snapshot_path)
            if wrapped_error is exc:
                raise
            raise wrapped_error from exc

        return response, usage_record

    def get_support_image_formats(self) -> List[str]:
        """获取 Gemini 当前支持的图片格式列表。

        Returns:
            List[str]: 当前客户端支持的图片格式列表。
        """
        return ["png", "jpg", "jpeg", "webp", "heic", "heif"]
