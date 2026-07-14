from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Sequence

from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.services.llm_service import LLMServiceClient

from ...utils.feedback_policy import (
    fuzzy_modify_cfg_allow_global_scope,
    fuzzy_modify_cfg_auto_execute_enabled,
    fuzzy_modify_cfg_candidate_limit,
    fuzzy_modify_cfg_confirm_threshold,
    fuzzy_modify_cfg_enabled,
    fuzzy_modify_cfg_max_targets,
)
from ...utils.metadata import coerce_metadata_dict
from ...utils.runtime_payloads import (
    argument_tokens,
    merge_argument_tokens,
    optional_float,
    safe_json_loads,
    tokens,
)
from ..models import KernelSearchRequest
from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")


class MemoryCorrectionAdminService(KernelServiceBase):
    async def memory_correction_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store is not None

        act = str(action or "").strip().lower()
        if act in {"preview", "plan"}:
            return await self._preview_fuzzy_modify_action(
                request_text=str(kwargs.get("request_text", "") or kwargs.get("text", "") or "").strip(),
                scope=str(kwargs.get("scope", "") or "person_profile").strip(),
                person_id=str(kwargs.get("person_id", "") or "").strip(),
                person_keyword=str(kwargs.get("person_keyword", "") or kwargs.get("keyword", "") or "").strip(),
                chat_id=str(kwargs.get("chat_id", "") or "").strip(),
                limit=max(
                    1,
                    int(kwargs.get("limit", fuzzy_modify_cfg_candidate_limit()) or fuzzy_modify_cfg_candidate_limit()),
                ),
                requested_by=str(kwargs.get("requested_by", "") or "webui").strip(),
                reason=str(kwargs.get("reason", "") or "").strip(),
            )
        if act == "execute":
            return await self._execute_fuzzy_modify_action(
                plan_id=str(kwargs.get("plan_id", "") or "").strip(),
                confirmed=bool(kwargs.get("confirmed", False)),
                requested_by=str(kwargs.get("requested_by", "") or "webui").strip(),
                reason=str(kwargs.get("reason", "") or "").strip(),
            )
        if act == "get":
            plan = self.metadata_store.get_fuzzy_modify_plan(str(kwargs.get("plan_id", "") or "").strip())
            return {"success": plan is not None, "plan": plan, "error": "" if plan is not None else "修改计划不存在"}
        if act == "list":
            raw_statuses = kwargs.get("statuses")
            if raw_statuses is None:
                raw_statuses = kwargs.get("status")
            statuses = tokens([raw_statuses] if isinstance(raw_statuses, str) else raw_statuses)
            items = self.metadata_store.list_fuzzy_modify_plans(
                limit=max(1, int(kwargs.get("limit", 50) or 50)),
                statuses=statuses,
                scope=str(kwargs.get("scope", "") or "").strip(),
            )
            return {"success": True, "items": items, "count": len(items)}
        if act == "rollback":
            return await self._rollback_fuzzy_modify_action(
                plan_id=str(kwargs.get("plan_id", "") or "").strip(),
                requested_by=str(kwargs.get("requested_by", "") or "webui").strip(),
                reason=str(kwargs.get("reason", "") or "").strip(),
            )
        return {"success": False, "error": f"不支持的记忆修正操作: {act}"}

    async def memory_fuzzy_modify_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        return await self.memory_correction_admin(action=action, **kwargs)

    async def _preview_fuzzy_modify_action(
        self,
        *,
        request_text: str,
        scope: str,
        person_id: str = "",
        person_keyword: str = "",
        chat_id: str = "",
        limit: int = 20,
        requested_by: str = "webui",
        reason: str = "",
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        if not fuzzy_modify_cfg_enabled():
            return {"success": False, "error": "记忆修正功能未启用"}
        text = str(request_text or "").strip()
        if not text:
            return {"success": False, "error": "修改描述不能为空"}

        scope_token = self._normalize_fuzzy_modify_scope(scope)
        pid = str(person_id or "").strip()
        keyword = str(person_keyword or "").strip()
        if scope_token == "person_profile":
            if not pid and keyword and self.person_profile_service is not None:
                pid = self.person_profile_service.resolve_person_id(keyword)
            if not pid:
                return {"success": False, "error": "人物画像修改需要提供 person_id 或 person_keyword"}
        elif not chat_id and not fuzzy_modify_cfg_allow_global_scope():
            return {"success": False, "error": "非人物画像修正需要提供 chat_id，或开启全局记忆修正范围"}

        candidate_limit = min(max(1, int(limit or 20)), fuzzy_modify_cfg_candidate_limit())
        candidates = await self._collect_fuzzy_modify_candidates(
            request_text=text,
            scope=scope_token,
            person_id=pid,
            person_keyword=keyword,
            chat_id=str(chat_id or "").strip(),
            limit=candidate_limit,
        )
        if not candidates:
            return {"success": False, "error": "未找到可修改的候选记忆", "candidates": []}

        plan_payload = await self._build_fuzzy_modify_llm_plan(
            request_text=text,
            scope=scope_token,
            person_id=pid,
            person_keyword=keyword,
            chat_id=str(chat_id or "").strip(),
            candidates=candidates,
        )
        plan = self._normalize_fuzzy_modify_plan(
            plan_payload,
            request_text=text,
            scope=scope_token,
            person_id=pid,
            chat_id=str(chat_id or "").strip(),
            candidates=candidates,
        )
        if not plan.get("operations"):
            return {
                "success": False,
                "error": str(plan.get("reason", "") or "LLM 未生成可执行修改计划"),
                "raw_plan": plan_payload,
                "candidates": candidates,
            }

        confidence = float(plan.get("confidence", 0.0) or 0.0)
        cascade_preview = self._build_fuzzy_modify_cascade_preview(
            operations=plan.get("operations", []),
        )
        preview = {
            "request_text": text,
            "scope": scope_token,
            "person_id": pid,
            "person_keyword": keyword,
            "chat_id": str(chat_id or "").strip(),
            "candidates": candidates,
            "operations": plan.get("operations", []),
            "cascade_preview": cascade_preview,
            "requires_confirmation": True,
            "confirm_threshold": fuzzy_modify_cfg_confirm_threshold(),
            "reason": str(plan.get("reason", "") or ""),
        }
        record = self.metadata_store.create_fuzzy_modify_plan(
            request_text=text,
            scope=scope_token,
            target_person_id=pid,
            target_chat_id=str(chat_id or "").strip(),
            plan=plan,
            preview=preview,
            status="awaiting_confirmation",
            confidence=confidence,
            requested_by=requested_by,
            reason=reason,
        )
        return {
            "success": True,
            "plan_id": str(record.get("plan_id", "") or ""),
            "plan": record,
            "preview": preview,
            "requires_confirmation": True,
        }

    async def _execute_fuzzy_modify_action(
        self,
        *,
        plan_id: str,
        confirmed: bool,
        requested_by: str = "webui",
        reason: str = "",
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        token = str(plan_id or "").strip()
        if not token:
            return {"success": False, "error": "plan_id 不能为空"}
        plan_record = self.metadata_store.get_fuzzy_modify_plan(token)
        if plan_record is None:
            return {"success": False, "error": "修改计划不存在"}
        status = str(plan_record.get("status", "") or "").strip()
        if status not in {"awaiting_confirmation", "failed", "executing"}:
            return {"success": False, "error": f"当前计划状态不可执行: {status}"}
        if not confirmed:
            confidence = optional_float(plan_record.get("confidence")) or 0.0
            if not fuzzy_modify_cfg_auto_execute_enabled() or confidence < fuzzy_modify_cfg_confirm_threshold():
                return {"success": False, "error": "需要用户确认后才能执行", "requires_confirmation": True}

        claimed_plan = self.metadata_store.claim_fuzzy_modify_plan(token)
        if claimed_plan is None:
            latest = self.metadata_store.get_fuzzy_modify_plan(token)
            return {"success": False, "error": "修改计划正在执行或状态已变化", "plan": latest}
        plan_record = claimed_plan

        previous_execution = plan_record.get("execution") if isinstance(plan_record.get("execution"), dict) else {}
        attempt_started_at = time.time()
        executing_payload = {
            **previous_execution,
            "attempt": {
                "status": "executing",
                "started_at": attempt_started_at,
                "requested_by": requested_by,
                "reason": reason,
                "recovered_from_stale_executing": status == "executing",
            },
        }
        self.metadata_store.update_fuzzy_modify_plan(token, status="executing", execution=executing_payload)
        try:
            execution = await self._apply_fuzzy_modify_plan(
                plan_record=plan_record,
                requested_by=requested_by,
                reason=reason,
            )
            execution = {
                **execution,
                "attempt": {
                    **executing_payload["attempt"],
                    "status": "finished",
                    "finished_at": time.time(),
                },
            }
            updated = self.metadata_store.update_fuzzy_modify_plan(
                token,
                status="executed" if bool(execution.get("success")) else "failed",
                execution=execution,
                executed_at=time.time() if bool(execution.get("success")) else None,
                reason=reason if reason else None,
            )
            return {"success": bool(execution.get("success")), "plan": updated, "execution": execution}
        except Exception as exc:
            execution = {
                **executing_payload,
                "success": False,
                "error": str(exc),
                "attempt": {
                    **executing_payload["attempt"],
                    "status": "failed",
                    "finished_at": time.time(),
                },
            }
            updated = self.metadata_store.update_fuzzy_modify_plan(
                token,
                status="failed",
                execution=execution,
                reason=reason if reason else None,
            )
            logger.warning(f"记忆修正执行失败: {exc}")
            return {"success": False, "plan": updated, "execution": execution, "error": str(exc)}

    async def _rollback_fuzzy_modify_action(
        self,
        *,
        plan_id: str,
        requested_by: str = "webui",
        reason: str = "",
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        token = str(plan_id or "").strip()
        if not token:
            return {"success": False, "error": "plan_id 不能为空"}
        plan_record = self.metadata_store.get_fuzzy_modify_plan(token)
        if plan_record is None:
            return {"success": False, "error": "修改计划不存在"}
        if str(plan_record.get("status", "") or "") != "executed":
            return {"success": False, "error": "只有已执行的修改计划可以回滚"}

        execution = plan_record.get("execution") if isinstance(plan_record.get("execution"), dict) else {}
        stored_ids = tokens(execution.get("stored_ids"))
        paragraph_hashes = [hash_value for hash_value in stored_ids if self.metadata_store.get_paragraph(hash_value)]
        relation_hashes = [hash_value for hash_value in stored_ids if self.metadata_store.get_relation(hash_value)]
        rollback_items: List[Dict[str, Any]] = []
        if paragraph_hashes:
            delete_result = await self._execute_delete_action(
                mode="paragraph",
                selector={"hashes": paragraph_hashes},
                requested_by=requested_by,
                reason=reason or "fuzzy_modify_rollback",
            )
            rollback_items.append({"type": "delete_new_paragraphs", "result": delete_result})
            if not bool(delete_result.get("success", False)):
                rollback_result = {
                    "success": False,
                    "error": str(delete_result.get("error", "") or "回滚删除新增记忆失败"),
                    "stored_ids_delete_requested": paragraph_hashes,
                    "new_relations_deactivated": [],
                    "restored_targets": [],
                    "items": rollback_items,
                    "requested_by": requested_by,
                    "reason": reason,
                }
                updated = self.metadata_store.update_fuzzy_modify_plan(
                    token,
                    status="rollback_failed",
                    execution={**execution, "rollback": rollback_result},
                    reason=reason if reason else None,
                )
                return {
                    "success": False,
                    "plan": updated,
                    "rollback": rollback_result,
                    "error": rollback_result["error"],
                }

        restored_targets: List[Dict[str, Any]] = []
        restore_failures: List[Dict[str, str]] = []
        stale_marks_deleted: List[Dict[str, Any]] = []
        stale_marks_restored: List[Dict[str, Any]] = []
        stale_marks_skipped: List[Dict[str, Any]] = []
        for item in execution.get("superseded_targets") or []:
            if not isinstance(item, dict):
                continue
            target_type = str(item.get("target_type", "") or "").strip()
            hash_value = str(item.get("hash", "") or "").strip()
            previous_metadata = item.get("previous_metadata") if isinstance(item.get("previous_metadata"), dict) else {}
            if target_type == "paragraph" and hash_value:
                cascade = item.get("cascade") if isinstance(item.get("cascade"), dict) else {}
                for relation_item in cascade.get("relations_marked_inactive") or []:
                    if not isinstance(relation_item, dict):
                        continue
                    relation_hash = str(relation_item.get("relation_hash", "") or "").strip()
                    if not relation_hash:
                        continue
                    previous_relation_metadata = (
                        relation_item.get("previous_metadata")
                        if isinstance(relation_item.get("previous_metadata"), dict)
                        else {}
                    )
                    updated_relation = self.metadata_store.update_relation_metadata(
                        relation_hash,
                        previous_relation_metadata,
                        merge=False,
                    )
                    if updated_relation is None:
                        restore_failures.append(
                            {"target_type": "relation", "hash": relation_hash, "error": "级联关系不存在"}
                        )
                        continue
                    if bool(relation_item.get("previous_is_inactive", False)):
                        self.metadata_store.mark_relations_inactive(
                            [relation_hash],
                            inactive_since=optional_float(relation_item.get("previous_inactive_since")),
                        )
                    else:
                        self.metadata_store.mark_relations_active([relation_hash])
                    restored_targets.append(
                        {"target_type": "relation", "hash": relation_hash, "cascade_from": hash_value}
                    )

                for snapshot in cascade.get("stale_mark_snapshots") or []:
                    if not isinstance(snapshot, dict):
                        continue
                    paragraph_hash = str(snapshot.get("paragraph_hash", "") or hash_value).strip()
                    relation_hash = str(snapshot.get("relation_hash", "") or "").strip()
                    if not paragraph_hash or not relation_hash:
                        continue
                    rollback_mark = self.metadata_store.rollback_paragraph_stale_relation_mark(
                        paragraph_hash=paragraph_hash,
                        relation_hash=relation_hash,
                        expected_source_type=str(snapshot.get("source_type", "") or "memory_correction"),
                        expected_source_id=str(snapshot.get("source_id", "") or token),
                        expected_source_operation_id=str(snapshot.get("source_operation_id", "") or ""),
                        previous_mark=(
                            snapshot.get("previous_mark") if isinstance(snapshot.get("previous_mark"), dict) else None
                        ),
                    )
                    action = str(rollback_mark.get("action", "") or "").strip()
                    if action == "deleted":
                        stale_marks_deleted.append(rollback_mark)
                    elif action == "restored":
                        stale_marks_restored.append(rollback_mark)
                    elif action in {"skipped_due_to_source_mismatch", "restore_failed", "invalid_target"}:
                        stale_marks_skipped.append(rollback_mark)
                        if action in {"restore_failed", "invalid_target"}:
                            restore_failures.append(
                                {
                                    "target_type": "stale_mark",
                                    "hash": f"{paragraph_hash}:{relation_hash}",
                                    "error": action,
                                }
                            )
                    else:
                        stale_marks_skipped.append(rollback_mark)

                updated = self.metadata_store.update_paragraph_metadata(hash_value, previous_metadata, merge=False)
                if updated is not None:
                    restored_targets.append({"target_type": target_type, "hash": hash_value})
                else:
                    restore_failures.append(
                        {"target_type": target_type, "hash": hash_value, "error": "目标段落不存在或已删除"}
                    )
                continue
            if target_type == "relation" and hash_value:
                updated = self.metadata_store.update_relation_metadata(hash_value, previous_metadata, merge=False)
                if updated is not None:
                    if bool(item.get("previous_is_inactive", False)):
                        self.metadata_store.mark_relations_inactive(
                            [hash_value],
                            inactive_since=optional_float(item.get("previous_inactive_since")),
                        )
                    else:
                        self.metadata_store.mark_relations_active([hash_value])
                    restored_targets.append({"target_type": target_type, "hash": hash_value})
                else:
                    restore_failures.append({"target_type": target_type, "hash": hash_value, "error": "目标关系不存在"})

        if relation_hashes:
            self.metadata_store.mark_relations_inactive(relation_hashes, inactive_since=time.time())
        if relation_hashes or restored_targets:
            self._rebuild_graph_from_metadata()
            self._persist()
        rollback_success = not restore_failures
        rollback_result = {
            "success": rollback_success,
            "stored_ids_deleted": paragraph_hashes,
            "new_relations_deactivated": relation_hashes,
            "restored_targets": restored_targets,
            "restore_failures": restore_failures,
            "stale_marks_deleted": stale_marks_deleted,
            "stale_marks_restored": stale_marks_restored,
            "stale_marks_skipped": stale_marks_skipped,
            "items": rollback_items,
            "requested_by": requested_by,
            "reason": reason,
        }
        updated = self.metadata_store.update_fuzzy_modify_plan(
            token,
            status="rolled_back" if rollback_success else "rollback_failed",
            execution={**execution, "rollback": rollback_result},
            reason=reason if reason else None,
        )
        return {"success": rollback_success, "plan": updated, "rollback": rollback_result}

    async def _collect_fuzzy_modify_candidates(
        self,
        *,
        request_text: str,
        scope: str,
        person_id: str = "",
        person_keyword: str = "",
        chat_id: str = "",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def append_candidate(item: Dict[str, Any]) -> None:
            candidate = self._normalize_fuzzy_modify_candidate(item)
            candidate_type = str(candidate.get("target_type", "") or "").strip()
            hash_value = str(candidate.get("hash", "") or "").strip()
            key = (candidate_type, hash_value)
            if not candidate_type or not hash_value or key in seen:
                return
            if not self._is_fuzzy_modify_candidate_mutable(candidate, item):
                return
            seen.add(key)
            candidates.append(candidate)

        if scope == "person_profile":
            evidence = await self._profile_evidence_admin(
                person_id=person_id,
                person_keyword=person_keyword,
                limit=max(limit, 12),
                force_refresh=False,
            )
            for item in evidence.get("evidence") or []:
                if isinstance(item, dict):
                    append_candidate(item)

        search_result = await self.search_memory(
            KernelSearchRequest(
                query=request_text,
                limit=limit,
                mode="aggregate",
                chat_id=chat_id,
                person_id=person_id,
                respect_filter=True,
            )
        )
        for item in search_result.get("hits") or []:
            if isinstance(item, dict):
                append_candidate(item)
        return candidates[:limit]

    def _is_fuzzy_modify_candidate_mutable(self, candidate: Dict[str, Any], raw_item: Dict[str, Any]) -> bool:
        assert self.metadata_store is not None
        if raw_item.get("deletable") is False:
            return False
        target_type = str(candidate.get("target_type", "") or "").strip()
        hash_value = str(candidate.get("hash", "") or "").strip()
        if not target_type or not hash_value:
            return False
        if target_type == "paragraph":
            paragraph = self.metadata_store.get_paragraph(hash_value)
            return isinstance(paragraph, dict) and not bool(paragraph.get("is_deleted", 0))
        if target_type == "relation":
            relation = self.metadata_store.get_relation(hash_value, include_inactive=False)
            if relation is None:
                return False
            status = self.metadata_store.get_relation_status_batch([hash_value]).get(hash_value, {})
            if bool(status.get("is_inactive", False)) or bool(status.get("is_pinned", False)):
                return False
            protected_until = optional_float(status.get("protected_until")) or 0.0
            return protected_until <= time.time()
        return False

    async def _build_fuzzy_modify_llm_plan(
        self,
        *,
        request_text: str,
        scope: str,
        person_id: str = "",
        person_keyword: str = "",
        chat_id: str = "",
        candidates: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payload = {
            "request_text": request_text,
            "scope": scope,
            "person_id": person_id,
            "person_keyword": person_keyword,
            "chat_id": chat_id,
            "max_targets": fuzzy_modify_cfg_max_targets(),
            "candidates": [
                {
                    "candidate_id": str(item.get("candidate_id", "") or ""),
                    "target_type": str(item.get("target_type", "") or ""),
                    "evidence_type": str(item.get("evidence_type", "") or ""),
                    "hash": str(item.get("hash", "") or ""),
                    "content": str(item.get("content", "") or ""),
                    "source": str(item.get("source", "") or ""),
                    "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                }
                for item in candidates
            ],
        }
        prompt = load_prompt(
            "memory_fuzzy_modify_plan",
            request_payload=json.dumps(payload, ensure_ascii=False, indent=2),
        )
        if self._fuzzy_modify_planner is None:
            self._fuzzy_modify_planner = LLMServiceClient(
                task_name="utils",
                request_type="A_Memorix.fuzzy_modify_plan",
            )
        response = await self._fuzzy_modify_planner.generate_response(prompt)
        return safe_json_loads(getattr(response, "response", ""))

    def _normalize_fuzzy_modify_plan(
        self,
        payload: Dict[str, Any],
        *,
        request_text: str,
        scope: str,
        person_id: str,
        chat_id: str,
        candidates: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        candidate_map = {
            str(item.get("candidate_id", "") or "").strip(): item
            for item in candidates
            if str(item.get("candidate_id", "") or "").strip()
        }
        hash_to_candidate = {
            str(item.get("hash", "") or "").strip(): item
            for item in candidates
            if str(item.get("hash", "") or "").strip()
        }
        try:
            confidence = float(payload.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))
        max_targets = fuzzy_modify_cfg_max_targets()
        operations: List[Dict[str, Any]] = []
        for raw in payload.get("operations") or []:
            if not isinstance(raw, dict):
                continue
            action = str(raw.get("action", "") or raw.get("op", "") or "").strip().lower()
            if action == "mark_superseded":
                candidate = candidate_map.get(str(raw.get("candidate_id", "") or "").strip())
                if candidate is None:
                    candidate = hash_to_candidate.get(str(raw.get("hash", "") or "").strip())
                if candidate is None:
                    candidate_id = str(raw.get("candidate_id", "") or "").strip()
                    raw_hash = str(raw.get("hash", "") or "").strip()
                    logger.warning(
                        f"记忆修正计划引用了候选集外的目标: action={action} candidate_id={candidate_id} hash={raw_hash}"
                    )
                    continue
                operations.append(
                    {
                        "action": "mark_superseded",
                        "candidate_id": str(candidate.get("candidate_id", "") or ""),
                        "target_type": str(candidate.get("target_type", "") or ""),
                        "hash": str(candidate.get("hash", "") or ""),
                        "reason": str(raw.get("reason", "") or payload.get("reason", "") or request_text).strip(),
                        "valid_to": optional_float(raw.get("valid_to")),
                    }
                )
                continue
            if action == "ingest_text":
                text = str(raw.get("text", "") or "").strip()
                if not text:
                    continue
                operation: Dict[str, Any] = {
                    "action": "ingest_text",
                    "text": text,
                    "source_type": str(
                        raw.get("source_type", "") or ("person_fact" if person_id else "memory")
                    ).strip(),
                    "chat_id": str(raw.get("chat_id", "") or chat_id).strip(),
                    "person_ids": merge_argument_tokens(raw.get("person_ids"), [person_id]),
                    "participants": argument_tokens(raw.get("participants")),
                    "tags": merge_argument_tokens(raw.get("tags"), ["fuzzy_modify"]),
                    "relations": self._normalize_fuzzy_modify_relations(raw.get("relations")),
                    "valid_from": optional_float(raw.get("valid_from")),
                    "reason": str(raw.get("reason", "") or payload.get("reason", "") or request_text).strip(),
                }
                operations.append(operation)
                continue
            if action == "refresh_person_profile":
                target_person_id = str(raw.get("person_id", "") or person_id).strip()
                if target_person_id:
                    operations.append({"action": "refresh_person_profile", "person_id": target_person_id})
        operations = operations[: max(1, max_targets * 2)]
        target_count = sum(1 for item in operations if item.get("action") == "mark_superseded")
        if target_count > max_targets:
            kept = 0
            limited: List[Dict[str, Any]] = []
            for item in operations:
                if item.get("action") != "mark_superseded":
                    limited.append(item)
                    continue
                kept += 1
                if kept <= max_targets:
                    limited.append(item)
            operations = limited
        if operations and not any(item.get("action") == "refresh_person_profile" for item in operations) and person_id:
            operations.append({"action": "refresh_person_profile", "person_id": person_id})
        return {
            "scope": scope,
            "request_text": request_text,
            "person_id": person_id,
            "chat_id": chat_id,
            "confidence": confidence,
            "risk_level": str(payload.get("risk_level", "medium") or "medium").strip(),
            "reason": str(payload.get("reason", "") or "").strip(),
            "operations": operations,
        }

    def _normalize_fuzzy_modify_candidate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        evidence_type = str(item.get("evidence_type", "") or item.get("type", "") or "").strip()
        target_type = "relation" if evidence_type == "relation" else "paragraph"
        hash_value = str(item.get("hash", "") or "").strip()
        metadata = coerce_metadata_dict(item.get("metadata"))
        return {
            "candidate_id": f"{target_type}:{hash_value}",
            "target_type": target_type,
            "evidence_type": evidence_type,
            "hash": hash_value,
            "content": self._trim_text(str(item.get("content", "") or item.get("title", "") or ""), 420),
            "source": str(item.get("source", "") or metadata.get("source", "") or "").strip(),
            "metadata": metadata,
            "score": item.get("score"),
        }

    @staticmethod
    def _normalize_fuzzy_modify_relations(value: Any) -> List[Dict[str, Any]]:
        relations: List[Dict[str, Any]] = []
        for row in value or []:
            if not isinstance(row, dict):
                continue
            subject = str(row.get("subject", "") or "").strip()
            predicate = str(row.get("predicate", "") or "").strip()
            obj = str(row.get("object", "") or "").strip()
            if not (subject and predicate and obj):
                continue
            raw_confidence = row.get("confidence", 1.0)
            try:
                confidence = float(1.0 if raw_confidence is None else raw_confidence)
            except (TypeError, ValueError):
                confidence = 1.0
            relations.append(
                {
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "confidence": min(1.0, max(0.0, confidence)),
                    "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
                }
            )
        return relations

    def _build_fuzzy_modify_cascade_preview(self, *, operations: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        relations: List[Dict[str, Any]] = []
        entities: List[Dict[str, Any]] = []
        seen_relations: set[tuple[str, str]] = set()
        seen_entities: set[tuple[str, str]] = set()
        for operation in operations or []:
            if not isinstance(operation, dict):
                continue
            if operation.get("action") != "mark_superseded":
                continue
            if str(operation.get("target_type", "") or "").strip() != "paragraph":
                continue
            paragraph_hash = str(operation.get("hash", "") or "").strip()
            if not paragraph_hash:
                continue
            cascade = self._build_fuzzy_modify_paragraph_cascade(
                paragraph_hash=paragraph_hash,
                reason=str(operation.get("reason", "") or "").strip(),
                preview_only=True,
                plan_id="",
            )
            for item in cascade.get("relations", []):
                if not isinstance(item, dict):
                    continue
                relation_hash = str(item.get("relation_hash", "") or "").strip()
                key = (paragraph_hash, relation_hash)
                if not relation_hash or key in seen_relations:
                    continue
                seen_relations.add(key)
                relations.append(item)
            for item in cascade.get("entities", []):
                if not isinstance(item, dict):
                    continue
                entity_hash = str(item.get("entity_hash", "") or "").strip()
                key = (paragraph_hash, entity_hash)
                if not entity_hash or key in seen_entities:
                    continue
                seen_entities.add(key)
                entities.append(item)
        counts = {
            "relations": len(relations),
            "relations_mark_inactive": sum(1 for item in relations if item.get("action") == "mark_inactive"),
            "relations_mark_stale_evidence": sum(
                1 for item in relations if item.get("action") == "mark_stale_evidence"
            ),
            "relations_skipped_protected": sum(1 for item in relations if item.get("action") == "skipped_protected"),
            "entities": len(entities),
        }
        return {"relations": relations, "entities": entities, "counts": counts}

    def _build_fuzzy_modify_paragraph_cascade(
        self,
        *,
        paragraph_hash: str,
        reason: str,
        preview_only: bool,
        plan_id: str,
    ) -> Dict[str, List[Dict[str, Any]]]:
        assert self.metadata_store is not None
        paragraph_token = str(paragraph_hash or "").strip()
        if not paragraph_token:
            return {"relations": [], "entities": []}

        relations: List[Dict[str, Any]] = []
        raw_relations = self.metadata_store.get_paragraph_relations(paragraph_token)
        relation_hashes = [
            str(item.get("hash", "") or "").strip()
            for item in raw_relations
            if isinstance(item, dict) and str(item.get("hash", "") or "").strip()
        ]
        statuses = self.metadata_store.get_relation_status_batch(relation_hashes) if relation_hashes else {}
        now = time.time()
        for relation in raw_relations:
            if not isinstance(relation, dict):
                continue
            relation_hash = str(relation.get("hash", "") or "").strip()
            if not relation_hash:
                continue
            status = statuses.get(relation_hash, {})
            protected_until = optional_float(status.get("protected_until")) or 0.0
            is_pinned = bool(status.get("is_pinned", False))
            protected = is_pinned or protected_until > now
            if protected:
                action = "skipped_protected"
                action_reason = "relation_is_pinned" if is_pinned else "relation_is_temporarily_protected"
            elif self._relation_has_remaining_paragraphs(relation_hash, [paragraph_token]):
                action = "mark_stale_evidence"
                action_reason = "relation_has_other_active_paragraphs"
            else:
                action = "mark_inactive"
                action_reason = "only_supported_by_superseded_paragraph"
            relations.append(
                {
                    "paragraph_hash": paragraph_token,
                    "relation_hash": relation_hash,
                    "action": action,
                    "reason": action_reason,
                    "source_reason": reason,
                    "subject": str(relation.get("subject", "") or ""),
                    "predicate": str(relation.get("predicate", "") or ""),
                    "object": str(relation.get("object", "") or ""),
                    "is_pinned": is_pinned,
                    "protected_until": protected_until or None,
                    "is_inactive": bool(status.get("is_inactive", False)),
                    "inactive_since": status.get("inactive_since"),
                    "preview_only": preview_only,
                    "source_operation_id": (
                        self._fuzzy_modify_stale_source_operation_id(
                            plan_id=plan_id,
                            paragraph_hash=paragraph_token,
                            relation_hash=relation_hash,
                        )
                        if plan_id
                        else ""
                    ),
                }
            )

        entities: List[Dict[str, Any]] = []
        for entity in self.metadata_store.get_paragraph_entities(paragraph_token):
            if not isinstance(entity, dict):
                continue
            entity_hash = str(entity.get("hash", "") or "").strip()
            if not entity_hash:
                continue
            entities.append(
                {
                    "paragraph_hash": paragraph_token,
                    "entity_hash": entity_hash,
                    "action": "record_impact_only",
                    "reason": "entity_state_has_no_superseded_semantics",
                    "name": str(entity.get("name", "") or entity.get("entity", "") or ""),
                    "type": str(entity.get("type", "") or entity.get("entity_type", "") or ""),
                    "preview_only": preview_only,
                }
            )
        return {"relations": relations, "entities": entities}

    @staticmethod
    def _fuzzy_modify_stale_source_operation_id(
        *,
        plan_id: str,
        paragraph_hash: str,
        relation_hash: str,
    ) -> str:
        return f"{str(plan_id or '').strip()}:{str(paragraph_hash or '').strip()}:{str(relation_hash or '').strip()}"

    def _execute_fuzzy_modify_paragraph_cascade(
        self,
        *,
        paragraph_hash: str,
        plan_id: str,
        changed_at: float,
        reason: str,
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        paragraph_token = str(paragraph_hash or "").strip()
        plan_token = str(plan_id or "").strip()
        cascade = self._build_fuzzy_modify_paragraph_cascade(
            paragraph_hash=paragraph_token,
            reason=reason,
            preview_only=False,
            plan_id=plan_token,
        )
        result = {
            "relations_marked_inactive": [],
            "relations_marked_stale": [],
            "relations_skipped": [],
            "impacted_entities": cascade.get("entities", []),
            "stale_mark_snapshots": [],
        }

        for relation in cascade.get("relations", []):
            if not isinstance(relation, dict):
                continue
            relation_hash = str(relation.get("relation_hash", "") or "").strip()
            if not relation_hash:
                continue
            action = str(relation.get("action", "") or "").strip()
            if action == "skipped_protected":
                result["relations_skipped"].append(relation)
                continue
            if action == "mark_inactive":
                previous = self.metadata_store.get_relation(relation_hash)
                previous_metadata = coerce_metadata_dict((previous or {}).get("metadata"))
                patch = {
                    "memory_change": {
                        "change_id": plan_token,
                        "change_type": "paragraph_cascade_inactive",
                        "changed_at": changed_at,
                        "changed_by": "memory_correction",
                        "reason": reason,
                        "source_paragraph_hash": paragraph_token,
                    }
                }
                updated_metadata = self.metadata_store.update_relation_metadata(relation_hash, patch, merge=True)
                self.metadata_store.mark_relations_inactive([relation_hash], inactive_since=changed_at)
                result["relations_marked_inactive"].append(
                    {
                        **relation,
                        "previous_metadata": previous_metadata,
                        "updated_metadata": updated_metadata if isinstance(updated_metadata, dict) else {},
                        "previous_is_inactive": bool((previous or {}).get("is_inactive", False)),
                        "previous_inactive_since": (previous or {}).get("inactive_since"),
                    }
                )
                continue
            if action == "mark_stale_evidence":
                source_operation_id = self._fuzzy_modify_stale_source_operation_id(
                    plan_id=plan_token,
                    paragraph_hash=paragraph_token,
                    relation_hash=relation_hash,
                )
                previous_mark = self.metadata_store.get_paragraph_stale_relation_mark(
                    paragraph_hash=paragraph_token,
                    relation_hash=relation_hash,
                )
                written = self.metadata_store.upsert_paragraph_stale_relation_mark(
                    paragraph_hash=paragraph_token,
                    relation_hash=relation_hash,
                    reason=reason or "memory_correction_paragraph_superseded",
                    source_type="memory_correction",
                    source_id=plan_token,
                    source_operation_id=source_operation_id,
                )
                snapshot = {
                    "paragraph_hash": paragraph_token,
                    "relation_hash": relation_hash,
                    "source_type": "memory_correction",
                    "source_id": plan_token,
                    "source_operation_id": source_operation_id,
                    "previous_mark": previous_mark if isinstance(previous_mark, dict) else None,
                    "written_mark": written if isinstance(written, dict) else {},
                }
                result["stale_mark_snapshots"].append(snapshot)
                result["relations_marked_stale"].append({**relation, "written_mark": written or {}})
        return result

    async def _apply_fuzzy_modify_plan(
        self,
        *,
        plan_record: Dict[str, Any],
        requested_by: str = "webui",
        reason: str = "",
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        plan = plan_record.get("plan") if isinstance(plan_record.get("plan"), dict) else {}
        operations = [dict(item) for item in plan.get("operations") or [] if isinstance(item, dict)]
        change_id = str(plan_record.get("plan_id", "") or f"fuzzy_{int(time.time())}")
        changed_at = time.time()
        stored_ids: List[str] = []
        ingest_results: List[Dict[str, Any]] = []
        superseded_targets: List[Dict[str, Any]] = []

        supersede_hashes = [
            str(item.get("hash", "") or "").strip()
            for item in operations
            if item.get("action") == "mark_superseded" and str(item.get("hash", "") or "").strip()
        ]
        for index, operation in enumerate(
            [item for item in operations if item.get("action") == "ingest_text"], start=1
        ):
            op_reason = str(operation.get("reason", "") or reason or plan.get("request_text", "") or "").strip()
            metadata = {
                "memory_change": {
                    "change_id": change_id,
                    "change_type": "ingest_text",
                    "changed_at": changed_at,
                    "changed_by": requested_by,
                    "reason": op_reason,
                    "supersedes_hashes": supersede_hashes,
                    "valid_from": operation.get("valid_from") or changed_at,
                },
                "source_request": str(plan.get("request_text", "") or plan_record.get("request_text", "") or ""),
            }
            result = await self.ingest_text(
                external_id=f"{change_id}:ingest:{index}",
                source_type=str(operation.get("source_type", "") or "memory"),
                text=str(operation.get("text", "") or ""),
                chat_id=str(operation.get("chat_id", "") or plan.get("chat_id", "") or ""),
                person_ids=argument_tokens(operation.get("person_ids")),
                participants=argument_tokens(operation.get("participants")),
                timestamp=optional_float(operation.get("valid_from")) or changed_at,
                tags=argument_tokens(operation.get("tags")),
                metadata=metadata,
                relations=operation.get("relations") if isinstance(operation.get("relations"), list) else [],
                respect_filter=False,
            )
            result_ids = tokens(result.get("stored_ids"))
            stored_ids.extend(result_ids)
            ingest_results.append({"operation": operation, "result": result})

        replacement_hashes = list(stored_ids)
        for operation in [item for item in operations if item.get("action") == "mark_superseded"]:
            marked = self._mark_fuzzy_modify_target_superseded(
                operation=operation,
                change_id=change_id,
                changed_at=changed_at,
                changed_by=requested_by,
                replacement_hashes=replacement_hashes,
                plan_id=change_id,
                default_reason=reason or str(plan.get("request_text", "") or ""),
            )
            if marked:
                superseded_targets.append(marked)

        refreshed_profiles: List[Dict[str, Any]] = []
        for operation in [item for item in operations if item.get("action") == "refresh_person_profile"]:
            person_id = str(operation.get("person_id", "") or "").strip()
            if not person_id:
                continue
            refreshed_profiles.append(await self.refresh_person_profile(person_id))

        if superseded_targets:
            self._current_effective_filter_cache = {"checked_at": 0.0, "needed": True}
            self._rebuild_graph_from_metadata()
            self._persist()

        return {
            "success": bool(stored_ids or superseded_targets or refreshed_profiles),
            "stored_ids": stored_ids,
            "ingest_results": ingest_results,
            "superseded_targets": superseded_targets,
            "refreshed_profiles": refreshed_profiles,
            "changed_at": changed_at,
            "changed_by": requested_by,
            "reason": reason,
        }

    def _mark_fuzzy_modify_target_superseded(
        self,
        *,
        operation: Dict[str, Any],
        change_id: str,
        changed_at: float,
        changed_by: str,
        replacement_hashes: Sequence[str],
        plan_id: str,
        default_reason: str = "",
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        target_type = str(operation.get("target_type", "") or "").strip()
        hash_value = str(operation.get("hash", "") or "").strip()
        if target_type not in {"paragraph", "relation"} or not hash_value:
            return {}
        valid_to = optional_float(operation.get("valid_to")) or changed_at
        reason = str(operation.get("reason", "") or default_reason or "").strip()
        patch = {
            "memory_change": {
                "change_id": change_id,
                "change_type": "mark_superseded",
                "changed_at": changed_at,
                "changed_by": changed_by,
                "reason": reason,
                "valid_to": valid_to,
                "superseded_by_hashes": [
                    str(item or "").strip() for item in replacement_hashes if str(item or "").strip()
                ],
            }
        }
        if target_type == "paragraph":
            previous = self.metadata_store.get_paragraph(hash_value)
            if previous is None:
                return {}
            previous_metadata = coerce_metadata_dict(previous.get("metadata"))
            updated = self.metadata_store.update_paragraph_metadata(hash_value, patch, merge=True)
            if updated is None:
                return {}
            cascade = self._execute_fuzzy_modify_paragraph_cascade(
                paragraph_hash=hash_value,
                plan_id=plan_id,
                changed_at=changed_at,
                reason=reason,
            )
            return {
                "target_type": target_type,
                "hash": hash_value,
                "previous_metadata": previous_metadata,
                "updated_metadata": updated,
                "cascade": cascade,
            }
        previous = self.metadata_store.get_relation(hash_value)
        if previous is None:
            return {}
        previous_metadata = coerce_metadata_dict(previous.get("metadata"))
        updated = self.metadata_store.update_relation_metadata(hash_value, patch, merge=True)
        if updated is None:
            return {}
        self.metadata_store.mark_relations_inactive([hash_value], inactive_since=valid_to)
        return {
            "target_type": target_type,
            "hash": hash_value,
            "previous_metadata": previous_metadata,
            "updated_metadata": updated,
            "previous_is_inactive": bool(previous.get("is_inactive", False)),
            "previous_inactive_since": previous.get("inactive_since"),
        }

    @staticmethod
    def _normalize_fuzzy_modify_scope(scope: str) -> str:
        token = str(scope or "").strip().lower()
        aliases = {
            "profile": "person_profile",
            "person": "person_profile",
            "person_fact": "person_profile",
            "memory": "memory",
            "general": "memory",
            "chat": "memory",
        }
        return aliases.get(token, token or "person_profile")

    @staticmethod
    def _fuzzy_modify_cfg_enabled() -> bool:
        return fuzzy_modify_cfg_enabled()

    @staticmethod
    def _fuzzy_modify_cfg_auto_execute_enabled() -> bool:
        return fuzzy_modify_cfg_auto_execute_enabled()

    @staticmethod
    def _fuzzy_modify_cfg_confirm_threshold() -> float:
        return fuzzy_modify_cfg_confirm_threshold()

    @staticmethod
    def _fuzzy_modify_cfg_candidate_limit() -> int:
        return fuzzy_modify_cfg_candidate_limit()

    @staticmethod
    def _fuzzy_modify_cfg_max_targets() -> int:
        return fuzzy_modify_cfg_max_targets()

    @staticmethod
    def _fuzzy_modify_cfg_allow_global_scope() -> bool:
        return fuzzy_modify_cfg_allow_global_scope()
