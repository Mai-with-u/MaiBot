"""v40 schema 升级到 v41：新增 Workspace 子系统与逻辑记忆空间基础表。"""

from src.common.logger import get_logger

from .models import MigrationExecutionContext

logger = get_logger("database_migration")


_TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS memory_spaces (
        id VARCHAR(64) NOT NULL PRIMARY KEY,
        name VARCHAR(100) NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        space_type VARCHAR(32) NOT NULL DEFAULT 'private',
        enabled BOOLEAN NOT NULL DEFAULT 1,
        policy_revision INTEGER NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS persona_profiles (
        id VARCHAR(64) NOT NULL PRIMARY KEY,
        name VARCHAR(100) NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        nickname VARCHAR(100) NOT NULL DEFAULT '',
        alias_names_json TEXT NOT NULL DEFAULT '[]',
        personality TEXT NOT NULL DEFAULT '',
        behavior_style TEXT NOT NULL DEFAULT '',
        reply_style TEXT NOT NULL DEFAULT '',
        group_chat_prompt TEXT NOT NULL DEFAULT '',
        private_chat_prompt TEXT NOT NULL DEFAULT '',
        multiple_reply_style TEXT NOT NULL DEFAULT '',
        emotion_trait TEXT NOT NULL DEFAULT '',
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspaces (
        id VARCHAR(64) NOT NULL PRIMARY KEY,
        name VARCHAR(100) NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        memory_space_id VARCHAR(64) NOT NULL,
        persona_profile_id VARCHAR(64),
        is_default BOOLEAN NOT NULL DEFAULT 0,
        enabled BOOLEAN NOT NULL DEFAULT 1,
        inherit_global_tools BOOLEAN NOT NULL DEFAULT 1,
        inherit_global_plugins BOOLEAN NOT NULL DEFAULT 1,
        policy_revision INTEGER NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(memory_space_id) REFERENCES memory_spaces (id),
        FOREIGN KEY(persona_profile_id) REFERENCES persona_profiles (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspace_memberships (
        id INTEGER NOT NULL PRIMARY KEY,
        workspace_id VARCHAR(64) NOT NULL,
        session_id VARCHAR(255) NOT NULL,
        assigned_by VARCHAR(32) NOT NULL DEFAULT 'manual',
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        CONSTRAINT uq_workspace_memberships_session UNIQUE (session_id),
        FOREIGN KEY(workspace_id) REFERENCES workspaces (id),
        FOREIGN KEY(session_id) REFERENCES chat_sessions (session_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspace_selectors (
        id INTEGER NOT NULL PRIMARY KEY,
        workspace_id VARCHAR(64) NOT NULL,
        platform VARCHAR(100) NOT NULL DEFAULT '',
        account_id VARCHAR(255) NOT NULL DEFAULT '',
        chat_type VARCHAR(32) NOT NULL DEFAULT 'any',
        target_id VARCHAR(255) NOT NULL DEFAULT '',
        priority INTEGER NOT NULL DEFAULT 0,
        enabled BOOLEAN NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspace_tool_policies (
        id INTEGER NOT NULL PRIMARY KEY,
        workspace_id VARCHAR(64) NOT NULL,
        tool_name VARCHAR(255) NOT NULL,
        effect VARCHAR(16) NOT NULL DEFAULT 'deny',
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        CONSTRAINT uq_workspace_tool_policy UNIQUE (workspace_id, tool_name),
        FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspace_plugin_policies (
        id INTEGER NOT NULL PRIMARY KEY,
        workspace_id VARCHAR(64) NOT NULL,
        plugin_id VARCHAR(255) NOT NULL,
        effect VARCHAR(16) NOT NULL DEFAULT 'inherit',
        overrides_json TEXT NOT NULL DEFAULT '{}',
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        CONSTRAINT uq_workspace_plugin_policy UNIQUE (workspace_id, plugin_id),
        FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_space_acl (
        id INTEGER NOT NULL PRIMARY KEY,
        owner_space_id VARCHAR(64) NOT NULL,
        peer_space_id VARCHAR(64) NOT NULL,
        can_read_from_peer BOOLEAN NOT NULL DEFAULT 0,
        expose_to_peer BOOLEAN NOT NULL DEFAULT 0,
        can_import_from_peer BOOLEAN NOT NULL DEFAULT 0,
        can_publish_to_peer BOOLEAN NOT NULL DEFAULT 0,
        filters_json TEXT NOT NULL DEFAULT '{}',
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        CONSTRAINT uq_memory_space_acl_pair UNIQUE (owner_space_id, peer_space_id),
        FOREIGN KEY(owner_space_id) REFERENCES memory_spaces (id) ON DELETE CASCADE,
        FOREIGN KEY(peer_space_id) REFERENCES memory_spaces (id) ON DELETE CASCADE,
        CHECK (owner_space_id <> peer_space_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_transfer_jobs (
        id VARCHAR(64) NOT NULL PRIMARY KEY,
        source_space_id VARCHAR(64) NOT NULL,
        target_space_id VARCHAR(64) NOT NULL,
        mode VARCHAR(16) NOT NULL,
        filters_json TEXT NOT NULL DEFAULT '{}',
        approval_policy VARCHAR(16) NOT NULL DEFAULT 'manual',
        conflict_policy VARCHAR(16) NOT NULL DEFAULT 'skip',
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        created_by VARCHAR(64) NOT NULL DEFAULT 'webui',
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(source_space_id) REFERENCES memory_spaces (id),
        FOREIGN KEY(target_space_id) REFERENCES memory_spaces (id),
        CHECK (source_space_id <> target_space_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspace_audit_logs (
        id INTEGER NOT NULL PRIMARY KEY,
        workspace_id VARCHAR(64),
        action VARCHAR(100) NOT NULL,
        actor VARCHAR(64) NOT NULL DEFAULT 'system',
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at DATETIME NOT NULL,
        FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE SET NULL
    )
    """,
)

_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS ix_memory_spaces_space_type ON memory_spaces (space_type)",
    "CREATE INDEX IF NOT EXISTS ix_memory_spaces_enabled ON memory_spaces (enabled)",
    "CREATE INDEX IF NOT EXISTS ix_memory_spaces_updated_at ON memory_spaces (updated_at)",
    "CREATE INDEX IF NOT EXISTS ix_persona_profiles_updated_at ON persona_profiles (updated_at)",
    "CREATE INDEX IF NOT EXISTS ix_workspaces_memory_space_id ON workspaces (memory_space_id)",
    "CREATE INDEX IF NOT EXISTS ix_workspaces_persona_profile_id ON workspaces (persona_profile_id)",
    "CREATE INDEX IF NOT EXISTS ix_workspaces_is_default ON workspaces (is_default)",
    "CREATE INDEX IF NOT EXISTS ix_workspaces_enabled ON workspaces (enabled)",
    "CREATE INDEX IF NOT EXISTS ix_workspace_memberships_workspace_id ON workspace_memberships (workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_workspace_memberships_session_id ON workspace_memberships (session_id)",
    "CREATE INDEX IF NOT EXISTS ix_workspace_selectors_workspace_id ON workspace_selectors (workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_workspace_selectors_match ON workspace_selectors (platform, account_id, chat_type, target_id, priority)",
    "CREATE INDEX IF NOT EXISTS ix_workspace_tool_policies_workspace_id ON workspace_tool_policies (workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_workspace_tool_policies_tool_name ON workspace_tool_policies (tool_name)",
    "CREATE INDEX IF NOT EXISTS ix_workspace_plugin_policies_workspace_id ON workspace_plugin_policies (workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_workspace_plugin_policies_plugin_id ON workspace_plugin_policies (plugin_id)",
    "CREATE INDEX IF NOT EXISTS ix_memory_space_acl_owner_space_id ON memory_space_acl (owner_space_id)",
    "CREATE INDEX IF NOT EXISTS ix_memory_space_acl_peer_space_id ON memory_space_acl (peer_space_id)",
    "CREATE INDEX IF NOT EXISTS ix_memory_transfer_jobs_source_space_id ON memory_transfer_jobs (source_space_id)",
    "CREATE INDEX IF NOT EXISTS ix_memory_transfer_jobs_target_space_id ON memory_transfer_jobs (target_space_id)",
    "CREATE INDEX IF NOT EXISTS ix_memory_transfer_jobs_status ON memory_transfer_jobs (status)",
    "CREATE INDEX IF NOT EXISTS ix_workspace_audit_logs_workspace_id ON workspace_audit_logs (workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_workspace_audit_logs_created_at ON workspace_audit_logs (created_at)",
)


def migrate_v40_to_v41(context: MigrationExecutionContext) -> None:
    """创建 Workspace 基础表，并建立兼容现有行为的默认工作区和公共记忆空间。"""

    total = len(_TABLE_STATEMENTS) + len(_INDEX_STATEMENTS) + 2
    context.start_progress(
        total_tables=len(_TABLE_STATEMENTS),
        total_records=total,
        description="v40 -> v41 迁移进度",
        table_unit_name="表",
        record_unit_name="项目",
    )
    connection = context.connection
    for index, statement in enumerate(_TABLE_STATEMENTS, start=1):
        connection.exec_driver_sql(statement)
        context.advance_progress(records=1, completed_tables=1, item_name=f"workspace_table_{index}")
    for statement in _INDEX_STATEMENTS:
        connection.exec_driver_sql(statement)
        context.advance_progress(records=1)

    connection.exec_driver_sql(
        """
        INSERT OR IGNORE INTO memory_spaces (
            id, name, description, space_type, enabled, policy_revision, created_at, updated_at
        ) VALUES (
            'memory-space-public', '公共记忆库', '兼容现有 MaiBot 记忆行为的默认公共空间',
            'public', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """
    )
    context.advance_progress(records=1)
    connection.exec_driver_sql(
        """
        INSERT OR IGNORE INTO workspaces (
            id, name, description, memory_space_id, persona_profile_id, is_default, enabled,
            inherit_global_tools, inherit_global_plugins, policy_revision, created_at, updated_at
        ) VALUES (
            'workspace-default', '默认子系统', '所有未显式分配聊天的兼容工作区',
            'memory-space-public', NULL, 1, 1, 1, 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """
    )
    context.advance_progress(records=1)
    logger.info("v40 -> v41 数据库迁移完成：Workspace 与逻辑记忆空间基础层已创建")
