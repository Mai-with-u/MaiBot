"""同实例聊天流记忆归属复制；内容与向量保持稳定，覆盖操作可事务性恢复。

MaiBot 在线迁移的兼容接口，待同步上游 MaiBot_branch。
"""

from typing import Any, Dict, List, Sequence, Union

import hashlib
import json

from .search_hit_processing_service import MemorySearchHitProcessingService


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class ChatMemoryMigration:
    def __init__(self, store: Any) -> None:
        self.store = store

    def snapshot(self, source: str, target: str) -> Dict[str, Any]:
        paragraphs: List[Dict[str, Any]] = []
        for row in self.store.query("SELECT * FROM paragraphs WHERE COALESCE(is_deleted, 0)=0 ORDER BY hash"):
            kind, ids = MemorySearchHitProcessingService._paragraph_scope_identity(row)
            if kind == "chat" and ids.intersection({source, target}):
                paragraphs.append({"row": row, "scopes": sorted(ids)})
        images = self.store.query(
            "SELECT * FROM image_occurrences WHERE scope_type='chat' AND chat_id IN (?, ?) AND status='active' ORDER BY occurrence_id",
            (source, target),
        )
        refs = self.store.query(
            "SELECT * FROM external_memory_refs WHERE json_valid(metadata_json) AND json_extract(metadata_json, '$.chat_id') IN (?, ?) ORDER BY external_id",
            (source, target),
        )
        checkpoints = self.store.query(
            "SELECT * FROM chat_summary_checkpoints WHERE chat_id IN (?, ?) ORDER BY external_id", (source, target)
        )
        episodes = self.store.query(
            "SELECT * FROM episodes WHERE source IN (?, ?) ORDER BY episode_id",
            (f"chat_summary:{source}", f"chat_summary:{target}"),
        )
        facts = self.store.query(
            "SELECT * FROM fact_claims WHERE scope_type='chat' AND scope_id IN (?, ?) ORDER BY claim_id",
            (source, target),
        )
        result = {
            "paragraphs": paragraphs,
            "images": images,
            "refs": refs,
            "checkpoints": checkpoints,
            "episodes": episodes,
            "facts": facts,
        }
        for name, column, ids in (
            ("episode_paragraphs", "episode_id", [row["episode_id"] for row in episodes]),
            ("fact_evidence", "claim_id", [row["claim_id"] for row in facts]),
            ("image_observations", "occurrence_id", [row["occurrence_id"] for row in images]),
            ("image_memory_links", "occurrence_id", [row["occurrence_id"] for row in images]),
        ):
            result[name] = self.store.query(
                f"SELECT * FROM {name} WHERE {column} IN (SELECT value FROM json_each(?)) ORDER BY rowid", (_json(ids),)
            )
        claim_ids = _json([row["claim_id"] for row in facts])
        result["fact_transitions"] = self.store.query(
            "SELECT * FROM fact_transitions WHERE old_claim_id IN (SELECT value FROM json_each(?)) OR new_claim_id IN (SELECT value FROM json_each(?)) ORDER BY transition_id",
            (claim_ids, claim_ids),
        )
        return {
            **result,
            "fingerprint": hashlib.sha256(_json(result).encode()).hexdigest(),
            "counts": {
                "source": sum(source in item["scopes"] for item in paragraphs),
                "target": sum(target in item["scopes"] for item in paragraphs),
                "source_images": sum(row["chat_id"] == source for row in images),
                "target_images": sum(row["chat_id"] == target for row in images),
                "source_facts": sum(row["scope_id"] == source for row in facts),
                "target_facts": sum(row["scope_id"] == target for row in facts),
                "source_episodes": sum(row["source"] == f"chat_summary:{source}" for row in episodes),
                "target_episodes": sum(row["source"] == f"chat_summary:{target}" for row in episodes),
            },
        }

    def apply(self, job_id: str, source: str, target: str, expected: str) -> Dict[str, Any]:
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS chat_scope_transfers (id TEXT PRIMARY KEY, status TEXT NOT NULL, changes TEXT NOT NULL)"
            )
            previous = conn.execute("SELECT status FROM chat_scope_transfers WHERE id=?", (job_id,)).fetchone()
            if previous:
                if previous[0] == "applied":
                    return {"success": True}
                raise ValueError("该记忆迁移已恢复，请重新创建迁移预览")
            snapshot = self.snapshot(source, target)
            if snapshot["fingerprint"] != expected:
                raise ValueError("长期记忆已变化，请重新预览")
            changes: List[Dict[str, Any]] = []

            def update(table: str, key: str, before: Dict[str, Any], patch: Dict[str, Any]) -> None:
                conn.execute(
                    f"UPDATE {table} SET {','.join(name + '=?' for name in patch)} WHERE {key}=?",
                    (*patch.values(), before[key]),
                )
                changes.append(
                    {
                        "table": table,
                        "key": key,
                        "id": before[key],
                        "before": {name: before[name] for name in patch},
                        "after": patch,
                    }
                )

            def insert(table: str, key: Union[str, Sequence[str]], row: Dict[str, Any]) -> None:
                conn.execute(
                    f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values())
                )
                keys = [key] if isinstance(key, str) else list(key)
                changes.append(
                    {"table": table, "keys": {name: row[name] for name in keys}, "before": None, "after": row}
                )

            def remove(table: str, keys: Sequence[str], row: Dict[str, Any]) -> None:
                identity = {name: row[name] for name in keys}
                conn.execute(
                    f"DELETE FROM {table} WHERE {' AND '.join(name + '=?' for name in keys)}", tuple(identity.values())
                )
                changes.append({"table": table, "keys": identity, "before": row, "after": None})

            for item in snapshot["paragraphs"]:
                row, scopes = item["row"], set(item["scopes"])
                scopes.discard(target)
                if source in scopes:
                    scopes.add(target)
                meta = json.loads(row["metadata"] or "{}")
                for key in ("chat_id", "session_id", "stream_id", "chat_ids", "session_ids", "stream_ids"):
                    meta.pop(key, None)
                meta.update(scope_type="chat", chat_ids=sorted(scopes))
                if scopes:
                    meta["chat_id"] = source if source in scopes else sorted(scopes)[0]
                # 没有归属的旧目标内容仍留在恢复账本，不能因旧 source 字段重新进入检索。
                patch = {"metadata": _json(meta)}
                if not scopes:
                    patch["source"] = f"migration_archive:{job_id}:{row['hash']}"
                    meta["source"] = patch["source"]
                    patch["metadata"] = _json(meta)
                update("paragraphs", "hash", row, patch)
            episode_ids = {
                row["episode_id"]: f"migration_{job_id}_{row['episode_id']}"
                for row in snapshot["episodes"]
                if row["source"] == f"chat_summary:{source}"
            }
            for row in snapshot["episodes"]:
                if row["source"] == f"chat_summary:{target}":
                    update("episodes", "episode_id", row, {"source": f"migration_archive:{job_id}"})
                else:
                    insert(
                        "episodes",
                        "episode_id",
                        {**row, "episode_id": episode_ids[row["episode_id"]], "source": f"chat_summary:{target}"},
                    )
            for row in snapshot["episode_paragraphs"]:
                if row["episode_id"] in episode_ids:
                    insert(
                        "episode_paragraphs",
                        ("episode_id", "paragraph_hash"),
                        {**row, "episode_id": episode_ids[row["episode_id"]]},
                    )
            claim_ids = {
                row["claim_id"]: self.store._claim_identity(
                    scope_type="chat",
                    scope_id=target,
                    fact_key=row["fact_key"],
                    value_normalized=row["value_normalized"],
                    polarity=row["polarity"],
                )
                for row in snapshot["facts"]
                if row["scope_id"] == source
            }
            removed_claims = {row["claim_id"] for row in snapshot["facts"] if row["scope_id"] == target}
            for row in snapshot["fact_transitions"]:
                if row["old_claim_id"] in removed_claims or row["new_claim_id"] in removed_claims:
                    remove("fact_transitions", ("transition_id",), row)
            for row in snapshot["fact_evidence"]:
                if row["claim_id"] in removed_claims:
                    remove("fact_evidence", ("claim_id", "evidence_type", "evidence_id", "stance"), row)
            for row in snapshot["facts"]:
                if row["scope_id"] == target:
                    remove("fact_claims", ("claim_id",), row)
            for row in snapshot["facts"]:
                if row["scope_id"] == source:
                    conflict_group = self.store._conflict_group(
                        scope_type="chat",
                        scope_id=target,
                        fact_key=row["fact_key"],
                        value_normalized=row["value_normalized"],
                        cardinality=row["cardinality"],
                    )
                    insert(
                        "fact_claims",
                        "claim_id",
                        {
                            **row,
                            "claim_id": claim_ids[row["claim_id"]],
                            "scope_id": target,
                            "conflict_group": conflict_group,
                        },
                    )
            for row in snapshot["fact_evidence"]:
                if row["claim_id"] in claim_ids:
                    insert(
                        "fact_evidence",
                        ("claim_id", "evidence_type", "evidence_id", "stance"),
                        {
                            **row,
                            "claim_id": claim_ids[row["claim_id"]],
                            "evidence_id": episode_ids.get(row["evidence_id"], row["evidence_id"]),
                        },
                    )
            for row in snapshot["fact_transitions"]:
                if row["old_claim_id"] in claim_ids or row["new_claim_id"] in claim_ids:
                    clone = {
                        **row,
                        "old_claim_id": claim_ids.get(row["old_claim_id"], row["old_claim_id"]),
                        "new_claim_id": claim_ids.get(row["new_claim_id"], row["new_claim_id"]),
                    }
                    clone["transition_id"] = conn.execute(
                        "SELECT COALESCE(MAX(transition_id),0)+1 FROM fact_transitions"
                    ).fetchone()[0]
                    insert("fact_transitions", "transition_id", clone)
            for row in snapshot["images"]:
                if row["chat_id"] == target:
                    # 恢复窗口内保留资产的活动引用，避免图片后台任务回收唯一副本。
                    update("image_occurrences", "occurrence_id", row, {"scope_type": "archive"})
                    continue
                clone = {
                    **row,
                    "occurrence_id": f"migration_{job_id}_{row['occurrence_id']}",
                    "chat_id": target,
                    "external_ref": f"migration_{job_id}_{row['external_ref']}",
                    "installation_id": "",
                }
                insert("image_occurrences", "occurrence_id", clone)
                for table, key in (("image_observations", "observation_id"), ("image_memory_links", "link_id")):
                    for child in snapshot[table]:
                        if child["occurrence_id"] != row["occurrence_id"]:
                            continue
                        copied = {
                            **child,
                            key: f"migration_{job_id}_{child[key]}",
                            "occurrence_id": clone["occurrence_id"],
                        }
                        if table == "image_observations":
                            for field in ("supersedes_id", "superseded_by"):
                                if child[field]:
                                    copied[field] = f"migration_{job_id}_{child[field]}"
                        if table == "image_memory_links" and child["target_type"] == "episode":
                            copied["target_id"] = episode_ids.get(child["target_id"], child["target_id"])
                        insert(table, key, copied)
            for table, rows in (
                ("external_memory_refs", snapshot["refs"]),
                ("chat_summary_checkpoints", snapshot["checkpoints"]),
            ):
                for row in rows:
                    meta = json.loads(row["metadata_json"] or "{}") if table == "external_memory_refs" else {}
                    owner = meta.get("chat_id") if table == "external_memory_refs" else row["chat_id"]
                    if owner == target:
                        # 保留旧记录供恢复，不让摘要进度继续引用被覆盖的目标窗口。
                        patch = (
                            {"metadata_json": _json({**meta, "chat_id": f"migration_archive:{job_id}"})}
                            if table == "external_memory_refs"
                            else {"chat_id": f"migration_archive:{job_id}"}
                        )
                        update(table, "external_id", row, patch)
                    else:
                        clone = {**row, "external_id": f"migration_{job_id}_{row['external_id']}"}
                        if table == "external_memory_refs":
                            clone["metadata_json"] = _json({**meta, "chat_id": target})
                        else:
                            clone["chat_id"] = target
                        insert(table, "external_id", clone)
            conn.execute("INSERT INTO chat_scope_transfers VALUES (?, 'applied', ?)", (job_id, _json(changes)))
        return {"success": True, "counts": snapshot["counts"]}

    def restore(self, job_id: str) -> Dict[str, Any]:
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS chat_scope_transfers (id TEXT PRIMARY KEY, status TEXT NOT NULL, changes TEXT NOT NULL)"
            )
            record = conn.execute("SELECT status, changes FROM chat_scope_transfers WHERE id=?", (job_id,)).fetchone()
            if record is None or record[0] == "restored":
                return {"success": True}
            for change in reversed(json.loads(record[1])):
                table = change["table"]
                keys = change.get("keys") or {change["key"]: change["id"]}
                where = " AND ".join(name + "=?" for name in keys)
                row = conn.execute(f"SELECT * FROM {table} WHERE {where}", tuple(keys.values())).fetchone()
                if change["after"] is None:
                    if row is not None:
                        raise ValueError("记忆已在迁移后被修改，不能自动覆盖恢复")
                    values = change["before"]
                    conn.execute(
                        f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                        tuple(values.values()),
                    )
                    continue
                if row is None or any(row[name] != value for name, value in change["after"].items()):
                    raise ValueError("记忆已在迁移后被修改，不能自动覆盖恢复")
                if change["before"] is None:
                    conn.execute(f"DELETE FROM {table} WHERE {where}", tuple(keys.values()))
                else:
                    values = change["before"]
                    conn.execute(
                        f"UPDATE {table} SET {','.join(name + '=?' for name in values)} WHERE {where}",
                        (*values.values(), *keys.values()),
                    )
            conn.execute("UPDATE chat_scope_transfers SET status='restored' WHERE id=?", (job_id,))
        return {"success": True}
