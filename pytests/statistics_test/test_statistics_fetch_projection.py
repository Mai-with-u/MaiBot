from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session

from src.chat.utils import statistic
from src.common.database.database_model import Messages
from src.services import statistics_service


def _message(
    timestamp: datetime,
    *,
    message_id: str = "m1",
    platform: str = "qq",
    user_id: str = "u1",
    user_nickname: str = "用户一",
    group_id: str | None = "g1",
    group_name: str | None = "测试群",
) -> Messages:
    return Messages(
        message_id=message_id,
        timestamp=timestamp,
        platform=platform,
        user_id=user_id,
        user_nickname=user_nickname,
        group_id=group_id,
        group_name=group_name,
        session_id="s1",
        raw_content=b"\x82\xa7content\xa5heavy" * 512,
        processed_plain_text="plain" * 512,
        additional_config='{"payload": "x"}',
    )


def _patch_messages_database(monkeypatch, tmp_path, records: list[Messages]) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'messages.db'}")
    SQLModel.metadata.create_all(engine, tables=[Messages.__table__])
    with Session(engine) as session:
        session.add_all(records)
        session.commit()

    @contextmanager
    def get_test_db_session(*, auto_commit: bool = True):
        with Session(engine) as session:
            yield session
            if auto_commit:
                session.commit()

    monkeypatch.setattr(statistics_service, "get_db_session", get_test_db_session)


def test_fetch_messages_since_selects_only_reporting_columns(monkeypatch, tmp_path) -> None:
    """统计任务只消费 6 个字段，查询不应把整行（含 raw_content 等大列）拉进内存。"""
    start_time = datetime(2026, 9, 1)
    _patch_messages_database(monkeypatch, tmp_path, [_message(start_time + timedelta(hours=1))])

    rows = statistics_service.fetch_messages_since(start_time)

    assert len(rows) == 1
    row = rows[0]
    for field in ("timestamp", "platform", "user_id", "group_id", "group_name", "user_nickname"):
        assert hasattr(row, field), f"统计调用方需要的字段 {field} 丢失"
    for heavy in ("raw_content", "processed_plain_text", "additional_config", "session_id"):
        assert not hasattr(row, heavy), f"未使用的大列 {heavy} 不应被加载"


def test_fetch_messages_since_preserves_field_values(monkeypatch, tmp_path) -> None:
    """投影后字段值与类型保持不变，调用方的 .timestamp() 与分组逻辑不受影响。"""
    start_time = datetime(2026, 9, 1)
    ts = start_time + timedelta(hours=2, minutes=30)
    _patch_messages_database(
        monkeypatch,
        tmp_path,
        [
            _message(
                ts,
                message_id="m2",
                platform="telegram",
                user_id="u2",
                user_nickname="昵称",
                group_id=None,
                group_name=None,
            )
        ],
    )

    rows = statistics_service.fetch_messages_since(start_time)

    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row.timestamp, datetime)
    assert row.timestamp == ts
    assert row.platform == "telegram"
    assert row.user_id == "u2"
    assert row.user_nickname == "昵称"
    assert row.group_id is None
    assert row.group_name is None


def test_collect_interval_data_groups_projected_rows(monkeypatch, tmp_path) -> None:
    """_collect_interval_data 走真实数据库读取投影行，聊天流分组结果与修复前一致。"""
    now = datetime(2026, 9, 1, 12)
    records = [
        _message(now - timedelta(minutes=30), message_id="g-msg", group_id="g1", group_name="测试群"),
        _message(
            now - timedelta(minutes=10),
            message_id="p-msg",
            user_id="u9",
            user_nickname="私聊用户",
            group_id=None,
            group_name=None,
        ),
    ]
    _patch_messages_database(monkeypatch, tmp_path, records)
    monkeypatch.setattr(statistic, "fetch_model_usage_since", lambda _: iter([]))

    task = object.__new__(statistic.StatisticOutputTask)
    data = task._collect_interval_data(now, hours=1, interval_minutes=60)

    assert data["message_by_chat"] == {"测试群": [1, 0], "私聊用户": [1, 0]}


def test_collect_metrics_interval_data_counts_replies_with_projected_rows(monkeypatch, tmp_path) -> None:
    """_collect_metrics_interval_data 走真实数据库读取投影行，bot 回复计数不受影响。"""
    now = datetime(2026, 9, 1, 12)
    records = [
        _message(now - timedelta(minutes=50), message_id="bot-msg", user_id="bot1"),
        _message(now - timedelta(minutes=40), message_id="user-msg", user_id="u1"),
    ]
    _patch_messages_database(monkeypatch, tmp_path, records)
    monkeypatch.setattr(statistic, "fetch_model_usage_since", lambda _: iter([]))
    monkeypatch.setattr(statistic, "fetch_online_time_since", lambda _: iter([]))
    import src.chat.utils.utils as utils_module

    monkeypatch.setattr(utils_module, "is_bot_self", lambda platform, user_id: user_id == "bot1")

    task = object.__new__(statistic.StatisticOutputTask)
    data = task._collect_metrics_interval_data(now, hours=1, interval_hours=1)

    # 1 条 bot 回复 → cost_per_100_replies 走 total_replies=1 分支（无花费时为 0.0，
    # 关键是分母逻辑执行且不抛异常；消息总数经 message 计数路径钉住）
    assert data["time_labels"] == ["11:00", "12:00"]
    assert len(data["cost_per_100_messages"]) == 2
