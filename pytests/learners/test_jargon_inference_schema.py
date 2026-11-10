"""黑话推断附属表的升级兼容性。"""

from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import (
    Jargon,
    JargonCreatedBy,
    JargonInferenceJob,
    JargonInferenceState,
    JargonMeaningHistory,
)
from src.common.database.migrations.builtin import build_default_migration_registry
from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v48_to_v49 import migrate_v48_to_v49


def test_upgrade_preserves_existing_jargon_and_is_replayable() -> None:
    """验证迁移只新增附属表，重复执行不会改写原词条或已保存的推断记录。"""
    engine = create_engine("sqlite://")
    try:
        SQLModel.metadata.create_all(engine, tables=[Jargon.__table__])
        with Session(engine) as session:
            session.add(Jargon(id=1, content="起飞", meaning="人工解释", created_by=JargonCreatedBy.MANUAL, count=7))
            session.commit()

        with engine.begin() as connection:
            schema_query = "SELECT sql FROM sqlite_master WHERE type='table' AND name='jargons'"
            before_schema = connection.exec_driver_sql(schema_query).scalar_one()
            context = MigrationExecutionContext(connection, 48, 49, 1, "v48_to_v49", 1)
            migrate_v48_to_v49(context)
            assert connection.exec_driver_sql(schema_query).scalar_one() == before_schema

        with Session(engine) as session:
            session.add(JargonInferenceState(jargon_id=1, locked=True, status="protected"))
            session.add(JargonInferenceJob(id="job-1", items_json='[{"id":1,"status":"protected"}]'))
            session.add(JargonMeaningHistory(jargon_id=1, previous_meaning="旧含义", meaning="人工解释"))
            session.commit()

        with engine.begin() as connection:
            migrate_v48_to_v49(MigrationExecutionContext(connection, 48, 49, 1, "v48_to_v49", 1))
        with Session(engine) as session:
            jargon = session.get(Jargon, 1)
            assert jargon is not None
            assert (jargon.meaning, jargon.count, jargon.created_by) == ("人工解释", 7, JargonCreatedBy.MANUAL)
            state = session.get(JargonInferenceState, 1)
            assert state is not None and state.locked
            job = session.get(JargonInferenceJob, "job-1")
            assert job is not None and '"protected"' in job.items_json
            history = session.get(JargonMeaningHistory, 1)
            assert history is not None and history.previous_meaning == "旧含义"
    finally:
        engine.dispose()


def test_upgrade_is_registered_after_v48() -> None:
    step = build_default_migration_registry().get_step(48)
    assert step is not None
    assert step.version_to == 49
    assert step.handler is migrate_v48_to_v49
