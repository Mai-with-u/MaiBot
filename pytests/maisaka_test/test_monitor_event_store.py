from contextlib import contextmanager
from typing import Generator

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import time

from src.common.database.database_model import ImageType, Images
from src.maisaka.monitor import event_store


def _install_in_memory_database(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def get_test_db_session(auto_commit: bool = True) -> Generator[Session, None, None]:
        session = Session(engine)
        try:
            yield session
            if auto_commit:
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(event_store, "get_db_session", get_test_db_session)
    monkeypatch.setattr(event_store, "_records_since_cleanup", 0)
    monkeypatch.setattr(event_store, "_last_cleanup_at", time.time())


def test_record_monitor_event_strips_data_url_and_persists_media_path(monkeypatch) -> None:
    _install_in_memory_database(monkeypatch)

    with event_store.get_db_session() as session:
        session.add(
            Images(
                image_hash="image-hash",
                description="一张图",
                full_path="data/images/image-hash.png",
                image_type=ImageType.IMAGE,
            )
        )

    payload = event_store.record_monitor_event(
        "message.ingested",
        {
            "session_id": "session-1",
            "timestamp": 1710000000.0,
            "media": [
                {
                    "kind": "image",
                    "hash": "image-hash",
                    "text": "[图片]",
                    "url": "/api/webui/system/maisaka-monitor/media/image/image-hash",
                    "data_url": "data:image/png;base64,AAAA",
                }
            ],
        },
    )

    assert payload["event_id"] == 1
    media = payload["media"][0]
    assert "data_url" not in media
    assert media["path"] == "data/images/image-hash.png"

    replay_events = event_store.replay_monitor_events(since_event_id=0, limit=10)
    replay_media = replay_events[0]["data"]["media"][0]
    assert "data_url" not in replay_media
    assert replay_media["path"] == "data/images/image-hash.png"


def test_replay_monitor_events_uses_event_id_cursor(monkeypatch) -> None:
    _install_in_memory_database(monkeypatch)

    first_payload = event_store.record_monitor_event(
        "session.start",
        {"session_id": "session-1", "session_name": "测试会话", "timestamp": 1710000000.0},
    )
    second_payload = event_store.record_monitor_event(
        "message.sent",
        {"session_id": "session-1", "speaker_name": "麦麦", "content": "你好", "timestamp": 1710000001.0},
    )

    replay_events = event_store.replay_monitor_events(since_event_id=first_payload["event_id"], limit=10)

    assert len(replay_events) == 1
    assert replay_events[0]["event"] == "message.sent"
    assert replay_events[0]["data"]["event_id"] == second_payload["event_id"]
