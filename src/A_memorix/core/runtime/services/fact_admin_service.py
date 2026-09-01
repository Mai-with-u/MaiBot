from __future__ import annotations

from typing import Any, Dict, Optional

import unicodedata

from .base import KernelServiceBase


class MemoryFactAdminService(KernelServiceBase):
    """提供基于 metadata 事实账本的结构化管理接口。"""

    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        return float(value)

    def _enqueue_profile_refresh(self, claim: Dict[str, Any], *, reason: str, conn: Any) -> bool:
        if str(claim.get("scope_type", "") or "") != "person":
            return False
        if not bool(self._cfg("person_profile.enabled", True)):
            return False
        request = self.metadata_store.enqueue_person_profile_refresh(
            person_id=str(claim.get("scope_id", "") or ""),
            reason=reason,
            conn=conn,
        )
        return request is not None

    @staticmethod
    def _claim_value(
        kwargs: Dict[str, Any],
        existing: Optional[Dict[str, Any]],
        key: str,
        default: Any,
        *,
        allow_none: bool = False,
    ) -> Any:
        if key in kwargs and (allow_none or kwargs[key] is not None):
            return kwargs[key]
        if existing is not None and key in existing:
            return existing[key]
        return default

    def _upsert_claim(
        self,
        *,
        kwargs: Dict[str, Any],
        existing: Optional[Dict[str, Any]] = None,
        supersedes_claim_ids: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        return self.metadata_store.upsert_fact_claim(
            scope_type=str(self._claim_value(kwargs, existing, "scope_type", "person")),
            scope_id=str(self._claim_value(kwargs, existing, "scope_id", "")),
            fact_key=str(self._claim_value(kwargs, existing, "fact_key", "")),
            value_text=str(self._claim_value(kwargs, existing, "value_text", "")),
            polarity=str(self._claim_value(kwargs, existing, "polarity", "positive")),
            cardinality=str(self._claim_value(kwargs, existing, "cardinality", "set")),
            stability=str(self._claim_value(kwargs, existing, "stability", "stable")),
            profile_section=str(self._claim_value(kwargs, existing, "profile_section", "stable_facts")),
            authority=str(self._claim_value(kwargs, existing, "authority", "manual")),
            confidence=float(self._claim_value(kwargs, existing, "confidence", 1.0)),
            valid_from=self._optional_float(self._claim_value(kwargs, existing, "valid_from", None, allow_none=True)),
            valid_to=self._optional_float(self._claim_value(kwargs, existing, "valid_to", None, allow_none=True)),
            supersedes_claim_ids=supersedes_claim_ids,
            reason=str(kwargs.get("reason", "") or "webui_fact_write"),
        )

    @staticmethod
    def _normalized_identity_value(value: Any) -> str:
        return " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().split()).casefold()

    @classmethod
    def _claim_identity_changed(cls, existing: Dict[str, Any], kwargs: Dict[str, Any]) -> bool:
        comparisons = {
            "fact_key": cls._normalized_identity_value(existing.get("fact_key", "")),
            "value_text": cls._normalized_identity_value(existing.get("value_normalized", "")),
            "polarity": cls._normalized_identity_value(existing.get("polarity", "")),
            "cardinality": cls._normalized_identity_value(existing.get("cardinality", "")),
        }
        for key, current_value in comparisons.items():
            if key not in kwargs:
                continue
            requested_value = cls._normalized_identity_value(kwargs[key])
            if requested_value != current_value:
                return True
        return False

    def _create_fact(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        with self.metadata_store.transaction(immediate=True) as conn:
            claim = self._upsert_claim(kwargs=kwargs)
            refresh_queued = self._enqueue_profile_refresh(
                claim,
                reason="fact_claim_created",
                conn=conn,
            )
        return {"success": True, "claim": claim, "refresh_queued": refresh_queued}

    def _update_fact(self, claim_id: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        existing = self.metadata_store.get_fact_claim(claim_id)
        if existing is None:
            raise ValueError(f"事实 claim 不存在: {claim_id}")
        requested_scope_type = str(self._claim_value(kwargs, existing, "scope_type", ""))
        requested_scope_id = str(self._claim_value(kwargs, existing, "scope_id", ""))
        if requested_scope_type != str(existing["scope_type"]) or requested_scope_id != str(existing["scope_id"]):
            raise ValueError("事实更新不能改变 scope_type 或 scope_id")

        with self.metadata_store.transaction(immediate=True) as conn:
            claim = existing
            if self._claim_identity_changed(existing, kwargs):
                try:
                    claim = self._upsert_claim(
                        kwargs=kwargs,
                        existing=existing,
                        supersedes_claim_ids=[claim_id],
                    )
                except ValueError as error:
                    if "不属于同一冲突组" not in str(error):
                        raise
                    claim = self._upsert_claim(kwargs=kwargs, existing=existing)
                    if str(claim["claim_id"]) != claim_id:
                        self.metadata_store.retract_fact_claim(
                            claim_id,
                            reason=str(kwargs.get("reason", "") or "webui_fact_revised"),
                        )
                        claim = self.metadata_store.restore_fact_claim(
                            str(claim["claim_id"]),
                            reason=str(kwargs.get("reason", "") or "webui_fact_revised"),
                        )

            claim = self.metadata_store.update_fact_claim_classification(
                str(claim["claim_id"]),
                stability=str(self._claim_value(kwargs, claim, "stability", "stable")),
                profile_section=str(self._claim_value(kwargs, claim, "profile_section", "stable_facts")),
                authority=str(self._claim_value(kwargs, claim, "authority", "manual")),
                confidence=float(self._claim_value(kwargs, claim, "confidence", 1.0)),
                valid_from=self._optional_float(self._claim_value(kwargs, claim, "valid_from", None, allow_none=True)),
                valid_to=self._optional_float(self._claim_value(kwargs, claim, "valid_to", None, allow_none=True)),
                reason=str(kwargs.get("reason", "") or "webui_fact_classification_updated"),
            )
            refresh_queued = self._enqueue_profile_refresh(
                claim,
                reason="fact_claim_updated",
                conn=conn,
            )
        return {
            "success": True,
            "claim": claim,
            "previous_claim_id": claim_id,
            "replaced": str(claim["claim_id"]) != claim_id,
            "refresh_queued": refresh_queued,
        }

    def _change_fact_status(self, action: str, claim_id: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        reason = str(kwargs.get("reason", "") or f"webui_fact_{action}")
        with self.metadata_store.transaction(immediate=True) as conn:
            if action == "retract":
                claim = self.metadata_store.retract_fact_claim(claim_id, reason=reason)
            else:
                claim = self.metadata_store.restore_fact_claim(claim_id, reason=reason)
            refresh_queued = self._enqueue_profile_refresh(
                claim,
                reason="fact_claim_retracted" if action == "retract" else "fact_claim_restored",
                conn=conn,
            )
        return {"success": True, "claim": claim, "refresh_queued": refresh_queued}

    async def memory_fact_admin(self, *, action: str, **kwargs: Any) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store is not None

        act = str(action or "").strip().lower()
        try:
            if act == "get":
                claim_id = str(kwargs.get("claim_id", "") or "").strip()
                claim = self.metadata_store.get_fact_claim(claim_id)
                return {"success": claim is not None, "claim": claim, "error": "" if claim else "事实 claim 不存在"}
            if act == "list":
                claims = self.metadata_store.list_fact_claims(
                    scope_type=str(kwargs.get("scope_type", "") or "person"),
                    scope_id=str(kwargs.get("scope_id", "") or ""),
                    statuses=kwargs.get("statuses"),
                    limit=max(1, int(kwargs.get("limit", 200) or 200)),
                )
                return {"success": True, "items": claims, "count": len(claims)}
            if act == "create":
                return self._create_fact(dict(kwargs))
            if act == "update":
                return self._update_fact(str(kwargs.get("claim_id", "") or "").strip(), dict(kwargs))
            if act in {"retract", "restore"}:
                return self._change_fact_status(
                    act,
                    str(kwargs.get("claim_id", "") or "").strip(),
                    dict(kwargs),
                )
        except ValueError as error:
            return {"success": False, "error": str(error)}
        return {"success": False, "error": f"不支持的 fact action: {act}"}
