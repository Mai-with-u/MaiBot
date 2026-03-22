from typing import List, Dict, Any
from .base import BaseStrategy, ProcessedChunk, KnowledgeType, SourceInfo, ChunkContext, ChunkFlags

class QuoteStrategy(BaseStrategy):
    def split(self, text: str) -> List[ProcessedChunk]:
        # Split by double newlines (stanzas)
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
                    requires_llm=False # Default to no LLM, but can be overridden
                )
            )
            chunks.append(chunk)
            offset += len(stanza) + 2 # +2 for \n\n
            
        return chunks

    async def extract(self, chunk: ProcessedChunk, llm_func=None) -> ProcessedChunk:
        # For quotes, the text itself is the entity/knowledge
        # We might use LLM to extract headers/metadata if requested, but core logic is pass-through
        
        # Treat the whole chunk text as a verbatim entity
        chunk.data = {
            "verbatim_entities": [chunk.chunk.text]
        }
        
        if llm_func and chunk.flags.requires_llm:
             # Optional: Extract metadata
             pass
             
        return chunk
