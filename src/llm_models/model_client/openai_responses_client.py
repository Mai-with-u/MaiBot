from copy import deepcopy
from typing import Any, Dict, List, Sequence, Tuple, cast

import asyncio
import json

from openai import APIConnectionError, APIStatusError, AsyncStream
from openai._types import omit
from openai.types.responses import Response, ResponseStreamEvent

from src.common.logger import get_logger
from src.llm_models.exceptions import (
    EmptyResponseException,
    NetworkConnectionError,
    ReqAbortException,
    RespNotOkException,
    RespParseException,
)
from src.llm_models.openai_compat import split_openai_request_overrides
from src.llm_models.payload_content.message import ImageMessagePart, Message, RoleType, TextMessagePart
from src.llm_models.payload_content.native_tool import NativeToolCallSummary
from src.llm_models.payload_content.provider_state import (
    PROVIDER_STATE_SCHEMA_VERSION,
    ProviderState,
    build_assistant_message_fingerprint,
    build_provider_endpoint_fingerprint,
)
from src.llm_models.payload_content.resp_format import RespFormat, RespFormatType
from src.llm_models.payload_content.tool_option import (
    TOOL_CALL_SOURCE_EXTRA_KEY,
    TOOL_CALL_SOURCE_RESPONSE,
    ToolCall,
    ToolOption,
)
from src.llm_models.request_snapshot import (
    attach_request_snapshot,
    has_request_snapshot,
    save_failed_request_snapshot,
    serialize_response_request_snapshot,
)

from .adapter_base import await_task_with_interrupt
from .base_client import APIResponse, ResponseRequest, UsageTuple, client_registry
from .openai_client import (
    OpenaiClient,
    _build_api_status_message,
    _normalize_image_part_for_openai,
    _parse_tool_arguments,
)


RESPONSES_CLIENT_TYPE = "openai_responses"
RESPONSES_OPERATION = "responses.create"
RESPONSES_ENDPOINT = "/responses"
NATIVE_TOOL_DETAIL_LIMIT = 500
NATIVE_TOOL_DETAIL_COUNT_LIMIT = 5

logger = get_logger("llm_models")


def _serialize_response_item(item: Any) -> Dict[str, Any]:
    """将 SDK Response Item 转换为可持久化的普通字典。"""

    if isinstance(item, dict):
        return deepcopy(item)
    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json", exclude_none=True)
        if isinstance(payload, dict):
            return payload
    raise RespParseException(item, f"Responses output item 无法序列化: {type(item).__name__}")


def _serialize_response(response: Any) -> Dict[str, Any]:
    """完整序列化 SDK Response，保留空值和未知扩展字段供诊断记录使用。"""

    if isinstance(response, dict):
        return deepcopy(response)
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json", exclude_none=False)
        if isinstance(payload, dict):
            return payload
    try:
        payload = {
            key: deepcopy(value)
            for key, value in vars(response).items()
            if not key.startswith("_")
        }
    except TypeError as exc:
        raise RespParseException(response, f"Responses 响应无法序列化: {type(response).__name__}") from exc
    if payload:
        return payload
    raise RespParseException(response, f"Responses 响应无法序列化: {type(response).__name__}")


def _get_value(value: Any, key: str, default: Any = None) -> Any:
    """同时读取 SDK 对象与普通字典字段。"""

    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _convert_text_parts(message: Message) -> str:
    """将只允许文本的消息内容转换为字符串。"""

    text_parts: List[str] = []
    for part in message.parts:
        if not isinstance(part, TextMessagePart):
            raise ValueError(f"{message.role.value} 消息仅支持文本片段")
        text_parts.append(part.text)
    return "".join(text_parts)


def _convert_user_content(message: Message) -> List[Dict[str, Any]]:
    """将用户消息转换为 Responses 输入内容块。"""

    content: List[Dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextMessagePart):
            if part.text.strip():
                content.append({"type": "input_text", "text": part.text})
            continue

        if not isinstance(part, ImageMessagePart):
            raise ValueError(f"不支持的消息片段类型: {type(part).__name__}")
        normalized_image = _normalize_image_part_for_openai(part)
        if normalized_image is None:
            content.append({"type": "input_text", "text": "[图片内容不可用]"})
            continue
        image_format, image_base64 = normalized_image
        content.append(
            {
                "type": "input_image",
                "detail": "auto",
                "image_url": f"data:image/{image_format};base64,{image_base64}",
            }
        )
    return content


def _can_replay_provider_state(message: Message, request: ResponseRequest, provider_name: str, base_url: str) -> bool:
    """判断 assistant 消息携带的 Responses Items 是否仍可安全回放。"""

    state = message.provider_state
    if state is None:
        return False
    return (
        state.schema_version == PROVIDER_STATE_SCHEMA_VERSION
        and state.client_type == RESPONSES_CLIENT_TYPE
        and state.provider_name == provider_name
        and state.endpoint_fingerprint == build_provider_endpoint_fingerprint(RESPONSES_CLIENT_TYPE, base_url)
        and state.model_identifier == request.model_info.model_identifier
        and state.message_fingerprint
        == build_assistant_message_fingerprint(message.get_text_content(), message.tool_calls)
        and bool(state.output_items)
    )


def _convert_messages(
    messages: Sequence[Message],
    request: ResponseRequest,
    provider_name: str,
    base_url: str,
) -> List[Dict[str, Any]]:
    """将统一消息转换为 Responses API Input Items。"""

    input_items: List[Dict[str, Any]] = []
    for message in messages:
        if message.role == RoleType.Assistant and _can_replay_provider_state(
            message,
            request,
            provider_name,
            base_url,
        ):
            input_items.extend(deepcopy(message.provider_state.output_items))
            continue

        if message.role == RoleType.System:
            input_items.append({"role": "system", "content": _convert_text_parts(message)})
            continue

        if message.role == RoleType.User:
            input_items.append({"role": "user", "content": _convert_user_content(message)})
            continue

        if message.role == RoleType.Assistant:
            assistant_content = _convert_text_parts(message)
            if assistant_content:
                input_items.append({"role": "assistant", "content": assistant_content})
            for tool_call in message.tool_calls or []:
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": tool_call.call_id,
                        "name": tool_call.func_name,
                        "arguments": json.dumps(tool_call.args or {}, ensure_ascii=False),
                    }
                )
            continue

        if message.role == RoleType.Tool:
            if message.tool_call_id is None:
                raise ValueError("Tool 消息缺少 tool_call_id")
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": _convert_text_parts(message),
                }
            )
            continue

        raise ValueError(f"不支持的消息角色: {message.role}")

    return input_items


def _convert_tool_options(tool_options: Sequence[ToolOption] | None) -> List[Dict[str, Any]]:
    """将 MaiBot function tools 转换为 Responses 原生工具定义。"""

    tools: List[Dict[str, Any]] = []
    for tool_option in tool_options or []:
        tools.append(
            {
                "type": "function",
                "name": tool_option.name,
                "description": tool_option.description,
                "parameters": tool_option.parameters_schema or {"type": "object", "properties": {}, "required": []},
            }
        )
    return tools


def _convert_response_format(response_format: RespFormat | None) -> Dict[str, Any] | None:
    """将统一响应格式转换为 Responses ``text.format``。"""

    if response_format is None or response_format.format_type == RespFormatType.TEXT:
        return None
    if response_format.format_type == RespFormatType.JSON_OBJ:
        return {"type": "json_object"}
    if response_format.format_type == RespFormatType.JSON_SCHEMA:
        if response_format.schema is None:
            raise ValueError("JSON Schema 响应格式缺少 schema")
        return {"type": "json_schema", **deepcopy(response_format.schema)}
    raise ValueError(f"不支持的响应格式: {response_format.format_type}")


def _merge_native_tools(extra_body: Dict[str, Any], function_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """合并 MaiBot function tools 与模型配置中的 Responses 原生工具。"""

    raw_native_tools = extra_body.pop("tools", [])
    if raw_native_tools in (None, []):
        return function_tools
    if not isinstance(raw_native_tools, list) or not all(isinstance(item, dict) for item in raw_native_tools):
        raise ValueError("Responses extra_params.body.tools 必须是工具定义字典列表")
    function_names = {str(tool.get("name", "")) for tool in function_tools if tool.get("type") == "function"}
    duplicate_names = {
        str(tool.get("name", ""))
        for tool in raw_native_tools
        if tool.get("type") == "function" and str(tool.get("name", "")) in function_names
    }
    if duplicate_names:
        raise ValueError(f"Responses 原生 function tools 与 MaiBot 工具重名: {', '.join(sorted(duplicate_names))}")
    return [*function_tools, *deepcopy(raw_native_tools)]


def _merge_text_config(extra_body: Dict[str, Any], response_format: RespFormat | None) -> Dict[str, Any] | None:
    """合并模型原生 text 配置与 MaiBot 结构化输出配置。"""

    raw_text_config = extra_body.pop("text", {})
    if raw_text_config in (None, {}):
        text_config: Dict[str, Any] = {}
    elif isinstance(raw_text_config, dict):
        text_config = deepcopy(raw_text_config)
    else:
        raise ValueError("Responses extra_params.body.text 必须是字典")

    format_config = _convert_response_format(response_format)
    if format_config is not None:
        existing_format = text_config.get("format")
        if existing_format is not None and existing_format != format_config:
            raise ValueError("Responses 原生 text.format 与 MaiBot response_format 冲突")
        text_config["format"] = format_config
    return text_config or None


def _extract_usage_record(usage: Any) -> UsageTuple | None:
    """提取 Responses API 使用量。"""

    if usage is None:
        return None
    input_tokens = int(_get_value(usage, "input_tokens", 0) or 0)
    output_tokens = int(_get_value(usage, "output_tokens", 0) or 0)
    total_tokens = int(_get_value(usage, "total_tokens", input_tokens + output_tokens) or 0)
    input_details = _get_value(usage, "input_tokens_details")
    cached_tokens = int(_get_value(input_details, "cached_tokens", 0) or 0)
    return (
        input_tokens,
        output_tokens,
        total_tokens,
        cached_tokens,
        max(input_tokens - cached_tokens, 0),
    )


def _extract_reasoning_summary(item: Any) -> List[str]:
    """提取 Responses reasoning item 中可展示的摘要文本。"""

    summary_parts: List[str] = []
    for summary_part in _get_value(item, "summary", []) or []:
        text = _get_value(summary_part, "text")
        if isinstance(text, str) and text.strip():
            summary_parts.append(text.strip())
    return summary_parts


def _extract_reasoning_display_text(item: Any) -> List[str]:
    """提取 reasoning item 的可展示文本，优先使用摘要并兼容明文推理块。"""

    summary_parts = _extract_reasoning_summary(item)
    if summary_parts:
        return summary_parts

    reasoning_parts: List[str] = []
    for content_part in _get_value(item, "content", []) or []:
        if str(_get_value(content_part, "type", "") or "") != "reasoning_text":
            continue
        text = _get_value(content_part, "text")
        if isinstance(text, str) and text.strip():
            reasoning_parts.append(text.strip())
    return reasoning_parts


def _normalize_native_tool_detail(value: Any) -> str:
    """将原生工具可观测字段压缩为适合日志和 WebUI 的单行摘要。"""

    detail = " ".join(str(value or "").split()).strip()
    if len(detail) <= NATIVE_TOOL_DETAIL_LIMIT:
        return detail
    return detail[: NATIVE_TOOL_DETAIL_LIMIT - 3] + "..."


def _extract_web_search_summary(item: Any) -> NativeToolCallSummary:
    """从标准 web_search_call item 提取不含原始搜索结果的安全摘要。"""

    action = _get_value(item, "action")
    action_type = str(_get_value(action, "type", "") or "").strip()
    details: List[str] = []
    source_count = 0

    if action_type == "search":
        raw_queries = _get_value(action, "queries")
        if not isinstance(raw_queries, list) or not raw_queries:
            raw_queries = [_get_value(action, "query")]
        for raw_query in raw_queries:
            query = _normalize_native_tool_detail(raw_query)
            if query:
                details.append(f"查询：{query}")
        sources = _get_value(action, "sources")
        if isinstance(sources, list):
            source_count = len(sources)
    elif action_type == "open_page":
        url = _normalize_native_tool_detail(_get_value(action, "url"))
        if url:
            details.append(f"页面：{url}")
    elif action_type == "find_in_page":
        pattern = _normalize_native_tool_detail(_get_value(action, "pattern"))
        url = _normalize_native_tool_detail(_get_value(action, "url"))
        if pattern:
            details.append(f"页内查找：{pattern}")
        if url:
            details.append(f"页面：{url}")

    return NativeToolCallSummary(
        tool_type="web_search",
        call_id=str(_get_value(item, "id", "") or "").strip(),
        status=str(_get_value(item, "status", "") or "").strip(),
        action_type=action_type,
        details=details[:NATIVE_TOOL_DETAIL_COUNT_LIMIT],
        source_count=source_count,
    )


def _extract_native_tool_summaries(output: Sequence[Any]) -> List[NativeToolCallSummary]:
    """仅从本次响应 output 提取原生工具摘要，并按调用 ID 去重。"""

    summaries: List[NativeToolCallSummary] = []
    seen_call_keys: set[str] = set()
    for index, item in enumerate(output):
        item_type = str(_get_value(item, "type", "") or "")
        if item_type != "web_search_call":
            continue
        summary = _extract_web_search_summary(item)
        call_key = summary.call_id or f"{item_type}:{index}"
        if call_key in seen_call_keys:
            continue
        seen_call_keys.add(call_key)
        summaries.append(summary)
    return summaries


def _format_native_tool_log(summaries: Sequence[NativeToolCallSummary]) -> str:
    """生成一条有界的原生工具调用日志文本。"""

    formatted_calls: List[str] = []
    for summary in summaries:
        labels = [label for label in (summary.action_type, summary.status) if label]
        header = summary.tool_type
        if labels:
            header += f"[{', '.join(labels)}]"
        detail = "；".join(summary.details)
        if summary.source_count:
            detail = f"{detail}；来源 {summary.source_count} 个" if detail else f"来源 {summary.source_count} 个"
        formatted_calls.append(f"{header}({detail})" if detail else header)
    return ", ".join(formatted_calls)


def _parse_completed_response(
    response: Response | Any,
    request: ResponseRequest,
    provider_name: str,
    base_url: str,
    tool_argument_parse_mode: Any,
) -> Tuple[APIResponse, UsageTuple | None]:
    """将完整 Responses 响应投影为 MaiBot APIResponse。"""

    status = str(_get_value(response, "status", "") or "")
    if status in {"failed", "incomplete", "cancelled"}:
        details = _get_value(response, "error") or _get_value(response, "incomplete_details") or status
        raise RespParseException(response, f"Responses 请求未完整完成: {details}")

    output = list(_get_value(response, "output", []) or [])
    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_calls: List[ToolCall] = []
    output_types: List[str] = []

    for item in output:
        item_type = str(_get_value(item, "type", "") or "")
        if item_type:
            output_types.append(item_type)

        if item_type == "message":
            for content_part in _get_value(item, "content", []) or []:
                content_type = str(_get_value(content_part, "type", "") or "")
                if content_type == "output_text":
                    text = _get_value(content_part, "text")
                    if isinstance(text, str) and text:
                        content_parts.append(text)
                elif content_type == "refusal":
                    refusal = _get_value(content_part, "refusal")
                    if isinstance(refusal, str) and refusal:
                        content_parts.append(refusal)
            continue

        if item_type == "reasoning":
            reasoning_parts.extend(_extract_reasoning_display_text(item))
            continue

        if item_type == "function_call":
            call_id = str(_get_value(item, "call_id", "") or "").strip()
            function_name = str(_get_value(item, "name", "") or "").strip()
            raw_arguments = str(_get_value(item, "arguments", "") or "")
            if not call_id or not function_name:
                raise RespParseException(response, "Responses function_call 缺少 call_id 或 name")
            arguments = _parse_tool_arguments(raw_arguments, tool_argument_parse_mode, response)
            tool_calls.append(
                ToolCall(
                    call_id=call_id,
                    func_name=function_name,
                    args=arguments,
                    extra_content={TOOL_CALL_SOURCE_EXTRA_KEY: TOOL_CALL_SOURCE_RESPONSE},
                )
            )

    content = "".join(content_parts).strip()
    reasoning_content = "\n".join(reasoning_parts).strip()
    if not content and not reasoning_content and not tool_calls:
        raise EmptyResponseException(
            {
                "model": _get_value(response, "model"),
                "output_types": output_types,
                "response_id": _get_value(response, "id"),
                "status": status,
            }
        )

    serialized_output = [_serialize_response_item(item) for item in output]
    provider_state = ProviderState(
        client_type=RESPONSES_CLIENT_TYPE,
        provider_name=provider_name,
        endpoint_fingerprint=build_provider_endpoint_fingerprint(RESPONSES_CLIENT_TYPE, base_url),
        model_identifier=request.model_info.model_identifier,
        message_fingerprint=build_assistant_message_fingerprint(content, tool_calls),
        output_items=serialized_output,
    )
    api_response = APIResponse(
        content=content or None,
        reasoning_content=reasoning_content or None,
        tool_calls=tool_calls or None,
        raw_data={
            "model": _get_value(response, "model"),
            "output_types": output_types,
            "response_id": _get_value(response, "id"),
            "status": status,
        },
        provider_state=provider_state,
        provider_response=_serialize_response(response),
        native_tool_calls=_extract_native_tool_summaries(output),
    )
    return api_response, _extract_usage_record(_get_value(response, "usage"))


async def _consume_response_stream(
    stream: AsyncStream[ResponseStreamEvent],
    interrupt_flag: asyncio.Event | None,
) -> Response:
    """消费 Responses 类型化事件流并返回最终完整响应。"""

    completed_response: Response | None = None
    try:
        async for event in stream:
            if interrupt_flag and interrupt_flag.is_set():
                raise ReqAbortException("请求被外部信号中断")

            event_type = str(_get_value(event, "type", "") or "")
            if event_type == "response.completed":
                completed_response = cast(Response, _get_value(event, "response"))
            elif event_type in {"response.failed", "response.incomplete"}:
                failed_response = _get_value(event, "response")
                details = _get_value(failed_response, "error") or _get_value(
                    failed_response,
                    "incomplete_details",
                )
                raise RespParseException(failed_response, f"Responses 流式请求未完整完成: {details or event_type}")
            elif event_type == "error":
                raise RespParseException(event, f"Responses 流式请求返回错误: {_get_value(event, 'message', event)}")
    finally:
        await stream.close()

    if completed_response is None:
        raise RespParseException(message="Responses 流结束时缺少 response.completed 事件")
    return completed_response


@client_registry.register_client_class(RESPONSES_CLIENT_TYPE)
class OpenAIResponsesClient(OpenaiClient):
    """使用 OpenAI Responses API 的客户端。"""

    async def get_response(self, request: ResponseRequest) -> APIResponse:
        """执行 Responses API 请求并返回统一响应。"""

        if request.stream_response_handler is not None or request.async_response_parser is not None:
            raise RespParseException(message="openai_responses 暂不支持自定义原始流处理器或响应解析器")

        model_info = request.model_info
        snapshot_provider_request: Dict[str, Any] = {
            "base_url": self.api_provider.base_url,
            "endpoint": RESPONSES_ENDPOINT,
            "method": "POST",
            "operation": RESPONSES_OPERATION,
            "organization": self.api_provider.organization,
            "project": self.api_provider.project,
            "request_kwargs": {},
        }

        try:
            input_items = _convert_messages(
                request.message_list,
                request,
                self.api_provider.name,
                self.api_provider.base_url,
            )
            request_overrides = split_openai_request_overrides(request.extra_params)
            extra_body = dict(request_overrides.extra_body)

            for protected_key in ("input", "model", "previous_response_id", "stream"):
                if protected_key in extra_body:
                    raise ValueError(f"Responses extra_params 不允许覆盖由客户端管理的字段: {protected_key}")

            tools = _merge_native_tools(extra_body, _convert_tool_options(request.tool_options))
            text_config = _merge_text_config(extra_body, request.response_format)
            legacy_max_tokens = extra_body.pop("max_tokens", None)
            max_output_tokens = extra_body.pop(
                "max_output_tokens",
                request.max_tokens if request.max_tokens is not None else legacy_max_tokens,
            )
            temperature = extra_body.pop("temperature", request.temperature)
            store = extra_body.pop("store", False)
            if store is not False:
                raise ValueError("openai_responses 当前固定使用 store=false")

            snapshot_provider_request["request_kwargs"] = {
                "extra_body": extra_body or None,
                "extra_headers": request_overrides.extra_headers or None,
                "extra_query": request_overrides.extra_query or None,
                "input": input_items,
                "max_output_tokens": max_output_tokens,
                "model": model_info.model_identifier,
                "store": store,
                "stream": bool(model_info.force_stream_mode),
                "temperature": temperature,
                "text": text_config,
                "tools": tools,
            }

            request_task = asyncio.create_task(
                self.client.responses.create(
                    model=model_info.model_identifier,
                    input=cast(Any, input_items),
                    tools=cast(Any, tools) if tools else omit,
                    text=cast(Any, text_config) if text_config else omit,
                    max_output_tokens=max_output_tokens if max_output_tokens is not None else omit,
                    temperature=temperature if temperature is not None else omit,
                    store=store,
                    stream=bool(model_info.force_stream_mode),
                    extra_headers=request_overrides.extra_headers or None,
                    extra_query=request_overrides.extra_query or None,
                    extra_body=extra_body or None,
                )
            )
            raw_response = await await_task_with_interrupt(request_task, request.interrupt_flag)
            if model_info.force_stream_mode:
                completed_response = await _consume_response_stream(
                    cast(AsyncStream[ResponseStreamEvent], raw_response),
                    request.interrupt_flag,
                )
            else:
                completed_response = cast(Response, raw_response)

            response, usage_record = _parse_completed_response(
                completed_response,
                request,
                self.api_provider.name,
                self.api_provider.base_url,
                self.tool_argument_parse_mode,
            )
            if response.native_tool_calls:
                logger.info(
                    "Responses 原生工具调用: "
                    f"provider={self.api_provider.name} model={model_info.model_identifier} "
                    f"response={response.raw_data.get('response_id', '')} "
                    f"calls={_format_native_tool_log(response.native_tool_calls)}"
                )
            if usage_record is not None:
                response.usage = self._build_usage_record(model_info, usage_record)
            return response
        except (EmptyResponseException, RespParseException) as exc:
            self._attach_failure_snapshot(exc, request, snapshot_provider_request)
            raise
        except APIConnectionError as exc:
            wrapped_error = NetworkConnectionError(str(exc))
            self._attach_failure_snapshot(wrapped_error, request, snapshot_provider_request, original_error=exc)
            raise wrapped_error from exc
        except APIStatusError as exc:
            wrapped_error = RespNotOkException(exc.status_code, _build_api_status_message(exc))
            self._attach_failure_snapshot(wrapped_error, request, snapshot_provider_request, original_error=exc)
            raise wrapped_error from exc
        except ReqAbortException:
            raise
        except Exception as exc:
            if not has_request_snapshot(exc):
                self._attach_failure_snapshot(exc, request, snapshot_provider_request)
            raise

    def _attach_failure_snapshot(
        self,
        error: Exception,
        request: ResponseRequest,
        provider_request: Dict[str, Any],
        *,
        original_error: Exception | None = None,
    ) -> None:
        """为 Responses 请求异常附加与现有客户端一致的失败快照。"""

        snapshot_path = save_failed_request_snapshot(
            api_provider=self.api_provider,
            client_type=RESPONSES_CLIENT_TYPE,
            error=original_error or error,
            internal_request=serialize_response_request_snapshot(request),
            model_info=request.model_info,
            operation=RESPONSES_OPERATION,
            provider_request=provider_request,
            trace_context=request.trace_context,
        )
        attach_request_snapshot(error, snapshot_path)
