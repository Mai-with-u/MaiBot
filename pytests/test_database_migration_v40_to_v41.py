from sqlalchemy import create_engine

from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v40_to_v41 import migrate_v40_to_v41


def test_v40_to_v41_creates_workspace_foundation_and_compatible_defaults() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE chat_sessions (session_id VARCHAR(255) PRIMARY KEY)")
        context = MigrationExecutionContext(
            connection=connection,
            current_version=40,
            target_version=41,
            step_index=1,
            step_name="v40_to_v41",
            total_steps=1,
        )

        migrate_v40_to_v41(context)

        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "memory_spaces",
            "persona_profiles",
            "workspaces",
            "workspace_memberships",
            "workspace_selectors",
            "workspace_tool_policies",
            "workspace_plugin_policies",
            "memory_space_acl",
            "memory_transfer_jobs",
            "workspace_audit_logs",
        }.issubset(tables)

        public_space = connection.exec_driver_sql(
            "SELECT id, space_type FROM memory_spaces WHERE id='memory-space-public'"
        ).one()
        default_workspace = connection.exec_driver_sql(
            "SELECT id, memory_space_id, is_default FROM workspaces WHERE id='workspace-default'"
        ).one()
        assert public_space == ("memory-space-public", "public")
        assert default_workspace == ("workspace-default", "memory-space-public", 1)

        # 迁移必须幂等，重复执行不能生成重复默认记录。
        migrate_v40_to_v41(context)
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM memory_spaces WHERE id='memory-space-public'"
        ).scalar_one() == 1
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM workspaces WHERE id='workspace-default'"
        ).scalar_one() == 1
