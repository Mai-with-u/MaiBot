from .models import MigrationExecutionContext


def migrate_v56_to_v57(context: MigrationExecutionContext) -> None:
    """旧版未填写释义的手动词条和未得到结论的 AI 词条都可能保留 False。"""
    context.connection.exec_driver_sql(
        """UPDATE jargons SET is_jargon = NULL
           WHERE is_jargon = 0 AND is_complete = 0
             AND trim(coalesce(meaning, '')) = ''"""
    )
