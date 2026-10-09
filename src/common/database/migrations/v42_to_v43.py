"""v42 到 v43：回收移除行为学习表后留下的数据库空闲页。"""

from src.common.logger import get_logger

from .models import MigrationExecutionContext

logger = get_logger("database_migration")


def migrate_v42_to_v43(context: MigrationExecutionContext) -> None:
    """在事务外压缩数据库，并将 WAL 内容回写以归还磁盘空间。"""
    context.start_progress(
        total_tables=1,
        total_records=0,
        description="v42 -> v43 数据库瘦身",
        table_unit_name="数据库",
        record_unit_name="记录",
    )
    connection = context.connection
    # VACUUM 不能在写事务中运行；前一步删表已独立提交。
    connection.commit()
    connection.exec_driver_sql("VACUUM")
    connection.commit()
    # WAL 模式下 VACUUM 的结果需回写主文件，才能实际缩小文件占用。
    checkpoint = connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").one()
    if checkpoint[0] != 0:
        raise RuntimeError("数据库瘦身后的 WAL 回写被占用，未完成空间回收")
    context.advance_progress(completed_tables=1, item_name="SQLite VACUUM")
    logger.info("v42 -> v43 数据库迁移完成：已压缩数据库并回收空闲页")
