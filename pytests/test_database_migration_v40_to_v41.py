from sqlalchemy import create_engine

from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v40_to_v41 import migrate_v40_to_v41


def test_message_routes_backfill_only_from_matching_session():
    with create_engine("sqlite://").begin() as connection:
        connection.exec_driver_sql("CREATE TABLE mai_messages (session_id TEXT)")
        connection.exec_driver_sql("CREATE TABLE chat_sessions (session_id TEXT, account_id TEXT, scope TEXT)")
        connection.exec_driver_sql("INSERT INTO mai_messages VALUES ('known'), ('orphan')")
        connection.exec_driver_sql("INSERT INTO chat_sessions VALUES ('known', 'bot-1', 'connection-1')")
        context = MigrationExecutionContext(
            connection=connection, current_version=40, target_version=41,
            step_index=1, step_name="v40_to_v41", total_steps=1,
        )
        migrate_v40_to_v41(context)
        migrate_v40_to_v41(context)
        assert connection.exec_driver_sql(
            "SELECT account_id, scope FROM mai_messages ORDER BY session_id"
        ).fetchall() == [("bot-1", "connection-1"), (None, None)]
