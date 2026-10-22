"""麦麦观察事件账本。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlmodel import Session, col, select

import json
import threading
import time

from src.common.database.database import get_db_session
from src.common.database.database_model import ImageType, Images, MaisakaMonitorEventRecord
from src.common.logger import get_logger
from src.maisaka.monitor.planner_delta import PLANNER_EVENTS

logger = get_logger("maisaka_monitor_event_store")

MONITOR_EVENT_SCHEMA_VERSION = 1
MAX_MONITOR_EVENT_RECORDS = 10000
MAX_MONITOR_EVENT_AGE_HOURS = 72
DEFAULT_REPLAY_LIMIT = 1000
MAX_REPLAY_LIMIT = MAX_MONITOR_EVENT_RECORDS
DEFAULT_HISTORY_PAGE_SIZE = 100
MAX_HISTORY_PAGE_SIZE = 500
MAX_SUBAGENT_HISTORY_EVENTS = 500
SESSION_MESSAGE_EVENT_TYPES = ("message.ingested", "message.sent")
# 子代理面板由这些事件累积而成；session.start 用于把上一个进程遗留的任务标记为已终止。
SUBAGENT_HISTORY_EVENT_TYPES = (
    "session.start",
    "subagent.started",
    "subagent.round",
    "subagent.tool_result",
    "subagent.finished",
)
CLEANUP_CHECK_INTERVAL_RECORDS = 200
CLEANUP_CHECK_INTERVAL_SECONDS = 60



@dataclass
class MonitorEventPage:
    """单个聊天流的一页历史事件，``events`` 按 ``event_id`` 升序排列。"""

    events: list[dict[str, Any]]
    oldest_event_id: int
    has_more: bool


_cleanup_lock = threading.Lock()
_records_since_cleanup = 0
_last_cleanup_at = 0.0


def record_monitor_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """写入一条麦麦观察时间线事件，并返回带 ``event_id`` 的清洗后 payload。"""

    now = time.time()
    with get_db_session() as session:
        cleaned_payload = sanitize_monitor_payload(payload, session=session)
        timestamp = _coerce_float(cleaned_payload.get("timestamp"), default=now)
        if event_type in ("message.ingested", "message.updated"):
            # 媒体处理完成可能很晚，账本按最初接收时刻索引；payload 的源时间仍原样保留。
            timestamp = _coerce_float(cleaned_payload.get("received_at"), default=timestamp)
        session_id = str(cleaned_payload.get("session_id") or "")
        record = MaisakaMonitorEventRecord(
            event_type=event_type,
            session_id=session_id,
            timestamp=timestamp,
            schema_version=MONITOR_EVENT_SCHEMA_VERSION,
            payload_json="{}",
            created_at=datetime.now(),
        )
        session.add(record)
        session.flush()

        if record.event_id is None:
            raise RuntimeError("麦麦观察事件写入后未获得 event_id")

        cleaned_payload["event_id"] = record.event_id
        cleaned_payload["schema_version"] = MONITOR_EVENT_SCHEMA_VERSION
        record.payload_json = json.dumps(cleaned_payload, ensure_ascii=False, separators=(",", ":"))
        session.add(record)

        if _should_cleanup_monitor_events():
            cleanup_monitor_events(session)

        return cleaned_payload


def replay_monitor_events(*, since_event_id: int = 0, limit: int = DEFAULT_REPLAY_LIMIT) -> list[dict[str, Any]]:
    """按 ``event_id`` 返回可重放的麦麦观察事件。"""

    normalized_limit = max(1, min(limit, MAX_REPLAY_LIMIT))
    with get_db_session(auto_commit=False) as session:
        if since_event_id > 0:
            statement = (
                select(MaisakaMonitorEventRecord)
                .where(MaisakaMonitorEventRecord.event_id > since_event_id)
                .order_by(MaisakaMonitorEventRecord.event_id)
                .limit(normalized_limit)
            )
            records = list(session.exec(statement).all())
        else:
            statement = (
                select(MaisakaMonitorEventRecord)
                .order_by(MaisakaMonitorEventRecord.event_id.desc())
                .limit(normalized_limit)
            )
            records = list(reversed(session.exec(statement).all()))

    events = [_record_to_replay_event(record) for record in records]
    valid_events = [event for event in events if _is_replayable_event(event)]
    skipped_count = len(events) - len(valid_events)
    if skipped_count > 0:
        logger.warning(f"回放时跳过 {skipped_count} 条缺少 run_id 的旧版 Planner 事件")
    return valid_events


def load_session_event_page(
    *,
    session_id: str,
    before_event_id: int = 0,
    limit: int = DEFAULT_HISTORY_PAGE_SIZE,
) -> MonitorEventPage:
    """读取某个聊天流早于 ``before_event_id`` 的最近一页事件；为 0 时读取最新一页。"""

    normalized_limit = max(1, min(limit, MAX_HISTORY_PAGE_SIZE))
    with get_db_session(auto_commit=False) as session:
        statement = select(MaisakaMonitorEventRecord).where(MaisakaMonitorEventRecord.session_id == session_id)
        if before_event_id > 0:
            statement = statement.where(MaisakaMonitorEventRecord.event_id < before_event_id)
        # 多取一条用于判断是否还有更早的记录。
        statement = statement.order_by(MaisakaMonitorEventRecord.event_id.desc()).limit(normalized_limit + 1)
        records = list(session.exec(statement).all())

    has_more = len(records) > normalized_limit
    page_records = list(reversed(records[:normalized_limit]))
    events = [_record_to_replay_event(record) for record in page_records]
    return MonitorEventPage(
        events=[event for event in events if _is_replayable_event(event)],
        oldest_event_id=int(page_records[0].event_id or 0) if page_records else 0,
        has_more=has_more,
    )


def load_session_subagent_events(
    *,
    session_id: str,
    before_event_id: int,
    limit: int = MAX_SUBAGENT_HISTORY_EVENTS,
) -> list[dict[str, Any]]:
    """读取某个聊天流早于 ``before_event_id`` 的子代理事件，按 ``event_id`` 升序返回。

    分页只加载最新一页时间线，仍在运行的子代理可能开始于更早的页；
    这些事件数量很少，随首页一并补发，保证子代理面板完整。
    """

    with get_db_session(auto_commit=False) as session:
        statement = (
            select(MaisakaMonitorEventRecord)
            .where(MaisakaMonitorEventRecord.session_id == session_id)
            .where(col(MaisakaMonitorEventRecord.event_type).in_(SUBAGENT_HISTORY_EVENT_TYPES))
            .where(MaisakaMonitorEventRecord.event_id < before_event_id)
            .order_by(MaisakaMonitorEventRecord.event_id.desc())
            .limit(max(1, limit))
        )
        records = list(reversed(session.exec(statement).all()))
    return [_record_to_replay_event(record) for record in records]


def load_session_summaries() -> list[dict[str, Any]]:
    """汇总账本中每个聊天流的概要：事件数、最近活动、最近一次启动事件与最新一条消息。"""

    message_types = ", ".join(f"'{event_type}'" for event_type in SESSION_MESSAGE_EVENT_TYPES)
    with get_db_session(auto_commit=False) as session:
        rows = session.execute(
            text(
                f"""
                SELECT
                    session_id,
                    COUNT(*) AS event_count,
                    MAX(event_id) AS last_event_id,
                    MAX(timestamp) AS last_activity,
                    MAX(CASE WHEN event_type = 'session.start' THEN event_id END) AS start_event_id,
                    MAX(CASE WHEN event_type IN ({message_types}) THEN event_id END) AS message_event_id
                FROM maisaka_monitor_events
                WHERE session_id != ''
                GROUP BY session_id
                """
            )
        ).all()
        detail_event_ids = [
            event_id for row in rows for event_id in (row.start_event_id, row.message_event_id) if event_id is not None
        ]
        detail_events: dict[int, dict[str, Any]] = {}
        if detail_event_ids:
            statement = select(MaisakaMonitorEventRecord).where(
                col(MaisakaMonitorEventRecord.event_id).in_(detail_event_ids)
            )
            for record in session.exec(statement).all():
                if record.event_id is not None:
                    detail_events[record.event_id] = _record_to_replay_event(record)

    summaries: list[dict[str, Any]] = []
    for row in rows:
        start_event = detail_events.get(row.start_event_id)
        summaries.append(
            {
                "session_id": row.session_id,
                "event_count": int(row.event_count),
                "last_event_id": int(row.last_event_id),
                "last_activity": float(row.last_activity),
                "session_start": start_event["data"] if start_event is not None else None,
                "latest_message": detail_events.get(row.message_event_id),
            }
        )
    return summaries


def cleanup_monitor_events(session: Optional[Session] = None) -> int:
    """清理超出保留策略的麦麦观察事件记录。"""

    if session is None:
        with get_db_session() as managed_session:
            return cleanup_monitor_events(managed_session)

    cutoff = datetime.now() - timedelta(hours=MAX_MONITOR_EVENT_AGE_HOURS)
    age_result = session.execute(
        text("DELETE FROM maisaka_monitor_events WHERE created_at < :cutoff"),
        {"cutoff": cutoff},
    )
    count_result = session.execute(
        text(
            """
            DELETE FROM maisaka_monitor_events
            WHERE event_id NOT IN (
                SELECT event_id
                FROM maisaka_monitor_events
                ORDER BY event_id DESC
                LIMIT :max_records
            )
            """
        ),
        {"max_records": MAX_MONITOR_EVENT_RECORDS},
    )
    removed_count = int(age_result.rowcount or 0) + int(count_result.rowcount or 0)
    if removed_count > 0:
        logger.debug(f"麦麦观察事件账本清理完成，删除记录数={removed_count}")
    return removed_count


def sanitize_monitor_payload(payload: dict[str, Any], *, session: Optional[Session] = None) -> dict[str, Any]:
    """清洗监控事件 payload，避免持久化大体积内联二进制。"""

    cleaned = _sanitize_payload_value(payload, session=session)
    if not isinstance(cleaned, dict):
        raise TypeError("麦麦观察事件 payload 必须是字典")
    return cleaned


def _is_replayable_event(event: dict[str, Any]) -> bool:
    """旧版本写入的 Planner 事件没有 run_id，无法做增量编码，回放时丢弃。"""

    if event["event"] not in PLANNER_EVENTS:
        return True
    return bool(event["data"].get("run_id"))


def _record_to_replay_event(record: MaisakaMonitorEventRecord) -> dict[str, Any]:
    payload = json.loads(record.payload_json)
    if not isinstance(payload, dict):
        raise TypeError(f"麦麦观察事件 payload 不是字典: event_id={record.event_id}")
    payload.setdefault("event_id", record.event_id)
    payload.setdefault("schema_version", record.schema_version)
    return {
        "event": record.event_type,
        "data": payload,
    }


def _sanitize_payload_value(value: Any, *, session: Optional[Session]) -> Any:
    if isinstance(value, dict):
        return _sanitize_payload_dict(value, session=session)
    if isinstance(value, list):
        return [_sanitize_payload_value(item, session=session) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_payload_value(item, session=session) for item in value]
    return value


def _sanitize_payload_dict(value: dict[Any, Any], *, session: Optional[Session]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for raw_key, raw_item in value.items():
        key = str(raw_key)
        if key == "data_url":
            continue
        cleaned[key] = _sanitize_payload_value(raw_item, session=session)

    if _looks_like_monitor_media(cleaned):
        media_path = _resolve_monitor_media_path(
            media_kind=str(cleaned.get("kind") or ""),
            media_hash=str(cleaned.get("hash") or ""),
            session=session,
        )
        if media_path:
            cleaned["path"] = media_path

    return cleaned


def _looks_like_monitor_media(value: dict[str, Any]) -> bool:
    return str(value.get("kind") or "") in {"image", "emoji"} and bool(str(value.get("hash") or "").strip())


def _resolve_monitor_media_path(*, media_kind: str, media_hash: str, session: Optional[Session]) -> str:
    normalized_hash = media_hash.strip()
    if not normalized_hash or media_kind not in {"image", "emoji"} or session is None:
        return ""

    image_type = ImageType.IMAGE if media_kind == "image" else ImageType.EMOJI
    statement = select(Images).filter_by(image_hash=normalized_hash, image_type=image_type).limit(1)
    image_record = session.exec(statement).first()
    if image_record is None or image_record.no_file_flag:
        return ""
    return image_record.full_path


def _coerce_float(value: Any, *, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized_value = value.strip()
        if normalized_value:
            return float(normalized_value)
    return default


def _should_cleanup_monitor_events() -> bool:
    global _last_cleanup_at, _records_since_cleanup

    with _cleanup_lock:
        _records_since_cleanup += 1
        now = time.time()
        if (
            _records_since_cleanup < CLEANUP_CHECK_INTERVAL_RECORDS
            and now - _last_cleanup_at < CLEANUP_CHECK_INTERVAL_SECONDS
        ):
            return False
        _records_since_cleanup = 0
        _last_cleanup_at = now
        return True
