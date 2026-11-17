"""宿主迁移范围：当前人物资料与有效长期记忆，保留历史会话及维护记录。"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from .person_migration_plan import DatabasePlan


class CurrentMemoryPlan(DatabasePlan):
    """向记忆迁移适配器提供本次允许处理的表和记录。"""

    def __init__(self, name: str, path: Path, decisions: Mapping[str, str], context: Dict[str, Any]) -> None:
        super().__init__(name, path, decisions, verify_fingerprint=False)
        self.people = (context["source_person"], context["target_person"])
        self.tables.intersection_update({
            "person_identities", "person_identity_aliases", "person_profile_overrides",
            "person_profile_alias_overrides", "person_profile_snapshots", "person_profile_refresh_queue",
            "fact_claims", "fact_evidence", "paragraphs", "external_memory_refs",
        })

    def selection(self, table: str) -> Tuple[str, Tuple[Any, ...]]:
        if table == "fact_claims":
            return "scope_type='person' AND scope_id IN (?,?) AND status IN ('active','conflicted')", self.people
        if table == "fact_evidence":
            return ("claim_id IN (SELECT claim_id FROM fact_claims WHERE scope_type='person' "
                    "AND scope_id IN (?,?) AND status IN ('active','conflicted'))", self.people)
        if table == "person_profile_snapshots":
            return ("person_id IN (?,?) AND profile_version=(SELECT MAX(latest.profile_version) "
                    "FROM person_profile_snapshots latest WHERE latest.person_id=person_profile_snapshots.person_id)", self.people)
        if table == "paragraphs":
            return ("source IN (?,?) AND (expires_at IS NULL OR expires_at>strftime('%s','now'))",
                    tuple(f"person_fact:{person}" for person in self.people))
        if table == "external_memory_refs":
            return ("paragraph_hash IN (SELECT hash FROM paragraphs WHERE source IN (?,?) "
                    "AND (expires_at IS NULL OR expires_at>strftime('%s','now')))",
                    tuple(f"person_fact:{person}" for person in self.people))
        return "person_id IN (?,?)", self.people

    def rows(self, table: str) -> List[Dict[str, Any]]:
        where, parameters = self.selection(table)
        return [dict(row) for row in self.conn.execute(f'SELECT * FROM "{table}" WHERE {where}', parameters)]

    def transform(
        self, table: str, transform: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
        *, merge: Optional[Callable[[Dict[str, Any], Dict[str, Any], str], Dict[str, Any]]] = None,
    ) -> None:
        where, parameters = self.selection(table)
        start = len(self.changes)
        super().transform(table, transform, merge=merge, where=where, parameters=parameters)
        if table in {"fact_claims", "fact_evidence"}:
            # 为新身份继承有效事实和证据，原记录继续供旧画像及历史变更记录引用。
            self.changes[start:] = [change for change in self.changes[start:] if change["after"] is not None]
