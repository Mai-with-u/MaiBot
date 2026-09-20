from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session

from src.chat.utils import statistic
from src.chat.utils import utils as utils_module
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


def _usage(timestamp: datetime, *, cost: float = 0.0, prompt_tokens: int = 0, completion_tokens: int = 0) -> dict:
    return {
        "timestamp": timestamp,
        "cost": cost,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "model_assign_name": "测试模型",
        "model_name": "test-model",
        "request_type": "chat.normal",
    }


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
    """_collect_interval_data 走真实数据库读取投影行，消息与花费按各自区间分桶。"""
    now = datetime(2026, 9, 1, 12)
    # hours=1, interval_minutes=60 → 两个分桶 [11:00, 12:00) 与 [12:00, ...]
    records = [
        _message(now - timedelta(minutes=50), message_id="g-msg", group_id="g1", group_name="测试群"),
        _message(
            now,
            message_id="p-msg",
            user_id="u9",
            user_nickname="私聊用户",
            group_id=None,
            group_name=None,
        ),
    ]
    _patch_messages_database(monkeypatch, tmp_path, records)
    # 一条花费落在第二个分桶（12:00 整点属 [12:00, 13:00) 桶），钉住花费数组也按区间分桶
    monkeypatch.setattr(
        statistic,
        "fetch_model_usage_since",
        lambda _: iter([_usage(now, cost=1.5)]),
    )

    task = object.__new__(statistic.StatisticOutputTask)
    data = task._collect_interval_data(now, hours=1, interval_minutes=60)

    # 群消息在第一个分桶、私聊消息在第二个分桶：数组位置必须区分开
    assert data["message_by_chat"] == {"测试群": [1, 0], "私聊用户": [0, 1]}
    assert data["time_labels"] == ["11:00", "12:00"]
    assert data["total_cost_data"] == [0.0, 1.5]


def test_collect_metrics_interval_data_counts_replies_with_projected_rows(monkeypatch, tmp_path) -> None:
    """_collect_metrics_interval_data 走真实数据库读取投影行，bot 回复计数与花费指标按区间分桶。"""
    now = datetime(2026, 9, 1, 12)
    # bot 回复落在第一个分桶（11:10），普通用户消息落在第二个分桶（12:00 整点）
    records = [
        _message(now - timedelta(minutes=50), message_id="bot-msg", user_id="bot1"),
        _message(now, message_id="user-msg", user_id="u1", user_nickname="用户二"),
    ]
    _patch_messages_database(monkeypatch, tmp_path, records)
    # 第一个分桶放一条非零花费记录，cost_per_100_replies 必须按回复数算出非零值
    monkeypatch.setattr(
        statistic,
        "fetch_model_usage_since",
        lambda _: iter([_usage(now - timedelta(minutes=50), cost=2.0, prompt_tokens=100, completion_tokens=50)]),
    )
    monkeypatch.setattr(statistic, "fetch_online_time_since", lambda _: iter([]))
    monkeypatch.setattr(utils_module, "is_bot_self", lambda platform, user_id: user_id == "bot1")

    task = object.__new__(statistic.StatisticOutputTask)
    data = task._collect_metrics_interval_data(now, hours=1, interval_hours=1)

    assert data["time_labels"] == ["11:00", "12:00"]
    # 第一个分桶：1 条 bot 回复 + 花费 2.0 → 2.0 / 1 * 100 = 200.0；第二个分桶无回复
    assert data["cost_per_100_replies"] == [200.0, 0.0]
    # 第一个分桶 1 条消息花费 2.0 → 200.0；第二个分桶 1 条消息零花费 → 0.0
    assert data["cost_per_100_messages"] == [200.0, 0.0]
    assert data["tokens_per_hour"] == [0.0, 0.0]
