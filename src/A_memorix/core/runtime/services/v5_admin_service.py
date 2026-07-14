from __future__ import annotations

from typing import Any, Dict, List, Optional

import time

from ...utils.runtime_payloads import tokens
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
        del reason
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

        if act == "reinforce":
            self.metadata_store.reinforce_relations(hashes)
        elif act == "freeze":
            self.metadata_store.mark_relations_inactive(hashes)
            self._rebuild_graph_from_metadata()
        elif act == "protect":
            ttl_seconds = max(0.0, float(hours or 0.0)) * 3600.0
            self.metadata_store.protect_relations(hashes, ttl_seconds=ttl_seconds, is_pinned=ttl_seconds <= 0)
        elif act == "restore":
            restored = sum(1 for hash_value in hashes if self.metadata_store.restore_relation(hash_value))
            if restored <= 0:
                return {"success": False, "detail": "未恢复任何关系"}
            self._rebuild_graph_from_metadata()
        else:
            return {"success": False, "detail": f"不支持的维护动作: {act}"}

        self._last_maintenance_at = time.time()
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
            result = await self._restore_relation_hashes(hashes)
            operation = self.metadata_store.record_v5_operation(
                action=act,
                target=target,
                resolved_hashes=hashes,
                reason=reason,
                updated_by=updated_by,
                result=result,
            )
            return {"success": bool(result.get("restored_count", 0) > 0), "operation": operation, **result}

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
                "half_life_hours": float(self._cfg("memory.half_life_hours", 24.0) or 24.0),
                "base_decay_interval_hours": float(self._cfg("memory.base_decay_interval_hours", 1.0) or 1.0),
                "prune_threshold": float(self._cfg("memory.prune_threshold", 0.1) or 0.1),
                "freeze_duration_hours": float(self._cfg("memory.freeze_duration_hours", 24.0) or 24.0),
            },
            "last_maintenance_at": self._last_maintenance_at,
        }
        token = str(target or "").strip()
        if not token:
            return payload

        active_hashes = self._resolve_relation_hashes(token)[:limit]
        deleted_hashes = self._resolve_deleted_relation_hashes(token)[:limit]
        active_statuses = self.metadata_store.get_relation_status_batch(active_hashes)
        items: List[Dict[str, Any]] = []
        for hash_value in active_hashes:
            relation = self.metadata_store.get_relation(hash_value) or {}
            status = active_statuses.get(hash_value, {})
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
                    "weight": float(status.get("weight", relation.get("confidence", 0.0)) or 0.0),
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
                    "weight": float(relation.get("confidence", 0.0) or 0.0),
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
        payloads: Optional[Dict[str, Dict[str, Any]]] = None,
        rebuild_graph: bool = True,
        persist: bool = True,
    ) -> Dict[str, Any]:
        assert self.metadata_store
        restored: List[str] = []
        failures: List[Dict[str, str]] = []
        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        payload_map = payloads or {}
        for hash_value in [str(item or "").strip() for item in hashes if str(item or "").strip()]:
            relation = self.metadata_store.restore_relation(hash_value)
            if relation is None:
                relation = self.metadata_store.get_relation(hash_value)
            if relation is None:
                failures.append({"hash": hash_value, "error": "relation 不存在"})
                continue
            payload = payload_map.get(hash_value) if isinstance(payload_map.get(hash_value), dict) else {}
            paragraph_hashes = tokens(payload.get("paragraph_hashes"))
            for paragraph_hash in paragraph_hashes:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO paragraph_relations (paragraph_hash, relation_hash)
                    VALUES (?, ?)
                    """,
                    (paragraph_hash, hash_value),
                )
            await self._ensure_relation_vector({**relation, "hash": hash_value})
            restored.append(hash_value)
        conn.commit()
        if restored and rebuild_graph:
            self._rebuild_graph_from_metadata()
        if restored and persist:
            self._persist()
        return {"restored_hashes": restored, "restored_count": len(restored), "failures": failures}

    def _adjust_relation_confidence(self, hashes: List[str], *, delta: float) -> Dict[str, float]:
        assert self.metadata_store
        normalized = [str(item or "").strip() for item in hashes if str(item or "").strip()]
        if not normalized:
            return {}
        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        chunk_size = 200
        for index in range(0, len(normalized), chunk_size):
            chunk = normalized[index : index + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cursor.execute(
                f"""
                UPDATE relations
                SET confidence = MAX(0.0, COALESCE(confidence, 0.0) + ?)
                WHERE hash IN ({placeholders})
                """,
                tuple([float(delta)] + chunk),
            )
        conn.commit()
        statuses = self.metadata_store.get_relation_status_batch(normalized)
        return {
            hash_value: float((statuses.get(hash_value) or {}).get("weight", 0.0) or 0.0) for hash_value in normalized
        }

    def _apply_v5_relation_action(self, *, action: str, hashes: List[str], strength: float = 1.0) -> Dict[str, Any]:
        assert self.metadata_store
        act = str(action or "").strip().lower()
        normalized = [str(item or "").strip() for item in hashes if str(item or "").strip()]
        if not normalized:
            return {"success": False, "error": "未命中可维护关系"}

        now = time.time()
        strength_value = max(0.1, float(strength or 1.0))
        prune_threshold = max(0.0, float(self._cfg("memory.prune_threshold", 0.1) or 0.1))
        detail = ""

        if act == "reinforce":
            weights = self._adjust_relation_confidence(normalized, delta=0.5 * strength_value)
            protect_hours = max(1.0, 24.0 * strength_value)
            self.metadata_store.reinforce_relations(normalized)
            self.metadata_store.mark_relations_active(normalized, boost_weight=max(prune_threshold, 0.1))
            self.metadata_store.update_relations_protection(
                normalized,
                protected_until=now + protect_hours * 3600.0,
                last_reinforced=now,
            )
            detail = f"reinforce {len(normalized)} 条关系"
        elif act == "weaken":
            weights = self._adjust_relation_confidence(normalized, delta=-0.5 * strength_value)
            to_freeze = [hash_value for hash_value, weight in weights.items() if weight <= prune_threshold]
            if to_freeze:
                self.metadata_store.mark_relations_inactive(to_freeze, inactive_since=now)
            detail = f"weaken {len(normalized)} 条关系"
        elif act == "remember_forever":
            self.metadata_store.mark_relations_active(normalized, boost_weight=max(prune_threshold, 0.1))
            self.metadata_store.update_relations_protection(normalized, protected_until=0.0, is_pinned=True)
            weights = {
                hash_value: float(
                    (self.metadata_store.get_relation_status_batch([hash_value]).get(hash_value) or {}).get(
                        "weight",
                        0.0,
                    )
                    or 0.0
                )
                for hash_value in normalized
            }
            detail = f"remember_forever {len(normalized)} 条关系"
        elif act == "forget":
            weights = self._adjust_relation_confidence(normalized, delta=-2.0 * strength_value)
            self.metadata_store.update_relations_protection(normalized, protected_until=0.0, is_pinned=False)
            self.metadata_store.mark_relations_inactive(normalized, inactive_since=now)
            detail = f"forget {len(normalized)} 条关系"
        else:
            return {"success": False, "error": f"不支持的 V5 动作: {act}"}

        self._rebuild_graph_from_metadata()
        self._last_maintenance_at = now
        self._persist()
        statuses = self.metadata_store.get_relation_status_batch(normalized)
        return {
            "success": True,
            "detail": detail,
            "hashes": normalized,
            "count": len(normalized),
            "weights": weights,
            "statuses": statuses,
        }
