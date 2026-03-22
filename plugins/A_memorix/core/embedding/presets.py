"""
嵌入模型配置模块
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, Union
from pathlib import Path


@dataclass
class EmbeddingModelConfig:
    """
    嵌入模型配置

    属性：
        model_name: 模型描述名称
        model_path: 实际加载路径（Local or HF）
        dimension: 嵌入向量维度
        max_seq_length: 最大序列长度
        batch_size: 编码批次大小
        model_size_mb: 估计显存占用
        description: 模型说明
        normalization: 是否自动归一化
        pooling: 池化策略 (mean, cls, max)
        cache_dir: 模型缓存目录
    """

    model_name: str
    model_path: str
    dimension: int
    max_seq_length: int = 512
    batch_size: int = 32
    model_size_mb: int = 100
    description: str = ""
    normalization: bool = True
    pooling: str = "mean"
    cache_dir: Optional[Union[str, Path]] = None


def validate_config_compatibility(
    config1: EmbeddingModelConfig, config2: EmbeddingModelConfig
) -> bool:
    """检查两个配置是否兼容（主要看维度）"""
    return config1.dimension == config2.dimension


def are_models_compatible(
    config1: EmbeddingModelConfig, config2: EmbeddingModelConfig
) -> bool:
    """检查模型是否完全相同（用于热切换判断）"""
    return (
        config1.model_path == config2.model_path
        and config1.dimension == config2.dimension
        and config1.pooling == config2.pooling
    )


def get_custom_config(
    model_name: str,
    model_path: str,
    dimension: int,
    cache_dir: Optional[Union[str, Path]] = None,
    **kwargs,
) -> EmbeddingModelConfig:
    """创建自定义模型配置"""
    return EmbeddingModelConfig(
        model_name=model_name,
        model_path=model_path,
        dimension=dimension,
        cache_dir=cache_dir,
        **kwargs,
    )
