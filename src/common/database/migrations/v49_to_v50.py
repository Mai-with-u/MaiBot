"""在线聊天流迁移的持久化状态和幂等来源账本。"""

from src.common.database.chat_migration_models import copies, jobs

from .models import MigrationExecutionContext


def migrate_v49_to_v50(context: MigrationExecutionContext) -> None:
    for table in (jobs, copies):
        table.create(context.connection, checkfirst=True)
