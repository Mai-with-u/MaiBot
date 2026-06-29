import asyncio
import base64
import binascii
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Tuple, cast
from urllib.parse import urlparse
from uuid import uuid4

from json_repair import repair_json
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AsyncStream
from openai._types import FileTypes, Omit, omit
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionChunk,
    ChatCompletionContentPartImageParam,
    ChatCompletionContentPartParam,
    ChatCompletionContentPartTextParam,
    ChatCompletionMessageFunctionToolCallParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
    ChatCompletionUserMessageParam,
)
from openai.types.shared_params.function_definition import FunctionDefinition
from openai.types.chat.chat_completion_chunk import ChoiceDelta
from PIL import Image as PILImage

from src.common.logger import get_logger
from src.config.model_configs import APIProvider, ReasoningParseMode, ToolArgumentParseMode
from src.llm_models.exceptions import (
    EmptyResponseException,
    NetworkConnectionError,
    ReqAbortException,
    RespNotOkException,
    RespParseException,
)
from src.llm_models.openai_compat import (
    build_openai_compatible_client_config,
    split_openai_request_overrides,
)
from src.llm_models.payload_content.message import ImageMessagePart, Message, RoleType, TextMessagePart
from src.llm_models.payload_content.resp_format import RespFormat, RespFormatType
from src.llm_models.payload_content.tool_option import (
    TOOL_CALL_SOURCE_EXTRA_KEY,
    TOOL_CALL_SOURCE_REASONING,
    TOOL_CALL_SOURCE_RESPONSE,
    ToolCall,
    ToolOption,
)

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

logger = get_logger("llm_models")

SUPPORTED_OPENAI_IMAGE_FORMATS = {"jpeg", "png", "webp"}
"""OpenAI 兼容图片输入稳定支持的格式集合。"""

THINK_CONTENT_PATTERN = re.compile(
    r"<think>(?P<think>.*?)</think>(?P<content>.*)|<think>(?P<think_unclosed>.*)|(?P<content_only>.+)",
    re.DOTALL,
)
"""用于解析 `<think>` 推理块的正则表达式。"""

XML_TOOL_CALL_PATTERN = re.compile(r"<tool_call>\s*(?P<body>.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)
"""用于兜底解析模型以 XML 文本返回的工具调用。

这是一个暂时性兼容方案，专门处理“思维链内容里夹带工具调用”的情况；
后续如果上游稳定返回标准 tool_calls 字段，这里可能会调整或移除。
"""

XML_FUNCTION_CALL_PATTERN = re.compile(
    r"<function=(?P<name>[A-Za-z0-9_.-]+)>\s*(?P<arguments>.*?)\s*</function>",
    re.DOTALL | re.IGNORECASE,
)
"""用于从 XML 风格工具调用块中提取函数名与参数。"""

XML_PARAMETER_PATTERN = re.compile(
    r"<parameter=(?P<name>[A-Za-z0-9_.-]+)>\s*(?P<value>.*?)\s*</parameter>",
    re.DOTALL | re.IGNORECASE,
)
"""用于从 XML 风格工具调用块中提取参数列表。"""

CHAT_COMPLETIONS_RESERVED_EXTRA_BODY_KEYS = {
    "max_tokens",
    "messages",
    "model",
    "response_format",
    "stream",
    "temperature",
    "tools",
}
"""由当前客户端显式承载、不应再落入 `extra_body` 的字段集合。"""

_MODELS_REQUIRING_MAX_COMPLETION_TOKENS: set[tuple[str, str]] = set()
"""记录本进程内已确认「仅支持 max_completion_tokens」的模型，键为 (base_url, model_identifier)。
命中后直接使用 max_completion_tokens，避免重复触发首次失败重试。"""

OpenAIStreamResponseHandler = Callable[
    [AsyncStream[ChatCompletionChunk], asyncio.Event | None],
    Coroutine[Any, Any, Tuple[APIResponse, UsageTuple | None]],
]
"""OpenAI 流式响应处理函数类型。"""

OpenAIResponseParser = Callable[[ChatCompletion], Tuple[APIResponse, UsageTuple | None]]
"""OpenAI 非流式响应解析函数类型。"""

PROVIDER_REASONING_KEYS_BY_DOMAIN: Dict[str, str] = {
    "api.groq.com": "reasoning",
    "openrouter.ai": "reasoning",
}
"""按 provider 域名指定的原生推理字段名。"""


def _build_fallback_tool_call_id(prefix: str) -> str:
    """为缺失原始调用 ID 的工具调用生成唯一兜底标识。"""

    normalized_prefix = str(prefix).strip() or "tool_call"
    return f"{normalized_prefix}_{uuid4().hex}"


def _build_tool_call_extra_content(source: str) -> Dict[str, Any]:
    """构造工具调用来源元信息。"""

    return {TOOL_CALL_SOURCE_EXTRA_KEY: source}


def _set_tool_call_source(tool_call: ToolCall, source: str) -> ToolCall:
    """给工具调用补充来源标记，保留已有 provider metadata。"""

    extra_content = dict(tool_call.extra_content or {})
    extra_content[TOOL_CALL_SOURCE_EXTRA_KEY] = source
    tool_call.extra_content = extra_content
    return tool_call


def _build_reasoning_key(api_provider: APIProvider) -> str:
    """根据 provider 构建可读取的原生推理字段名。"""
    normalized_base_url = api_provider.base_url.strip()
    if not normalized_base_url:
        return "reasoning_content"
    parsed_url = urlparse(normalized_base_url if "://" in normalized_base_url else f"//{normalized_base_url}")
    provider_hostname = (parsed_url.hostname or "").lower()
    return PROVIDER_REASONING_KEYS_BY_DOMAIN.get(provider_hostname, "reasoning_content")


def _extract_reasoning_content(message_part: Any, reasoning_key: str) -> str | None:
    """从 OpenAI 兼容响应对象中读取原生推理内容。

    不同兼容服务商对推理字段命名并不完全一致。这里集中处理字段访问，
    避免解析路径里散落 provider 特判；具体字段名由 provider 决定。
    """
    native_reasoning = getattr(message_part, reasoning_key, None)
    if isinstance(native_reasoning, str) and native_reasoning:
        return native_reasoning
    return None


def _normalize_reasoning_parse_mode(parse_mode: str | ReasoningParseMode) -> ReasoningParseMode:
    """将配置中的推理解析模式收敛为枚举值。

    Args:
        parse_mode: 原始解析模式配置。

    Returns:
        ReasoningParseMode: 规范化后的解析模式；未知值会回退为 `AUTO`。
    """
    if isinstance(parse_mode, ReasoningParseMode):
        return parse_mode
    try:
        return ReasoningParseMode(parse_mode)
    except ValueError:
        logger.warning(f"未识别的推理解析模式 {parse_mode}，已回退为 auto")
        return ReasoningParseMode.AUTO


def _normalize_tool_argument_parse_mode(parse_mode: str | ToolArgumentParseMode) -> ToolArgumentParseMode:
    """将配置中的工具参数解析模式收敛为枚举值。

    Args:
        parse_mode: 原始解析模式配置。

    Returns:
        ToolArgumentParseMode: 规范化后的解析模式；未知值会回退为 `AUTO`。
    """
    if isinstance(parse_mode, ToolArgumentParseMode):
        return parse_mode
    try:
        return ToolArgumentParseMode(parse_mode)
    except ValueError:
        logger.warning(f"未识别的工具参数解析模式 {parse_mode}，已回退为 auto")
        return ToolArgumentParseMode.AUTO


def _build_text_content_part(text: str) -> ChatCompletionContentPartTextParam:
    """构建文本内容片段。

    Args:
        text: 文本内容。

    Returns:
        ChatCompletionContentPartTextParam: OpenAI 兼容的文本片段。
    """
    return {
        "type": "text",
        "text": text,
    }


def _build_image_content_part(part: ImageMessagePart) -> ChatCompletionContentPartImageParam:
    """构建图片内容片段。

    Args:
        part: 内部图片片段。

    Returns:
        ChatCompletionContentPartImageParam: OpenAI 兼容的图片片段。
    """
    normalized_image = _normalize_image_part_for_openai(part)
    if normalized_image is None:
        raise ValueError("图片数据无效，无法构建图片消息片段")

    image_format, image_base64 = normalized_image
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:image/{image_format};base64,{image_base64}",
        },
    }


def _normalize_image_part_for_openai(part: ImageMessagePart) -> Tuple[str, str] | None:
    """将图片片段规范化为 OpenAI 兼容格式。

    Args:
        part: 内部图片片段。

    Returns:
        Tuple[str, str] | None: `(image_format, image_base64)`；无法解析时返回 `None`。
    """
    try:
        image_bytes = base64.b64decode(part.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        logger.warning(f"图片 Base64 解码失败，已跳过该图片片段: {exc}")
        return None

    try:
        with PILImage.open(io.BytesIO(image_bytes)) as image:
            image_format = (image.format or part.normalized_image_format).lower()
            if image_format in {"jpg", "jpeg"}:
                image_format = "jpeg"

            if image_format in SUPPORTED_OPENAI_IMAGE_FORMATS:
                return image_format, part.image_base64

            if image_format == "gif":
                frame_count = getattr(image, "n_frames", 1)
                frames: List[PILImage.Image] = []
                durations: List[int] = []

                for frame_index in range(frame_count):
                    image.seek(frame_index)
                    frame = image.copy()
                    if frame.mode not in {"RGB", "RGBA"}:
                        frame = frame.convert("RGBA")
                    frames.append(frame)
                    durations.append(int(image.info.get("duration", 100) or 100))

                output_buffer = io.BytesIO()
                save_kwargs: Dict[str, Any] = {
                    "format": "WEBP",
                    "save_all": True,
                    "append_images": frames[1:],
                    "duration": durations,
                    "loop": int(image.info.get("loop", 0) or 0),
                }
                if frame_count > 1:
                    save_kwargs["lossless"] = True

                frames[0].save(output_buffer, **save_kwargs)
                converted_base64 = base64.b64encode(output_buffer.getvalue()).decode("utf-8")
                return "webp", converted_base64

            image.seek(0)
            normalized_image = image.copy()
            if normalized_image.mode not in {"RGB", "RGBA"}:
                normalized_image = normalized_image.convert("RGBA")

            output_buffer = io.BytesIO()
            normalized_image.save(output_buffer, format="PNG")
            converted_base64 = base64.b64encode(output_buffer.getvalue()).decode("utf-8")
            return "png", converted_base64
    except Exception as exc:
        logger.warning(f"图片内容无法被识别为有效图片，已跳过该图片片段: {exc}")
        return None


def _convert_response_format(response_format: RespFormat | None) -> Any:
    """将内部响应格式转换为 OpenAI 兼容结构。

    Args:
        response_format: 内部响应格式定义。

    Returns:
        Any: OpenAI SDK 可接受的响应格式参数；未指定时返回 `omit`。
    """
    if response_format is None or response_format.format_type == RespFormatType.TEXT:
        return omit
    if response_format.format_type == RespFormatType.JSON_OBJ:
        return {"type": "json_object"}
    if response_format.format_type == RespFormatType.JSON_SCHEMA:
        return {
            "type": "json_schema",
            "json_schema": response_format.schema,
        }
    return omit


def _convert_text_only_message_content(
    message: Message,
) -> str | List[ChatCompletionContentPartTextParam]:
    """将仅允许文本的消息转换为 OpenAI 兼容内容。

    Args:
        message: 内部统一消息对象。

    Returns:
        str | List[ChatCompletionContentPartTextParam]: 文本内容结构。

    Raises:
        ValueError: 当消息中包含非文本片段时抛出。
    """
    if not message.parts:
        return ""
    if len(message.parts) == 1 and isinstance(message.parts[0], TextMessagePart):
        return message.parts[0].text

    content: List[ChatCompletionContentPartTextParam] = []
    for part in message.parts:
        if not isinstance(part, TextMessagePart):
            raise ValueError(f"{message.role.value} 消息仅支持文本片段")
        if not part.text.strip():
            continue
        content.append(_build_text_content_part(part.text))
    if not content:
        return ""
    return content


def _convert_user_message_content(message: Message) -> str | List[ChatCompletionContentPartParam]:
    """将用户消息转换为 OpenAI 兼容内容。

    Args:
        message: 内部统一消息对象。

    Returns:
        str | List[ChatCompletionContentPartParam]: 用户消息内容结构。
    """
    if len(message.parts) == 1 and isinstance(message.parts[0], TextMessagePart):
        return message.parts[0].text

    content: List[ChatCompletionContentPartParam] = []
    for part in message.parts:
        if isinstance(part, TextMessagePart):
            if part.text.strip():
                content.append(_build_text_content_part(part.text))
            continue

        normalized_image = _normalize_image_part_for_openai(part)
        if normalized_image is None:
            content.append(_build_text_content_part("[图片内容不可用]"))
            continue

        image_format, image_base64 = normalized_image
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/{image_format};base64,{image_base64}",
                },
            }
        )
    if not content:
        return ""
    return content


def _convert_assistant_tool_calls(tool_calls: List[ToolCall]) -> List[ChatCompletionMessageFunctionToolCallParam]:
    """将内部工具调用转换为 OpenAI assistant tool_calls 结构。

    Args:
        tool_calls: 内部工具调用列表。

    Returns:
        List[ChatCompletionMessageFunctionToolCallParam]: OpenAI 兼容工具调用结构。
    """
    converted_tool_calls: List[ChatCompletionMessageFunctionToolCallParam] = []
    for tool_call in tool_calls:
        converted_tool_calls.append(
            {
                "id": tool_call.call_id,
                "type": "function",
                "function": {
                    "name": tool_call.func_name,
                    "arguments": json.dumps(tool_call.args or {}, ensure_ascii=False),
                },
            }
        )
    return converted_tool_calls


def _sanitize_messages_for_toolless_request(messages: List[Message]) -> List[Message]:
    """在无工具请求时清洗历史工具调用链，避免兼容接口拒收消息。"""
    sanitized_messages: List[Message] = []

    for message in messages:
        if message.role == RoleType.Tool:
            continue

        if message.role == RoleType.Assistant and message.tool_calls:
            if not message.parts:
                continue
            assistant_message = Message(
                role=message.role,
                parts=list(message.parts),
                tool_call_id=message.tool_call_id,
                tool_name=message.tool_name,
                tool_calls=None,
            )
            sanitized_messages.append(assistant_message)
            continue

        sanitized_messages.append(message)

    return sanitized_messages


def _convert_messages(messages: List[Message]) -> List[ChatCompletionMessageParam]:
    """将内部消息列表转换为 OpenAI 兼容消息列表。

    Args:
        messages: 内部统一消息列表。

    Returns:
        List[ChatCompletionMessageParam]: OpenAI SDK 所需的消息结构列表。
    """
    converted_messages: List[ChatCompletionMessageParam] = []
    for message in messages:
        if message.role == RoleType.System:
            system_payload: ChatCompletionSystemMessageParam = {
                "role": "system",
                "content": _convert_text_only_message_content(message),
            }
            converted_messages.append(system_payload)
            continue

        if message.role == RoleType.User:
            user_payload: ChatCompletionUserMessageParam = {
                "role": "user",
                "content": _convert_user_message_content(message),
            }
            converted_messages.append(user_payload)
            continue

        if message.role == RoleType.Assistant:
            assistant_payload: ChatCompletionAssistantMessageParam = {
                "role": "assistant",
                "content": None if not message.parts and message.tool_calls else _convert_text_only_message_content(message),
            }
            if message.tool_calls:
                assistant_payload["tool_calls"] = _convert_assistant_tool_calls(message.tool_calls)
            converted_messages.append(assistant_payload)
            continue

        if message.role == RoleType.Tool:
            if message.tool_call_id is None:
                raise ValueError("Tool 消息缺少 tool_call_id")
            tool_payload: ChatCompletionToolMessageParam = {
                "role": "tool",
                "content": _convert_text_only_message_content(message),
                "tool_call_id": message.tool_call_id,
            }
            converted_messages.append(tool_payload)
            continue

        raise ValueError(f"不支持的消息角色：{message.role}")

    return converted_messages


def _convert_tool_options(tool_options: List[ToolOption]) -> List[ChatCompletionToolParam]:
    """将工具定义转换为 OpenAI 兼容的工具列表。

    Args:
        tool_options: 内部统一工具定义列表。

    Returns:
        List[ChatCompletionToolParam]: OpenAI SDK 所需的工具定义列表。
    """
    converted_tools: List[ChatCompletionToolParam] = []
    for tool_option in tool_options:
        parameters_schema = cast(
            Dict[str, object],
            tool_option.parameters_schema or {"type": "object", "properties": {}, "required": []},
        )
        function_schema: FunctionDefinition = {
            "name": tool_option.name,
            "description": tool_option.description,
            "parameters": parameters_schema,
        }
        converted_tools.append(
            {
                "type": "function",
                "function": function_schema,
            }
        )
    return converted_tools


def _extract_usage_record(usage: Any) -> UsageTuple | None:
    """从响应对象中提取 usage 三元组。

    Args:
        usage: OpenAI SDK 返回的 usage 对象。

    Returns:
        UsageTuple | None: `(prompt_tokens, completion_tokens, total_tokens)`。
    """
    if usage is None:
        return None
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    prompt_cache_hit_tokens = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    prompt_cache_miss_tokens = getattr(usage, "prompt_cache_miss_tokens", 0) or 0
    prompt_tokens_details = getattr(usage, "prompt_tokens_details", None)
    if prompt_cache_hit_tokens == 0 and prompt_tokens_details is not None:
        prompt_cache_hit_tokens = getattr(prompt_tokens_details, "cached_tokens", 0) or 0
    if prompt_cache_miss_tokens == 0 and prompt_cache_hit_tokens > 0:
        prompt_cache_miss_tokens = max(prompt_tokens - prompt_cache_hit_tokens, 0)
    return (
        prompt_tokens,
        getattr(usage, "completion_tokens", 0) or 0,
        getattr(usage, "total_tokens", 0) or 0,
        prompt_cache_hit_tokens,
        prompt_cache_miss_tokens,
    )


def _parse_tool_arguments(
    raw_arguments: str,
    parse_mode: ToolArgumentParseMode,
    response: Any,
) -> Dict[str, Any]:
    """解析工具调用参数字符串。

    Args:
        raw_arguments: 工具调用参数原始字符串。
        parse_mode: 参数解析模式。
        response: 原始响应对象，用于异常上下文。

    Returns:
        Dict[str, Any]: 解析后的参数字典。

    Raises:
        RespParseException: 当参数无法解析为字典时抛出。
    """
    if not raw_arguments.strip():
        return {}

    try:
        if parse_mode == ToolArgumentParseMode.STRICT:
            arguments: Any = json.loads(raw_arguments)
        elif parse_mode == ToolArgumentParseMode.REPAIR:
            arguments = repair_json(raw_arguments, return_objects=True, logging=False)
        else:
            arguments = repair_json(raw_arguments, return_objects=True, logging=False)
            if isinstance(arguments, str) and parse_mode in {
                ToolArgumentParseMode.AUTO,
                ToolArgumentParseMode.DOUBLE_DECODE,
            }:
                arguments = repair_json(arguments, return_objects=True, logging=False)
    except json.JSONDecodeError as exc:
        raise RespParseException(response, f"响应解析失败，无法解析工具调用参数。原始参数：{raw_arguments}") from exc

    if not isinstance(arguments, dict):
        raise RespParseException(
            response,
            f"响应解析失败，工具调用参数必须解析为字典，实际类型为 {type(arguments).__name__}。",
        )
    return arguments


def _extract_reasoning_and_content(
    content: str,
    parse_mode: ReasoningParseMode,
) -> Tuple[str | None, str | None]:
    """从文本内容中提取推理内容与正式输出。

    Args:
        content: 模型返回的文本内容。
        parse_mode: 推理解析模式。

    Returns:
        Tuple[str | None, str | None]: `(reasoning_content, content)`。
    """
    if parse_mode in {ReasoningParseMode.NATIVE, ReasoningParseMode.NONE}:
        return None, content

    match = THINK_CONTENT_PATTERN.match(content)
    if not match:
        return None, content
    if match.group("think") is not None:
        reasoning_content = match.group("think").strip() or None
        final_content = match.group("content").strip() or None
        return reasoning_content, final_content
    if match.group("think_unclosed") is not None:
        return match.group("think_unclosed").strip() or None, None
    return None, match.group("content_only").strip() or None


def _extract_xml_tool_calls(
    raw_text: str | None,
    parse_mode: ToolArgumentParseMode,
    response: Any,
    *,
    source: str,
) -> Tuple[str | None, List[ToolCall] | None]:
    """从 XML 风格文本中兜底提取工具调用。"""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return raw_text, None

    tool_calls: List[ToolCall] = []

    def _coerce_xml_parameter_value(raw_value: str) -> Any:
        normalized_value = raw_value.strip()
        if not normalized_value:
            return ""
        lowered_value = normalized_value.lower()
        if lowered_value == "true":
            return True
        if lowered_value == "false":
            return False
        if lowered_value in {"null", "none"}:
            return None
        if normalized_value.startswith(("{", "[")):
            try:
                return repair_json(normalized_value, return_objects=True, logging=False)
            except Exception:
                return normalized_value
        return normalized_value

    def _parse_xml_parameters(raw_arguments: str) -> Dict[str, Any] | None:
        parameters = {
            match.group("name").strip(): _coerce_xml_parameter_value(match.group("value"))
            for match in XML_PARAMETER_PATTERN.finditer(raw_arguments)
        }
        return parameters or None

    def _replace_tool_call(match: re.Match[str]) -> str:
        body = match.group("body")
        function_match = XML_FUNCTION_CALL_PATTERN.search(body)
        if function_match is None:
            return match.group(0)

        function_name = function_match.group("name").strip()
        raw_arguments = function_match.group("arguments").strip()
        arguments = _parse_xml_parameters(raw_arguments)
        if arguments is None:
            arguments = _parse_tool_arguments(raw_arguments, parse_mode, response) if raw_arguments else {}
        tool_calls.append(
            ToolCall(
                call_id=_build_fallback_tool_call_id("xml_tool_call"),
                func_name=function_name,
                args=arguments,
                extra_content=_build_tool_call_extra_content(source),
            )
        )
        return ""

    cleaned_text = XML_TOOL_CALL_PATTERN.sub(_replace_tool_call, raw_text).strip() or None
    return cleaned_text, tool_calls or None


def _get_content_block_value(content_block: Any, key: str) -> Any:
    """读取 OpenAI 兼容 content block 的字段，兼容 dict 与 SDK 对象。"""

    if isinstance(content_block, dict):
        return content_block.get(key)
    return getattr(content_block, key, None)


def _extract_content_block_text(content_block: Any) -> str:
    """从 content block 中提取文本字段。"""

    for key in ("text", "content", "reasoning", "summary"):
        value = _get_content_block_value(content_block, key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _normalize_content_block_arguments(raw_arguments: Any, parse_mode: ToolArgumentParseMode, response: Any) -> Dict[str, Any]:
    """将 content block 内的工具参数规范化为字典。"""

    if raw_arguments is None:
        return {}
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        normalized_arguments = raw_arguments.strip()
        if not normalized_arguments:
            return {}
        return _parse_tool_arguments(normalized_arguments, parse_mode, response)
    return {"value": raw_arguments}


def _extract_content_block_tool_call(
    content_block: Any,
    parse_mode: ToolArgumentParseMode,
    response: Any,
    *,
    source: str,
) -> ToolCall | None:
    """从 content block 中提取工具调用。"""

    block_type = str(_get_content_block_value(content_block, "type") or "").strip().lower()
    if block_type not in {"tool_use", "tool_call", "function_call"}:
        return None

    function_info = _get_content_block_value(content_block, "function")
    if function_info is not None:
        call_name = _get_content_block_value(function_info, "name")
        raw_arguments = _get_content_block_value(function_info, "arguments")
    else:
        call_name = _get_content_block_value(content_block, "name") or _get_content_block_value(content_block, "func_name")
        raw_arguments = (
            _get_content_block_value(content_block, "input")
            or _get_content_block_value(content_block, "arguments")
            or _get_content_block_value(content_block, "args")
        )

    if not isinstance(call_name, str) or not call_name.strip():
        raise RespParseException(response, "响应解析失败，content block 工具调用缺少 name 字段。")

    call_id = (
        _get_content_block_value(content_block, "id")
        or _get_content_block_value(content_block, "tool_call_id")
        or _get_content_block_value(content_block, "call_id")
        or _build_fallback_tool_call_id("content_block_tool_call")
    )
    return ToolCall(
        call_id=str(call_id),
        func_name=call_name.strip(),
        args=_normalize_content_block_arguments(raw_arguments, parse_mode, response),
        extra_content=_build_tool_call_extra_content(source),
    )


def _extract_content_blocks(
    message_content: Any,
    parse_mode: ToolArgumentParseMode,
    response: Any,
) -> tuple[str | None, str | None, List[ToolCall] | None]:
    """解析同时包含 reasoning/text/tool_use 的 content block 响应。"""

    if not isinstance(message_content, list):
        return None, None, None

    reasoning_parts: List[str] = []
    content_parts: List[str] = []
    tool_calls: List[ToolCall] = []
    for content_block in message_content:
        block_type = str(_get_content_block_value(content_block, "type") or "").strip().lower()
        if block_type in {"reasoning", "reasoning_content", "thinking", "thought"}:
            block_text = _extract_content_block_text(content_block).strip()
            if block_text:
                reasoning_parts.append(block_text)
            continue

        tool_call = _extract_content_block_tool_call(
            content_block,
            parse_mode,
            response,
            source=TOOL_CALL_SOURCE_RESPONSE,
        )
        if tool_call is not None:
            tool_calls.append(tool_call)
            continue

        if block_type in {"text", "output_text", "message"}:
            block_text = _extract_content_block_text(content_block).strip()
            if block_text:
                content_parts.append(block_text)

    tool_call_source = TOOL_CALL_SOURCE_REASONING if reasoning_parts and not content_parts else TOOL_CALL_SOURCE_RESPONSE
    tool_calls = [_set_tool_call_source(tool_call, tool_call_source) for tool_call in tool_calls]
    reasoning_content = "\n".join(reasoning_parts).strip() or None
    content = "\n".join(content_parts).strip() or None
    return reasoning_content, content, tool_calls or None


def _log_length_truncation(finish_reason: str | None, model_name: str | None) -> None:
    """记录因长度截断导致的告警日志。

    Args:
        finish_reason: OpenAI 兼容接口返回的完成原因。
        model_name: 上游返回的模型标识。
    """
    if finish_reason == "length":
        logger.info(f"模型{model_name or ''}因为超过最大 max_token 限制，可能仅输出部分内容，可视情况调整")


def _apply_xml_tool_call_fallback(
    response: APIResponse,
    parse_mode: ToolArgumentParseMode,
    raw_response: Any,
) -> None:
    """当上游未返回标准 tool_calls 时，尝试从 XML 文本兜底解析。

    这是一个暂时性处理方法，用来兼容思维链中混入工具调用的返回格式，
    后续可能随着模型或上游接口的规范化而变更。
    """
    if response.tool_calls:
        return

    reasoning_content, tool_calls = _extract_xml_tool_calls(
        response.reasoning_content,
        parse_mode,
        raw_response,
        source=TOOL_CALL_SOURCE_REASONING,
    )
    if reasoning_content != response.reasoning_content:
        response.reasoning_content = reasoning_content
    if tool_calls:
        response.tool_calls = tool_calls
        if not response.content and reasoning_content:
            response.content = reasoning_content
            response.reasoning_content = None
        logger.warning("OpenAI 兼容响应未返回标准 tool_calls，已从 XML 文本兜底解析工具调用")
        return

    cleaned_content, tool_calls = _extract_xml_tool_calls(
        response.content,
        parse_mode,
        raw_response,
        source=TOOL_CALL_SOURCE_RESPONSE,
    )
    if cleaned_content != response.content:
        response.content = cleaned_content
    if tool_calls:
        response.tool_calls = tool_calls
        logger.warning("OpenAI 兼容响应未返回标准 tool_calls，已从 XML 文本兜底解析工具调用")


def _coerce_openai_argument(value: Any) -> Any | Omit:
    """将可选参数转换为 OpenAI SDK 期望的值。

    Args:
        value: 原始参数值。

    Returns:
        Any | Omit: `None` 会被转换为 `omit`，其余值原样返回。
    """
    if value is None:
        return omit
    return value


def _snapshot_openai_argument(value: Any | Omit) -> Any | None:
    """将 OpenAI SDK 参数转换为适合写入快照的普通值。"""
    if value is omit:
        return None
    return value


def _build_api_status_message(error: APIStatusError) -> str:
    """构建更适合记录和展示的状态错误信息。

    Args:
        error: OpenAI SDK 抛出的状态错误。

    Returns:
        str: 拼装后的错误信息。
    """
    message_parts: List[str] = []
    if getattr(error, "message", None):
        message_parts.append(str(error.message))
    response_text = getattr(getattr(error, "response", None), "text", None)
    if response_text:
        message_parts.append(str(response_text))
    if message_parts:
        return " | ".join(message_parts)
    return f"上游接口返回状态码 {error.status_code}"


def _is_max_tokens_unsupported_error(error: APIStatusError) -> bool:
    """判断该 400 错误是否为「max_tokens 不被支持、应改用 max_completion_tokens」。

    以错误原文匹配为主判据（OpenAI 的报错即 "Use 'max_completion_tokens' instead."），
    最稳健、不依赖动态属性。

    Args:
        error: OpenAI SDK 抛出的状态错误。

    Returns:
        bool: 命中该特征返回 True。
    """
    if error.status_code != 400:
        return False
    return "max_completion_tokens" in (error.message or "").lower()


@dataclass(slots=True)
class _StreamedToolCallState:
    """流式工具调用累积状态。"""

    index: int
    call_id: str = ""
    function_name: str = ""
    arguments_buffer: io.StringIO = field(default_factory=io.StringIO)

    def append_arguments(self, arguments_chunk: str) -> None:
        """追加一段工具调用参数字符串。

        Args:
            arguments_chunk: 参数增量片段。
        """
        self.arguments_buffer.write(arguments_chunk)

    def close(self) -> None:
        """关闭内部缓存。"""
        if not self.arguments_buffer.closed:
            self.arguments_buffer.close()


class _OpenAIStreamAccumulator:
    """OpenAI 兼容流式响应累积器。"""

    def __init__(
        self,
        reasoning_parse_mode: ReasoningParseMode,
        tool_argument_parse_mode: ToolArgumentParseMode,
        reasoning_key: str,
    ) -> None:
        """初始化累积器。

        Args:
            reasoning_parse_mode: 推理内容解析模式。
            tool_argument_parse_mode: 工具参数解析模式。
            reasoning_key: 允许读取的原生推理字段名。
        """
        self.reasoning_parse_mode = reasoning_parse_mode
        self.tool_argument_parse_mode = tool_argument_parse_mode
        self.reasoning_key = reasoning_key
        self.reasoning_buffer = io.StringIO()
        self.content_buffer = io.StringIO()
        self.tool_call_states: Dict[int, _StreamedToolCallState] = {}
        self.finish_reason: str | None = None
        self.model_name: str | None = None
        self._using_native_reasoning = False

    def capture_event_metadata(self, event: ChatCompletionChunk) -> None:
        """捕获事件中的完成原因和模型名。

        Args:
            event: 当前流式事件。
        """
        if getattr(event, "model", None) and not self.model_name:
            self.model_name = event.model
        if getattr(event, "choices", None):
            finish_reason = getattr(event.choices[0], "finish_reason", None)
            if finish_reason:
                self.finish_reason = finish_reason

    def process_delta(self, delta: ChoiceDelta) -> None:
        """处理一个增量块。

        Args:
            delta: 当前增量对象。
        """
        self._process_reasoning_delta(delta)
        self._process_tool_call_delta(delta)

    def _process_reasoning_delta(self, delta: ChoiceDelta) -> None:
        """处理推理内容与正式内容。

        Args:
            delta: 当前增量对象。
        """
        native_reasoning = _extract_reasoning_content(delta, self.reasoning_key)
        if native_reasoning is not None:
            self._using_native_reasoning = True
            if self.reasoning_parse_mode != ReasoningParseMode.NONE:
                self.reasoning_buffer.write(native_reasoning)
            return

        content_chunk = getattr(delta, "content", None)
        if not isinstance(content_chunk, str) or content_chunk == "":
            return

        if self.reasoning_parse_mode == ReasoningParseMode.NONE:
            self.content_buffer.write(content_chunk)
            return

        if self.reasoning_parse_mode == ReasoningParseMode.NATIVE:
            self.content_buffer.write(content_chunk)
            return

        self.content_buffer.write(content_chunk)

    def _process_tool_call_delta(self, delta: ChoiceDelta) -> None:
        """处理工具调用增量。

        Args:
            delta: 当前增量对象。
        """
        tool_call_deltas = getattr(delta, "tool_calls", None) or []
        for tool_call_delta in tool_call_deltas:
            state = self.tool_call_states.setdefault(tool_call_delta.index, _StreamedToolCallState(index=tool_call_delta.index))
            if tool_call_delta.id:
                state.call_id = tool_call_delta.id
            function = tool_call_delta.function
            if function is not None and function.name:
                state.function_name = function.name
            if function is not None and function.arguments:
                state.append_arguments(function.arguments)

    def build_response(self) -> APIResponse:
        """构建最终 APIResponse 对象。

        Returns:
            APIResponse: 累积完成的响应对象。

        Raises:
            EmptyResponseException: 当响应中既无可见内容也无工具调用时抛出。
            RespParseException: 当工具调用结构不完整时抛出。
        """
        response = APIResponse()

        content = self.content_buffer.getvalue().strip()
        reasoning_content = self.reasoning_buffer.getvalue().strip()
        if not self._using_native_reasoning and self.reasoning_parse_mode != ReasoningParseMode.NONE and content:
            parsed_reasoning_content, parsed_content = _extract_reasoning_and_content(
                content=content,
                parse_mode=self.reasoning_parse_mode,
            )
            if parsed_reasoning_content:
                reasoning_content = parsed_reasoning_content
            content = parsed_content or ""
        if reasoning_content:
            response.reasoning_content = reasoning_content
        if content:
            response.content = content

        if self.tool_call_states:
            response.tool_calls = []
            for index in sorted(self.tool_call_states):
                state = self.tool_call_states[index]
                if not state.function_name:
                    raise RespParseException(None, f"响应解析失败，工具调用 {index} 缺少函数名。")
                raw_arguments = state.arguments_buffer.getvalue().strip()
                arguments = (
                    _parse_tool_arguments(raw_arguments, self.tool_argument_parse_mode, None)
                    if raw_arguments
                    else None
                )
                call_id = state.call_id or _build_fallback_tool_call_id(f"tool_call_{index}")
                source = TOOL_CALL_SOURCE_REASONING if reasoning_content else TOOL_CALL_SOURCE_RESPONSE
                response.tool_calls.append(
                    ToolCall(
                        call_id=call_id,
                        func_name=state.function_name,
                        args=arguments,
                        extra_content=_build_tool_call_extra_content(source),
                    )
                )

        response.raw_data = {"model": self.model_name} if self.model_name else None
        _apply_xml_tool_call_fallback(response, self.tool_argument_parse_mode, response.raw_data)

        if not response.content and not response.tool_calls:
            raise EmptyResponseException(response.raw_data)

        return response

    def close(self) -> None:
        """关闭内部缓冲区。"""
        if not self.reasoning_buffer.closed:
            self.reasoning_buffer.close()
        if not self.content_buffer.closed:
            self.content_buffer.close()
        for state in self.tool_call_states.values():
            state.close()


async def _default_stream_response_handler(
    resp_stream: AsyncStream[ChatCompletionChunk],
    interrupt_flag: asyncio.Event | None,
    *,
    reasoning_parse_mode: ReasoningParseMode,
    tool_argument_parse_mode: ToolArgumentParseMode,
    reasoning_key: str,
) -> Tuple[APIResponse, UsageTuple | None]:
    """处理 OpenAI 兼容流式响应。

    Args:
        resp_stream: OpenAI SDK 返回的流式响应对象。
        interrupt_flag: 外部中断标记。
        reasoning_parse_mode: 推理内容解析模式。
        tool_argument_parse_mode: 工具参数解析模式。
        reasoning_key: 允许读取的原生推理字段名。

    Returns:
        Tuple[APIResponse, UsageTuple | None]: 解析后的响应与 usage 统计。
    """
    accumulator = _OpenAIStreamAccumulator(
        reasoning_parse_mode=reasoning_parse_mode,
        tool_argument_parse_mode=tool_argument_parse_mode,
        reasoning_key=reasoning_key,
    )
    usage_record: UsageTuple | None = None

    try:
        async for event in resp_stream:
            if interrupt_flag and interrupt_flag.is_set():
                raise ReqAbortException("请求被外部信号中断")

            accumulator.capture_event_metadata(event)
            event_usage = _extract_usage_record(getattr(event, "usage", None))
            if event_usage is not None:
                usage_record = event_usage

            if not getattr(event, "choices", None):
                continue

            accumulator.process_delta(event.choices[0].delta)

        response = accumulator.build_response()
        model_name = None
        if isinstance(response.raw_data, dict):
            model_name = response.raw_data.get("model")
        _log_length_truncation(accumulator.finish_reason, model_name)
        return response, usage_record
    finally:
        accumulator.close()


def _default_normal_response_parser(
    resp: ChatCompletion,
    *,
    reasoning_parse_mode: ReasoningParseMode,
    tool_argument_parse_mode: ToolArgumentParseMode,
    reasoning_key: str,
) -> Tuple[APIResponse, UsageTuple | None]:
    """解析 OpenAI 兼容的非流式响应。

    Args:
        resp: OpenAI SDK 返回的聊天补全响应。
        reasoning_parse_mode: 推理内容解析模式。
        tool_argument_parse_mode: 工具参数解析模式。
        reasoning_key: 允许读取的原生推理字段名。

    Returns:
        Tuple[APIResponse, UsageTuple | None]: 解析后的响应与 usage 统计。

    Raises:
        EmptyResponseException: 当 choices 为空或响应内容为空时抛出。
    """
    choices = getattr(resp, "choices", None)
    if not choices:
        raise EmptyResponseException(resp, "响应解析失败，choices 为空或缺失")

    api_response = APIResponse()
    message_part = choices[0].message
    native_reasoning = _extract_reasoning_content(message_part, reasoning_key)
    raw_message_content = message_part.content
    message_content = raw_message_content if isinstance(raw_message_content, str) else None
    content_block_reasoning, content_block_content, content_block_tool_calls = _extract_content_blocks(
        raw_message_content,
        tool_argument_parse_mode,
        resp,
    )

    if native_reasoning is not None and reasoning_parse_mode != ReasoningParseMode.NONE:
        api_response.reasoning_content = native_reasoning
        api_response.content = message_content or content_block_content
    elif isinstance(message_content, str) and message_content:
        reasoning_content, final_content = _extract_reasoning_and_content(
            content=message_content,
            parse_mode=reasoning_parse_mode,
        )
        api_response.reasoning_content = reasoning_content
        api_response.content = final_content
    else:
        api_response.reasoning_content = content_block_reasoning
        api_response.content = content_block_content
        api_response.tool_calls = content_block_tool_calls

    tool_calls = getattr(message_part, "tool_calls", None) or []
    if tool_calls:
        api_response.tool_calls = []
        for tool_call in tool_calls:
            if tool_call.type != "function":
                raise RespParseException(resp, f"响应解析失败，暂不支持工具调用类型 {tool_call.type}。")
            raw_arguments = tool_call.function.arguments or ""
            arguments = _parse_tool_arguments(raw_arguments, tool_argument_parse_mode, resp)
            api_response.tool_calls.append(
                ToolCall(
                    call_id=tool_call.id,
                    func_name=tool_call.function.name,
                    args=arguments,
                    extra_content=_build_tool_call_extra_content(
                        TOOL_CALL_SOURCE_REASONING
                        if api_response.reasoning_content
                        else TOOL_CALL_SOURCE_RESPONSE
                    ),
                )
            )
    elif content_block_tool_calls:
        api_response.tool_calls = content_block_tool_calls

    usage_record = _extract_usage_record(getattr(resp, "usage", None))
    api_response.raw_data = resp

    finish_reason = getattr(resp.choices[0], "finish_reason", None)
    _log_length_truncation(finish_reason, getattr(resp, "model", None))
    _apply_xml_tool_call_fallback(api_response, tool_argument_parse_mode, resp)

    if not api_response.content and not api_response.tool_calls:
        raise EmptyResponseException(resp)

    return api_response, usage_record


@client_registry.register_client_class("openai")
class OpenaiClient(AdapterClient[AsyncStream[ChatCompletionChunk], ChatCompletion]):
    """OpenAI 兼容客户端。"""

    client: AsyncOpenAI
    reasoning_parse_mode: ReasoningParseMode
    reasoning_key: str
    tool_argument_parse_mode: ToolArgumentParseMode

    def __init__(self, api_provider: APIProvider) -> None:
        """初始化 OpenAI 兼容客户端。

        Args:
            api_provider: API 提供商配置。
        """
        super().__init__(api_provider)
        client_config = build_openai_compatible_client_config(api_provider)
        self.reasoning_parse_mode = _normalize_reasoning_parse_mode(api_provider.reasoning_parse_mode)
        self.reasoning_key = _build_reasoning_key(api_provider)
        self.tool_argument_parse_mode = _normalize_tool_argument_parse_mode(api_provider.tool_argument_parse_mode)
        self.client = AsyncOpenAI(
            api_key=client_config.api_key,
            organization=api_provider.organization,
            project=api_provider.project,
            base_url=client_config.base_url,
            timeout=api_provider.timeout,
            max_retries=api_provider.max_retry,
            default_headers=client_config.default_headers or None,
            default_query=client_config.default_query or None,
        )

    def _build_default_stream_response_handler(
        self,
        request: ResponseRequest,
    ) -> ProviderStreamResponseHandler[AsyncStream[ChatCompletionChunk]]:
        """构建 OpenAI 默认流式响应处理器。

        Args:
            request: 统一响应请求对象。

        Returns:
            ProviderStreamResponseHandler[AsyncStream[ChatCompletionChunk]]: 默认流式处理器。
        """
        del request

        async def default_stream_handler(
            resp_stream: AsyncStream[ChatCompletionChunk],
            flag: asyncio.Event | None,
        ) -> Tuple[APIResponse, UsageTuple | None]:
            """包装默认流式解析器。"""
            return await _default_stream_response_handler(
                resp_stream,
                flag,
                reasoning_parse_mode=self.reasoning_parse_mode,
                tool_argument_parse_mode=self.tool_argument_parse_mode,
                reasoning_key=self.reasoning_key,
            )

        return default_stream_handler

    def _build_default_response_parser(
        self,
        request: ResponseRequest,
    ) -> ProviderResponseParser[ChatCompletion]:
        """构建 OpenAI 默认非流式响应解析器。

        Args:
            request: 统一响应请求对象。

        Returns:
            ProviderResponseParser[ChatCompletion]: 默认非流式解析器。
        """
        del request

        def default_response_parser(
            response: ChatCompletion,
        ) -> Tuple[APIResponse, UsageTuple | None]:
            """包装默认非流式解析器。"""
            return _default_normal_response_parser(
                response,
                reasoning_parse_mode=self.reasoning_parse_mode,
                tool_argument_parse_mode=self.tool_argument_parse_mode,
                reasoning_key=self.reasoning_key,
            )

        return default_response_parser

    async def _execute_response_request(
        self,
        request: ResponseRequest,
        stream_response_handler: ProviderStreamResponseHandler[AsyncStream[ChatCompletionChunk]],
        response_parser: ProviderResponseParser[ChatCompletion],
    ) -> Tuple[APIResponse, UsageTuple | None]:
        """执行 OpenAI 兼容的文本/多模态响应请求。

        Args:
            request: 统一响应请求对象。
            stream_response_handler: 流式响应处理器。
            response_parser: 非流式响应解析器。

        Returns:
            Tuple[APIResponse, UsageTuple | None]: 统一响应对象与可选使用量信息。
        """
        snapshot_provider_request = {
            "base_url": self.api_provider.base_url,
            "endpoint": "/chat/completions",
            "method": "POST",
            "operation": "chat.completions.create",
            "organization": self.api_provider.organization,
            "project": self.api_provider.project,
            "request_kwargs": {},
        }
        model_info = request.model_info

        try:
            request_messages = (
                list(request.message_list)
                if request.tool_options
                else _sanitize_messages_for_toolless_request(request.message_list)
            )
            messages_payload: List[ChatCompletionMessageParam] = _convert_messages(request_messages)
            tools_payload: List[ChatCompletionToolParam] | None = (
                _convert_tool_options(request.tool_options) if request.tool_options else None
            )
            openai_response_format = _convert_response_format(request.response_format)
            request_overrides = split_openai_request_overrides(
                request.extra_params,
                reserved_body_keys=CHAT_COMPLETIONS_RESERVED_EXTRA_BODY_KEYS,
            )

            temperature_argument = (
                omit if "temperature" in request_overrides.extra_body else _coerce_openai_argument(request.temperature)
            )

            async def _dispatch(use_max_completion_tokens: bool) -> Tuple[APIResponse, UsageTuple | None]:
                """构造并发起一次 chat.completions 请求。

                Args:
                    use_max_completion_tokens: 为 True 时改用 max_completion_tokens 承载
                        最大输出 token 数，并省略原生 max_tokens（适配 gpt-5 系列等模型）。
                """
                # 每次用副本，避免重试两次之间相互污染 extra_body
                extra_body = dict(request_overrides.extra_body)
                if use_max_completion_tokens:
                    # 自动改用 max_completion_tokens：用户未显式给值时用任务级 max_tokens 填充
                    if "max_completion_tokens" not in extra_body and request.max_tokens is not None:
                        extra_body["max_completion_tokens"] = request.max_tokens
                    max_tokens_argument = omit
                else:
                    max_tokens_argument = (
                        omit
                        if "max_tokens" in extra_body or "max_completion_tokens" in extra_body
                        else _coerce_openai_argument(request.max_tokens)
                    )
                snapshot_provider_request["request_kwargs"] = {
                    "extra_body": extra_body or None,
                    "extra_headers": request_overrides.extra_headers or None,
                    "extra_query": request_overrides.extra_query or None,
                    "max_tokens": _snapshot_openai_argument(max_tokens_argument),
                    "messages": messages_payload,
                    "model": model_info.model_identifier,
                    "response_format": _snapshot_openai_argument(openai_response_format),
                    "stream": bool(model_info.force_stream_mode),
                    "temperature": _snapshot_openai_argument(temperature_argument),
                    "tools": tools_payload,
                }

                if model_info.force_stream_mode:
                    stream_task: asyncio.Task[AsyncStream[ChatCompletionChunk]] = asyncio.create_task(
                        self.client.chat.completions.create(
                            model=model_info.model_identifier,
                            messages=messages_payload,
                            tools=tools_payload or omit,
                            temperature=temperature_argument,
                            max_tokens=max_tokens_argument,
                            stream=True,
                            response_format=openai_response_format,
                            extra_headers=request_overrides.extra_headers or None,
                            extra_query=request_overrides.extra_query or None,
                            extra_body=extra_body or None,
                        )
                    )
                    raw_response = cast(
                        AsyncStream[ChatCompletionChunk],
                        await await_task_with_interrupt(stream_task, request.interrupt_flag),
                    )
                    return await stream_response_handler(raw_response, request.interrupt_flag)

                completion_task: asyncio.Task[ChatCompletion] = asyncio.create_task(
                    self.client.chat.completions.create(
                        model=model_info.model_identifier,
                        messages=messages_payload,
                        tools=tools_payload or omit,
                        temperature=temperature_argument,
                        max_tokens=max_tokens_argument,
                        stream=False,
                        response_format=openai_response_format,
                        extra_headers=request_overrides.extra_headers or None,
                        extra_query=request_overrides.extra_query or None,
                        extra_body=extra_body or None,
                    )
                )
                raw_response = cast(
                    ChatCompletion,
                    await await_task_with_interrupt(completion_task, request.interrupt_flag),
                )
                return response_parser(raw_response)

            # 已知仅支持 max_completion_tokens 的模型直接走对应分支；否则先按常规发送，
            # 命中「max_tokens 不被支持」的 400 错误时自动改用 max_completion_tokens 重试并记忆。
            cache_key = (self.api_provider.base_url or "", model_info.model_identifier)
            use_mct = cache_key in _MODELS_REQUIRING_MAX_COMPLETION_TOKENS
            try:
                return await _dispatch(use_mct)
            except APIStatusError as exc:
                if not use_mct and _is_max_tokens_unsupported_error(exc):
                    _MODELS_REQUIRING_MAX_COMPLETION_TOKENS.add(cache_key)
                    logger.info(
                        f"模型 '{model_info.name}' 不支持 max_tokens，自动改用 "
                        f"max_completion_tokens 重试并记忆该模型。"
                    )
                    return await _dispatch(True)
                raise
        except (EmptyResponseException, RespParseException) as exc:
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="openai",
                error=exc,
                internal_request=serialize_response_request_snapshot(request),
                model_info=model_info,
                operation="chat.completions.create",
                provider_request=snapshot_provider_request,
            )
            attach_request_snapshot(exc, snapshot_path)
            raise
        except APIConnectionError as exc:
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="openai",
                error=exc,
                internal_request=serialize_response_request_snapshot(request),
                model_info=model_info,
                operation="chat.completions.create",
                provider_request=snapshot_provider_request,
            )
            wrapped_error = NetworkConnectionError(str(exc))
            attach_request_snapshot(wrapped_error, snapshot_path)
            raise wrapped_error from exc
        except APIStatusError as exc:
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="openai",
                error=exc,
                internal_request=serialize_response_request_snapshot(request),
                model_info=model_info,
                operation="chat.completions.create",
                provider_request=snapshot_provider_request,
            )
            wrapped_error = RespNotOkException(exc.status_code, _build_api_status_message(exc))
            attach_request_snapshot(wrapped_error, snapshot_path)
            raise wrapped_error from exc
        except ReqAbortException:
            raise
        except Exception as exc:
            if has_request_snapshot(exc):
                raise
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="openai",
                error=exc,
                internal_request=serialize_response_request_snapshot(request),
                model_info=model_info,
                operation="chat.completions.create",
                provider_request=snapshot_provider_request,
            )
            attach_request_snapshot(exc, snapshot_path)
            raise

    async def _execute_embedding_request(
        self,
        request: EmbeddingRequest,
    ) -> Tuple[APIResponse, UsageTuple | None]:
        """执行 OpenAI 兼容的文本嵌入请求。

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
            "endpoint": "/embeddings",
            "method": "POST",
            "operation": "embeddings.create",
            "organization": self.api_provider.organization,
            "project": self.api_provider.project,
            "request_kwargs": {},
        }

        try:
            request_overrides = split_openai_request_overrides(extra_params)
            snapshot_provider_request["request_kwargs"] = {
                "extra_body": request_overrides.extra_body or None,
                "extra_headers": request_overrides.extra_headers or None,
                "extra_query": request_overrides.extra_query or None,
                "input": embedding_input,
                "model": model_info.model_identifier,
            }
            raw_response = await self.client.embeddings.create(
                model=model_info.model_identifier,
                input=embedding_input,
                extra_headers=request_overrides.extra_headers or None,
                extra_query=request_overrides.extra_query or None,
                extra_body=request_overrides.extra_body or None,
            )
        except APIConnectionError as exc:
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="openai",
                error=exc,
                internal_request=serialize_embedding_request_snapshot(request),
                model_info=model_info,
                operation="embeddings.create",
                provider_request=snapshot_provider_request,
            )
            wrapped_error = NetworkConnectionError(str(exc))
            attach_request_snapshot(wrapped_error, snapshot_path)
            raise wrapped_error from exc
        except APIStatusError as exc:
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="openai",
                error=exc,
                internal_request=serialize_embedding_request_snapshot(request),
                model_info=model_info,
                operation="embeddings.create",
                provider_request=snapshot_provider_request,
            )
            wrapped_error = RespNotOkException(exc.status_code, _build_api_status_message(exc))
            attach_request_snapshot(wrapped_error, snapshot_path)
            raise wrapped_error from exc
        except Exception as exc:
            if has_request_snapshot(exc):
                raise
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="openai",
                error=exc,
                internal_request=serialize_embedding_request_snapshot(request),
                model_info=model_info,
                operation="embeddings.create",
                provider_request=snapshot_provider_request,
            )
            attach_request_snapshot(exc, snapshot_path)
            raise

        response = APIResponse()
        if not raw_response.data:
            exc = RespParseException(raw_response, "嵌入响应解析失败，缺少 embeddings 数据。")
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="openai",
                error=exc,
                internal_request=serialize_embedding_request_snapshot(request),
                model_info=model_info,
                operation="embeddings.create",
                provider_request=snapshot_provider_request,
            )
            attach_request_snapshot(exc, snapshot_path)
            raise exc
        if raw_response.data:
            response.embedding = raw_response.data[0].embedding
        else:
            raise RespParseException(raw_response, "响应解析失败，缺失嵌入数据。")

        usage_record = _extract_usage_record(getattr(raw_response, "usage", None))
        return response, usage_record

    async def _execute_audio_transcription_request(
        self,
        request: AudioTranscriptionRequest,
    ) -> Tuple[APIResponse, UsageTuple | None]:
        """执行 OpenAI 兼容的音频转录请求。

        Args:
            request: 统一音频转录请求对象。

        Returns:
            Tuple[APIResponse, UsageTuple | None]: 统一响应对象与可选使用量信息。
        """
        model_info = request.model_info
        audio_base64 = request.audio_base64
        extra_params = request.extra_params
        snapshot_provider_request = {
            "base_url": self.api_provider.base_url,
            "endpoint": "/audio/transcriptions",
            "method": "POST",
            "operation": "audio.transcriptions.create",
            "organization": self.api_provider.organization,
            "project": self.api_provider.project,
            "request_kwargs": {},
        }

        try:
            request_overrides = split_openai_request_overrides(extra_params)
            audio_file: FileTypes = ("audio.wav", io.BytesIO(base64.b64decode(audio_base64)))
            snapshot_provider_request["request_kwargs"] = {
                "audio_base64": audio_base64,
                "extra_body": request_overrides.extra_body or None,
                "extra_headers": request_overrides.extra_headers or None,
                "extra_query": request_overrides.extra_query or None,
                "file_name": "audio.wav",
                "model": model_info.model_identifier,
            }
            raw_response = await self.client.audio.transcriptions.create(
                model=model_info.model_identifier,
                file=audio_file,
                extra_headers=request_overrides.extra_headers or None,
                extra_query=request_overrides.extra_query or None,
                extra_body=request_overrides.extra_body or None,
            )
        except APIConnectionError as exc:
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="openai",
                error=exc,
                internal_request=serialize_audio_request_snapshot(request),
                model_info=model_info,
                operation="audio.transcriptions.create",
                provider_request=snapshot_provider_request,
            )
            wrapped_error = NetworkConnectionError(str(exc))
            attach_request_snapshot(wrapped_error, snapshot_path)
            raise wrapped_error from exc
        except APIStatusError as exc:
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="openai",
                error=exc,
                internal_request=serialize_audio_request_snapshot(request),
                model_info=model_info,
                operation="audio.transcriptions.create",
                provider_request=snapshot_provider_request,
            )
            wrapped_error = RespNotOkException(exc.status_code, _build_api_status_message(exc))
            attach_request_snapshot(wrapped_error, snapshot_path)
            raise wrapped_error from exc
        except Exception as exc:
            if has_request_snapshot(exc):
                raise
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="openai",
                error=exc,
                internal_request=serialize_audio_request_snapshot(request),
                model_info=model_info,
                operation="audio.transcriptions.create",
                provider_request=snapshot_provider_request,
            )
            attach_request_snapshot(exc, snapshot_path)
            raise

        response = APIResponse()
        transcription_text = raw_response if isinstance(raw_response, str) else getattr(raw_response, "text", None)
        if not isinstance(transcription_text, str):
            exc = RespParseException(raw_response, "音频转写响应解析失败，缺少文本内容。")
            snapshot_path = save_failed_request_snapshot(
                api_provider=self.api_provider,
                client_type="openai",
                error=exc,
                internal_request=serialize_audio_request_snapshot(request),
                model_info=model_info,
                operation="audio.transcriptions.create",
                provider_request=snapshot_provider_request,
            )
            attach_request_snapshot(exc, snapshot_path)
            raise exc
        if isinstance(transcription_text, str):
            response.content = transcription_text
            return response, None
        raise RespParseException(raw_response, "响应解析失败，缺失转录文本。")

    def get_support_image_formats(self) -> List[str]:
        """获取支持的图片格式列表。

        Returns:
            List[str]: 当前客户端支持的图片格式列表。
        """
        return ["jpg", "jpeg", "png", "webp", "gif"]
