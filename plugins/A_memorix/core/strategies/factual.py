import re
from typing import List, Dict, Any
from .base import BaseStrategy, ProcessedChunk, KnowledgeType, SourceInfo, ChunkContext

class FactualStrategy(BaseStrategy):
    def split(self, text: str) -> List[ProcessedChunk]:
        # 结构感知切分
        lines = text.split('\n')
        chunks = []
        current_chunk_lines = []
        current_len = 0
        target_size = 600
        
        for i, line in enumerate(lines):
            # 判断是否应当切分
            # 若当前行为列表项/定义/表格行，则尽量不切分
            is_structure = self._is_structural_line(line)
            
            current_len += len(line) + 1
            current_chunk_lines.append(line)
            
            # 达到目标长度且不在紧凑结构块内时切分（过长时强制切分）
            if current_len >= target_size and not is_structure:
                 chunks.append(self._create_chunk(current_chunk_lines, len(chunks)))
                 current_chunk_lines = []
                 current_len = 0
            elif current_len >= target_size * 2: # 超长时强制切分
                 chunks.append(self._create_chunk(current_chunk_lines, len(chunks)))
                 current_chunk_lines = []
                 current_len = 0

        if current_chunk_lines:
            chunks.append(self._create_chunk(current_chunk_lines, len(chunks)))
            
        return chunks

    def _is_structural_line(self, line: str) -> bool:
        line = line.strip()
        if not line: return False
        # 列表项
        if re.match(r'^[\-\*]\s+', line) or re.match(r'^\d+\.\s+', line):
            return True
        # 定义项（术语: 定义）
        if re.match(r'^[^：:]+[：:].+', line):
            return True
        # 表格行（按 markdown 语法假设）
        if line.startswith('|') and line.endswith('|'):
            return True
        return False

    def _create_chunk(self, lines: List[str], index: int) -> ProcessedChunk:
        text = "\n".join(lines)
        return ProcessedChunk(
            type=KnowledgeType.FACTUAL,
            source=SourceInfo(
                file=self.filename,
                offset_start=0, # 简化处理：真实偏移跟踪需要额外状态
                offset_end=0,
                checksum=self.calculate_checksum(text)
            ),
            chunk=ChunkContext(
                chunk_id=f"{self.filename}_{index}",
                index=index,
                text=text
            )
        )

    async def extract(self, chunk: ProcessedChunk, llm_func=None) -> ProcessedChunk:
        if not llm_func:
            raise ValueError("LLM function required for Factual extraction")

        language_guard = self.build_language_guard(chunk.chunk.text)
        prompt = f"""You are a factual knowledge extraction engine.
Extract factual triples and entities from the text.
Preserve lists and definitions accurately.

Language constraints:
- {language_guard}
- Preserve original names and domain terms exactly when possible.
- JSON keys must stay exactly as: triples, entities, subject, predicate, object.

Text:
{chunk.chunk.text}

Return ONLY valid JSON:
{{
  "triples": [
    {{"subject": "Entity", "predicate": "Relationship", "object": "Entity"}}
  ],
  "entities": ["Entity1", "Entity2"]
}}
"""
        result = await llm_func(prompt)
        
        # 结果保持原样存入 data，后续统一归一化流程会处理
        # vector_store 侧期望关系字段为 subject/predicate/object 映射形式
        chunk.data = result
        return chunk
