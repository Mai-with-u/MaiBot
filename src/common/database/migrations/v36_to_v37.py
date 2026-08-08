"""v36 schema 升级到 v37：新增 MaiSaka 回复效果汇总表。"""

from src.common.logger import get_logger

from .models import MigrationExecutionContext

logger = get_logger("database_migration")


def migrate_v36_to_v37(context: MigrationExecutionContext) -> None:
    context.start_progress(
        total_tables=1,
        total_records=1,
        description="v36 -> v37 迁移进度",
        table_unit_name="表",
        record_unit_name="表",
    )
    context.connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS maisaka_reply_effects (
            effect_id VARCHAR(36) PRIMARY KEY NOT NULL,
            session_id VARCHAR(255) NOT NULL,
            session_name VARCHAR(255) NOT NULL DEFAULT '',
            chat_type VARCHAR(20) NOT NULL DEFAULT 'group',
            status VARCHAR(30) NOT NULL,
            created_at DATETIME NOT NULL,
            finalized_at DATETIME,
            strategy_primary VARCHAR(40) NOT NULL DEFAULT 'other',
            model_name VARCHAR(255) NOT NULL DEFAULT '',
            prompt_fingerprint VARCHAR(64) NOT NULL DEFAULT '',
            scorer_version INTEGER NOT NULL DEFAULT 2,
            response_score FLOAT,
            reception_score FLOAT,
            conversation_score FLOAT,
            raw_score FLOAT,
            relative_score FLOAT,
            confidence FLOAT NOT NULL DEFAULT 0,
            record_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    indexes = (
        "CREATE INDEX IF NOT EXISTS ix_maisaka_reply_effects_session_id ON maisaka_reply_effects (session_id)",
        "CREATE INDEX IF NOT EXISTS ix_maisaka_reply_effects_status ON maisaka_reply_effects (status)",
        "CREATE INDEX IF NOT EXISTS ix_maisaka_reply_effects_created_at ON maisaka_reply_effects (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_maisaka_reply_effects_finalized_at ON maisaka_reply_effects (finalized_at)",
        "CREATE INDEX IF NOT EXISTS ix_reply_effect_session_finalized ON maisaka_reply_effects (session_id, finalized_at)",
        "CREATE INDEX IF NOT EXISTS ix_reply_effect_strategy_finalized ON maisaka_reply_effects (strategy_primary, finalized_at)",
        "CREATE INDEX IF NOT EXISTS ix_reply_effect_model_prompt ON maisaka_reply_effects (model_name, prompt_fingerprint)",
    )
    for statement in indexes:
        context.connection.exec_driver_sql(statement)
    context.advance_progress(records=1, completed_tables=1, item_name="maisaka_reply_effects")
    logger.info("v36 -> v37 数据库迁移完成：MaiSaka 回复效果汇总表已就绪")
