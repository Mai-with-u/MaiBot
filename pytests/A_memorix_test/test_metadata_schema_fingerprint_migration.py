from pathlib import Path

import sqlite3

from src.A_memorix.core.storage.metadata_store import MetadataStore, SCHEMA_VERSION


def test_runtime_migration_adds_episode_profile_and_retry_generation_columns(tmp_path: Path) -> None:
    initial = MetadataStore(data_dir=tmp_path)
    initial.connect()
    relation_hash = initial.add_relation("迁移用户", "验证", "投影基线")
    database_path = initial.get_db_path()
    initial.close()

    with sqlite3.connect(database_path) as connection:
        connection.execute("ALTER TABLE episodes DROP COLUMN input_fingerprint")
        connection.execute("ALTER TABLE person_profile_snapshots DROP COLUMN evidence_fingerprint")
        connection.execute("ALTER TABLE episode_rebuild_sources DROP COLUMN retry_revision")
        connection.execute("ALTER TABLE episode_rebuild_sources DROP COLUMN retry_generation_hash")
        connection.execute("DROP TRIGGER trg_relations_graph_projection_lifecycle")
        connection.execute("DROP TABLE relation_graph_projection_jobs")
        connection.execute("DELETE FROM schema_migrations WHERE version = ?", (SCHEMA_VERSION,))
        connection.execute(
            "INSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION - 1, 1.0),
        )

    migrated = MetadataStore(data_dir=tmp_path)
    migrated.connect()
    try:
        episode_columns = {
            row["name"] for row in migrated.query("PRAGMA table_info(episodes)")
        }
        profile_columns = {
            row["name"]
            for row in migrated.query("PRAGMA table_info(person_profile_snapshots)")
        }
        source_rebuild_columns = {
            row["name"]
            for row in migrated.query("PRAGMA table_info(episode_rebuild_sources)")
        }

        assert migrated.get_schema_version() == SCHEMA_VERSION == 21
        assert "input_fingerprint" in episode_columns
        assert "evidence_fingerprint" in profile_columns
        assert "retry_revision" in source_rebuild_columns
        assert "retry_generation_hash" in source_rebuild_columns
        projection_jobs = migrated.query(
            """
            SELECT relation_hash, desired_active, desired_lifecycle_revision,
                   job_revision, status
            FROM relation_graph_projection_jobs
            """
        )
        assert projection_jobs == [
            {
                "relation_hash": relation_hash,
                "desired_active": 1,
                "desired_lifecycle_revision": 0,
                "job_revision": 1,
                "status": "pending",
            }
        ]
    finally:
        migrated.close()
