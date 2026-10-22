"""v43 到 v44：新增按任务、模型和提供商聚合的调用统计。"""

from .models import MigrationExecutionContext


def migrate_v43_to_v44(context: MigrationExecutionContext) -> None:
    """汇总表独立于计费用量，不向历史成功记录推测失败率。"""
    context.connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS model_task_usage (
            bucket_time DATETIME NOT NULL,
            task_name VARCHAR(100) NOT NULL,
            model_name VARCHAR(255) NOT NULL,
            model_identifier VARCHAR(255) NOT NULL,
            provider_name VARCHAR(255) NOT NULL,
            request_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            success_duration FLOAT NOT NULL DEFAULT 0,
            PRIMARY KEY (bucket_time, task_name, model_name, model_identifier, provider_name)
        )
        """
    )
    context.connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_model_task_usage_bucket_time ON model_task_usage (bucket_time)"
    )
