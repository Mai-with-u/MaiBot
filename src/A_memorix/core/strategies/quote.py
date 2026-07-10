from typing import List
from .base import BaseStrategy, ProcessedChunk, KnowledgeType, SourceInfo, ChunkContext, ChunkFlags

class QuoteStrategy(BaseStrategy):
    def split(self, text: str) -> List[ProcessedChunk]:
        # 按连续两个换行符切分段落（stanza）。
        stanzas = text.split("\n\n")
        chunks = []
        offset = 0
        
        for idx, stanza in enumerate(stanzas):
            if not stanza.strip():
                offset += len(stanza) + 2
                continue
                
            chunk = ProcessedChunk(
                type=KnowledgeType.QUOTE,
                source=SourceInfo(
                    file=self.filename,
                    offset_start=offset,
                    offset_end=offset + len(stanza),
                    checksum=self.calculate_checksum(stanza)
                ),
                chunk=ChunkContext(
                    chunk_id=f"{self.filename}_{idx}",
                    index=idx,
                    text=stanza
                ),
                flags=ChunkFlags(
                    verbatim=True,
                    requires_llm=False # 默认不调用 LLM，但允许调用方覆盖
                )
            )
            chunks.append(chunk)
            offset += len(stanza) + 2 # 加 2 以计入 \n\n
            
        return chunks

    async def extract(self, chunk: ProcessedChunk, llm_func=None) -> ProcessedChunk:
        # 对引文而言，文本本身就是实体或知识内容。
        # 如有需要可调用 LLM 提取标题或元数据，但核心链路保持透传。
        
        # 将整个分块文本作为逐字实体（verbatim entity）。
        chunk.data = {
            "verbatim_entities": [chunk.chunk.text]
        }
        
        if llm_func and chunk.flags.requires_llm:
             # 可选：提取元数据
             pass
             
        return chunk
