"""v48 到 v49：新增黑话推断状态、后台任务和含义历史表。"""

from src.common.database.database_model import JargonInferenceJob, JargonInferenceState, JargonMeaningHistory

from .models import MigrationExecutionContext


def migrate_v48_to_v49(context: MigrationExecutionContext) -> None:
    """只创建新表，已有词条在首次处理时按需建立推断状态。"""
    for model in (JargonInferenceState, JargonInferenceJob, JargonMeaningHistory):
        model.__table__.create(context.connection, checkfirst=True)
