"""v46 到 v47：保存 WebUI 答疑会话和问答轮次。"""

from src.common.database.database_model import WebUIHelpSession, WebUIHelpTurn

from .models import MigrationExecutionContext


def migrate_v46_to_v47(context: MigrationExecutionContext) -> None:
    for model in (WebUIHelpSession, WebUIHelpTurn):
        model.__table__.create(context.connection, checkfirst=True)
