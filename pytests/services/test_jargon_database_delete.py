"""插件通用删除接口必须与黑话状态、历史一起提交或回滚。"""

from contextlib import contextmanager

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

import pytest

from src.common.database.database_model import Jargon, JargonInferenceState, JargonMeaningHistory
from src.plugin_runtime.capabilities.data import RuntimeDataCapabilityMixin
from src.services import database_service


@pytest.fixture
def database(tmp_path, monkeypatch):
    """为插件数据库能力准备两个独立词条和关联的推断状态及历史。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'delete.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(
        engine, tables=[Jargon.__table__, JargonInferenceState.__table__, JargonMeaningHistory.__table__]
    )

    @contextmanager
    def session_scope(**_kwargs):
        """让通用删除与关联清理共享一次真实的 SQLite 提交。"""
        with Session(engine) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr(database_service, "get_db_session", session_scope)
    with session_scope() as session:
        for jargon_id in (1, 2):
            session.add(Jargon(id=jargon_id, content=f"旧词{jargon_id}", meaning="含义"))
            session.add(JargonInferenceState(jargon_id=jargon_id, locked=True, status="succeeded"))
            session.add(JargonMeaningHistory(jargon_id=jargon_id, previous_meaning="旧含义", meaning="含义"))
    yield engine
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("query_api", [False, True])
async def test_plugin_delete_cleans_only_matching_jargon_metadata_before_id_reuse(database, query_api):
    """两种插件删除入口都应清除目标元数据，且重用 ID 后不得继承旧锁。"""
    capability = RuntimeDataCapabilityMixin()
    args = {"model_name": "Jargon", "filters": {"id": 2}}
    if query_api:
        result = await capability._cap_database_query("test", "database.query", {**args, "query_type": "delete"})
    else:
        result = await capability._cap_database_delete("test", "database.delete", args)
    assert result == 1
    with Session(database) as session:
        replacement = Jargon(content="新词", meaning="")
        session.add(replacement)
        session.commit()
        session.refresh(replacement)
        assert replacement.id == 2
        assert session.get(JargonInferenceState, 2) is None
        assert session.get(JargonInferenceState, 1).locked is True
        assert [item.jargon_id for item in session.exec(select(JargonMeaningHistory))] == [1]


@pytest.mark.asyncio
async def test_failed_parent_delete_rolls_back_metadata_cleanup(database):
    with database.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_jargon_delete BEFORE DELETE ON jargons "
                "BEGIN SELECT RAISE(ABORT, 'injected deletion failure'); END"
            )
        )
    result = await database_service.db_delete(Jargon, {"id": 2})
    assert result == 0
    with Session(database) as session:
        assert session.get(Jargon, 2) is not None
        assert session.get(JargonInferenceState, 2).locked is True
        assert len(session.exec(select(JargonMeaningHistory)).all()) == 2


@pytest.mark.asyncio
async def test_nonmatching_filter_does_not_remove_metadata(database):
    assert await database_service.db_delete(Jargon, {"id": 999}) == 0
    with Session(database) as session:
        assert len(session.exec(select(Jargon)).all()) == 2
        assert len(session.exec(select(JargonInferenceState)).all()) == 2
        assert len(session.exec(select(JargonMeaningHistory)).all()) == 2
