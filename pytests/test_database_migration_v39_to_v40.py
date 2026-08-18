from sqlalchemy import create_engine

from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v39_to_v40 import migrate_v39_to_v40


def test_v39_to_v40_creates_empty_bot_account_table_without_backfill() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE chat_sessions (id INTEGER PRIMARY KEY, platform VARCHAR(100), account_id VARCHAR(255))"
        )
        connection.exec_driver_sql("INSERT INTO chat_sessions (platform, account_id) VALUES ('qq', 'historical')")
        context = MigrationExecutionContext(
            connection=connection,
            current_version=39,
            target_version=40,
            step_index=1,
            step_name="v39_to_v40",
            total_steps=1,
        )

        migrate_v39_to_v40(context)

        count = connection.exec_driver_sql("SELECT COUNT(*) FROM bot_platform_accounts").scalar_one()
        indexes = {
            row[1] for row in connection.exec_driver_sql("PRAGMA index_list('bot_platform_accounts')").fetchall()
        }
        assert count == 0
        assert "sqlite_autoindex_bot_platform_accounts_1" in indexes
