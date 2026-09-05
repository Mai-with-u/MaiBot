from datetime import datetime
from typing import Any

from sqlalchemy import text

from src.common.database.database import get_db_session
from src.common.logger import get_logger

logger = get_logger("statistics_aggregation_service")


def refresh_statistics_aggregates() -> None:
    """增量刷新统计汇总表。"""

    with get_db_session(auto_commit=False) as session:
        _aggregate_messages(session)
        _aggregate_tool_records(session)
        _aggregate_model_usage(session)
        session.commit()


def fetch_message_count_by_chat_since(start_time: datetime) -> list[dict[str, Any]]:
    """按聊天对象聚合指定时间之后的消息数，不加载消息明细。"""

    with get_db_session(auto_commit=False) as session:
        rows = session.exec(
            text(
                """
                SELECT
                    message_aggregate.chat_id,
                    latest_message.chat_name,
                    message_aggregate.message_count,
                    message_aggregate.latest_timestamp
                FROM (
                    SELECT
                        CASE
                            WHEN group_id IS NOT NULL AND group_id != '' THEN 'g' || group_id
                            ELSE 'u' || user_id
                        END AS chat_id,
                        COUNT(*) AS message_count,
                        MAX(timestamp) AS latest_timestamp
                    FROM mai_messages
                    WHERE timestamp >= :start_time
                      AND (
                          (group_id IS NOT NULL AND group_id != '')
                          OR (user_id IS NOT NULL AND user_id != '')
                      )
                    GROUP BY chat_id
                ) AS message_aggregate
                JOIN (
                    SELECT chat_id, chat_name
                    FROM (
                        SELECT
                            CASE
                                WHEN group_id IS NOT NULL AND group_id != '' THEN 'g' || group_id
                                ELSE 'u' || user_id
                            END AS chat_id,
                            COALESCE(
                                CASE WHEN group_id IS NOT NULL AND group_id != '' THEN group_name ELSE user_nickname END,
                                CASE
                                    WHEN group_id IS NOT NULL AND group_id != '' THEN '群' || group_id
                                    ELSE '用户' || user_id
                                END
                            ) AS chat_name,
                            ROW_NUMBER() OVER (
                                PARTITION BY CASE
                                    WHEN group_id IS NOT NULL AND group_id != '' THEN 'g' || group_id
                                    ELSE 'u' || user_id
                                END
                                ORDER BY timestamp DESC, id DESC
                            ) AS row_num
                        FROM mai_messages
                        WHERE timestamp >= :start_time
                          AND (
                              (group_id IS NOT NULL AND group_id != '')
                              OR (user_id IS NOT NULL AND user_id != '')
                          )
                    )
                    WHERE row_num = 1
                ) AS latest_message
                  ON latest_message.chat_id = message_aggregate.chat_id
                """
            ),
            params={"start_time": start_time},
        ).all()
    return [
        {
            "chat_id": row[0],
            "chat_name": row[1],
            "message_count": int(row[2] or 0),
            "latest_timestamp": _coerce_datetime(row[3]),
        }
        for row in rows
    ]


def count_tool_records_since(start_time: datetime, tool_name: str) -> int:
    """统计指定时间之后的工具调用次数，不加载工具调用明细。"""

    with get_db_session(auto_commit=False) as session:
        row = session.exec(
            text(
                """
                SELECT COUNT(*)
                FROM tool_records
                WHERE timestamp >= :start_time
                  AND tool_name = :tool_name
                """
            ),
            params={"start_time": start_time, "tool_name": tool_name},
        ).one()
    try:
        return int(row[0])
    except (TypeError, IndexError):
        return int(row)


def _aggregate_messages(session) -> None:
    last_id = _get_cursor(session, "messages")
    session.exec(
        text(
            """
            INSERT INTO statistics_message_hourly (
                bucket_time, chat_id, chat_name, chat_type, message_count, latest_timestamp
            )
            WITH message_source AS (
                SELECT
                    id,
                    datetime(strftime('%Y-%m-%d %H:00:00', timestamp)) AS bucket_time,
                    CASE
                        WHEN group_id IS NOT NULL AND group_id != '' THEN 'g' || group_id
                        ELSE 'u' || user_id
                    END AS chat_id,
                    COALESCE(
                        CASE WHEN group_id IS NOT NULL AND group_id != '' THEN group_name ELSE user_nickname END,
                        CASE
                            WHEN group_id IS NOT NULL AND group_id != '' THEN '群' || group_id
                            ELSE '用户' || user_id
                        END
                    ) AS chat_name,
                    CASE WHEN group_id IS NOT NULL AND group_id != '' THEN 'group' ELSE 'private' END AS chat_type,
                    timestamp
                FROM mai_messages
                WHERE id > :last_id
                  AND timestamp IS NOT NULL
                  AND (
                      (group_id IS NOT NULL AND group_id != '')
                      OR (user_id IS NOT NULL AND user_id != '')
                  )
            ),
            message_aggregate AS (
                SELECT
                    bucket_time,
                    chat_id,
                    COUNT(*) AS message_count,
                    MAX(timestamp) AS latest_timestamp
                FROM message_source
                GROUP BY bucket_time, chat_id
            ),
            latest_message AS (
                SELECT
                    bucket_time,
                    chat_id,
                    chat_name,
                    chat_type,
                    ROW_NUMBER() OVER (
                        PARTITION BY bucket_time, chat_id
                        ORDER BY timestamp DESC, id DESC
                    ) AS row_num
                FROM message_source
            )
            SELECT
                message_aggregate.bucket_time,
                message_aggregate.chat_id,
                latest_message.chat_name,
                latest_message.chat_type,
                message_aggregate.message_count,
                message_aggregate.latest_timestamp
            FROM message_aggregate
            JOIN latest_message
              ON latest_message.bucket_time = message_aggregate.bucket_time
             AND latest_message.chat_id = message_aggregate.chat_id
             AND latest_message.row_num = 1
            ON CONFLICT(bucket_time, chat_id) DO UPDATE SET
                chat_name = excluded.chat_name,
                chat_type = excluded.chat_type,
                message_count = statistics_message_hourly.message_count + excluded.message_count,
                latest_timestamp = MAX(statistics_message_hourly.latest_timestamp, excluded.latest_timestamp)
            """
        ),
        params={"last_id": last_id},
    )
    _set_cursor_to_table_max(session, "messages", "mai_messages")


def _aggregate_tool_records(session) -> None:
    last_id = _get_cursor(session, "tool_records")
    session.exec(
        text(
            """
            INSERT INTO statistics_tool_hourly (bucket_time, tool_name, call_count)
            SELECT
                datetime(strftime('%Y-%m-%d %H:00:00', timestamp)) AS bucket_time,
                COALESCE(tool_name, 'unknown') AS tool_name,
                COUNT(*) AS call_count
            FROM tool_records
            WHERE id > :last_id
              AND timestamp IS NOT NULL
            GROUP BY bucket_time, COALESCE(tool_name, 'unknown')
            ON CONFLICT(bucket_time, tool_name) DO UPDATE SET
                call_count = statistics_tool_hourly.call_count + excluded.call_count
            """
        ),
        params={"last_id": last_id},
    )
    _set_cursor_to_table_max(session, "tool_records", "tool_records")


def _aggregate_model_usage(session) -> None:
    last_id = _get_cursor(session, "model_usage")
    session.exec(
        text(
            """
            INSERT INTO statistics_model_hourly (
                bucket_time, request_type, module_name, provider_name, model_name,
                request_count, prompt_tokens, completion_tokens, total_tokens, cost,
                time_cost_sum, time_cost_sq_sum
            )
            SELECT
                datetime(strftime('%Y-%m-%d %H:00:00', timestamp)) AS bucket_time,
                COALESCE(request_type, 'unknown') AS request_type,
                CASE
                    WHEN instr(COALESCE(request_type, 'unknown'), '.') > 0
                    THEN substr(COALESCE(request_type, 'unknown'), 1, instr(COALESCE(request_type, 'unknown'), '.') - 1)
                    ELSE COALESCE(request_type, 'unknown')
                END AS module_name,
                COALESCE(model_api_provider_name, 'unknown') AS provider_name,
                COALESCE(model_assign_name, model_name, 'unknown') AS model_name,
                COUNT(*) AS request_count,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM(cost), 0) AS cost,
                COALESCE(SUM(time_cost), 0) AS time_cost_sum,
                COALESCE(SUM(time_cost * time_cost), 0) AS time_cost_sq_sum
            FROM llm_usage
            WHERE id > :last_id
              AND timestamp IS NOT NULL
            GROUP BY
                datetime(strftime('%Y-%m-%d %H:00:00', timestamp)),
                COALESCE(request_type, 'unknown'),
                COALESCE(model_api_provider_name, 'unknown'),
                COALESCE(model_assign_name, model_name, 'unknown')
            ON CONFLICT(bucket_time, request_type, model_name, provider_name) DO UPDATE SET
                request_count = statistics_model_hourly.request_count + excluded.request_count,
                prompt_tokens = statistics_model_hourly.prompt_tokens + excluded.prompt_tokens,
                completion_tokens = statistics_model_hourly.completion_tokens + excluded.completion_tokens,
                total_tokens = statistics_model_hourly.total_tokens + excluded.total_tokens,
                cost = statistics_model_hourly.cost + excluded.cost,
                time_cost_sum = statistics_model_hourly.time_cost_sum + excluded.time_cost_sum,
                time_cost_sq_sum = statistics_model_hourly.time_cost_sq_sum + excluded.time_cost_sq_sum
            """
        ),
        params={"last_id": last_id},
    )
    _set_cursor_to_table_max(session, "model_usage", "llm_usage")


def _get_cursor(session, source_name: str) -> int:
    row = session.exec(
        text("SELECT COALESCE(last_processed_id, 0) FROM statistics_aggregation_cursors WHERE source_name = :source"),
        params={"source": source_name},
    ).first()
    return int(row[0]) if row else 0


def _set_cursor_to_table_max(session, source_name: str, table_name: str) -> None:
    row = session.exec(text(f"SELECT COALESCE(MAX(id), 0) FROM {table_name}")).first()
    max_id = int(row[0]) if row else 0
    session.exec(
        text(
            """
            INSERT INTO statistics_aggregation_cursors (source_name, last_processed_id, updated_at)
            VALUES (:source_name, :last_processed_id, CURRENT_TIMESTAMP)
            ON CONFLICT(source_name) DO UPDATE SET
                last_processed_id = excluded.last_processed_id,
                updated_at = excluded.updated_at
            """
        ),
        params={"source_name": source_name, "last_processed_id": max_id},
    )


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
