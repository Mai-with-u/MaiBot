"""
请求式嵌入 API 适配器。

恢复 v1.0.1 的真实 embedding 请求语义：
- 通过宿主模型配置探测/请求 embedding
- 支持 dimensions 参数
- 支持批量与重试
- 不再提供本地 hash fallback
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, List, Optional, Union

import aiohttp
import numpy as np
import openai

from src.common.logger import get_logger
from src.config.config import config_manager
from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.exceptions import NetworkConnectionError
from src.llm_models.model_client.base_client import client_registry

logger = get_logger("A_Memorix.EmbeddingAPIAdapter")


class EmbeddingAPIAdapter:
    """适配宿主 embedding 请求接口。"""

    def __init__(
        self,
        batch_size: int = 32,
        max_concurrent: int = 5,
        default_dimension: int = 1024,
        enable_cache: bool = False,
        model_name: str = "auto",
        retry_config: Optional[dict] = None,
    ) -> None:
        self.batch_size = max(1, int(batch_size))
        self.max_concurrent = max(1, int(max_concurrent))
        self.default_dimension = max(1, int(default_dimension))
        self.enable_cache = bool(enable_cache)
        self.model_name = str(model_name or "auto")

        self.retry_config = retry_config or {}
        self.max_attempts = max(1, int(self.retry_config.get("max_attempts", 5)))
        self.max_wait_seconds = max(0.1, float(self.retry_config.get("max_wait_seconds", 40)))
        self.min_wait_seconds = max(0.1, float(self.retry_config.get("min_wait_seconds", 3)))
        self.backoff_multiplier = max(1.0, float(self.retry_config.get("backoff_multiplier", 3)))

        self._dimension: Optional[int] = None
        self._dimension_detected = False
        self._total_encoded = 0
        self._total_errors = 0
        self._total_time = 0.0

        logger.info(
            "EmbeddingAPIAdapter 初始化: "
            f"batch_size={self.batch_size}, "
            f"max_concurrent={self.max_concurrent}, "
            f"default_dim={self.default_dimension}, "
            f"model={self.model_name}"
        )

    def _get_current_model_config(self):
        return config_manager.get_model_config()

    @staticmethod
    def _find_model_info(model_name: str) -> ModelInfo:
        model_cfg = config_manager.get_model_config()
        for item in model_cfg.models:
            if item.name == model_name:
                return item
        raise ValueError(f"未找到 embedding 模型: {model_name}")

    @staticmethod
    def _find_provider(provider_name: str) -> APIProvider:
        model_cfg = config_manager.get_model_config()
        for item in model_cfg.api_providers:
            if item.name == provider_name:
                return item
        raise ValueError(f"未找到 embedding provider: {provider_name}")

    def _resolve_candidate_model_names(self) -> List[str]:
        task_config = self._get_current_model_config().model_task_config.embedding
        configured = list(getattr(task_config, "model_list", []) or [])
        if self.model_name and self.model_name != "auto":
            return [self.model_name, *[name for name in configured if name != self.model_name]]
        return configured

    @staticmethod
    def _validate_embedding_vector(embedding: Any, *, source: str) -> np.ndarray:
        array = np.asarray(embedding, dtype=np.float32)
        if array.ndim != 1:
            raise RuntimeError(f"{source} 返回的 embedding 维度非法: ndim={array.ndim}")
        if array.size <= 0:
            raise RuntimeError(f"{source} 返回了空 embedding")
        if not np.all(np.isfinite(array)):
            raise RuntimeError(f"{source} 返回了非有限 embedding 值")
        return array

    async def _request_with_retry(self, client, model_info, text: str, extra_params: dict):
        retriable_exceptions = (
            openai.APIConnectionError,
            openai.APITimeoutError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            NetworkConnectionError,
        )

        last_exc: Optional[BaseException] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await client.get_embedding(
                    model_info=model_info,
                    embedding_input=text,
                    extra_params=extra_params,
                )
            except retriable_exceptions as exc:
                last_exc = exc
                if attempt >= self.max_attempts:
                    raise
                wait_seconds = min(
                    self.max_wait_seconds,
                    self.min_wait_seconds * (self.backoff_multiplier ** (attempt - 1)),
                )
                logger.warning(
                    "Embedding 请求失败，重试 "
                    f"{attempt}/{max(1, self.max_attempts - 1)}，"
                    f"{wait_seconds:.1f}s 后重试: {exc}"
                )
                await asyncio.sleep(wait_seconds)
            except Exception:
                raise

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Embedding 请求失败：未知错误")

    async def _get_embedding_direct(self, text: str, dimensions: Optional[int] = None) -> Optional[List[float]]:
        candidate_names = self._resolve_candidate_model_names()
        if not candidate_names:
            raise RuntimeError("embedding 任务未配置模型")

        last_exc: Optional[BaseException] = None
        for candidate_name in candidate_names:
            try:
                model_info = self._find_model_info(candidate_name)
                api_provider = self._find_provider(model_info.api_provider)
                client = client_registry.get_client_class_instance(api_provider, force_new=True)

                extra_params = dict(getattr(model_info, "extra_params", {}) or {})
                if dimensions is not None:
                    extra_params["dimensions"] = int(dimensions)

                response = await self._request_with_retry(
                    client=client,
                    model_info=model_info,
                    text=text,
                    extra_params=extra_params,
                )
                embedding = getattr(response, "embedding", None)
                if embedding is None:
                    raise RuntimeError(f"模型 {candidate_name} 未返回 embedding")
                vector = self._validate_embedding_vector(
                    embedding,
                    source=f"embedding 模型 {candidate_name}",
                )
                return vector.tolist()
            except Exception as exc:
                last_exc = exc
                logger.warning(f"embedding 模型 {candidate_name} 请求失败: {exc}")

        if last_exc is not None:
            logger.error(f"通过直接 Client 获取 Embedding 失败: {last_exc}")
        return None

    async def _detect_dimension(self) -> int:
        if self._dimension_detected and self._dimension is not None:
            return self._dimension

        logger.info("正在检测嵌入模型维度...")
        try:
            target_dim = self.default_dimension
            logger.debug(f"尝试请求指定维度: {target_dim}")
            test_embedding = await self._get_embedding_direct("test", dimensions=target_dim)
            if test_embedding and isinstance(test_embedding, list):
                detected_dim = len(test_embedding)
                if detected_dim == target_dim:
                    logger.info(f"嵌入维度检测成功 (匹配配置): {detected_dim}")
                else:
                    logger.warning(
                        f"请求维度 {target_dim} 但模型返回 {detected_dim}，将使用模型自然维度"
                    )
                self._dimension = detected_dim
                self._dimension_detected = True
                return detected_dim
        except Exception as exc:
            logger.debug(f"带维度参数探测失败: {exc}，尝试不带参数探测")

        try:
            test_embedding = await self._get_embedding_direct("test", dimensions=None)
            if test_embedding and isinstance(test_embedding, list):
                detected_dim = len(test_embedding)
                self._dimension = detected_dim
                self._dimension_detected = True
                logger.info(f"嵌入维度检测成功 (自然维度): {detected_dim}")
                return detected_dim
            logger.warning(f"嵌入维度检测失败，使用默认值: {self.default_dimension}")
        except Exception as exc:
            logger.error(f"嵌入维度检测异常: {exc}，使用默认值: {self.default_dimension}")

        self._dimension = self.default_dimension
        self._dimension_detected = True
        return self.default_dimension

    async def encode(
        self,
        texts: Union[str, List[str]],
        batch_size: Optional[int] = None,
        show_progress: bool = False,
        normalize: bool = True,
        dimensions: Optional[int] = None,
    ) -> np.ndarray:
        del show_progress
        del normalize

        start_time = time.time()
        target_dim = int(dimensions) if dimensions is not None else int(await self._detect_dimension())

        if isinstance(texts, str):
            normalized_texts = [texts]
            single_input = True
        else:
            normalized_texts = list(texts or [])
            single_input = False

        if not normalized_texts:
            empty = np.zeros((0, target_dim), dtype=np.float32)
            return empty[0] if single_input else empty

        if batch_size is None:
            batch_size = self.batch_size

        try:
            embeddings = await self._encode_batch_internal(
                normalized_texts,
                batch_size=max(1, int(batch_size)),
                dimensions=dimensions,
            )
            if embeddings.ndim == 1:
                embeddings = embeddings.reshape(1, -1)
            self._total_encoded += len(normalized_texts)
            elapsed = time.time() - start_time
            self._total_time += elapsed
            logger.debug(
                "编码完成: "
                f"{len(normalized_texts)} 个文本, "
                f"耗时 {elapsed:.2f}s, "
                f"平均 {elapsed / max(1, len(normalized_texts)):.3f}s/文本"
            )
            return embeddings[0] if single_input else embeddings
        except Exception as exc:
            self._total_errors += 1
            logger.error(f"编码失败: {exc}")
            raise RuntimeError(f"embedding encode failed: {exc}") from exc

    async def _encode_batch_internal(
        self,
        texts: List[str],
        batch_size: int,
        dimensions: Optional[int] = None,
    ) -> np.ndarray:
        all_embeddings: List[np.ndarray] = []
        for offset in range(0, len(texts), batch_size):
            batch = texts[offset : offset + batch_size]
            semaphore = asyncio.Semaphore(self.max_concurrent)

            async def encode_with_semaphore(text: str, index: int):
                async with semaphore:
                    embedding = await self._get_embedding_direct(text, dimensions=dimensions)
                    if embedding is None:
                        raise RuntimeError(f"文本 {index} 编码失败：embedding 返回为空")
                    vector = self._validate_embedding_vector(
                        embedding,
                        source=f"文本 {index}",
                    )
                    return index, vector

            tasks = [
                encode_with_semaphore(text, offset + index)
                for index, text in enumerate(batch)
            ]
            results = await asyncio.gather(*tasks)
            results.sort(key=lambda item: item[0])
            all_embeddings.extend(emb for _, emb in results)

        return np.array(all_embeddings, dtype=np.float32)

    async def encode_batch(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        num_workers: Optional[int] = None,
        show_progress: bool = False,
        dimensions: Optional[int] = None,
    ) -> np.ndarray:
        del show_progress
        if num_workers is not None:
            previous = self.max_concurrent
            self.max_concurrent = max(1, int(num_workers))
            try:
                return await self.encode(texts, batch_size=batch_size, dimensions=dimensions)
            finally:
                self.max_concurrent = previous
        return await self.encode(texts, batch_size=batch_size, dimensions=dimensions)

    def get_embedding_dimension(self) -> int:
        if self._dimension is not None:
            return self._dimension
        logger.warning(f"维度尚未检测，返回默认值: {self.default_dimension}")
        return self.default_dimension

    def get_model_info(self) -> dict:
        return {
            "model_name": self.model_name,
            "dimension": self._dimension or self.default_dimension,
            "dimension_detected": self._dimension_detected,
            "batch_size": self.batch_size,
            "max_concurrent": self.max_concurrent,
            "total_encoded": self._total_encoded,
            "total_errors": self._total_errors,
            "avg_time_per_text": self._total_time / self._total_encoded if self._total_encoded else 0.0,
        }

    def get_statistics(self) -> dict:
        return self.get_model_info()

    @property
    def is_model_loaded(self) -> bool:
        return True

    def __repr__(self) -> str:
        return (
            f"EmbeddingAPIAdapter(dim={self._dimension or self.default_dimension}, "
            f"detected={self._dimension_detected}, encoded={self._total_encoded})"
        )


def create_embedding_api_adapter(
    batch_size: int = 32,
    max_concurrent: int = 5,
    default_dimension: int = 1024,
    enable_cache: bool = False,
    model_name: str = "auto",
    retry_config: Optional[dict] = None,
) -> EmbeddingAPIAdapter:
    return EmbeddingAPIAdapter(
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        default_dimension=default_dimension,
        enable_cache=enable_cache,
        model_name=model_name,
        retry_config=retry_config,
    )
