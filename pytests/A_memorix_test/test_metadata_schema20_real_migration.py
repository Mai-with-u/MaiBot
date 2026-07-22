from pathlib import Path

import sqlite3

import pytest

from src.A_memorix.core.storage.metadata_store import MetadataStore, SCHEMA_VERSION


_PARAGRAPH_HASHES = {
    "live": "a" * 64,
    "permanent": "b" * 64,
    "deleted": "c" * 64,
    "pending": "d" * 64,
}
_RELATION_HASHES = {
    "normal": "1" * 64,
    "permanent": "2" * 64,
    "inactive": "3" * 64,
    "deleted": "4" * 64,
    "trigger_probe": "5" * 64,
}
_SCHEMA21_FINGERPRINT_TABLES = (
    "paragraphs",
    "relations",
    "deleted_relations",
    "episode_rebuild_sources",
    "external_memory_refs",
    "storage_cleanup_jobs",
    "fact_claims",
    "fact_evidence",
    "fact_transitions",
    "relation_graph_projection_jobs",
)


def _materialize_schema16_database(database_path: Path) -> None:
    """把空库降格为真实 schema 16 关键表结构，并写入历史数据。"""

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(
            """
            DROP TABLE paragraphs;
            CREATE TABLE paragraphs (
                hash TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                vector_index INTEGER,
                created_at REAL,
                updated_at REAL,
                metadata TEXT,
                source TEXT,
                word_count INTEGER,
                event_time REAL,
                event_time_start REAL,
                event_time_end REAL,
                time_granularity TEXT,
                time_confidence REAL DEFAULT 1.0,
                knowledge_type TEXT DEFAULT 'mixed',
                is_permanent BOOLEAN DEFAULT 0,
                last_accessed REAL,
                access_count INTEGER DEFAULT 0,
                is_deleted INTEGER DEFAULT 0,
                deleted_at REAL
            );
            CREATE INDEX idx_paragraphs_vector ON paragraphs(vector_index);
            CREATE INDEX idx_paragraphs_source ON paragraphs(source);
            CREATE INDEX idx_paragraphs_deleted ON paragraphs(is_deleted, deleted_at);
            CREATE INDEX idx_paragraphs_event_time ON paragraphs(event_time);
            CREATE INDEX idx_paragraphs_event_start ON paragraphs(event_time_start);
            CREATE INDEX idx_paragraphs_event_end ON paragraphs(event_time_end);
            CREATE INDEX idx_paragraphs_source_live_created
            ON paragraphs(source, is_deleted, created_at, hash);

            DROP TABLE relations;
            CREATE TABLE relations (
                hash TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                vector_index INTEGER,
                confidence REAL DEFAULT 1.0,
                vector_state TEXT DEFAULT 'none',
                vector_updated_at REAL,
                vector_error TEXT,
                vector_retry_count INTEGER DEFAULT 0,
                created_at REAL,
                source_paragraph TEXT,
                metadata TEXT,
                is_permanent BOOLEAN DEFAULT 0,
                last_accessed REAL,
                access_count INTEGER DEFAULT 0,
                last_access_reinforced_at REAL,
                is_inactive BOOLEAN DEFAULT 0,
                inactive_since REAL,
                is_pinned BOOLEAN DEFAULT 0,
                protected_until REAL,
                last_reinforced REAL,
                UNIQUE(subject, predicate, object)
            );
            CREATE INDEX idx_relations_vector ON relations(vector_index);
            CREATE INDEX idx_relations_subject ON relations(subject);
            CREATE INDEX idx_relations_object ON relations(object);
            CREATE INDEX idx_relations_inactive ON relations(is_inactive, inactive_since);
            CREATE INDEX idx_relations_protected ON relations(is_pinned, protected_until);
            CREATE INDEX idx_relations_subject_object_active
            ON relations(LOWER(TRIM(subject)), LOWER(TRIM(object)), is_inactive);
            CREATE INDEX idx_relations_object_active
            ON relations(LOWER(TRIM(object)), is_inactive);
            CREATE TABLE schema16_relation_trigger_audit (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                relation_hash TEXT NOT NULL,
                confidence REAL,
                recorded_subject TEXT NOT NULL
            );
            CREATE TRIGGER trg_schema16_relations_insert_audit
            AFTER INSERT ON relations
            BEGIN
                INSERT INTO schema16_relation_trigger_audit (
                    event_type, relation_hash, confidence, recorded_subject
                ) VALUES ('insert', NEW.hash, NEW.confidence, NEW.subject);
            END;
            CREATE TRIGGER trg_schema16_relations_update_audit
            AFTER UPDATE OF confidence ON relations
            WHEN NEW.confidence IS NOT OLD.confidence
            BEGIN
                INSERT INTO schema16_relation_trigger_audit (
                    event_type, relation_hash, confidence, recorded_subject
                ) VALUES ('update', NEW.hash, NEW.confidence, NEW.subject);
            END;

            DROP TABLE deleted_relations;
            CREATE TABLE deleted_relations (
                hash TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                vector_index INTEGER,
                confidence REAL DEFAULT 1.0,
                vector_state TEXT DEFAULT 'none',
                vector_updated_at REAL,
                vector_error TEXT,
                vector_retry_count INTEGER DEFAULT 0,
                created_at REAL,
                source_paragraph TEXT,
                metadata TEXT,
                is_permanent BOOLEAN DEFAULT 0,
                last_accessed REAL,
                access_count INTEGER DEFAULT 0,
                last_access_reinforced_at REAL,
                is_inactive BOOLEAN DEFAULT 0,
                inactive_since REAL,
                is_pinned BOOLEAN DEFAULT 0,
                protected_until REAL,
                last_reinforced REAL,
                deleted_at REAL
            );

            DROP TABLE episodes;
            CREATE TABLE episodes (
                episode_id TEXT PRIMARY KEY,
                source TEXT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                event_time_start REAL,
                event_time_end REAL,
                time_granularity TEXT,
                time_confidence REAL DEFAULT 1.0,
                participants_json TEXT,
                keywords_json TEXT,
                evidence_ids_json TEXT,
                paragraph_count INTEGER DEFAULT 0,
                llm_confidence REAL DEFAULT 0.0,
                segmentation_model TEXT,
                segmentation_version TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            DROP TABLE episode_rebuild_sources;
            CREATE TABLE episode_rebuild_sources (
                source TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                reason TEXT,
                requested_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE episode_pending_paragraphs (
                paragraph_hash TEXT PRIMARY KEY,
                source TEXT,
                created_at REAL,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                updated_at REAL NOT NULL
            );

            DROP TABLE external_memory_refs;
            CREATE TABLE external_memory_refs (
                external_id TEXT PRIMARY KEY,
                paragraph_hash TEXT NOT NULL,
                source_type TEXT,
                created_at REAL NOT NULL,
                metadata_json TEXT
            );

            DROP TABLE person_profile_snapshots;
            CREATE TABLE person_profile_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id TEXT NOT NULL,
                profile_version INTEGER NOT NULL,
                profile_text TEXT NOT NULL,
                aliases_json TEXT,
                relation_edges_json TEXT,
                vector_evidence_json TEXT,
                evidence_ids_json TEXT,
                evidence_fingerprint TEXT,
                updated_at REAL NOT NULL,
                expires_at REAL,
                source_note TEXT,
                UNIQUE(person_id, profile_version)
            );

            DROP TABLE IF EXISTS fact_evidence;
            DROP TABLE IF EXISTS fact_transitions;
            DROP TABLE IF EXISTS fact_claims;
            DROP TABLE IF EXISTS storage_cleanup_jobs;
            """
        )

        paragraph_rows = (
            (
                _PARAGRAPH_HASHES["live"],
                "用户喜欢蓝色",
                '{"owner": "legacy-live"}',
                "source-live",
                10.0,
                0,
                0,
                None,
            ),
            (
                _PARAGRAPH_HASHES["permanent"],
                "永久保留的历史段落",
                '{"owner": "legacy-permanent"}',
                "source-permanent",
                11.0,
                1,
                0,
                None,
            ),
            (
                _PARAGRAPH_HASHES["deleted"],
                "已经软删除的历史段落",
                '{"owner": "legacy-deleted"}',
                "source-deleted-only",
                12.0,
                0,
                1,
                90.0,
            ),
            (
                _PARAGRAPH_HASHES["pending"],
                "仍在等待 Episode 构建",
                '{"owner": "legacy-pending"}',
                "source-from-pending",
                13.0,
                0,
                0,
                None,
            ),
        )
        connection.executemany(
            """
            INSERT INTO paragraphs (
                hash, content, metadata, source, created_at,
                is_permanent, is_deleted, deleted_at, knowledge_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'factual')
            """,
            paragraph_rows,
        )
        relation_rows = (
            (
                _RELATION_HASHES["normal"],
                "用户",
                "喜欢",
                "蓝色",
                20.0,
                _PARAGRAPH_HASHES["live"],
                0,
                0,
                0,
                None,
                7.0,
            ),
            (
                _RELATION_HASHES["permanent"],
                "用户",
                "长期居住",
                "深圳",
                21.0,
                _PARAGRAPH_HASHES["permanent"],
                1,
                0,
                0,
                None,
                8.0,
            ),
            (
                _RELATION_HASHES["inactive"],
                "用户",
                "曾经喜欢",
                "红色",
                22.0,
                _PARAGRAPH_HASHES["live"],
                0,
                1,
                0,
                80.0,
                9.0,
            ),
        )
        connection.executemany(
            """
            INSERT INTO relations (
                hash, subject, predicate, object, created_at, source_paragraph,
                is_permanent, is_inactive, is_pinned, inactive_since,
                last_access_reinforced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            relation_rows,
        )
        connection.execute(
            """
            INSERT INTO deleted_relations (
                hash, subject, predicate, object, created_at, source_paragraph,
                is_inactive, inactive_since, deleted_at
            ) VALUES (?, '用户', '不再居住', '旧城市', 23.0, ?, 1, 70.0, 95.0)
            """,
            (_RELATION_HASHES["deleted"], _PARAGRAPH_HASHES["deleted"]),
        )
        connection.execute(
            """
            INSERT INTO paragraph_relations (paragraph_hash, relation_hash)
            VALUES (?, ?)
            """,
            (_PARAGRAPH_HASHES["live"], _RELATION_HASHES["normal"]),
        )
        connection.execute(
            """
            INSERT INTO paragraph_stale_relation_marks (
                paragraph_hash, relation_hash, query_tool_id, reason,
                source_type, source_id, source_operation_id, created_at, updated_at
            ) VALUES (?, ?, 'legacy-query', 'legacy-stale',
                      'feedback', 'legacy-source', 'legacy-operation', 24.0, 25.0)
            """,
            (_PARAGRAPH_HASHES["live"], _RELATION_HASHES["normal"]),
        )
        connection.executemany(
            """
            INSERT INTO external_memory_refs (
                external_id, paragraph_hash, source_type, created_at, metadata_json
            ) VALUES (?, ?, 'legacy', 30.0, ?)
            """,
            (
                ("external-valid", _PARAGRAPH_HASHES["live"], '{"kept": true}'),
                ("external-dangling", "f" * 64, '{"kept": false}'),
            ),
        )
        connection.execute(
            """
            INSERT INTO episodes (
                episode_id, source, title, summary, event_time_start,
                event_time_end, created_at, updated_at
            ) VALUES ('episode-legacy', 'source-episode-only', '旧 Episode',
                      '旧 Episode 摘要', 40.0, 41.0, 42.0, 43.0)
            """
        )
        connection.executemany(
            """
            INSERT INTO episode_rebuild_sources (
                source, status, retry_count, last_error, reason, requested_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ("source-done", "done", 0, None, "legacy_done", 100.0, 101.0),
                ("source-running", "running", 2, "worker lost", "legacy_running", 200.0, 201.0),
                ("source-pending-overlap", "done", 1, None, "legacy_done", 300.0, 301.0),
            ),
        )
        connection.executemany(
            """
            INSERT INTO episode_pending_paragraphs (
                paragraph_hash, source, created_at, status, retry_count, last_error, updated_at
            ) VALUES (?, ?, ?, 'pending', 0, NULL, ?)
            """,
            (
                (_PARAGRAPH_HASHES["pending"], "source-from-pending", 13.0, 310.0),
                ("e" * 64, "source-pending-overlap", 14.0, 311.0),
            ),
        )
        connection.execute("DELETE FROM schema_migrations")
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (16, 1.0)"
        )
        connection.commit()


def _inject_known_orphaned_associations(database_path: Path) -> None:
    """模拟历史版本在未启用外键约束时留下的孤立关联。"""
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO paragraph_relations (paragraph_hash, relation_hash)
            VALUES (?, ?)
            """,
            ("f" * 64, _RELATION_HASHES["normal"]),
        )
        connection.execute(
            """
            INSERT INTO paragraph_entities (paragraph_hash, entity_hash, mention_count)
            VALUES (?, ?, 1)
            """,
            (_PARAGRAPH_HASHES["live"], "e" * 64),
        )
        connection.execute(
            """
            INSERT INTO episode_paragraphs (episode_id, paragraph_hash, position)
            VALUES ('episode-legacy', ?, 1)
            """,
            ("f" * 64,),
        )
        connection.execute(
            """
            INSERT INTO memory_feedback_action_logs (
                task_id, query_tool_id, action_type, target_hash, created_at
            ) VALUES (999, 'orphan-query', 'test', NULL, 50.0)
            """
        )
        connection.execute(
            """
            INSERT INTO paragraph_stale_relation_marks (
                paragraph_hash, relation_hash, query_tool_id, task_id,
                reason, created_at, updated_at
            ) VALUES (?, ?, 'orphan-task-query', 999, 'orphan-task', 51.0, 52.0)
            """,
            (_PARAGRAPH_HASHES["permanent"], _RELATION_HASHES["permanent"]),
        )
        connection.execute(
            """
            INSERT INTO delete_operation_items (
                operation_id, item_type, item_hash, created_at
            ) VALUES ('missing-operation', 'relation', ?, 53.0)
            """,
            (_RELATION_HASHES["normal"],),
        )


def _inject_unknown_foreign_key_violation(database_path: Path) -> None:
    """创建不属于 A_Memorix 已知关联表的外键异常。"""
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(
            """
            CREATE TABLE unknown_parent (
                id INTEGER PRIMARY KEY
            );
            CREATE TABLE unknown_child (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER NOT NULL,
                FOREIGN KEY (parent_id) REFERENCES unknown_parent(id)
            );
            INSERT INTO unknown_child (id, parent_id) VALUES (1, 999);
            """
        )


def _column_names(store: MetadataStore, table: str) -> set[str]:
    return {str(row["name"]) for row in store.query(f"PRAGMA table_info({table})")}


def _normalize_sql_default(value: object) -> str | None:
    if value is None:
        return None
    token = " ".join(str(value).strip().split())
    while token.startswith("(") and token.endswith(")"):
        token = token[1:-1].strip()
    return token


def _quote_sqlite_identifier(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def _table_structure_fingerprint(store: MetadataStore, table: str) -> dict[str, object]:
    """比较可观察结构语义，忽略 PRAGMA 序号和 SQLite 自动索引的内部名称。

    调用方使用同一个正式表名读取 fresh 与 migrated 数据库，因此表自身名称
    无需进入指纹。外键目标表名不会被归一化，它属于必须严格相等的持久化契约。
    """

    quoted_table = _quote_sqlite_identifier(table)

    columns = tuple(
        (
            str(row["name"]),
            str(row["type"] or "").upper(),
            int(row["notnull"] or 0),
            _normalize_sql_default(row["dflt_value"]),
            int(row["pk"] or 0),
        )
        for row in store.query(f"PRAGMA table_info({quoted_table})")
    )

    indexes = []
    for row in store.query(f"PRAGMA index_list({quoted_table})"):
        index_name = str(row["name"])
        normalized_name = "<sqlite-autoindex>" if index_name.startswith("sqlite_autoindex_") else index_name
        quoted_index = _quote_sqlite_identifier(index_name)
        indexed_columns = tuple(
            (
                int(column["cid"]),
                None if column["name"] is None else str(column["name"]),
                int(column["desc"] or 0),
                str(column["coll"] or ""),
                int(column["key"] or 0),
            )
            for column in store.query(f"PRAGMA index_xinfo({quoted_index})")
        )
        indexes.append(
            (
                normalized_name,
                int(row["unique"] or 0),
                str(row["origin"] or ""),
                int(row["partial"] or 0),
                indexed_columns,
            )
        )

    foreign_key_groups: dict[int, dict[str, object]] = {}
    for row in store.query(f"PRAGMA foreign_key_list({quoted_table})"):
        group_id = int(row["id"])
        group = foreign_key_groups.setdefault(
            group_id,
            {
                "table": str(row["table"]),
                "on_update": str(row["on_update"]).upper(),
                "on_delete": str(row["on_delete"]).upper(),
                "match": str(row["match"]).upper(),
                "columns": [],
            },
        )
        columns_in_group = group["columns"]
        assert isinstance(columns_in_group, list)
        columns_in_group.append(
            (
                int(row["seq"]),
                str(row["from"]),
                None if row["to"] is None else str(row["to"]),
            )
        )
    foreign_keys = tuple(
        sorted(
            (
                str(group["table"]),
                str(group["on_update"]),
                str(group["on_delete"]),
                str(group["match"]),
                tuple(sorted(group["columns"])),
            )
            for group in foreign_key_groups.values()
        )
    )
    return {
        "columns": columns,
        "indexes": tuple(sorted(indexes, key=repr)),
        "foreign_keys": foreign_keys,
    }


def test_schema16_database_migrates_to_schema21_without_losing_live_data(tmp_path: Path) -> None:
    bootstrap = MetadataStore(data_dir=tmp_path)
    bootstrap.connect()
    database_path = bootstrap.get_db_path()
    bootstrap.close()
    _materialize_schema16_database(database_path)

    migrated = MetadataStore(data_dir=tmp_path)
    migrated.connect()
    try:
        assert migrated.get_schema_version() == SCHEMA_VERSION == 21

        paragraphs = {
            str(row["hash"]): dict(row)
            for row in migrated.query(
                """
                SELECT hash, content, metadata, source, is_permanent, is_deleted,
                       expires_at, deletion_reason
                FROM paragraphs
                """
            )
        }
        assert len(paragraphs) == 4
        assert paragraphs[_PARAGRAPH_HASHES["live"]]["content"] == "用户喜欢蓝色"
        assert paragraphs[_PARAGRAPH_HASHES["live"]]["metadata"] == '{"owner": "legacy-live"}'
        assert paragraphs[_PARAGRAPH_HASHES["live"]]["expires_at"] is None
        assert paragraphs[_PARAGRAPH_HASHES["permanent"]]["is_permanent"] == 1
        assert paragraphs[_PARAGRAPH_HASHES["deleted"]]["deletion_reason"] == (
            "schema_19_migrated_soft_delete"
        )

        relations = {
            str(row["hash"]): dict(row)
            for row in migrated.query(
                """
                SELECT hash, subject, predicate, object, is_permanent, is_pinned,
                       is_inactive, retention_strength, retention_anchor_at,
                       next_lifecycle_at, reinforcement_count, lifecycle_revision,
                       inactive_reason, last_access_reinforced_at
                FROM relations
                """
            )
        }
        assert len(relations) == 3
        normal = relations[_RELATION_HASHES["normal"]]
        assert normal["retention_strength"] == 1.0
        assert normal["retention_anchor_at"] is not None
        assert normal["next_lifecycle_at"] is not None
        assert normal["reinforcement_count"] == 0
        assert normal["lifecycle_revision"] == 0
        assert normal["last_access_reinforced_at"] == 7.0
        permanent = relations[_RELATION_HASHES["permanent"]]
        assert permanent["is_permanent"] == 1
        assert permanent["is_pinned"] == 1
        assert permanent["next_lifecycle_at"] is None
        inactive = relations[_RELATION_HASHES["inactive"]]
        assert inactive["inactive_reason"] == "schema_19_migrated_inactive"

        deleted_relation = dict(
            migrated.query(
                """
                SELECT hash, retention_strength, retention_anchor_at,
                       reinforcement_count, lifecycle_revision, inactive_reason
                FROM deleted_relations
                WHERE hash = ?
                """,
                (_RELATION_HASHES["deleted"],),
            )[0]
        )
        assert deleted_relation == {
            "hash": _RELATION_HASHES["deleted"],
            "retention_strength": 1.0,
            "retention_anchor_at": deleted_relation["retention_anchor_at"],
            "reinforcement_count": 0,
            "lifecycle_revision": 0,
            "inactive_reason": "deleted",
        }
        assert deleted_relation["retention_anchor_at"] is not None

        external_refs = [
            dict(row)
            for row in migrated.query(
                "SELECT external_id, paragraph_hash, metadata_json FROM external_memory_refs"
            )
        ]
        assert external_refs == [
            {
                "external_id": "external-valid",
                "paragraph_hash": _PARAGRAPH_HASHES["live"],
                "metadata_json": '{"kept": true}',
            }
        ]
        external_foreign_keys = [
            dict(row) for row in migrated.query("PRAGMA foreign_key_list(external_memory_refs)")
        ]
        assert any(
            row["table"] == "paragraphs"
            and row["from"] == "paragraph_hash"
            and row["to"] == "hash"
            and str(row["on_delete"]).upper() == "CASCADE"
            for row in external_foreign_keys
        )
        assert migrated.query("PRAGMA foreign_key_check") == []
        with migrated.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO paragraphs (
                    hash, content, created_at, source, knowledge_type,
                    is_permanent, is_deleted
                ) VALUES (?, '级联删除探针', 50.0, 'source-cascade-probe', 'factual', 0, 0)
                """,
                ("0" * 64,),
            )
            connection.execute(
                """
                INSERT INTO external_memory_refs (
                    external_id, paragraph_hash, source_type, created_at, metadata_json
                ) VALUES ('external-cascade-probe', ?, 'test', 50.0, '{}')
                """,
                ("0" * 64,),
            )
            connection.execute("DELETE FROM paragraphs WHERE hash = ?", ("0" * 64,))
            remaining_probe = connection.execute(
                "SELECT COUNT(*) FROM external_memory_refs WHERE external_id = 'external-cascade-probe'"
            ).fetchone()[0]
            assert remaining_probe == 0

        table_names = {
            str(row["name"])
            for row in migrated.query("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "episode_pending_paragraphs" not in table_names
        assert {
            "storage_cleanup_jobs",
            "fact_claims",
            "fact_evidence",
            "fact_transitions",
            "relation_graph_projection_jobs",
        } <= table_names
        projection_jobs = {
            str(row["relation_hash"]): dict(row)
            for row in migrated.query(
                """
                SELECT relation_hash, desired_active, desired_lifecycle_revision,
                       job_revision, status
                FROM relation_graph_projection_jobs
                """
            )
        }
        assert set(projection_jobs) == {
            _RELATION_HASHES["normal"],
            _RELATION_HASHES["permanent"],
            _RELATION_HASHES["inactive"],
        }
        assert projection_jobs[_RELATION_HASHES["normal"]]["desired_active"] == 1
        assert projection_jobs[_RELATION_HASHES["permanent"]]["desired_active"] == 1
        assert projection_jobs[_RELATION_HASHES["inactive"]]["desired_active"] == 0
        assert all(item["job_revision"] == 1 for item in projection_jobs.values())
        assert all(item["status"] == "pending" for item in projection_jobs.values())
        assert "fact_claim_ids_json" in _column_names(migrated, "person_profile_snapshots")
        assert "input_fingerprint" in _column_names(migrated, "episodes")
        legacy_episode = dict(
            migrated.query(
                """
                SELECT episode_id, source, title, summary, event_time_start,
                       event_time_end, input_fingerprint
                FROM episodes
                WHERE episode_id = 'episode-legacy'
                """
            )[0]
        )
        assert legacy_episode == {
            "episode_id": "episode-legacy",
            "source": "source-episode-only",
            "title": "旧 Episode",
            "summary": "旧 Episode 摘要",
            "event_time_start": 40.0,
            "event_time_end": 41.0,
            "input_fingerprint": None,
        }

        source_columns = _column_names(migrated, "episode_rebuild_sources")
        assert {
            "desired_revision",
            "built_revision",
            "claimed_revision",
            "dirty_start",
            "dirty_end",
            "first_requested_at",
            "ready_at",
            "lease_token",
            "lease_until",
            "next_attempt_at",
            "built_generation_hash",
            "claimed_generation_hash",
            "retry_revision",
            "retry_generation_hash",
        } <= source_columns
        sources = {
            str(row["source"]): dict(row)
            for row in migrated.query(
                """
                SELECT source, status, retry_count, reason, desired_revision,
                       built_revision, claimed_revision, first_requested_at,
                       ready_at, next_attempt_at, lease_token, lease_until,
                       retry_revision, retry_generation_hash
                FROM episode_rebuild_sources
                """
            )
        }
        assert sources["source-done"]["status"] == "done"
        assert sources["source-done"]["desired_revision"] == 1
        assert sources["source-done"]["built_revision"] == 1
        assert sources["source-running"]["status"] == "pending"
        assert sources["source-running"]["retry_count"] == 2
        assert sources["source-running"]["built_revision"] == 0
        assert sources["source-running"]["lease_token"] is None
        assert sources["source-running"]["lease_until"] is None
        assert sources["source-pending-overlap"]["status"] == "pending"
        assert sources["source-pending-overlap"]["desired_revision"] == 2
        assert sources["source-pending-overlap"]["built_revision"] == 1
        assert sources["source-pending-overlap"]["reason"] == "schema_19_pending_migration"
        assert sources["source-from-pending"]["reason"] == "schema_19_pending_migration"
        assert sources["source-from-pending"]["desired_revision"] == 1
        assert sources["source-from-pending"]["built_revision"] == 0
        assert sources["source-live"]["reason"] == "schema_19_source_discovery"
        assert sources["source-permanent"]["reason"] == "schema_19_source_discovery"
        assert sources["source-episode-only"]["reason"] == "schema_19_source_discovery"
        assert "source-deleted-only" not in sources
        for source in sources.values():
            assert source["first_requested_at"] is not None
            assert source["ready_at"] is not None
            assert source["next_attempt_at"] is not None
            assert source["claimed_revision"] is None
            assert source["retry_revision"] is None
            assert source["retry_generation_hash"] is None
    finally:
        migrated.close()

    reopened = MetadataStore(data_dir=tmp_path)
    reopened.connect()
    try:
        assert reopened.get_schema_version() == 21
        assert len(reopened.query("SELECT hash FROM paragraphs")) == 4
        assert len(reopened.query("SELECT hash FROM relations")) == 3
        assert len(reopened.query("SELECT source FROM episode_rebuild_sources")) == 7
    finally:
        reopened.close()


def test_schema16_migration_repairs_known_orphans_and_creates_backup(tmp_path: Path) -> None:
    bootstrap = MetadataStore(data_dir=tmp_path)
    bootstrap.connect()
    database_path = bootstrap.get_db_path()
    bootstrap.close()
    _materialize_schema16_database(database_path)
    _inject_known_orphaned_associations(database_path)

    migrated = MetadataStore(data_dir=tmp_path)
    migrated.connect()
    try:
        assert migrated.get_schema_version() == SCHEMA_VERSION == 21
        assert migrated.query("PRAGMA foreign_key_check") == []
        assert migrated.query(
            "SELECT 1 FROM paragraph_relations WHERE paragraph_hash = ?",
            ("f" * 64,),
        ) == []
        assert migrated.query(
            "SELECT 1 FROM paragraph_entities WHERE entity_hash = ?",
            ("e" * 64,),
        ) == []
        assert migrated.query(
            "SELECT 1 FROM episode_paragraphs WHERE paragraph_hash = ?",
            ("f" * 64,),
        ) == []
        assert migrated.query(
            "SELECT 1 FROM memory_feedback_action_logs WHERE task_id = 999"
        ) == []
        assert migrated.query(
            "SELECT 1 FROM delete_operation_items WHERE operation_id = 'missing-operation'"
        ) == []
        stale_mark = dict(
            migrated.query(
                """
                SELECT task_id, reason
                FROM paragraph_stale_relation_marks
                WHERE paragraph_hash = ? AND relation_hash = ?
                """,
                (_PARAGRAPH_HASHES["permanent"], _RELATION_HASHES["permanent"]),
            )[0]
        )
        assert stale_mark == {"task_id": None, "reason": "orphan-task"}

        backup_path = tmp_path / "metadata.db.pre-schema16-to-21-repair.bak"
        assert backup_path.is_file()
        with sqlite3.connect(backup_path) as backup_connection:
            assert backup_connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0] == 16
            assert len(backup_connection.execute("PRAGMA foreign_key_check").fetchall()) == 6
    finally:
        migrated.close()


def test_schema16_migration_preserves_unknown_foreign_key_violation(tmp_path: Path) -> None:
    bootstrap = MetadataStore(data_dir=tmp_path)
    bootstrap.connect()
    database_path = bootstrap.get_db_path()
    bootstrap.close()
    _materialize_schema16_database(database_path)
    _inject_unknown_foreign_key_violation(database_path)

    migrated = MetadataStore(data_dir=tmp_path)
    with pytest.raises(RuntimeError, match=r"unknown_child->unknown_parent"):
        migrated.connect()
    migrated.close()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM unknown_child").fetchone()[0] == 1
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 16
    assert (tmp_path / "metadata.db.pre-schema16-to-21-repair.bak").is_file()


def test_schema16_migration_matches_fresh_schema21_structure(tmp_path: Path) -> None:
    fresh = MetadataStore(data_dir=tmp_path / "fresh")
    fresh.connect()

    legacy_bootstrap = MetadataStore(data_dir=tmp_path / "migrated")
    legacy_bootstrap.connect()
    legacy_database_path = legacy_bootstrap.get_db_path()
    legacy_bootstrap.close()
    _materialize_schema16_database(legacy_database_path)

    migrated = MetadataStore(data_dir=tmp_path / "migrated")
    migrated.connect()
    try:
        for table in _SCHEMA21_FINGERPRINT_TABLES:
            assert _table_structure_fingerprint(migrated, table) == _table_structure_fingerprint(
                fresh,
                table,
            ), f"schema 16 迁移后的 {table} 结构与全新 schema 21 不一致"
        projection_trigger_rows = migrated.query(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'trg_relations_graph_projection_lifecycle'
            """
        )
        fresh_projection_trigger_rows = fresh.query(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'trg_relations_graph_projection_lifecycle'
            """
        )
        assert len(projection_trigger_rows) == len(fresh_projection_trigger_rows) == 1
        projection_trigger_sql = "".join(str(projection_trigger_rows[0]["sql"] or "").split())
        fresh_projection_trigger_sql = "".join(
            str(fresh_projection_trigger_rows[0]["sql"] or "").split()
        )
        assert projection_trigger_sql
        assert projection_trigger_sql == fresh_projection_trigger_sql
    finally:
        migrated.close()
        fresh.close()


def test_relation_rebuild_preserves_children_and_cascade_contract(tmp_path: Path) -> None:
    bootstrap = MetadataStore(data_dir=tmp_path)
    bootstrap.connect()
    database_path = bootstrap.get_db_path()
    bootstrap.close()
    _materialize_schema16_database(database_path)

    migrated = MetadataStore(data_dir=tmp_path)
    migrated.connect()
    try:
        foreign_keys_row = dict(migrated.query("PRAGMA foreign_keys")[0])
        assert list(foreign_keys_row.values()) == [1]
        paragraph_relation = dict(
            migrated.query(
                """
                SELECT paragraph_hash, relation_hash
                FROM paragraph_relations
                WHERE paragraph_hash = ? AND relation_hash = ?
                """,
                (_PARAGRAPH_HASHES["live"], _RELATION_HASHES["normal"]),
            )[0]
        )
        assert paragraph_relation == {
            "paragraph_hash": _PARAGRAPH_HASHES["live"],
            "relation_hash": _RELATION_HASHES["normal"],
        }
        stale_mark = dict(
            migrated.query(
                """
                SELECT paragraph_hash, relation_hash, query_tool_id, reason,
                       source_type, source_id, source_operation_id,
                       created_at, updated_at
                FROM paragraph_stale_relation_marks
                WHERE paragraph_hash = ? AND relation_hash = ?
                """,
                (_PARAGRAPH_HASHES["live"], _RELATION_HASHES["normal"]),
            )[0]
        )
        assert stale_mark == {
            "paragraph_hash": _PARAGRAPH_HASHES["live"],
            "relation_hash": _RELATION_HASHES["normal"],
            "query_tool_id": "legacy-query",
            "reason": "legacy-stale",
            "source_type": "feedback",
            "source_id": "legacy-source",
            "source_operation_id": "legacy-operation",
            "created_at": 24.0,
            "updated_at": 25.0,
        }

        relation_triggers = [
            dict(row)
            for row in migrated.query(
                """
                SELECT name, tbl_name, sql
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND name IN (
                      'trg_schema16_relations_insert_audit',
                      'trg_schema16_relations_update_audit'
                  )
                ORDER BY name
                """
            )
        ]
        assert [trigger["name"] for trigger in relation_triggers] == [
            "trg_schema16_relations_insert_audit",
            "trg_schema16_relations_update_audit",
        ]
        assert all(trigger["tbl_name"] == "relations" for trigger in relation_triggers)
        assert all(str(trigger["sql"] or "").strip() for trigger in relation_triggers)

        with migrated.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM schema16_relation_trigger_audit")
            connection.execute(
                """
                INSERT INTO relations (
                    hash, subject, predicate, object, confidence, created_at,
                    retention_strength, retention_anchor_at, next_lifecycle_at
                ) VALUES (?, '触发器用户', '验证', '触发器恢复', 1.0, 60.0, 1.0, 60.0, 120.0)
                """,
                (_RELATION_HASHES["trigger_probe"],),
            )
            connection.execute(
                "UPDATE relations SET confidence = 0.75 WHERE hash = ?",
                (_RELATION_HASHES["trigger_probe"],),
            )
        trigger_events = [
            dict(row)
            for row in migrated.query(
                """
                SELECT event_type, relation_hash, confidence, recorded_subject
                FROM schema16_relation_trigger_audit
                ORDER BY event_id
                """
            )
        ]
        assert trigger_events == [
            {
                "event_type": "insert",
                "relation_hash": _RELATION_HASHES["trigger_probe"],
                "confidence": 1.0,
                "recorded_subject": "触发器用户",
            },
            {
                "event_type": "update",
                "relation_hash": _RELATION_HASHES["trigger_probe"],
                "confidence": 0.75,
                "recorded_subject": "触发器用户",
            },
        ]

        with migrated.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE relations
                SET is_inactive = 1, lifecycle_revision = lifecycle_revision + 1
                WHERE hash = ?
                """,
                (_RELATION_HASHES["trigger_probe"],),
            )
        projection_job = dict(
            migrated.query(
                """
                SELECT relation_hash, desired_active, desired_lifecycle_revision,
                       job_revision, status
                FROM relation_graph_projection_jobs
                WHERE relation_hash = ?
                """,
                (_RELATION_HASHES["trigger_probe"],),
            )[0]
        )
        assert projection_job == {
            "relation_hash": _RELATION_HASHES["trigger_probe"],
            "desired_active": 0,
            "desired_lifecycle_revision": 1,
            "job_revision": 1,
            "status": "pending",
        }

        for child_table in ("paragraph_relations", "paragraph_stale_relation_marks"):
            child_foreign_keys = [
                dict(row) for row in migrated.query(f"PRAGMA foreign_key_list({child_table})")
            ]
            assert any(
                row["table"] == "relations"
                and row["from"] == "relation_hash"
                and row["to"] == "hash"
                and str(row["on_delete"]).upper() == "CASCADE"
                for row in child_foreign_keys
            )

        with migrated.transaction(immediate=True) as connection:
            connection.execute(
                "DELETE FROM relations WHERE hash = ?",
                (_RELATION_HASHES["normal"],),
            )
        assert migrated.query(
            "SELECT 1 FROM paragraph_relations WHERE relation_hash = ?",
            (_RELATION_HASHES["normal"],),
        ) == []
        assert migrated.query(
            "SELECT 1 FROM paragraph_stale_relation_marks WHERE relation_hash = ?",
            (_RELATION_HASHES["normal"],),
        ) == []
        assert migrated.query("PRAGMA foreign_key_check") == []
    finally:
        migrated.close()
