from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass, field
from enum import Enum
import hashlib

class KnowledgeType(str, Enum):
    NARRATIVE = "narrative"
    FACTUAL = "factual"
    QUOTE = "quote"
    MIXED = "mixed"

@dataclass
class SourceInfo:
    file: str
    offset_start: int
    offset_end: int
    checksum: str = ""

@dataclass
class ChunkContext:
    chunk_id: str
    index: int
    context: Dict[str, Any] = field(default_factory=dict)
    text: str = ""

@dataclass
class ChunkFlags:
    verbatim: bool = False
    requires_llm: bool = True

@dataclass
class ProcessedChunk:
    type: KnowledgeType
    source: SourceInfo
    chunk: ChunkContext
    data: Dict[str, Any] = field(default_factory=dict) # triples、events、verbatim_entities
    flags: ChunkFlags = field(default_factory=ChunkFlags)

    def to_dict(self) -> Dict:
        return {
            "type": self.type.value,
            "source": {
                "file": self.source.file,
                "offset_start": self.source.offset_start,
                "offset_end": self.source.offset_end,
                "checksum": self.source.checksum
            },
            "chunk": {
                "text": self.chunk.text,
                "chunk_id": self.chunk.chunk_id,
                "index": self.chunk.index,
                "context": self.chunk.context
            },
            "data": self.data,
            "flags": {
                "verbatim": self.flags.verbatim,
                "requires_llm": self.flags.requires_llm
            }
        }

class BaseStrategy(ABC):
    def __init__(self, filename: str):
        self.filename = filename

    @abstractmethod
    def split(self, text: str) -> List[ProcessedChunk]:
        """按策略将文本切分为块。"""
        pass

    @abstractmethod
    async def extract(self, chunk: ProcessedChunk, llm_func=None) -> ProcessedChunk:
        """从文本块中抽取结构化信息。"""
        pass

    def calculate_checksum(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def build_language_guard(self, text: str) -> str:
        """
        构建统一的输出语言约束。
        不区分语言类型，仅要求抽取值保持原文语言，不做翻译。
        """
        _ = text  # 预留参数，便于后续按需扩展
        return (
            "Focus on the original source language. Keep extracted events, entities, predicates "
            "and objects in the same language as the source text, preserve names/terms as-is, "
            "and do not translate."
        )
