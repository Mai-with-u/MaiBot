from __future__ import annotations

from typing import Any, Sequence

import asyncio

from src.A_memorix.core.image.component_paths import build_chat_external_ref, iter_message_image_components
from src.chat.message_receive.message import SessionMessage
from src.config.config import global_config
from src.services.memory_service import memory_service

IMAGE_MEMORY_REFERENCE_MARKER = "【图片记忆-内部参考】"


class ImageMemoryInjector:
    """对当前消息中的图片执行同图与相似图召回。"""

    async def build_injection_message(
        self,
        *,
        session_id: str,
        source_messages: Sequence[SessionMessage],
    ) -> str:
        config = global_config.a_memorix.image_memory
        if not config.enabled:
            return ""
        queries: list[tuple[str, str]] = []
        for message in reversed(source_messages):
            for component_path, component in iter_message_image_components(message.raw_message.components):
                content_hash = str(component.binary_hash or "").strip()
                if not content_hash:
                    continue
                external_ref = build_chat_external_ref(
                    chat_id=session_id,
                    message_id=str(message.message_id),
                    component_path=component_path,
                )
                queries.append((content_hash, external_ref))
            if queries:
                break
        if not queries:
            return ""

        async def search_one(content_hash: str, external_ref: str) -> dict[str, Any]:
            return await memory_service.image_memory(
                action="search",
                content_hash=content_hash,
                current_external_ref=external_ref,
                chat_id=session_id,
                session_id=session_id,
                candidate_limit=config.candidate_limit,
                similarity_threshold=config.similarity_threshold,
            )

        # 沿用记忆服务与模型请求的超时机制，避免把检索超时当作没有匹配。
        results = await asyncio.gather(*(search_one(*query) for query in queries[:3]))

        lines = [IMAGE_MEMORY_REFERENCE_MARKER, "以下内容来自当前图片的历史同图或相似图记录，仅作为回答依据。"]
        seen: set[str] = set()
        for result in results:
            if result.get("success") is False:
                raise RuntimeError(str(result.get("error") or "图片记忆检索失败"))
            for hit in result.get("hits") or []:
                score = float(hit.get("similarity") or 0.0)
                match_kind = "同一图片" if hit.get("match_kind") == "exact_hash" else "相似图片"
                for observation in hit.get("observations") or []:
                    text = " ".join(str(observation.get("text") or "").split())
                    key = f"observation:{text}"
                    if text and key not in seen:
                        seen.add(key)
                        lines.append(f"- {match_kind} {score:.3f}：{text}")
                for memory in hit.get("related_memories") or []:
                    text = " ".join(str(memory.get("content") or "").split())
                    key = f"memory:{memory.get('target_type')}:{memory.get('target_id')}"
                    if text and key not in seen:
                        seen.add(key)
                        lines.append(f"- 关联记忆：{text}")
        return "\n".join(lines) if len(lines) > 2 else ""


image_memory_injector = ImageMemoryInjector()
