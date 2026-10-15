from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine, Generic, Tuple, TypeVar, cast

import asyncio

from src.common.logger import get_logger
from src.config.model_configs import ModelInfo
from src.llm_models.exceptions import RespNotOkException
from src.llm_models.payload_content.context_protocol import ContextProtocolMode, validate_context_items

from .base_client import (
    APIResponse,
    AudioTranscriptionRequest,
    BaseClient,
    EmbeddingRequest,
    ImageEmbeddingRequest,
    ResponseRequest,
    UsageRecord,
    UsageTuple,
)
from .embedding_rate_limit import get_embedding_rate_limit

RawStreamT = TypeVar("RawStreamT")
"""流式原始响应类型变量。"""

RawResponseT = TypeVar("RawResponseT")
"""非流式原始响应类型变量。"""

TaskResultT = TypeVar("TaskResultT")
"""异步任务返回值类型变量。"""

ProviderStreamResponseHandler = Callable[
    [RawStreamT, asyncio.Event | None],
    Coroutine[Any, Any, Tuple[APIResponse, UsageTuple | None]],
]
"""Provider 专用流式响应处理函数类型。"""

ProviderResponseParser = Callable[[RawResponseT], Tuple[APIResponse, UsageTuple | None]]
"""Provider 专用非流式响应解析函数类型。"""

logger = get_logger("llm_adapter_base")


async def await_task_with_interrupt(
    task: asyncio.Task[TaskResultT],
    interrupt_flag: asyncio.Event | None,
) -> TaskResultT:
    """在支持外部中断的前提下等待异步任务完成。

    Args:
        task: 待等待的异步任务。
        interrupt_flag: 外部中断标记。

    Returns:
        TaskResultT: 任务执行结果。

    Raises:
        ReqAbortException: 等待期间收到外部中断信号时抛出。
    """
    from src.llm_models.exceptions import ReqAbortException

    started_at = asyncio.get_running_loop().time()
    interrupt_task: asyncio.Task[bool] | None = None
    interrupted = False
    try:
        if interrupt_flag is not None:
            if not interrupt_flag.is_set():
                interrupt_task = asyncio.create_task(interrupt_flag.wait())
                await asyncio.wait({task, interrupt_task}, return_when=asyncio.FIRST_COMPLETED)
            # 同时完成时优先中断，避免新消息已到达却仍使用旧请求的结果。
            if interrupt_flag.is_set():
                interrupted = True
                elapsed = asyncio.get_running_loop().time() - started_at
                logger.info(f"LLM 请求检测到中断信号，准备取消底层任务，elapsed={elapsed:.3f}s")
                task.cancel()
                # 流式处理器在清理缓冲区前携带片段抛出中断异常，逐层保留该异常。
                try:
                    await task
                except ReqAbortException:
                    raise
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.debug("中断请求清理失败", exc_info=True)
                raise ReqAbortException("请求被外部信号中断")
        return await task
    finally:
        if interrupt_task is not None:
            interrupt_task.cancel()
            try:
                await interrupt_task
            except asyncio.CancelledError:
                pass
        # 调用方协程被 CancelledError（如 asyncio.wait_for 命中 hard_timeout）打断时，
        # 必须把 child task 也取消，否则上游 httpx 请求会继续在后台运行，
        # 占用连接 / token 并使 hard_timeout 形同虚设。
        # cancel 后再 await 让子任务真正完成清理。
        if interrupted or not task.done():
            # 嵌套请求已经在清理时，不重复取消，避免打断响应流的关闭过程。
            if not task.cancelling():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                # 子任务清理过程中的异常（httpx 内部、SDK 等）已不影响主流程的取消语义，
                # 但仍以 debug 级别记录，避免编程错误被完全静默。
                logger.debug("await_task_with_interrupt: 取消子任务后清理时抛出异常", exc_info=True)


class AdapterClient(BaseClient, ABC, Generic[RawStreamT, RawResponseT]):
    """提供统一请求执行骨架的 Provider 适配基类。"""

    async def get_response(self, request: ResponseRequest) -> APIResponse:
        """获取对话响应。

        Args:
            request: 统一响应请求对象。

        Returns:
            APIResponse: 解析完成的统一响应对象。
        """
        validate_context_items(request.context_items, ContextProtocolMode.REQUEST_CONTEXT)
        stream_response_handler = self._resolve_stream_response_handler(request)
        response_parser = self._resolve_response_parser(request)
        response, usage_record = await self._execute_response_request(
            request,
            stream_response_handler,
            response_parser,
        )
        validate_context_items(response.output_items, ContextProtocolMode.MODEL_OUTPUT)
        response.bind_logical_turn(request.logical_turn_id)
        response = self._attach_usage_record(response, request.model_info, usage_record)
        response.attach_generation_trace(
            provider=self.api_provider.name,
            endpoint=self.api_provider.base_url,
            model=request.model_info.model_identifier,
        )
        return response

    async def get_embedding(self, request: EmbeddingRequest) -> APIResponse:
        """获取文本嵌入。

        Args:
            request: 统一嵌入请求对象。

        Returns:
            APIResponse: 解析完成的统一嵌入响应。
        """
        limiter = get_embedding_rate_limit(
            self.api_provider.name, self.api_provider.base_url, request.model_info.model_identifier,
        )
        wait_seconds = 0.0
        max_attempts = 1 if request.retry_handled_externally else 3
        for attempt in range(max_attempts):
            generation, waited = await limiter.wait()
            wait_seconds += waited
            if request.trace_context is not None:
                request.trace_context.rate_limit_wait_seconds += waited
            try:
                response, usage_record = await self._execute_embedding_request(request)
                break
            except RespNotOkException as exc:
                if exc.status_code == 429:
                    limiter.reduce(generation)
                if attempt + 1 == max_attempts or exc.status_code not in (408, 429, 500, 502, 503, 504):
                    raise
                # 直接调用才在客户端重试；经 Orchestrator 的请求由外层负责。
                # 重试等待不计入模型请求耗时。
                delay = float(2 ** attempt)
                await asyncio.sleep(delay)
                wait_seconds += delay
                if request.trace_context is not None:
                    request.trace_context.rate_limit_wait_seconds += delay
        response.rate_limit_wait_seconds = (
            request.trace_context.rate_limit_wait_seconds if request.trace_context is not None else wait_seconds
        )
        return self._attach_usage_record(response, request.model_info, usage_record)

    async def get_image_embedding(self, request: ImageEmbeddingRequest) -> APIResponse:
        """获取图片嵌入，调用 Provider 显式实现的图片协议。"""

        response, usage_record = await self._execute_image_embedding_request(request)
        return self._attach_usage_record(response, request.model_info, usage_record)

    async def get_audio_transcriptions(self, request: AudioTranscriptionRequest) -> APIResponse:
        """获取音频转录。

        Args:
            request: 统一音频转录请求对象。

        Returns:
            APIResponse: 解析完成的统一音频转录响应。
        """
        response, usage_record = await self._execute_audio_transcription_request(request)
        return self._attach_usage_record(response, request.model_info, usage_record)

    def _resolve_stream_response_handler(
        self,
        request: ResponseRequest,
    ) -> ProviderStreamResponseHandler[RawStreamT]:
        """解析实际使用的流式响应处理器。

        Args:
            request: 统一响应请求对象。

        Returns:
            ProviderStreamResponseHandler[RawStreamT]: 流式响应处理器。
        """
        if request.stream_response_handler is not None:
            return cast(ProviderStreamResponseHandler[RawStreamT], request.stream_response_handler)
        return self._build_default_stream_response_handler(request)

    def _resolve_response_parser(
        self,
        request: ResponseRequest,
    ) -> ProviderResponseParser[RawResponseT]:
        """解析实际使用的非流式响应解析器。

        Args:
            request: 统一响应请求对象。

        Returns:
            ProviderResponseParser[RawResponseT]: 非流式响应解析器。
        """
        if request.async_response_parser is not None:
            return cast(ProviderResponseParser[RawResponseT], request.async_response_parser)
        return self._build_default_response_parser(request)

    @staticmethod
    def _build_usage_record(model_info: ModelInfo, usage_record: UsageTuple) -> UsageRecord:
        """根据统一使用量三元组构建 `UsageRecord`。

        Args:
            model_info: 模型信息。
            usage_record: 使用量三元组。

        Returns:
            UsageRecord: 可直接挂载到 `APIResponse` 的使用记录对象。
        """
        return UsageRecord(
            model_name=model_info.name,
            provider_name=model_info.api_provider,
            prompt_tokens=usage_record[0],
            completion_tokens=usage_record[1],
            total_tokens=usage_record[2],
            prompt_cache_hit_tokens=usage_record[3] if len(usage_record) > 3 else 0,
            prompt_cache_miss_tokens=usage_record[4] if len(usage_record) > 4 else 0,
            prompt_cache_reported=bool(usage_record[5]) if len(usage_record) > 5 else False,
        )

    def _attach_usage_record(
        self,
        response: APIResponse,
        model_info: ModelInfo,
        usage_record: UsageTuple | None,
    ) -> APIResponse:
        """在响应对象上附加统一使用量信息。

        Args:
            response: 已解析的统一响应对象。
            model_info: 模型信息。
            usage_record: 可选的使用量三元组。

        Returns:
            APIResponse: 附加使用量后的响应对象。
        """
        if usage_record is not None:
            response.usage = self._build_usage_record(model_info, usage_record)
        return response

    @abstractmethod
    def _build_default_stream_response_handler(
        self,
        request: ResponseRequest,
    ) -> ProviderStreamResponseHandler[RawStreamT]:
        """构建默认流式响应处理器。

        Args:
            request: 统一响应请求对象。

        Returns:
            ProviderStreamResponseHandler[RawStreamT]: 默认流式处理器。
        """
        raise NotImplementedError

    @abstractmethod
    def _build_default_response_parser(
        self,
        request: ResponseRequest,
    ) -> ProviderResponseParser[RawResponseT]:
        """构建默认非流式响应解析器。

        Args:
            request: 统一响应请求对象。

        Returns:
            ProviderResponseParser[RawResponseT]: 默认非流式解析器。
        """
        raise NotImplementedError

    @abstractmethod
    async def _execute_response_request(
        self,
        request: ResponseRequest,
        stream_response_handler: ProviderStreamResponseHandler[RawStreamT],
        response_parser: ProviderResponseParser[RawResponseT],
    ) -> Tuple[APIResponse, UsageTuple | None]:
        """执行 Provider 的文本/多模态响应请求。

        Args:
            request: 统一响应请求对象。
            stream_response_handler: 流式响应处理器。
            response_parser: 非流式响应解析器。

        Returns:
            Tuple[APIResponse, UsageTuple | None]: 统一响应对象与可选使用量信息。
        """
        raise NotImplementedError

    @abstractmethod
    async def _execute_embedding_request(
        self,
        request: EmbeddingRequest,
    ) -> Tuple[APIResponse, UsageTuple | None]:
        """执行 Provider 的嵌入请求。

        Args:
            request: 统一嵌入请求对象。

        Returns:
            Tuple[APIResponse, UsageTuple | None]: 统一响应对象与可选使用量信息。
        """
        raise NotImplementedError

    async def _execute_image_embedding_request(
        self,
        request: ImageEmbeddingRequest,
    ) -> Tuple[APIResponse, UsageTuple | None]:
        """执行图片嵌入请求；默认实现直接说明协议不受支持。"""

        from src.llm_models.exceptions import ImageEmbeddingUnsupportedError

        raise ImageEmbeddingUnsupportedError(
            f"Provider 客户端 {type(self).__name__} 未实现图片嵌入协议"
        )

    @abstractmethod
    async def _execute_audio_transcription_request(
        self,
        request: AudioTranscriptionRequest,
    ) -> Tuple[APIResponse, UsageTuple | None]:
        """执行 Provider 的音频转录请求。

        Args:
            request: 统一音频转录请求对象。

        Returns:
            Tuple[APIResponse, UsageTuple | None]: 统一响应对象与可选使用量信息。
        """
        raise NotImplementedError
