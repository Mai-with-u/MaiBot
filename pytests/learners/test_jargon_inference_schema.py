"""黑话推断附属表的升级兼容性。"""

from sqlalchemy import inspect
from sqlmodel import Session, SQLModel, create_engine

import pytest

from src.common.database.database_model import (
    Jargon,
    JargonCreatedBy,
    JargonInferenceJob,
    JargonInferenceState,
    JargonMeaningHistory,
)
from src.common.database.migrations.bootstrap import create_database_migration_bootstrapper
from src.common.database.migrations.builtin import (
    build_default_migration_registry,
    build_default_schema_version_resolver,
)
from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v52_to_v53 import migrate_v52_to_v53


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
            context = MigrationExecutionContext(connection, 52, 53, 1, "v52_to_v53", 1)
            migrate_v52_to_v53(context)
            assert connection.exec_driver_sql(schema_query).scalar_one() == before_schema

        with Session(engine) as session:
            session.add(JargonInferenceState(jargon_id=1, locked=True, status="protected"))
            session.add(JargonInferenceJob(id="job-1", items_json='[{"id":1,"status":"protected"}]'))
            session.add(JargonMeaningHistory(jargon_id=1, previous_meaning="旧含义", meaning="人工解释"))
            session.commit()

        with engine.begin() as connection:
            migrate_v52_to_v53(MigrationExecutionContext(connection, 52, 53, 1, "v52_to_v53", 1))
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


def test_upgrade_is_registered_after_current_dev_v52() -> None:
    """推断迁移接到相通组与聊天迁移账本之后，不占用上游历史版本。"""
    registry = build_default_migration_registry()
    assert [registry.get_step(version).name for version in range(48, 53)] == [
        "v48_to_v49",
        "v49_to_v50",
        "v50_to_v51",
        "v51_to_v52",
        "v52_to_v53",
    ]
    step = registry.get_step(52)
    assert step is not None
    assert step.version_to == 53
    assert step.handler is migrate_v52_to_v53


INFERENCE_TABLES = {model.__tablename__ for model in (JargonInferenceState, JargonInferenceJob, JargonMeaningHistory)}


def test_current_dev_v52_upgrade_creates_inference_tables_without_rewriting_data() -> None:
    """实际 v52 库经启动入口升级，同时保留上游共享组、账本及原词条。"""
    from src.common.database.sharing_models import SharingGroup

    engine = create_engine("sqlite://")
    try:
        SQLModel.metadata.create_all(
            engine, tables=[table for table in SQLModel.metadata.sorted_tables if table.name not in INFERENCE_TABLES]
        )
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA user_version=52")
            connection.exec_driver_sql(
                "INSERT INTO sharing_groups(id,name,color,note,enabled,revision,created_at,updated_at) VALUES ('stable-group','已存在相通组','#39845A','',1,1,1,1)"
            )
            connection.exec_driver_sql(
                "INSERT INTO chat_migration_copies(table_name,origin,target,target_key,amounts) VALUES ('jargons','A:1','B','2','{}')"
            )
        with Session(engine) as session:
            session.add(Jargon(id=1, content="原词条", meaning="人工解释", created_by=JargonCreatedBy.MANUAL, count=7))
            session.commit()
        bootstrapper = create_database_migration_bootstrapper(engine)
        state = bootstrapper.prepare_database()
        bootstrapper.finalize_database(state)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 53
            assert INFERENCE_TABLES.issubset(inspect(connection).get_table_names())
            assert connection.exec_driver_sql("SELECT origin,target,target_key FROM chat_migration_copies").one() == (
                "A:1",
                "B",
                "2",
            )
        with Session(engine) as session:
            assert session.get(SharingGroup, "stable-group").name == "已存在相通组"
            original = session.get(Jargon, 1)
            assert (original.meaning, original.count, original.created_by) == ("人工解释", 7, JargonCreatedBy.MANUAL)
    finally:
        engine.dispose()


def test_fresh_and_unmarked_databases_finish_with_current_schema() -> None:
    """空库与丢失版本标记的完整模型库最终均建成并标记为 v53。"""
    engine = create_engine("sqlite://")
    try:
        bootstrapper = create_database_migration_bootstrapper(engine)
        state = bootstrapper.prepare_database()
        SQLModel.metadata.create_all(engine)
        bootstrapper.finalize_database(state)
        with engine.begin() as connection:
            assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 53
            assert INFERENCE_TABLES.issubset(inspect(connection).get_table_names())
            connection.exec_driver_sql("PRAGMA user_version=0")
        with engine.connect() as connection:
            assert build_default_schema_version_resolver().resolve(connection).version == 50
        state = bootstrapper.prepare_database()
        bootstrapper.finalize_database(state)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 53
    finally:
        engine.dispose()


def test_current_dev_v52_is_not_misdetected_as_inference_schema() -> None:
    """上游完整 v52 结构缺少推断表时仍要执行新增迁移。"""
    engine = create_engine("sqlite://")
    try:
        SQLModel.metadata.create_all(
            engine, tables=[table for table in SQLModel.metadata.sorted_tables if table.name not in INFERENCE_TABLES]
        )
        with engine.connect() as connection:
            assert build_default_schema_version_resolver().resolve(connection).version == 50
    finally:
        engine.dispose()


@pytest.mark.parametrize("existing_inference_schema", [False, True])
def test_unmarked_pre_signature_ledger_still_receives_upstream_index_upgrade(existing_inference_schema: bool) -> None:
    """v50 与 v52 列结构相同，缺少版本标记时不能跳过上游补索引步骤。"""
    engine = create_engine("sqlite://")
    try:
        SQLModel.metadata.create_all(
            engine,
            tables=[
                table
                for table in SQLModel.metadata.sorted_tables
                if existing_inference_schema or table.name not in INFERENCE_TABLES
            ],
        )
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP INDEX ix_chat_migration_copies_table_key")
        bootstrapper = create_database_migration_bootstrapper(engine)
        state = bootstrapper.prepare_database()
        bootstrapper.finalize_database(state)
        with engine.connect() as connection:
            assert "ix_chat_migration_copies_table_key" in {
                index["name"] for index in inspect(connection).get_indexes("chat_migration_copies")
            }
            assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 53
    finally:
        engine.dispose()


def test_original_v48_runs_upstream_steps_before_inference() -> None:
    """原基线先建相通组与聊天迁移账本，再建推断表，不覆盖任何既有词条。"""
    upstream_tables = {
        "sharing_groups",
        "sharing_group_members",
        "sharing_group_capabilities",
        "sharing_group_rules",
        "sharing_group_migrations",
        "chat_migration_jobs",
        "chat_migration_copies",
    }
    engine = create_engine("sqlite://")
    try:
        SQLModel.metadata.create_all(
            engine,
            tables=[
                table
                for table in SQLModel.metadata.sorted_tables
                if table.name not in INFERENCE_TABLES | upstream_tables
            ],
        )
        with Session(engine) as session:
            session.add(Jargon(id=1, content="旧基线词条", meaning="保留解释", count=8))
            session.commit()
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA user_version=48")
        bootstrapper = create_database_migration_bootstrapper(engine)
        assert [step.name for step in bootstrapper.manager.describe_state().plan.steps] == [
            "v48_to_v49",
            "v49_to_v50",
            "v50_to_v51",
            "v51_to_v52",
            "v52_to_v53",
        ]
        state = bootstrapper.prepare_database()
        bootstrapper.finalize_database(state)
        with engine.connect() as connection:
            assert INFERENCE_TABLES | upstream_tables <= set(inspect(connection).get_table_names())
            assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 53
            assert "signature" not in {
                column["name"] for column in inspect(connection).get_columns("chat_migration_copies")
            }
        with Session(engine) as session:
            original = session.get(Jargon, 1)
            assert (original.meaning, original.count) == ("保留解释", 8)
    finally:
        engine.dispose()


def test_unmarked_partial_inference_tables_are_completed_without_losing_state() -> None:
    """部分附属表存在时仍执行完整迁移，同时保留已有锁定状态。"""
    engine = create_engine("sqlite://")
    try:
        SQLModel.metadata.create_all(
            engine,
            tables=[
                table
                for table in SQLModel.metadata.sorted_tables
                if table.name not in INFERENCE_TABLES or table is JargonInferenceState.__table__
            ],
        )
        with Session(engine) as session:
            session.add(JargonInferenceState(jargon_id=1, locked=True, status="protected"))
            session.commit()
        bootstrapper = create_database_migration_bootstrapper(engine)
        state = bootstrapper.prepare_database()
        bootstrapper.finalize_database(state)
        with engine.connect() as connection:
            assert INFERENCE_TABLES.issubset(inspect(connection).get_table_names())
            assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 53
        with Session(engine) as session:
            assert session.get(JargonInferenceState, 1).locked
    finally:
        engine.dispose()


def test_old_inference_only_schema_does_not_claim_upstream_sharing_version() -> None:
    """旧预览的推断表不能冒充上游相通组迁移后的结构。"""
    engine = create_engine("sqlite://")
    try:
        SQLModel.metadata.create_all(
            engine,
            tables=[
                table
                for table in SQLModel.metadata.sorted_tables
                if not table.name.startswith(("sharing_group", "chat_migration_"))
            ],
        )
        with engine.connect() as connection:
            assert build_default_schema_version_resolver().resolve(connection).version < 49
    finally:
        engine.dispose()
