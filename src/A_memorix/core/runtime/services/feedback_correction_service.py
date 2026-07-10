from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

import asyncio
import json
import time

from src.common.logger import get_logger
from src.services import message_service as message_api
from src.services.llm_service import LLMServiceClient

from ...utils import profile_policy
from ...utils.feedback_policy import (
    feedback_cfg_auto_apply_threshold,
    feedback_cfg_batch_size,
    feedback_cfg_check_interval_seconds,
    feedback_cfg_enabled,
    feedback_cfg_episode_rebuild_enabled,
    feedback_cfg_max_messages,
    feedback_cfg_paragraph_mark_enabled,
    feedback_cfg_prefilter_enabled,
    feedback_cfg_profile_refresh_enabled,
    feedback_cfg_reconcile_batch_size,
    feedback_cfg_reconcile_interval_seconds,
    feedback_cfg_window_hours,
    feedback_cfg_window_label,
    feedback_noise,
    feedback_signal_tokens,
)
from ...utils.hash import compute_hash
from ...utils.person_profile_service import PersonProfileService
from ...utils.runtime_payloads import coerce_datetime, merge_tokens, safe_json_loads, tokens
from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")


class MemoryFeedbackCorrectionService(KernelServiceBase):
    async def memory_feedback_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store is not None

        act = str(action or "").strip().lower()
        if act == "list":
            items = self.metadata_store.list_feedback_tasks(
                limit=max(1, int(kwargs.get("limit", 50) or 50)),
                statuses=tokens(kwargs.get("status") or kwargs.get("statuses")),
                rollback_statuses=tokens(kwargs.get("rollback_status") or kwargs.get("rollback_statuses")),
                query=str(kwargs.get("query", "") or "").strip(),
            )
            return {
                "success": True,
                "items": [self._build_feedback_task_summary(task) for task in items],
                "count": len(items),
            }

        if act == "get":
            task = self.metadata_store.get_feedback_task_by_id(int(kwargs.get("task_id", 0) or 0))
            if task is None:
                return {"success": False, "error": "反馈纠错任务不存在"}
            return {"success": True, "task": self._build_feedback_task_detail(task)}

        if act == "rollback":
            return await self._rollback_feedback_task(
                task_id=int(kwargs.get("task_id", 0) or 0),
                requested_by=str(kwargs.get("requested_by", "") or "").strip(),
                reason=str(kwargs.get("reason", "") or "").strip(),
            )

        return {"success": False, "error": f"不支持的 feedback action: {act}"}

    def _resolve_feedback_related_person_ids(
        self,
        *,
        old_relation_rows: Sequence[Dict[str, Any]],
        corrected_relations: Sequence[Dict[str, Any]],
    ) -> List[str]:
        candidates = tokens(
            value
            for row in list(old_relation_rows) + list(corrected_relations)
            if isinstance(row, dict)
            for value in (row.get("subject"), row.get("object"))
        )
        resolved: List[str] = []
        seen = set()
        for candidate in candidates:
            person_id = PersonProfileService.resolve_person_id(candidate)
            if not person_id or person_id in seen:
                continue
            seen.add(person_id)
            resolved.append(person_id)
        return resolved

    def _mark_feedback_stale_paragraphs(
        self,
        *,
        task_id: int,
        query_tool_id: str,
        relation_hashes: Sequence[str],
        reason: str,
    ) -> Dict[str, List[str]]:
        if self.metadata_store is None or not feedback_cfg_paragraph_mark_enabled():
            return {}

        relation_tokens = tokens(relation_hashes)
        paragraph_map = self.metadata_store.get_paragraph_hashes_by_relation_hashes(relation_tokens)
        for relation_hash, paragraph_hashes in paragraph_map.items():
            for paragraph_hash in paragraph_hashes:
                self.metadata_store.upsert_paragraph_stale_relation_mark(
                    paragraph_hash=paragraph_hash,
                    relation_hash=relation_hash,
                    query_tool_id=query_tool_id,
                    task_id=task_id,
                    reason=reason,
                    source_type="feedback_correction",
                    source_id=str(task_id),
                    source_operation_id=f"feedback_correction:{task_id}:{paragraph_hash}:{relation_hash}",
                )
        return paragraph_map

    def _enqueue_feedback_episode_rebuilds(
        self,
        *,
        paragraph_hashes: Sequence[str],
        session_id: str,
        include_correction_source: bool,
    ) -> List[str]:
        if self.metadata_store is None or not feedback_cfg_episode_rebuild_enabled():
            return []

        sources = tokens(
            row.get("source", "")
            for row in self._load_paragraph_rows(paragraph_hashes)
            if isinstance(row, dict)
        )
        correction_source = self._chat_source(session_id)
        if include_correction_source and correction_source:
            sources = merge_tokens(sources, [correction_source])

        queued: List[str] = []
        for source in sources:
            if self.metadata_store.enqueue_episode_source_rebuild(source, reason="feedback_correction"):
                queued.append(source)
        return queued

    def _enqueue_feedback_profile_refreshes(
        self,
        *,
        person_ids: Sequence[str],
        query_tool_id: str,
    ) -> List[str]:
        if self.metadata_store is None or not feedback_cfg_profile_refresh_enabled():
            return []
        queued: List[str] = []
        for person_id in tokens(person_ids):
            payload = self.metadata_store.enqueue_person_profile_refresh(
                person_id=person_id,
                reason="feedback_correction",
                source_query_tool_id=query_tool_id,
            )
            if isinstance(payload, dict):
                queued.append(person_id)
        return queued

    @staticmethod
    def _feedback_affected_counts(task: Dict[str, Any]) -> Dict[str, int]:
        decision_payload = task.get("decision_payload") if isinstance(task.get("decision_payload"), dict) else {}
        apply_result = decision_payload.get("apply_result") if isinstance(decision_payload.get("apply_result"), dict) else {}
        rollback_plan = task.get("rollback_plan") if isinstance(task.get("rollback_plan"), dict) else {}
        corrected_write = rollback_plan.get("corrected_write") if isinstance(rollback_plan.get("corrected_write"), dict) else {}
        return {
            "relations": len(list(apply_result.get("relation_hashes") or rollback_plan.get("forgotten_relations") or [])),
            "stale_paragraphs": len(list(apply_result.get("stale_paragraph_hashes") or rollback_plan.get("stale_marks") or [])),
            "episode_sources": len(list(apply_result.get("episode_rebuild_sources") or rollback_plan.get("episode_sources") or [])),
            "profile_person_ids": len(list(apply_result.get("profile_refresh_person_ids") or rollback_plan.get("profile_person_ids") or [])),
            "correction_paragraphs": len(list(corrected_write.get("paragraph_hashes") or [])),
            "corrected_relations": len(list(corrected_write.get("corrected_relations") or [])),
        }

    def _build_feedback_rollback_plan_summary(self, rollback_plan: Dict[str, Any]) -> Dict[str, Any]:
        corrected_write = rollback_plan.get("corrected_write") if isinstance(rollback_plan.get("corrected_write"), dict) else {}
        return {
            "forgotten_relations": list(rollback_plan.get("forgotten_relations") or []),
            "corrected_write": corrected_write,
            "stale_marks": list(rollback_plan.get("stale_marks") or []),
            "episode_sources": tokens(rollback_plan.get("episode_sources")),
            "profile_person_ids": tokens(rollback_plan.get("profile_person_ids")),
            "affected_counts": {
                "forgotten_relations": len(list(rollback_plan.get("forgotten_relations") or [])),
                "corrected_relations": len(list(corrected_write.get("corrected_relations") or [])),
                "correction_paragraphs": len(list(corrected_write.get("paragraph_hashes") or [])),
                "stale_marks": len(list(rollback_plan.get("stale_marks") or [])),
                "episode_sources": len(tokens(rollback_plan.get("episode_sources"))),
                "profile_person_ids": len(tokens(rollback_plan.get("profile_person_ids"))),
            },
        }

    def _build_feedback_task_summary(self, task: Dict[str, Any]) -> Dict[str, Any]:
        query_snapshot = task.get("query_snapshot") if isinstance(task.get("query_snapshot"), dict) else {}
        decision_payload = task.get("decision_payload") if isinstance(task.get("decision_payload"), dict) else {}
        return {
            "task_id": int(task.get("id", 0) or 0),
            "query_tool_id": str(task.get("query_tool_id", "") or "").strip(),
            "session_id": str(task.get("session_id", "") or "").strip(),
            "query_text": str(query_snapshot.get("query", "") or "").strip(),
            "query_timestamp": task.get("query_timestamp"),
            "task_status": str(task.get("status", "") or "").strip().lower(),
            "decision": str(decision_payload.get("decision", "") or "").strip().lower(),
            "decision_confidence": float(decision_payload.get("confidence", 0.0) or 0.0),
            "feedback_message_count": int(decision_payload.get("feedback_message_count", 0) or 0),
            "rollback_status": str(task.get("rollback_status", "") or "none").strip().lower() or "none",
            "affected_counts": self._feedback_affected_counts(task),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
        }

    def _build_feedback_task_detail(self, task: Dict[str, Any]) -> Dict[str, Any]:
        detail = self._build_feedback_task_summary(task)
        detail.update(
            {
                "query_snapshot": task.get("query_snapshot") if isinstance(task.get("query_snapshot"), dict) else {},
                "decision_payload": task.get("decision_payload") if isinstance(task.get("decision_payload"), dict) else {},
                "rollback_plan_summary": self._build_feedback_rollback_plan_summary(
                    task.get("rollback_plan") if isinstance(task.get("rollback_plan"), dict) else {}
                ),
                "rollback_result": task.get("rollback_result") if isinstance(task.get("rollback_result"), dict) else {},
                "rollback_error": str(task.get("rollback_error", "") or "").strip(),
                "rollback_requested_by": str(task.get("rollback_requested_by", "") or "").strip(),
                "rollback_reason": str(task.get("rollback_reason", "") or "").strip(),
                "rollback_requested_at": task.get("rollback_requested_at"),
                "rolled_back_at": task.get("rolled_back_at"),
                "action_logs": self.metadata_store.list_feedback_action_logs(int(task.get("id", 0) or 0))
                if self.metadata_store is not None
                else [],
            }
        )
        return detail

    def _soft_delete_feedback_correction_paragraphs(self, paragraph_hashes: Sequence[str]) -> Dict[str, Any]:
        assert self.metadata_store is not None
        hashes = tokens(paragraph_hashes)
        if not hashes:
            return {"deleted_hashes": [], "deleted_external_refs": []}

        paragraph_rows = {hash_value: self.metadata_store.get_paragraph(hash_value) for hash_value in hashes}
        self.metadata_store.mark_as_deleted(hashes, "paragraph")
        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"DELETE FROM paragraph_entities WHERE paragraph_hash IN ({','.join(['?'] * len(hashes))})",
            tuple(hashes),
        )
        cursor.execute(
            f"DELETE FROM paragraph_relations WHERE paragraph_hash IN ({','.join(['?'] * len(hashes))})",
            tuple(hashes),
        )
        conn.commit()
        deleted_external_refs = self.metadata_store.delete_external_memory_refs_by_paragraphs(hashes)
        return {
            "deleted_hashes": hashes,
            "paragraph_rows": paragraph_rows,
            "deleted_external_refs": deleted_external_refs,
        }

    async def _rollback_feedback_task(
        self,
        *,
        task_id: int,
        requested_by: str,
        reason: str,
    ) -> Dict[str, Any]:
        """依据已落库的回退计划补偿一项已应用的反馈纠错任务。

        状态按 ``running``、``rolled_back`` 或 ``error`` 收敛；已完成回退的任务保持
        幂等。补偿会恢复被遗忘关系、移除纠正写入和过期标记，并重新入队 Episode
        与人物画像派生任务，最后重建图和持久化结果。
        """
        await self.initialize()
        assert self.metadata_store is not None

        task = self.metadata_store.get_feedback_task_by_id(task_id)
        if task is None:
            return {"success": False, "error": "反馈纠错任务不存在"}
        if str(task.get("status", "") or "").strip().lower() != "applied":
            return {"success": False, "error": "仅 applied 的反馈纠错任务允许回退"}
        rollback_status = str(task.get("rollback_status", "") or "none").strip().lower()
        if rollback_status == "rolled_back":
            return {
                "success": True,
                "already_rolled_back": True,
                "task": self._build_feedback_task_detail(task),
                "result": task.get("rollback_result") if isinstance(task.get("rollback_result"), dict) else {},
            }
        if rollback_status == "running":
            return {"success": False, "error": "该反馈纠错任务正在回退中", "task": self._build_feedback_task_detail(task)}

        query_tool_id = str(task.get("query_tool_id", "") or "").strip()
        rollback_plan = task.get("rollback_plan") if isinstance(task.get("rollback_plan"), dict) else {}
        if not rollback_plan:
            running_task = self.metadata_store.mark_feedback_task_rollback_running(
                task_id=task_id,
                requested_by=requested_by,
                reason=reason,
            )
            if running_task is None:
                latest_task = self.metadata_store.get_feedback_task_by_id(task_id)
                latest_status = str((latest_task or {}).get("rollback_status", "") or "none").strip().lower()
                if latest_status == "running":
                    return {
                        "success": False,
                        "error": "该反馈纠错任务正在回退中",
                        "task": self._build_feedback_task_detail(latest_task) if isinstance(latest_task, dict) else None,
                    }
                if latest_status == "rolled_back":
                    return {
                        "success": True,
                        "already_rolled_back": True,
                        "task": self._build_feedback_task_detail(latest_task) if isinstance(latest_task, dict) else None,
                        "result": (latest_task or {}).get("rollback_result") if isinstance((latest_task or {}).get("rollback_result"), dict) else {},
                    }
                return {
                    "success": False,
                    "error": "无法进入回退状态",
                    "task": self._build_feedback_task_detail(latest_task) if isinstance(latest_task, dict) else None,
                }
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="rollback_error",
                reason="rollback_plan_missing",
            )
            failed = self.metadata_store.finalize_feedback_task_rollback(
                task_id=task_id,
                rollback_status="error",
                rollback_error="rollback_plan_missing",
            )
            return {"success": False, "error": "缺少 rollback_plan，无法回退", "task": failed}

        running_task = self.metadata_store.mark_feedback_task_rollback_running(
            task_id=task_id,
            requested_by=requested_by,
            reason=reason,
        )
        if running_task is None:
            latest_task = self.metadata_store.get_feedback_task_by_id(task_id)
            latest_status = str((latest_task or {}).get("rollback_status", "") or "none").strip().lower()
            if latest_status == "running":
                return {
                    "success": False,
                    "error": "该反馈纠错任务正在回退中",
                    "task": self._build_feedback_task_detail(latest_task) if isinstance(latest_task, dict) else None,
                }
            if latest_status == "rolled_back":
                return {
                    "success": True,
                    "already_rolled_back": True,
                    "task": self._build_feedback_task_detail(latest_task) if isinstance(latest_task, dict) else None,
                    "result": (latest_task or {}).get("rollback_result") if isinstance((latest_task or {}).get("rollback_result"), dict) else {},
                }
            return {
                "success": False,
                "error": "无法进入回退状态",
                "task": self._build_feedback_task_detail(latest_task) if isinstance(latest_task, dict) else None,
            }

        result: Dict[str, Any] = {
            "task_id": task_id,
            "query_tool_id": query_tool_id,
            "restored_relation_hashes": [],
            "reverted_corrected_relation_hashes": [],
            "deleted_correction_paragraph_hashes": [],
            "cleared_stale_mark_count": 0,
            "episode_sources_queued": [],
            "profile_person_ids_queued": [],
            "warnings": [],
        }
        try:
            forgotten_relations = rollback_plan.get("forgotten_relations") if isinstance(rollback_plan.get("forgotten_relations"), list) else []
            for item in forgotten_relations:
                if not isinstance(item, dict):
                    continue
                relation_hash = str(item.get("hash", "") or "").strip()
                snapshot = item.get("before_status") if isinstance(item.get("before_status"), dict) else {}
                if not relation_hash or not snapshot:
                    continue
                before_status = self.metadata_store.get_relation_status_batch([relation_hash]).get(relation_hash, {})
                after_status = self.metadata_store.restore_relation_status_from_snapshot(relation_hash, snapshot)
                if after_status is None:
                    result["warnings"].append(f"restore_old_relation_failed:{relation_hash}")
                    continue
                result["restored_relation_hashes"].append(relation_hash)
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_restore_relation",
                    target_hash=relation_hash,
                    before_payload=before_status,
                    after_payload=after_status,
                    reason=reason,
                )

            corrected_write = rollback_plan.get("corrected_write") if isinstance(rollback_plan.get("corrected_write"), dict) else {}
            correction_paragraph_hashes = tokens(corrected_write.get("paragraph_hashes"))
            deleted_paragraphs = self._soft_delete_feedback_correction_paragraphs(correction_paragraph_hashes)
            result["deleted_correction_paragraph_hashes"] = deleted_paragraphs.get("deleted_hashes", [])
            paragraph_rows = deleted_paragraphs.get("paragraph_rows") if isinstance(deleted_paragraphs.get("paragraph_rows"), dict) else {}
            deleted_external_refs = deleted_paragraphs.get("deleted_external_refs") if isinstance(deleted_paragraphs.get("deleted_external_refs"), list) else []
            deleted_ref_map: Dict[str, List[Dict[str, Any]]] = {}
            for ref in deleted_external_refs:
                if not isinstance(ref, dict):
                    continue
                paragraph_hash = str(ref.get("paragraph_hash", "") or "").strip()
                if not paragraph_hash:
                    continue
                deleted_ref_map.setdefault(paragraph_hash, []).append(ref)
            for paragraph_hash in result["deleted_correction_paragraph_hashes"]:
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_delete_correction_paragraph",
                    target_hash=paragraph_hash,
                    before_payload={
                        "paragraph": paragraph_rows.get(paragraph_hash) if isinstance(paragraph_rows.get(paragraph_hash), dict) else {},
                        "external_refs": deleted_ref_map.get(paragraph_hash, []),
                    },
                    reason=reason,
                )

            corrected_relations = corrected_write.get("corrected_relations") if isinstance(corrected_write.get("corrected_relations"), list) else []
            for item in corrected_relations:
                if not isinstance(item, dict):
                    continue
                relation_hash = str(item.get("hash", "") or "").strip()
                if not relation_hash:
                    continue
                before_status = self.metadata_store.get_relation_status_batch([relation_hash]).get(relation_hash, {})
                if bool(item.get("existed_before")):
                    snapshot = item.get("before_status") if isinstance(item.get("before_status"), dict) else {}
                    after_status = self.metadata_store.restore_relation_status_from_snapshot(relation_hash, snapshot)
                else:
                    self.metadata_store.update_relations_protection([relation_hash], protected_until=0.0, is_pinned=False)
                    self.metadata_store.mark_relations_inactive([relation_hash], inactive_since=time.time())
                    after_status = self.metadata_store.get_relation_status_batch([relation_hash]).get(relation_hash)
                if after_status is None:
                    result["warnings"].append(f"revert_corrected_relation_failed:{relation_hash}")
                    continue
                result["reverted_corrected_relation_hashes"].append(relation_hash)
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_revert_corrected_relation",
                    target_hash=relation_hash,
                    before_payload=before_status,
                    after_payload=after_status,
                    reason=reason,
                )

            stale_marks_raw = rollback_plan.get("stale_marks") if isinstance(rollback_plan.get("stale_marks"), list) else []
            stale_mark_rollbacks: List[Dict[str, Any]] = []
            for item in stale_marks_raw:
                if not isinstance(item, dict):
                    continue
                paragraph_hash = str(item.get("paragraph_hash", "") or "").strip()
                relation_hash = str(item.get("relation_hash", "") or "").strip()
                if not paragraph_hash or not relation_hash:
                    continue
                source_operation_id = str(
                    item.get("source_operation_id", "")
                    or f"feedback_correction:{task_id}:{paragraph_hash}:{relation_hash}"
                ).strip()
                rollback_mark = self.metadata_store.rollback_paragraph_stale_relation_mark(
                    paragraph_hash=paragraph_hash,
                    relation_hash=relation_hash,
                    expected_source_type=str(item.get("source_type", "") or "feedback_correction"),
                    expected_source_id=str(item.get("source_id", "") or task_id),
                    expected_source_operation_id=source_operation_id,
                    previous_mark=(
                        item.get("previous_mark")
                        if isinstance(item.get("previous_mark"), dict)
                        else None
                    ),
                )
                stale_mark_rollbacks.append(rollback_mark)
            result["cleared_stale_mark_count"] = sum(
                1 for item in stale_mark_rollbacks if item.get("action") == "deleted"
            )
            result["stale_mark_rollbacks"] = stale_mark_rollbacks
            for rollback_mark in stale_mark_rollbacks:
                paragraph_hash = str(rollback_mark.get("paragraph_hash", "") or "").strip()
                relation_hash = str(rollback_mark.get("relation_hash", "") or "").strip()
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_clear_stale_mark",
                    target_hash=paragraph_hash,
                    after_payload={"relation_hash": relation_hash, "rollback": rollback_mark},
                    reason=reason,
                )

            for source in tokens(rollback_plan.get("episode_sources")):
                if self.metadata_store.enqueue_episode_source_rebuild(source, reason="feedback_correction_rollback"):
                    result["episode_sources_queued"].append(source)
                    self.metadata_store.append_feedback_action_log(
                        task_id=task_id,
                        query_tool_id=query_tool_id,
                        action_type="rollback_enqueue_episode_rebuild",
                        target_hash=source,
                        reason=reason,
                    )

            for person_id in tokens(rollback_plan.get("profile_person_ids")):
                payload = self.metadata_store.enqueue_person_profile_refresh(
                    person_id=person_id,
                    reason="feedback_correction_rollback",
                    source_query_tool_id=query_tool_id,
                )
                if not isinstance(payload, dict):
                    continue
                result["profile_person_ids_queued"].append(person_id)
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_enqueue_profile_refresh",
                    target_hash=person_id,
                    reason=reason,
                )

            self._rebuild_graph_from_metadata()
            self._persist()
            final_task = self.metadata_store.finalize_feedback_task_rollback(
                task_id=task_id,
                rollback_status="rolled_back",
                rollback_result=result,
            )
            return {"success": True, "result": result, "task": self._build_feedback_task_detail(final_task or running_task)}
        except Exception as exc:
            logger.warning(f"反馈纠错回退失败: task_id={task_id} err={exc}", exc_info=True)
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="rollback_error",
                reason=str(exc),
                after_payload=result if result else None,
            )
            final_task = self.metadata_store.finalize_feedback_task_rollback(
                task_id=task_id,
                rollback_status="error",
                rollback_result=result if result else None,
                rollback_error=str(exc),
            )
            return {
                "success": False,
                "error": str(exc),
                "result": result,
                "task": self._build_feedback_task_detail(final_task or running_task),
            }

    async def _process_feedback_profile_refresh_batch(
        self,
        *,
        limit: int,
        debounce_seconds: float = 0.0,
        retry_backoff_seconds: float = 0.0,
        max_retry: Optional[int] = None,
    ) -> Dict[str, Any]:
        if self.metadata_store is None or self.person_profile_service is None:
            return {"processed": 0, "refreshed": 0, "failed": 0, "items": [], "failures": []}

        rows = self.metadata_store.fetch_person_profile_refresh_batch(
            limit=max(1, int(limit or 1)),
            max_retry=profile_policy.person_profile_refresh_max_retry(self._cfg) if max_retry is None else max(0, int(max_retry)),
            debounce_seconds=max(0.0, float(debounce_seconds or 0.0)),
            retry_backoff_seconds=max(0.0, float(retry_backoff_seconds or 0.0)),
        )
        items: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        for row in rows:
            person_id = str(row.get("person_id", "") or "").strip()
            requested_at = row.get("requested_at")
            if not person_id:
                continue
            if not self.metadata_store.mark_person_profile_refresh_running(person_id, requested_at=requested_at):
                continue
            try:
                profile = await self.refresh_person_profile(
                    person_id,
                    limit=max(4, int(self._cfg("person_profile.top_k_evidence", 12) or 12)),
                    mark_active=False,
                )
                if isinstance(profile, dict) and bool(profile.get("success")):
                    self.metadata_store.mark_person_profile_refresh_done(person_id, requested_at=requested_at)
                    items.append(
                        {
                            "person_id": person_id,
                            "profile_version": int(profile.get("profile_version", 0) or 0),
                            "profile_source": str(profile.get("profile_source", "") or ""),
                        }
                    )
                else:
                    error = str((profile or {}).get("error", "") or "person profile refresh failed")
                    self.metadata_store.mark_person_profile_refresh_failed(person_id, error, requested_at=requested_at)
                    failures.append({"person_id": person_id, "error": error})
            except Exception as exc:
                error = str(exc)[:500]
                self.metadata_store.mark_person_profile_refresh_failed(person_id, error, requested_at=requested_at)
                failures.append({"person_id": person_id, "error": error})
        return {
            "processed": len(items) + len(failures),
            "refreshed": len(items),
            "failed": len(failures),
            "items": items,
            "failures": failures,
        }

    async def _process_feedback_episode_rebuild_batch(self, *, limit: int) -> Dict[str, Any]:
        if self.metadata_store is None or self.episode_service is None:
            return {"processed": 0, "rebuilt": 0, "failed": 0, "items": [], "failures": []}

        rows = self.metadata_store.fetch_episode_source_rebuild_batch(
            limit=max(1, int(limit or 1)),
            max_retry=max(1, int(self._cfg("episode.pending_max_retry", 3) or 3)),
        )
        items: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        for row in rows:
            source = str(row.get("source", "") or "").strip()
            requested_at = row.get("requested_at")
            if not source:
                continue
            if not self.metadata_store.mark_episode_source_running(source, requested_at=requested_at):
                continue
            try:
                result = await self.episode_service.rebuild_source(source)
                self.metadata_store.mark_episode_source_done(source, requested_at=requested_at)
                items.append(result if isinstance(result, dict) else {"source": source})
            except Exception as exc:
                error = str(exc)[:500]
                self.metadata_store.mark_episode_source_failed(source, error, requested_at=requested_at)
                failures.append({"source": source, "error": error})
        if items or failures:
            self._persist()
        return {
            "processed": len(items) + len(failures),
            "rebuilt": len(items),
            "failed": len(failures),
            "items": items,
            "failures": failures,
        }

    async def _feedback_correction_reconcile_loop(self) -> None:
        try:
            while not self._background_stopping:
                await asyncio.sleep(feedback_cfg_reconcile_interval_seconds())
                if self._background_stopping:
                    break
                if self.metadata_store is None or not feedback_cfg_enabled():
                    continue
                batch_size = feedback_cfg_reconcile_batch_size()
                if feedback_cfg_profile_refresh_enabled():
                    await self._process_person_profile_refresh_queue_batch(limit=batch_size)
                if feedback_cfg_episode_rebuild_enabled():
                    await self._process_feedback_episode_rebuild_batch(limit=batch_size)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"feedback_correction_reconcile loop 异常: {exc}")

    async def enqueue_feedback_task(
        self,
        *,
        query_tool_id: str,
        session_id: str,
        query_timestamp: Any = None,
        structured_content: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not feedback_cfg_enabled():
            return {"success": False, "queued": False, "reason": "feedback_correction_disabled"}
        if self.metadata_store is None:
            return {"success": False, "queued": False, "reason": "metadata_store_unavailable"}

        clean_tool_id = str(query_tool_id or "").strip()
        clean_session_id = str(session_id or "").strip()
        if not clean_tool_id or not clean_session_id:
            return {"success": False, "queued": False, "reason": "missing_required_fields"}

        content = structured_content if isinstance(structured_content, dict) else {}
        hits = content.get("hits")
        if not isinstance(hits, list) or not hits:
            return {"success": False, "queued": False, "reason": "no_hits"}

        query_time = coerce_datetime(query_timestamp) or datetime.now()
        due_at = query_time + timedelta(hours=feedback_cfg_window_hours())
        saved = self.metadata_store.enqueue_feedback_task(
            query_tool_id=clean_tool_id,
            session_id=clean_session_id,
            query_timestamp=query_time.timestamp(),
            due_at=due_at.timestamp(),
            query_snapshot=content,
        )
        if not isinstance(saved, dict):
            return {"success": False, "queued": False, "reason": "db_save_failed"}

        logger.debug(
            f"反馈纠错任务入队: query_tool_id={clean_tool_id} due_at={due_at.isoformat()}",
        )
        return {
            "success": True,
            "queued": True,
            "query_tool_id": clean_tool_id,
            "due_at": due_at.isoformat(),
            "task": saved,
        }

    def _extract_feedback_messages(
        self,
        *,
        session_id: str,
        query_time: datetime,
        due_time: datetime,
        max_messages: int,
    ) -> List[str]:
        raw_messages = message_api.get_messages_by_time_in_chat(
            chat_id=session_id,
            start_time=query_time.timestamp(),
            end_time=due_time.timestamp(),
            limit=max(1, int(max_messages) * 4),
            limit_mode="latest",
            filter_mai=True,
            filter_command=True,
        )
        collected: List[str] = []
        seen = set()
        for item in raw_messages:
            text = str(getattr(item, "processed_plain_text", "") or "").strip()
            if feedback_noise(text):
                continue
            if text in seen:
                continue
            seen.add(text)
            collected.append(text)
        if len(collected) > max_messages:
            collected = collected[-max_messages:]
        return collected

    def _build_feedback_hit_briefs(self, hits: List[Dict[str, Any]], *, limit: int = 12) -> List[Dict[str, Any]]:
        briefs: List[Dict[str, Any]] = []
        for raw in hits[: max(1, int(limit))]:
            if not isinstance(raw, dict):
                continue
            metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            subject = str(metadata.get("subject", "") or "").strip()
            predicate = str(metadata.get("predicate", "") or "").strip()
            obj = str(metadata.get("object", "") or "").strip()
            linked_relation_hashes: List[str] = []
            linked_relation_texts: List[str] = []

            item_type = str(raw.get("type", "") or "").strip()
            item_hash = str(raw.get("hash", "") or "").strip()
            if item_type == "paragraph" and item_hash and self.metadata_store is not None:
                linked_relations = self.metadata_store.get_paragraph_relations(item_hash)
                for relation in linked_relations:
                    relation_hash = str(relation.get("hash", "") or "").strip()
                    if not relation_hash or relation_hash in linked_relation_hashes:
                        continue
                    linked_relation_hashes.append(relation_hash)
                    rel_subject = str(relation.get("subject", "") or "").strip()
                    rel_predicate = str(relation.get("predicate", "") or "").strip()
                    rel_object = str(relation.get("object", "") or "").strip()
                    relation_text = self._format_relation_text(rel_subject, rel_predicate, rel_object)
                    if relation_text:
                        linked_relation_texts.append(relation_text)
                    if not (subject and predicate and obj):
                        subject = rel_subject
                        predicate = rel_predicate
                        obj = rel_object
            briefs.append(
                {
                    "hash": item_hash,
                    "type": item_type,
                    "content": str(raw.get("content", "") or "").strip(),
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "linked_relation_hashes": linked_relation_hashes[:6],
                    "linked_relation_texts": linked_relation_texts[:3],
                }
            )
        return briefs

    def _should_invoke_feedback_classifier(self, feedback_messages: List[str]) -> bool:
        if not feedback_messages:
            return False
        lowered = "\n".join(feedback_messages).lower()
        return any(token in lowered for token in feedback_signal_tokens())

    async def _classify_feedback(
        self,
        *,
        query_tool_id: str,
        query_text: str,
        hit_briefs: List[Dict[str, Any]],
        feedback_messages: List[str],
    ) -> Dict[str, Any]:
        prompt = (
            "你是长期记忆纠错分类器。"
            "你会根据“记忆检索命中列表”和“用户后续反馈”判断是否需要修正记忆。"
            "请严格输出 JSON 对象，不要输出解释文字。\n\n"
            f"query_tool_id: {query_tool_id}\n"
            f"原查询: {query_text}\n"
            f"候选命中: {json.dumps(hit_briefs, ensure_ascii=False)}\n"
            f"反馈消息: {json.dumps(feedback_messages, ensure_ascii=False)}\n\n"
            "输出 JSON schema:\n"
            "{"
            "\"decision\":\"confirm|reject|correct|supplement|none\","
            "\"confidence\":0.0,"
            "\"target_hashes\":[\"命中列表中的 hash\"],"
            "\"corrected_relations\":[{\"subject\":\"\",\"predicate\":\"\",\"object\":\"\",\"confidence\":1.0}],"
            "\"reason\":\"\""
            "}\n"
            "约束:\n"
            "1. 只有当反馈明确指向错误时才输出 reject/correct。\n"
            "2. target_hashes 必须来自候选命中 hash。\n"
            "3. corrected_relations 仅在 decision=correct 时填写，且必须是明确三元组。\n"
            "4. 不确定时输出 decision=none, confidence<=0.5。"
        )
        try:
            if self._feedback_classifier is None:
                self._feedback_classifier = LLMServiceClient(
                    task_name="utils",
                    request_type="memory_feedback_correction",
                )
            response = await self._feedback_classifier.generate_response(prompt)
            payload = safe_json_loads(getattr(response, "response", ""))
        except Exception as exc:
            logger.warning(f"反馈分类器调用失败: {exc}")
            payload = {}
        return payload

    @staticmethod
    def _normalize_feedback_decision(
        payload: Dict[str, Any],
        *,
        hit_hashes: Sequence[str],
    ) -> Dict[str, Any]:
        allowed = {"confirm", "reject", "correct", "supplement", "none"}
        decision = str(payload.get("decision", "") or "").strip().lower()
        if decision not in allowed:
            decision = "none"
        try:
            confidence = float(payload.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))

        valid_hashes = {str(item or "").strip() for item in hit_hashes if str(item or "").strip()}
        target_hashes_raw = payload.get("target_hashes")
        if isinstance(target_hashes_raw, str):
            target_hashes_candidates = [target_hashes_raw]
        elif isinstance(target_hashes_raw, list):
            target_hashes_candidates = target_hashes_raw
        else:
            target_hashes_candidates = []
        target_hashes = [
            str(item or "").strip()
            for item in target_hashes_candidates
            if str(item or "").strip() in valid_hashes
        ]

        corrected_relations: List[Dict[str, Any]] = []
        raw_relations = payload.get("corrected_relations")
        if isinstance(raw_relations, list):
            for item in raw_relations:
                if not isinstance(item, dict):
                    continue
                subject = str(item.get("subject", "") or "").strip()
                predicate = str(item.get("predicate", "") or "").strip()
                obj = str(item.get("object", "") or "").strip()
                if not (subject and predicate and obj):
                    continue
                try:
                    rel_conf = float(item.get("confidence", 1.0) or 1.0)
                except (TypeError, ValueError):
                    rel_conf = 1.0
                corrected_relations.append(
                    {
                        "subject": subject,
                        "predicate": predicate,
                        "object": obj,
                        "confidence": min(1.0, max(0.0, rel_conf)),
                    }
                )
        corrected_relations = corrected_relations[:6]

        return {
            "decision": decision,
            "confidence": confidence,
            "target_hashes": target_hashes,
            "corrected_relations": corrected_relations,
            "reason": str(payload.get("reason", "") or "").strip(),
            "raw": payload,
        }

    @staticmethod
    def _feedback_apply_result_status(apply_result: Dict[str, Any]) -> str:
        if bool(apply_result.get("applied")):
            return "applied"

        reason = str(apply_result.get("reason", "") or "").strip().lower()
        if reason in {"low_confidence", "no_relation_targets"} or reason.startswith("decision_"):
            return "skipped"
        return "error"

    def _restore_feedback_relations_from_snapshots(
        self,
        *,
        task_id: int,
        query_tool_id: str,
        relation_hashes: Sequence[str],
        snapshots: Dict[str, Dict[str, Any]],
        current_statuses: Optional[Dict[str, Dict[str, Any]]] = None,
        reason: str,
    ) -> Dict[str, List[str]]:
        assert self.metadata_store is not None

        restored_hashes: List[str] = []
        failed_hashes: List[str] = []
        status_map = current_statuses if isinstance(current_statuses, dict) else {}

        for relation_hash in tokens(relation_hashes):
            snapshot = snapshots.get(relation_hash) if isinstance(snapshots, dict) else None
            if not isinstance(snapshot, dict) or not snapshot:
                failed_hashes.append(relation_hash)
                continue

            after_status = self.metadata_store.restore_relation_status_from_snapshot(relation_hash, snapshot)
            if after_status is None:
                failed_hashes.append(relation_hash)
                continue

            restored_hashes.append(relation_hash)
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="compensate_restore_relation",
                target_hash=relation_hash,
                before_payload=status_map.get(relation_hash, {}),
                after_payload=after_status,
                reason=reason,
            )

        if restored_hashes or failed_hashes:
            self._rebuild_graph_from_metadata()
            self._persist()

        return {
            "restored_hashes": restored_hashes,
            "failed_hashes": failed_hashes,
        }

    async def _ingest_feedback_relations(
        self,
        *,
        query_tool_id: str,
        session_id: str,
        relation_hashes: List[str],
        corrected_relations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        supersedes_hash = relation_hashes[0] if relation_hashes else ""
        relation_rows: List[Dict[str, Any]] = []
        for row in corrected_relations:
            relation_rows.append(
                {
                    "subject": str(row.get("subject", "") or "").strip(),
                    "predicate": str(row.get("predicate", "") or "").strip(),
                    "object": str(row.get("object", "") or "").strip(),
                    "confidence": float(row.get("confidence", 1.0) or 1.0),
                    "metadata": {
                        "supersedes_hash": supersedes_hash,
                        "supersedes_hashes": relation_hashes,
                        "from_query_tool_id": query_tool_id,
                        "feedback_window": feedback_cfg_window_label(),
                    },
                }
            )
        plain_text = "；".join(
            f"{item['subject']} {item['predicate']} {item['object']}"
            for item in relation_rows
            if item.get("subject") and item.get("predicate") and item.get("object")
        )
        external_id = compute_hash(
            "feedback_correction:"
            + query_tool_id
            + ":"
            + json.dumps(relation_rows, ensure_ascii=False, sort_keys=True)
        )
        payload = await self.ingest_text(
            external_id=external_id,
            source_type="chat_summary",
            text=plain_text,
            chat_id=session_id,
            relations=relation_rows,
            metadata={
                "from_query_tool_id": query_tool_id,
                "feedback_window": feedback_cfg_window_label(),
                "supersedes_hashes": relation_hashes,
                "feedback_correction_source": True,
            },
            respect_filter=False,
        )
        if isinstance(payload, dict):
            stored_ids = tokens(payload.get("stored_ids"))
            corrected_relation_hashes = stored_ids[1:]
            payload["external_id"] = external_id
            payload["source"] = self._chat_source(session_id)
            payload["paragraph_hashes"] = stored_ids[:1]
            payload["corrected_relation_hashes"] = corrected_relation_hashes
            base_success = bool(payload.get("success")) if "success" in payload else True
            payload["success"] = base_success and bool(corrected_relation_hashes)
            if not payload["success"] and not str(payload.get("error", "") or "").strip():
                payload["error"] = "missing_corrected_relations"
            return payload
        return {"success": False, "error": "invalid_ingest_payload"}

    async def _apply_feedback_decision(
        self,
        *,
        task_id: int,
        query_tool_id: str,
        session_id: str,
        decision: Dict[str, Any],
        hit_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """自动应用达到置信度阈值的拒绝或纠正决策。

        流程先遗忘目标关系；纠正决策随后写入替代关系，写入失败时立即按快照恢复
        原关系。成功后才标记受影响段落、安排 Episode 与人物画像重建，并保存供
        后续人工回退使用的补偿计划。
        """
        threshold = feedback_cfg_auto_apply_threshold()
        confidence = float(decision.get("confidence", 0.0) or 0.0)
        if confidence < threshold:
            return {
                "applied": False,
                "reason": "low_confidence",
                "threshold": threshold,
                "confidence": confidence,
            }

        decision_type = str(decision.get("decision", "none") or "none").strip().lower()
        if decision_type not in {"reject", "correct"}:
            return {
                "applied": False,
                "reason": f"decision_{decision_type}_no_auto_apply",
            }

        target_hashes = [
            str(item or "").strip()
            for item in (decision.get("target_hashes") or [])
            if str(item or "").strip()
        ]
        relation_hashes = self._resolve_feedback_relation_hashes(
            target_hashes=target_hashes,
            hit_map=hit_map,
        )
        if not relation_hashes:
            return {
                "applied": False,
                "reason": "no_relation_targets",
            }

        corrected_relations = [
            dict(item)
            for item in (decision.get("corrected_relations") or [])
            if isinstance(item, dict)
        ]
        if decision_type == "correct" and not corrected_relations:
            return {
                "applied": False,
                "reason": "missing_corrected_relations",
                "relation_hashes": relation_hashes,
                "stale_paragraph_hashes": [],
                "episode_rebuild_sources": [],
                "profile_refresh_person_ids": [],
                "rollback_plan_summary": {},
            }

        assert self.metadata_store is not None
        old_relation_rows = self._query_relation_rows_by_hashes(relation_hashes, include_inactive=True)
        before_status = self.metadata_store.get_relation_status_batch(relation_hashes)
        forget_result = self._apply_v5_relation_action(action="forget", hashes=relation_hashes, strength=1.0)
        forget_success = bool(forget_result.get("success"))
        after_status = self.metadata_store.get_relation_status_batch(relation_hashes)
        for hash_value in relation_hashes:
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="forget_relation",
                target_hash=hash_value,
                before_payload=before_status.get(hash_value) if isinstance(before_status, dict) else {},
                after_payload=after_status.get(hash_value) if isinstance(after_status, dict) else {},
                reason=str(decision.get("reason", "") or ""),
            )

        ingest_result = None
        corrected_relation_hash_candidates: List[str] = []
        corrected_relation_specs_by_hash: Dict[str, Dict[str, Any]] = {}
        if decision_type == "correct" and corrected_relations and self.metadata_store is not None:
            for item in corrected_relations:
                try:
                    relation_hash = self.metadata_store.compute_relation_hash(
                        str(item.get("subject", "") or "").strip(),
                        str(item.get("predicate", "") or "").strip(),
                        str(item.get("object", "") or "").strip(),
                    )
                except Exception:
                    continue
                if not relation_hash:
                    continue
                corrected_relation_hash_candidates.append(relation_hash)
                corrected_relation_specs_by_hash[relation_hash] = {
                    "subject": str(item.get("subject", "") or "").strip(),
                    "predicate": str(item.get("predicate", "") or "").strip(),
                    "object": str(item.get("object", "") or "").strip(),
                }
        corrected_relation_before_status = (
            self.metadata_store.get_relation_status_batch(corrected_relation_hash_candidates)
            if corrected_relation_hash_candidates
            else {}
        )
        if not forget_success:
            return {
                "applied": False,
                "reason": "forget_failed",
                "error": str(forget_result.get("error", "") or "forget_failed"),
                "forget": forget_result,
                "ingest": ingest_result,
                "relation_hashes": relation_hashes,
                "stale_paragraph_hashes": [],
                "episode_rebuild_sources": [],
                "profile_refresh_person_ids": [],
                "rollback_plan_summary": {},
            }

        stale_paragraph_map: Dict[str, List[str]] = {}
        stale_paragraph_hashes: List[str] = []
        episode_rebuild_sources: List[str] = []
        profile_refresh_person_ids: List[str] = []
        rollback_plan: Dict[str, Any] = {}
        if decision_type == "correct" and corrected_relations:
            ingest_result = await self._ingest_feedback_relations(
                query_tool_id=query_tool_id,
                session_id=session_id,
                relation_hashes=relation_hashes,
                corrected_relations=corrected_relations,
            )
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="ingest_correction",
                target_hash=relation_hashes[0] if relation_hashes else "",
                before_payload={"target_hashes": relation_hashes},
                after_payload=ingest_result,
                reason=str(decision.get("reason", "") or ""),
            )

            ingest_success = bool((ingest_result or {}).get("success")) if isinstance(ingest_result, dict) else False
            if not ingest_success:
                compensation_result = self._restore_feedback_relations_from_snapshots(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    relation_hashes=relation_hashes,
                    snapshots=before_status if isinstance(before_status, dict) else {},
                    current_statuses=after_status if isinstance(after_status, dict) else {},
                    reason=str(decision.get("reason", "") or "") or "feedback_correction_ingest_failed",
                )
                restore_failed_hashes = compensation_result.get("failed_hashes", [])
                return {
                    "applied": False,
                    "reason": "correction_restore_failed" if restore_failed_hashes else "correction_ingest_failed",
                    "error": str((ingest_result or {}).get("error", "") or "correction_ingest_failed"),
                    "forget": forget_result,
                    "ingest": ingest_result,
                    "relation_hashes": relation_hashes,
                    "stale_paragraph_hashes": [],
                    "episode_rebuild_sources": [],
                    "profile_refresh_person_ids": [],
                    "restored_relation_hashes": compensation_result.get("restored_hashes", []),
                    "restore_failed_hashes": restore_failed_hashes,
                    "rollback_plan_summary": {},
                }
        else:
            ingest_success = False

        applied = forget_success if decision_type == "reject" else (forget_success and ingest_success)
        if applied:
            stale_paragraph_map = self._mark_feedback_stale_paragraphs(
                task_id=task_id,
                query_tool_id=query_tool_id,
                relation_hashes=relation_hashes,
                reason=str(decision.get("reason", "") or "") or "feedback_correction",
            )
            stale_paragraph_hashes = merge_tokens(
                *[
                    paragraph_hashes
                    for paragraph_hashes in stale_paragraph_map.values()
                    if isinstance(paragraph_hashes, list)
                ]
            )
            episode_rebuild_sources = self._enqueue_feedback_episode_rebuilds(
                paragraph_hashes=stale_paragraph_hashes,
                session_id=session_id,
                include_correction_source=bool(ingest_success),
            )
            profile_refresh_person_ids = self._enqueue_feedback_profile_refreshes(
                person_ids=self._resolve_feedback_related_person_ids(
                    old_relation_rows=old_relation_rows,
                    corrected_relations=corrected_relations,
                ),
                query_tool_id=query_tool_id,
            )
            for relation_hash, paragraph_hashes in stale_paragraph_map.items():
                for paragraph_hash in paragraph_hashes:
                    self.metadata_store.append_feedback_action_log(
                        task_id=task_id,
                        query_tool_id=query_tool_id,
                        action_type="mark_stale_paragraph",
                        target_hash=paragraph_hash,
                        after_payload={"relation_hash": relation_hash},
                        reason=str(decision.get("reason", "") or ""),
                    )
            for source in episode_rebuild_sources:
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="enqueue_episode_rebuild",
                    target_hash=source,
                    reason=str(decision.get("reason", "") or ""),
                )
            for person_id in profile_refresh_person_ids:
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="enqueue_profile_refresh",
                    target_hash=person_id,
                    reason=str(decision.get("reason", "") or ""),
                )
            forgotten_relations = []
            for row in old_relation_rows:
                relation_hash = str(row.get("hash", "") or "").strip()
                if not relation_hash:
                    continue
                forgotten_relations.append(
                    {
                        "hash": relation_hash,
                        "subject": str(row.get("subject", "") or "").strip(),
                        "predicate": str(row.get("predicate", "") or "").strip(),
                        "object": str(row.get("object", "") or "").strip(),
                        "before_status": before_status.get(relation_hash) if isinstance(before_status, dict) else {},
                    }
                )

            corrected_write: Dict[str, Any] = {}
            if isinstance(ingest_result, dict):
                stored_relation_hashes = tokens(ingest_result.get("corrected_relation_hashes"))
                corrected_write = {
                    "external_id": str(ingest_result.get("external_id", "") or "").strip(),
                    "source": str(ingest_result.get("source", "") or "").strip(),
                    "paragraph_hashes": tokens(ingest_result.get("paragraph_hashes")),
                    "corrected_relation_hashes": stored_relation_hashes,
                    "corrected_relations": [
                        {
                            "hash": relation_hash,
                            **corrected_relation_specs_by_hash.get(relation_hash, {}),
                            "existed_before": relation_hash in corrected_relation_before_status,
                            "before_status": corrected_relation_before_status.get(relation_hash, {}),
                        }
                        for relation_hash in stored_relation_hashes
                    ],
                }

            rollback_plan = {
                "task_id": task_id,
                "query_tool_id": query_tool_id,
                "session_id": session_id,
                "decision_type": decision_type,
                "forgotten_relations": forgotten_relations,
                "corrected_write": corrected_write,
                "stale_marks": [
                    {
                        "paragraph_hash": paragraph_hash,
                        "relation_hash": relation_hash,
                        "source_type": "feedback_correction",
                        "source_id": str(task_id),
                        "source_operation_id": f"feedback_correction:{task_id}:{paragraph_hash}:{relation_hash}",
                    }
                    for relation_hash, paragraph_hashes in stale_paragraph_map.items()
                    for paragraph_hash in (paragraph_hashes or [])
                    if str(paragraph_hash or "").strip()
                ],
                "episode_sources": episode_rebuild_sources,
                "profile_person_ids": profile_refresh_person_ids,
                "created_at": time.time(),
            }
            update_rollback_plan = getattr(self.metadata_store, "update_feedback_task_rollback_plan", None)
            if callable(update_rollback_plan):
                update_rollback_plan(
                    task_id=task_id,
                    rollback_plan=rollback_plan,
                )
        return {
            "applied": applied,
            "forget": forget_result,
            "ingest": ingest_result,
            "relation_hashes": relation_hashes,
            "stale_paragraph_hashes": stale_paragraph_hashes,
            "episode_rebuild_sources": episode_rebuild_sources,
            "profile_refresh_person_ids": profile_refresh_person_ids,
            "rollback_plan_summary": self._build_feedback_rollback_plan_summary(rollback_plan) if rollback_plan else {},
        }

    def _resolve_feedback_relation_hashes(
        self,
        *,
        target_hashes: Sequence[str],
        hit_map: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        resolved: List[str] = []
        seen: set[str] = set()
        for target_hash in target_hashes:
            token = str(target_hash or "").strip()
            if not token:
                continue
            hit = hit_map.get(token) if isinstance(hit_map, dict) else None
            item_type = str((hit or {}).get("type", "") or "").strip()
            if item_type == "relation":
                if token not in seen:
                    seen.add(token)
                    resolved.append(token)
                continue
            if item_type != "paragraph":
                continue

            linked_candidates = tokens((hit or {}).get("linked_relation_hashes"))
            if not linked_candidates and self.metadata_store is not None:
                for relation in self.metadata_store.get_paragraph_relations(token):
                    linked_hash = str(relation.get("hash", "") or "").strip()
                    if linked_hash:
                        linked_candidates.append(linked_hash)

            for linked_hash in linked_candidates:
                if linked_hash in seen:
                    continue
                seen.add(linked_hash)
                resolved.append(linked_hash)
        return resolved

    async def _process_feedback_task(self, task: Dict[str, Any]) -> None:
        task_id = int(task.get("id") or 0)
        query_tool_id = str(task.get("query_tool_id", "") or "").strip()
        if task_id <= 0 or not query_tool_id:
            return

        assert self.metadata_store is not None
        self.metadata_store.mark_feedback_task_running(task_id)

        decision_payload: Dict[str, Any] = {}
        session_id = str(task.get("session_id", "") or "").strip()
        try:
            structured = task.get("query_snapshot") if isinstance(task.get("query_snapshot"), dict) else {}
            if not session_id:
                session_id = str(structured.get("chat_id", "") or "").strip()
            if not session_id:
                raise RuntimeError("反馈任务缺少 session_id")
            hits_raw = structured.get("hits")
            if not isinstance(hits_raw, list) or not hits_raw:
                decision_payload = {"decision": "none", "confidence": 1.0, "reason": "no_hits"}
                self.metadata_store.finalize_feedback_task(
                    task_id=task_id,
                    status="skipped",
                    decision_payload=decision_payload,
                )
                return

            query_timestamp = coerce_datetime(task.get("query_timestamp")) or datetime.now()
            due_at = coerce_datetime(task.get("due_at")) or (
                query_timestamp + timedelta(hours=feedback_cfg_window_hours())
            )
            if due_at <= query_timestamp:
                due_at = query_timestamp + timedelta(hours=feedback_cfg_window_hours())

            feedback_messages = self._extract_feedback_messages(
                session_id=session_id,
                query_time=query_timestamp,
                due_time=due_at,
                max_messages=feedback_cfg_max_messages(),
            )
            if not feedback_messages:
                decision_payload = {"decision": "none", "confidence": 1.0, "reason": "no_feedback_messages"}
                self.metadata_store.finalize_feedback_task(
                    task_id=task_id,
                    status="skipped",
                    decision_payload=decision_payload,
                )
                return

            if feedback_cfg_prefilter_enabled() and not self._should_invoke_feedback_classifier(feedback_messages):
                decision_payload = {"decision": "none", "confidence": 1.0, "reason": "prefilter_skipped"}
                self.metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="skip",
                    reason="prefilter_skipped",
                    after_payload={"feedback_messages": feedback_messages},
                )
                self.metadata_store.finalize_feedback_task(
                    task_id=task_id,
                    status="skipped",
                    decision_payload=decision_payload,
                )
                return

            hit_briefs = self._build_feedback_hit_briefs(hits_raw)
            hit_map = {str(item.get("hash", "") or "").strip(): item for item in hit_briefs if str(item.get("hash", "") or "").strip()}
            raw_decision = await self._classify_feedback(
                query_tool_id=query_tool_id,
                query_text=str(structured.get("query", "") or ""),
                hit_briefs=hit_briefs,
                feedback_messages=feedback_messages,
            )
            decision_payload = self._normalize_feedback_decision(raw_decision, hit_hashes=list(hit_map.keys()))
            decision_payload["feedback_message_count"] = len(feedback_messages)
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="classification",
                after_payload=decision_payload,
                reason=str(decision_payload.get("reason", "") or ""),
            )

            apply_result = await self._apply_feedback_decision(
                task_id=task_id,
                query_tool_id=query_tool_id,
                session_id=session_id,
                decision=decision_payload,
                hit_map=hit_map,
            )
            decision_payload["apply_result"] = apply_result
            final_status = self._feedback_apply_result_status(apply_result)
            self.metadata_store.finalize_feedback_task(
                task_id=task_id,
                status=final_status,
                decision_payload=decision_payload,
                last_error=str(apply_result.get("error", "") or "") if final_status == "error" else "",
            )
        except Exception as exc:
            logger.warning(f"反馈纠错任务处理失败: task_id={task_id} err={exc}", exc_info=True)
            self.metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="error",
                reason=str(exc),
                after_payload=decision_payload if decision_payload else None,
            )
            self.metadata_store.finalize_feedback_task(
                task_id=task_id,
                status="error",
                decision_payload=decision_payload if decision_payload else None,
                last_error=str(exc),
            )

    async def _feedback_correction_loop(self) -> None:
        try:
            while not self._background_stopping:
                interval_seconds = feedback_cfg_check_interval_seconds()
                if not feedback_cfg_enabled():
                    await asyncio.sleep(interval_seconds)
                    continue
                if self.metadata_store is None:
                    await asyncio.sleep(interval_seconds)
                    continue
                tasks = self.metadata_store.fetch_due_feedback_tasks(
                    limit=feedback_cfg_batch_size(),
                    now=datetime.now().timestamp(),
                )
                if not tasks:
                    await asyncio.sleep(interval_seconds)
                    continue
                for task in tasks:
                    if self._background_stopping:
                        break
                    if not isinstance(task, dict):
                        continue
                    await self._process_feedback_task(task)
                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"feedback_correction loop 异常: {exc}")

