from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine, Generic, Tuple, TypeVar, cast

import asyncio

from src.common.logger import get_logger
from src.config.model_configs import ModelInfo

from .base_client import (
    APIResponse,
    AudioTranscriptionRequest,
    BaseClient,
    EmbeddingRequest,
    ResponseRequest,
    UsageRecord,
    UsageTuple,
)

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
    *,
    interval_seconds: float = 0.02,
) -> TaskResultT:
    """在支持外部中断的前提下等待异步任务完成。

    Args:
        task: 待等待的异步任务。
        interrupt_flag: 外部中断标记。
        interval_seconds: 轮询检查间隔，单位秒。

    Returns:
        TaskResultT: 任务执行结果。

    Raises:
        ReqAbortException: 等待期间收到外部中断信号时抛出。
    """
    from src.llm_models.exceptions import ReqAbortException

    started_at = asyncio.get_running_loop().time()
    try:
        while not task.done():
            if interrupt_flag and interrupt_flag.is_set():
                elapsed = asyncio.get_running_loop().time() - started_at
                logger.info(f"LLM 请求检测到中断信号，准备取消底层任务，elapsed={elapsed:.3f}s")
                task.cancel()
                raise ReqAbortException("请求被外部信号中断")
            await asyncio.sleep(interval_seconds)
        return await task
    finally:
        # 调用方协程被 CancelledError（如 asyncio.wait_for 命中 hard_timeout）打断时，
        # 必须把 child task 也取消，否则上游 httpx 请求会继续在后台运行，
        # 占用连接 / token 并使 hard_timeout 形同虚设。
        # cancel 后再 await 让子任务真正完成清理。
        if not task.done():
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
        stream_response_handler = self._resolve_stream_response_handler(request)
        response_parser = self._resolve_response_parser(request)
        response, usage_record = await self._execute_response_request(
            request,
            stream_response_handler,
            response_parser,
        )
        return self._attach_usage_record(response, request.model_info, usage_record)

    async def get_embedding(self, request: EmbeddingRequest) -> APIResponse:
        """获取文本嵌入。

        Args:
            request: 统一嵌入请求对象。

        Returns:
            APIResponse: 解析完成的统一嵌入响应。
        """
        response, usage_record = await self._execute_embedding_request(request)
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
