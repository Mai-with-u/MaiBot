"""嵌入模块 - 向量生成与量化"""

# 新的 API 适配器（主程序嵌入 API）
from .api_adapter import (
    EmbeddingAPIAdapter,
    create_embedding_api_adapter,
)

from ..utils.quantization import QuantizationType

__all__ = [
    # 新的 API 适配器（推荐使用）
    "EmbeddingAPIAdapter",
    "create_embedding_api_adapter",
    # 量化
    "QuantizationType",
]

