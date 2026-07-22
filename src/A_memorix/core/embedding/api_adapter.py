"""
请求式嵌入 API 适配器。

统一记忆插件内部的维度控制语义：
- 对外公开 `embedding.dimension` 与 `embedding.dimension_request_mode`
- 维度请求是否随默认 encode 发送由 `embedding.dimension_request_mode` 控制
- provider-specific 字段在适配层内部完成映射
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
import numpy as np
import openai

from src.common.logger import get_logger
from src.config.config import config_manager
from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.exceptions import NetworkConnectionError
from src.llm_models.model_client.base_client import EmbeddingRequest, client_registry

logger = get_logger("A_Memorix.EmbeddingAPIAdapter")


class EmbeddingAPIAdapter:
    """适配宿主 embedding 请求接口。"""

    _GLOBAL_DIMENSION_CACHE: Dict[str, int] = {}
    _GLOBAL_TEXT_EMBEDDING_CACHE: Dict[Tuple[str, int, str], np.ndarray] = {}

    def __init__(
        self,
        batch_size: int = 32,
        max_concurrent: int = 5,
        default_dimension: int = 1024,
        enable_cache: bool = False,
        model_name: str = "auto",
        dimension_request_mode: str = "explicit",
        retry_config: Optional[dict] = None,
    ) -> None:
        self.batch_size = max(1, int(batch_size))
        self.max_concurrent = max(1, int(max_concurrent))
        self.default_dimension = max(1, int(default_dimension))
        self.enable_cache = bool(enable_cache)
        self.model_name = str(model_name or "auto")
        self.dimension_request_mode = self._normalize_dimension_request_mode(dimension_request_mode)

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
        self._last_success_model_name = ""
        self._last_success_provider_name = ""

        logger.info(
            "Embedding 初始化: "
            f"batch={self.batch_size}, "
            f"concurrent={self.max_concurrent}, "
            f"dim={self.default_dimension}, "
            f"model={self.model_name}, "
            f"dimension_request_mode={self.dimension_request_mode}"
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
    def _fingerprint_hash(payload: Dict[str, Any]) -> str:
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def _resolve_model_provider_name(self, model_name: str) -> str:
        token = str(model_name or "").strip()
        if not token:
            return ""
        try:
            return str(self._find_model_info(token).api_provider or "").strip()
        except Exception:
            return ""

    def get_embedding_fingerprint(self, *, dimension: Optional[int] = None) -> Dict[str, Any]:
        """返回当前适配器所用向量空间的精简指纹。"""
        effective_dimension = max(1, int(dimension or self.get_embedding_dimension()))
        model_token = str(self._last_success_model_name or "").strip()
        provider_token = str(self._last_success_provider_name or "").strip()
        source = "observed" if model_token else "configured"
        candidate_names = [
            str(item or "").strip() for item in self._resolve_candidate_model_names() if str(item or "").strip()
        ]
        if not model_token:
            model_token = str(self.model_name or "auto").strip() or "auto"
            if model_token == "auto" and candidate_names:
                model_token = candidate_names[0]
            if model_token != "auto":
                provider_token = self._resolve_model_provider_name(model_token)
        if model_token == "auto":
            provider_token = ""

        compare_payload: Dict[str, Any] = {
            "model": model_token,
            "provider": provider_token,
            "dimension": effective_dimension,
            "dimension_request_mode": self.dimension_request_mode,
        }
        if model_token == "auto":
            compare_payload["candidate_models"] = candidate_names

        return {
            "version": 1,
            "hash": self._fingerprint_hash(compare_payload),
            "model": model_token,
            "provider": provider_token,
            "dimension": effective_dimension,
            "dimension_request_mode": self.dimension_request_mode,
            "source": source,
        }

    def get_requested_dimension(self) -> int:
        if self._dimension is not None:
            return int(self._dimension)
        return int(self.default_dimension)

    @staticmethod
    def _normalize_dimension_override(dimensions: Optional[int]) -> Optional[int]:
        if dimensions is None:
            return None
        return max(1, int(dimensions))

    def _resolve_canonical_dimension(self, dimensions: Optional[int] = None) -> int:
        override = self._normalize_dimension_override(dimensions)
        if override is not None:
            return override
        return self.get_requested_dimension()

    @staticmethod
    def _normalize_dimension_request_mode(mode: str) -> str:
        normalized = str(mode or "explicit").strip().lower()
        if normalized in {"auto", "explicit", "on_demand", "on-demand"}:
            return "explicit"
        if normalized in {"always", "force", "forced"}:
            return "always"
        if normalized in {"never", "none", "natural"}:
            return "never"
        logger.warning(f"未知 embedding.dimension_request_mode={mode!r}，回退 explicit")
        return "explicit"

    def _should_include_dimension(self, dimensions: Optional[int], include_dimension: bool) -> bool:
        if not include_dimension:
            return False
        if self.dimension_request_mode == "never":
            return False
        if self.dimension_request_mode == "always":
            return True
        return dimensions is not None

    @staticmethod
    def _strip_dimension_control_keys(extra_params: dict) -> dict:
        sanitized = dict(extra_params or {})
        sanitized.pop("dimensions", None)
        sanitized.pop("output_dimensionality", None)
        return sanitized

    def _build_request_extra_params(
        self,
        *,
        api_provider: APIProvider,
        base_extra_params: dict,
        requested_dimension: Optional[int],
        include_dimension: bool,
    ) -> dict:
        extra_params = self._strip_dimension_control_keys(base_extra_params)
        if not include_dimension or requested_dimension is None:
            return extra_params

        client_type = str(getattr(api_provider, "client_type", "") or "").strip().lower()
        if client_type in {"gemini", "google"}:
            extra_params["output_dimensionality"] = int(requested_dimension)
        elif client_type == "openai":
            extra_params["dimensions"] = int(requested_dimension)
        return extra_params

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
                    EmbeddingRequest(
                        model_info=model_info,
                        embedding_input=text,
                        extra_params=extra_params,
                    )
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

    async def _get_embedding_direct(
        self,
        text: str,
        dimensions: Optional[int] = None,
        *,
        include_dimension: bool = True,
    ) -> Optional[List[float]]:
        candidate_names = self._resolve_candidate_model_names()
        if not candidate_names:
            raise RuntimeError("embedding 任务未配置模型")

        last_exc: Optional[BaseException] = None
        for candidate_name in candidate_names:
            try:
                model_info = self._find_model_info(candidate_name)
                api_provider = self._find_provider(model_info.api_provider)
                client = client_registry.get_client_class_instance(api_provider)

                should_include_dimension = self._should_include_dimension(dimensions, include_dimension)
                requested_dimension = (
                    self._resolve_canonical_dimension(dimensions) if should_include_dimension else None
                )
                extra_params = self._build_request_extra_params(
                    api_provider=api_provider,
                    base_extra_params=dict(getattr(model_info, "extra_params", {}) or {}),
                    requested_dimension=requested_dimension,
                    include_dimension=should_include_dimension,
                )

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
                self._last_success_model_name = str(candidate_name or "").strip()
                self._last_success_provider_name = str(model_info.api_provider or "").strip()
                return vector.tolist()
            except Exception as exc:
                last_exc = exc
                logger.warning(f"embedding 模型 {candidate_name} 请求失败: {exc}")

        if last_exc is not None:
            logger.error(f"通过直接 Client 获取 Embedding 失败: {last_exc}")
        return None

    def _dimension_cache_key(self) -> str:
        candidate_names = self._resolve_candidate_model_names()
        return "|".join(
            [
                str(self.model_name or "auto"),
                str(self.default_dimension),
                str(self.dimension_request_mode),
                ",".join(candidate_names),
            ]
        )

    def _embedding_cache_key(self, text: str, dimensions: Optional[int]) -> Tuple[str, int, str]:
        requested_dimension = self._resolve_canonical_dimension(dimensions)
        return (self._dimension_cache_key(), int(requested_dimension), str(text or ""))

    async def _detect_dimension(self) -> int:
        if self._dimension_detected and self._dimension is not None:
            return self._dimension

        cache_key = self._dimension_cache_key()
        cached_dimension = self._GLOBAL_DIMENSION_CACHE.get(cache_key)
        if cached_dimension is not None:
            self._dimension = int(cached_dimension)
            self._dimension_detected = True
            logger.debug(f"嵌入维度命中进程缓存: {self._dimension}")
            return self._dimension

        logger.info("检测嵌入维度...")
        if self.dimension_request_mode == "always":
            try:
                target_dim = self.default_dimension
                logger.debug(f"尝试请求指定维度: {target_dim}")
                test_embedding = await self._get_embedding_direct("test", dimensions=target_dim)
                if test_embedding and isinstance(test_embedding, list):
                    detected_dim = len(test_embedding)
                    if detected_dim == target_dim:
                        logger.info(f"嵌入维度: {detected_dim}")
                    else:
                        logger.warning(
                            f"requested_dimension={target_dim} 但模型返回 detected_dimension={detected_dim}，将使用真实输出维度"
                        )
                    self._dimension = detected_dim
                    self._dimension_detected = True
                    self._GLOBAL_DIMENSION_CACHE[cache_key] = int(detected_dim)
                    return detected_dim
            except Exception as exc:
                logger.debug(f"带维度参数探测失败: {exc}，尝试不带维度参数探测")

        try:
            test_embedding = await self._get_embedding_direct("test", include_dimension=False)
            if test_embedding and isinstance(test_embedding, list):
                detected_dim = len(test_embedding)
                self._dimension = detected_dim
                self._dimension_detected = True
                self._GLOBAL_DIMENSION_CACHE[cache_key] = int(detected_dim)
                logger.info(f"嵌入维度: {detected_dim} (自然输出)")
                return detected_dim
            logger.warning(f"嵌入维度检测失败，使用 configured_dimension: {self.default_dimension}")
        except Exception as exc:
            logger.error(f"嵌入维度检测异常: {exc}，使用 configured_dimension: {self.default_dimension}")

        self._dimension = self.default_dimension
        self._dimension_detected = True
        self._GLOBAL_DIMENSION_CACHE[cache_key] = int(self.default_dimension)
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
        if dimensions is None or self.dimension_request_mode == "never":
            target_dim = int(await self._detect_dimension())
            requested_dimension = (
                None if self.dimension_request_mode != "always" else self._resolve_canonical_dimension()
            )
        else:
            target_dim = self._resolve_canonical_dimension(dimensions)
            requested_dimension = target_dim

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
                dimensions=requested_dimension,
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
            batch_results: List[Tuple[int, np.ndarray]] = []
            uncached_items: List[Tuple[int, str]] = []

            if self.enable_cache:
                for index, text in enumerate(batch):
                    cache_key = self._embedding_cache_key(text, dimensions)
                    cached_vector = self._GLOBAL_TEXT_EMBEDDING_CACHE.get(cache_key)
                    if cached_vector is None:
                        uncached_items.append((index, text))
                    else:
                        batch_results.append((index, cached_vector.copy()))
            else:
                uncached_items = list(enumerate(batch))

            if not uncached_items:
                batch_results.sort(key=lambda item: item[0])
                all_embeddings.extend(emb for _, emb in batch_results)
                continue

            semaphore = asyncio.Semaphore(self.max_concurrent)

            async def encode_with_semaphore(
                text: str,
                batch_index: int,
                absolute_index: int,
                _semaphore: asyncio.Semaphore = semaphore,
            ):
                async with _semaphore:
                    embedding = await self._get_embedding_direct(text, dimensions=dimensions)
                    if embedding is None:
                        raise RuntimeError(f"文本 {absolute_index} 编码失败：embedding 返回为空")
                    vector = self._validate_embedding_vector(
                        embedding,
                        source=f"文本 {absolute_index}",
                    )
                    return batch_index, vector

            tasks = [encode_with_semaphore(text, index, offset + index) for index, text in uncached_items]
            results = await asyncio.gather(*tasks)
            normalized_results: List[Tuple[int, np.ndarray]] = []
            for batch_index, vector in results:
                normalized_results.append((batch_index, vector))
                if self.enable_cache:
                    text = batch[batch_index]
                    cache_key = self._embedding_cache_key(text, dimensions)
                    self._GLOBAL_TEXT_EMBEDDING_CACHE[cache_key] = vector.copy()

            batch_results.extend(normalized_results)
            batch_results.sort(key=lambda item: item[0])
            all_embeddings.extend(emb for _, emb in batch_results)

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
        logger.warning(f"维度尚未检测，返回 configured_dimension: {self.default_dimension}")
        return self.default_dimension

    def get_model_info(self) -> dict:
        effective_dimension = self.get_embedding_dimension()
        return {
            "model_name": self.model_name,
            "dimension": effective_dimension,
            "configured_dimension": int(self.default_dimension),
            "requested_dimension": int(self.get_requested_dimension()),
            "detected_dimension": int(self._dimension or 0),
            "dimension_detected": self._dimension_detected,
            "dimension_request_mode": self.dimension_request_mode,
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
            "EmbeddingAPIAdapter("
            f"configured={self.default_dimension}, "
            f"requested={self.get_requested_dimension()}, "
            f"detected={self._dimension or 0}, "
            f"dimension_request_mode={self.dimension_request_mode}, "
            f"encoded={self._total_encoded})"
        )


def create_embedding_api_adapter(
    batch_size: int = 32,
    max_concurrent: int = 5,
    default_dimension: int = 1024,
    enable_cache: bool = False,
    model_name: str = "auto",
    dimension_request_mode: str = "explicit",
    retry_config: Optional[dict] = None,
) -> EmbeddingAPIAdapter:
    return EmbeddingAPIAdapter(
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        default_dimension=default_dimension,
        enable_cache=enable_cache,
        model_name=model_name,
        dimension_request_mode=dimension_request_mode,
        retry_config=retry_config,
    )
