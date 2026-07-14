from __future__ import annotations

from typing import Any, Dict, List, Optional

import time

from ...utils import profile_policy
from ...utils.feedback_policy import (
    feedback_cfg_profile_force_refresh_on_read,
    feedback_cfg_profile_refresh_enabled,
    feedback_cfg_reconcile_batch_size,
)
from ...utils.metadata import coerce_metadata_dict
from ...utils.profile_evidence import profile_evidence_type_from_source, profile_relation_content
from ...utils.runtime_payloads import tokens
from .base import KernelServiceBase


class MemoryProfileAdminService(KernelServiceBase):
    def _mark_person_active(self, person_id: str) -> None:
        token = str(person_id or "").strip()
        if not token:
            return
        self._active_person_timestamps[token] = time.time()

    def _enqueue_person_profile_refresh(self, person_id: str, *, reason: str = "") -> bool:
        if self.metadata_store is None or not bool(self._cfg("person_profile.enabled", True)):
            return False
        payload = self.metadata_store.enqueue_person_profile_refresh(
            person_id=person_id,
            reason=str(reason or "").strip() or "memory_ingest",
        )
        return isinstance(payload, dict)

    def _has_pending_person_profile_refresh(self, person_id: str) -> bool:
        if self.metadata_store is None:
            return False
        request = self.metadata_store.get_person_profile_refresh_request(person_id)
        if not isinstance(request, dict):
            return False
        status = str(request.get("status", "") or "").strip().lower()
        if status in {"pending", "running"}:
            return True
        if status != "failed":
            return False
        return int(request.get("retry_count", 0) or 0) < profile_policy.person_profile_refresh_max_retry(self._cfg)

    @staticmethod
    def _empty_person_profile_response(*, person_id: str = "", person_name: str = "") -> Dict[str, Any]:
        return {
            "summary": "",
            "traits": [],
            "evidence": [],
            "person_id": str(person_id or "").strip(),
            "person_name": str(person_name or "").strip(),
            "profile_source": "",
            "has_manual_override": False,
        }

    async def _query_person_profile_with_feedback_refresh(
        self,
        *,
        person_id: str = "",
        person_keyword: str = "",
        limit: int = 10,
        force_refresh: bool = False,
        source_note: str,
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        assert self.person_profile_service is not None

        pid = str(person_id or "").strip()
        if not pid and person_keyword:
            pid = self.person_profile_service.resolve_person_id(str(person_keyword or "").strip())

        dirty_request = self.metadata_store.get_person_profile_refresh_request(pid) if pid else None
        should_force_refresh = bool(force_refresh)
        if (
            pid
            and feedback_cfg_profile_refresh_enabled()
            and feedback_cfg_profile_force_refresh_on_read()
            and isinstance(dirty_request, dict)
            and str(dirty_request.get("status", "") or "").strip().lower() in {"pending", "running", "failed"}
        ):
            should_force_refresh = True

        profile = await self.person_profile_service.query_person_profile(
            person_id=pid,
            person_keyword=str(person_keyword or "").strip(),
            top_k=max(1, int(limit or 10)),
            force_refresh=should_force_refresh,
            source_note=source_note,
        )
        payload = profile if isinstance(profile, dict) else {"success": False, "error": "invalid profile payload"}
        if dirty_request:
            payload["feedback_refresh_request"] = dirty_request
        if should_force_refresh and dirty_request and not bool(payload.get("success")):
            payload.setdefault("error", "feedback_refresh_failed")
            payload["feedback_refresh_failed"] = True
        return payload

    def _build_person_profile_response(
        self,
        profile: Dict[str, Any],
        *,
        requested_person_id: str,
        limit: int,
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        if not bool(profile.get("success")):
            return self._empty_person_profile_response(
                person_id=str(profile.get("person_id", "") or requested_person_id),
                person_name=str(profile.get("person_name", "") or ""),
            )

        evidence: List[Dict[str, Any]] = []
        evidence_limit = max(1, int(limit or 10))
        for hash_value in profile.get("evidence_ids", [])[:evidence_limit]:
            paragraph = self.metadata_store.get_paragraph(hash_value)
            if paragraph is not None:
                evidence.append(
                    {
                        "hash": hash_value,
                        "content": str(paragraph.get("content", "") or "")[:220],
                        "metadata": paragraph.get("metadata", {}) or {},
                        "type": "paragraph",
                    }
                )
                continue

            relation = self.metadata_store.get_relation(hash_value)
            if relation is not None:
                evidence.append(
                    {
                        "hash": hash_value,
                        "content": " ".join(
                            [
                                str(relation.get("subject", "") or "").strip(),
                                str(relation.get("predicate", "") or "").strip(),
                                str(relation.get("object", "") or "").strip(),
                            ]
                        ).strip(),
                        "metadata": {
                            "confidence": relation.get("confidence"),
                            "source_paragraph": relation.get("source_paragraph"),
                        },
                        "type": "relation",
                    }
                )

        evidence = self._filter_user_visible_hits(evidence)
        text = str(profile.get("profile_text", "") or "").strip()
        traits = [line.strip("- ").strip() for line in text.splitlines() if line.strip()][:8]
        return {
            "summary": text,
            "traits": traits,
            "evidence": evidence,
            "person_id": str(profile.get("person_id", "") or requested_person_id),
            "person_name": str(profile.get("person_name", "") or ""),
            "profile_source": str(profile.get("profile_source", "") or "auto_snapshot"),
            "has_manual_override": bool(profile.get("has_manual_override", False)),
        }

    async def get_person_profile(self, *, person_id: str, chat_id: str = "", limit: int = 10) -> Dict[str, Any]:
        del chat_id
        await self.initialize()
        assert self.metadata_store is not None
        assert self.person_profile_service is not None
        self._mark_person_active(person_id)
        profile = await self._query_person_profile_with_feedback_refresh(
            person_id=person_id,
            limit=max(4, int(limit or 10)),
            source_note="sdk_memory_kernel.get_person_profile",
        )
        return self._build_person_profile_response(profile, requested_person_id=person_id, limit=limit)

    async def refresh_person_profile(
        self, person_id: str, limit: int = 10, *, mark_active: bool = True
    ) -> Dict[str, Any]:
        await self.initialize()
        assert self.person_profile_service
        if mark_active:
            self._mark_person_active(person_id)
        profile = await self.person_profile_service.query_person_profile(
            person_id=person_id,
            top_k=max(4, int(limit or 10)),
            force_refresh=True,
            source_note="sdk_memory_kernel.refresh_person_profile",
        )
        return profile if isinstance(profile, dict) else {}

    async def memory_profile_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store is not None
        assert self.person_profile_service is not None

        act = str(action or "").strip().lower()
        if act == "query":
            profile = await self._query_person_profile_with_feedback_refresh(
                person_id=str(kwargs.get("person_id", "") or "").strip(),
                person_keyword=str(kwargs.get("person_keyword", "") or kwargs.get("keyword", "") or "").strip(),
                limit=max(1, int(kwargs.get("limit", kwargs.get("top_k", 12)) or 12)),
                force_refresh=bool(kwargs.get("force_refresh", False)),
                source_note="sdk_memory_kernel.memory_profile_admin.query",
            )
            return profile if isinstance(profile, dict) else {"success": False, "error": "invalid profile payload"}

        if act == "evidence":
            return await self._profile_evidence_admin(
                person_id=str(kwargs.get("person_id", "") or "").strip(),
                person_keyword=str(kwargs.get("person_keyword", "") or kwargs.get("keyword", "") or "").strip(),
                limit=max(1, int(kwargs.get("limit", kwargs.get("top_k", 12)) or 12)),
                force_refresh=bool(kwargs.get("force_refresh", False)),
            )

        if act == "correct_evidence":
            return await self._profile_correct_evidence_admin(
                person_id=str(kwargs.get("person_id", "") or "").strip(),
                person_keyword=str(kwargs.get("person_keyword", "") or kwargs.get("keyword", "") or "").strip(),
                evidence_type=str(kwargs.get("evidence_type", "") or "").strip(),
                hash_value=str(kwargs.get("hash", "") or kwargs.get("hash_value", "") or "").strip(),
                requested_by=str(kwargs.get("requested_by", "") or "webui").strip(),
                reason=str(kwargs.get("reason", "") or "profile_evidence_correction").strip(),
                refresh=bool(kwargs.get("refresh", True)),
                limit=max(1, int(kwargs.get("limit", kwargs.get("top_k", 12)) or 12)),
            )

        if act == "status":
            summary = self.metadata_store.get_person_profile_refresh_summary(
                failed_limit=max(1, int(kwargs.get("limit", 20) or 20))
            )
            return {"success": True, **summary}

        if act == "process_pending":
            result = await self._process_feedback_profile_refresh_batch(
                limit=max(
                    1,
                    int(
                        kwargs.get("limit", feedback_cfg_reconcile_batch_size()) or feedback_cfg_reconcile_batch_size()
                    ),
                )
            )
            return {"success": True, **result}

        if act == "list":
            limit = max(1, int(kwargs.get("limit", 50) or 50))
            rows = self.metadata_store.query(
                """
                SELECT s.person_id, s.profile_version, s.profile_text, s.updated_at, s.expires_at, s.source_note
                FROM person_profile_snapshots s
                JOIN (
                    SELECT person_id, MAX(profile_version) AS max_version
                    FROM person_profile_snapshots
                    GROUP BY person_id
                ) latest
                  ON latest.person_id = s.person_id
                 AND latest.max_version = s.profile_version
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            items = []
            for row in rows:
                person_id = str(row.get("person_id", "") or "").strip()
                override = self.metadata_store.get_person_profile_override(person_id)
                items.append(
                    {
                        "person_id": person_id,
                        "profile_version": int(row.get("profile_version", 0) or 0),
                        "profile_text": str(row.get("profile_text", "") or ""),
                        "updated_at": row.get("updated_at"),
                        "expires_at": row.get("expires_at"),
                        "source_note": str(row.get("source_note", "") or ""),
                        "has_manual_override": bool(override),
                        "manual_override": override,
                    }
                )
            return {"success": True, "items": items, "count": len(items)}

        if act == "set_override":
            person_id = str(kwargs.get("person_id", "") or "").strip()
            override = self.metadata_store.set_person_profile_override(
                person_id=person_id,
                override_text=str(kwargs.get("override_text", "") or kwargs.get("text", "") or ""),
                updated_by=str(kwargs.get("updated_by", "") or ""),
                source=str(kwargs.get("source", "") or "memory_profile_admin"),
            )
            return {"success": True, "override": override}

        if act == "delete_override":
            person_id = str(kwargs.get("person_id", "") or "").strip()
            deleted = self.metadata_store.delete_person_profile_override(person_id)
            return {"success": bool(deleted), "deleted": bool(deleted), "person_id": person_id}

        return {"success": False, "error": f"不支持的 profile action: {act}"}

    @staticmethod
    def _profile_evidence_type_from_source(source: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        return profile_evidence_type_from_source(source, metadata)

    @staticmethod
    def _profile_relation_content(relation: Dict[str, Any]) -> str:
        return profile_relation_content(relation)

    def _build_profile_relation_evidence_item(self, relation: Dict[str, Any], *, index: int) -> Dict[str, Any]:
        relation_hash = str(relation.get("hash", "") or "").strip()
        metadata = coerce_metadata_dict(relation.get("metadata"))
        return {
            "evidence_key": f"relation:{relation_hash or index}",
            "evidence_type": "relation",
            "hash": relation_hash,
            "content": self._profile_relation_content(relation),
            "source": str(relation.get("source_paragraph", "") or metadata.get("source", "") or "").strip(),
            "source_type": "relation",
            "metadata": metadata,
            "score": None,
            "confidence": relation.get("confidence"),
            "correction_mode": "delete_relation",
            "deletable": bool(relation_hash),
            "not_deletable_reason": "" if relation_hash else "缺少关系 hash",
            "raw": relation,
        }

    def _build_profile_paragraph_evidence_item(
        self,
        item: Dict[str, Any],
        *,
        index: int,
        fallback_hash: str = "",
    ) -> Dict[str, Any]:
        hash_value = str(item.get("hash", "") or fallback_hash or "").strip()
        metadata = coerce_metadata_dict(item.get("metadata"))
        source = str(item.get("source", "") or metadata.get("source", "") or "").strip()
        content = str(item.get("content", "") or "").strip()
        source_type = self._profile_evidence_type_from_source(source, metadata)
        is_deleted = False
        if hash_value:
            try:
                paragraph = self.metadata_store.get_paragraph(hash_value) if self.metadata_store else None
            except Exception:
                paragraph = None
            if isinstance(paragraph, dict):
                paragraph_metadata = coerce_metadata_dict(paragraph.get("metadata"))
                metadata = {**paragraph_metadata, **metadata}
                source = source or str(paragraph.get("source", "") or "").strip()
                content = content or str(paragraph.get("content", "") or "").strip()
                source_type = self._profile_evidence_type_from_source(source, metadata)
                is_deleted = bool(paragraph.get("is_deleted", 0))
        return {
            "evidence_key": f"{source_type}:{hash_value or index}",
            "evidence_type": source_type,
            "hash": hash_value,
            "content": self._trim_text(content, 260),
            "source": source,
            "source_type": source_type,
            "metadata": metadata,
            "score": item.get("score"),
            "confidence": None,
            "correction_mode": "delete_paragraph",
            "deletable": bool(hash_value) and not is_deleted,
            "not_deletable_reason": ""
            if hash_value and not is_deleted
            else ("证据已删除" if is_deleted else "缺少段落 hash"),
            "raw": item,
        }

    def _build_profile_evidence_items(self, profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        assert self.metadata_store is not None
        evidence: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def append(item: Dict[str, Any]) -> None:
            evidence_type = str(item.get("evidence_type", "") or "").strip()
            hash_value = str(item.get("hash", "") or "").strip()
            key = (evidence_type, hash_value or str(item.get("evidence_key", "") or ""))
            if not key[0] or key in seen:
                return
            seen.add(key)
            evidence.append(item)

        for index, relation in enumerate(profile.get("relation_edges") or [], start=1):
            if isinstance(relation, dict):
                append(self._build_profile_relation_evidence_item(relation, index=index))

        for index, item in enumerate(profile.get("vector_evidence") or [], start=1):
            if isinstance(item, dict):
                append(self._build_profile_paragraph_evidence_item(item, index=index))

        for index, hash_value in enumerate(tokens(profile.get("evidence_ids")), start=1):
            if any(str(item.get("hash", "") or "").strip() == hash_value for item in evidence):
                continue
            paragraph = self.metadata_store.get_paragraph(hash_value)
            if isinstance(paragraph, dict):
                append(
                    self._build_profile_paragraph_evidence_item(
                        {
                            "hash": hash_value,
                            "content": str(paragraph.get("content", "") or ""),
                            "source": str(paragraph.get("source", "") or ""),
                            "metadata": coerce_metadata_dict(paragraph.get("metadata")),
                        },
                        index=index,
                    )
                )
                continue
            relation = self.metadata_store.get_relation(hash_value)
            if isinstance(relation, dict):
                append(self._build_profile_relation_evidence_item(relation, index=index))

        return evidence

    def _profile_evidence_response(
        self, profile: Dict[str, Any], *, requested_person_id: str, limit: int
    ) -> Dict[str, Any]:
        if not bool(profile.get("success")):
            return {
                "success": False,
                "error": str(profile.get("error", "") or "人物画像查询失败"),
                "person_id": str(profile.get("person_id", "") or requested_person_id),
                "evidence": [],
            }
        evidence = self._build_profile_evidence_items(profile)
        return {
            "success": True,
            "person_id": str(profile.get("person_id", "") or requested_person_id),
            "person_name": str(profile.get("person_name", "") or ""),
            "profile_text": str(profile.get("profile_text", "") or ""),
            "auto_profile_text": str(profile.get("auto_profile_text", "") or profile.get("profile_text", "") or ""),
            "profile_version": profile.get("profile_version"),
            "updated_at": profile.get("updated_at"),
            "expires_at": profile.get("expires_at"),
            "profile_source": str(profile.get("profile_source", "") or "auto_snapshot"),
            "has_manual_override": bool(profile.get("has_manual_override", False)),
            "manual_override_text": str(profile.get("manual_override_text", "") or ""),
            "evidence": evidence[: max(1, int(limit or 12))],
            "evidence_count": len(evidence),
            "raw_profile": profile,
        }

    async def _profile_evidence_admin(
        self,
        *,
        person_id: str = "",
        person_keyword: str = "",
        limit: int = 12,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        profile = await self._query_person_profile_with_feedback_refresh(
            person_id=person_id,
            person_keyword=person_keyword,
            limit=max(1, int(limit or 12)),
            force_refresh=force_refresh,
            source_note="sdk_memory_kernel.memory_profile_admin.evidence",
        )
        requested_person_id = (
            str(profile.get("person_id", "") or person_id or "").strip() if isinstance(profile, dict) else person_id
        )
        return self._profile_evidence_response(
            profile if isinstance(profile, dict) else {}, requested_person_id=requested_person_id, limit=limit
        )

    async def _profile_correct_evidence_admin(
        self,
        *,
        person_id: str = "",
        person_keyword: str = "",
        evidence_type: str,
        hash_value: str,
        requested_by: str = "webui",
        reason: str = "profile_evidence_correction",
        refresh: bool = True,
        limit: int = 12,
    ) -> Dict[str, Any]:
        normalized_type = str(evidence_type or "").strip().lower()
        normalized_hash = str(hash_value or "").strip()
        if normalized_type not in {"relation", "paragraph", "person_fact", "chat_summary"}:
            return {"success": False, "error": "不支持的画像证据类型"}
        if not normalized_hash:
            return {"success": False, "error": "画像证据 hash 不能为空"}

        evidence_payload = await self._profile_evidence_admin(
            person_id=person_id,
            person_keyword=person_keyword,
            limit=max(50, int(limit or 12)),
            force_refresh=False,
        )
        if not bool(evidence_payload.get("success")):
            return evidence_payload
        matched = None
        for item in evidence_payload.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("hash", "") or "").strip() != normalized_hash:
                continue
            item_type = str(item.get("evidence_type", "") or "").strip().lower()
            if normalized_type == item_type or (
                normalized_type == "paragraph" and item_type in {"person_fact", "chat_summary"}
            ):
                matched = item
                break
        if matched is None:
            return {"success": False, "error": "当前画像证据中未找到目标 hash"}
        if not bool(matched.get("deletable", False)):
            return {
                "success": False,
                "error": str(matched.get("not_deletable_reason", "") or "该画像证据不可纠错"),
                "evidence": matched,
            }

        delete_mode = "relation" if normalized_type == "relation" else "paragraph"
        delete_result = await self._execute_delete_action(
            mode=delete_mode,
            selector={"hashes": [normalized_hash]},
            requested_by=requested_by or "webui",
            reason=reason or "profile_evidence_correction",
        )
        if bool(delete_result.get("success")):
            await self._invalidate_import_manifest_for_sources(delete_result)

        refreshed_profile: Dict[str, Any] = {}
        refreshed_evidence: Dict[str, Any] = {}
        if refresh and bool(delete_result.get("success")):
            refreshed_profile = await self.person_profile_service.query_person_profile(
                person_id=str(evidence_payload.get("person_id", "") or person_id),
                top_k=max(4, int(limit or 12)),
                force_refresh=True,
                source_note="sdk_memory_kernel.memory_profile_admin.correct_evidence",
            )
            refreshed_evidence = self._profile_evidence_response(
                refreshed_profile if isinstance(refreshed_profile, dict) else {},
                requested_person_id=str(evidence_payload.get("person_id", "") or person_id),
                limit=limit,
            )

        return {
            "success": bool(delete_result.get("success")),
            "person_id": str(evidence_payload.get("person_id", "") or person_id),
            "evidence": matched,
            "delete_result": delete_result,
            "operation_id": str(delete_result.get("operation_id", "") or ""),
            "refreshed_profile": refreshed_profile,
            "refreshed_evidence": refreshed_evidence,
            "error": str(delete_result.get("error", "") or ""),
        }
