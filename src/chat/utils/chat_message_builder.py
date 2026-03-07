import time
import random
import re
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional, Callable
from rich.traceback import install

from sqlmodel import select, col
from src.config.config import global_config
from src.common.logger import get_logger
from src.common.message_repository import find_messages, count_messages
from src.common.data_models.database_data_model import DatabaseMessages, DatabaseActionRecords
from src.common.database.database import get_db_session
from src.common.database.database_model import ActionRecord, Images
from src.person_info.person_info import Person, get_person_id
from src.chat.utils.utils import translate_timestamp_to_human_readable, assign_message_ids, is_bot_self

install(extra_lines=3)
logger = get_logger("chat_message_builder")


def replace_user_references(
    content: Optional[str],
    platform: str,
    name_resolver: Optional[Callable[[str, str], str]] = None,
    replace_bot_name: bool = True,
) -> str:
    """
    替换内容中的用户引用格式，包括回复<aaa:bbb>和@<aaa:bbb>格式

    Args:
        content: 要处理的内容字符串
        platform: 平台标识
        name_resolver: 名称解析函数，接收(platform, user_id)参数，返回用户名称
                       如果为None，则使用默认的person_info_manager
        replace_bot_name: 是否将机器人的user_id替换为"机器人昵称(你)"

    Returns:
        str: 处理后的内容字符串
    """
    if not content:
        return ""
    if name_resolver is None:

        def default_resolver(platform: str, user_id: str) -> str:
            # 检查是否是机器人自己（支持多平台，包括 WebUI）
            if replace_bot_name and is_bot_self(platform, user_id):
                return f"{global_config.bot.nickname}(你)"
            person = Person(platform=platform, user_id=user_id)
            return person.person_name or user_id  # type: ignore

        name_resolver = default_resolver

    # 处理回复<aaa:bbb>格式
    reply_pattern = r"回复<([^:<>]+):([^:<>]+)>"
    match = re.search(reply_pattern, content)
    if match:
        aaa = match[1]
        bbb = match[2]
        try:
            # 检查是否是机器人自己（支持多平台，包括 WebUI）
            if replace_bot_name and is_bot_self(platform, bbb):
                reply_person_name = f"{global_config.bot.nickname}(你)"
            else:
                reply_person_name = name_resolver(platform, bbb) or aaa
            content = re.sub(reply_pattern, f"回复 {reply_person_name}", content, count=1)
        except Exception:
            # 如果解析失败，使用原始昵称
            content = re.sub(reply_pattern, f"回复 {aaa}", content, count=1)

    # 处理@<aaa:bbb>格式
    at_pattern = r"@<([^:<>]+):([^:<>]+)>"
    at_matches = list(re.finditer(at_pattern, content))
    if at_matches:
        new_content = ""
        last_end = 0
        for m in at_matches:
            new_content += content[last_end : m.start()]
            aaa = m.group(1)
            bbb = m.group(2)
            try:
                # 检查是否是机器人自己
                if replace_bot_name and bbb == global_config.bot.qq_account:
                    at_person_name = f"{global_config.bot.nickname}(你)"
                else:
                    at_person_name = name_resolver(platform, bbb) or aaa
                new_content += f"@{at_person_name}"
            except Exception:
                # 如果解析失败，使用原始昵称
                new_content += f"@{aaa}"
            last_end = m.end()
        new_content += content[last_end:]
        content = new_content

    # Telegram 文本 @username 的显示映射交由适配器或平台层处理；此处不做硬编码替换

    return content


def get_raw_msg_by_timestamp(timestamp_start: float, timestamp_end: float, limit: int = 0, limit_mode: str = "latest"):
    """
    获取从指定时间戳到指定时间戳的消息，按时间升序排序，返回消息列表
    limit: 限制返回的消息数量，0为不限制
    limit_mode: 当 limit > 0 时生效。 'earliest' 表示获取最早的记录， 'latest' 表示获取最新的记录。默认为 'latest'。
    """
    filter_query = {"time": {"$gt": timestamp_start, "$lt": timestamp_end}}
    # 只有当 limit 为 0 时才应用外部 sort
    sort_order = [("time", 1)] if limit == 0 else None
    return find_messages(message_filter=filter_query, sort=sort_order, limit=limit, limit_mode=limit_mode)


def get_raw_msg_by_timestamp_with_chat(
    chat_id: str,
    timestamp_start: float,
    timestamp_end: float,
    limit: int = 0,
    limit_mode: str = "latest",
    filter_bot=False,
    filter_command=False,
    filter_intercept_message_level: Optional[int] = None,
) -> List[DatabaseMessages]:
    """获取在特定聊天从指定时间戳到指定时间戳的消息，按时间升序排序，返回消息列表
    limit: 限制返回的消息数量，0为不限制
    limit_mode: 当 limit > 0 时生效。 'earliest' 表示获取最早的记录， 'latest' 表示获取最新的记录。默认为 'latest'。
    """
    filter_query = {"chat_id": chat_id, "time": {"$gt": timestamp_start, "$lt": timestamp_end}}
    # 只有当 limit 为 0 时才应用外部 sort
    sort_order = [("time", 1)] if limit == 0 else None
    # 直接将 limit_mode 传递给 find_messages
    # print(f"get_raw_msg_by_timestamp_with_chat: {chat_id}, {timestamp_start}, {timestamp_end}, {limit}, {limit_mode}, {filter_bot}, {filter_command}")
    return find_messages(
        message_filter=filter_query,
        sort=sort_order,
        limit=limit,
        limit_mode=limit_mode,
        filter_bot=filter_bot,
        filter_command=filter_command,
        filter_intercept_message_level=filter_intercept_message_level,
    )


def get_raw_msg_by_timestamp_with_chat_inclusive(
    chat_id: str,
    timestamp_start: float,
    timestamp_end: float,
    limit: int = 0,
    limit_mode: str = "latest",
    filter_bot=False,
    filter_command=False,
    filter_intercept_message_level: Optional[int] = None,
) -> List[DatabaseMessages]:
    """获取在特定聊天从指定时间戳到指定时间戳的消息（包含边界），按时间升序排序，返回消息列表
    limit: 限制返回的消息数量，0为不限制
    limit_mode: 当 limit > 0 时生效。 'earliest' 表示获取最早的记录， 'latest' 表示获取最新的记录。默认为 'latest'。
    """
    filter_query = {"chat_id": chat_id, "time": {"$gte": timestamp_start, "$lte": timestamp_end}}
    # 只有当 limit 为 0 时才应用外部 sort
    sort_order = [("time", 1)] if limit == 0 else None
    # 直接将 limit_mode 传递给 find_messages
    return find_messages(
        message_filter=filter_query,
        sort=sort_order,
        limit=limit,
        limit_mode=limit_mode,
        filter_bot=filter_bot,
        filter_command=filter_command,
        filter_intercept_message_level=filter_intercept_message_level,
    )


def get_raw_msg_by_timestamp_with_chat_users(
    chat_id: str,
    timestamp_start: float,
    timestamp_end: float,
    person_ids: List[str],
    limit: int = 0,
    limit_mode: str = "latest",
) -> List[DatabaseMessages]:
    """获取某些特定用户在特定聊天从指定时间戳到指定时间戳的消息，按时间升序排序，返回消息列表
    limit: 限制返回的消息数量，0为不限制
    limit_mode: 当 limit > 0 时生效。 'earliest' 表示获取最早的记录， 'latest' 表示获取最新的记录。默认为 'latest'。
    """
    filter_query = {
        "chat_id": chat_id,
        "time": {"$gt": timestamp_start, "$lt": timestamp_end},
        "user_id": {"$in": person_ids},
    }
    # 只有当 limit 为 0 时才应用外部 sort
    sort_order = [("time", 1)] if limit == 0 else None
    return find_messages(message_filter=filter_query, sort=sort_order, limit=limit, limit_mode=limit_mode)


def get_actions_by_timestamp_with_chat(
    chat_id: str,
    timestamp_start: float = 0,
    timestamp_end: float = time.time(),
    limit: int = 0,
    limit_mode: str = "latest",
) -> List[DatabaseActionRecords]:
    """获取在特定聊天从指定时间戳到指定时间戳的动作记录，按时间升序排序，返回动作记录列表"""
    with get_db_session() as session:
        statement = (
            select(ActionRecord)
            .where((col(ActionRecord.session_id) == chat_id))
            .where(col(ActionRecord.timestamp) > datetime.fromtimestamp(timestamp_start))
            .where(col(ActionRecord.timestamp) < datetime.fromtimestamp(timestamp_end))
        )

        if limit > 0:
            if limit_mode == "latest":
                statement = statement.order_by(col(ActionRecord.timestamp).desc()).limit(limit)
                actions = list(session.exec(statement).all())
                actions = list(reversed(actions))
            else:
                statement = statement.order_by(col(ActionRecord.timestamp)).limit(limit)
                actions = list(session.exec(statement).all())
        else:
            statement = statement.order_by(col(ActionRecord.timestamp))
            actions = session.exec(statement).all()
    return [
        DatabaseActionRecords(
            action_id=action.action_id,
            time=action.timestamp.timestamp(),
            action_name=action.action_name,
            action_data=action.action_data or "{}",
            action_done=True,
            action_build_into_prompt=bool(action.action_display_prompt),
            action_prompt_display=action.action_display_prompt or "",
            chat_id=action.session_id,
            chat_info_stream_id=action.session_id,
            chat_info_platform=global_config.bot.platform,
            action_reasoning=action.action_reasoning or "",
        )
        for action in actions
    ]


def get_actions_by_timestamp_with_chat_inclusive(
    chat_id: str, timestamp_start: float, timestamp_end: float, limit: int = 0, limit_mode: str = "latest"
) -> List[Dict[str, Any]]:
    """获取在特定聊天从指定时间戳到指定时间戳的动作记录（包含边界），按时间升序排序，返回动作记录列表"""
    with get_db_session() as session:
        statement = (
            select(ActionRecord)
            .where((col(ActionRecord.session_id) == chat_id))
            .where(col(ActionRecord.timestamp) >= datetime.fromtimestamp(timestamp_start))
            .where(col(ActionRecord.timestamp) <= datetime.fromtimestamp(timestamp_end))
        )

        if limit > 0:
            if limit_mode == "latest":
                statement = statement.order_by(col(ActionRecord.timestamp).desc()).limit(limit)
                actions = list(session.exec(statement).all())
                actions = list(reversed(actions))
            else:
                statement = statement.order_by(col(ActionRecord.timestamp)).limit(limit)
                actions = list(session.exec(statement).all())
        else:
            statement = statement.order_by(col(ActionRecord.timestamp))
            actions = session.exec(statement).all()

    return [action.model_dump() for action in actions]


def get_raw_msg_by_timestamp_random(
    timestamp_start: float, timestamp_end: float, limit: int = 0, limit_mode: str = "latest"
) -> List[DatabaseMessages]:
    """
    先在范围时间戳内随机选择一条消息，取得消息的chat_id，然后根据chat_id获取该聊天在指定时间戳范围内的消息
    """
    # 获取所有消息，只取chat_id字段
    all_msgs = get_raw_msg_by_timestamp(timestamp_start, timestamp_end)
    if not all_msgs:
        return []
    # 随机选一条
    msg = random.choice(all_msgs)
    chat_id = msg.chat_id
    timestamp_start = msg.time
    # 用 chat_id 获取该聊天在指定时间戳范围内的消息
    return get_raw_msg_by_timestamp_with_chat(chat_id, timestamp_start, timestamp_end, limit, "earliest")


def get_raw_msg_by_timestamp_with_users(
    timestamp_start: float, timestamp_end: float, person_ids: List[str], limit: int = 0, limit_mode: str = "latest"
) -> List[DatabaseMessages]:
    """获取某些特定用户在 *所有聊天* 中从指定时间戳到指定时间戳的消息，按时间升序排序，返回消息列表
    limit: 限制返回的消息数量，0为不限制
    limit_mode: 当 limit > 0 时生效。 'earliest' 表示获取最早的记录， 'latest' 表示获取最新的记录。默认为 'latest'。
    """
    filter_query = {"time": {"$gt": timestamp_start, "$lt": timestamp_end}, "user_id": {"$in": person_ids}}
    # 只有当 limit 为 0 时才应用外部 sort
    sort_order = [("time", 1)] if limit == 0 else None
    return find_messages(message_filter=filter_query, sort=sort_order, limit=limit, limit_mode=limit_mode)


def get_raw_msg_before_timestamp(timestamp: float, limit: int = 0) -> List[DatabaseMessages]:
    """获取指定时间戳之前的消息，按时间升序排序，返回消息列表
    limit: 限制返回的消息数量，0为不限制
    """
    filter_query = {"time": {"$lt": timestamp}}
    sort_order = [("time", 1)]
    return find_messages(message_filter=filter_query, sort=sort_order, limit=limit)


def get_raw_msg_before_timestamp_with_chat(
    chat_id: str, timestamp: float, limit: int = 0, filter_intercept_message_level: Optional[int] = None
) -> List[DatabaseMessages]:
    """获取指定时间戳之前的消息，按时间升序排序，返回消息列表
    limit: 限制返回的消息数量，0为不限制
    """
    filter_query = {"chat_id": chat_id, "time": {"$lt": timestamp}}
    sort_order = [("time", 1)]
    return find_messages(
        message_filter=filter_query,
        sort=sort_order,
        limit=limit,
        filter_intercept_message_level=filter_intercept_message_level,
    )


def get_raw_msg_before_timestamp_with_users(
    timestamp: float, person_ids: List[str], limit: int = 0
) -> List[DatabaseMessages]:
    """获取指定时间戳之前的消息，按时间升序排序，返回消息列表
    limit: 限制返回的消息数量，0为不限制
    """
    filter_query = {"time": {"$lt": timestamp}, "user_id": {"$in": person_ids}}
    sort_order = [("time", 1)]
    return find_messages(message_filter=filter_query, sort=sort_order, limit=limit)


def num_new_messages_since(chat_id: str, timestamp_start: float = 0.0, timestamp_end: Optional[float] = None) -> int:
    """
    检查特定聊天从 timestamp_start (不含) 到 timestamp_end (不含) 之间有多少新消息。
    如果 timestamp_end 为 None，则检查从 timestamp_start (不含) 到当前时间的消息。
    """
    # 确定有效的结束时间戳
    _timestamp_end = timestamp_end if timestamp_end is not None else time.time()

    # 确保 timestamp_start < _timestamp_end
    if timestamp_start >= _timestamp_end:
        # logger.warning(f"timestamp_start ({timestamp_start}) must be less than _timestamp_end ({_timestamp_end}). Returning 0.")
        return 0  # 起始时间大于等于结束时间，没有新消息

    filter_query = {"chat_id": chat_id, "time": {"$gt": timestamp_start, "$lt": _timestamp_end}}
    return count_messages(message_filter=filter_query)


def num_new_messages_since_with_users(
    chat_id: str, timestamp_start: float, timestamp_end: float, person_ids: List[str]
) -> int:
    """检查某些特定用户在特定聊天在指定时间戳之间有多少新消息"""
    if not person_ids:  # 保持空列表检查
        return 0
    filter_query = {
        "chat_id": chat_id,
        "time": {"$gt": timestamp_start, "$lt": timestamp_end},
        "user_id": {"$in": person_ids},
    }
    return count_messages(message_filter=filter_query)


def _build_readable_messages_internal(
    messages: List[DatabaseMessages],
    replace_bot_name: bool = True,
    timestamp_mode: str = "relative",
    truncate: bool = False,
    pic_id_mapping: Optional[Dict[str, str]] = None,
    pic_counter: int = 1,
    show_pic: bool = True,
    message_id_list: Optional[List[Tuple[str, DatabaseMessages]]] = None,
    pic_single: bool = False,
    long_time_notice: bool = False,
) -> Tuple[str, List[Tuple[float, str, str]], Dict[str, str], int]:
    # sourcery skip: use-getitem-for-re-match-groups
    """
    内部辅助函数，构建可读消息字符串和原始消息详情列表。

    Args:
        messages: 消息字典列表。
        replace_bot_name: 是否将机器人的 user_id 替换为 "我"。
        merge_messages: 是否合并来自同一用户的连续消息。
        timestamp_mode: 时间戳的显示模式 ('relative', 'absolute', etc.)。传递给 translate_timestamp_to_human_readable。
        truncate: 是否根据消息的新旧程度截断过长的消息内容。
        pic_id_mapping: 图片ID映射字典，如果为None则创建新的
        pic_counter: 图片计数器起始值

    Returns:
        包含格式化消息的字符串、原始消息详情列表、图片映射字典和更新后的计数器的元组。
    """
    if not messages:
        return "", [], pic_id_mapping or {}, pic_counter

    detailed_messages_raw: List[Tuple[float, str, str, bool]] = []

    # 使用传入的映射字典，如果没有则创建新的
    if pic_id_mapping is None:
        pic_id_mapping = {}
    current_pic_counter = pic_counter
    pic_description_cache: Dict[str, str] = {}

    # 创建时间戳到消息ID的映射，用于在消息前添加[id]标识符
    timestamp_to_id_mapping: Dict[float, str] = {}
    if message_id_list:
        for msg_id, msg in message_id_list:
            timestamp = msg.time
            if timestamp is not None:
                timestamp_to_id_mapping[timestamp] = msg_id

    def process_pic_ids(content: Optional[str]) -> str:
        """处理内容中的图片ID，将其替换为[图片x]格式"""
        if content is None:
            logger.warning("Content is None when processing pic IDs.")
            raise ValueError("Content is None")

        # 匹配 [picid:xxxxx] 格式
        pic_pattern = r"\[picid:([^\]]+)\]"

        def replace_pic_id(match: re.Match[str]) -> str:
            nonlocal current_pic_counter
            nonlocal pic_counter
            pic_id = match.group(1)
            if pic_single:
                if pic_id not in pic_description_cache:
                    description = "内容正在阅读，请稍等"
                    try:
                        with get_db_session() as session:
                            image = session.get(Images, int(pic_id)) if pic_id.isdigit() else None
                        if image and image.description:
                            description = image.description
                    except Exception:
                        pass
                    pic_description_cache[pic_id] = description
                return f"[图片：{pic_description_cache[pic_id]}]"
            if pic_id not in pic_id_mapping:
                pic_id_mapping[pic_id] = f"图片{current_pic_counter}"
                current_pic_counter += 1

            return f"[{pic_id_mapping[pic_id]}]"

        return re.sub(pic_pattern, replace_pic_id, content)

    # 1: 获取发送者信息并提取消息组件
    for message in messages:
        user_info = message.user_info
        platform = user_info.platform
        user_id = user_info.user_id
        user_nickname = user_info.user_nickname
        user_cardname = user_info.user_cardname

        timestamp = message.time
        content = message.display_message or message.processed_plain_text or ""

        # 处理图片ID
        if show_pic:
            content = process_pic_ids(content)

        # 检查必要信息是否存在
        if not all([platform, user_id, timestamp is not None]):
            continue

        person = Person(platform=platform, user_id=user_id)
        # 根据 replace_bot_name 参数决定是否替换机器人名称
        person_name = (
            person.person_name or f"{user_nickname}" or (f"昵称：{user_cardname}" if user_cardname else "某人")
        )
        # 使用统一的 is_bot_self 函数判断是否是机器人自己（支持多平台，包括 WebUI）
        if replace_bot_name and is_bot_self(platform, user_id):
            person_name = f"{global_config.bot.nickname}(你)"

        # 使用独立函数处理用户引用格式
        if content := replace_user_references(content, platform, replace_bot_name=replace_bot_name):
            if getattr(message, "is_command", False):
                content = f"[is_command=True] {content}"
            detailed_messages_raw.append((timestamp, person_name, content, False))

    if not detailed_messages_raw:
        return "", [], pic_id_mapping, current_pic_counter

    detailed_messages_raw.sort(key=lambda x: x[0])  # 按时间戳(第一个元素)升序排序，越早的消息排在前面
    detailed_message: List[Tuple[float, str, str, bool]] = []

    # 2. 应用消息截断逻辑
    messages_count = len(detailed_messages_raw)
    if truncate and messages_count > 0:
        for i, (timestamp, name, content, is_action) in enumerate(detailed_messages_raw):
            # 对于动作记录，不进行截断
            if is_action:
                detailed_message.append((timestamp, name, content, is_action))
                continue

            percentile = i / messages_count  # 计算消息在列表中的位置百分比 (0 <= percentile < 1)
            original_len = len(content)
            limit = -1  # 默认不截断

            if percentile < 0.2:  # 60% 之前的消息 (即最旧的 60%)
                limit = 50
                replace_content = "......（记不清了）"
            elif percentile < 0.5:  # 60% 之前的消息 (即最旧的 60%)
                limit = 100
                replace_content = "......（有点记不清了）"
            elif percentile < 0.7:  # 60% 到 80% 之前的消息 (即中间的 20%)
                limit = 200
                replace_content = "......（内容太长了）"
            elif percentile <= 1.0:  # 80% 到 100% 之前的消息 (即较新的 20%)
                limit = 400
                replace_content = "......（内容太长了）"

            truncated_content = content
            if 0 < limit < original_len:
                truncated_content = f"{content[:limit]}{replace_content}"  # pyright: ignore[reportPossiblyUnboundVariable]

            detailed_message.append((timestamp, name, truncated_content, is_action))
    else:
        # 如果不截断，直接使用原始列表
        detailed_message = detailed_messages_raw

    # 3: 格式化为字符串
    output_lines: List[str] = []

    prev_timestamp: Optional[float] = None
    for timestamp, name, content, _is_action in detailed_message:
        # 检查是否需要插入长时间间隔提示
        if long_time_notice and prev_timestamp is not None:
            time_diff = timestamp - prev_timestamp
            time_diff_hours = time_diff / 3600

            # 检查是否跨天
            prev_date = time.strftime("%Y-%m-%d", time.localtime(prev_timestamp))
            current_date = time.strftime("%Y-%m-%d", time.localtime(timestamp))
            is_cross_day = prev_date != current_date

            # 如果间隔大于8小时或跨天，插入提示
            if time_diff_hours > 8 or is_cross_day:
                # 格式化日期为中文格式：xxxx年xx月xx日（去掉前导零）
                current_time_struct = time.localtime(timestamp)
                year = current_time_struct.tm_year
                month = current_time_struct.tm_mon
                day = current_time_struct.tm_mday
                date_str = f"{year}年{month}月{day}日"
                hours_str = f"{int(time_diff_hours)}h"
                notice = f"以下聊天开始时间：{date_str}。距离上一条消息过去了{hours_str}\n"
                output_lines.append(notice)

        readable_time = translate_timestamp_to_human_readable(timestamp, mode=timestamp_mode)

        # 查找消息id（如果有）并构建id_prefix
        message_id = timestamp_to_id_mapping.get(timestamp, "")
        id_prefix = f"[{message_id}]" if message_id else ""

        output_lines.append(f"{id_prefix}{readable_time}, {name}: {content}")
        output_lines.append("\n")
        prev_timestamp = timestamp

    formatted_string = "".join(output_lines).strip()

    # 返回格式化后的字符串、消息详情列表、图片映射字典和更新后的计数器
    return (
        formatted_string,
        [(t, n, c) for t, n, c, is_action in detailed_message if not is_action],
        pic_id_mapping,
        current_pic_counter,
    )


def build_pic_mapping_info(pic_id_mapping: Dict[str, str]) -> str:
    # sourcery skip: use-contextlib-suppress
    """
    构建图片映射信息字符串，显示图片的具体描述内容

    Args:
        pic_id_mapping: 图片ID到显示名称的映射字典

    Returns:
        格式化的映射信息字符串
    """
    if not pic_id_mapping:
        return ""

    mapping_lines = []

    # 按图片编号排序
    sorted_items = sorted(pic_id_mapping.items(), key=lambda x: int(x[1].replace("图片", "")))

    for pic_id, display_name in sorted_items:
        # 从数据库中获取图片描述
        description = "内容正在阅读，请稍等"
        try:
            with get_db_session() as session:
                image = session.get(Images, int(pic_id)) if pic_id.isdigit() else None
            if image and image.description:
                description = image.description
        except Exception:
            # 如果查询失败，保持默认描述
            pass

        mapping_lines.append(f"[{display_name}] 的内容：{description}")

    return "\n".join(mapping_lines)


def build_readable_actions(actions: List[DatabaseActionRecords], mode: str = "relative") -> str:
    """
    将动作列表转换为可读的文本格式。
    格式: 在（）分钟前，你使用了(action_name)，具体内容是：（action_prompt_display）

    Args:
        actions: 动作记录字典列表。

    Returns:
        格式化的动作字符串。
    """
    if not actions:
        return ""

    output_lines = []
    current_time = time.time()

    for action in actions:
        action_time = action.time or current_time
        action_name = action.action_name or "未知动作"
        # action_reason = action.get(action_data")
        if action_name in ["no_reply", "no_reply"]:
            continue

        action_prompt_display = action.action_prompt_display or "无具体内容"

        time_diff_seconds = current_time - action_time
        if mode == "relative":
            if time_diff_seconds < 60:
                time_ago_str = f"在{int(time_diff_seconds)}秒前"
            else:
                time_diff_minutes = round(time_diff_seconds / 60)
                time_ago_str = f"在{int(time_diff_minutes)}分钟前"
        elif mode == "absolute":
            # 转化为可读时间（仅保留时分秒，不包含日期）
            action_time_struct = time.localtime(action_time)
            time_str = time.strftime("%H:%M:%S", action_time_struct)
            time_ago_str = f"在{time_str}"
        else:
            raise ValueError(f"Unsupported mode: {mode}")

        line = f"{time_ago_str}，你使用了“{action_name}”，具体内容是：“{action_prompt_display}”"
        output_lines.append(line)

    return "\n".join(output_lines)


async def build_readable_messages_with_list(
    messages: List[DatabaseMessages],
    replace_bot_name: bool = True,
    timestamp_mode: str = "relative",
    truncate: bool = False,
    pic_single: bool = False,
) -> Tuple[str, List[Tuple[float, str, str]]]:
    """
    将消息列表转换为可读的文本格式，并返回原始(时间戳, 昵称, 内容)列表。
    允许通过参数控制格式化行为。
    """
    formatted_string, details_list, pic_id_mapping, _ = _build_readable_messages_internal(
        messages,
        replace_bot_name,
        timestamp_mode,
        truncate,
        pic_id_mapping=None,
        pic_counter=1,
        show_pic=True,
        message_id_list=None,
        pic_single=pic_single,
        long_time_notice=False,
    )

    if not pic_single:
        if pic_mapping_info := build_pic_mapping_info(pic_id_mapping):
            formatted_string = f"{pic_mapping_info}\n\n{formatted_string}"

    return formatted_string, details_list


def build_readable_messages_with_id(
    messages: List[DatabaseMessages],
    replace_bot_name: bool = True,
    timestamp_mode: str = "relative",
    read_mark: float = 0.0,
    truncate: bool = False,
    show_actions: bool = False,
    show_pic: bool = True,
    remove_emoji_stickers: bool = False,
    pic_single: bool = False,
) -> Tuple[str, List[Tuple[str, DatabaseMessages]]]:
    """
    将消息列表转换为可读的文本格式，并返回原始(时间戳, 昵称, 内容)列表。
    允许通过参数控制格式化行为。
    """
    message_id_list = assign_message_ids(messages)

    formatted_string = build_readable_messages(
        messages=messages,
        replace_bot_name=replace_bot_name,
        timestamp_mode=timestamp_mode,
        truncate=truncate,
        show_actions=show_actions,
        show_pic=show_pic,
        read_mark=read_mark,
        message_id_list=message_id_list,
        remove_emoji_stickers=remove_emoji_stickers,
        pic_single=pic_single,
    )

    return formatted_string, message_id_list


def build_readable_messages(
    messages: List[DatabaseMessages],
    replace_bot_name: bool = True,
    timestamp_mode: str = "relative",
    read_mark: float = 0.0,
    truncate: bool = False,
    show_actions: bool = False,
    show_pic: bool = True,
    message_id_list: Optional[List[Tuple[str, DatabaseMessages]]] = None,
    remove_emoji_stickers: bool = False,
    pic_single: bool = False,
    long_time_notice: bool = False,
) -> str:  # sourcery skip: extract-method
    """
    将消息列表转换为可读的文本格式。
    如果提供了 read_mark，则在相应位置插入已读标记。
    允许通过参数控制格式化行为。

    Args:
        messages: 消息列表
        replace_bot_name: 是否替换机器人名称为"你"
        merge_messages: 是否合并连续消息
        timestamp_mode: 时间戳显示模式，"normal"或"normal_no_YMD"或"relative"
        read_mark: 已读标记时间戳
        truncate: 是否截断长消息
        show_actions: 是否显示动作记录
        remove_emoji_stickers: 是否移除表情包并过滤空消息
        long_time_notice: 是否在消息间隔过长（>8小时）或跨天时插入时间提示
    """
    # WIP HERE and BELOW ----------------------------------------------
    # 创建messages的深拷贝，避免修改原始列表
    if not messages:
        return ""

    # 如果启用移除表情包，先过滤消息
    if remove_emoji_stickers:
        filtered_messages = []
        for msg in messages:
            # 获取消息内容
            content = msg.processed_plain_text or ""
            # 移除表情包
            emoji_pattern = r"\[表情包：[^\]]+\]"
            content = re.sub(emoji_pattern, "", content)

            # 如果移除表情包后内容不为空，则保留消息
            if content.strip():
                filtered_messages.append(msg)

        messages = filtered_messages

    copy_messages: List[DatabaseMessages] = []
    for msg in messages:
        if remove_emoji_stickers:
            # 移除表情包
            msg.processed_plain_text = re.sub(r"\[表情包：[^\]]+\]", "", msg.processed_plain_text or "")
            copy_messages.append(msg)
        else:
            copy_messages.append(msg)

    if show_actions and copy_messages:
        # 获取所有消息的时间范围
        min_time = min(msg.time or 0 for msg in copy_messages)
        max_time = max(msg.time or 0 for msg in copy_messages)

        # 从第一条消息中获取chat_id
        chat_id = messages[0].chat_id if messages else None

        # 获取这个时间范围内的动作记录，并匹配chat_id
        with get_db_session() as session:
            actions_in_range = session.exec(
                select(ActionRecord)
                .where(col(ActionRecord.timestamp) >= datetime.fromtimestamp(min_time))
                .where(col(ActionRecord.timestamp) <= datetime.fromtimestamp(max_time))
                .where(col(ActionRecord.session_id) == chat_id)
                .order_by(col(ActionRecord.timestamp))
            ).all()

        # 获取最新消息之后的第一个动作记录
        with get_db_session() as session:
            action_after_latest = session.exec(
                select(ActionRecord)
                .where(col(ActionRecord.timestamp) > datetime.fromtimestamp(max_time))
                .where(col(ActionRecord.session_id) == chat_id)
                .order_by(col(ActionRecord.timestamp))
                .limit(1)
            ).all()

        # 合并两部分动作记录
        actions: List[ActionRecord] = list(actions_in_range) + list(action_after_latest)

        # 将动作记录转换为消息格式
        for action in actions:
            # 只有当build_into_prompt为True时才添加动作记录
            action_display_prompt = action.action_display_prompt or ""
            if action_display_prompt:
                action_msg = DatabaseMessages(
                    message_id=f"action_{action.action_id}",
                    time=float(action.timestamp.timestamp()),
                    chat_id=chat_id or "",
                    processed_plain_text=action_display_prompt,
                    display_message=action_display_prompt,
                    user_platform=global_config.bot.platform,
                    user_id=str(global_config.bot.qq_account),
                    user_nickname=global_config.bot.nickname,
                    user_cardname="",
                    chat_info_platform=str(global_config.bot.platform),
                    chat_info_stream_id=chat_id or "",
                )
                copy_messages.append(action_msg)

        # 重新按时间排序
        copy_messages.sort(key=lambda x: x.time or 0)

    if read_mark <= 0:
        # 没有有效的 read_mark，直接格式化所有消息
        formatted_string, _, pic_id_mapping, _ = _build_readable_messages_internal(
            copy_messages,
            replace_bot_name,
            timestamp_mode,
            truncate,
            show_pic=show_pic,
            message_id_list=message_id_list,
            pic_single=pic_single,
            long_time_notice=long_time_notice,
        )

        if not pic_single:
            pic_mapping_info = build_pic_mapping_info(pic_id_mapping)
            if pic_mapping_info:
                return f"{pic_mapping_info}\n\n{formatted_string}"
        return formatted_string
    else:
        # 按 read_mark 分割消息
        messages_before_mark = [msg for msg in copy_messages if (msg.time or 0) <= read_mark]
        messages_after_mark = [msg for msg in copy_messages if (msg.time or 0) > read_mark]

        # 共享的图片映射字典和计数器
        pic_id_mapping = {}
        pic_counter = 1

        # 分别格式化，但使用共享的图片映射
        formatted_before, _, pic_id_mapping, pic_counter = _build_readable_messages_internal(
            messages_before_mark,
            replace_bot_name,
            timestamp_mode,
            truncate,
            pic_id_mapping,
            pic_counter,
            show_pic=show_pic,
            message_id_list=message_id_list,
            pic_single=pic_single,
            long_time_notice=long_time_notice,
        )
        formatted_after, _, pic_id_mapping, _ = _build_readable_messages_internal(
            messages_after_mark,
            replace_bot_name,
            timestamp_mode,
            False,
            pic_id_mapping,
            pic_counter,
            show_pic=show_pic,
            message_id_list=message_id_list,
            pic_single=pic_single,
            long_time_notice=long_time_notice,
        )

        read_mark_line = "\n--- 以上消息是你已经看过，请关注以下未读的新消息---\n"

        # 生成图片映射信息
        if not pic_single:
            if pic_id_mapping:
                pic_mapping_info = f"图片信息：\n{build_pic_mapping_info(pic_id_mapping)}\n聊天记录信息：\n"
            else:
                pic_mapping_info = "聊天记录信息：\n"
        else:
            pic_mapping_info = ""

        # 组合结果
        result_parts = []
        if pic_mapping_info:
            result_parts.extend((pic_mapping_info, "\n"))
        if formatted_before and formatted_after:
            result_parts.extend([formatted_before, read_mark_line, formatted_after])
        elif formatted_before:
            result_parts.extend([formatted_before, read_mark_line])
        elif formatted_after:
            result_parts.extend([read_mark_line, formatted_after])
        else:
            result_parts.append(read_mark_line.strip())

        return "".join(result_parts)


async def build_anonymous_messages(messages: List[DatabaseMessages], show_ids: bool = False) -> str:
    """
    构建匿名可读消息，将不同人的名称转为唯一占位符（A、B、C...），bot自己用SELF。
    处理 回复<aaa:bbb> 和 @<aaa:bbb> 字段，将bbb映射为匿名占位符。
    """
    if not messages:
        logger.warning("没有消息，无法构建匿名消息")
        return ""

    person_map = {}
    current_char = ord("A")
    output_lines = []

    # 图片ID映射字典
    pic_id_mapping = {}
    pic_counter = 1

    def process_pic_ids(content: str) -> str:
        """处理内容中的图片ID，将其替换为[图片x]格式"""
        nonlocal pic_counter

        # 匹配 [picid:xxxxx] 格式
        pic_pattern = r"\[picid:([^\]]+)\]"

        def replace_pic_id(match):
            nonlocal pic_counter
            pic_id = match.group(1)

            if pic_id not in pic_id_mapping:
                pic_id_mapping[pic_id] = f"图片{pic_counter}"
                pic_counter += 1

            return f"[{pic_id_mapping[pic_id]}]"

        return re.sub(pic_pattern, replace_pic_id, content)

    def get_anon_name(platform, user_id):
        # print(f"get_anon_name: platform:{platform}, user_id:{user_id}")
        # print(f"global_config.bot.qq_account:{global_config.bot.qq_account}")

        if (platform == "qq" and user_id == global_config.bot.qq_account) or (
            platform == "telegram" and user_id == getattr(global_config.bot, "telegram_account", "")
        ):
            # print("SELF11111111111111")
            return "SELF"
        try:
            person_id = get_person_id(platform, user_id)
        except Exception as _e:
            person_id = None
        if not person_id:
            return "?"
        if person_id not in person_map:
            nonlocal current_char
            person_map[person_id] = chr(current_char)
            current_char += 1
        return person_map[person_id]

    for i, msg in enumerate(messages):
        try:
            platform = msg.chat_info.platform
            user_id = msg.user_info.user_id
            content = msg.display_message or msg.processed_plain_text or ""

            # 处理图片ID
            content = process_pic_ids(content)

            anon_name = get_anon_name(platform, user_id)
            # print(f"anon_name:{anon_name}")

            # 使用独立函数处理用户引用格式，传入自定义的匿名名称解析器
            def anon_name_resolver(platform: str, user_id: str) -> str:
                try:
                    return get_anon_name(platform, user_id)
                except Exception:
                    return "?"

            content = replace_user_references(content, platform, anon_name_resolver, replace_bot_name=False)

            # 构建消息头，如果启用show_ids则添加序号
            if show_ids:
                header = f"[{i + 1}] {anon_name}说 "
            else:
                header = f"{anon_name}说 "

            output_lines.append(header)
            stripped_line = content.strip()
            if stripped_line:
                if stripped_line.endswith("。"):
                    stripped_line = stripped_line[:-1]
                output_lines.append(f"{stripped_line}")
            # print(f"output_lines:{output_lines}")
            output_lines.append("\n")
        except Exception:
            continue

    # 在最前面添加图片映射信息
    final_output_lines = []
    pic_mapping_info = build_pic_mapping_info(pic_id_mapping)
    if pic_mapping_info:
        final_output_lines.append(pic_mapping_info)
        final_output_lines.append("\n\n")

    final_output_lines.extend(output_lines)
    formatted_string = "".join(final_output_lines).strip()
    return formatted_string


async def get_person_id_list(messages: List[Dict[str, Any]]) -> List[str]:
    """
    从消息列表中提取不重复的 person_id 列表 (忽略机器人自身)。

    Args:
        messages: 消息字典列表。

    Returns:
        一个包含唯一 person_id 的列表。
    """
    person_ids_set = set()  # 使用集合来自动去重

    for msg in messages:
        platform = msg.get("user_platform") or ""
        user_id = msg.get("user_id") or ""

        # 检查必要信息是否存在 且 不是机器人自己
        if not all([platform, user_id]) or user_id == global_config.bot.qq_account:
            continue

        if person_id := get_person_id(platform, user_id):
            person_ids_set.add(person_id)

    return list(person_ids_set)  # 将集合转换为列表返回


async def build_bare_messages(messages: List[DatabaseMessages]) -> str:
    """
    构建简化版消息字符串，只包含processed_plain_text内容，不考虑用户名和时间戳

    Args:
        messages: 消息列表

    Returns:
        只包含消息内容的字符串
    """
    if not messages:
        return ""

    output_lines = []

    for msg in messages:
        # 获取纯文本内容
        content = msg.processed_plain_text or ""

        # 处理图片ID
        pic_pattern = r"\[picid:[^\]]+\]"

        def replace_pic_id(match):
            return "[图片]"

        content = re.sub(pic_pattern, replace_pic_id, content)

        # 处理用户引用格式，移除回复和@标记
        reply_pattern = r"回复<[^:<>]+:[^:<>]+>"
        content = re.sub(reply_pattern, "回复[某人]", content)

        at_pattern = r"@<[^:<>]+:[^:<>]+>"
        content = re.sub(at_pattern, "@[某人]", content)

        # 清理并添加到输出
        content = content.strip()
        if content:
            output_lines.append(content)

    return "\n".join(output_lines)
