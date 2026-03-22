"""
嵌入管理器

负责嵌入模型的加载、缓存和批量生成。
"""

import hashlib
import pickle
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Union, List, Dict, Any, Tuple

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False

from src.common.logger import get_logger
from .presets import (
    EmbeddingModelConfig,
    get_custom_config,
    validate_config_compatibility,
    are_models_compatible,
)
from ..utils.quantization import QuantizationType

logger = get_logger("A_Memorix.EmbeddingManager")


class EmbeddingManager:
    """
    嵌入管理器

    功能：
    - 模型加载与缓存
    - 批量生成嵌入
    - 多线程/多进程支持
    - 模型一致性检查
    - 智能分批

    参数：
        config: 模型配置
        cache_dir: 缓存目录
        enable_cache: 是否启用缓存
        num_workers: 工作线程数
    """

    def __init__(
        self,
        config: EmbeddingModelConfig,
        cache_dir: Optional[Union[str, Path]] = None,
        enable_cache: bool = True,
        num_workers: int = 1,
    ):
        """
        初始化嵌入管理器

        Args:
            config: 模型配置
            cache_dir: 缓存目录
            enable_cache: 是否启用缓存
            num_workers: 工作线程数
        """
        if not HAS_SENTENCE_TRANSFORMERS:
            raise ImportError(
                "sentence-transformers 未安装，请安装: "
                "pip install sentence-transformers"
            )

        self.config = config
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.enable_cache = enable_cache
        self.num_workers = max(1, num_workers)

        # 模型实例
        self._model: Optional[SentenceTransformer] = None
        self._model_lock = threading.Lock()

        # 缓存
        self._embedding_cache: Dict[str, np.ndarray] = {}
        self._cache_lock = threading.Lock()

        # 统计
        self._total_encoded = 0
        self._cache_hits = 0
        self._cache_misses = 0

        logger.info(
            f"EmbeddingManager 初始化: model={config.model_name}, "
            f"dim={config.dimension}, workers={num_workers}"
        )

    def load_model(self) -> None:
        """加载模型（懒加载）"""
        if self._model is not None:
            return

        with self._model_lock:
            # 双重检查
            if self._model is not None:
                return

            logger.info(f"正在加载模型: {self.config.model_name}")

            try:
                # 构建模型参数
                model_kwargs = {}
                if self.config.cache_dir:
                    model_kwargs["cache_folder"] = self.config.cache_dir

                # 加载模型
                self._model = SentenceTransformer(
                    self.config.model_path,
                    **model_kwargs,
                )

                logger.info(f"模型加载成功: {self.config.model_name}")

            except Exception as e:
                logger.error(f"模型加载失败: {e}")
                raise

    def encode(
        self,
        texts: Union[str, List[str]],
        batch_size: Optional[int] = None,
        show_progress: bool = False,
        normalize: bool = True,
    ) -> np.ndarray:
        """
        生成文本嵌入

        Args:
            texts: 文本或文本列表
            batch_size: 批次大小（默认使用配置值）
            show_progress: 是否显示进度条
            normalize: 是否归一化

        Returns:
            嵌入向量 (N x D)
        """
        # 确保模型已加载
        self.load_model()

        # 标准化输入
        if isinstance(texts, str):
            texts = [texts]
            single_input = True
        else:
            single_input = False

        if not texts:
            return np.zeros((0, self.config.dimension), dtype=np.float32)

        # 使用配置的批次大小
        if batch_size is None:
            batch_size = self.config.batch_size

        # 生成嵌入
        try:
            embeddings = self._model.encode(
                texts,
                batch_size=batch_size,
                show_progress_bar=show_progress,
                normalize_embeddings=normalize and self.config.normalization,
                convert_to_numpy=True,
            )

            # 确保是2D数组
            if embeddings.ndim == 1:
                embeddings = embeddings.reshape(1, -1)

            self._total_encoded += len(texts)

            # 如果是单个输入，返回1D数组
            if single_input:
                return embeddings[0]

            return embeddings

        except Exception as e:
            logger.error(f"生成嵌入失败: {e}")
            raise

    def encode_batch(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        num_workers: Optional[int] = None,
        show_progress: bool = False,
    ) -> np.ndarray:
        """
        批量生成嵌入（多线程优化）

        Args:
            texts: 文本列表
            batch_size: 批次大小
            num_workers: 工作线程数（默认使用初始化时的值）
            show_progress: 是否显示进度条

        Returns:
            嵌入向量 (N x D)
        """
        if not texts:
            return np.zeros((0, self.config.dimension), dtype=np.float32)

        # 单线程模式
        num_workers = num_workers if num_workers is not None else self.num_workers
        if num_workers == 1:
            return self.encode(texts, batch_size=batch_size, show_progress=show_progress)

        # 多线程模式
        logger.info(f"使用 {num_workers} 个线程生成 {len(texts)} 个嵌入")

        # 分批
        batch_size = batch_size or self.config.batch_size
        batches = [
            texts[i:i + batch_size]
            for i in range(0, len(texts), batch_size)
        ]

        # 多线程生成
        all_embeddings = []
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            # 提交任务
            future_to_batch = {
                executor.submit(
                    self.encode,
                    batch,
                    batch_size,
                    False,  # 不显示进度条（多线程时会混乱）
                ): i
                for i, batch in enumerate(batches)
            }

            # 收集结果
            for future in as_completed(future_to_batch):
                batch_idx = future_to_batch[future]
                try:
                    embeddings = future.result()
                    all_embeddings.append((batch_idx, embeddings))
                except Exception as e:
                    logger.error(f"批次 {batch_idx} 生成嵌入失败: {e}")
                    raise

        # 按顺序合并
        all_embeddings.sort(key=lambda x: x[0])
        final_embeddings = np.concatenate([emb for _, emb in all_embeddings], axis=0)

        return final_embeddings

    def encode_with_cache(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        show_progress: bool = False,
    ) -> np.ndarray:
        """
        生成嵌入（带缓存）

        Args:
            texts: 文本列表
            batch_size: 批次大小
            show_progress: 是否显示进度条

        Returns:
            嵌入向量 (N x D)
        """
        if not self.enable_cache:
            return self.encode(texts, batch_size, show_progress)

        # 分离缓存命中和未命中的文本
        cached_embeddings = []
        uncached_texts = []
        uncached_indices = []

        for i, text in enumerate(texts):
            cache_key = self._get_cache_key(text)

            with self._cache_lock:
                if cache_key in self._embedding_cache:
                    cached_embeddings.append((i, self._embedding_cache[cache_key]))
                    self._cache_hits += 1
                else:
                    uncached_texts.append(text)
                    uncached_indices.append(i)
                    self._cache_misses += 1

        # 生成未缓存的嵌入
        if uncached_texts:
            new_embeddings = self.encode(
                uncached_texts,
                batch_size,
                show_progress,
            )

            # 更新缓存
            with self._cache_lock:
                for text, embedding in zip(uncached_texts, new_embeddings):
                    cache_key = self._get_cache_key(text)
                    self._embedding_cache[cache_key] = embedding.copy()

            # 合并结果
            for idx, embedding in zip(uncached_indices, new_embeddings):
                cached_embeddings.append((idx, embedding))

        # 按原始顺序排序
        cached_embeddings.sort(key=lambda x: x[0])
        final_embeddings = np.array([emb for _, emb in cached_embeddings])

        return final_embeddings

    def save_cache(self, cache_path: Optional[Union[str, Path]] = None) -> None:
        """
        保存缓存到磁盘

        Args:
            cache_path: 缓存文件路径（默认使用cache_dir/embeddings_cache.pkl）
        """
        if cache_path is None:
            if self.cache_dir is None:
                raise ValueError("未指定缓存目录")
            cache_path = self.cache_dir / "embeddings_cache.pkl"

        cache_path = Path(cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        with self._cache_lock:
            with open(cache_path, "wb") as f:
                pickle.dump(self._embedding_cache, f)

            logger.info(f"缓存已保存: {cache_path} ({len(self._embedding_cache)} 条)")

    def load_cache(self, cache_path: Optional[Union[str, Path]] = None) -> None:
        """
        从磁盘加载缓存

        Args:
            cache_path: 缓存文件路径（默认使用cache_dir/embeddings_cache.pkl）
        """
        if cache_path is None:
            if self.cache_dir is None:
                raise ValueError("未指定缓存目录")
            cache_path = self.cache_dir / "embeddings_cache.pkl"

        cache_path = Path(cache_path)
        if not cache_path.exists():
            logger.warning(f"缓存文件不存在: {cache_path}")
            return

        with self._cache_lock:
            with open(cache_path, "rb") as f:
                self._embedding_cache = pickle.load(f)

            logger.info(f"缓存已加载: {cache_path} ({len(self._embedding_cache)} 条)")

    def clear_cache(self) -> None:
        """清空缓存"""
        with self._cache_lock:
            count = len(self._embedding_cache)
            self._embedding_cache.clear()
            logger.info(f"已清空缓存: {count} 条")

    def check_model_consistency(
        self,
        stored_embeddings: np.ndarray,
        sample_texts: List[str] = None,
    ) -> Tuple[bool, str]:
        """
        检查模型一致性

        Args:
            stored_embeddings: 存储的嵌入向量
            sample_texts: 样本文本（用于重新生成对比）

        Returns:
            (是否一致, 详细信息)
        """
        # 检查维度
        if stored_embeddings.shape[1] != self.config.dimension:
            return False, f"维度不匹配: 期望 {self.config.dimension}, 实际 {stored_embeddings.shape[1]}"

        # 如果提供了样本文本，重新生成并比较
        if sample_texts:
            try:
                new_embeddings = self.encode(sample_texts[:5])  # 只比较前5个

                # 计算相似度
                similarities = np.dot(
                    stored_embeddings[:5],
                    new_embeddings.T,
                ).diagonal()

                # 检查相似度
                if np.mean(similarities) < 0.95:
                    return False, f"模型可能已更改，平均相似度: {np.mean(similarities):.3f}"

                return True, f"模型一致，平均相似度: {np.mean(similarities):.3f}"

            except Exception as e:
                return False, f"一致性检查失败: {e}"

        return True, "维度匹配"

    def get_model_info(self) -> Dict[str, Any]:
        """
        获取模型信息

        Returns:
            模型信息字典
        """
        return {
            "model_name": self.config.model_name,
            "dimension": self.config.dimension,
            "max_seq_length": self.config.max_seq_length,
            "batch_size": self.config.batch_size,
            "normalization": self.config.normalization,
            "pooling": self.config.pooling,
            "model_loaded": self._model is not None,
            "cache_enabled": self.enable_cache,
            "cache_size": len(self._embedding_cache),
            "total_encoded": self._total_encoded,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
        }

    def get_embedding_dimension(self) -> int:
        """获取嵌入维度"""
        return self.config.dimension

    def _get_cache_key(self, text: str) -> str:
        """
        生成缓存键

        Args:
            text: 文本内容

        Returns:
            缓存键（SHA256哈希）
        """
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @property
    def is_model_loaded(self) -> bool:
        """模型是否已加载"""
        return self._model is not None

    @property
    def cache_hit_rate(self) -> float:
        """缓存命中率"""
        total = self._cache_hits + self._cache_misses
        if total == 0:
            return 0.0
        return self._cache_hits / total

    def __repr__(self) -> str:
        return (
            f"EmbeddingManager(model={self.config.model_name}, "
            f"dim={self.config.dimension}, "
            f"loaded={self.is_model_loaded}, "
            f"cache={len(self._embedding_cache)})"
        )




def create_embedding_manager_from_config(
    model_name: str,
    model_path: str,
    dimension: int,
    cache_dir: Optional[Union[str, Path]] = None,
    enable_cache: bool = True,
    num_workers: int = 1,
    **config_kwargs,
) -> EmbeddingManager:
    """
    从自定义配置创建嵌入管理器

    Args:
        model_name: 模型名称
        model_path: HuggingFace模型路径
        dimension: 输出维度
        cache_dir: 缓存目录
        enable_cache: 是否启用缓存
        num_workers: 工作线程数
        **config_kwargs: 其他配置参数

    Returns:
        嵌入管理器实例
    """
    # 创建自定义配置
    config = get_custom_config(
        model_name=model_name,
        model_path=model_path,
        dimension=dimension,
        cache_dir=cache_dir,
        **config_kwargs,
    )

    # 创建管理器
    return EmbeddingManager(
        config=config,
        cache_dir=cache_dir,
        enable_cache=enable_cache,
        num_workers=num_workers,
    )
