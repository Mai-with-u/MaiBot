"""Embedding 服务层。

该模块负责在宿主侧收口统一的文本嵌入请求，并将其转发到
`src.llm_models` 中的底层嵌入调度器。
"""

from __future__ import annotations

from typing import Any, Coroutine, List, TypeVar

import asyncio

from src.common.data_models.embedding_service_data_models import EmbeddingResult
from src.common.logger import get_logger
from src.llm_models.utils_model import LLMOrchestrator
from src.services.service_task_resolver import resolve_task_name

logger = get_logger("embedding_service")

_CoroutineReturnT = TypeVar("_CoroutineReturnT")


class EmbeddingServiceClient:
    """面向上层模块的 Embedding 服务对象式门面。"""

    def __init__(self, task_name: str = "embedding", request_type: str = "", session_id: str = "") -> None:
        """初始化 Embedding 服务门面。

        Args:
            task_name: 任务配置名称，对应 `model_task_config` 下的字段名。
            request_type: 当前请求的业务类型标识。
            session_id: 当前请求归属的真实聊天流 ID；非聊天上下文为空。
        """
        self.task_name = resolve_task_name(task_name)
        self.request_type = request_type
        self.session_id = str(session_id or "").strip()
        self._orchestrator = LLMOrchestrator(
            task_name=self.task_name,
            request_type=request_type,
            session_id=self.session_id,
        )

    def _resolve_effective_session_id(self, session_id: str = "") -> str:
        """解析本次请求用于统计归属的聊天流 ID。"""

        return str(session_id or self.session_id or "").strip()

    async def embed_text(self, embedding_input: str, *, session_id: str = "") -> EmbeddingResult:
        """生成单条文本的嵌入向量。

        Args:
            embedding_input: 待编码的文本内容。

        Returns:
            EmbeddingResult: 统一嵌入结果对象。
        """
        raw_result = await self._orchestrator.get_embedding(
            embedding_input,
            session_id=self._resolve_effective_session_id(session_id),
        )
        return EmbeddingResult(
            embedding=list(raw_result.embedding),
            model_name=raw_result.model_name,
            model_identifier=raw_result.model_identifier,
            api_provider=raw_result.api_provider,
        )

    async def embed_texts(
        self,
        embedding_inputs: List[str],
        max_concurrent: int | None = None,
        *,
        session_id: str = "",
    ) -> List[EmbeddingResult]:
        """批量生成文本嵌入向量。

        Args:
            embedding_inputs: 待编码的文本列表。
            max_concurrent: 最大并发数；未提供时按串行执行。

        Returns:
            List[EmbeddingResult]: 与输入顺序一致的嵌入结果列表。
        """
        if not embedding_inputs:
            return []

        safe_max_concurrent = max(1, int(max_concurrent or 1))
        if safe_max_concurrent == 1:
            results: List[EmbeddingResult] = []
            for embedding_input in embedding_inputs:
                results.append(await self.embed_text(embedding_input, session_id=session_id))
            return results

        semaphore = asyncio.Semaphore(safe_max_concurrent)

        async def _embed_one(index: int, embedding_input: str) -> tuple[int, EmbeddingResult]:
            """执行单条嵌入并保留原始顺序索引。

            Args:
                index: 原始输入索引。
                embedding_input: 待编码的文本内容。

            Returns:
                tuple[int, EmbeddingResult]: 输入索引与对应嵌入结果。
            """
            async with semaphore:
                result = await self.embed_text(embedding_input, session_id=session_id)
                return index, result

        ordered_results = await asyncio.gather(
            *[_embed_one(index, embedding_input) for index, embedding_input in enumerate(embedding_inputs)]
        )
        ordered_results.sort(key=lambda item: item[0])
        return [result for _, result in ordered_results]

    def embed_text_sync(self, embedding_input: str, *, session_id: str = "") -> EmbeddingResult:
        """以同步方式生成单条文本的嵌入向量。

        Args:
            embedding_input: 待编码的文本内容。

        Returns:
            EmbeddingResult: 统一嵌入结果对象。
        """
        return self._run_coroutine_sync(self.embed_text(embedding_input, session_id=session_id))

    def embed_texts_sync(
        self,
        embedding_inputs: List[str],
        max_concurrent: int | None = None,
        *,
        session_id: str = "",
    ) -> List[EmbeddingResult]:
        """以同步方式批量生成文本嵌入向量。

        Args:
            embedding_inputs: 待编码的文本列表。
            max_concurrent: 最大并发数；未提供时按串行执行。

        Returns:
            List[EmbeddingResult]: 与输入顺序一致的嵌入结果列表。
        """
        return self._run_coroutine_sync(
            self.embed_texts(
                embedding_inputs,
                max_concurrent=max_concurrent,
                session_id=session_id,
            )
        )

    @staticmethod
    def _run_coroutine_sync(coroutine: Coroutine[Any, Any, _CoroutineReturnT]) -> _CoroutineReturnT:
        """在独立事件循环中执行协程。

        Args:
            coroutine: 需要同步执行的协程对象。

        Returns:
            _CoroutineReturnT: 协程返回值。

        Raises:
            RuntimeError: 当前线程已有运行中的事件循环时抛出。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("当前线程存在运行中的事件循环，请改用异步 Embedding 接口")

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coroutine)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception as exc:
                logger.debug(f"关闭 EmbeddingService 临时异步生成器失败: {exc}")
            asyncio.set_event_loop(None)
            loop.close()
