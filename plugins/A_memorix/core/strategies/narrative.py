import re
from typing import List, Dict, Any
from .base import BaseStrategy, ProcessedChunk, KnowledgeType, SourceInfo, ChunkContext

class NarrativeStrategy(BaseStrategy):
    def split(self, text: str) -> List[ProcessedChunk]:
        scenes = self._split_into_scenes(text)
        chunks = []
        
        for scene_idx, (scene_text, scene_title) in enumerate(scenes):
             scene_chunks = self._sliding_window(scene_text, scene_title, scene_idx)
             chunks.extend(scene_chunks)
             
        return chunks

    def _split_into_scenes(self, text: str) -> List[tuple[str, str]]:
        """按标题或分隔符把文本切分为场景。"""
        # 简单启发式：按 markdown 标题或特定分隔符切分
        # 该正则匹配以 #、Chapter 或 *** / === 开头的分隔行
        # 该正则匹配以 #、Chapter 或 *** / === 开头的分隔行
        scene_pattern_str = r'^(?:#{1,6}\s+.*|Chapter\s+\d+|^\*{3,}$|^={3,}$)'
        
        # 保留分隔符，以便识别场景起点
        parts = re.split(f"({scene_pattern_str})", text, flags=re.MULTILINE)
        
        scenes = []
        current_scene_title = "Start"
        current_scene_content = []
        
        if parts and parts[0].strip() == "":
            parts = parts[1:]
            
        for part in parts:
            if re.match(scene_pattern_str, part, re.MULTILINE):
                # 先保存上一段场景
                if current_scene_content:
                    scenes.append(("".join(current_scene_content), current_scene_title))
                    current_scene_content = []
                current_scene_title = part.strip()
            else:
                current_scene_content.append(part)
                
        if current_scene_content:
             scenes.append(("".join(current_scene_content), current_scene_title))
             
        # 若未识别到场景，则把全文视作单一场景
        if not scenes:
            scenes = [(text, "Whole Text")]

        return scenes

    def _sliding_window(self, text: str, scene_id: str, scene_idx: int, window_size=800, overlap=200) -> List[ProcessedChunk]:
        chunks = []
        if len(text) <= window_size:
            chunks.append(self._create_chunk(text, scene_id, scene_idx, 0, 0))
            return chunks

        stride = window_size - overlap
        start = 0
        local_idx = 0
        while start < len(text):
            end = min(start + window_size, len(text))
            chunk_text = text[start:end]
            
            # 尽量对齐到最近换行，避免生硬截断句子
            # 仅在未到文本尾部时进行回退
            if end < len(text):
                last_newline = chunk_text.rfind('\n')
                if last_newline > window_size // 2: # 仅在回退距离可接受时启用
                    end = start + last_newline + 1
                    chunk_text = text[start:end]
            
            chunks.append(self._create_chunk(chunk_text, scene_id, scene_idx, local_idx, start))
            
            start += len(chunk_text) - overlap if end < len(text) else len(chunk_text)
            local_idx += 1
            
        return chunks

    def _create_chunk(self, text: str, scene_id: str, scene_idx: int, local_idx: int, offset: int) -> ProcessedChunk:
        return ProcessedChunk(
            type=KnowledgeType.NARRATIVE,
            source=SourceInfo(
                file=self.filename,
                offset_start=offset,
                offset_end=offset + len(text),
                checksum=self.calculate_checksum(text)
            ),
            chunk=ChunkContext(
                chunk_id=f"{self.filename}_{scene_idx}_{local_idx}",
                index=local_idx,
                text=text,
                context={"scene_id": scene_id}
            )
        )

    async def extract(self, chunk: ProcessedChunk, llm_func=None) -> ProcessedChunk:
        if not llm_func:
            raise ValueError("LLM function required for Narrative extraction")

        language_guard = self.build_language_guard(chunk.chunk.text)
        prompt = f"""You are a narrative knowledge extraction engine.
Extract key events and character relations from the scene text.

Language constraints:
- {language_guard}
- Preserve original names and terms exactly when possible.
- JSON keys must stay exactly as: events, relations, subject, predicate, object.

Scene:
{chunk.chunk.context.get('scene_id')}

Text:
{chunk.chunk.text}

Return ONLY valid JSON:
{{
  "events": ["event description 1", "event description 2"],
  "relations": [
    {{"subject": "CharacterA", "predicate": "relation", "object": "CharacterB"}}
  ]
}}
"""
        result = await llm_func(prompt)
        chunk.data = result
        return chunk
