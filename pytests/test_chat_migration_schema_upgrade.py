"""已有 v50 账本必须升级，create_all 不会给已有表添加列。"""

from sqlalchemy import create_engine, inspect, select

import pytest

from src.common.database.chat_migration_models import copies
from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v50_to_v51 import migrate_v50_to_v51
from src.common.database.migrations.v51_to_v52 import migrate_v51_to_v52


@pytest.mark.parametrize("existing_signature", [False, True])
def test_upgrade_existing_ledger_preserves_rows_and_is_idempotent(tmp_path, existing_signature):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as conn:
        signature_column = ", signature VARCHAR NOT NULL DEFAULT ''" if existing_signature else ""
        conn.exec_driver_sql(
            "CREATE TABLE chat_migration_copies (id INTEGER PRIMARY KEY, table_name VARCHAR NOT NULL, "
            "origin VARCHAR NOT NULL, target VARCHAR NOT NULL, target_key TEXT NOT NULL, "
            "amounts TEXT NOT NULL DEFAULT '{}'" + signature_column + ", UNIQUE(table_name,origin,target))"
        )
        conn.exec_driver_sql(
            "INSERT INTO chat_migration_copies(table_name,origin,target,target_key,amounts) "
            "VALUES ('expressions','source:1','target','2','{\"count\":3}')"
        )
        context = MigrationExecutionContext(
            connection=conn,
            current_version=50,
            target_version=51,
            step_index=1,
            step_name="v50_to_v51",
            total_steps=1,
        )
        migrate_v50_to_v51(context)
        migrate_v50_to_v51(context)
        assert "signature" in {column["name"] for column in inspect(conn).get_columns(copies.name)}
        migrate_v51_to_v52(context)
        migrate_v51_to_v52(context)
        row = conn.execute(select(copies)).mappings().one()
        assert "signature" not in {column["name"] for column in inspect(conn).get_columns(copies.name)}
        assert row["origin"] == "source:1"
        assert row["amounts"] == '{"count":3}'
        assert "ix_chat_migration_copies_table_key" in {
            index["name"] for index in inspect(conn).get_indexes(copies.name)
        }


def test_remove_signature_preserves_legacy_copies_and_normalizes_origin(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    copies.create(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE chat_migration_copies ADD COLUMN signature VARCHAR NOT NULL DEFAULT ''")
        for target, key, fingerprint in (("B", "2", "a" * 64), ("B", "3", "b" * 64), ("C", "4", "a" * 64)):
            conn.exec_driver_sql(
                "INSERT INTO chat_migration_copies(table_name,origin,target,target_key,signature) VALUES (?,?,?,?,?)",
                ("expressions", f"A:1:{fingerprint}", target, key, fingerprint),
            )
        context = MigrationExecutionContext(
            connection=conn,
            current_version=51,
            target_version=52,
            step_index=1,
            step_name="v51_to_v52",
            total_steps=1,
        )
        migrate_v51_to_v52(context)
        rows = conn.execute(select(copies).order_by(copies.c.id)).mappings().all()
        assert len(rows) == 3
        assert [row["target_key"] for row in rows] == ["2", "3", "4"]
        assert rows[0]["origin"] == rows[2]["origin"] == "A:1"
        assert "signature" not in {column["name"] for column in inspect(conn).get_columns(copies.name)}
