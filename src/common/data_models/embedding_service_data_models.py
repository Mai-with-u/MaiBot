"""Embedding 服务层共享数据模型。"""

from dataclasses import dataclass, field
from typing import List

from src.common.data_models import BaseDataModel


@dataclass(slots=True)
class EmbeddingResult(BaseDataModel):
    """Embedding 服务层统一响应对象。"""

    embedding: List[float] = field(default_factory=list)
    model_name: str = field(default_factory=str)
    model_identifier: str = field(default_factory=str)
    api_provider: str = field(default_factory=str)


__all__ = [
    "EmbeddingResult",
]
