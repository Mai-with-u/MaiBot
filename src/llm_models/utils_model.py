from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

import asyncio
import inspect
import random
import re
import time
import traceback

from rich.traceback import install

from src.common.logger import get_logger
from src.common.data_models.llm_service_data_models import (
    LLMAudioTranscriptionResult,
    LLMEmbeddingResult,
    LLMResponseResult,
)
from src.config.config import config_manager
from src.config.model_configs import APIProvider, ModelInfo, TaskConfig
from src.llm_models.exceptions import (
    EmptyResponseException,
    LLMTaskTimeoutError,
    ModelAttemptFailed,
    NetworkConnectionError,
    ReqAbortException,
    RespNotOkException,
    RespParseException,
)
from src.llm_models.model_client import ensure_configured_clients_loaded
from src.llm_models.model_client.base_client import (
    APIResponse,
    AudioTranscriptionRequest,
    BaseClient,
    ClientRequest,
    EmbeddingRequest,
    ResponseRequest,
    UsageRecord,
    client_registry,
)
from src.llm_models.request_snapshot import format_request_snapshot_log_info
from src.llm_models.payload_content.message import Message, MessageBuilder
from src.llm_models.payload_content.resp_format import RespFormat
from src.llm_models.payload_content.tool_option import (
    ToolCall,
    ToolDefinitionInput,
    ToolOption,
    normalize_tool_options,
)
from src.llm_models.utils import compress_messages, llm_usage_recorder

install(extra_lines=3)

logger = get_logger("model_utils")

DATA_URI_LIMIT_PATTERN = re.compile(
    r"Exceeded limit on max bytes per data-uri item\s*:\s*(?P<limit>\d+)",
    re.IGNORECASE,
)
DATA_URI_RETRY_MARGIN_BYTES = 128 * 1024
MIN_COMPRESSED_IMAGE_TARGET_SIZE_BYTES = 512 * 1024
EMPTY_TASK_FALLBACKS = {
    "expression_use": "utils",
    "learner": "utils",
    "mid_memory": "planner",
}


class RequestType(Enum):
    """请求类型枚举"""

    RESPONSE = "response"
    EMBEDDING = "embedding"
    AUDIO = "audio"


@dataclass(slots=True)
class LLMExecutionResult:
    """单次模型执行结果。"""

    api_response: APIResponse
    model_info: ModelInfo


class LLMOrchestrator:
    """LLM 编排调度器。"""

    def __init__(self, task_name: str, request_type: str = "", session_id: str = "") -> None:
        """初始化 LLM 请求调度器。

        Args:
            task_name: 任务配置名称，对应 `model_task_config` 下的字段名。
            request_type: 当前请求的业务类型标识。
            session_id: 当前请求归属的真实聊天流 ID；非聊天上下文为空。
        """
        self.task_name = task_name.strip()
        self.request_type = request_type
        self.session_id = str(session_id or "").strip()
        self.model_for_task = self._get_task_config_or_raise()
        self.model_usage: Dict[str, Tuple[int, int, int]] = {
            model: (0, 0, 0) for model in self.model_for_task.model_list
        }
        """模型使用量记录，用于进行负载均衡，对应为(total_tokens, penalty, usage_penalty)，惩罚值是为了能在某个模型请求不给力或正在被使用的时候进行调整"""

    def _resolve_effective_session_id(self, session_id: str = "") -> str:
        """解析本次请求用于统计归属的聊天流 ID。"""

        return str(session_id or self.session_id or "").strip()

    def _get_task_config_or_raise(self) -> TaskConfig:
        """获取当前任务名对应的最新任务配置。

        Returns:
            TaskConfig: 当前任务对应的最新任务配置对象。

        Raises:
            ValueError: 当任务名为空或对应配置不存在时抛出。
        """
        if not self.task_name:
            raise ValueError("任务配置名称不能为空")

        model_task_config = config_manager.get_model_config().model_task_config
        task_config = getattr(model_task_config, self.task_name, None)
        if not isinstance(task_config, TaskConfig):
            raise ValueError(f"未找到名为 '{self.task_name}' 的任务配置")
        if not any(str(model_name).strip() for model_name in task_config.model_list):
            fallback_task_name = EMPTY_TASK_FALLBACKS.get(self.task_name, "")
            if fallback_task_name:
                fallback_task_config = getattr(model_task_config, fallback_task_name, None)
                if isinstance(fallback_task_config, TaskConfig):
                    return fallback_task_config
        return task_config

    def _refresh_task_config(self) -> TaskConfig:
        """刷新并同步任务配置缓存。

        Returns:
            TaskConfig: 刷新后的任务配置对象。
        """
        latest = self._get_task_config_or_raise()
        if latest is not self.model_for_task:
            self.model_for_task = latest
        if list(self.model_usage.keys()) != latest.model_list:
            self.model_usage = {model: self.model_usage.get(model, (0, 0, 0)) for model in latest.model_list}
        return self.model_for_task

    def _check_slow_request(self, time_cost: float, model_name: str) -> None:
        """检查请求是否过慢并输出警告日志。

        Args:
            time_cost: 请求耗时（秒）。
            model_name: 使用的模型名称。
        """
        threshold = self.model_for_task.slow_threshold
        if time_cost > threshold:
            request_type_display = self.request_type or "未知任务"
            logger.warning(
                f"LLM请求耗时过长: {request_type_display} 使用模型 {model_name} 耗时 {time_cost:.1f}s（阈值: {threshold}s），请考虑使用更快的模型\n"
                f"  如果你认为该警告出现得过于频繁，请调整model_config.toml中对应任务的slow_threshold至符合你实际情况的合理值"
            )

    @staticmethod
    def _can_retry_with_compressed_images(
        active_request: ClientRequest,
        original_response_request: ResponseRequest | None,
    ) -> bool:
        """判断当前请求是否还可以通过压缩图片进行一次兜底重试。"""
        return (
            isinstance(active_request, ResponseRequest)
            and bool(active_request.message_list)
            and original_response_request is not None
            and active_request.message_list == original_response_request.message_list
        )

    @staticmethod
    def _extract_data_uri_limit_bytes(error: RespNotOkException) -> int | None:
        """从兼容 OpenAI 的错误文本中提取 data URI 单项大小限制。"""
        candidate_messages = [error.message, str(error)]
        if error.__cause__ is not None:
            candidate_messages.append(str(error.__cause__))

        for candidate_message in candidate_messages:
            if not candidate_message:
                continue

            match = DATA_URI_LIMIT_PATTERN.search(candidate_message)
            if match is None:
                continue

            try:
                return int(match.group("limit"))
            except (TypeError, ValueError):
                return None

        return None

    @staticmethod
    def _build_data_uri_retry_target_size(limit_bytes: int) -> int:
        """根据上游返回的 data URI 上限，计算压缩重试的安全目标值。"""
        return max(
            MIN_COMPRESSED_IMAGE_TARGET_SIZE_BYTES,
            limit_bytes - DATA_URI_RETRY_MARGIN_BYTES,
        )

    def _schedule_llm_retry_event(
        self,
        *,
        model_name: str,
        attempt: int,
        max_attempts: int,
        reason: str,
        retry_interval: float,
    ) -> None:
        """异步广播模型重试进度；非聊天上下文没有 session_id 时跳过。"""

        session_id = self._resolve_effective_session_id()
        if not session_id:
            return

        try:
            from src.maisaka.monitor.events import emit_llm_retry

            asyncio.get_running_loop().create_task(
                emit_llm_retry(
                    session_id=session_id,
                    task_name=self.task_name,
                    request_type=self.request_type,
                    model_name=model_name,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    reason=reason,
                    retry_interval=retry_interval,
                )
            )
        except RuntimeError:
            return

    def _schedule_llm_error_event(
        self,
        *,
        model_name: str,
        message: str,
    ) -> None:
        """异步广播模型最终失败；非聊天上下文没有 session_id 时跳过。"""

        session_id = self._resolve_effective_session_id()
        if not session_id:
            return

        try:
            from src.maisaka.monitor.events import emit_llm_error

            asyncio.get_running_loop().create_task(
                emit_llm_error(
                    session_id=session_id,
                    task_name=self.task_name,
                    request_type=self.request_type,
                    model_name=model_name,
                    message=message,
                )
            )
        except RuntimeError:
            return

    @staticmethod
    def _build_generation_result(
        content: str,
        reasoning_content: str,
        model_name: str,
        tool_calls: List[ToolCall] | None,
        usage: UsageRecord | None = None,
    ) -> LLMResponseResult:
        """构建统一的文本响应结果。

        Args:
            content: 模型返回的正文内容。
            reasoning_content: 模型返回的推理内容。
            model_name: 实际使用的模型名称。
            tool_calls: 模型返回的工具调用列表。

        Returns:
            LLMResponseResult: 统一文本响应结果对象。
        """
        return LLMResponseResult(
            response=content,
            reasoning=reasoning_content,
            model_name=model_name,
            tool_calls=tool_calls,
            prompt_tokens=usage.prompt_tokens if usage is not None else 0,
            completion_tokens=usage.completion_tokens if usage is not None else 0,
            total_tokens=usage.total_tokens if usage is not None else 0,
            prompt_cache_hit_tokens=usage.prompt_cache_hit_tokens if usage is not None else 0,
            prompt_cache_miss_tokens=usage.prompt_cache_miss_tokens if usage is not None else 0,
        )

    async def generate_response_for_image(
        self,
        prompt: str,
        image_base64: str,
        image_format: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        interrupt_flag: asyncio.Event | None = None,
        session_id: str = "",
    ) -> LLMResponseResult:
        """为图像生成响应。

        Args:
            prompt: 文本提示词。
            image_base64: 图像的 Base64 编码字符串。
            image_format: 图像格式，例如 `png`、`jpeg`。
            temperature: 显式指定的温度参数。
            max_tokens: 显式指定的最大输出 token 数。
            interrupt_flag: 外部中断标记；被设置时会尽快终止请求。

        Returns:
            LLMResponseResult: 统一文本响应结果对象。
        """
        self._refresh_task_config()
        start_time = time.time()

        def message_factory(client: BaseClient) -> List[Message]:
            message_builder = MessageBuilder()
            message_builder.add_text_content(prompt)
            message_builder.add_image_content(
                image_base64=image_base64, image_format=image_format, support_formats=client.get_support_image_formats()
            )
            return [message_builder.build()]

        execution_result = await self._execute_request(
            request_type=RequestType.RESPONSE,
            message_factory=message_factory,
            temperature=temperature,
            max_tokens=max_tokens,
            interrupt_flag=interrupt_flag,
        )
        response = execution_result.api_response
        model_info = execution_result.model_info
        content = response.content or ""
        reasoning_content = response.reasoning_content or ""
        tool_calls = response.tool_calls
        if not reasoning_content and content:
            content, extracted_reasoning = self._extract_reasoning(content)
            reasoning_content = extracted_reasoning
        time_cost = time.time() - start_time
        self._check_slow_request(time_cost, model_info.name)
        if usage := response.usage:
            llm_usage_recorder.record_usage_to_database(
                model_info=model_info,
                model_usage=usage,
                user_id="system",
                request_type=self.request_type,
                task_name=self.task_name,
                session_id=self._resolve_effective_session_id(session_id),
                time_cost=time_cost,
            )
        return self._build_generation_result(
            content,
            reasoning_content,
            model_info.name,
            tool_calls,
            response.usage,
        )

    async def generate_response_for_voice(self, voice_base64: str) -> LLMAudioTranscriptionResult:
        """为语音生成转录响应。

        Args:
            voice_base64: 语音的 Base64 编码字符串。

        Returns:
            LLMAudioTranscriptionResult: 语音转写结果对象。
        """
        self._refresh_task_config()
        execution_result = await self._execute_request(
            request_type=RequestType.AUDIO,
            audio_base64=voice_base64,
        )
        return LLMAudioTranscriptionResult(text=execution_result.api_response.content or None)

    async def generate_response_async(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model_name: Optional[str] = None,
        tools: List[ToolDefinitionInput] | None = None,
        response_format: RespFormat | None = None,
        raise_when_empty: bool = True,
        interrupt_flag: asyncio.Event | None = None,
        session_id: str = "",
    ) -> LLMResponseResult:
        """异步生成文本响应。

        Args:
            prompt: 提示词。
            temperature: 显式指定的温度参数。
            max_tokens: 显式指定的最大输出 token 数。
            tools: 原始工具定义列表。
            response_format: 响应格式约束。
            raise_when_empty: 保留字段，当前版本暂未单独使用。
            interrupt_flag: 外部中断标记；被设置时会尽快终止请求。

        Returns:
            LLMResponseResult: 统一文本响应结果对象。
        """
        del raise_when_empty
        self._refresh_task_config()
        start_time = time.time()

        def message_factory(client: BaseClient) -> List[Message]:
            message_builder = MessageBuilder()
            message_builder.add_text_content(prompt)
            return [message_builder.build()]

        tool_built = self._build_tool_options(tools)

        execution_result = await self._execute_request(
            request_type=RequestType.RESPONSE,
            message_factory=message_factory,
            temperature=temperature,
            max_tokens=max_tokens,
            model_name=model_name,
            tool_options=tool_built,
            response_format=response_format,
            interrupt_flag=interrupt_flag,
        )
        response = execution_result.api_response
        model_info = execution_result.model_info

        logger.debug(f"LLM请求总耗时: {time.time() - start_time}")
        logger.debug(f"LLM生成内容: {response}")

        content = response.content
        reasoning_content = response.reasoning_content or ""
        tool_calls = response.tool_calls
        if not reasoning_content and content:
            content, extracted_reasoning = self._extract_reasoning(content)
            reasoning_content = extracted_reasoning
        if usage := response.usage:
            llm_usage_recorder.record_usage_to_database(
                model_info=model_info,
                model_usage=usage,
                user_id="system",
                request_type=self.request_type,
                task_name=self.task_name,
                session_id=self._resolve_effective_session_id(session_id),
                time_cost=time.time() - start_time,
            )
        return self._build_generation_result(
            content or "",
            reasoning_content,
            model_info.name,
            tool_calls,
            response.usage,
        )

    async def generate_response_with_message_async(
        self,
        message_factory: Callable[..., List[Message] | Awaitable[List[Message]]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model_name: Optional[str] = None,
        tools: List[ToolDefinitionInput] | None = None,
        response_format: RespFormat | None = None,
        raise_when_empty: bool = True,
        interrupt_flag: asyncio.Event | None = None,
        session_id: str = "",
    ) -> LLMResponseResult:
        """基于外部消息工厂异步生成响应。

        Args:
            message_factory: 消息工厂，会根据客户端能力构建消息列表。
            temperature: 显式指定的温度参数。
            max_tokens: 显式指定的最大输出 token 数。
            tools: 原始工具定义列表。
            response_format: 响应格式约束。
            raise_when_empty: 保留字段，当前版本暂未单独使用。
            interrupt_flag: 外部中断标记；被设置时会尽快终止请求。

        Returns:
            LLMResponseResult: 统一文本响应结果对象。
        """
        del raise_when_empty
        self._refresh_task_config()
        start_time = time.time()

        tool_built = self._build_tool_options(tools)

        execution_result = await self._execute_request(
            request_type=RequestType.RESPONSE,
            message_factory=message_factory,
            temperature=temperature,
            max_tokens=max_tokens,
            model_name=model_name,
            tool_options=tool_built,
            response_format=response_format,
            interrupt_flag=interrupt_flag,
        )
        response = execution_result.api_response
        model_info = execution_result.model_info

        time_cost = time.time() - start_time
        logger.debug(f"LLM请求总耗时: {time_cost}")
        logger.debug(f"LLM生成内容: {response}")

        content = response.content
        reasoning_content = response.reasoning_content or ""
        tool_calls = response.tool_calls
        if not reasoning_content and content:
            content, extracted_reasoning = self._extract_reasoning(content)
            reasoning_content = extracted_reasoning
        self._check_slow_request(time_cost, model_info.name)
        if usage := response.usage:
            llm_usage_recorder.record_usage_to_database(
                model_info=model_info,
                model_usage=usage,
                user_id="system",
                request_type=self.request_type,
                task_name=self.task_name,
                session_id=self._resolve_effective_session_id(session_id),
                time_cost=time_cost,
            )
        return self._build_generation_result(
            content or "",
            reasoning_content,
            model_info.name,
            tool_calls,
            response.usage,
        )

    async def get_embedding(self, embedding_input: str, *, session_id: str = "") -> LLMEmbeddingResult:
        """获取嵌入向量。

        Args:
            embedding_input: 待编码的文本。

        Returns:
            LLMEmbeddingResult: 向量生成结果对象。
        """
        self._refresh_task_config()
        start_time = time.time()
        execution_result = await self._execute_request(
            request_type=RequestType.EMBEDDING,
            embedding_input=embedding_input,
        )
        response = execution_result.api_response
        model_info = execution_result.model_info
        embedding = response.embedding
        if usage := response.usage:
            llm_usage_recorder.record_usage_to_database(
                model_info=model_info,
                model_usage=usage,
                user_id="system",
                request_type=self.request_type,
                task_name=self.task_name,
                session_id=self._resolve_effective_session_id(session_id),
                time_cost=time.time() - start_time,
            )
        if not embedding:
            raise RuntimeError("获取embedding失败")
        return LLMEmbeddingResult(embedding=embedding, model_name=model_info.name)

    def _resolve_effective_temperature(
        self,
        model_info: ModelInfo,
        temperature: Optional[float],
    ) -> Optional[float]:
        """解析响应请求最终使用的温度参数。

        Args:
            model_info: 当前模型信息。
            temperature: 调用方显式传入的温度。

        Returns:
            Optional[float]: 最终生效的温度参数。
        """
        if temperature is not None:
            return temperature
        if model_info.temperature is not None:
            return model_info.temperature
        if "temperature" in model_info.extra_params:
            return model_info.extra_params["temperature"]
        return self.model_for_task.temperature

    def _resolve_effective_max_tokens(
        self,
        model_info: ModelInfo,
        max_tokens: Optional[int],
    ) -> Optional[int]:
        """解析响应请求最终使用的最大输出 token 数。

        Args:
            model_info: 当前模型信息。
            max_tokens: 调用方显式传入的最大 token 数。

        Returns:
            Optional[int]: 最终生效的最大 token 数。
        """
        if max_tokens is not None:
            return max_tokens
        if model_info.max_tokens is not None:
            return model_info.max_tokens
        if "max_tokens" in model_info.extra_params:
            return model_info.extra_params["max_tokens"]
        return self.model_for_task.max_tokens

    def _build_response_request(
        self,
        model_info: ModelInfo,
        message_list: List[Message],
        tool_options: List[ToolOption] | None,
        response_format: RespFormat | None,
        stream_response_handler: Optional[Callable[..., Any]],
        async_response_parser: Optional[Callable[..., Any]],
        interrupt_flag: asyncio.Event | None,
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> ResponseRequest:
        """构建统一响应请求对象。

        Args:
            model_info: 当前模型信息。
            message_list: 请求消息列表。
            tool_options: 工具定义列表。
            response_format: 输出格式定义。
            stream_response_handler: 流式响应处理函数。
            async_response_parser: 非流式响应解析函数。
            interrupt_flag: 外部中断标记。
            temperature: 调用方显式传入的温度。
            max_tokens: 调用方显式传入的最大 token 数。

        Returns:
            ResponseRequest: 统一响应请求对象。
        """
        return ResponseRequest(
            model_info=model_info,
            message_list=list(message_list),
            tool_options=None if tool_options is None else list(tool_options),
            max_tokens=self._resolve_effective_max_tokens(model_info, max_tokens),
            temperature=self._resolve_effective_temperature(model_info, temperature),
            response_format=response_format,
            stream_response_handler=stream_response_handler,
            async_response_parser=async_response_parser,
            interrupt_flag=interrupt_flag,
            extra_params=dict(model_info.extra_params),
        )

    @staticmethod
    def _build_embedding_request(
        model_info: ModelInfo,
        embedding_input: str,
    ) -> EmbeddingRequest:
        """构建统一嵌入请求对象。

        Args:
            model_info: 当前模型信息。
            embedding_input: 嵌入输入文本。

        Returns:
            EmbeddingRequest: 统一嵌入请求对象。
        """
        return EmbeddingRequest(
            model_info=model_info,
            embedding_input=embedding_input,
            extra_params=dict(model_info.extra_params),
        )

    @staticmethod
    def _build_audio_transcription_request(
        model_info: ModelInfo,
        audio_base64: str,
        max_tokens: Optional[int] = None,
    ) -> AudioTranscriptionRequest:
        """构建统一音频转录请求对象。

        Args:
            model_info: 当前模型信息。
            audio_base64: Base64 编码的音频数据。
            max_tokens: 调用方显式传入的最大 token 数。

        Returns:
            AudioTranscriptionRequest: 统一音频转录请求对象。
        """
        return AudioTranscriptionRequest(
            model_info=model_info,
            audio_base64=audio_base64,
            max_tokens=max_tokens,
            extra_params=dict(model_info.extra_params),
        )

    def _build_client_request(
        self,
        request_type: RequestType,
        model_info: ModelInfo,
        message_list: List[Message],
        tool_options: List[ToolOption] | None,
        response_format: RespFormat | None,
        stream_response_handler: Optional[Callable[..., Any]],
        async_response_parser: Optional[Callable[..., Any]],
        interrupt_flag: asyncio.Event | None,
        temperature: Optional[float],
        max_tokens: Optional[int],
        embedding_input: str | None,
        audio_base64: str | None,
    ) -> ClientRequest:
        """按请求类型构建统一客户端请求对象。

        Args:
            request_type: 请求类型。
            model_info: 当前模型信息。
            message_list: 请求消息列表。
            tool_options: 工具定义列表。
            response_format: 响应格式定义。
            stream_response_handler: 流式响应处理函数。
            async_response_parser: 非流式响应解析函数。
            interrupt_flag: 外部中断标记。
            temperature: 调用方显式传入的温度。
            max_tokens: 调用方显式传入的最大 token 数。
            embedding_input: 嵌入输入文本。
            audio_base64: Base64 编码的音频数据。

        Returns:
            ClientRequest: 对应请求类型的统一请求对象。

        Raises:
            ValueError: 请求类型未知或缺少必需字段时抛出。
        """
        if request_type == RequestType.RESPONSE:
            return self._build_response_request(
                model_info=model_info,
                message_list=message_list,
                tool_options=tool_options,
                response_format=response_format,
                stream_response_handler=stream_response_handler,
                async_response_parser=async_response_parser,
                interrupt_flag=interrupt_flag,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        if request_type == RequestType.EMBEDDING:
            if embedding_input is None:
                raise ValueError("嵌入输入不能为空")
            return self._build_embedding_request(model_info=model_info, embedding_input=embedding_input)
        if request_type == RequestType.AUDIO:
            if audio_base64 is None:
                raise ValueError("音频 Base64 不能为空")
            return self._build_audio_transcription_request(
                model_info=model_info,
                audio_base64=audio_base64,
                max_tokens=max_tokens,
            )
        raise ValueError(f"不支持的请求类型: {request_type}")

    def _select_model(
        self,
        exclude_models: Optional[Set[str]] = None,
        model_name: Optional[str] = None,
    ) -> Tuple[ModelInfo, APIProvider, BaseClient]:
        """根据策略选择一个可用模型。

        Args:
            exclude_models: 本次请求中需要排除的模型名称集合。

        Returns:
            Tuple[ModelInfo, APIProvider, BaseClient]: 选中的模型、提供商与客户端实例。
        """
        self._refresh_task_config()
        available_models = {
            model: scores
            for model, scores in self.model_usage.items()
            if not exclude_models or model not in exclude_models
        }
        requested_model_name = str(model_name or "").strip()
        if requested_model_name:
            if exclude_models and requested_model_name in exclude_models:
                raise RuntimeError(f"指定模型 '{requested_model_name}' 已在本次请求中尝试失败")
            TempMethodsLLMUtils.get_model_info_by_name(requested_model_name)
            if requested_model_name not in self.model_usage:
                self.model_usage[requested_model_name] = (0, 0, 0)
            available_models = {requested_model_name: self.model_usage.get(requested_model_name, (0, 0, 0))}

        if not available_models:
            raise RuntimeError("没有可用的模型可供选择。所有模型均已尝试失败。")

        ensure_configured_clients_loaded()

        strategy = self.model_for_task.selection_strategy.strip().lower()

        if requested_model_name:
            selected_model_name = requested_model_name
        elif strategy == "random":
            # 随机选择策略
            selected_model_name = random.choice(list(available_models.keys()))
        elif strategy == "sequential":
            # 顺序优先策略：按照配置顺序选择第一个尚未失败的模型。
            selected_model_name = next(
                model_name for model_name in self.model_for_task.model_list if model_name in available_models
            )
        elif strategy == "balance":
            # 负载均衡策略：根据总tokens和惩罚值选择
            selected_model_name = min(
                available_models,
                key=lambda k: available_models[k][0] + available_models[k][1] * 300 + available_models[k][2] * 1000,
            )
        else:
            # 默认使用负载均衡策略
            logger.warning(f"未知的选择策略 '{strategy}'，使用默认的负载均衡策略")
            selected_model_name = min(
                available_models,
                key=lambda k: available_models[k][0] + available_models[k][1] * 300 + available_models[k][2] * 1000,
            )

        model_info = TempMethodsLLMUtils.get_model_info_by_name(selected_model_name)
        api_provider = TempMethodsLLMUtils.get_provider_by_name(model_info.api_provider)
        client = client_registry.get_client_class_instance(api_provider)
        logger.debug(f"选择请求模型: {model_info.name} (策略: {strategy})")
        total_tokens, penalty, usage_penalty = self.model_usage[model_info.name]
        self.model_usage[model_info.name] = (total_tokens, penalty, usage_penalty + 1)
        return model_info, api_provider, client

    async def _attempt_request_on_model(
        self,
        api_provider: APIProvider,
        client: BaseClient,
        request: ClientRequest,
        retry_limit: Optional[int] = None,
    ) -> APIResponse:
        """在单个模型上执行请求，并处理重试逻辑。

        Args:
            api_provider: 当前请求对应的 API 提供商配置。
            client: 已初始化的客户端实例。
            request: 统一客户端请求对象。
            retry_limit: 显式指定的重试次数；未指定时使用 Provider 配置。

        Returns:
            APIResponse: 统一响应对象。

        Raises:
            ModelAttemptFailed: 当当前模型重试耗尽或遇到硬错误时抛出。
        """
        retry_remain = retry_limit if retry_limit is not None else api_provider.max_retry
        retry_remain = max(1, retry_remain)
        max_attempts = retry_remain
        model_info = request.model_info
        original_response_request = request if isinstance(request, ResponseRequest) else None
        active_request: ClientRequest = request

        while retry_remain > 0:
            try:
                if isinstance(active_request, ResponseRequest):
                    return await client.get_response(active_request)
                if isinstance(active_request, EmbeddingRequest):
                    return await client.get_embedding(active_request)
                return await client.get_audio_transcriptions(active_request)
            except EmptyResponseException as e:
                # 空回复：通常为临时问题，单独记录并重试
                original_error_info = self._get_original_error_info(e)
                retry_remain -= 1
                task_display = self.request_type or "未知任务"
                if retry_remain <= 0:
                    logger.error(
                        f"任务 '{task_display}' 的模型 '{model_info.name}' 在多次出现空回复后仍然失败。{original_error_info}"
                    )
                    raise ModelAttemptFailed(f"模型 '{model_info.name}' 重试耗尽", original_exception=e) from e

                logger.warning(
                    f"任务 '{task_display}' 的模型 '{model_info.name}' 返回空回复(可重试){original_error_info}。剩余重试次数: {retry_remain}"
                )
                self._schedule_llm_retry_event(
                    model_name=model_info.name,
                    attempt=max_attempts - retry_remain + 1,
                    max_attempts=max_attempts,
                    reason="模型返回空回复",
                    retry_interval=api_provider.retry_interval,
                )
                await asyncio.sleep(api_provider.retry_interval)

            except NetworkConnectionError as e:
                # 网络错误：单独记录并重试
                # 尝试从链式异常中获取原始错误信息以诊断具体原因
                original_error_info = self._get_original_error_info(e)

                retry_remain -= 1
                task_display = self.request_type or "未知任务"
                if retry_remain <= 0:
                    logger.error(
                        f"任务 '{task_display}' 的模型 '{model_info.name}' 在网络错误重试用尽后仍然失败。{original_error_info}"
                    )
                    raise ModelAttemptFailed(f"模型 '{model_info.name}' 重试耗尽", original_exception=e) from e

                logger.warning(
                    f"任务 '{task_display}' 的模型 '{model_info.name}' 遇到网络错误(可重试): {str(e)}{original_error_info}\n"
                    f"  常见原因: 如请求的API正常但APITimeoutError类型错误过多，请尝试调整模型配置中对应API Provider的timeout值\n"
                    f"  其它可能原因: 网络波动、DNS 故障、连接超时、防火墙限制或代理问题\n"
                    f"  剩余重试次数: {retry_remain}"
                )
                self._schedule_llm_retry_event(
                    model_name=model_info.name,
                    attempt=max_attempts - retry_remain + 1,
                    max_attempts=max_attempts,
                    reason="网络错误",
                    retry_interval=api_provider.retry_interval,
                )
                await asyncio.sleep(api_provider.retry_interval)

            except RespNotOkException as e:
                original_error_info = self._get_original_error_info(e)
                task_display = self.request_type or "未知任务"

                # 可重试的HTTP错误
                can_retry_with_compression = self._can_retry_with_compressed_images(
                    active_request,
                    original_response_request,
                )

                if e.status_code == 429 or e.status_code >= 500:
                    retry_remain -= 1
                    if retry_remain <= 0:
                        logger.error(
                            f"任务 '{task_display}' 的模型 '{model_info.name}' 在遇到 {e.status_code} 错误并用尽重试次数后仍然失败。{original_error_info}"
                        )
                        raise ModelAttemptFailed(f"模型 '{model_info.name}' 重试耗尽", original_exception=e) from e

                    logger.warning(
                        f"任务 '{task_display}' 的模型 '{model_info.name}' 遇到可重试的HTTP错误: {str(e)}{original_error_info}。剩余重试次数: {retry_remain}"
                    )
                    self._schedule_llm_retry_event(
                        model_name=model_info.name,
                        attempt=max_attempts - retry_remain + 1,
                        max_attempts=max_attempts,
                        reason=f"HTTP {e.status_code}",
                        retry_interval=api_provider.retry_interval,
                    )
                    await asyncio.sleep(api_provider.retry_interval)
                    continue

                # 特殊处理413，尝试压缩
                data_uri_limit_bytes = self._extract_data_uri_limit_bytes(e)
                if data_uri_limit_bytes is not None and can_retry_with_compression:
                    target_size = self._build_data_uri_retry_target_size(data_uri_limit_bytes)
                    logger.warning(
                        f"任务 '{task_display}' 的模型 '{model_info.name}' 返回 data URI 图片过大错误，"
                        f"检测到单项上限 {data_uri_limit_bytes} 字节，尝试压缩图片后重试..."
                    )
                    compressed_messages = compress_messages(
                        active_request.message_list,
                        img_target_size=target_size,
                    )
                    active_request = active_request.copy_with(message_list=compressed_messages)
                    continue

                if e.status_code == 413 and can_retry_with_compression:
                    logger.warning(
                        f"任务 '{task_display}' 的模型 '{model_info.name}' 返回413请求体过大，尝试压缩后重试..."
                    )
                    # 压缩消息本身不消耗重试次数
                    compressed_messages = compress_messages(active_request.message_list)
                    active_request = active_request.copy_with(message_list=compressed_messages)
                    continue

                # 不可重试的HTTP错误
                logger.warning(
                    f"任务 '{task_display}' 的模型 '{model_info.name}' 遇到不可重试的HTTP错误: {str(e)}{original_error_info}"
                )
                raise ModelAttemptFailed(f"模型 '{model_info.name}' 遇到硬错误", original_exception=e) from e

            except RespParseException as e:
                original_error_info = self._get_original_error_info(e)
                retry_remain -= 1
                task_display = self.request_type or "未知任务"
                if retry_remain <= 0:
                    logger.error(
                        f"任务 '{task_display}' 的模型 '{model_info.name}' 在响应解析多次失败后仍然失败。{original_error_info}"
                    )
                    raise ModelAttemptFailed(f"模型 '{model_info.name}' 重试耗尽", original_exception=e) from e

                logger.warning(
                    f"任务 '{task_display}' 的模型 '{model_info.name}' 返回内容解析失败(可重试): {str(e)}{original_error_info}。"
                    f"剩余重试次数: {retry_remain}"
                )
                self._schedule_llm_retry_event(
                    model_name=model_info.name,
                    attempt=max_attempts - retry_remain + 1,
                    max_attempts=max_attempts,
                    reason="响应解析失败",
                    retry_interval=api_provider.retry_interval,
                )
                await asyncio.sleep(api_provider.retry_interval)

            except ReqAbortException:
                raise

            except Exception as e:
                logger.error(traceback.format_exc())

                original_error_info = self._get_original_error_info(e)
                task_display = self.request_type or "未知任务"

                logger.warning(
                    f"任务 '{task_display}' 的模型 '{model_info.name}' 遇到未知的不可重试错误: {str(e)}{original_error_info}"
                )
                raise ModelAttemptFailed(f"模型 '{model_info.name}' 遇到硬错误", original_exception=e) from e

        raise ModelAttemptFailed(
            f"任务 '{self.request_type or '未知任务'}' 的模型 '{model_info.name}' 未被尝试，因为重试次数已配置为0或更少。"
        )

    async def _attempt_request_on_model_with_timeout(
        self,
        api_provider: APIProvider,
        client: BaseClient,
        request: ClientRequest,
        model_name: str,
    ) -> APIResponse:
        """对 `_attempt_request_on_model` 套一层任务级 hard_timeout。

        单次模型尝试超时时把 TimeoutError 转成 LLMTaskTimeoutError（继承 ModelAttemptFailed），
        由 `_execute_request` 内既有的 `except ModelAttemptFailed` 分支接住，按"切下一个模型"
        的既有语义处理（penalty +1、usage_penalty -1、failed_models_this_request 登记）。
        全部模型都触发 hard_timeout 时，最后一个 LLMTaskTimeoutError 上抛给调用方。
        """
        timeout_s = self.model_for_task.hard_timeout
        try:
            return await asyncio.wait_for(
                self._attempt_request_on_model(api_provider, client, request=request),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError as e:
            task_display = self.request_type or self.task_name or "未知任务"
            raise LLMTaskTimeoutError(
                task_name=task_display,
                model_name=model_name,
                timeout_s=timeout_s,
            ) from e

    async def _execute_request(
        self,
        request_type: RequestType,
        message_factory: Optional[Callable[..., List[Message] | Awaitable[List[Message]]]] = None,
        tool_options: List[ToolOption] | None = None,
        response_format: RespFormat | None = None,
        stream_response_handler: Optional[Callable[..., Any]] = None,
        async_response_parser: Optional[Callable[..., Any]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model_name: Optional[str] = None,
        embedding_input: str | None = None,
        audio_base64: str | None = None,
        interrupt_flag: asyncio.Event | None = None,
    ) -> LLMExecutionResult:
        """执行一次完整的模型调度请求。

        Args:
            request_type: 请求类型。
            message_factory: 消息工厂，仅在响应请求中使用。
            tool_options: 工具定义列表。
            response_format: 响应格式定义。
            stream_response_handler: 流式响应处理函数。
            async_response_parser: 非流式响应解析函数。
            temperature: 显式指定的温度参数。
            max_tokens: 显式指定的最大输出 token 数。
            embedding_input: 嵌入输入文本。
            audio_base64: Base64 编码的音频数据。
            interrupt_flag: 外部中断标记。

        Returns:
            LLMExecutionResult: 单次模型执行结果对象。
        """
        failed_models_this_request: Set[str] = set()
        max_attempts = 1 if str(model_name or "").strip() else len(self.model_for_task.model_list)
        last_exception: Optional[Exception] = None
        last_model_name = ""

        for _ in range(max_attempts):
            model_info, api_provider, client = self._select_model(
                exclude_models=failed_models_this_request,
                model_name=model_name,
            )
            last_model_name = model_info.name
            message_list = []
            if message_factory:
                parameter_count = len(inspect.signature(message_factory).parameters)
                if parameter_count >= 2:
                    message_result = message_factory(client, model_info)
                else:
                    message_result = message_factory(client)
                if inspect.isawaitable(message_result):
                    message_list = await message_result
                else:
                    message_list = message_result
            try:
                request = self._build_client_request(
                    request_type=request_type,
                    model_info=model_info,
                    message_list=message_list,
                    tool_options=tool_options,
                    response_format=response_format,
                    stream_response_handler=stream_response_handler,
                    async_response_parser=async_response_parser,
                    interrupt_flag=interrupt_flag,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    embedding_input=embedding_input,
                    audio_base64=audio_base64,
                )
                if self.request_type.startswith("maisaka."):
                    logger.debug(
                        f"LLMOrchestrator[{self.request_type}] 正在向模型 model={model_info.name} 发送请求 "
                        f"(tool_options={len(tool_options or [])})"
                    )
                response = await self._attempt_request_on_model_with_timeout(
                    api_provider,
                    client,
                    request,
                    model_info.name,
                )
                if self.request_type.startswith("maisaka."):
                    logger.debug(f"LLMOrchestrator[{self.request_type}] 模型 model={model_info.name} 已返回 API 响应")
                total_tokens, penalty, usage_penalty = self.model_usage[model_info.name]
                if response_usage := response.usage:
                    total_tokens += response_usage.total_tokens
                self.model_usage[model_info.name] = (total_tokens, penalty, usage_penalty - 1)
                return LLMExecutionResult(api_response=response, model_info=model_info)

            except ReqAbortException as e:
                total_tokens, penalty, usage_penalty = self.model_usage[model_info.name]
                self.model_usage[model_info.name] = (total_tokens, penalty, usage_penalty - 1)
                if self.request_type.startswith("maisaka."):
                    logger.debug(
                        f"LLMOrchestrator[{self.request_type}] 模型 model={model_info.name} 的请求已被外部信号中断"
                    )
                raise e

            except ModelAttemptFailed as e:
                last_exception = e.original_exception or e
                logger.warning(f"模型 '{model_info.name}' 尝试失败，切换到下一个模型。原因: {e}")
                total_tokens, penalty, usage_penalty = self.model_usage[model_info.name]
                self.model_usage[model_info.name] = (total_tokens, penalty + 1, usage_penalty - 1)
                failed_models_this_request.add(model_info.name)

                if isinstance(last_exception, RespNotOkException) and last_exception.status_code == 400:
                    logger.warning("收到客户端错误 (400)，跳过当前模型并继续尝试其他模型。")
                    continue

        logger.error(f"所有 {max_attempts} 个模型均尝试失败。")
        if last_exception:
            self._schedule_llm_error_event(
                model_name=last_model_name,
                message=str(last_exception),
            )
            raise last_exception
        self._schedule_llm_error_event(
            model_name=last_model_name,
            message="请求失败，所有可用模型均已尝试失败。",
        )
        raise RuntimeError("请求失败，所有可用模型均已尝试失败。")

    def _build_tool_options(self, tools: List[ToolDefinitionInput] | None) -> List[ToolOption] | None:
        """将任意输入工具定义列表规范化为内部工具选项。

        Args:
            tools: 原始工具定义列表。

        Returns:
            List[ToolOption] | None: 规范化后的工具选项列表。
        """
        return normalize_tool_options(tools)

    @staticmethod
    def _extract_reasoning(content: str) -> Tuple[str, str]:
        """提取 `<think>` 思维链内容。

        Args:
            content: 原始模型输出文本。

        Returns:
            Tuple[str, str]: `(正文内容, 推理内容)`。
        """
        match = re.search(r"(?:<think>)?(.*?)</think>", content, re.DOTALL)
        content = re.sub(r"(?:<think>)?.*?</think>", "", content, flags=re.DOTALL, count=1).strip()
        reasoning = match[1].strip() if match else ""
        return content, reasoning

    @staticmethod
    def _get_original_error_info(e: Exception) -> str:
        """提取底层异常信息。

        Args:
            e: 当前捕获的异常对象。

        Returns:
            str: 可直接拼接到日志中的底层异常描述。
        """
        detail_lines: List[str] = []
        if e.__cause__:
            detail_lines.append(f"底层异常: {type(e.__cause__).__name__} | {e.__cause__}")

        snapshot_info = format_request_snapshot_log_info(e)
        if detail_lines or snapshot_info:
            detail_text = "\n  " + "\n  ".join(detail_lines) if detail_lines else ""
            return f"{detail_text}{snapshot_info}"

        return ""


class TempMethodsLLMUtils:
    @staticmethod
    def get_model_info_by_name(model_name: str) -> ModelInfo:
        """根据模型名称获取模型信息。

        Args:
            model_name: 模型名称

        Returns:
            ModelInfo: 模型信息。

        Raises:
            ValueError: 未找到指定模型。
        """
        for model in config_manager.get_model_config().models:
            if model.name == model_name:
                return model
        raise ValueError(f"未找到名为 '{model_name}' 的模型")

    @staticmethod
    def get_provider_by_name(provider_name: str) -> APIProvider:
        """根据提供商名称获取提供商信息。

        Args:
            provider_name: 提供商名称

        Returns:
            APIProvider: API 提供商信息。

        Raises:
            ValueError: 未找到指定提供商。
        """
        for provider in config_manager.get_model_config().api_providers:
            if provider.name == provider_name:
                return provider
        raise ValueError(f"未找到名为 '{provider_name}' 的API提供商")


class LLMRequest(LLMOrchestrator):
    """兼容旧调用方的 LLM 请求入口。

    新代码应优先使用 ``LLMOrchestrator`` 或服务层 ``LLMServiceClient``；
    该类保留旧版 ``model_set=TaskConfig`` 的构造方式，并在运行时解析
    对应的最新任务配置名称。
    """

    def __init__(self, model_set: TaskConfig, request_type: str = "") -> None:
        """初始化旧版 LLM 请求对象。

        Args:
            model_set: 旧调用方传入的任务配置对象。
            request_type: 当前请求的业务类型标识。
        """
        self._task_config_name = self._resolve_task_config_name(model_set)
        super().__init__(task_name=self._task_config_name, request_type=request_type)

    @staticmethod
    def _build_task_config_signature(task_config: TaskConfig) -> Tuple[Any, ...]:
        """构造任务配置签名。

        Args:
            task_config: 任务配置对象。

        Returns:
            Tuple[Any, ...]: 可用于匹配热重载前后任务配置的签名。
        """
        return (
            tuple(task_config.model_list),
            task_config.max_tokens,
            task_config.temperature,
            task_config.slow_threshold,
            task_config.selection_strategy,
            task_config.hard_timeout,
        )

    @classmethod
    def _resolve_task_config_name(cls, model_set: TaskConfig) -> str:
        """根据旧版 TaskConfig 对象解析任务配置名称。

        Args:
            model_set: 旧调用方传入的任务配置对象。

        Returns:
            str: 对应 ``model_task_config`` 下的字段名。

        Raises:
            ValueError: 未能找到匹配任务配置时抛出。
        """
        target_signature = cls._build_task_config_signature(model_set)
        model_task_config = config_manager.get_model_config().model_task_config
        for attr_name in dir(model_task_config):
            if attr_name.startswith("_"):
                continue
            attr_value = getattr(model_task_config, attr_name)
            if not isinstance(attr_value, TaskConfig):
                continue
            if attr_value is model_set or cls._build_task_config_signature(attr_value) == target_signature:
                return attr_name
        raise ValueError("无法根据旧版 model_set 解析任务配置名称")
