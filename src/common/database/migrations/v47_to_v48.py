"""v47 到 v48：新增按全部统计维度汇总的小时表。

迁移只建表，不回填历史：上百万条调用记录的回填由统计任务按 id 分批完成，
每批与游标在同一事务内提交，崩溃后从游标继续，出错时下一轮重试；
回填追上之前统计继续读取明细，结果不受影响。
"""

from src.common.database.database_model import StatisticsUsageHourly

from .models import MigrationExecutionContext


def migrate_v47_to_v48(context: MigrationExecutionContext) -> None:
    StatisticsUsageHourly.__table__.create(context.connection, checkfirst=True)
