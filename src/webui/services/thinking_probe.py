"""通过开关对照请求识别兼容端点的思考参数，不把参数被忽略误判为支持。"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai._types import omit
from pydantic import BaseModel, Field, field_validator

import asyncio
import httpx

from src.common.http_client import get_shared_ssl_context_async
from src.common.network_security import validate_public_url
from src.config.model_configs import APIProvider
from src.llm_models.openai_compat import (
    OpenAICompatibleClientConfig,
    build_openai_compatible_client_config,
    split_openai_request_overrides,
)


ThinkingProbeMode = Literal["thinking_type", "enable_thinking", "reasoning_effort_nested", "reasoning_effort"]
PROBE_MODES: List[ThinkingProbeMode] = [
    "thinking_type", "enable_thinking", "reasoning_effort_nested", "reasoning_effort",
]
PROBE_TIMEOUT_SECONDS = 20.0
PROBE_BATCH_SIZE = 2
PROBE_MAX_TOKENS = 1024
PROBE_PROMPT = (
    "A box contains 12 red, 9 blue and 7 green balls. Without looking, what is the minimum number "
    "of balls you must draw to guarantee at least 4 balls of the same color? Give only the answer."
)
THINKING_KEYS = {"thinking", "enable_thinking", "reasoning_effort", "thinking_budget", "thinking_config"}
# 探测必须使用相同问题、单个完整文本答复和有限输出，不能被模型的工具或生成参数覆盖。
CONTROLLED_KEYS = {
    "model", "messages", "input", "prompt", "instructions", "stream", "stream_options", "n", "stop",
    "max_tokens", "max_completion_tokens", "max_output_tokens", "tools", "tool_choice", "parallel_tool_calls",
    "functions", "function_call", "response_format", "text", "store", "background", "previous_response_id",
    "conversation", "temperature",
}


class ThinkingProbeRequest(BaseModel):
    """使用已保存厂商配置探测草稿模型，无需先保存模型。"""

    api_provider: str = Field(..., min_length=1)
    model_identifier: str = Field(..., min_length=1)
    extra_params: Dict[str, Any] = Field(default_factory=dict)
    force_stream_mode: bool = False

    @field_validator("api_provider", "model_identifier")
    @classmethod
    def strip_required_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("厂商和模型标识不能为空")
        return value


class ThinkingProbeAttempt(BaseModel):
    mode: ThinkingProbeMode
    status: Literal["matched", "inconclusive", "rejected"]
    message: str


class ThinkingProbeResponse(BaseModel):
    status: Literal["matched", "inconclusive"]
    matches: List[ThinkingProbeMode] = Field(default_factory=list)
    attempts: List[ThinkingProbeAttempt] = Field(default_factory=list)
    message: str


@dataclass
class ProbeObservation:
    text: str = ""
    reasoning: bool = False
    complete: bool = False
    refused: bool = False


def _build_probe_body(
    extra_params: Dict[str, Any], mode: ThinkingProbeMode, enabled: bool, client_type: str
) -> Dict[str, Any]:
    # 先合并 body 与顶层参数，再删除全部旧思考字段，避免不同模式互相影响。
    body = deepcopy(split_openai_request_overrides(extra_params).extra_body)
    for key in THINKING_KEYS | CONTROLLED_KEYS:
        body.pop(key, None)
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        reasoning.pop("effort", None)
        if not reasoning:
            body.pop("reasoning")
    else:
        body.pop("reasoning", None)
    if mode == "thinking_type":
        body["thinking"] = {"type": "enabled" if enabled else "disabled"}
    elif mode == "enable_thinking":
        body["enable_thinking"] = enabled
    elif mode == "reasoning_effort_nested":
        body["reasoning"] = {**body.get("reasoning", {}), "effort": "high" if enabled else "none"}
    else:
        body["reasoning_effort"] = "high" if enabled else "off"
    if client_type == "openai_responses":
        body["max_output_tokens"] = PROBE_MAX_TOKENS
        body["store"] = False
    else:
        # 尊重已配置的 Chat token 字段类型，同时把预算固定到探测上限。
        original = split_openai_request_overrides(extra_params).extra_body
        token_key = "max_completion_tokens" if "max_completion_tokens" in original else "max_tokens"
        body[token_key] = PROBE_MAX_TOKENS
    return body


def _has_reasoning_tokens(usage: Any) -> bool:
    if not isinstance(usage, dict):
        return False
    for key in ("completion_tokens_details", "output_tokens_details"):
        details = usage.get(key)
        if isinstance(details, dict):
            count = details.get("reasoning_tokens")
            if isinstance(count, (int, float)) and count > 0:
                return True
    return False


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part["text"] for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
            and isinstance(part.get("text"), str)
        )
    return ""


def _has_reasoning_text(message: Dict[str, Any]) -> bool:
    for key in ("reasoning_content", "reasoning"):
        if isinstance(message.get(key), str) and message[key].strip():
            return True
    details = message.get("reasoning_details")
    return isinstance(details, list) and any(
        isinstance(detail, dict) and any(
            isinstance(detail.get(key), str) and detail[key].strip() for key in ("text", "summary")
        ) for detail in details
    )


def _read_chat_response(payload: Dict[str, Any]) -> ProbeObservation:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ProbeObservation(reasoning=_has_reasoning_tokens(payload.get("usage")))
    choice = choices[0]
    message = choice.get("message", choice.get("delta", {}))
    if not isinstance(message, dict):
        return ProbeObservation()
    return ProbeObservation(
        text=_text_content(message.get("content")),
        reasoning=_has_reasoning_text(message) or _has_reasoning_tokens(payload.get("usage")),
        complete=choice.get("finish_reason") == "stop" and not message.get("refusal"),
        refused=bool(message.get("refusal")),
    )


def _read_responses_response(payload: Dict[str, Any]) -> ProbeObservation:
    observation = ProbeObservation(
        reasoning=_has_reasoning_tokens(payload.get("usage")),
        complete=payload.get("status") == "completed" and not payload.get("error"),
    )
    output = payload.get("output")
    if not isinstance(output, list):
        return observation
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            content = item.get("content")
            observation.text += _text_content(content)
            if isinstance(content, list) and any(
                isinstance(part, dict) and part.get("type") == "refusal" for part in content
            ):
                observation.complete = False
                observation.refused = True
        elif item.get("type") == "reasoning":
            # 空的 reasoning 占位项不证明模型进行了思考；只接受正文、摘要或 token 用量。
            for field in ("summary", "content"):
                parts = item.get(field)
                if isinstance(parts, list) and any(
                    isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip()
                    for part in parts
                ):
                    observation.reasoning = True
    return observation


async def _request_probe(
    client: AsyncOpenAI,
    provider: APIProvider,
    request: ThinkingProbeRequest,
    mode: ThinkingProbeMode,
    enabled: bool,
    should_stop: Optional[Callable[[], Awaitable[bool]]] = None,
) -> Optional[ProbeObservation]:
    overrides = split_openai_request_overrides(request.extra_params)
    client_config = build_openai_compatible_client_config(provider)
    extra_headers: Dict[str, Any] = {
        "Authorization" if key.lower() == "authorization" else key: value
        for key, value in overrides.extra_headers.items()
    }
    if not client_config.api_key and not any(key.lower() == "authorization" for key in client_config.default_headers):
        # 与实际客户端一致：none/header/query 鉴权不能额外带 SDK 的 Bearer 占位凭证。
        extra_headers.setdefault("Authorization", omit)
    kwargs: Dict[str, Any] = {
        "model": request.model_identifier,
        "stream": request.force_stream_mode,
        "extra_body": _build_probe_body(request.extra_params, mode, enabled, provider.client_type),
        "extra_headers": extra_headers,
        "extra_query": overrides.extra_query,
    }
    if provider.client_type == "openai_responses":
        result = await client.responses.create(input=PROBE_PROMPT, **kwargs)
        if not request.force_stream_mode:
            return _read_responses_response(result.model_dump())
        observation = ProbeObservation()
        async with result:
            async for event in result:
                payload = event.model_dump()
                if payload.get("type") in {"response.completed", "response.incomplete", "response.failed"}:
                    completed = _read_responses_response(payload.get("response", {}))
                    completed.reasoning |= observation.reasoning
                    completed.refused |= observation.refused
                    completed.complete &= not completed.refused
                    observation = completed
                elif payload.get("type") == "response.refusal.delta":
                    observation.refused = True
                    observation.complete = False
                elif payload.get("type") in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
                    delta = payload.get("delta")
                    if isinstance(delta, str) and delta.strip():
                        observation.reasoning = True
                elif payload.get("type") == "error":
                    return ProbeObservation()
        return observation

    if request.force_stream_mode:
        kwargs["stream_options"] = {"include_usage": True}
    messages = [{"role": "user", "content": PROBE_PROMPT}]
    try:
        result = await client.chat.completions.create(messages=messages, **kwargs)
    except APIStatusError as error:
        # 和实际客户端一样，仅在上游明确要求替换预算字段时协商一次，不重试模型生成错误。
        # 整次协商仍受外层单次请求的硬超时限制，后续对照请求沿用已确认的字段。
        message = f"{error.message} {error.response.text}".lower()
        if (
            error.status_code != 400 or "max_tokens" not in kwargs["extra_body"]
            or "max_tokens" not in message or "max_completion_tokens" not in message
        ):
            raise
        if should_stop and await should_stop():
            return None
        kwargs["extra_body"].pop("max_tokens")
        kwargs["extra_body"]["max_completion_tokens"] = PROBE_MAX_TOKENS
        request.extra_params["max_completion_tokens"] = PROBE_MAX_TOKENS
        result = await client.chat.completions.create(messages=messages, **kwargs)
    if not request.force_stream_mode:
        return _read_chat_response(result.model_dump())
    observation = ProbeObservation()
    async with result:
        async for chunk in result:
            part = _read_chat_response(chunk.model_dump())
            observation.text += part.text
            observation.reasoning |= part.reasoning
            observation.complete |= part.complete
            observation.refused |= part.refused
    observation.complete &= not observation.refused
    return observation


def _compare_observations(enabled: ProbeObservation, disabled: ProbeObservation) -> str:
    if (
        not enabled.complete or not disabled.complete or not enabled.text.strip() or not disabled.text.strip()
        or enabled.refused or disabled.refused
    ):
        return "测试未得到完整文本答复，无法确认开关效果（可能输出截断或被拒绝）"
    if not enabled.reasoning:
        return "开启时未返回可验证的思考内容或用量，无法确认此参数有效"
    if disabled.reasoning:
        return "关闭后仍返回思考内容或用量，参数可能被忽略或模型不支持关闭"
    return ""


def _create_probe_client(
    client_config: OpenAICompatibleClientConfig, provider: APIProvider, http_client: httpx.AsyncClient
) -> AsyncOpenAI:
    client = AsyncOpenAI(
        api_key=client_config.api_key or "maibot-sdk-auth-disabled",
        base_url=client_config.base_url,
        default_headers={
            "Authorization" if key.lower() == "authorization" else key: value
            for key, value in client_config.default_headers.items()
        },
        default_query=client_config.default_query,
        organization=provider.organization,
        project=provider.project,
        timeout=PROBE_TIMEOUT_SECONDS,
        max_retries=0,
        http_client=http_client,
    )
    # SDK 资源首次访问会同步导入模块，和客户端构造一起在线程里预热。
    _ = client.chat.completions, client.responses
    return client


async def probe_thinking(
    provider: APIProvider,
    request: ThinkingProbeRequest,
    should_stop: Optional[Callable[[], Awaitable[bool]]] = None,
) -> ThinkingProbeResponse:
    """每批并行两个模式，分两批最多执行八次对照生成；不重试生成失败，无法确认时保留原配置。"""
    # 预算字段协商只影响本次探测，不能修改调用方传入的模型草稿。
    request = request.model_copy(deep=True)
    client_config = build_openai_compatible_client_config(provider)
    client_config.base_url = validate_public_url(client_config.base_url, require_public_network=False)
    attempts: List[ThinkingProbeAttempt] = []
    matches: List[ThinkingProbeMode] = []
    stopped = asyncio.Event()
    stop_check_lock = asyncio.Lock()

    async def stop_requested() -> bool:
        if stopped.is_set():
            return True
        if should_stop:
            # Request.is_disconnected 会读取 ASGI receive，两个模式不能同时检查。
            async with stop_check_lock:
                if stopped.is_set():
                    return True
                if await should_stop():
                    stopped.set()
                    return True
        return stopped.is_set()

    # 不跟随重定向，避免已校验地址把凭证转发到未校验的目标。
    async with httpx.AsyncClient(
        verify=await get_shared_ssl_context_async(), timeout=PROBE_TIMEOUT_SECONDS, follow_redirects=False
    ) as http_client:
        async with await asyncio.to_thread(_create_probe_client, client_config, provider, http_client) as client:
            async def probe_mode(mode: ThinkingProbeMode) -> Optional[ThinkingProbeAttempt]:
                try:
                    if await stop_requested():
                        return None
                    async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                        enabled = await _request_probe(client, provider, request, mode, True, should_stop=stop_requested)
                    if enabled is None or await stop_requested():
                        return None
                    async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                        disabled = await _request_probe(client, provider, request, mode, False, should_stop=stop_requested)
                    if disabled is None:
                        return None
                except APIStatusError as error:
                    code = error.status_code
                    # 上游错误正文可能回显请求头或密钥，只向界面提供状态码与诊断类别。
                    message = f"上游拒绝此参数模式（HTTP {code}）"
                    if code in {401, 403}:
                        message = f"提供商鉴权失败（HTTP {code}），请检查密钥和权限"
                    elif code == 429:
                        message = "提供商限流或额度不足（HTTP 429），已停止测试"
                    elif code == 404:
                        message = "提供商接口或模型不存在（HTTP 404），请检查地址和模型标识"
                    if code not in {400, 422}:
                        stopped.set()
                    return ThinkingProbeAttempt(mode=mode, status="rejected", message=message)
                except (TimeoutError, APIConnectionError, httpx.HTTPError) as error:
                    message = (
                        "测试请求超时，已停止测试"
                        if isinstance(error, (TimeoutError, APITimeoutError)) else "连接提供商失败，已停止测试"
                    )
                    stopped.set()
                    return ThinkingProbeAttempt(mode=mode, status="inconclusive", message=message)
                reason = _compare_observations(enabled, disabled)
                if reason:
                    return ThinkingProbeAttempt(mode=mode, status="inconclusive", message=reason)
                return ThinkingProbeAttempt(mode=mode, status="matched", message="开启有思考、关闭无思考，已验证")

            # 每批全部完成才启动下一批；每个模式内部先开后关，最多同时两个上游请求。
            # gather 按候选顺序返回，避免响应速度影响自动选中的参数格式。
            for start in range(0, len(PROBE_MODES), PROBE_BATCH_SIZE):
                if await stop_requested():
                    break
                tasks = [
                    asyncio.create_task(probe_mode(mode))
                    for mode in PROBE_MODES[start:start + PROBE_BATCH_SIZE]
                ]
                try:
                    batch_attempts = await asyncio.gather(*tasks)
                finally:
                    # 意外异常或外层取消时先回收同批任务，再关闭共用的 HTTP 客户端。
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                for attempt in batch_attempts:
                    if attempt is not None:
                        attempts.append(attempt)
                        if attempt.status == "matched":
                            matches.append(attempt.mode)
    return ThinkingProbeResponse(
        status="matched" if matches else "inconclusive",
        matches=matches,
        attempts=attempts,
        message="已找到通过开关对照测试的参数模式" if matches else "未能可靠匹配思考参数模式，请查看各项测试结果",
    )
