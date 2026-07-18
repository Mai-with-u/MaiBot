from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import time

from src.common.logger import get_logger

from ...storage import VectorStore
from ...utils import profile_policy
from ...utils.hash import compute_hash, normalize_text
from ...utils.metadata import coerce_metadata_dict
from ...utils.relation_write_service import RelationWriteService
from ...utils.runtime_payloads import (
    build_source,
    merge_tokens,
    optional_int,
    resolve_knowledge_type,
    time_meta,
    tokens,
)
from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")


class MemoryIngestService(KernelServiceBase):
    """协调段落元数据、向量、实体关系和后续派生任务的写入。"""

    async def _write_paragraph_vector_or_enqueue(
        self,
        *,
        paragraph_hash: str,
        content: str,
        context: str = "",
    ) -> Dict[str, Any]:
        """写入段落向量，并按配置决定失败时是否进入回填队列。

        ``success`` 表示主写入流程可以继续，不代表向量已经落库；调用方必须结合
        ``vector_written`` 和 ``queued`` 判断当前向量状态。
        """
        token = str(paragraph_hash or "").strip()
        text = str(content or "").strip()
        if not token or not text:
            return {
                "success": False,
                "vector_written": False,
                "queued": False,
                "warning": "",
                "detail": "invalid_paragraph_input",
            }

        allow_metadata_only = self._allow_metadata_only_write()

        target_store = self._paragraph_store()
        if target_store is None or self.embedding_manager is None:
            if not allow_metadata_only:
                raise RuntimeError("向量写入依赖未初始化")
            self._enqueue_paragraph_vector_backfill(token, error="vector_runtime_components_missing")
            return {
                "success": True,
                "vector_written": False,
                "queued": True,
                "warning": "vector_degraded_write",
                "detail": "vector_runtime_components_missing",
            }

        if self._is_embedding_degraded():
            if not allow_metadata_only:
                raise RuntimeError("embedding 处于降级态，metadata-only 写入已禁用")
            self._enqueue_paragraph_vector_backfill(token, error="embedding_degraded")
            return {
                "success": True,
                "vector_written": False,
                "queued": True,
                "warning": "vector_degraded_write",
                "detail": "embedding_degraded",
            }

        if token in target_store:
            return {
                "success": True,
                "vector_written": True,
                "queued": False,
                "warning": "",
                "detail": "vector_already_exists",
            }

        try:
            embedding = await self.embedding_manager.encode(text)
            if getattr(embedding, "ndim", 1) == 1:
                embedding = embedding.reshape(1, -1)
            target_store.add(vectors=embedding, ids=[token])
            return {
                "success": True,
                "vector_written": True,
                "queued": False,
                "warning": "",
                "detail": "",
            }
        except Exception as exc:
            error_text = str(exc)
            if self._embedding_fallback_enabled():
                self._set_embedding_degraded(active=True, reason=error_text[:500], checked_at=time.time())
            if not allow_metadata_only:
                raise
            self._enqueue_paragraph_vector_backfill(token, error=error_text)
            return {
                "success": True,
                "vector_written": False,
                "queued": True,
                "warning": "vector_degraded_write",
                "detail": f"{str(context or 'paragraph')} vector write failed: {error_text}",
            }

    async def ingest_summary(
        self,
        *,
        external_id: str,
        chat_id: str,
        text: str,
        participants: Optional[Sequence[str]] = None,
        time_start: Optional[float] = None,
        time_end: Optional[float] = None,
        tags: Optional[Sequence[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        respect_filter: bool = True,
        user_id: str = "",
        group_id: str = "",
    ) -> Dict[str, Any]:
        """写入已有摘要，或在正文为空时先从聊天流生成摘要。

        聊天过滤在初始化前执行。已有正文最终复用 ``ingest_text()``，保证摘要与
        普通文本使用相同的幂等、向量写入和派生任务语义。
        """
        external_token = str(external_id or "").strip() or compute_hash(f"chat_summary:{chat_id}:{text}")
        if self._is_chat_filtered(
            respect_filter=respect_filter,
            stream_id=chat_id,
            group_id=group_id,
            user_id=user_id,
        ):
            return {
                "success": True,
                "stored_ids": [],
                "skipped_ids": [external_token],
                "detail": "chat_filtered",
            }

        summary_meta = coerce_metadata_dict(metadata)
        summary_meta.setdefault("kind", "chat_summary")
        if not str(text or "").strip() or bool(summary_meta.get("generate_from_chat", False)):
            result = await self.summarize_chat_stream(
                chat_id=chat_id,
                context_length=optional_int(summary_meta.get("context_length")),
                include_personality=summary_meta.get("include_personality"),
                time_end=time_end,
                metadata={
                    **summary_meta,
                    "external_id": external_token,
                    "chat_id": str(chat_id or "").strip(),
                    "source_type": "chat_summary",
                },
            )
            result.setdefault("external_id", external_id)
            result.setdefault("chat_id", chat_id)
            return result
        return await self.ingest_text(
            external_id=external_id,
            source_type="chat_summary",
            text=text,
            chat_id=chat_id,
            participants=participants,
            time_start=time_start,
            time_end=time_end,
            tags=tags,
            metadata=summary_meta,
            respect_filter=respect_filter,
            user_id=user_id,
            group_id=group_id,
        )

    async def ingest_text(
        self,
        *,
        external_id: str,
        source_type: str,
        text: str,
        chat_id: str = "",
        person_ids: Optional[Sequence[str]] = None,
        participants: Optional[Sequence[str]] = None,
        timestamp: Optional[float] = None,
        time_start: Optional[float] = None,
        time_end: Optional[float] = None,
        tags: Optional[Sequence[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        entities: Optional[Sequence[str]] = None,
        relations: Optional[Sequence[Dict[str, Any]]] = None,
        respect_filter: bool = True,
        user_id: str = "",
        group_id: str = "",
    ) -> Dict[str, Any]:
        """按 ``external_id`` 幂等写入一条文本记忆及其派生数据。

        写入顺序为段落元数据、段落向量、实体与关系、外部幂等映射，随后再入队
        Episode 和人物画像任务。SQLite、向量库与图存储不构成单一事务；向量失败
        仅在配置允许时转入回填队列，其余异常会直接暴露给调用方。
        """
        content = normalize_text(text)
        external_token = str(external_id or "").strip() or compute_hash(f"{source_type}:{chat_id}:{content}")
        if self._is_chat_filtered(
            respect_filter=respect_filter,
            stream_id=chat_id,
            group_id=group_id,
            user_id=user_id,
        ):
            return {
                "success": True,
                "stored_ids": [],
                "skipped_ids": [external_token],
                "detail": "chat_filtered",
            }

        await self.initialize()
        assert self.metadata_store is not None
        assert self.vector_store is not None
        assert self.graph_store is not None
        assert self.embedding_manager is not None
        assert self.relation_write_service is not None

        if not content:
            return {"stored_ids": [], "skipped_ids": [external_token], "reason": "empty_text"}

        existing_ref = self.metadata_store.get_external_memory_ref(external_token)
        if existing_ref:
            return {
                "stored_ids": [],
                "skipped_ids": [str(existing_ref.get("paragraph_hash", "") or "")],
                "reason": "exists",
            }

        person_tokens = tokens(person_ids)
        participant_tokens = tokens(participants)
        entity_tokens = merge_tokens(entities, person_tokens, participant_tokens)
        source = build_source(source_type, chat_id, person_tokens)
        paragraph_meta = coerce_metadata_dict(metadata)
        paragraph_meta.update(
            {
                "external_id": external_token,
                "source_type": str(source_type or "").strip(),
                "chat_id": str(chat_id or "").strip(),
                "person_ids": person_tokens,
                "participants": participant_tokens,
                "tags": tokens(tags),
            }
        )
        warnings: List[str] = []

        paragraph_hash = self.metadata_store.add_paragraph(
            content=content,
            source=source,
            metadata=paragraph_meta,
            knowledge_type=resolve_knowledge_type(source_type),
            time_meta=time_meta(timestamp, time_start, time_end),
        )
        vector_result = await self._write_paragraph_vector_or_enqueue(
            paragraph_hash=paragraph_hash,
            content=content,
            context="ingest_text",
        )
        warning = str(vector_result.get("warning", "") or "").strip()
        if warning:
            warnings.append(warning)

        for name in entity_tokens:
            entity_hash = self.metadata_store.add_entity(name=name, source_paragraph=paragraph_hash)
            await self._ensure_entity_vector({"hash": entity_hash, "name": name})

        stored_relations: List[str] = []
        for row in [dict(item) for item in (relations or []) if isinstance(item, dict)]:
            confidence_value = row.get("confidence", 1.0)
            subject = str(row.get("subject", "") or "").strip()
            predicate = str(row.get("predicate", "") or "").strip()
            obj = str(row.get("object", "") or "").strip()
            if not (subject and predicate and obj):
                continue
            result = await self.relation_write_service.upsert_relation_with_vector(
                subject=subject,
                predicate=predicate,
                obj=obj,
                confidence=float(1.0 if confidence_value is None else confidence_value),
                source_paragraph=paragraph_hash,
                metadata=row.get("metadata")
                if isinstance(row.get("metadata"), dict)
                else {"external_id": external_token, "source_type": source_type},
                write_vector=self.relation_vectors_enabled,
            )
            self.metadata_store.link_paragraph_relation(paragraph_hash, result.hash_value)
            stored_relations.append(result.hash_value)

        self.metadata_store.upsert_external_memory_ref(
            external_id=external_token,
            paragraph_hash=paragraph_hash,
            source_type=source_type,
            metadata={"chat_id": chat_id, "person_ids": person_tokens},
        )
        if profile_policy.should_auto_enqueue_episode(self._cfg, source_type=source_type):
            self.metadata_store.enqueue_episode_pending(paragraph_hash, source=source)
        self._persist()
        for person_id in person_tokens:
            self._mark_person_active(person_id)
            self._enqueue_person_profile_refresh(person_id, reason=str(source_type or "ingest_text"))
        payload = {"stored_ids": [paragraph_hash, *stored_relations], "skipped_ids": []}
        if warnings:
            payload["warnings"] = warnings
            payload["detail"] = "vector_degraded_write"
        return payload

    async def process_episode_pending_batch(self, *, limit: int = 20, max_retry: int = 3) -> Dict[str, Any]:
        """领取一批 Episode 待处理段落，并统一回写行级与来源级状态。

        每条记录必须明确进入完成或失败状态；未返回结果的记录会记为失败，避免
        ``running`` 状态长期残留。批次结束后才持久化派生存储。
        """
        await self.initialize()
        assert self.metadata_store is not None
        assert self.episode_service is not None

        pending_rows = self.metadata_store.fetch_episode_pending_batch(
            limit=max(1, int(limit)), max_retry=max(1, int(max_retry))
        )
        if not pending_rows:
            return {"processed": 0, "episode_count": 0, "fallback_count": 0, "failed": 0}

        source_to_hashes: Dict[str, List[str]] = {}
        pending_hashes = [
            str(row.get("paragraph_hash", "") or "").strip()
            for row in pending_rows
            if str(row.get("paragraph_hash", "") or "").strip()
        ]
        for row in pending_rows:
            paragraph_hash = str(row.get("paragraph_hash", "") or "").strip()
            source = str(row.get("source", "") or "").strip()
            if not paragraph_hash or not source:
                continue
            source_to_hashes.setdefault(source, []).append(paragraph_hash)

        if pending_hashes:
            self.metadata_store.mark_episode_pending_running(pending_hashes)

        try:
            result = await self.episode_service.process_pending_rows(pending_rows)
        except Exception as exc:
            error = f"episode processing failed: {exc}"
            for hash_value in pending_hashes:
                try:
                    self.metadata_store.mark_episode_pending_failed(hash_value, error)
                except Exception as mark_exc:
                    logger.warning(f"回写 Episode 待处理项失败状态异常: hash={hash_value}, error={mark_exc}")
            for source in source_to_hashes:
                try:
                    self.metadata_store.mark_episode_source_failed(source, error)
                except Exception as mark_exc:
                    logger.warning(f"回写 Episode 来源失败状态异常: source={source}, error={mark_exc}")
            raise
        done_hashes = [str(item or "").strip() for item in result.get("done_hashes", []) if str(item or "").strip()]
        failed_hashes = {
            str(hash_value or "").strip(): str(error or "").strip()
            for hash_value, error in (result.get("failed_hashes", {}) or {}).items()
            if str(hash_value or "").strip()
        }

        if done_hashes:
            self.metadata_store.mark_episode_pending_done(done_hashes)
        for hash_value, error in failed_hashes.items():
            self.metadata_store.mark_episode_pending_failed(hash_value, error)

        untouched = [
            hash_value
            for hash_value in pending_hashes
            if hash_value not in set(done_hashes) and hash_value not in failed_hashes
        ]
        for hash_value in untouched:
            self.metadata_store.mark_episode_pending_failed(
                hash_value, "episode processing finished without explicit status"
            )

        for source, paragraph_hashes in source_to_hashes.items():
            counts = self.metadata_store.get_episode_pending_status_counts(source)
            if counts.get("failed", 0) > 0:
                source_error = next(
                    (failed_hashes.get(hash_value) for hash_value in paragraph_hashes if failed_hashes.get(hash_value)),
                    "episode pending source contains failed rows",
                )
                self.metadata_store.mark_episode_source_failed(
                    source, str(source_error or "episode pending source contains failed rows")
                )
            elif counts.get("pending", 0) == 0 and counts.get("running", 0) == 0:
                self.metadata_store.mark_episode_source_done(source)

        self._persist()
        return {
            "processed": len(done_hashes) + len(failed_hashes),
            "episode_count": int(result.get("episode_count") or 0),
            "fallback_count": int(result.get("fallback_count") or 0),
            "failed": len(failed_hashes) + len(untouched),
            "group_count": int(result.get("group_count") or 0),
            "missing_count": int(result.get("missing_count") or 0),
        }

    async def _ensure_vector_for_text(
        self,
        *,
        item_hash: str,
        text: str,
        vector_store: Optional[VectorStore] = None,
    ) -> bool:
        target_store = vector_store or self.vector_store
        if target_store is None or self.embedding_manager is None:
            return False
        token = str(item_hash or "").strip()
        content = str(text or "").strip()
        if not token or not content:
            return False
        embedding = await self.embedding_manager.encode([content])
        if getattr(embedding, "ndim", 1) == 1:
            embedding = embedding.reshape(1, -1)
        if getattr(embedding, "size", 0) <= 0:
            return False
        try:
            target_store.add(embedding, [token])
            return True
        except Exception as exc:
            logger.warning(f"重建向量失败: {exc}")
            return False

    async def _ensure_relation_vector(self, relation: Dict[str, Any]) -> bool:
        if not bool(self.relation_vectors_enabled):
            return False
        relation_service = self.relation_write_service
        if relation_service is not None:
            result = await relation_service.ensure_relation_vector(
                hash_value=str(relation.get("hash", "") or ""),
                subject=str(relation.get("subject", "") or "").strip(),
                predicate=str(relation.get("predicate", "") or "").strip(),
                obj=str(relation.get("object", "") or "").strip(),
                typed_id=self._dual_vector_pools_enabled(),
            )
            return bool(result.vector_written or result.vector_already_exists)
        return await self._ensure_vector_for_text(
            item_hash=str(relation.get("hash", "") or ""),
            text=RelationWriteService.build_relation_vector_text(
                str(relation.get("subject", "") or "").strip(),
                str(relation.get("predicate", "") or "").strip(),
                str(relation.get("object", "") or "").strip(),
            ),
        )

    async def _ensure_paragraph_vector(self, paragraph: Dict[str, Any]) -> bool:
        return await self._ensure_vector_for_text(
            item_hash=str(paragraph.get("hash", "") or ""),
            text=str(paragraph.get("content", "") or ""),
            vector_store=self._paragraph_store(),
        )

    async def _ensure_entity_vector(self, entity: Dict[str, Any]) -> bool:
        if self._dual_vector_pools_enabled():
            return await self._ensure_vector_for_text(
                item_hash=self._graph_vector_id("entity", str(entity.get("hash", "") or "")),
                text=str(entity.get("name", "") or ""),
                vector_store=self._graph_vector_store(),
            )
        return await self._ensure_vector_for_text(
            item_hash=str(entity.get("hash", "") or ""),
            text=str(entity.get("name", "") or ""),
        )
