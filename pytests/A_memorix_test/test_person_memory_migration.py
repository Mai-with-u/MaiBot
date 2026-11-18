from pathlib import Path

import json
import sqlite3

from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.person_info.identity import person_id_for
from src.services.person_migration_plan import DatabasePlan, apply_database


def test_memory_identity_facts_evidence_profiles_and_vectors_merge(tmp_path: Path) -> None:
    store = MetadataStore(tmp_path)
    store.connect()
    source, target = person_id_for("qq", "old"), person_id_for("qq", "new")
    store.observe_person_identity(person_id=source, platform="qq", user_id="old", aliases=["旧称呼"], preferred_name="旧称呼")
    store.observe_person_identity(person_id=target, platform="qq", user_id="new", aliases=["新称呼"], preferred_name="新称呼")
    old_claim = store.upsert_fact_claim(
        scope_type="person", scope_id=source, fact_key="喜欢的颜色", value_text="蓝色",
        cardinality="single", evidence_type="message", evidence_id="old-message",
    )["claim_id"]
    target_claim = store.upsert_fact_claim(
        scope_type="person", scope_id=target, fact_key="喜欢的颜色", value_text="红色",
        cardinality="single", evidence_type="message", evidence_id="new-message",
    )["claim_id"]
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO paragraphs(hash,content,vector_index,created_at,metadata,source) VALUES (?,?,?,?,?,?)",
            ("paragraph", "保留原始记忆正文", 42, 1.0,
             json.dumps({"person_ids": [source], "scope_type": "person", "scope_id": source, "chat_id": "old-session"}),
             f"person_fact:{source}"),
        )
        conn.execute(
            "INSERT INTO external_memory_refs(external_id,paragraph_hash,created_at,metadata_json) VALUES (?,?,?,?)",
            (f"person_fact:{source}:hash", "paragraph", 1.0, json.dumps({"person_id": source, "claim_ids": [old_claim]})),
        )
        for person, user in ((source, "old"), (target, "new")):
            conn.execute(
                "INSERT INTO person_profile_snapshots(person_id,profile_version,profile_text,updated_at,expires_at,fact_claim_ids_json) "
                "VALUES (?,1,?,1,9999999999,?)", (person, user, json.dumps([old_claim if person == source else target_claim])),
            )
            conn.execute("INSERT INTO person_profile_active_persons VALUES ('group',?,?,1)", (user, person))
    store.close()
    database = tmp_path / "metadata.db"
    context = {"platform": "qq", "source": "old", "target": "new", "source_person": source, "target_person": target,
               "sessions": {"old-session": "new-session"}, "user_map": {"old": "new"}, "person_map": {source: target}}
    from src.A_memorix.core.person_migration import build_person_migration

    plan = DatabasePlan("memory", database)
    try:
        build_person_migration(plan, context)
        decisions = {item["id"]: "target" for item in plan.conflicts}
    finally:
        plan.close()
    plan = DatabasePlan("memory", database, decisions)
    try:
        build_person_migration(plan, context)
        apply_database(plan.export())
    finally:
        plan.close()
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        identities = conn.execute("SELECT * FROM person_identities").fetchall()
        assert len(identities) == 1
        assert identities[0]["person_id"] == target
        assert identities[0]["user_id"] == "new"
        assert {row[0] for row in conn.execute("SELECT alias FROM person_identity_aliases WHERE person_id=?", (target,))} == {
            "旧称呼", "新称呼",
        }
        paragraph = conn.execute("SELECT * FROM paragraphs WHERE hash='paragraph'").fetchone()
        assert paragraph["vector_index"] == 42
        assert paragraph["content"] == "保留原始记忆正文"
        assert json.loads(paragraph["metadata"]) == {
            "person_ids": [target], "scope_type": "person", "scope_id": target, "chat_id": "new-session",
        }
        assert paragraph["source"] == f"person_fact:{target}"
        claims = conn.execute("SELECT * FROM fact_claims").fetchall()
        assert len(claims) == 2
        assert {row["scope_id"] for row in claims} == {target}
        assert {row["status"] for row in claims} == {"conflicted"}
        new_claim = next(row["claim_id"] for row in claims if row["value_text"] == "蓝色")
        assert old_claim != new_claim
        assert {row[0] for row in conn.execute("SELECT claim_id FROM fact_evidence")} == {new_claim, target_claim}
        profiles = conn.execute("SELECT * FROM person_profile_snapshots").fetchall()
        assert len(profiles) == 2
        assert {row["profile_version"] for row in profiles} == {1, 2}
        assert {row["expires_at"] for row in profiles} == {0}
        assert old_claim not in {item for row in profiles for item in json.loads(row["fact_claim_ids_json"])}
        ref = conn.execute("SELECT * FROM external_memory_refs").fetchone()
        assert ref["external_id"] == f"person_fact:{target}:hash"
        assert json.loads(ref["metadata_json"])["claim_ids"] == [new_claim]
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
