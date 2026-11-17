"""停机人物迁移适配：由宿主提供数据库计划器，不初始化记忆内核或改写向量。"""

from typing import Any, Dict, List, Mapping, Optional, Protocol

import hashlib
import json


class PersonMigrationPlanner(Protocol):
    tables: set
    changes: List[Dict[str, Any]]

    def rows(self, table: str) -> List[Dict[str, Any]]: ...
    def columns(self, table: str) -> List[str]: ...
    def transform(self, table: str, transform: Any, *, merge: Optional[Any] = None) -> None: ...
    def combine(self, table: str, source: Dict[str, Any], target: Dict[str, Any], key: str) -> Dict[str, Any]: ...
    def choose(self, key: str, field: str, source: Any, target: Any) -> Any: ...


def _hash(value: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_person_migration(plan: PersonMigrationPlanner, context: Mapping[str, Any]) -> None:
    """迁移所有结构化人物/私聊引用；冲突交给宿主逐字段确认，未知归属表直接拒绝。"""
    source, target = context["source_person"], context["target_person"]
    sessions = context["sessions"]
    users = context["user_map"]
    claim_ids: Dict[str, str] = {}
    claims = plan.rows("fact_claims") if "fact_claims" in plan.tables else []
    transformed_claims: Dict[str, Dict[str, Any]] = {}
    for row in claims:
        changed = dict(row)
        scope = target if row["scope_type"] == "person" and row["scope_id"] == source else (
            sessions.get(row["scope_id"], row["scope_id"]) if row["scope_type"] == "chat" else row["scope_id"]
        )
        if scope != row["scope_id"]:
            changed["scope_id"] = scope
            changed["claim_id"] = _hash({"scope_type": row["scope_type"], "scope_id": scope,
                                          "fact_key": row["fact_key"], "value": row["value_normalized"], "polarity": row["polarity"]})
            group = {"scope_type": row["scope_type"], "scope_id": scope, "fact_key": row["fact_key"]}
            if row["cardinality"] == "set":
                group["value"] = row["value_normalized"]
            changed["conflict_group"] = _hash(group)
            claim_ids[row["claim_id"]] = changed["claim_id"]
        transformed_claims[row["claim_id"]] = changed
    affected_groups = {row["conflict_group"] for old, row in transformed_claims.items() if old in claim_ids}
    for row in transformed_claims.values():
        if row["conflict_group"] not in affected_groups or row["status"] not in {"active", "conflicted"}:
            continue
        competitors = {other["claim_id"] for other in transformed_claims.values()
                       if other["conflict_group"] == row["conflict_group"] and other["status"] in {"active", "conflicted"}}
        if len(competitors) > 1:
            row["status"] = "conflicted"

    def source_ref(value: str) -> str:
        prefixes = {f"person_fact:{source}": f"person_fact:{target}",
                    **{f"chat_summary:{old}": f"chat_summary:{new}" for old, new in sessions.items()}}
        for old, new in prefixes.items():
            if value == old or value.startswith(old + ":"):
                return new + value[len(old):]
        return value

    def rewrite(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            result = {field: rewrite(item, field) for field, item in value.items()}
            if value.get("scope_type") == "person" and value.get("scope_id") == source:
                result["scope_id"] = target
            elif value.get("scope_type") == "chat" and value.get("scope_id") in sessions:
                result["scope_id"] = sessions[value["scope_id"]]
            return result
        if isinstance(value, list):
            result = [rewrite(item, key) for item in value]
            return list(dict.fromkeys(result)) if all(isinstance(item, str) for item in result) else result
        if not isinstance(value, str):
            return value
        if key in {"person_id", "person_ids", "target_person_id"}:
            return target if value == source else value
        if key in {"user_id", "user_ids", "target_user_id", "sender_id"}:
            return users.get(value, value)
        if key in {"chat_id", "session_id", "stream_id", "target_chat_id", "chat_ids", "session_ids", "stream_ids"}:
            return sessions.get(value, value)
        if key in {"claim_id", "claim_ids", "old_claim_id", "new_claim_id", "fact_claim_ids"}:
            return claim_ids.get(value, value)
        if key in {"source", "external_id", "external_ref"}:
            return source_ref(value)
        return value

    snapshots = plan.rows("person_profile_snapshots") if "person_profile_snapshots" in plan.tables else []
    latest_version = max((row["profile_version"] for row in snapshots if row["person_id"] == target), default=0)
    versions = {row["snapshot_id"]: latest_version + index + 1 for index, row in enumerate(
        sorted((row for row in snapshots if row["person_id"] == source), key=lambda row: row["profile_version"]))}
    direct_tables = {"person_identities", "person_identity_aliases", "person_profile_switches",
                     "person_profile_snapshots", "person_profile_active_persons", "person_profile_overrides",
                     "person_profile_alias_overrides", "person_profile_refresh_queue", "memory_fuzzy_modify_plans"}
    known_chats = {item["stream_id"] for item in (
        plan.rows("person_profile_active_persons") if "person_profile_active_persons" in plan.tables else []
    ) if item["person_id"] in {source, target}}
    known_chats.update(context.get("platform_sessions", []))
    for table in sorted(plan.tables):
        columns = set(plan.columns(table))
        if columns.intersection({"person_id", "user_id", "target_person_id"}) and table not in direct_tables:
            raise ValueError(f"记忆人物归属表缺少迁移规则: {table}")

        def transform(row: Dict[str, Any], table: str = table) -> Dict[str, Any]:
            old_person = row.get("person_id")
            if table == "fact_claims":
                row = dict(transformed_claims[row["claim_id"]])
            # person_profile_switches 没有 platform 列，仅在来源人物有记录的聊天范围内迁移。
            platform_matches = row.get("platform", context["platform"]) == context["platform"]
            if table == "person_profile_switches" and row["user_id"] in users and row["stream_id"] not in known_chats | set(sessions):
                raise ValueError("画像开关引用了无法确认平台归属的聊天流，不能猜测其用户身份")
            for field, value in list(row.items()):
                if field == "user_id" and (not platform_matches or (
                    table == "person_profile_switches" and row["stream_id"] not in known_chats | set(sessions)
                ) or (table == "person_profile_active_persons" and old_person not in {source, target})):
                    continue
                if isinstance(value, str) and (field.endswith("_json") or field == "metadata") and value:
                    data = json.loads(value)
                    changed = rewrite(data, field.removesuffix("_json"))
                    if changed != data:
                        row[field] = json.dumps(changed, ensure_ascii=False)
                else:
                    row[field] = rewrite(value, field)
            if table == "person_profile_snapshots" and old_person in {source, target}:
                if old_person == source:
                    row["profile_version"] = versions[row["snapshot_id"]]
                row["expires_at"] = 0
            if table == "person_profile_refresh_queue" and old_person in {source, target}:
                row.update(status="pending", reason="person_identity_migration", retry_count=0, last_error=None)
            return row

        def merge(incoming: Dict[str, Any], existing: Dict[str, Any], key: str, table: str = table) -> Dict[str, Any]:
            if table in {"person_profile_alias_overrides"}:
                aliases = list(dict.fromkeys([*json.loads(existing["aliases_json"]), *json.loads(incoming["aliases_json"])]))
                incoming, existing = dict(incoming), dict(existing)
                incoming["aliases_json"] = existing["aliases_json"] = json.dumps(aliases, ensure_ascii=False)
            return plan.combine(table, incoming, existing, key)

        # 没有主键的内部索引/FTS shadow 表不承载人物字段，不读取或重建它们。
        relevant = columns.intersection({"person_id", "user_id", "target_person_id", "scope_id", "chat_id", "stream_id",
                                         "source", "external_id", "external_ref", "metadata", "metadata_json",
                                         "participants_json", "claim_id", "old_claim_id", "new_claim_id", "payload_json",
                                         "result_json", "request_json", "fact_claim_ids_json"})
        if relevant:
            plan.transform(table, transform, merge=merge)
