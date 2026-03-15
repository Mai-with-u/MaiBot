from datetime import datetime
from typing import Any

import json
import traceback

from sqlalchemy import and_, func, not_, or_
from sqlmodel import col, select

from src.chat.message_receive.message import SessionMessage
from src.common.database.database import get_db_session
from src.common.database.database_model import Messages
from src.common.logger import get_logger

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


def _message_to_instance(message: Messages) -> SessionMessage:
    return SessionMessage.from_db_instance(message)


def _coerce_datetime(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)
    return value


def _resolve_field(field_name: str) -> Any | None:
    if field_name in FIELD_MAP:
        return FIELD_MAP[field_name]
    if hasattr(Messages, field_name):
        return getattr(Messages, field_name)
    return None


def _build_message_conditions(
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    group_id: str | None = None,
    platform: str | None = None,
    message_id: str | None = None,
    reply_to: str | None = None,
    start_time: float | None = None,
    end_time: float | None = None,
    before_time: float | None = None,
    after_time: float | None = None,
    has_reply_to: bool | None = None,
) -> list[Any]:
    conditions: list[Any] = [Messages.message_id != "notice"]

    if session_id is not None:
        conditions.append(Messages.session_id == session_id)
    if user_id is not None:
        conditions.append(Messages.user_id == user_id)
    if group_id is not None:
        conditions.append(Messages.group_id == group_id)
    if platform is not None:
        conditions.append(Messages.platform == platform)
    if message_id is not None:
        conditions.append(Messages.message_id == message_id)
    if reply_to is not None:
        conditions.append(Messages.reply_to == reply_to)
    if start_time is not None:
        conditions.append(Messages.timestamp >= _coerce_datetime(start_time))
    if end_time is not None:
        conditions.append(Messages.timestamp <= _coerce_datetime(end_time))
    if before_time is not None:
        conditions.append(Messages.timestamp < _coerce_datetime(before_time))
    if after_time is not None:
        conditions.append(Messages.timestamp > _coerce_datetime(after_time))
    if has_reply_to is True:
        conditions.append(col(Messages.reply_to).is_not(None))
    elif has_reply_to is False:
        conditions.append(col(Messages.reply_to).is_(None))

    return conditions


def find_messages(
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    group_id: str | None = None,
    platform: str | None = None,
    message_id: str | None = None,
    reply_to: str | None = None,
    start_time: float | None = None,
    end_time: float | None = None,
    before_time: float | None = None,
    after_time: float | None = None,
    sort: list[tuple[str, int]] | None = None,
    limit: int = 0,
    limit_mode: str = "latest",
    filter_bot: bool = False,
    filter_command: bool = False,
    filter_intercept_message_level: int | None = None,
) -> list[SessionMessage]:
    """
    根据提供的过滤器、排序和限制条件查找消息。

    Args:
        session_id: 会话 ID 过滤。
        user_id: 用户 ID 过滤。
        group_id: 群 ID 过滤。
        platform: 平台过滤。
        message_id: 消息 ID 过滤。
        reply_to: 回复目标消息 ID 过滤。
        start_time: 起始时间，闭区间下界。
        end_time: 结束时间，闭区间上界。
        before_time: 严格早于该时间。
        after_time: 严格晚于该时间。
        sort: 排序条件列表，例如 [('time', 1)] (1 for asc, -1 for desc)。仅在 limit 为 0 时生效。
        limit: 返回的最大文档数，0表示不限制。
        limit_mode: 当 limit > 0 时生效。 'earliest' 表示获取最早的记录， 'latest' 表示获取最新的记录（结果仍按时间正序排列）。默认为 'latest'。

    Returns:
        消息字典列表，如果出错则返回空列表。
    """
    try:
        conditions = _build_message_conditions(
            session_id=session_id,
            user_id=user_id,
            group_id=group_id,
            platform=platform,
            message_id=message_id,
            reply_to=reply_to,
            start_time=start_time,
            end_time=end_time,
            before_time=before_time,
            after_time=after_time,
        )
        if filter_bot:
            from src.chat.utils.utils import get_all_bot_accounts, get_bot_account

            bot_accounts = get_all_bot_accounts()
            exclusion_conditions: list[Any] = []
            if bot_accounts:
                exclusion_conditions.append(
                    or_(
                        *[
                            and_(Messages.platform == platform_name, Messages.user_id == account)
                            for platform_name, account in bot_accounts.items()
                        ]
                    )
                )

            # 兼容旧数据：历史机器人消息在所有平台上都使用 QQ 账号作为 user_id 存储，
            # 例如旧 Telegram bot 消息的 (platform="telegram", user_id=qq_account)。
            # plan 建议的 ("", qq_account) pair 只能覆盖空 platform 行，无法覆盖这种情况。
            # 因此这里使用全局 user_id 匹配作为临时方案，待 DB 迁移后应移除此兜底。
            if qq_fallback := get_bot_account("qq"):
                exclusion_conditions.append(Messages.user_id == qq_fallback)

            if exclusion_conditions:
                conditions.append(not_(or_(*exclusion_conditions)))
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
            "使用 SQLModel 查找消息失败 "
            f"(session_id={session_id}, user_id={user_id}, group_id={group_id}, platform={platform}, "
            f"message_id={message_id}, reply_to={reply_to}, start_time={start_time}, end_time={end_time}, "
            f"before_time={before_time}, after_time={after_time}, sort={sort}, limit={limit}, limit_mode={limit_mode}): {e}\n"
            + traceback.format_exc()
        )
        logger.error(log_message)
        return []


def count_messages(
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    group_id: str | None = None,
    platform: str | None = None,
    message_id: str | None = None,
    reply_to: str | None = None,
    start_time: float | None = None,
    end_time: float | None = None,
    before_time: float | None = None,
    after_time: float | None = None,
    has_reply_to: bool | None = None,
) -> int:
    """
    根据提供的过滤器计算消息数量。

    Args:
        session_id: 会话 ID 过滤。
        user_id: 用户 ID 过滤。
        group_id: 群 ID 过滤。
        platform: 平台过滤。
        message_id: 消息 ID 过滤。
        reply_to: 回复目标消息 ID 过滤。
        start_time: 起始时间，闭区间下界。
        end_time: 结束时间，闭区间上界。
        before_time: 严格早于该时间。
        after_time: 严格晚于该时间。
        has_reply_to: 是否要求存在 reply_to 字段。

    Returns:
        符合条件的消息数量，如果出错则返回 0。
    """
    try:
        conditions = _build_message_conditions(
            session_id=session_id,
            user_id=user_id,
            group_id=group_id,
            platform=platform,
            message_id=message_id,
            reply_to=reply_to,
            start_time=start_time,
            end_time=end_time,
            before_time=before_time,
            after_time=after_time,
            has_reply_to=has_reply_to,
        )
        statement = select(func.count()).select_from(Messages).where(*conditions)
        with get_db_session() as session:
            result = session.exec(statement).one()
        return int(result or 0)
    except Exception as e:
        log_message = (
            "使用 SQLModel 计数消息失败 "
            f"(session_id={session_id}, user_id={user_id}, group_id={group_id}, platform={platform}, "
            f"message_id={message_id}, reply_to={reply_to}, start_time={start_time}, end_time={end_time}, "
            f"before_time={before_time}, after_time={after_time}, has_reply_to={has_reply_to}): {e}\n{traceback.format_exc()}"
        )
        logger.error(log_message)
        return 0
