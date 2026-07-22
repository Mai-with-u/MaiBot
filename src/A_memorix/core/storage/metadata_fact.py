"""结构化事实账本存储与确定性状态机。"""

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

import hashlib
import json
import unicodedata


_SCOPE_TYPES = {"person", "chat"}
_POLARITIES = {"positive", "negative"}
_CARDINALITIES = {"single", "set"}
_STABILITIES = {"stable", "temporal", "uncertain"}
_AUTHORITIES = {"manual", "direct_user", "imported", "summary_derived"}
_STATUSES = {"active", "conflicted", "superseded", "retracted"}
_EVIDENCE_STANCES = {"support", "refute"}
_TRUSTED_FACT_ORIGINS = {"manual_confirmed", "server_verified", "trusted_import"}
_PROFILE_SECTIONS = {
    "identity_settings",
    "relationship_settings",
    "stable_facts",
    "interaction_preferences",
    "recent_interactions",
    "uncertain_notes",
}

FACT_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS fact_claims (
        claim_id TEXT PRIMARY KEY,
        scope_type TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        fact_key TEXT NOT NULL,
        value_text TEXT NOT NULL,
        value_normalized TEXT NOT NULL,
        polarity TEXT NOT NULL,
        cardinality TEXT NOT NULL,
        conflict_group TEXT NOT NULL,
        stability TEXT NOT NULL,
        profile_section TEXT NOT NULL,
        authority TEXT NOT NULL,
        status TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 1.0,
        valid_from REAL,
        valid_to REAL,
        first_observed_at REAL NOT NULL,
        last_confirmed_at REAL NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fact_claims_scope_status
    ON fact_claims(scope_type, scope_id, status, profile_section, last_confirmed_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fact_claims_conflict
    ON fact_claims(scope_type, scope_id, conflict_group, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_evidence (
        claim_id TEXT NOT NULL,
        evidence_type TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        stance TEXT NOT NULL,
        weight REAL NOT NULL DEFAULT 1.0,
        observed_at REAL NOT NULL,
        metadata_json TEXT,
        PRIMARY KEY(claim_id, evidence_type, evidence_id, stance),
        FOREIGN KEY(claim_id) REFERENCES fact_claims(claim_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fact_evidence_claim
    ON fact_evidence(claim_id, observed_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_transitions (
        transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
        old_claim_id TEXT,
        new_claim_id TEXT,
        transition_type TEXT NOT NULL,
        reason TEXT,
        evidence_type TEXT,
        evidence_id TEXT,
        created_at REAL NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fact_transitions_old
    ON fact_transitions(old_claim_id, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fact_transitions_new
    ON fact_transitions(new_claim_id, created_at DESC)
    """,
)


def _normalized_token(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().split()).casefold()


def _required_token(name: str, value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError(f"{name} 不能为空")
    return token


def _enum_token(name: str, value: Any, allowed: set[str]) -> str:
    token = _normalized_token(value)
    if token not in allowed:
        raise ValueError(f"无效 {name}: {value}")
    return token


def _stable_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evidence_identity(evidence_type: Any, evidence_id: Any) -> tuple[str, str]:
    """校验证据标识；类型与 ID 必须同时存在或同时为空。"""

    normalized_type = str(evidence_type or "").strip()
    normalized_id = str(evidence_id or "").strip()
    if bool(normalized_type) != bool(normalized_id):
        raise ValueError("evidence_type 与 evidence_id 必须同时提供")
    return normalized_type, normalized_id


class MetadataFactMixin:
    """维护事实 claim、证据和状态转换。

    状态机的安全边界是：精确重复只增强原 claim；显式指定 supersedes
    才能使旧 claim 失效；单值槽位出现未声明的异值时，新 claim
    进入 conflicted，不会按时间静默覆盖已确认事实。
    """

    @staticmethod
    def _fact_claim_row(row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        payload = dict(row)
        for key in (
            "confidence",
            "valid_from",
            "valid_to",
            "first_observed_at",
            "last_confirmed_at",
            "created_at",
            "updated_at",
        ):
            value = payload.get(key)
            payload[key] = float(value) if value is not None else None
        return payload

    @staticmethod
    def _fact_evidence_row(row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        payload = dict(row)
        raw_metadata = payload.pop("metadata_json", None)
        try:
            metadata = json.loads(raw_metadata) if raw_metadata else {}
        except (TypeError, ValueError):
            metadata = {}
        payload["metadata"] = metadata if isinstance(metadata, dict) else {}
        payload["weight"] = float(payload.get("weight", 1.0) or 1.0)
        payload["observed_at"] = float(payload.get("observed_at", 0.0) or 0.0)
        return payload

    @staticmethod
    def _claim_identity(
        *,
        scope_type: str,
        scope_id: str,
        fact_key: str,
        value_normalized: str,
        polarity: str,
    ) -> str:
        return _stable_hash(
            {
                "scope_type": scope_type,
                "scope_id": scope_id,
                "fact_key": fact_key,
                "value": value_normalized,
                "polarity": polarity,
            }
        )

    @staticmethod
    def _conflict_group(
        *,
        scope_type: str,
        scope_id: str,
        fact_key: str,
        value_normalized: str,
        cardinality: str,
    ) -> str:
        payload = {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "fact_key": fact_key,
        }
        if cardinality == "set":
            payload["value"] = value_normalized
        return _stable_hash(payload)

    @staticmethod
    def _append_fact_transition(
        cursor: Any,
        *,
        old_claim_id: Optional[str],
        new_claim_id: Optional[str],
        transition_type: str,
        reason: str,
        evidence_type: str,
        evidence_id: str,
        created_at: float,
    ) -> int:
        cursor.execute(
            """
            INSERT INTO fact_transitions (
                old_claim_id, new_claim_id, transition_type, reason,
                evidence_type, evidence_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(old_claim_id or "") or None,
                str(new_claim_id or "") or None,
                str(transition_type or "").strip(),
                str(reason or "").strip(),
                str(evidence_type or "").strip(),
                str(evidence_id or "").strip(),
                float(created_at),
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _upsert_fact_evidence_in_transaction(
        cursor: Any,
        *,
        claim_id: str,
        evidence_type: str,
        evidence_id: str,
        stance: str,
        weight: float,
        observed_at: float,
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        if not evidence_type or not evidence_id:
            return
        cursor.execute(
            """
            INSERT INTO fact_evidence (
                claim_id, evidence_type, evidence_id, stance,
                weight, observed_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(claim_id, evidence_type, evidence_id, stance) DO UPDATE SET
                weight = MAX(fact_evidence.weight, excluded.weight),
                observed_at = MAX(fact_evidence.observed_at, excluded.observed_at),
                metadata_json = excluded.metadata_json
            """,
            (
                claim_id,
                evidence_type,
                evidence_id,
                stance,
                min(1.0, max(0.0, float(weight))),
                float(observed_at),
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            ),
        )

    def upsert_fact_claim(
        self,
        *,
        scope_type: str,
        scope_id: str,
        fact_key: str,
        value_text: str,
        polarity: str = "positive",
        cardinality: str = "set",
        stability: str = "stable",
        profile_section: str = "stable_facts",
        authority: str = "direct_user",
        confidence: float = 1.0,
        valid_from: Optional[float] = None,
        valid_to: Optional[float] = None,
        evidence_type: str = "",
        evidence_id: str = "",
        evidence_stance: str = "support",
        evidence_weight: float = 1.0,
        evidence_metadata: Optional[Dict[str, Any]] = None,
        supersedes_claim_ids: Optional[Sequence[str]] = None,
        reason: str = "",
        observed_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """写入或增强一条事实，并执行显式 supersession。"""

        normalized_scope_type = _enum_token("scope_type", scope_type, _SCOPE_TYPES)
        normalized_scope_id = _required_token("scope_id", scope_id)
        normalized_fact_key = _normalized_token(_required_token("fact_key", fact_key))
        normalized_value_text = _required_token("value_text", value_text)
        value_normalized = _normalized_token(normalized_value_text)
        normalized_polarity = _enum_token("polarity", polarity, _POLARITIES)
        normalized_cardinality = _enum_token("cardinality", cardinality, _CARDINALITIES)
        normalized_stability = _enum_token("stability", stability, _STABILITIES)
        normalized_profile_section = _enum_token("profile_section", profile_section, _PROFILE_SECTIONS)
        normalized_authority = _enum_token("authority", authority, _AUTHORITIES)
        normalized_evidence_stance = _enum_token("evidence_stance", evidence_stance, _EVIDENCE_STANCES)
        normalized_evidence_type, normalized_evidence_id = _evidence_identity(evidence_type, evidence_id)
        if normalized_evidence_type and normalized_evidence_stance != "support":
            raise ValueError("upsert_fact_claim 只能用 support 证据断言事实；反向证据请使用 add_fact_evidence")
        normalized_confidence = min(1.0, max(0.0, float(confidence)))
        now = float(observed_at) if observed_at is not None else datetime.now().timestamp()
        normalized_valid_from = float(valid_from) if valid_from is not None else None
        normalized_valid_to = float(valid_to) if valid_to is not None else None
        if (
            normalized_valid_from is not None
            and normalized_valid_to is not None
            and normalized_valid_to < normalized_valid_from
        ):
            raise ValueError("valid_to 不能早于 valid_from")

        claim_id = self._claim_identity(
            scope_type=normalized_scope_type,
            scope_id=normalized_scope_id,
            fact_key=normalized_fact_key,
            value_normalized=value_normalized,
            polarity=normalized_polarity,
        )
        conflict_group = self._conflict_group(
            scope_type=normalized_scope_type,
            scope_id=normalized_scope_id,
            fact_key=normalized_fact_key,
            value_normalized=value_normalized,
            cardinality=normalized_cardinality,
        )
        requested_supersedes = list(
            dict.fromkeys(
                str(item or "").strip()
                for item in (supersedes_claim_ids or [])
                if str(item or "").strip() and str(item or "").strip() != claim_id
            )
        )

        created = False
        reinforced = False
        restored = False
        superseded: List[str] = []
        with self.transaction(immediate=True) as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM fact_claims WHERE claim_id = ?", (claim_id,))
            existing = self._fact_claim_row(cursor.fetchone())
            if existing is not None and str(existing.get("cardinality", "")) != normalized_cardinality:
                raise ValueError("同一 claim 不能改变 cardinality；请创建新 claim 并显式取代旧 claim")

            existing_evidence = None
            if existing is not None and normalized_evidence_type:
                cursor.execute(
                    """
                    SELECT * FROM fact_evidence
                    WHERE claim_id = ? AND evidence_type = ? AND evidence_id = ? AND stance = ?
                    """,
                    (
                        claim_id,
                        normalized_evidence_type,
                        normalized_evidence_id,
                        normalized_evidence_stance,
                    ),
                )
                existing_evidence = self._fact_evidence_row(cursor.fetchone())

            if requested_supersedes:
                placeholders = ",".join("?" for _ in requested_supersedes)
                cursor.execute(
                    f"""
                    SELECT claim_id, scope_type, scope_id, conflict_group, status
                    FROM fact_claims
                    WHERE claim_id IN ({placeholders})
                    """,
                    tuple(requested_supersedes),
                )
                rows = [dict(row) for row in cursor.fetchall()]
                found = {str(row["claim_id"]): row for row in rows}
                missing = [item for item in requested_supersedes if item not in found]
                if missing:
                    raise ValueError(f"待取代 claim 不存在: {missing}")
                invalid = [
                    item
                    for item, row in found.items()
                    if str(row["scope_type"]) != normalized_scope_type
                    or str(row["scope_id"]) != normalized_scope_id
                    or str(row["conflict_group"]) != conflict_group
                ]
                if invalid:
                    raise ValueError(f"待取代 claim 不属于同一冲突组: {invalid}")
                superseded = [
                    item
                    for item, row in found.items()
                    if str(row.get("status", "")) != "superseded"
                ]

            if existing is not None and existing_evidence is not None and not requested_supersedes:
                claim = existing
                return {
                    **claim,
                    "created": False,
                    "reinforced": False,
                    "restored": False,
                    "idempotent": True,
                    "superseded_claim_ids": [],
                    "conflicting_claim_ids": [],
                }

            cursor.execute(
                """
                SELECT claim_id
                FROM fact_claims
                WHERE scope_type = ? AND scope_id = ? AND conflict_group = ?
                  AND status = 'active' AND claim_id != ?
                ORDER BY claim_id ASC
                """,
                (normalized_scope_type, normalized_scope_id, conflict_group, claim_id),
            )
            active_conflicts = [str(row[0]) for row in cursor.fetchall()]
            unsuperseded_conflicts = [item for item in active_conflicts if item not in superseded]
            target_status = "conflicted" if unsuperseded_conflicts else "active"

            if existing is None:
                cursor.execute(
                    """
                    INSERT INTO fact_claims (
                        claim_id, scope_type, scope_id, fact_key,
                        value_text, value_normalized, polarity, cardinality,
                        conflict_group, stability, profile_section, authority, status,
                        confidence, valid_from, valid_to, first_observed_at,
                        last_confirmed_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        claim_id,
                        normalized_scope_type,
                        normalized_scope_id,
                        normalized_fact_key,
                        normalized_value_text,
                        value_normalized,
                        normalized_polarity,
                        normalized_cardinality,
                        conflict_group,
                        normalized_stability,
                        normalized_profile_section,
                        normalized_authority,
                        target_status,
                        normalized_confidence,
                        normalized_valid_from,
                        normalized_valid_to,
                        now,
                        now,
                        now,
                        now,
                    ),
                )
                created = True
                self._append_fact_transition(
                    cursor,
                    old_claim_id=None,
                    new_claim_id=claim_id,
                    transition_type="assert",
                    reason=reason,
                    evidence_type=normalized_evidence_type,
                    evidence_id=normalized_evidence_id,
                    created_at=now,
                )
            else:
                previous_status = str(existing.get("status", ""))
                restored = previous_status in {"superseded", "retracted"}
                cursor.execute(
                    """
                    UPDATE fact_claims
                    SET value_text = ?, cardinality = ?, conflict_group = ?,
                        stability = ?, profile_section = ?, authority = ?, status = ?,
                        confidence = MAX(confidence, ?),
                        valid_from = COALESCE(?, valid_from),
                        valid_to = ?, last_confirmed_at = ?, updated_at = ?
                    WHERE claim_id = ?
                    """,
                    (
                        normalized_value_text,
                        normalized_cardinality,
                        conflict_group,
                        normalized_stability,
                        normalized_profile_section,
                        normalized_authority,
                        target_status,
                        normalized_confidence,
                        normalized_valid_from,
                        normalized_valid_to,
                        now,
                        now,
                        claim_id,
                    ),
                )
                reinforced = previous_status in {"active", "conflicted"}
                self._append_fact_transition(
                    cursor,
                    old_claim_id=claim_id,
                    new_claim_id=claim_id,
                    transition_type="restore" if restored else "reinforce",
                    reason=reason,
                    evidence_type=normalized_evidence_type,
                    evidence_id=normalized_evidence_id,
                    created_at=now,
                )

            for old_claim_id in superseded:
                cursor.execute(
                    """
                    UPDATE fact_claims
                    SET status = 'superseded', valid_to = COALESCE(valid_to, ?), updated_at = ?
                    WHERE claim_id = ?
                    """,
                    (now, now, old_claim_id),
                )
                self._append_fact_transition(
                    cursor,
                    old_claim_id=old_claim_id,
                    new_claim_id=claim_id,
                    transition_type="supersede",
                    reason=reason,
                    evidence_type=normalized_evidence_type,
                    evidence_id=normalized_evidence_id,
                    created_at=now,
                )

            self._upsert_fact_evidence_in_transaction(
                cursor,
                claim_id=claim_id,
                evidence_type=normalized_evidence_type,
                evidence_id=normalized_evidence_id,
                stance=normalized_evidence_stance,
                weight=evidence_weight,
                observed_at=now,
                metadata=evidence_metadata,
            )

            cursor.execute("SELECT * FROM fact_claims WHERE claim_id = ?", (claim_id,))
            claim = self._fact_claim_row(cursor.fetchone())

        if claim is None:
            raise RuntimeError(f"事实 claim 写入后丢失: {claim_id}")
        return {
            **claim,
            "created": created,
            "reinforced": reinforced,
            "restored": restored,
            "idempotent": False,
            "superseded_claim_ids": superseded,
            "conflicting_claim_ids": unsuperseded_conflicts,
        }

    def add_fact_evidence(
        self,
        claim_id: str,
        *,
        evidence_type: str,
        evidence_id: str,
        stance: str = "support",
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
        reason: str = "",
        observed_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """给既有 claim 增加证据，不隐式改变 claim 状态。"""

        claim_token = _required_token("claim_id", claim_id)
        normalized_type, normalized_id = _evidence_identity(evidence_type, evidence_id)
        if not normalized_type:
            raise ValueError("事实证据不能为空")
        normalized_stance = _enum_token("stance", stance, _EVIDENCE_STANCES)
        now = float(observed_at) if observed_at is not None else datetime.now().timestamp()
        with self.transaction(immediate=True) as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT 1 FROM fact_claims WHERE claim_id = ?", (claim_token,))
            if cursor.fetchone() is None:
                raise ValueError(f"事实 claim 不存在: {claim_token}")
            cursor.execute(
                """
                SELECT * FROM fact_evidence
                WHERE claim_id = ? AND evidence_type = ? AND evidence_id = ? AND stance = ?
                """,
                (claim_token, normalized_type, normalized_id, normalized_stance),
            )
            existing = self._fact_evidence_row(cursor.fetchone())
            if existing is not None:
                return {**existing, "created": False, "idempotent": True}

            self._upsert_fact_evidence_in_transaction(
                cursor,
                claim_id=claim_token,
                evidence_type=normalized_type,
                evidence_id=normalized_id,
                stance=normalized_stance,
                weight=weight,
                observed_at=now,
                metadata=metadata,
            )
            self._append_fact_transition(
                cursor,
                old_claim_id=claim_token,
                new_claim_id=claim_token,
                transition_type=f"{normalized_stance}_evidence",
                reason=reason,
                evidence_type=normalized_type,
                evidence_id=normalized_id,
                created_at=now,
            )
            cursor.execute(
                """
                SELECT * FROM fact_evidence
                WHERE claim_id = ? AND evidence_type = ? AND evidence_id = ? AND stance = ?
                """,
                (claim_token, normalized_type, normalized_id, normalized_stance),
            )
            created = self._fact_evidence_row(cursor.fetchone())
        if created is None:
            raise RuntimeError(f"事实证据写入后丢失: {claim_token}/{normalized_type}/{normalized_id}")
        return {**created, "created": True, "idempotent": False}

    def retract_fact_claim(
        self,
        claim_id: str,
        *,
        reason: str = "",
        evidence_type: str = "",
        evidence_id: str = "",
        retracted_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """显式撤回 claim，不根据文本关键词推断。"""

        token = _required_token("claim_id", claim_id)
        now = float(retracted_at) if retracted_at is not None else datetime.now().timestamp()
        with self.transaction(immediate=True) as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM fact_claims WHERE claim_id = ?", (token,))
            previous = self._fact_claim_row(cursor.fetchone())
            if previous is None:
                raise ValueError(f"事实 claim 不存在: {token}")
            cursor.execute(
                """
                UPDATE fact_claims
                SET status = 'retracted', valid_to = COALESCE(valid_to, ?), updated_at = ?
                WHERE claim_id = ?
                """,
                (now, now, token),
            )
            if str(previous.get("status", "")) != "retracted":
                self._append_fact_transition(
                    cursor,
                    old_claim_id=token,
                    new_claim_id=None,
                    transition_type="retract",
                    reason=reason,
                    evidence_type=evidence_type,
                    evidence_id=evidence_id,
                    created_at=now,
                )
            cursor.execute("SELECT * FROM fact_claims WHERE claim_id = ?", (token,))
            updated = self._fact_claim_row(cursor.fetchone())
        if updated is None:
            raise RuntimeError(f"事实 claim 撤回后丢失: {token}")
        return updated

    def restore_fact_claim(
        self,
        claim_id: str,
        *,
        reason: str = "",
        evidence_type: str = "",
        evidence_id: str = "",
        restored_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """恢复一条被撤回或取代的 claim；存在异值 active 时仅恢复为 conflicted。"""

        token = _required_token("claim_id", claim_id)
        now = float(restored_at) if restored_at is not None else datetime.now().timestamp()
        with self.transaction(immediate=True) as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM fact_claims WHERE claim_id = ?", (token,))
            previous = self._fact_claim_row(cursor.fetchone())
            if previous is None:
                raise ValueError(f"事实 claim 不存在: {token}")
            cursor.execute(
                """
                SELECT claim_id
                FROM fact_claims
                WHERE scope_type = ? AND scope_id = ? AND conflict_group = ?
                  AND status = 'active' AND claim_id != ?
                ORDER BY claim_id ASC
                """,
                (
                    str(previous["scope_type"]),
                    str(previous["scope_id"]),
                    str(previous["conflict_group"]),
                    token,
                ),
            )
            conflicts = [str(row[0]) for row in cursor.fetchall()]
            next_status = "conflicted" if conflicts else "active"
            if str(previous.get("status", "")) == next_status:
                return {
                    **previous,
                    "restored": False,
                    "idempotent": True,
                    "conflicting_claim_ids": conflicts,
                }
            cursor.execute(
                """
                UPDATE fact_claims
                SET status = ?, valid_to = NULL, last_confirmed_at = ?, updated_at = ?
                WHERE claim_id = ?
                """,
                (next_status, now, now, token),
            )
            self._append_fact_transition(
                cursor,
                old_claim_id=token,
                new_claim_id=token,
                transition_type="restore",
                reason=reason,
                evidence_type=evidence_type,
                evidence_id=evidence_id,
                created_at=now,
            )
            cursor.execute("SELECT * FROM fact_claims WHERE claim_id = ?", (token,))
            updated = self._fact_claim_row(cursor.fetchone())
        if updated is None:
            raise RuntimeError(f"事实 claim 恢复后丢失: {token}")
        return {
            **updated,
            "restored": True,
            "idempotent": False,
            "conflicting_claim_ids": conflicts,
        }

    def get_fact_claim(self, claim_id: str) -> Optional[Dict[str, Any]]:
        token = str(claim_id or "").strip()
        if not token:
            return None
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM fact_claims WHERE claim_id = ?", (token,))
        return self._fact_claim_row(cursor.fetchone())

    def list_fact_claims(
        self,
        *,
        scope_type: str,
        scope_id: str,
        statuses: Optional[Iterable[str]] = None,
        effective_at: Optional[float] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        normalized_scope_type = _enum_token("scope_type", scope_type, _SCOPE_TYPES)
        normalized_scope_id = _required_token("scope_id", scope_id)
        normalized_statuses = [
            _enum_token("status", item, _STATUSES)
            for item in dict.fromkeys(statuses or ["active"])
        ]
        placeholders = ",".join("?" for _ in normalized_statuses)
        sql = f"""
            SELECT *
            FROM fact_claims
            WHERE scope_type = ? AND scope_id = ?
              AND status IN ({placeholders})
        """
        params: List[Any] = [normalized_scope_type, normalized_scope_id, *normalized_statuses]
        if effective_at is not None:
            point = float(effective_at)
            sql += " AND (valid_from IS NULL OR valid_from <= ?) AND (valid_to IS NULL OR valid_to > ?)"
            params.extend([point, point])
        sql += " ORDER BY updated_at DESC, claim_id ASC LIMIT ?"
        params.append(max(1, int(limit)))
        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params))
        return [self._fact_claim_row(row) or {} for row in cursor.fetchall()]

    def list_current_person_fact_claims(
        self,
        person_id: str,
        *,
        effective_at: Optional[float] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """返回可进入人物画像的当前事实，排序不依赖向量召回分数。"""

        token = _required_token("person_id", person_id)
        point = float(effective_at) if effective_at is not None else datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM fact_claims
            WHERE scope_type = 'person' AND scope_id = ? AND status = 'active'
              AND stability = 'stable'
              AND authority IN ('manual', 'direct_user', 'imported')
              AND (valid_from IS NULL OR valid_from <= ?)
              AND (valid_to IS NULL OR valid_to > ?)
            ORDER BY
                CASE profile_section
                    WHEN 'identity_settings' THEN 0
                    WHEN 'relationship_settings' THEN 1
                    WHEN 'stable_facts' THEN 2
                    WHEN 'interaction_preferences' THEN 3
                    WHEN 'recent_interactions' THEN 4
                    ELSE 5
                END ASC,
                CASE authority
                    WHEN 'manual' THEN 0
                    WHEN 'direct_user' THEN 1
                    ELSE 2
                END ASC,
                fact_key ASC,
                value_normalized ASC,
                claim_id ASC
            LIMIT ?
            """,
            (token, point, point, max(1, int(limit))),
        )
        return [self._fact_claim_row(row) or {} for row in cursor.fetchall()]

    def list_person_profile_fact_claims(
        self,
        person_id: str,
        *,
        effective_at: Optional[float] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """返回画像可投影 claim，包括可信稳定事实和明确标记的不确定事实。"""

        token = _required_token("person_id", person_id)
        point = float(effective_at) if effective_at is not None else datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM fact_claims
            WHERE scope_type = 'person' AND scope_id = ? AND status = 'active'
              AND (valid_from IS NULL OR valid_from <= ?)
              AND (valid_to IS NULL OR valid_to > ?)
              AND (
                    (
                        stability = 'stable'
                        AND authority IN ('manual', 'direct_user', 'imported')
                    )
                    OR (
                        stability = 'uncertain'
                        AND authority = 'summary_derived'
                        AND profile_section = 'uncertain_notes'
                    )
              )
            ORDER BY
                CASE profile_section
                    WHEN 'identity_settings' THEN 0
                    WHEN 'relationship_settings' THEN 1
                    WHEN 'stable_facts' THEN 2
                    WHEN 'interaction_preferences' THEN 3
                    WHEN 'recent_interactions' THEN 4
                    ELSE 5
                END ASC,
                CASE authority
                    WHEN 'manual' THEN 0
                    WHEN 'direct_user' THEN 1
                    WHEN 'imported' THEN 2
                    ELSE 3
                END ASC,
                fact_key ASC,
                value_normalized ASC,
                claim_id ASC
            LIMIT ?
            """,
            (token, point, point, max(1, int(limit))),
        )
        return [self._fact_claim_row(row) or {} for row in cursor.fetchall()]

    def backfill_person_fact_claims(self, *, limit: Optional[int] = None) -> Dict[str, int]:
        """把现有原子人物事实段落确定性迁移到事实账本。

        迁移不调用模型，也不猜测语义槽位。每条旧段落获得独立 statement 槽位，
        后续显式修正可以再将它归并或撤回。
        """

        sql = """
            SELECT hash, content, source, metadata, event_time, created_at, is_deleted
            FROM paragraphs
            WHERE source LIKE 'person_fact:%'
              AND (is_deleted IS NULL OR is_deleted = 0)
            ORDER BY created_at ASC, hash ASC
        """
        params: List[Any] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params))
        rows = [dict(row) for row in cursor.fetchall()]
        migrated = 0
        retracted = 0
        for row in rows:
            paragraph_hash = str(row.get("hash", "") or "").strip()
            content = str(row.get("content", "") or "").strip()
            source = str(row.get("source", "") or "").strip()
            if not paragraph_hash or not content or not source.startswith("person_fact:"):
                continue
            raw_metadata = row.get("metadata")
            try:
                metadata = json.loads(raw_metadata) if raw_metadata else {}
            except (TypeError, ValueError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            person_id = str(metadata.get("person_id", "") or source[len("person_fact:") :]).strip()
            if not person_id:
                continue
            evidence_source = str(metadata.get("evidence_source", "") or "").strip()
            raw_claim = metadata.get("fact_claim")
            claim_spec = dict(raw_claim) if isinstance(raw_claim, dict) else {}
            trust = _normalized_token(claim_spec.get("trust"))
            trusted = trust in _TRUSTED_FACT_ORIGINS
            observed_at = row.get("event_time") or row.get("created_at")
            result = self.upsert_fact_claim(
                scope_type="person",
                scope_id=person_id,
                fact_key=(
                    str(claim_spec.get("fact_key", "") or f"statement:{paragraph_hash}")
                    if trusted
                    else f"statement:{paragraph_hash}"
                ),
                value_text=content,
                polarity=str(claim_spec.get("polarity", "positive") or "positive") if trusted else "positive",
                cardinality=str(claim_spec.get("cardinality", "set") or "set") if trusted else "set",
                stability=str(claim_spec.get("stability", "stable") or "stable") if trusted else "uncertain",
                profile_section=(
                    str(claim_spec.get("profile_section", "stable_facts") or "stable_facts")
                    if trusted
                    else "uncertain_notes"
                ),
                authority=(
                    str(claim_spec.get("authority", "") or "direct_user")
                    if trusted
                    else "summary_derived"
                ),
                confidence=(
                    float(claim_spec.get("confidence", 1.0) or 1.0)
                    if trusted
                    else min(0.5, float(claim_spec.get("confidence", 0.5) or 0.5))
                ),
                evidence_type="paragraph",
                evidence_id=paragraph_hash,
                evidence_metadata={
                    "source_type": "person_fact",
                    "migration": True,
                    "evidence_source": evidence_source,
                    "trust": trust,
                },
                reason="person_fact_schema_backfill",
                observed_at=float(observed_at) if observed_at is not None else None,
            )
            claim_id = str(result["claim_id"])
            self.update_paragraph_metadata(
                paragraph_hash,
                {"fact_claim_ids": [claim_id]},
                merge=True,
            )
            migrated += 1
            memory_change = metadata.get("memory_change") if isinstance(metadata.get("memory_change"), dict) else {}
            valid_to = memory_change.get("valid_to")
            if valid_to is not None:
                self.retract_fact_claim(
                    claim_id,
                    reason="person_fact_schema_backfill_superseded",
                    evidence_type="paragraph",
                    evidence_id=paragraph_hash,
                    retracted_at=float(valid_to),
                )
                retracted += 1
        return {"scanned": len(rows), "migrated": migrated, "retracted": retracted}

    def get_fact_evidence(self, claim_id: str) -> List[Dict[str, Any]]:
        token = _required_token("claim_id", claim_id)
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT * FROM fact_evidence
            WHERE claim_id = ?
            ORDER BY observed_at DESC, evidence_type ASC, evidence_id ASC
            """,
            (token,),
        )
        return [self._fact_evidence_row(row) or {} for row in cursor.fetchall()]

    def snapshot_fact_evidence_for_paragraphs(
        self,
        paragraph_hashes: Sequence[str],
        *,
        conn: Any = None,
    ) -> Dict[str, Any]:
        """读取段落证据及其 claim 状态，供删除操作持久化回滚快照。"""

        hashes = list(
            dict.fromkeys(
                str(item or "").strip()
                for item in paragraph_hashes
                if str(item or "").strip()
            )
        )
        if not hashes:
            return {"paragraph_hashes": [], "claims": [], "evidence": []}
        connection = self._resolve_conn(conn)
        cursor = connection.cursor()
        placeholders = ",".join("?" for _ in hashes)
        cursor.execute(
            f"""
            SELECT *
            FROM fact_evidence
            WHERE evidence_type = 'paragraph' AND evidence_id IN ({placeholders})
            ORDER BY claim_id ASC, evidence_id ASC, stance ASC
            """,
            tuple(hashes),
        )
        evidence = [self._fact_evidence_row(row) or {} for row in cursor.fetchall()]
        claim_ids = list(
            dict.fromkeys(
                str(item.get("claim_id", "") or "").strip()
                for item in evidence
                if str(item.get("claim_id", "") or "").strip()
            )
        )
        claims: List[Dict[str, Any]] = []
        if claim_ids:
            claim_placeholders = ",".join("?" for _ in claim_ids)
            cursor.execute(
                f"""
                SELECT * FROM fact_claims
                WHERE claim_id IN ({claim_placeholders})
                ORDER BY claim_id ASC
                """,
                tuple(claim_ids),
            )
            claims = [self._fact_claim_row(row) or {} for row in cursor.fetchall()]
        return {"paragraph_hashes": hashes, "claims": claims, "evidence": evidence}

    def detach_fact_evidence_for_paragraphs(
        self,
        paragraph_hashes: Sequence[str],
        *,
        reason: str,
        conn: Any = None,
        detached_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """删除段落证据，并撤回已经没有任何 support 证据的当前 claim。"""

        if conn is None:
            with self.transaction(immediate=True) as connection:
                return self.detach_fact_evidence_for_paragraphs(
                    paragraph_hashes,
                    reason=reason,
                    conn=connection,
                    detached_at=detached_at,
                )

        snapshot = self.snapshot_fact_evidence_for_paragraphs(paragraph_hashes, conn=conn)
        evidence = [dict(item) for item in snapshot.get("evidence", []) if isinstance(item, dict)]
        if not evidence:
            return {**snapshot, "detached_evidence_count": 0, "retracted_claim_ids": []}

        now = float(detached_at) if detached_at is not None else datetime.now().timestamp()
        cursor = self._resolve_conn(conn).cursor()
        removed_support_by_claim: Dict[str, List[str]] = {}
        for item in evidence:
            claim_id = str(item.get("claim_id", "") or "").strip()
            evidence_type = str(item.get("evidence_type", "") or "").strip()
            evidence_id = str(item.get("evidence_id", "") or "").strip()
            stance = str(item.get("stance", "") or "").strip()
            cursor.execute(
                """
                DELETE FROM fact_evidence
                WHERE claim_id = ? AND evidence_type = ? AND evidence_id = ? AND stance = ?
                """,
                (claim_id, evidence_type, evidence_id, stance),
            )
            if stance == "support":
                removed_support_by_claim.setdefault(claim_id, []).append(evidence_id)

        retracted_claim_ids: List[str] = []
        for claim_id, removed_evidence_ids in removed_support_by_claim.items():
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM fact_evidence
                WHERE claim_id = ? AND stance = 'support'
                """,
                (claim_id,),
            )
            remaining_support_count = int(cursor.fetchone()[0])
            if remaining_support_count > 0:
                continue
            cursor.execute("SELECT status FROM fact_claims WHERE claim_id = ?", (claim_id,))
            row = cursor.fetchone()
            if row is None or str(row[0]) not in {"active", "conflicted"}:
                continue
            cursor.execute(
                """
                UPDATE fact_claims
                SET status = 'retracted', valid_to = COALESCE(valid_to, ?), updated_at = ?
                WHERE claim_id = ?
                """,
                (now, now, claim_id),
            )
            self._append_fact_transition(
                cursor,
                old_claim_id=claim_id,
                new_claim_id=None,
                transition_type="detach_evidence",
                reason=reason,
                evidence_type="paragraph",
                evidence_id=sorted(removed_evidence_ids)[0],
                created_at=now,
            )
            retracted_claim_ids.append(claim_id)

        return {
            **snapshot,
            "detached_evidence_count": len(evidence),
            "retracted_claim_ids": sorted(retracted_claim_ids),
        }

    def restore_fact_evidence_snapshot(
        self,
        snapshot: Dict[str, Any],
        *,
        conn: Any = None,
        reason: str = "restore_fact_evidence_snapshot",
        restored_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """恢复 detach 快照中的 claim 状态与证据，并保留一条审计转换记录。"""

        if conn is None:
            with self.transaction(immediate=True) as connection:
                return self.restore_fact_evidence_snapshot(
                    snapshot,
                    conn=connection,
                    reason=reason,
                    restored_at=restored_at,
                )
        if not isinstance(snapshot, dict):
            raise ValueError("fact evidence snapshot 必须是字典")

        claims = [dict(item) for item in snapshot.get("claims", []) if isinstance(item, dict)]
        evidence = [dict(item) for item in snapshot.get("evidence", []) if isinstance(item, dict)]
        now = float(restored_at) if restored_at is not None else datetime.now().timestamp()
        cursor = self._resolve_conn(conn).cursor()
        evidence_claim_ids = {
            str(item.get("claim_id", "") or "").strip()
            for item in evidence
            if str(item.get("claim_id", "") or "").strip()
        }
        claims_by_id = {
            str(item.get("claim_id", "") or "").strip(): item
            for item in claims
            if str(item.get("claim_id", "") or "").strip() in evidence_claim_ids
        }
        if evidence_claim_ids - set(claims_by_id):
            raise ValueError("fact evidence snapshot 缺少证据对应的 claim 快照")

        restored_claim_ids: List[str] = []
        touched_claim_ids: set[str] = set()
        for claim_id, claim in claims_by_id.items():
            previous_status = str(claim.get("status", "") or "").strip()
            if previous_status not in _STATUSES:
                raise ValueError("fact evidence snapshot 包含无效 claim 状态")
            cursor.execute("SELECT status FROM fact_claims WHERE claim_id = ?", (claim_id,))
            current_row = cursor.fetchone()
            if current_row is None:
                raise ValueError(f"fact evidence snapshot 对应 claim 不存在: {claim_id}")
            current_status = str(current_row[0] or "")
            if current_status == previous_status:
                continue
            cursor.execute(
                """
                SELECT transition_type
                FROM fact_transitions
                WHERE old_claim_id = ? OR new_claim_id = ?
                ORDER BY transition_id DESC
                LIMIT 1
                """,
                (claim_id, claim_id),
            )
            transition_row = cursor.fetchone()
            latest_transition = str(transition_row[0] or "") if transition_row is not None else ""
            if latest_transition != "detach_evidence":
                continue
            cursor.execute(
                """
                UPDATE fact_claims
                SET status = ?, valid_to = ?, last_confirmed_at = ?, updated_at = ?
                WHERE claim_id = ?
                """,
                (
                    previous_status,
                    claim.get("valid_to"),
                    claim.get("last_confirmed_at"),
                    now,
                    claim_id,
                ),
            )
            restored_claim_ids.append(claim_id)
            touched_claim_ids.add(claim_id)

        restored_evidence_count = 0
        for item in evidence:
            claim_id = str(item.get("claim_id", "") or "").strip()
            evidence_type, evidence_id = _evidence_identity(
                item.get("evidence_type"),
                item.get("evidence_id"),
            )
            if not evidence_type:
                raise ValueError("fact evidence snapshot 包含空证据")
            stance = _enum_token("stance", item.get("stance"), _EVIDENCE_STANCES)
            cursor.execute(
                """
                INSERT OR IGNORE INTO fact_evidence (
                    claim_id, evidence_type, evidence_id, stance,
                    weight, observed_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim_id,
                    evidence_type,
                    evidence_id,
                    stance,
                    min(1.0, max(0.0, float(item.get("weight", 1.0) or 1.0))),
                    float(item.get("observed_at", now) or now),
                    json.dumps(
                        item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
            if int(cursor.rowcount or 0) > 0:
                restored_evidence_count += 1
                touched_claim_ids.add(claim_id)

        for claim_id in sorted(touched_claim_ids):
            claim_evidence_ids = sorted(
                str(item.get("evidence_id", "") or "").strip()
                for item in evidence
                if str(item.get("claim_id", "") or "").strip() == claim_id
                and str(item.get("evidence_id", "") or "").strip()
            )
            self._append_fact_transition(
                cursor,
                old_claim_id=claim_id,
                new_claim_id=claim_id,
                transition_type="restore_evidence",
                reason=reason,
                evidence_type="paragraph",
                evidence_id=claim_evidence_ids[0] if claim_evidence_ids else "",
                created_at=now,
            )

        return {
            "restored_claim_ids": sorted(set(restored_claim_ids)),
            "restored_evidence_count": restored_evidence_count,
        }

    def get_fact_transitions(self, claim_id: str) -> List[Dict[str, Any]]:
        token = _required_token("claim_id", claim_id)
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT * FROM fact_transitions
            WHERE old_claim_id = ? OR new_claim_id = ?
            ORDER BY created_at ASC, transition_id ASC
            """,
            (token, token),
        )
        rows: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            payload = dict(row)
            payload["created_at"] = float(payload.get("created_at", 0.0) or 0.0)
            rows.append(payload)
        return rows
