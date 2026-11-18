"""聊天副本不能再次计入实例收发量。"""

from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlmodel import Session, SQLModel

from src.common.database.chat_migration_models import copies
from src.common.database.database_model import Messages, StatisticsMessageHourly
from src.services import statistics_aggregation_service as statistics


def test_copied_messages_do_not_inflate_instance_statistics(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'statistics.db'}")
    SQLModel.metadata.create_all(engine)
    now = datetime(2026, 1, 1, 12)
    with Session(engine) as session:
        for identity in ("source", "target"):
            session.add(
                Messages(
                    message_id="same",
                    timestamp=now,
                    platform="qq",
                    user_id="user",
                    user_nickname="name",
                    group_id="group",
                    session_id=identity,
                    raw_content=b"",
                )
            )
        session.commit()
        session.execute(
            copies.insert().values(table_name="mai_messages", origin="source:1", target="target", target_key="2")
        )
        session.commit()

    @contextmanager
    def database(**kwargs):
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(statistics, "get_db_session", database)
    statistics.refresh_statistics_aggregates()
    statistics.refresh_statistics_aggregates()
    with Session(engine) as session:
        assert session.execute(select(StatisticsMessageHourly.message_count)).scalar_one() == 1
    assert statistics.fetch_message_count_by_chat_since(datetime(2026, 1, 1))[0]["message_count"] == 1
