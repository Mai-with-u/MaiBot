"""v45 到 v46：文件记录、聊天归属和文本解析状态。"""

from src.files.models import FileAsset, FileBinding, FileParse

from .models import MigrationExecutionContext


def migrate_v45_to_v46(context: MigrationExecutionContext) -> None:
    for model in (FileAsset, FileBinding, FileParse):
        model.__table__.create(context.connection, checkfirst=True)
