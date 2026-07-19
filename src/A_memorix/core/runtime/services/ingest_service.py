from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

import asyncio
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

_TRUSTED_FACT_ORIGINS = {"manual_confirmed", "server_verified", "trusted_import"}


class MemoryIngestService(KernelServiceBase):
    """协调段落元数据、向量、实体关系和后续派生任务的写入。"""

    def _write_person_fact_claims(
        self,
        *,
        paragraph_hash: str,
        content: str,
        person_ids: Sequence[str],
        metadata: Dict[str, Any],
        timestamp: Optional[float],
    ) -> List[str]:
        """把原子人物事实登记为有证据的结构化 claim。

        文本始终采用已落库段落正文。只有人工确认、服务端校验或可信迁移
        明确标记的结构化 claim 才能进入稳定投影；其余模型改写只登记为
        summary_derived + uncertain，不得取代稳定事实。
        """

        assert self.metadata_store is not None
        paragraph_token = str(paragraph_hash or "").strip()
        statement = str(content or "").strip()
        if not paragraph_token or not statement:
            raise ValueError("人物事实 claim 缺少段落证据")

        raw_claim = metadata.get("fact_claim")
        claim_spec = dict(raw_claim) if isinstance(raw_claim, dict) else {}
        evidence_source = str(metadata.get("evidence_source", "") or "").strip()
        trust = str(claim_spec.get("trust", "") or "").strip().casefold()
        trusted = trust in _TRUSTED_FACT_ORIGINS
        default_authority = {
            "manual_confirmed": "manual",
            "server_verified": "direct_user",
            "trusted_import": "imported",
        }.get(trust, "summary_derived")
        claim_ids: List[str] = []
        for person_id in person_ids:
            person_token = str(person_id or "").strip()
            if not person_token:
                continue
            result = self.metadata_store.upsert_fact_claim(
                scope_type="person",
                scope_id=person_token,
                fact_key=(
                    str(claim_spec.get("fact_key", "") or f"statement:{paragraph_token}")
                    if trusted
                    else f"statement:{paragraph_token}"
                ),
                value_text=statement,
                polarity=str(claim_spec.get("polarity", "positive") or "positive") if trusted else "positive",
                cardinality=str(claim_spec.get("cardinality", "set") or "set") if trusted else "set",
                stability=str(claim_spec.get("stability", "stable") or "stable") if trusted else "uncertain",
                profile_section=(
                    str(claim_spec.get("profile_section", "stable_facts") or "stable_facts")
                    if trusted
                    else "uncertain_notes"
                ),
                authority=(
                    str(claim_spec.get("authority", default_authority) or default_authority)
                    if trusted
                    else "summary_derived"
                ),
                confidence=(
                    float(claim_spec.get("confidence", 1.0) or 1.0)
                    if trusted
                    else min(0.5, float(claim_spec.get("confidence", 0.5) or 0.5))
                ),
                valid_from=claim_spec.get("valid_from") if trusted else None,
                valid_to=claim_spec.get("valid_to") if trusted else None,
                evidence_type="paragraph",
                evidence_id=paragraph_token,
                evidence_stance="support",
                evidence_weight=1.0,
                evidence_metadata={
                    "source_type": "person_fact",
                    "evidence_message_ids": metadata.get("evidence_message_ids", []),
                    "evidence_source": evidence_source,
                    "trust": trust,
                },
                supersedes_claim_ids=claim_spec.get("supersedes_claim_ids")
                if trusted and isinstance(claim_spec.get("supersedes_claim_ids"), list)
                else [],
                reason=str(claim_spec.get("reason", "") or "person_fact_ingest"),
                observed_at=timestamp,
            )
            claim_ids.append(str(result["claim_id"]))
        return claim_ids

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
        if target_store is None:
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

        if token in target_store:
            return {
                "success": True,
                "vector_written": True,
                "queued": False,
                "warning": "",
                "detail": "vector_already_exists",
            }

        try:
            restored = target_store.restore([token])
            if restored:
                if token not in target_store:
                    raise RuntimeError("向量恢复后成员校验失败")
                return {
                    "success": True,
                    "vector_written": True,
                    "queued": False,
                    "warning": "",
                    "detail": "vector_restored",
                }
        except Exception as exc:
            error_text = str(exc)
            if not allow_metadata_only:
                raise
            self._enqueue_paragraph_vector_backfill(token, error=error_text)
            return {
                "success": True,
                "vector_written": False,
                "queued": True,
                "warning": "vector_degraded_write",
                "detail": f"{str(context or 'paragraph')} vector restore failed: {error_text}",
            }

        if self.embedding_manager is None:
            if not allow_metadata_only:
                raise RuntimeError("embedding 依赖未初始化")
            self._enqueue_paragraph_vector_backfill(token, error="embedding_runtime_component_missing")
            return {
                "success": True,
                "vector_written": False,
                "queued": True,
                "warning": "vector_degraded_write",
                "detail": "embedding_runtime_component_missing",
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

        try:
            embedding = await self.embedding_manager.encode(text)
            if getattr(embedding, "ndim", 1) == 1:
                embedding = embedding.reshape(1, -1)
            target_store.add(vectors=embedding, ids=[token])
            if token not in target_store:
                raise RuntimeError("段落向量写入后成员校验失败")
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

        fact_claim_ids: List[str] = []
        if str(source_type or "").strip() == "person_fact":
            fact_claim_ids = self._write_person_fact_claims(
                paragraph_hash=paragraph_hash,
                content=content,
                person_ids=person_tokens,
                metadata=paragraph_meta,
                timestamp=timestamp,
            )
            if fact_claim_ids:
                self.metadata_store.update_paragraph_metadata(
                    paragraph_hash,
                    {"fact_claim_ids": fact_claim_ids},
                    merge=True,
                )

        self.metadata_store.upsert_external_memory_ref(
            external_id=external_token,
            paragraph_hash=paragraph_hash,
            source_type=source_type,
            metadata={"chat_id": chat_id, "person_ids": person_tokens},
        )
        self._persist()
        for person_id in person_tokens:
            self._mark_person_active(person_id)
            self._enqueue_person_profile_refresh(person_id, reason=str(source_type or "ingest_text"))
        payload = {
            "stored_ids": [paragraph_hash, *stored_relations],
            "skipped_ids": [],
            "fact_claim_ids": fact_claim_ids,
        }
        if warnings:
            payload["warnings"] = warnings
            payload["detail"] = "vector_degraded_write"
        return payload

    async def _maintain_episode_source_lease(
        self,
        *,
        source: str,
        lease_token: str,
        claimed_revision: int,
        generation_hash: str,
        lease_seconds: float,
        stop_event: asyncio.Event,
    ) -> bool:
        """在模型规划期间续期当前租约；租约失效或revision变化时立即停止。"""
        assert self.metadata_store is not None
        safe_lease_seconds = max(1.0, float(lease_seconds))
        heartbeat_interval = max(0.1, safe_lease_seconds / 3.0)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=heartbeat_interval)
            except asyncio.TimeoutError:
                renewed = self.metadata_store.renew_episode_source_rebuild_lease(
                    source,
                    lease_token=lease_token,
                    claimed_revision=claimed_revision,
                    generation_hash=generation_hash,
                    lease_seconds=safe_lease_seconds,
                )
                if not renewed:
                    return False
        return True

    async def process_episode_source_rebuild_batch(
        self,
        *,
        sources: Optional[Sequence[str]] = None,
        limit: int = 20,
        max_retry: int = 3,
        lease_seconds: float = 1800.0,
        max_wait_seconds: float = 60.0,
    ) -> Dict[str, Any]:
        """按来源领取物化任务，并以revision CAS发布完整Episode快照。"""
        safe_max_retry = int(max_retry)
        if safe_max_retry < 1:
            raise ValueError("max_retry 必须至少为1")
        await self.initialize()
        assert self.metadata_store is not None
        assert self.episode_service is not None

        generation = self.episode_service.generation_signature()
        generation_hash = self.episode_service.generation_hash(generation)
        source_scope = list(dict.fromkeys(str(item or "").strip() for item in (sources or []) if str(item or "").strip()))
        if sources is not None and not source_scope:
            return {
                "processed": 0,
                "rebuilt": 0,
                "superseded": 0,
                "failed": 0,
                "episode_count": 0,
                "fallback_count": 0,
                "items": [],
                "failures": [],
                "unfinished": 0,
                "unfinished_items": [],
            }
        rebuilt_items: List[Dict[str, Any]] = []
        failures: List[Dict[str, str]] = []
        unfinished_items: List[Dict[str, str]] = []
        superseded = 0
        fallback_count = 0
        episode_count = 0

        for _ in range(max(1, int(limit))):
            claims = self.metadata_store.claim_episode_source_rebuild_batch(
                generation_hash=generation_hash,
                sources=source_scope if sources is not None else None,
                limit=1,
                max_retry=safe_max_retry,
                lease_seconds=max(1.0, float(lease_seconds)),
                max_wait_seconds=max(0.0, float(max_wait_seconds)),
            )
            if not claims:
                break
            claim = claims[0]
            source = str(claim.get("source", "") or "").strip()
            if sources is not None and source in source_scope:
                source_scope.remove(source)
            lease_token = str(claim.get("lease_token", "") or "").strip()
            claimed_revision = int(claim.get("claimed_revision", 0) or 0)
            try:
                heartbeat_stop = asyncio.Event()
                heartbeat_task = asyncio.create_task(
                    self._maintain_episode_source_lease(
                        source=source,
                        lease_token=lease_token,
                        claimed_revision=claimed_revision,
                        generation_hash=generation_hash,
                        lease_seconds=lease_seconds,
                        stop_event=heartbeat_stop,
                    )
                )
                try:
                    source_type = source.split(":", 1)[0]
                    if profile_policy.should_auto_enqueue_episode(self._cfg, source_type=source_type):
                        plan = await self.episode_service.plan_source_rebuild(
                            source,
                            segmentation_generation=generation,
                        )
                    else:
                        plan = {
                            "source": source,
                            "payloads": [],
                            "episode_count": 0,
                            "fallback_count": 0,
                            "group_count": 0,
                            "paragraph_count": 0,
                            "reused_group_count": 0,
                            "reused_episode_count": 0,
                            "recomputed_group_count": 0,
                            "generation_hash": generation_hash,
                        }
                finally:
                    heartbeat_stop.set()
                    try:
                        await heartbeat_task
                    except Exception as heartbeat_exc:
                        logger.warning(f"Episode 来源租约心跳异常: source={source}, error={heartbeat_exc}")
                publish_result = self.metadata_store.publish_episode_source_rebuild(
                    source,
                    lease_token=lease_token,
                    claimed_revision=claimed_revision,
                    generation_hash=generation_hash,
                    episodes_payloads=list(plan.get("payloads") or []),
                )
                if not bool(publish_result.get("published")):
                    is_superseded = bool(publish_result.get("superseded"))
                    superseded += int(is_superseded)
                    unfinished_items.append(
                        {
                            "source": source,
                            "reason": "superseded" if is_superseded else "lease_lost_or_claim_mismatch",
                        }
                    )
                    continue
                item = {key: value for key, value in plan.items() if key != "payloads"}
                item.update(publish_result)
                rebuilt_items.append(item)
                episode_count += int(publish_result.get("episode_count") or 0)
                fallback_count += int(plan.get("fallback_count") or 0)
            except Exception as exc:
                error = str(exc)[:500]
                retry_count = max(0, int(claim.get("retry_count", 0) or 0))
                try:
                    self.metadata_store.fail_episode_source_rebuild(
                        source,
                        lease_token=lease_token,
                        claimed_revision=claimed_revision,
                        error=error,
                        retry_backoff_seconds=min(300.0, 5.0 * (2**retry_count)),
                    )
                except Exception as mark_exc:
                    logger.warning(f"Episode 来源失败状态回写异常: source={source}, error={mark_exc}")
                failures.append({"source": source, "error": error})

        if sources is not None:
            unfinished_items.extend(
                {"source": source, "reason": "not_claimed"}
                for source in source_scope
            )

        if rebuilt_items:
            self._persist()
        return {
            "processed": len(rebuilt_items) + len(failures) + len(unfinished_items),
            "rebuilt": len(rebuilt_items),
            "superseded": superseded,
            "unfinished": len(unfinished_items),
            "failed": len(failures),
            "episode_count": episode_count,
            "fallback_count": fallback_count,
            "items": rebuilt_items,
            "failures": failures,
            "unfinished_items": unfinished_items,
        }

    async def _ensure_vector_for_text(
        self,
        *,
        item_hash: str,
        text: str,
        vector_store: Optional[VectorStore] = None,
        before_vector_write: Optional[Callable[[], None]] = None,
    ) -> bool:
        target_store = vector_store or self.vector_store
        if target_store is None:
            return False
        token = str(item_hash or "").strip()
        content = str(text or "").strip()
        if not token or not content:
            return False
        if token in target_store:
            return True
        try:
            tombstoned = target_store.is_tombstoned(token)
        except Exception as exc:
            logger.warning(f"检查待恢复向量状态失败: {exc}")
            return False
        if tombstoned:
            if before_vector_write is not None:
                before_vector_write()
            try:
                restored = target_store.restore([token])
                if restored != 1 or token not in target_store:
                    raise RuntimeError("向量恢复后成员校验失败")
                return True
            except Exception as exc:
                logger.warning(f"重建向量失败: {exc}")
                return False

        try:
            if self.embedding_manager is None:
                return False
            embedding = await self.embedding_manager.encode([content])
            if getattr(embedding, "ndim", 1) == 1:
                embedding = embedding.reshape(1, -1)
            if getattr(embedding, "size", 0) <= 0:
                return False
        except Exception as exc:
            logger.warning(f"生成待恢复向量失败: {exc}")
            return False

        if before_vector_write is not None:
            before_vector_write()
        try:
            target_store.add(embedding, [token])
            if token not in target_store:
                raise RuntimeError("向量写入后成员校验失败")
            return True
        except Exception as exc:
            logger.warning(f"重建向量失败: {exc}")
            return False

    async def _ensure_relation_vector(
        self,
        relation: Dict[str, Any],
        *,
        before_vector_write: Optional[Callable[[], None]] = None,
    ) -> bool:
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
                before_vector_write=before_vector_write,
            )
            return bool(result.vector_written or result.vector_already_exists)
        return await self._ensure_vector_for_text(
            item_hash=str(relation.get("hash", "") or ""),
            text=RelationWriteService.build_relation_vector_text(
                str(relation.get("subject", "") or "").strip(),
                str(relation.get("predicate", "") or "").strip(),
                str(relation.get("object", "") or "").strip(),
            ),
            before_vector_write=before_vector_write,
        )

    async def _ensure_paragraph_vector(
        self,
        paragraph: Dict[str, Any],
        *,
        before_vector_write: Optional[Callable[[], None]] = None,
    ) -> bool:
        return await self._ensure_vector_for_text(
            item_hash=str(paragraph.get("hash", "") or ""),
            text=str(paragraph.get("content", "") or ""),
            vector_store=self._paragraph_store(),
            before_vector_write=before_vector_write,
        )

    async def _ensure_entity_vector(
        self,
        entity: Dict[str, Any],
        *,
        before_vector_write: Optional[Callable[[], None]] = None,
    ) -> bool:
        if self._dual_vector_pools_enabled():
            return await self._ensure_vector_for_text(
                item_hash=self._graph_vector_id("entity", str(entity.get("hash", "") or "")),
                text=str(entity.get("name", "") or ""),
                vector_store=self._graph_vector_store(),
                before_vector_write=before_vector_write,
            )
        return await self._ensure_vector_for_text(
            item_hash=str(entity.get("hash", "") or ""),
            text=str(entity.get("name", "") or ""),
            before_vector_write=before_vector_write,
        )
