"""测试表达方式自动检查任务的数据库读取行为。"""

from contextlib import contextmanager
from typing import Generator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from src.bw_learner.expression_auto_check_task import ExpressionAutoCheckTask
from src.common.database.database_model import Expression


@pytest.fixture(name="expression_auto_check_engine")
def expression_auto_check_engine_fixture() -> Generator:
    """创建用于表达方式自动检查任务测试的内存数据库引擎。

    Yields:
        Generator: 供测试使用的 SQLite 内存引擎。
    """

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine


@pytest.mark.asyncio
async def test_select_expressions_uses_read_only_session(
    monkeypatch: pytest.MonkeyPatch,
    expression_auto_check_engine,
) -> None:
    """选择表达方式时应使用只读会话，并在离开会话后安全读取 ORM 字段。"""

    import src.bw_learner.expression_auto_check_task as expression_auto_check_task_module

    with Session(expression_auto_check_engine) as session:
        session.add(
            Expression(
                situation="表达情绪高涨或生理反应",
                style="发送💦表情符号",
                content_list='["表达情绪高涨或生理反应"]',
                count=1,
                session_id="session-a",
                checked=False,
                rejected=False,
            )
        )
        session.commit()

    auto_commit_calls: list[bool] = []

    @contextmanager
    def fake_get_db_session(auto_commit: bool = True) -> Generator[Session, None, None]:
        """构造带自动提交语义的测试会话工厂。

        Args:
            auto_commit: 退出上下文时是否自动提交。

        Yields:
            Generator[Session, None, None]: SQLModel 会话对象。
        """

        auto_commit_calls.append(auto_commit)
        session = Session(expression_auto_check_engine)
        try:
            yield session
            if auto_commit:
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(expression_auto_check_task_module, "get_db_session", fake_get_db_session)
    monkeypatch.setattr(expression_auto_check_task_module.random, "sample", lambda entries, _count: list(entries))

    task = ExpressionAutoCheckTask()
    expressions = await task._select_expressions(1)

    assert auto_commit_calls == [False]
    assert len(expressions) == 1
    assert expressions[0].id is not None
    assert expressions[0].situation == "表达情绪高涨或生理反应"
    assert expressions[0].style == "发送💦表情符号"
