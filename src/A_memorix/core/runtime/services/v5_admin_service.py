from __future__ import annotations

from math import isfinite
from typing import Any, Dict, List, Optional

import time

from ...utils.memory_lifecycle_policy import (
    RelationLifecycleEvent,
    RelationLifecycleState,
    retention_at,
)
from .base import KernelServiceBase


class MemoryV5AdminService(KernelServiceBase):
    async def maintain_memory(
        self,
        *,
        action: str,
        target: str = "",
        hours: Optional[float] = None,
        reason: str = "",
        limit: int = 50,
    ) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store
        act = str(action or "").strip().lower()
        if act == "recycle_bin":
            items = self.metadata_store.get_deleted_relations(limit=max(1, int(limit or 50)))
            return {"success": True, "items": items, "count": len(items)}

        hashes = (
            self._resolve_deleted_relation_hashes(target) if act == "restore" else self._resolve_relation_hashes(target)
        )
        if not hashes:
            return {"success": False, "detail": "未命中可维护关系"}

        if act in {"reinforce", "freeze"}:
            result = self._apply_v5_relation_action(action=act, hashes=hashes)
            if not result.get("success"):
                return {"success": False, "detail": str(result.get("error", "生命周期操作失败"))}
            return result
        elif act == "protect":
            ttl_seconds = max(0.0, float(hours or 0.0)) * 3600.0
            self.metadata_store.protect_relations(hashes, ttl_seconds=ttl_seconds, is_pinned=ttl_seconds <= 0)
        elif act == "restore":
            return await self._restore_relation_hashes(hashes, reason=reason)
        else:
            return {"success": False, "detail": f"不支持的维护动作: {act}"}

        self._persist()
        return {"success": True, "detail": f"{act} {len(hashes)} 条关系"}

    async def memory_v5_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store

        act = str(action or "").strip().lower()
        target = str(kwargs.get("target", "") or kwargs.get("query", "") or "").strip()
        reason = str(kwargs.get("reason", "") or "").strip()
        updated_by = str(kwargs.get("updated_by", "") or kwargs.get("requested_by", "") or "").strip()
        limit = max(1, int(kwargs.get("limit", 50) or 50))

        if act == "recycle_bin":
            items = self.metadata_store.get_deleted_relations(limit=limit)
            return {"success": True, "items": items, "count": len(items)}

        if act == "status":
            return self._memory_v5_status(target=target, limit=limit)

        if act == "restore":
            hashes = self._resolve_deleted_relation_hashes(target)
            if not hashes:
                return {"success": False, "error": "未命中可恢复关系"}
            result = await self._restore_relation_hashes(
                hashes,
                requested_by=updated_by,
                reason=reason,
            )
            operation = self.metadata_store.record_v5_operation(
                action=act,
                target=target,
                resolved_hashes=hashes,
                reason=reason,
                updated_by=updated_by,
                result=result,
            )
            return {"operation": operation, **result}

        hashes = self._resolve_relation_hashes(target)
        if not hashes:
            return {"success": False, "error": "未命中可维护关系"}

        result = self._apply_v5_relation_action(
            action=act,
            hashes=hashes,
            strength=float(kwargs.get("strength", 1.0) or 1.0),
        )
        operation = self.metadata_store.record_v5_operation(
            action=act,
            target=target,
            resolved_hashes=hashes,
            reason=reason,
            updated_by=updated_by,
            result=result,
        )
        return {"success": bool(result.get("success", False)), "operation": operation, **result}

    def _resolve_relation_hashes(self, target: str) -> List[str]:
        assert self.metadata_store
        token = str(target or "").strip()
        if not token:
            return []
        if len(token) == 64 and all(ch in "0123456789abcdef" for ch in token.lower()):
            return [token] if self.metadata_store.get_relation(token) is not None else []
        hashes = self.metadata_store.search_relation_hashes_by_text(token, limit=10)
        if hashes:
            return hashes
        return [
            str(row.get("hash", "") or "")
            for row in self.metadata_store.get_relations(subject=token)[:10]
            if str(row.get("hash", "")).strip()
        ]

    def _resolve_deleted_relation_hashes(self, target: str) -> List[str]:
        assert self.metadata_store
        token = str(target or "").strip()
        if not token:
            return []
        if len(token) == 64 and all(ch in "0123456789abcdef" for ch in token.lower()):
            return [token] if self.metadata_store.get_deleted_relation(token) is not None else []
        return self.metadata_store.search_deleted_relation_hashes_by_text(token, limit=10)

    def _memory_v5_status(self, *, target: str = "", limit: int = 50) -> Dict[str, Any]:
        assert self.metadata_store
        now = time.time()
        summary = self.metadata_store.get_memory_status_summary(now)
        payload: Dict[str, Any] = {
            "success": True,
            **summary,
            "config": {
                "half_life_hours": float(self._cfg("memory.half_life_hours", 24.0)),
                "base_decay_interval_hours": float(self._cfg("memory.base_decay_interval_hours", 1.0)),
                "prune_threshold": float(self._cfg("memory.prune_threshold", 0.1)),
                "freeze_duration_hours": float(self._cfg("memory.freeze_duration_hours", 24.0)),
                "access_reinforcement_cooldown_minutes": float(
                    self._cfg("memory.access_reinforcement_cooldown_minutes", 60.0)
                ),
            },
            "last_maintenance_at": self._last_maintenance_at,
        }
        token = str(target or "").strip()
        if not token:
            return payload

        active_hashes = self._resolve_relation_hashes(token)[:limit]
        deleted_hashes = self._resolve_deleted_relation_hashes(token)[:limit]
        active_statuses = self.metadata_store.get_relation_status_batch(active_hashes)
        policy = self._maintenance_service._relation_lifecycle_policy()
        items: List[Dict[str, Any]] = []
        for hash_value in active_hashes:
            relation = self.metadata_store.get_relation(hash_value) or {}
            status = active_statuses.get(hash_value, {})
            if not relation or not status:
                continue
            lifecycle_state = RelationLifecycleState(
                strength=float(status["retention_strength"]),
                anchor_at=float(status["retention_anchor_at"]),
                is_inactive=bool(status.get("is_inactive")),
                inactive_since=status.get("inactive_since"),
                inactive_reason=str(status.get("inactive_reason", "") or "") or None,
            )
            items.append(
                {
                    "hash": hash_value,
                    "subject": str(relation.get("subject", "") or ""),
                    "predicate": str(relation.get("predicate", "") or ""),
                    "object": str(relation.get("object", "") or ""),
                    "state": "inactive" if bool(status.get("is_inactive")) else "active",
                    "is_pinned": bool(status.get("is_pinned", False)),
                    "temp_protected": bool(float(status.get("protected_until") or 0.0) > now),
                    "protected_until": status.get("protected_until"),
                    "last_reinforced": status.get("last_reinforced"),
                    "last_access_reinforced_at": status.get("last_access_reinforced_at"),
                    "confidence": float(status.get("confidence", 0.0) or 0.0),
                    "retention_score": retention_at(lifecycle_state, now=now, policy=policy),
                    "retention_anchor_at": status.get("retention_anchor_at"),
                    "next_lifecycle_at": status.get("next_lifecycle_at"),
                    "reinforcement_count": int(status.get("reinforcement_count", 0) or 0),
                    "inactive_reason": str(status.get("inactive_reason", "") or ""),
                }
            )
        for hash_value in deleted_hashes:
            relation = self.metadata_store.get_deleted_relation(hash_value) or {}
            items.append(
                {
                    "hash": hash_value,
                    "subject": str(relation.get("subject", "") or ""),
                    "predicate": str(relation.get("predicate", "") or ""),
                    "object": str(relation.get("object", "") or ""),
                    "state": "deleted",
                    "is_pinned": bool(relation.get("is_pinned", False)),
                    "temp_protected": False,
                    "protected_until": relation.get("protected_until"),
                    "last_reinforced": relation.get("last_reinforced"),
                    "last_access_reinforced_at": relation.get("last_access_reinforced_at"),
                    "confidence": float(relation.get("confidence", 0.0) or 0.0),
                    "retention_strength": float(relation.get("retention_strength", 0.0) or 0.0),
                    "retention_anchor_at": relation.get("retention_anchor_at"),
                    "reinforcement_count": int(relation.get("reinforcement_count", 0) or 0),
                    "inactive_reason": str(relation.get("inactive_reason", "") or ""),
                    "deleted_at": relation.get("deleted_at"),
                }
            )
        payload["items"] = items[:limit]
        payload["count"] = len(payload["items"])
        payload["target"] = token
        return payload

    async def _restore_relation_hashes(
        self,
        hashes: List[str],
        *,
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        delete_service = self._delete_admin_service
        return await delete_service.restore_deleted_relations(
            hashes,
            requested_by=requested_by,
            reason=reason,
        )

    def _apply_v5_relation_action(self, *, action: str, hashes: List[str], strength: float = 1.0) -> Dict[str, Any]:
        assert self.metadata_store
        act = str(action or "").strip().lower()
        normalized = [str(item or "").strip() for item in hashes if str(item or "").strip()]
        if not normalized:
            return {"success": False, "error": "未命中可维护关系"}

        now = time.time()
        strength_value = float(strength)
        if not isfinite(strength_value) or strength_value <= 0.0:
            return {"success": False, "error": "strength 必须是大于0的有限数"}
        detail = ""
        lifecycle_service = self._maintenance_service

        if act == "reinforce":
            transitions = type(lifecycle_service).apply_relation_lifecycle_event(
                lifecycle_service,
                normalized,
                event=RelationLifecycleEvent.REINFORCE,
                strength=strength_value,
                now=now,
            )
            protect_hours = max(1.0, 24.0 * strength_value)
            logical_now = max(
                [now]
                + [
                    float(item["retention_anchor_at"])
                    for item in transitions
                    if item.get("retention_anchor_at") is not None
                ]
            )
            self.metadata_store.update_relations_protection(
                normalized,
                protected_until=logical_now + protect_hours * 3600.0,
                last_reinforced=logical_now,
            )
            detail = f"reinforce {len(normalized)} 条关系"
        elif act == "weaken":
            transitions = type(lifecycle_service).apply_relation_lifecycle_event(
                lifecycle_service,
                normalized,
                event=RelationLifecycleEvent.WEAKEN,
                strength=strength_value,
                now=now,
            )
            detail = f"weaken {len(normalized)} 条关系"
        elif act == "remember_forever":
            transitions = type(lifecycle_service).apply_relation_lifecycle_event(
                lifecycle_service,
                normalized,
                event=RelationLifecycleEvent.REINFORCE,
                strength=strength_value,
                now=now,
            )
            self.metadata_store.update_relations_protection(normalized, protected_until=0.0, is_pinned=True)
            detail = f"remember_forever {len(normalized)} 条关系"
        elif act == "forget":
            self.metadata_store.update_relations_protection(normalized, protected_until=0.0, is_pinned=False)
            transitions = type(lifecycle_service).apply_relation_lifecycle_event(
                lifecycle_service,
                normalized,
                event=RelationLifecycleEvent.FORGET,
                strength=strength_value,
                now=now,
            )
            detail = f"forget {len(normalized)} 条关系"
        elif act == "freeze":
            transitions = type(lifecycle_service).apply_relation_lifecycle_event(
                lifecycle_service,
                normalized,
                event=RelationLifecycleEvent.FREEZE,
                strength=strength_value,
                now=now,
            )
            detail = f"freeze {len(normalized)} 条关系"
        else:
            return {"success": False, "error": f"不支持的 V5 动作: {act}"}

        self._persist()
        statuses = self.metadata_store.get_relation_status_batch(normalized)
        retention_scores = {
            str(item["hash"]): float(item["retention_score"])
            for item in transitions
        }
        return {
            "success": True,
            "detail": detail,
            "hashes": normalized,
            "count": len(normalized),
            "retention_scores": retention_scores,
            "statuses": statuses,
        }
