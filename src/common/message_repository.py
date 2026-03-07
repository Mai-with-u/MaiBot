import traceback
from datetime import datetime
from typing import Any

import json

from sqlalchemy import func
from sqlmodel import col, select

from src.common.database.database import get_db_session
from src.common.database.database_model import Messages
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger(__name__)


FIELD_MAP: dict[str, Any] = {
    "time": Messages.timestamp,
    "timestamp": Messages.timestamp,
    "chat_id": Messages.session_id,
    "session_id": Messages.session_id,
    "user_id": Messages.user_id,
    "message_id": Messages.message_id,
    "group_id": Messages.group_id,
    "platform": Messages.platform,
    "is_command": Messages.is_command,
    "is_mentioned": Messages.is_mentioned,
    "is_at": Messages.is_at,
    "is_emoji": Messages.is_emoji,
    "is_picid": Messages.is_picture,
    "is_picture": Messages.is_picture,
    "reply_to": Messages.reply_to,
}


def _parse_additional_config(message: Messages) -> dict[str, Any]:
    if not message.additional_config:
        return {}
    try:
        parsed = json.loads(message.additional_config)
    except (json.JSONDecodeError, TypeError):
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _normalize_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _message_to_instance(message: Messages) -> DatabaseMessages:
    config = _parse_additional_config(message)
    timestamp_value = message.timestamp
    if isinstance(timestamp_value, datetime):
        time_value = timestamp_value.timestamp()
    else:
        time_value = float(timestamp_value)
    selected_expressions = _normalize_optional_str(config.get("selected_expressions"))
    priority_info = _normalize_optional_str(config.get("priority_info"))
    return DatabaseMessages(
        message_id=message.message_id,
        time=time_value,
        chat_id=message.session_id,
        reply_to=message.reply_to,
        interest_value=config.get("interest_value"),
        key_words=_normalize_optional_str(config.get("key_words")),
        key_words_lite=_normalize_optional_str(config.get("key_words_lite")),
        is_mentioned=message.is_mentioned,
        is_at=message.is_at,
        reply_probability_boost=config.get("reply_probability_boost"),
        processed_plain_text=message.processed_plain_text,
        display_message=message.display_message,
        priority_mode=_normalize_optional_str(config.get("priority_mode")),
        priority_info=priority_info,
        additional_config=message.additional_config,
        is_emoji=message.is_emoji,
        is_picid=message.is_picture,
        is_command=message.is_command,
        intercept_message_level=config.get("intercept_message_level", 0),
        is_notify=message.is_notify,
        selected_expressions=selected_expressions,
        user_id=message.user_id,
        user_nickname=message.user_nickname,
        user_cardname=message.user_cardname,
        user_platform=message.platform,
        chat_info_group_id=message.group_id,
        chat_info_group_name=message.group_name,
        chat_info_group_platform=message.platform,
        chat_info_user_id=message.user_id,
        chat_info_user_nickname=message.user_nickname,
        chat_info_user_cardname=message.user_cardname,
        chat_info_user_platform=message.platform,
        chat_info_stream_id=message.session_id,
        chat_info_platform=message.platform,
        chat_info_create_time=0.0,
        chat_info_last_active_time=0.0,
    )


def _coerce_datetime(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)
    return value


def _cast_value_for_field(field: Any, value: Any) -> Any:
    if field is Messages.timestamp:
        return _coerce_datetime(value)
    return value


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _resolve_field(field_name: str) -> Any | None:
    if field_name in FIELD_MAP:
        return FIELD_MAP[field_name]
    if hasattr(Messages, field_name):
        return getattr(Messages, field_name)
    return None


def find_messages(
    message_filter: dict[str, Any],
    sort: list[tuple[str, int]] | None = None,
    limit: int = 0,
    limit_mode: str = "latest",
    filter_bot: bool = False,
    filter_command: bool = False,
    filter_intercept_message_level: int | None = None,
) -> list[DatabaseMessages]:
    """
    根据提供的过滤器、排序和限制条件查找消息。

    Args:
        message_filter: 查询过滤器字典，键为模型字段名，值为期望值或包含操作符的字典 (例如 {'$gt': value}).
        sort: 排序条件列表，例如 [('time', 1)] (1 for asc, -1 for desc)。仅在 limit 为 0 时生效。
        limit: 返回的最大文档数，0表示不限制。
        limit_mode: 当 limit > 0 时生效。 'earliest' 表示获取最早的记录， 'latest' 表示获取最新的记录（结果仍按时间正序排列）。默认为 'latest'。

    Returns:
        消息字典列表，如果出错则返回空列表。
    """
    try:
        conditions: list[Any] = []
        if message_filter:
            for key, value in message_filter.items():
                field = _resolve_field(key)
                if field is None:
                    logger.warning(f"过滤器键 '{key}' 在 Messages 模型中未找到。将跳过此条件。")
                    continue
                if isinstance(value, dict):
                    for op, op_value in value.items():
                        coerced_value = _coerce_datetime(op_value) if field is Messages.timestamp else op_value
                        if op == "$gt":
                            conditions.append(field > coerced_value)
                        elif op == "$lt":
                            conditions.append(field < coerced_value)
                        elif op == "$gte":
                            conditions.append(field >= coerced_value)
                        elif op == "$lte":
                            conditions.append(field <= coerced_value)
                        elif op == "$ne":
                            conditions.append(field != coerced_value)
                        elif op == "$in":
                            conditions.append(field.in_(_ensure_list(coerced_value)))
                        elif op == "$nin":
                            conditions.append(field.not_in(_ensure_list(coerced_value)))
                        else:
                            logger.warning(f"过滤器中遇到未知操作符 '{op}' (字段: '{key}')。将跳过此操作符。")
                else:
                    coerced_value = _coerce_datetime(value) if field is Messages.timestamp else value
                    conditions.append(field == coerced_value)

        conditions.append(Messages.message_id != "notice")
        if filter_bot:
            conditions.append(Messages.user_id != global_config.bot.qq_account)
        if filter_command:
            conditions.append(Messages.is_command == False)  # noqa: E712

        statement = select(Messages).where(*conditions)
        if limit > 0:
            if limit_mode == "earliest":
                statement = statement.order_by(col(Messages.timestamp)).limit(limit)
                with get_db_session() as session:
                    results = list(session.exec(statement).all())
            else:
                statement = statement.order_by(col(Messages.timestamp).desc()).limit(limit)
                with get_db_session() as session:
                    results = list(session.exec(statement).all())
                results = list(reversed(results))
        else:
            if sort:
                order_terms: list[Any] = []
                for field_name, direction in sort:
                    sort_field = _resolve_field(field_name)
                    if sort_field is None:
                        logger.warning(f"排序字段 '{field_name}' 在 Messages 模型中未找到。将跳过此排序条件。")
                        continue
                    order_terms.append(sort_field.asc() if direction == 1 else sort_field.desc())
                if order_terms:
                    statement = statement.order_by(*order_terms)
            with get_db_session() as session:
                results = list(session.exec(statement).all())

        if filter_intercept_message_level is not None:
            filtered_results = []
            for msg in results:
                config = _parse_additional_config(msg)
                if config.get("intercept_message_level", 0) <= filter_intercept_message_level:
                    filtered_results.append(msg)
            results = filtered_results

        return [_message_to_instance(msg) for msg in results]
    except Exception as e:
        log_message = (
            f"使用 SQLModel 查找消息失败 (filter={message_filter}, sort={sort}, limit={limit}, limit_mode={limit_mode}): {e}\n"
            + traceback.format_exc()
        )
        logger.error(log_message)
        return []


def count_messages(message_filter: dict[str, Any]) -> int:
    """
    根据提供的过滤器计算消息数量。

    Args:
        message_filter: 查询过滤器字典，键为模型字段名，值为期望值或包含操作符的字典 (例如 {'$gt': value}).

    Returns:
        符合条件的消息数量，如果出错则返回 0。
    """
    try:
        conditions: list[Any] = []
        if message_filter:
            for key, value in message_filter.items():
                field = _resolve_field(key)
                if field is None:
                    logger.warning(f"计数时，过滤器键 '{key}' 在 Messages 模型中未找到。将跳过此条件。")
                    continue
                if isinstance(value, dict):
                    for op, op_value in value.items():
                        coerced_value = _coerce_datetime(op_value) if field is Messages.timestamp else op_value
                        if op == "$gt":
                            conditions.append(field > coerced_value)
                        elif op == "$lt":
                            conditions.append(field < coerced_value)
                        elif op == "$gte":
                            conditions.append(field >= coerced_value)
                        elif op == "$lte":
                            conditions.append(field <= coerced_value)
                        elif op == "$ne":
                            conditions.append(field != coerced_value)
                        elif op == "$in":
                            conditions.append(field.in_(_ensure_list(coerced_value)))
                        elif op == "$nin":
                            conditions.append(field.not_in(_ensure_list(coerced_value)))
                        else:
                            logger.warning(f"计数时，过滤器中遇到未知操作符 '{op}' (字段: '{key}')。将跳过此操作符。")
                else:
                    coerced_value = _coerce_datetime(value) if field is Messages.timestamp else value
                    conditions.append(field == coerced_value)

        conditions.append(Messages.message_id != "notice")
        statement = select(func.count()).select_from(Messages).where(*conditions)
        with get_db_session() as session:
            result = session.exec(statement).one()
        return int(result or 0)
    except Exception as e:
        log_message = f"使用 SQLModel 计数消息失败 (message_filter={message_filter}): {e}\n{traceback.format_exc()}"
        logger.error(log_message)
        return 0
